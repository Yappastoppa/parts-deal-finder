"""Validation and the explicit boundary between private supplier data and public listings."""
import os
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
        'images': images,
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
