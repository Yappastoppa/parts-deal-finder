"""Validation and the explicit boundary between private supplier data and public listings."""
import os
import math
import re


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def field(data, key, maximum, required=True):
    value = data.get(key, '')
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise APIError(400, f'Invalid {key}.')
    value = value.strip()
    if required and not value:
        raise APIError(400, f'Please provide {key}.')
    return value


def search_input(data):
    values = {k: field(data, k, n) for k, n in [('year', 4), ('make', 50), ('model', 100), ('part', 120)]}
    if not re.fullmatch(r'(19|20)\d{2}', values['year']):
        raise APIError(400, 'Enter a four-digit vehicle year from 1900 to 2099.')
    values['interchange'] = field(data, 'interchange', 240, False)
    if 'force_refresh' in data and not isinstance(data['force_refresh'], bool):
        raise APIError(400, 'Invalid force_refresh value.')
    values['force_refresh'] = bool(data.get('force_refresh'))
    return values


def public_text(value, limit=160):
    """No source links, source actions, embedded markup or credential values in public fields."""
    text = str(value or '')
    for key in ('CARPART_USERNAME', 'CARPART_PASSWORD', 'TELEGRAM_BOT_TOKEN'):
        secret = os.environ.get(key)
        if secret:
            text = text.replace(secret, '[removed]')
    text = re.sub(r'https?://\S+|www\.\S+', '', text, flags=re.I)
    text = re.sub(r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b', '[removed]', text)
    text = re.sub(r'(?i)\b(?:token|password|cookie|session|authorization)\s*[:=]\s*\S+', '[removed]', text)
    text = re.sub(r'<[^>]*>', '', text)
    return ' '.join(text.split())[:limit]


def public_listing(raw, listing_id, images):
    # Supplier prices are intentionally not presented as customer quotes until pricing rules exist.
    miles = raw.get('mileage', raw.get('miles'))
    try:
        miles = int(str(miles).replace(',', ''))
        if not 0 <= miles <= 2000000:
            miles = None
    except (ValueError, TypeError):
        miles = None
    donor_year = re.match(r'\s*((?:19|20)\d{2})\b', str(raw.get('year_part_model', '')))
    return {
        'id': listing_id, 'mode': 'live',
        'year': donor_year.group(1) if donor_year else public_text(raw.get('year'), 4),
        'make': public_text(raw.get('make'), 50), 'model': public_text(raw.get('model'), 100),
        'part': public_text(raw.get('part'), 120), 'location': public_text(raw.get('location'), 100),
        'price': None, 'mileage': miles, 'condition': public_text(raw.get('grade'), 80) or 'Not provided',
        'seller': public_text(raw.get('supplier'), 180) or 'Supplier details pending',
        'city': public_text(raw.get('city'), 100) or 'Location to be confirmed',
        'stock': public_text(raw.get('stock'), 80),
        'source': 'Supplier search result · fitment, price and availability require confirmation',
        'images': images, 'orderable': bool(raw.get('orderable', False)), 'has_gallery': bool(raw.get('gallery_url') or raw.get('gallery_trigger')),
    }


def quote_input(data):
    if data.get('consent') is not True:
        raise APIError(400, 'Please agree to be contacted about this request.')
    values = {k: field(data, k, n, k not in ['phone', 'notes']) for k, n in [
        ('name', 100), ('email', 254), ('phone', 40), ('postal_code', 20), ('notes', 1000)]}
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', values['email']):
        raise APIError(400, 'Enter a valid email address.')
    ids = data.get('listing_ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= 10 or any(not isinstance(x, str) or not re.fullmatch(r'[a-f0-9]{32}', x) for x in ids):
        raise APIError(400, 'Select between one and ten current parts.')
    values['listing_ids'] = list(dict.fromkeys(ids))
    key = field(data, 'request_key', 36)
    if not re.fullmatch(r'[a-f0-9-]{32,36}', key):
        raise APIError(400, 'Invalid request identifier.')
    values['request_key'] = key
    return values


def admin_login_input(data):
    return field(data, 'password', 200)


PRICING_SCOPES = {'global', 'part', 'make', 'supplier'}
MARKUP_TYPES = {'percent', 'fixed'}


def pricing_rule_input(data):
    scope_type = field(data, 'scope_type', 20)
    if scope_type not in PRICING_SCOPES:
        raise APIError(400, 'Invalid pricing rule scope.')
    scope_value = field(data, 'scope_value', 120, required=scope_type != 'global')
    markup_type = field(data, 'markup_type', 20)
    if markup_type not in MARKUP_TYPES:
        raise APIError(400, 'Invalid markup type.')
    try:
        markup_value = float(data.get('markup_value'))
    except (TypeError, ValueError):
        raise APIError(400, 'Invalid markup value.')
    if not math.isfinite(markup_value) or not 0 <= markup_value < 1_000_000:
        raise APIError(400, 'Invalid markup value.')
    min_margin = data.get('min_margin')
    if min_margin is not None:
        try:
            min_margin = float(min_margin)
        except (TypeError, ValueError):
            raise APIError(400, 'Invalid minimum margin.')
        if not math.isfinite(min_margin) or not 0 <= min_margin < 1_000_000:
            raise APIError(400, 'Invalid minimum margin.')
    rule_id = data.get('id')
    if rule_id is not None and not isinstance(rule_id, int):
        raise APIError(400, 'Invalid rule id.')
    return scope_type, scope_value, markup_type, markup_value, min_margin, rule_id


def listing_override_input(data):
    listing_id = field(data, 'listing_id', 40)
    if not re.fullmatch(r'[a-f0-9]{32}', listing_id):
        raise APIError(400, 'Invalid listing id.')
    hidden = data.get('hidden')
    if hidden is not None and not isinstance(hidden, bool):
        raise APIError(400, 'Invalid hidden value.')
    manual_price = data.get('manual_price')
    clear_manual_price = manual_price is None and 'manual_price' in data and data.get('clear_manual_price') is True
    if manual_price is not None:
        try:
            manual_price = float(manual_price)
        except (TypeError, ValueError):
            raise APIError(400, 'Invalid manual price.')
        if not 0 <= manual_price < 1_000_000:
            raise APIError(400, 'Invalid manual price.')
    admin_notes = data.get('admin_notes')
    if admin_notes is not None:
        admin_notes = field(data, 'admin_notes', 2000, required=False)
    return listing_id, hidden, manual_price, admin_notes, clear_manual_price


def order_status_input(data):
    from .pricing import ORDER_STATES
    status = field(data, 'status', 40)
    if status not in ORDER_STATES:
        raise APIError(400, 'Invalid order status.')
    return status
