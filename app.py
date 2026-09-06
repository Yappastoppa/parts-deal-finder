import json
import mimetypes
import sqlite3
import uuid
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from carpart_engine import capture_listing_gallery, search_parts
from telegram_bot import _cart, _clear_cart, _garage, _save_vehicle, _set_cart, _persist_search, _part_matches, resolve_part_query, sanitize_customer_text
from business_config import BUSINESS_NAME, COOKIE_SECURE, DEFAULT_CURRENCY, DEVELOPMENT_MODE, PRIVACY_VERSION, SHIPPING_POLICY_VERSION, TERMS_VERSION


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "storefront.sqlite3"
HOST = "127.0.0.1"
PORT = 8000
WEB_SESSIONS = {}


def canonical_search_query(query):
	words = query.split()
	if len(words) < 3 or not words[0].isdigit():
		return {"status": "invalid_prompt", "results": []}
	try:
		models = json.loads((ROOT / "data" / "resolver_options.json").read_text()).get("models", [])
	except (OSError, ValueError):
		models = []
	model = next((value for value in sorted(models, key=len, reverse=True) if " ".join(words[1:]).lower().startswith(value.lower())), None)
	if not model:
		return {"status": "vehicle_not_found", "results": []}
	part_text = " ".join(words[1:])[len(model):].strip()
	candidates = resolve_part_query(part_text)
	if len(part_text) < 3:
		candidates = [item for item in candidates if item["normalized_label"].startswith(part_text.lower())]
	else:
		ranked = _part_matches(part_text)
		if ranked:
			best = ranked[0][0]
			candidates = [item for score, item in ranked if score >= max(60, best - 15)]
	if not candidates:
		return {"status": "part_not_found", "results": []}
	if len(candidates) > 1 and candidates[0]["normalized_label"] != candidates[1]["normalized_label"]:
		return {"status": "needs_part_choice", "request": query, "vehicle": {"year": int(words[0]), "make": model.split()[0], "model": " ".join(model.split()[1:])}, "part_candidates": [item["source_label"] for item in candidates[:5]], "results": []}
	return {"status": "ok", "query": f"{words[0]} {model} {candidates[0]['source_label']}"}


def db():
	connection = sqlite3.connect(DB_PATH)
	connection.row_factory = sqlite3.Row
	connection.executescript("""
	CREATE TABLE IF NOT EXISTS orders (
	 id INTEGER PRIMARY KEY, public_order_number TEXT UNIQUE NOT NULL,
	 web_session_id TEXT NOT NULL, status TEXT NOT NULL, subtotal TEXT NOT NULL,
	 shipping_amount TEXT NOT NULL, tax_amount TEXT NOT NULL, total TEXT NOT NULL,
	 currency TEXT NOT NULL, customer_name TEXT NOT NULL, customer_email TEXT NOT NULL,
	 customer_phone TEXT NOT NULL, shipping_address_1 TEXT NOT NULL,
	 shipping_address_2 TEXT, shipping_city TEXT NOT NULL, shipping_state TEXT NOT NULL,
	 shipping_zip TEXT NOT NULL, shipping_country TEXT NOT NULL, payment_status TEXT NOT NULL,
	 fulfillment_status TEXT NOT NULL, terms_version TEXT NOT NULL,
	 terms_accepted_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
	);
	CREATE TABLE IF NOT EXISTS order_items (
	 id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, store_inventory_id INTEGER,
	 year INTEGER, make TEXT, model TEXT, part TEXT, configuration TEXT,
	 description TEXT, condition TEXT, stock_number TEXT, source_price TEXT,
	 customer_price TEXT, quantity INTEGER NOT NULL, source_listing_identity TEXT,
	 primary_image TEXT, created_at TEXT NOT NULL
	);
	CREATE TABLE IF NOT EXISTS order_events (
	 id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, event_type TEXT NOT NULL,
	 safe_metadata_json TEXT NOT NULL, created_at TEXT NOT NULL
	);
	""")
	return connection


def _customer_text(value):
	return sanitize_customer_text(value)


def now():
	return datetime.now(timezone.utc).isoformat()


def public_order(row):
	return {key: row[key] for key in ("public_order_number", "status", "subtotal", "shipping_amount", "tax_amount", "total", "currency", "customer_name", "customer_email", "customer_phone", "shipping_address_1", "shipping_address_2", "shipping_city", "shipping_state", "shipping_zip", "shipping_country", "payment_status", "fulfillment_status", "terms_version", "terms_accepted_at", "created_at", "updated_at")}


