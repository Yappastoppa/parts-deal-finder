"""Pure pricing computation. No I/O; supplier price is only ever an input here, never the output shown to customers."""
import re

ORDER_STATES = [
    'new', 'cart', 'checkout_started', 'submitted', 'needs_review', 'reviewing', 'quoted', 'customer_confirmed',
    'ready_for_supplier_order', 'supplier_order_started', 'supplier_ordered', 'supplier_failed',
    'cancelled', 'closed', 'fulfilled',
]
ORDER_TRANSITIONS = {
    'submitted': {'needs_review', 'cancelled'}, 'new': {'needs_review', 'cancelled'},
    'needs_review': {'reviewing', 'quoted', 'customer_confirmed', 'cancelled'},
    'reviewing': {'quoted', 'customer_confirmed', 'cancelled'},
    'quoted': {'needs_review', 'customer_confirmed', 'cancelled'},
    'customer_confirmed': {'needs_review', 'ready_for_supplier_order', 'cancelled'},
    'ready_for_supplier_order': {'needs_review', 'cancelled'},
}


def rule_priority(rule):
    return ({'supplier': 0, 'part': 1, 'make': 2, 'global': 3}.get(rule['scope_type'], 9),
            -rule.get('updated_at', 0), -rule.get('id', 0))


def parse_supplier_price(raw_price):
    """Best-effort parse of the supplier's raw price text. Returns None when absent or nonsensical."""
    if raw_price is None:
        return None
    match = re.search(r'[\d,]+\.?\d*', str(raw_price))
    if not match:
        return None
    try:
        value = float(match.group().replace(',', ''))
    except ValueError:
        return None
    return value if 0 < value < 1_000_000 else None


def price_for(supplier_price, context, rules, override):
    """context: {'part','make','seller'}. rules: pricing_rules() output, most-specific scope first.
    Returns the customer-facing price, or None to keep the listing at 'Price on request'."""
    if override and override.get('manual_price') is not None:
        return round(override['manual_price'], 2)
    if supplier_price is None:
        return None
    rule = next((r for r in sorted(rules, key=rule_priority) if _rule_matches(r, context)), None)
    if not rule:
        return round(supplier_price, 2)
    price = supplier_price
    if rule['markup_type'] == 'percent':
        price = supplier_price * (1 + rule['markup_value'] / 100)
    elif rule['markup_type'] == 'fixed':
        price = supplier_price + rule['markup_value']
    if rule.get('min_margin') is not None:
        price = max(price, supplier_price + rule['min_margin'])
    return round(max(price, 0), 2)


def _rule_matches(rule, context):
    scope_type, scope_value = rule['scope_type'], (rule.get('scope_value') or '').strip().lower()
    if scope_type == 'global':
        return True
    if scope_type == 'part':
        return (context.get('part') or '').strip().lower() == scope_value
    if scope_type == 'make':
        return (context.get('make') or '').strip().lower() == scope_value
    if scope_type == 'supplier':
        return scope_value in (context.get('seller') or '').strip().lower()
    return False
