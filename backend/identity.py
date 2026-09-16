"""Conservative supplier inventory identity, independent of price and session actions."""
import hashlib
import json
from urllib.parse import urlsplit


def source_identity(public, source, seller):
    def normalized(value):
        return ' '.join(str(value or '').split()).casefold()

    stock = normalized(source.get('stock'))
    supplier = normalized(seller)
    search = source.get('search') or {}
    origin = urlsplit(source.get('source_results_url') or '')
    # Never attach an override using only a yard name or a positional Order Part button.
    if (not stock or stock in {'-', '--', 'n/a', 'none', 'unknown'} or not supplier
            or not origin.hostname or not all(search.get(k) for k in ('year', 'make', 'model', 'part'))):
        return None
    fields = [origin.hostname.casefold(), supplier, stock, normalized(public.get('year')),
              *[normalized(search.get(k)) for k in ('year', 'make', 'model', 'part', 'interchange')]]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=True).encode()).hexdigest()