def order_number(order_id):
	return f"CBF-{datetime.now().year}-{order_id:06d}"


def cart_order_snapshot(session_id, details):
	listing = WEB_SESSIONS[session_id].get("cart")
	if not listing:
		return None
	price = listing.get("customer_price") or listing.get("price")
	if not price:
		return None
	with db() as connection:
		row = connection.execute("SELECT id, source_listing_identity, source_price, availability_status, customer_price FROM store_inventory WHERE stock_number=? AND year=? AND make=? AND model=? ORDER BY id DESC LIMIT 1", (listing.get("stock"), listing.get("year"), listing.get("make"), listing.get("model"))).fetchone()
		if not row or row["availability_status"] != "available":
			return {"status": "UNAVAILABLE"}
		if row["customer_price"] and row["customer_price"] != price:
			return {"status": "PRICE_CHANGED"}
		return {"status": "AVAILABLE", "listing": listing, "inventory": row}


def inventory(limit=40, offset=0):
	with db() as connection:
		rows = connection.execute(
			"""SELECT store_inventory.*, part_catalog.source_label AS part_label
			   FROM store_inventory LEFT JOIN part_catalog ON part_catalog.id = store_inventory.part_catalog_id
			   WHERE availability_status = 'available'
			   ORDER BY last_verified_at DESC, id DESC LIMIT ? OFFSET ?""",
			(limit, offset),
		).fetchall()
		for row in rows:
			item = dict(row)
			item["listing"] = json.loads(item.pop("source_listing_json"))
			item["images"] = [
				image["image_reference"]
				for image in connection.execute(
					"SELECT image_reference FROM store_inventory_images WHERE store_inventory_id=? ORDER BY position",
					(item["id"],),
				).fetchall()
			]
			yield item


def recent_windows(limit=5):
	items = []
	for item in inventory(1000):
		if item["images"]:
			item["primary_image"] = item["images"][0]
			items.append(item)
			if len(items) >= limit:
				break
	return items


def image_href(reference):
	value = str(reference or "")
	if value.startswith("images/"):
		return "/" + value
	return "/images/" + Path(value).name


def customer_item(item):
	listing = item["listing"]
	return {
		"id": item["id"],
		"year": item["year"],
		"make": item["make"],
		"model": item["model"],
		"part": item["part_label"] or listing.get("part"),
		"description": _customer_text(listing.get("description", "")),
		"grade": item["grade"] or listing.get("grade", ""),
		"stock": item["stock_number"] or listing.get("stock", ""),
		"price": item["customer_price"] or item["source_price"] or "Price unavailable",
		"images": item["images"],
		"configuration": item["interchange_label"] or "",
	}


def page_shell(title, body, active="HOME"):
	nav = "".join(
		f'<a class="nav-link {"active" if label == active else ""}" href="{href}">{label}</a>'
		for label, href in (("HOME", "/"), ("DATABASE", "/database"), ("GARAGE", "/garage"), ("CART", "/cart"), ("MY ORDERS", "/orders"))
	)
	footer = f'<footer class="site-footer"><div><strong>{escape(BUSINESS_NAME)}</strong><span>© 2026 {escape(BUSINESS_NAME)}. All rights reserved.</span></div><div><b>SHOP</b><a href="/">Find a Part</a><a href="/database">Database</a><a href="/garage">Garage</a><a href="/cart">Cart</a><a href="/orders">My Orders</a></div><div><b>CUSTOMER CARE</b><a href="/contact">Contact Us</a><a href="/shipping">Shipping Policy</a><a href="/returns">Returns &amp; Refunds</a></div><div><b>LEGAL</b><a href="/terms">Terms of Sale</a><a href="/terms-of-use">Terms of Use</a><a href="/privacy">Privacy Policy</a></div></footer>'
	return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="Customer-facing used auto parts database and search."><meta property="og:title" content="{escape(title)} | {escape(BUSINESS_NAME)}"><meta property="og:description" content="Search available used auto parts by vehicle."><meta property="og:type" content="website"><title>{escape(title)} | {escape(BUSINESS_NAME)}</title><link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' fill='%233f718a'/%3E%3Ctext x='5' y='23' fill='white' font-size='18'%3EC%3C/text%3E%3C/svg%3E"><link rel="stylesheet" href="/static/site.css"></head><body><header class="topbar"><a class="brand" href="/"><strong>{escape(BUSINESS_NAME)}</strong><span>AUTO PARTS DATABASE</span></a><nav>{nav}</nav><form class="quick-search" action="/database" method="get"><input name="q" placeholder="Search vehicle or part"><button>SEARCH</button></form></header>{body}{footer}<script src="/static/site.js"></script></body></html>"""


