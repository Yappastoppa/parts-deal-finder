"""Admin control center. Separate bearer-token auth; never bundles a password into browser JavaScript."""
import re
from . import auth
from .domain import APIError, listing_override_input, order_status_input, pricing_rule_input

PUBLIC_SETTINGS = {'maintenance_mode': False, 'announcement': ''}


def public_settings(store):
    return {key: store.get_setting(key, default) for key, default in PUBLIC_SETTINGS.items()}


class AdminAPI:
    def __init__(self, store, rate, search=None):
        self.store, self.rate, self.search = store, rate, search

    def login(self, data, ip):
        self.rate.check(ip, 'admin_login', 10)
        if not auth.admin_password_configured():
            raise APIError(503, 'Admin access is not configured on this server.')
        from .domain import admin_login_input
        password = admin_login_input(data)
        if not auth.check_admin_password(password):
            raise APIError(401, 'Incorrect password.')
        token = self.store.create_admin_session()
        return {'status': 'ok', 'token': token}

    def require_session(self, environ):
        token = auth.read_bearer_token(environ)
        if not self.store.admin_session_valid(token):
            raise APIError(401, 'Sign in required.')
        return token

    def handle(self, method, rest, data, environ):
        """rest is the path with the '/api/admin' prefix removed, e.g. '/dashboard'."""
        if rest == '/logout' and method == 'POST':
            self.store.delete_admin_session(auth.read_bearer_token(environ))
            return {'status': 'ok'}
        if rest == '/dashboard' and method == 'GET':
            return self.store.dashboard_stats()
        if rest == '/session' and method == 'GET':
            return {'status': 'ok'}
        if rest == '/cache' and method == 'GET':
            return {'cache': self.store.cache_list()}
        if rest == '/cache/refresh-popular' and method == 'POST':
            import time
            from .search import FRESH_SECONDS
            entries = sorted(self.store.cache_list(), key=lambda c: c['search_count'], reverse=True)
            candidate = next((c for c in entries if time.time()-c['last_refreshed_at'] >= FRESH_SECONDS), None)
            if not candidate:
                return {'status': 'fresh'}
            job = self.search.start({k: candidate[k] for k in ('year','make','model','part','interchange')}, force_refresh=True)
            self.store.log_admin_action('popular_cache_refresh', candidate['cache_key'])
            return {'status': 'pending', 'search_id': job}
        match = re.fullmatch(r'/cache/(.{1,600})/(refresh|expire)', rest)
        if match and method == 'POST':
            cached = self.store.cache_get(match.group(1))
            if not cached:
                raise APIError(404, 'Cache entry not found.')
            if match.group(2) == 'refresh':
                job = self.search.start({k: cached[k] for k in ('year','make','model','part','interchange')}, force_refresh=True)
                self.store.log_admin_action('cache_refresh', match.group(1))
                return {'status': 'pending', 'search_id': job}
            self.store.cache_expire(match.group(1))
            self.store.log_admin_action('cache_expire', match.group(1))
            return {'status': 'ok'}
        match = re.fullmatch(r'/cache/(.{1,600})', rest)
        if match and method == 'GET':
            cached = self.store.cache_get(match.group(1))
            if not cached:
                raise APIError(404, 'Cache entry not found.')
            import json
            cached['payload'] = self.search._priced(json.loads(cached['payload']))
            return {'cache': cached}
        if match and method == 'DELETE':
            self.store.cache_delete(match.group(1))
            self.store.log_admin_action('cache_delete', match.group(1))
            return {'status': 'ok'}
        if rest == '/pricing-rules' and method == 'GET':
            return {'rules': self.store.pricing_rules_all()}
        if rest == '/pricing-rules' and method == 'POST':
            scope_type, scope_value, markup_type, markup_value, min_margin, rule_id = pricing_rule_input(data)
            new_id = self.store.save_pricing_rule(scope_type, scope_value, markup_type, markup_value, min_margin, rule_id)
            self.store.log_admin_action('pricing_rule_save', str(new_id), None, data)
            return {'id': new_id}
        match = re.fullmatch(r'/pricing-rules/(\d+)', rest)
        if match and method == 'DELETE':
            self.store.delete_pricing_rule(int(match.group(1)))
            self.store.log_admin_action('pricing_rule_delete', match.group(1))
            return {'status': 'ok'}
        match = re.fullmatch(r'/listings/([a-f0-9]{32})', rest)
        if match and method == 'GET':
            listing_id = match.group(1)
            public = self.store.listing_public(listing_id)
            if not public:
                raise APIError(404, 'Listing not found.')
            private = self.store.listing_private(listing_id) or {}
            override = self.store.listing_override(listing_id) or {}
            from .pricing import price_for
            return {'listing': public, 'customer_price': price_for(private.get('supplier_price'), public, self.store.pricing_rules(), override), 'supplier_price': private.get('supplier_price'), 'seller': private.get('seller'),
                    'override': {'hidden': bool(override.get('hidden')), 'manual_price': override.get('manual_price'),
                                 'admin_notes': override.get('admin_notes', '')}}
        if match and method == 'POST':
            listing_id = match.group(1)
            if not self.store.listing_public(listing_id):
                raise APIError(404, 'Listing not found.')
            _, hidden, manual_price, admin_notes, clear = listing_override_input({**data, 'listing_id': listing_id})
            before = self.store.listing_override(listing_id)
            self.store.save_listing_override(listing_id, hidden=hidden, manual_price=manual_price,
                                              admin_notes=admin_notes, clear_manual_price=clear)
            self.store.log_admin_action('listing_override', listing_id, before, data)
            return {'status': 'ok'}
        if rest == '/orders' and method == 'GET':
            return {'orders': self.store.orders()}
        match = re.fullmatch(r'/orders/([A-Z0-9-]{1,20})/prepare-supplier-order', rest)
        if match and method == 'POST':
            reference = match.group(1)
            order = self.store.order(reference)
            if not order:
                raise APIError(404, 'Order not found.')
            if order['status'] != 'ready_for_supplier_order':
                raise APIError(409, 'Confirm the customer and mark the order ready first.')
            import json
            with self.store.connect() as db:
                rows = db.execute('SELECT listing_id FROM order_sources WHERE reference=?', (reference,)).fetchall()
            if len(rows) != len(json.loads(order['items'])):
                raise APIError(409, 'Exact supplier sources are unavailable; review this order manually.')
            self.store.log_admin_action('supplier_handoff_prepared', reference)
            return {'status': 'prepared', 'reference': reference, 'listing_ids': [r['listing_id'] for r in rows],
                    'supplier_submitted': False, 'requires_supplier_revalidation': True}
        match = re.fullmatch(r'/orders/([A-Z0-9-]{1,20})/status', rest)
        if match and method == 'POST':
            reference = match.group(1)
            status = order_status_input(data)
            before = self.store.order(reference)
            self.store.order_status(reference, status)
            self.store.log_admin_action('order_status', reference, before and before.get('status'), status)
            return {'status': 'ok'}
        match = re.fullmatch(r'/orders/([A-Z0-9-]{1,20})/place-supplier-order', rest)
        if match and method == 'POST':
            # Deliberately unimplemented: the real supplier Order Part handoff needs a dedicated, tested
            # engine integration and explicit sign-off before any irreversible purchase can be triggered.
            raise APIError(501, 'Supplier order execution is not implemented. This action is intentionally disabled.')
        if rest == '/settings' and method == 'GET':
            return {'settings': public_settings(self.store)}
        if rest == '/settings' and method == 'POST':
            before = public_settings(self.store)
            for key in PUBLIC_SETTINGS:
                if key in data:
                    value = data[key]
                    if key == 'maintenance_mode' and not isinstance(value, bool):
                        raise APIError(400, 'maintenance_mode must be true or false.')
                    if key == 'announcement' and (not isinstance(value, str) or len(value) > 300):
                        raise APIError(400, 'announcement must be text up to 300 characters.')
                    self.store.set_setting(key, value)
            self.store.log_admin_action('settings', 'settings', before, {k: data[k] for k in PUBLIC_SETTINGS if k in data})
            return {'settings': public_settings(self.store)}
        if rest == '/audit' and method == 'GET':
            return {'audit': self.store.admin_audit()}
        raise APIError(404, 'Not found.')