def home_page():
	windows = recent_windows()
	stack = "".join(
		f'<article class="retro-window window-{index}" data-window><div class="window-bar"><span>{escape(str(item["year"]))} {escape(item["make"])} {escape(item["model"])}</span><button type="button" data-close>_</button></div><a href="/database?inventory={item["id"]}"><img src="{escape(image_href(item["primary_image"]))}" alt="{escape(item["make"])} {escape(item["model"])}"><b>{escape(item["part_label"] or item["listing"].get("part", "Part"))}</b></a></article>'
		for index, item in enumerate(windows[:5], 1)
	)
	body = f"""<main class="home-grid"><section class="home-copy"><p class="eyebrow">CARBOTFINDER / INVENTORY ACCESS</p><h1>FIND THE RIGHT<br><em>PART. FAST.</em></h1><p class="lede">Search available used auto parts by vehicle.</p><form class="search-panel" action="/database" method="get"><label>VEHICLE + PART<input name="q" required placeholder="2021 BMW M4 Spindle"></label><button class="steel-button">SEARCH DATABASE</button></form><div class="status-line"><span class="status-dot"></span> LIVE INVENTORY LINKED</div></section><section class="window-stack" aria-label="Recent inventory">{stack or '<div class="empty-window">NO IMAGE INVENTORY YET</div>'}</section></main>"""
	return page_shell("Home", body)


def card(item):
	image = f'<img src="{escape(image_href(item["images"][0]))}" alt="">' if item["images"] else '<div class="no-image">NO IMAGE</div>'
	config = f'<span class="config">{escape(item["configuration"])}</span>' if item["configuration"] else ""
	return f"""<article class="result-card" data-detail='{escape(json.dumps(item))}'><div class="card-image">{image}<span class="image-count">{len(item['images'])} PHOTOS</span></div><div class="card-body"><p class="card-kicker">{escape(item['part'] or 'PART')}</p><h3>{escape(str(item['year']))} {escape(item['make'])} {escape(item['model'])}</h3><p>{escape(item['description'][:115])}</p>{config}<div class="card-foot"><span>GRADE {escape(item['grade'] or 'N/A')}</span><strong>{escape(str(item['price']))}</strong></div><button class="open-detail" type="button">OPEN RECORD</button></div></article>"""


def database_page(query=""):
	items = [customer_item(item) for item in inventory()]
	if query:
		needle = query.lower()
		items = [item for item in items if needle in json.dumps(item).lower()]
	cards = "".join(card(item) for item in items)
	body = f"""<main class="database-layout"><aside class="filter-panel"><div class="panel-heading"><span>QUERY FILTERS</span><b>01</b></div><form class="web-search-form" action="/database" method="get"><label>SEARCH<input name="q" value="{escape(query)}" placeholder="Make, model, part"></label><label>YEAR<input name="year" placeholder="Any year"></label><button class="steel-button">RUN QUERY</button></form><div class="filter-note">SOURCE-LINKED RECORDS<br>AVAILABLE INVENTORY</div></aside><section class="database-main"><div class="database-heading"><div><p class="eyebrow">DATABASE / LIVE RECORDS</p><h1>AVAILABLE INVENTORY</h1></div><span class="record-count">{len(items):03d} RECORDS</span></div><div class="search-feedback" data-search-feedback></div><div class="result-grid" data-result-grid>{cards or '<div class="empty-state">NO MATCHING RECORDS<br><a href="/">RETURN TO SEARCH</a></div>'}</div></section><div class="detail-layer" data-detail-layer></div></main>"""
	return page_shell("Database", body, "DATABASE")


def search_result_payload(result):
	return {
		"status": result.get("status"),
		"request": result.get("request"),
		"vehicle": result.get("vehicle"),
		"part": result.get("part"),
		"search": {
			"interchange": result.get("search", {}).get("interchange"),
			"available_interchange_choices": result.get("search", {}).get("available_interchange_choices", []),
		},
		"choices": [{"label": item.get("label"), "value": item.get("label")} for item in result.get("choices", [])],
		"pagination": result.get("pagination", {}),
		"results": [
			{
				"year": item.get("year"), "make": item.get("make"), "model": item.get("model"),
				"part": item.get("part"), "description": sanitize_customer_text(item.get("description", "")),
				"grade": item.get("grade", ""), "stock": item.get("stock", ""),
				"price": item.get("customer_price") or item.get("price"),
				"images": item.get("images", []), "configuration": result.get("search", {}).get("interchange"),
			}
			for item in result.get("results", [])
		],
	}


def simple_page(title, active, content):
	return page_shell(title, f'<main class="simple-page"><p class="eyebrow">CARBOTFINDER / {active}</p><h1>{escape(title.upper())}</h1>{content}</main>', active)


def _cart_html(listing):
	if not listing:
		return '<p class="empty-state">YOUR CART IS EMPTY.<br><a href="/database">CONTINUE SHOPPING</a></p>'
	vehicle = " ".join(str(listing.get(key, "")) for key in ("year", "make", "model") if listing.get(key))
	price = listing.get("customer_price") or listing.get("price") or "Price unavailable"
	return f'<article class="cart-record"><p class="eyebrow">SELECTED RECORD</p><h2>{escape(str(listing.get("part", "Part")))}</h2><p>{escape(vehicle)}</p><p>Price: <strong>{escape(str(price))}</strong></p><p>Shipping: Calculated later</p><button class="steel-button" data-cart-remove>REMOVE</button><a class="steel-button" href="/database">CONTINUE SHOPPING</a><p class="filter-note">CHECKOUT COMING NEXT.</p></article>'


def checkout_page(session_id, error=""):
	listing = WEB_SESSIONS[session_id].get("cart")
	if not listing:
		return page_shell("Checkout", '<main class="simple-page"><p class="empty-state">YOUR CART IS EMPTY.<br><a href="/database">RETURN TO DATABASE</a></p></main>', "CART")
	price = listing.get("customer_price") or listing.get("price") or "Price unavailable"
	vehicle = " ".join(str(listing.get(key, "")) for key in ("year", "make", "model") if listing.get(key))
	error_html = f'<p class="checkout-error">{escape(error)}</p>' if error else ""
	body = f'''<main class="checkout-layout"><section class="checkout-summary"><p class="eyebrow">CHECKOUT / ORDER SUMMARY</p><h1>REVIEW ORDER</h1><div class="checkout-record"><h2>{escape(str(listing.get("part", "Part")))}</h2><p>{escape(vehicle)}</p><p>Condition: {escape(str(listing.get("grade", "N/A")))}</p><p>Stock #: {escape(str(listing.get("stock", "N/A")))}</p><strong>{escape(str(price))}</strong><hr><p>Subtotal: {escape(str(price))}</p><p>Shipping: Calculated later</p><p>Total: {escape(str(price))}</p></div></section><section class="checkout-form-wrap">{error_html}<form class="checkout-form" method="post" action="/api/checkout"><h2>SHIPPING INFORMATION</h2><input name="customer_name" required placeholder="Full Name"><input name="shipping_address_1" required placeholder="Address"><input name="shipping_address_2" placeholder="Address Line 2 (optional)"><div class="form-row"><input name="shipping_city" required placeholder="City"><input name="shipping_state" required placeholder="State"></div><div class="form-row"><input name="shipping_zip" required pattern="[0-9A-Za-z -]{{3,10}}" placeholder="ZIP"><input name="shipping_country" required value="United States" placeholder="Country"></div><h2>CONTACT INFORMATION</h2><input type="email" name="customer_email" required placeholder="Email"><input name="customer_phone" required placeholder="Phone"><h2>TERMS OF SALE</h2><label class="terms-check"><input type="checkbox" name="terms_accepted" value="yes"> I have reviewed my vehicle, part configuration, condition, shipping information, <a href="/returns">Return Policy</a>, and <a href="/terms">Terms of Sale</a> and agree to the purchase terms.</label><button class="steel-button" type="submit">SIMULATE TEST PAYMENT</button><p class="filter-note">Development checkout only. No real payment is processed.</p></form></section></main>'''
	return page_shell("Checkout", body, "CART")


def terms_page():
	content = '<div class="policy-text"><p>This development policy is presented for review before production launch.</p><h2>Used Auto Parts</h2><p>Parts are previously used components described from available inventory records.</p><h2>Part Condition</h2><p>Condition information is based on the available listing record and may vary.</p><h2>Vehicle and Configuration</h2><p>Customers should review vehicle, part, and configuration details before purchase.</p><h2>Fitment</h2><p>Fitment should be confirmed with a qualified professional when needed.</p><h2>Shipping</h2><p>Shipping is calculated separately and will be confirmed before production payment.</p><h2>Cancellations and Returns</h2><p>Final cancellation, return, damaged shipment, refund, and support rules require owner and legal review.</p><h2>Payment Disputes</h2><p>Nothing in this development policy limits rights provided by applicable law.</p></div>'
	return simple_page("Terms of Sale", "TERMS", content)


def returns_page():
	return simple_page("Return Policy", "RETURNS", '<div class="policy-text"><p>This development return policy is pending final owner and legal review.</p><h2>Before Purchase</h2><p>Review the vehicle, configuration, condition, and shipping details.</p><h2>Return Requests</h2><p>Contact support with the order number and part details. Final eligibility rules will be published before production checkout.</p><h2>Damaged Shipments</h2><p>Document visible damage promptly and retain packaging while the issue is reviewed.</p><h2>Refunds</h2><p>Refund timing and method will be confirmed under the final production policy.</p></div>')


def policy_page(title, sections):
	return simple_page(title, "LEGAL", '<div class="policy-text">' + ''.join(f'<h2>{escape(head)}</h2><p>{escape(text)}</p>' for head, text in sections) + '</div>')


POLICIES = {
	"/shipping": ("Shipping Policy", [("Processing Times", "Processing and delivery details will be confirmed before production launch."), ("Freight and Oversized Parts", "Oversized or freight items may require separate arrangements."), ("Address Accuracy", "Customers are responsible for providing an accurate delivery address."), ("Tracking and Damage", "Tracking information and damage-reporting instructions will be provided when available."), ("International Shipping", "International shipping rules are not configured yet.")]),
	"/privacy": ("Privacy Policy", [("Information We Process", "We may process name, email, phone, shipping address, order information, saved garage vehicles, browser session information, and future payment transaction references."), ("Why We Use It", "This information supports searches, saved vehicles, carts, checkout, support, and order fulfillment."), ("Service Providers", "Payment and delivery service providers may process information when those services are enabled."), ("Security and Retention", "We use reasonable safeguards and retain information as needed for service, records, and legal obligations."), ("Customer Rights", "Privacy requests and contact details will be configured before production launch.")]),
	"/terms-of-use": ("Terms of Use", [("Website Availability", "The website may change, be unavailable, or contain developing features."), ("Acceptable Use", "Do not misuse the site, interfere with its operation, or attempt unauthorized access."), ("Sessions and Accounts", "Guest sessions are used in this development version. Keep access to your session private."), ("Intellectual Property", "Site content and software are protected by applicable rights."), ("Third-Party Services", "Search, hosting, delivery, and future payment services may involve third parties."), ("Disclaimers", "Final production terms require owner and legal review.")]),
	"/contact": ("Contact Us", [("Order Help", "Support for order questions will be provided through the configured support channel."), ("Part Search Help", "We can help clarify vehicle and part information."), ("Returns and Shipping", "Support workflows for returns and shipping are being prepared."), ("Contact Information", "Contact information will be available before launch.")]),
}


class Handler(BaseHTTPRequestHandler):
	def _ensure_session(self):
		if getattr(self, "session_id", None):
			return
		cookies = self.headers.get("Cookie", "")
		self.session_id = next((part.split("=", 1)[1] for part in cookies.split(";") if part.strip().startswith("cb_sid=")), None)
		if not self.session_id or self.session_id not in WEB_SESSIONS:
			self.session_id = uuid.uuid4().hex
		WEB_SESSIONS.setdefault(self.session_id, {"search": None, "cart": None, "garage": [], "csrf": uuid.uuid4().hex})
		self.csrf = WEB_SESSIONS[self.session_id]["csrf"]
	def log_message(self, format, *args):
		return

	def send_text(self, text, status=200, content_type="text/html; charset=utf-8"):
		payload = text if isinstance(text, bytes) else text.encode()
		self.send_response(status)
		self.send_header("Content-Type", content_type)
		self.send_header("Content-Length", str(len(payload)))
		self.send_header("Set-Cookie", f"cb_sid={self.session_id}; Path=/; HttpOnly; SameSite=Lax{' ; Secure' if COOKIE_SECURE else ''}")
		self.send_header("Set-Cookie", f"cb_csrf={self.csrf}; Path=/; SameSite=Lax{' ; Secure' if COOKIE_SECURE else ''}")
		self.send_header("X-Content-Type-Options", "nosniff")
		self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
		self.send_header("X-Frame-Options", "SAMEORIGIN")
		self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; script-src 'self'")
		self.end_headers()
		self.wfile.write(payload)

	def do_GET(self):
		self._ensure_session()
		parsed = urlparse(self.path)
		if parsed.path == "/static/site.css" or parsed.path == "/static/site.js":
			path = ROOT / "static" / Path(parsed.path).name
			if path.exists():
				self.send_text(path.read_text(), content_type=mimetypes.guess_type(path.name)[0] or "text/plain")
			else:
				self.send_text("", 404)
			return
		if parsed.path == "/robots.txt":
			self.send_text("User-agent: *\nAllow: /\nDisallow: /cart\nDisallow: /checkout\nDisallow: /orders\nSitemap: http://127.0.0.1:8000/sitemap.xml\n", content_type="text/plain")
			return
		if parsed.path == "/sitemap.xml":
			self.send_text("<?xml version=\"1.0\" encoding=\"UTF-8\"?><urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\"><url><loc>http://127.0.0.1:8000/</loc></url><url><loc>http://127.0.0.1:8000/database</loc></url><url><loc>http://127.0.0.1:8000/garage</loc></url><url><loc>http://127.0.0.1:8000/terms</loc></url><url><loc>http://127.0.0.1:8000/returns</loc></url><url><loc>http://127.0.0.1:8000/shipping</loc></url><url><loc>http://127.0.0.1:8000/privacy</loc></url><url><loc>http://127.0.0.1:8000/terms-of-use</loc></url><url><loc>http://127.0.0.1:8000/contact</loc></url></urlset>", content_type="application/xml")
			return
		if parsed.path.startswith("/images/"):
			path = (ROOT / "images" / unquote(parsed.path.removeprefix("/images/"))).resolve()
			if path.is_file() and ROOT / "images" in path.parents:
				self.send_text(path.read_bytes(), content_type=mimetypes.guess_type(path.name)[0] or "image/jpeg")
			else:
				self.send_text("", 404)
			return
		elif parsed.path.startswith("/api/inventory/"):
			try:
				item_id = int(parsed.path.rsplit("/", 1)[-1])
			except ValueError:
				self.send_text(json.dumps({"status": "not_found"}), 404, "application/json")
				return
			item = next((customer_item(row) for row in inventory(1000) if row["id"] == item_id), None)
			self.send_text(json.dumps(item or {"status": "not_found"}), 200 if item else 404, "application/json")
			return
		query = parse_qs(parsed.query).get("q", [""])[0]
		if parsed.path == "/":
			self.send_text(home_page())
		elif parsed.path == "/database":
			self.send_text(database_page(query))
		elif parsed.path == "/garage":
			vehicles = WEB_SESSIONS[self.session_id]["garage"]
			rows = "".join(f'<div class="garage-row"><b>{escape(str(v["year"]))} {escape(v["make"])} {escape(v["model"])}</b><button data-garage-remove="{v["id"]}">REMOVE</button></div>' for v in vehicles)
			self.send_text(simple_page("My Garage", "GARAGE", f'<form class="search-panel garage-form" method="post" action="/garage"><input name="vehicle" placeholder="2020 Infiniti Q50"><button class="steel-button">SAVE VEHICLE</button></form><div class="garage-list">{rows or "<p class=empty-state>NO SAVED VEHICLES</p>"}</div>'))
		elif parsed.path == "/cart":
			item = WEB_SESSIONS[self.session_id].get("cart")
			content = _cart_html(item)
			if item:
				content = content.replace('CONTINUE SHOPPING', 'CHECKOUT')
				content = content.replace('href="/database"', 'href="/checkout"', 1)
			self.send_text(simple_page("Cart", "CART", content))
		elif parsed.path == "/checkout":
			self.send_text(checkout_page(self.session_id))
		elif parsed.path == "/terms":
			self.send_text(terms_page())
		elif parsed.path == "/returns":
			self.send_text(returns_page())
		elif parsed.path == "/500":
			self.send_text(page_shell("Server Error", '<main class="simple-page"><h1>WE COULD NOT COMPLETE THAT REQUEST</h1><p class="empty-state"><a href="/">RETURN HOME</a></p></main>'), 500)
		elif parsed.path in POLICIES:
			title, sections = POLICIES[parsed.path]
			self.send_text(policy_page(title, sections))
		elif parsed.path == "/orders":
			with db() as connection:
				rows = connection.execute("SELECT * FROM orders WHERE web_session_id=? ORDER BY created_at DESC", (self.session_id,)).fetchall()
			body = "".join(f'<div class="checkout-record"><a href="/orders/{escape(row["public_order_number"])}">{escape(row["public_order_number"])}</a><p>{escape(row["created_at"][:10])} | {escape(row["total"])} | {escape(row["status"])}</p></div>' for row in rows) or '<p class="empty-state">NO ORDERS YET.</p>'
			self.send_text(simple_page("My Orders", "MY ORDERS", body))
		elif parsed.path.startswith("/orders/"):
			order_number_value = unquote(parsed.path.rsplit("/", 1)[-1])
			with db() as connection:
				row = connection.execute("SELECT * FROM orders WHERE public_order_number=? AND web_session_id=?", (order_number_value, self.session_id)).fetchone()
			if not row:
				self.send_text("Not found", 404); return
			self.send_text(simple_page("Order " + row["public_order_number"], "MY ORDERS", f'<div class="checkout-record"><h2>{escape(row["public_order_number"])}</h2><p>Status: {escape(row["status"])}</p><p>Payment: {escape(row["payment_status"])}</p><p>Fulfillment: {escape(row["fulfillment_status"])}</p><p>{escape(row["shipping_address_1"])}<br>{escape(row["shipping_city"])}, {escape(row["shipping_state"])} {escape(row["shipping_zip"])}</p></div>'))
		elif parsed.path == "/api/parts/resolve":
			from telegram_bot import resolve_part_query
			self.send_text(json.dumps([item["source_label"] for item in resolve_part_query(parse_qs(parsed.query).get("q", [""])[0])]), content_type="application/json")
		elif parsed.path == "/api/inventory":
			self.send_text(json.dumps([customer_item(item) for item in inventory()]), content_type="application/json")
		else:
			self.send_text(page_shell("Page Not Found", '<main class="simple-page"><h1>PAGE NOT FOUND</h1><p class="empty-state"><a href="/">RETURN HOME</a></p></main>'), 404)

	def do_POST(self):
		self._ensure_session()
		parsed = urlparse(self.path)
		if parsed.path.startswith("/api/inventory/") and parsed.path.endswith("/gallery"):
			try:
				item_id = int(parsed.path.split("/")[3])
			except (IndexError, ValueError):
				self.send_text(json.dumps({"status": "not_found"}), 404, "application/json")
				return
			item = next((row for row in inventory(1000) if row["id"] == item_id), None)
			if not item:
				self.send_text(json.dumps({"status": "not_found"}), 404, "application/json")
				return
			if item["images"]:
				self.send_text(json.dumps({"status": "cached", "images": item["images"]}), content_type="application/json")
				return
			listing = capture_listing_gallery(item["listing"])
			with db() as connection:
				connection.execute("UPDATE store_inventory SET primary_image=?, last_verified_at=? WHERE id=?", ((listing.get("images") or [None])[0], __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), item_id))
				for position, image in enumerate(listing.get("images", [])):
					connection.execute("INSERT OR IGNORE INTO store_inventory_images (store_inventory_id,image_reference,position) VALUES (?,?,?)", (item_id, image, position))
			self.send_text(json.dumps({"status": "ok", "images": listing.get("images", [])}), content_type="application/json")
			return
		if parsed.path not in ("/api/search", "/api/cart", "/api/checkout", "/garage"):
			self.send_text("Not found", 404)
			return
		length = int(self.headers.get("Content-Length", 0))
		body = self.rfile.read(length) or b"{}"
		payload = json.loads(body or b"{}") if self.headers.get("Content-Type", "").startswith("application/json") else {key: values[0] for key, values in parse_qs(body.decode()).items()}
		if payload.get("csrf_token") != self.csrf and self.headers.get("X-CSRF-Token") != self.csrf:
			self.send_text(json.dumps({"status": "request_rejected"}), 403, "application/json")
			return
		try:
			if parsed.path == "/garage":
				words = payload.get("vehicle", "").split()
				if len(words) < 3 or not words[0].isdigit():
					self.send_text("Invalid vehicle", 400); return
				vehicle_id, _ = _save_vehicle(self.session_id, int(words[0]), words[1], " ".join(words[2:]))
				WEB_SESSIONS[self.session_id]["garage"] = _garage(self.session_id)
				self.send_text("Saved", content_type="text/plain"); return
			if parsed.path == "/api/cart":
				if payload.get("action") == "remove":
					WEB_SESSIONS[self.session_id]["cart"] = None
				else:
					item = next((row for row in inventory(1000) if row["id"] == int(payload.get("inventory_id", 0))), None)
					WEB_SESSIONS[self.session_id]["cart"] = item["listing"] if item else None
				self.send_text(json.dumps({"status": "ok"}), content_type="application/json"); return
			if parsed.path == "/api/checkout":
				if not payload.get("terms_accepted"):
					self.send_text(json.dumps({"status": "terms_required"}), 400, "application/json"); return
				recheck = cart_order_snapshot(self.session_id, payload)
				if not recheck or recheck.get("status") != "AVAILABLE":
					self.send_text(json.dumps({"status": recheck.get("status", "RECHECK_ERROR") if recheck else "RECHECK_ERROR"}), 409, "application/json"); return
				listing = recheck["listing"]; price = listing.get("customer_price") or listing.get("price")
				with db() as connection:
					connection.execute("INSERT INTO orders (public_order_number,web_session_id,status,subtotal,shipping_amount,tax_amount,total,currency,customer_name,customer_email,customer_phone,shipping_address_1,shipping_address_2,shipping_city,shipping_state,shipping_zip,shipping_country,payment_status,fulfillment_status,terms_version,terms_accepted_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("PENDING-" + uuid.uuid4().hex, self.session_id, "paid", price, "0.00", "0.00", price, "USD", payload.get("customer_name"), payload.get("customer_email"), payload.get("customer_phone"), payload.get("shipping_address_1"), payload.get("shipping_address_2"), payload.get("shipping_city"), payload.get("shipping_state"), payload.get("shipping_zip"), payload.get("shipping_country"), "test_paid", "fulfillment_pending", TERMS_VERSION, now(), now(), now()))
					order_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]; public_number = order_number(order_id); connection.execute("UPDATE orders SET public_order_number=? WHERE id=?", (public_number, order_id))
					connection.execute("INSERT INTO order_items (order_id,store_inventory_id,year,make,model,part,configuration,description,condition,stock_number,source_price,customer_price,quantity,source_listing_identity,primary_image,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (order_id, recheck["inventory"]["id"], listing.get("year"), listing.get("make"), listing.get("model"), listing.get("part"), listing.get("search_choice_metadata", {}).get("selected_interchange"), listing.get("description"), listing.get("grade"), listing.get("stock"), listing.get("price"), price, 1, recheck["inventory"]["source_listing_identity"], (listing.get("images") or [None])[0], now()))
					connection.execute("INSERT INTO order_events (order_id,event_type,safe_metadata_json,created_at) VALUES (?,?,?,?)", (order_id, "ORDER_CREATED", json.dumps({"terms_version": TERMS_VERSION, "payment_mode": "simulated_test"}), now()))
				WEB_SESSIONS[self.session_id]["cart"] = None
				self.send_text(json.dumps({"status": "ok", "public_order_number": public_number, "payment_status": "test_paid", "fulfillment_status": "fulfillment_pending"}), content_type="application/json"); return
			if not payload.get("resolved_query"):
				resolved = canonical_search_query(payload.get("query", ""))
				if resolved.get("status") != "ok":
					self.send_text(json.dumps(resolved), content_type="application/json"); return
				payload["resolved_query"] = resolved["query"]
			result = search_parts(payload.get("resolved_query"), capture_galleries=False, requested_interchange=payload.get("interchange"))
			WEB_SESSIONS[self.session_id]["search"] = result
			if result.get("status") == "ok": _persist_search(result, payload.get("query", ""))
			self.send_text(json.dumps(search_result_payload(result)), content_type="application/json")
		except Exception:
			self.send_text(json.dumps({"status": "search_unavailable", "results": []}), 200, "application/json")


if __name__ == "__main__":
	print(f"CARBOTFINDER WEB listening on http://{HOST}:{PORT}")
	ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
