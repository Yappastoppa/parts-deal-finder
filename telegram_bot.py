import os
import asyncio
import logging
import html
import uuid
import re
import json
import hashlib
import sqlite3
import threading
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from carpart_engine import capture_listing_gallery, place_order, search_parts

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)

for logger_name in (
    "httpx",
    "httpcore",
    "telegram",
    "telegram.ext",
    "telegram.request",
):
    logging.getLogger(logger_name).setLevel(logging.WARNING)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SESSIONS = {}
USER_CARTS = {}
USER_ORDERS = {}
ORDER_LOCKS = set()
DB_PATH = Path(__file__).resolve().parent / "data" / "storefront.sqlite3"
SEARCH_STATES = {}
ENGINE_SEARCH_SLOT = threading.BoundedSemaphore(1)


def _new_search_state(user_id, vehicle=None):
    state = {
        "stage": "AWAITING_PART" if vehicle else "IDLE",
        "search_id": uuid.uuid4().hex[:8],
        "vehicle": vehicle,
        "requested_part_text": None,
        "resolved_part": None,
        "part_candidates": [],
        "interchange_candidates": [],
        "selected_interchange": None,
        "results": {},
        "result_page": 0,
        "search_in_progress": False,
    }
    SEARCH_STATES[user_id] = state
    logging.info("SEARCH_STATE user=%s IDLE -> %s", _safe_user(user_id), state["stage"])
    return state


def _state(user_id):
    return SEARCH_STATES.get(user_id)


def _safe_user(user_id):
    return hashlib.sha256(str(user_id).encode()).hexdigest()[:10]


def _transition(user_id, state, stage):
    previous = state.get("stage", "IDLE")
    if previous != stage:
        logging.info("SEARCH_STATE user=%s %s -> %s", _safe_user(user_id), previous, stage)
        state["stage"] = stage


def _clear_dependent_state(state, keep_vehicle=True):
    vehicle = state.get("vehicle") if keep_vehicle else None
    state.update({
        "stage": "AWAITING_PART" if vehicle else "IDLE",
        "vehicle": vehicle,
        "requested_part_text": None,
        "resolved_part": None,
        "part_candidates": [],
        "interchange_candidates": [],
        "selected_interchange": None,
        "results": {},
        "result_page": 0,
        "search_in_progress": False,
    })


def _clear_active_search(user_id):
    state = _state(user_id)
    if not state or (state.get("stage") == "IDLE" and not state.get("vehicle")):
        return False
    _clear_dependent_state(state, keep_vehicle=False)
    _transition(user_id, state, "IDLE")
    state["search_id"] = uuid.uuid4().hex[:8]
    SESSIONS.pop(user_id, None)
    return True


def _command_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Find a Part", callback_data="m:find")],
        [InlineKeyboardButton("Main Menu", callback_data="m:home")],
    ])


async def _send_menu(message):
    await message.reply_text(WELCOME, parse_mode=ParseMode.HTML, reply_markup=_menu_keyboard())


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("COMMAND_RECEIVED: menu")
    await _send_menu(update.message)
    logging.info("COMMAND_HANDLED: menu YES")


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("COMMAND_RECEIVED: clear")
    cleared = _clear_active_search(update.effective_user.id)
    text = "Search cleared.\n\nWhat would you like to do?" if cleared else "There is no active search to clear."
    await update.message.reply_text(text, reply_markup=_command_keyboard())
    logging.info("COMMAND_HANDLED: clear YES")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("COMMAND_RECEIVED: cancel")
    context.user_data.pop("awaiting_vehicle", None)
    context.user_data.pop("awaiting_part", None)
    context.user_data.pop("garage_add", None)
    context.user_data.pop("pending_vehicle", None)
    if context.user_data.pop("checkout_state", None):
        await update.message.reply_text("Checkout cancelled. Your cart is still saved.", reply_markup=_command_keyboard())
    elif _state(update.effective_user.id) and _state(update.effective_user.id).get("stage") == "AWAITING_INTERCHANGE":
        await update.message.reply_text("Configuration selection cancelled.", reply_markup=_command_keyboard())
    else:
        await _send_menu(update.message)
    logging.info("COMMAND_HANDLED: cancel YES")


async def new_search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("COMMAND_RECEIVED: newsearch")
    _clear_active_search(update.effective_user.id)
    context.user_data.clear()
    state = _new_search_state(update.effective_user.id)
    state["stage"] = "AWAITING_VEHICLE"
    await update.message.reply_text("What vehicle and part are you looking for?\n\nExample: <code>2020 Infiniti Q50 turbo</code>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Use My Garage", callback_data="m:garage"), InlineKeyboardButton("Main Menu", callback_data="m:home")]]))
    logging.info("COMMAND_HANDLED: newsearch YES")


async def find_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("COMMAND_RECEIVED: find")
    _clear_active_search(update.effective_user.id)
    context.user_data.clear()
    vehicles = _garage(update.effective_user.id)
    if vehicles:
        buttons = [[InlineKeyboardButton(f"{v['year']} {v['make']} {v['model']}", callback_data=f"g:use:{v['id']}")] for v in vehicles]
        buttons.extend([[InlineKeyboardButton("Enter Another Vehicle", callback_data="m:find_text")], [InlineKeyboardButton("Main Menu", callback_data="m:home")]])
        await update.message.reply_text("Which vehicle are you shopping for?", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        context.user_data["awaiting_vehicle"] = True
        await update.message.reply_text("Tell us what you're looking for.\n\nExample: <code>2020 Infiniti Q50 turbo</code>", parse_mode=ParseMode.HTML, reply_markup=_back_menu_keyboard())
    logging.info("COMMAND_HANDLED: find YES")


async def garage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("COMMAND_RECEIVED: garage")
    await show_garage(update.message, update.effective_user.id)
    logging.info("COMMAND_HANDLED: garage YES")


async def cart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("COMMAND_RECEIVED: cart")
    await show_cart(update.message, update.effective_user.id)
    logging.info("COMMAND_HANDLED: cart YES")


async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("COMMAND_RECEIVED: orders")
    await update.message.reply_text("You don't have any orders yet.", reply_markup=_command_keyboard())
    logging.info("COMMAND_HANDLED: orders YES")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("COMMAND_RECEIVED: help")
    await update.message.reply_text("<b>How to use CARBOTFINDER</b>\n\nSearch by sending:\nYear + Make + Model + Part\n\nExample: <code>2020 Infiniti Q50 turbo</code>\n\n/find - Find a part\n/garage - Saved vehicles\n/cart - View cart\n/orders - View orders\n/newsearch - Start over\n/clear - Clear current search\n/menu - Main menu\n/cancel - Cancel current step", parse_mode=ParseMode.HTML, reply_markup=_command_keyboard())
    logging.info("COMMAND_HANDLED: help YES")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.info("COMMAND_RECEIVED: status")
    state = _state(update.effective_user.id)
    if state and state.get("vehicle"):
        vehicle = state["vehicle"]
        lines = [f"Current search:\n{vehicle.get('year', '')} {vehicle.get('make', '')} {vehicle.get('model', '')}"]
        if state.get("resolved_part"):
            part = state["resolved_part"]
            lines.append(part.get("source_label", str(part)) if isinstance(part, dict) else str(part))
        if state.get("selected_interchange"):
            lines.append(_customer_choice_label(state["selected_interchange"]))
        await update.message.reply_text("\n".join(lines), reply_markup=_command_keyboard())
    else:
        await update.message.reply_text("No active search.", reply_markup=_command_keyboard())
    logging.info("COMMAND_HANDLED: status YES")


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("I don't recognize that command.\n\nUse /help to see available commands.", reply_markup=_command_keyboard())


async def set_command_menu(application):
    await application.bot.set_my_commands([
        ("start", "Welcome and main menu"),
        ("menu", "Main menu"),
        ("find", "Find a part"),
        ("garage", "My saved vehicles"),
        ("cart", "View my cart"),
        ("orders", "View my orders"),
        ("newsearch", "Start a new search"),
        ("clear", "Clear current search"),
        ("cancel", "Cancel current step"),
        ("help", "How to use the bot"),
        ("status", "Show current search"),
    ])


def _run_engine_search(prompt, capture_galleries=True, requested_interchange=None):
    acquired = ENGINE_SEARCH_SLOT.acquire(timeout=180)
    if not acquired:
        raise TimeoutError("search slot unavailable")
    try:
        return search_parts(prompt, capture_galleries, requested_interchange)
    finally:
        ENGINE_SEARCH_SLOT.release()


def _db():
    DB_PATH.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS garage_vehicles (
            id INTEGER PRIMARY KEY, telegram_user_id INTEGER NOT NULL,
            year INTEGER NOT NULL, make TEXT NOT NULL, model TEXT NOT NULL,
            normalized_make TEXT NOT NULL, normalized_model TEXT NOT NULL,
            nickname TEXT, is_default INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(telegram_user_id, year, normalized_make, normalized_model)
        );
        CREATE TABLE IF NOT EXISTS carts (
            telegram_user_id INTEGER PRIMARY KEY, listing_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS search_state (
            telegram_user_id INTEGER PRIMARY KEY, state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS part_catalog (
            id INTEGER PRIMARY KEY, source_label TEXT UNIQUE NOT NULL,
            source_value TEXT, normalized_label TEXT NOT NULL,
            category TEXT, qualifiers_json TEXT, first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS part_aliases (
            id INTEGER PRIMARY KEY, alias TEXT NOT NULL,
            normalized_alias TEXT UNIQUE NOT NULL, part_catalog_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS search_demand (
            id INTEGER PRIMARY KEY, year INTEGER NOT NULL, make TEXT NOT NULL,
            model TEXT NOT NULL, normalized_make TEXT NOT NULL,
            normalized_model TEXT NOT NULL, part_catalog_id INTEGER,
            requested_part_text TEXT NOT NULL, resolved_part_label TEXT,
            search_count INTEGER NOT NULL DEFAULT 1, first_searched_at TEXT NOT NULL,
            last_searched_at TEXT NOT NULL,
            UNIQUE(year, normalized_make, normalized_model, part_catalog_id)
        );
        CREATE TABLE IF NOT EXISTS store_inventory (
            id INTEGER PRIMARY KEY, year INTEGER NOT NULL, make TEXT NOT NULL,
            model TEXT NOT NULL, part_catalog_id INTEGER, interchange_label TEXT,
            source_listing_identity TEXT UNIQUE NOT NULL, source_price TEXT,
            customer_price TEXT, grade TEXT, stock_number TEXT, mileage TEXT,
            primary_image TEXT, availability_status TEXT NOT NULL,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
            last_verified_at TEXT NOT NULL, source_listing_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS store_inventory_images (
            id INTEGER PRIMARY KEY, store_inventory_id INTEGER NOT NULL,
            image_reference TEXT NOT NULL, position INTEGER NOT NULL,
            UNIQUE(store_inventory_id, image_reference)
        );
    """)
    return connection


def _now():
    return datetime.now(timezone.utc).isoformat()


def _norm(value):
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def sanitize_customer_text(value):
    text = " ".join(str(value or "").split())
    text = re.sub(r"https?://\S+|www\.\S+", "", text, flags=re.I)
    text = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "", text, flags=re.I)
    text = re.sub(r"\b(?:call|text|contact|phone)\s*(?:us|seller)?\s*[:.-]?\s*\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", "", text, flags=re.I)
    text = re.sub(r"\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b", "", text)
    text = re.sub(r"\b(?:call|contact)\s+(?:seller|yard|supplier)\b.*$", "", text, flags=re.I)
    text = re.sub(r"\b(?:car[- ]?part(?:\.com)?|supplier|seller|yard)\b", "", text, flags=re.I)
    text = re.sub(r"\b(?:warranty|guarantee)\s+(?:call|contact)\b.*$", "", text, flags=re.I)
    text = re.sub(r"\b\d{7,11}\b", "", text)
    return " ".join(text.split()).strip(" -|,;")


def _save_state(user_id, state):
    with _db() as db:
        db.execute("INSERT OR REPLACE INTO search_state VALUES (?, ?, ?)", (user_id, json.dumps(state), _now()))


def _load_state(user_id):
    with _db() as db:
        row = db.execute("SELECT state_json FROM search_state WHERE telegram_user_id=?", (user_id,)).fetchone()
    return json.loads(row["state_json"]) if row else {}


def _garage(user_id):
    with _db() as db:
        return [dict(row) for row in db.execute("SELECT * FROM garage_vehicles WHERE telegram_user_id=? ORDER BY is_default DESC, id", (user_id,))]


def _catalog():
    try:
        root = Path(__file__).resolve().parent
        data = json.loads((root / "data" / "resolver_options.json").read_text())
        search_data = json.loads((root / "data" / "search_options.json").read_text())
        part_dropdown = next((item for item in search_data.get("dropdowns", []) if item.get("id") == "part_dropdown"), {})
        options = [
            {"source_label": item.get("text", "").strip(), "source_value": item.get("value")}
            for item in part_dropdown.get("options", [])
            if item.get("text", "").strip() and item.get("text", "").strip().lower() != "select part"
        ]
        labels = data.get("parts", [])
        if options:
            labels = [item["source_label"] for item in options]
        option_values = {item.get("source_label"): item.get("source_value") for item in options}
        now = _now()
        with _db() as db:
            for label in labels:
                db.execute("INSERT INTO part_catalog (source_label,source_value,normalized_label,first_seen,last_seen) VALUES (?,?,?,?,?) ON CONFLICT(source_label) DO UPDATE SET source_value=COALESCE(part_catalog.source_value,excluded.source_value),last_seen=excluded.last_seen,active=1", (label, option_values.get(label), _norm(label), now, now))
            aliases = {"turbo": "Turbocharger/Supercharger", "turbocharger": "Turbocharger/Supercharger", "knuckle": "Spindle/Knuckle Assembly, Front"}
            for alias, label in aliases.items():
                row = db.execute("SELECT id FROM part_catalog WHERE source_label=?", (label,)).fetchone()
                db.execute("INSERT OR IGNORE INTO part_aliases (alias,normalized_alias,part_catalog_id) VALUES (?,?,?)", (alias, _norm(alias), row["id"] if row else None))
            rows = db.execute("SELECT * FROM part_catalog WHERE active=1 ORDER BY source_label").fetchall()
        return [{"source_label": row["source_label"], "source_value": row["source_value"], "normalized_label": row["normalized_label"], "tokens": row["normalized_label"].split(), "aliases": [], "last_seen": row["last_seen"]} for row in rows]
    except (OSError, ValueError):
        return []


def _part_matches(query):
    normalized = _norm(query)
    candidates = []
    for option in _catalog():
        label = option["normalized_label"]
        if label == normalized:
            score = 100
        elif label.startswith(normalized):
            score = 80 + min(len(normalized), 20)
        elif all(token in label.split() for token in normalized.split()):
            score = 65
        elif normalized in label:
            score = 50
        else:
            token_similarity = max(
                (SequenceMatcher(None, normalized, token).ratio() for token in label.split()),
                default=0,
            )
            score = max(
                int(SequenceMatcher(None, normalized, label).ratio() * 45),
                int(token_similarity * 70),
            )
        if score >= 45:
            candidates.append((score, option))
    return sorted(candidates, key=lambda item: (-item[0], item[1]["source_label"]))


def resolve_part_query(user_text, vehicle_context=None):
    catalog = _catalog()
    return [option for _, option in _part_matches(user_text)] if catalog else []


def _save_vehicle(user_id, year, make, model):
    now = _now()
    with _db() as db:
        existing = db.execute("SELECT id FROM garage_vehicles WHERE telegram_user_id=? AND year=? AND normalized_make=? AND normalized_model=?", (user_id, year, _norm(make), _norm(model))).fetchone()
        if existing:
            return existing["id"], False
        count = db.execute("SELECT COUNT(*) AS n FROM garage_vehicles WHERE telegram_user_id=?", (user_id,)).fetchone()["n"]
        db.execute("INSERT INTO garage_vehicles (telegram_user_id,year,make,model,normalized_make,normalized_model,is_default,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (user_id, year, make, model, _norm(make), _norm(model), int(count == 0), now, now))
        return db.execute("SELECT last_insert_rowid()").fetchone()[0], True


def _cart(user_id):
    with _db() as db:
        row = db.execute("SELECT listing_json FROM carts WHERE telegram_user_id=?", (user_id,)).fetchone()
    return json.loads(row["listing_json"]) if row else None


def _set_cart(user_id, listing):
    with _db() as db:
        db.execute("INSERT OR REPLACE INTO carts VALUES (?, ?, ?)", (user_id, json.dumps(listing), _now()))


def _clear_cart(user_id):
    with _db() as db:
        db.execute("DELETE FROM carts WHERE telegram_user_id=?", (user_id,))


def top_search_demand(limit=10):
    with _db() as db:
        return [dict(row) for row in db.execute("SELECT * FROM search_demand ORDER BY search_count DESC, last_searched_at DESC LIMIT ?", (limit,))]


def recent_searches(limit=10):
    with _db() as db:
        return [dict(row) for row in db.execute("SELECT * FROM search_demand ORDER BY last_searched_at DESC LIMIT ?", (limit,))]


def available_inventory_counts(limit=10):
    with _db() as db:
        return [dict(row) for row in db.execute("SELECT year, make, model, part_catalog_id, COUNT(*) AS listing_count FROM store_inventory WHERE availability_status='available' GROUP BY year, make, model, part_catalog_id ORDER BY listing_count DESC LIMIT ?", (limit,))]


def _persist_search(result, requested_text):
    vehicle = result.get("vehicle", {})
    part = result.get("part", "")
    now = _now()
    _catalog()
    with _db() as db:
        catalog = db.execute("SELECT id FROM part_catalog WHERE source_label=?", (part,)).fetchone()
        catalog_id = catalog["id"] if catalog else None
        db.execute("INSERT INTO search_demand (year,make,model,normalized_make,normalized_model,part_catalog_id,requested_part_text,resolved_part_label,first_searched_at,last_searched_at) VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(year,normalized_make,normalized_model,part_catalog_id) DO UPDATE SET search_count=search_count+1,last_searched_at=excluded.last_searched_at,requested_part_text=excluded.requested_part_text,resolved_part_label=excluded.resolved_part_label", (vehicle.get("year", 0), vehicle.get("make", ""), vehicle.get("model", ""), _norm(vehicle.get("make", "")), _norm(vehicle.get("model", "")), catalog_id, requested_text, part, now, now))
        for listing in result.get("results", []):
            identity = _listing_identity(listing, result)
            db.execute("INSERT INTO store_inventory (year,make,model,part_catalog_id,interchange_label,source_listing_identity,source_price,customer_price,grade,stock_number,mileage,primary_image,availability_status,first_seen_at,last_seen_at,last_verified_at,source_listing_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(source_listing_identity) DO UPDATE SET source_price=excluded.source_price,customer_price=excluded.customer_price,grade=excluded.grade,stock_number=excluded.stock_number,mileage=excluded.mileage,primary_image=excluded.primary_image,availability_status=excluded.availability_status,last_seen_at=excluded.last_seen_at,last_verified_at=excluded.last_verified_at,source_listing_json=excluded.source_listing_json", (vehicle.get("year", 0), vehicle.get("make", ""), vehicle.get("model", ""), catalog_id, result.get("search", {}).get("interchange"), identity, listing.get("price"), listing.get("customer_price") or listing.get("price"), listing.get("grade"), listing.get("stock"), listing.get("miles") or listing.get("mileage"), (listing.get("images") or [None])[0], "available", now, now, now, json.dumps(listing)))
            row = db.execute("SELECT id FROM store_inventory WHERE source_listing_identity=?", (identity,)).fetchone()
            for position, image in enumerate(listing.get("images", [])):
                db.execute("INSERT OR IGNORE INTO store_inventory_images (store_inventory_id,image_reference,position) VALUES (?,?,?)", (row["id"], image, position))


def _listing_identity(listing, result=None):
    action = listing.get("order_action", {})
    stable = {
        "vehicle": {
            "year": listing.get("year") or (result or {}).get("vehicle", {}).get("year"),
            "make": listing.get("make") or (result or {}).get("vehicle", {}).get("make"),
            "model": listing.get("model") or (result or {}).get("vehicle", {}).get("model"),
        },
        "part": listing.get("part") or (result or {}).get("part"),
        "stock": listing.get("stock"),
        "supplier": listing.get("supplier"),
        "gallery": (listing.get("gallery_trigger") or {}).get("onclick"),
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


class _TokenRedactionFilter(logging.Filter):
    def filter(self, record):
        if TOKEN:
            message = record.getMessage().replace(TOKEN, "[REDACTED]")
            record.msg = message
            record.args = ()
        return True


if TOKEN:
    for handler in logging.getLogger().handlers:
        handler.addFilter(_TokenRedactionFilter())


HELP = (
    "<b>Parts Deal Finder</b>\n\n"
    "Send a vehicle and part to search available ORDER PART listings.\n\n"
    "Example: <code>2021 BMW M4 spindle knuckle</code>"
)
def _menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Find a Part", callback_data="m:find")],
        [InlineKeyboardButton("My Garage", callback_data="m:garage"), InlineKeyboardButton("My Cart", callback_data="m:cart")],
        [InlineKeyboardButton("My Orders", callback_data="m:orders"), InlineKeyboardButton("How It Works", callback_data="m:how")],
        [InlineKeyboardButton("Support", callback_data="m:support")],
    ])


WELCOME = "<b>Auto Car Part Finder</b>\n\nFind used auto parts from our nationwide inventory.\n\nSearch by entering:\nYear + Make + Model + Part\n\nExample: <code>2021 BMW M4 Spindle</code>"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_active_search(update.effective_user.id)
    context.user_data.clear()
    await update.message.reply_text(
        WELCOME, parse_mode=ParseMode.HTML, reply_markup=_menu_keyboard()
    )


async def menu_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    if action in ("find_text", "home"):
        if action == "home":
            _new_search_state(update.effective_user.id)
        context.user_data.pop("vehicle", None)
    if action == "find":
        vehicles = _garage(update.effective_user.id)
        if vehicles:
            buttons = [[InlineKeyboardButton(f"{v['year']} {v['make']} {v['model']}", callback_data=f"g:use:{v['id']}")] for v in vehicles]
            buttons.append([InlineKeyboardButton("Enter Another Vehicle", callback_data="m:find_text")])
            buttons.append([InlineKeyboardButton("My Garage", callback_data="m:garage"), InlineKeyboardButton("Main Menu", callback_data="m:home")])
            await query.edit_message_text("Which vehicle are you shopping for?", reply_markup=InlineKeyboardMarkup(buttons))
        else:
            await query.edit_message_text("Tell us what you're looking for.\n\nSend:\nYear + Make + Model + Part\n\nExample: <code>2021 BMW M4 Spindle</code>", parse_mode=ParseMode.HTML, reply_markup=_back_menu_keyboard())
    elif action == "find_text":
        context.user_data["awaiting_vehicle"] = True
        await query.edit_message_text("Send the year, make, and model, followed by the part.\n\nExample: <code>2021 BMW M4 Spindle</code>", parse_mode=ParseMode.HTML, reply_markup=_back_menu_keyboard())
    elif action == "search_another":
        state = _state(update.effective_user.id)
        if state and state.get("vehicle"):
            _clear_dependent_state(state)
            context.user_data["vehicle"] = state["vehicle"]
            await query.edit_message_text(f"What part are you looking for on {state['vehicle']['year']} {state['vehicle']['make']} {state['vehicle']['model']}?", reply_markup=_back_menu_keyboard())
        else:
            await query.edit_message_text("Choose a vehicle to start a new search.", reply_markup=_menu_keyboard())
    elif action == "choose_vehicle":
        _new_search_state(update.effective_user.id)
        context.user_data.pop("vehicle", None)
        await query.edit_message_text("Which vehicle are you shopping for?", reply_markup=_menu_keyboard())
    elif action == "garage":
        await show_garage(query.message, update.effective_user.id)
    elif action == "cart":
        await show_cart(query.message, update.effective_chat.id)
    elif action == "orders":
        await query.edit_message_text("You don't have any orders yet.", reply_markup=_back_menu_keyboard())
    elif action == "how":
        await query.edit_message_text("1. Search for your vehicle and part.\n2. Compare available parts and photos.\n3. Add the part you want to your cart.\n4. Complete checkout.\n5. Track your order.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Find a Part", callback_data="m:find"), InlineKeyboardButton("Main Menu", callback_data="m:home")]]))
    elif action == "support":
        await query.edit_message_text("Need help finding a part or with an order?", reply_markup=_back_menu_keyboard())
    else:
        await query.edit_message_text(WELCOME, parse_mode=ParseMode.HTML, reply_markup=_menu_keyboard())


def _back_menu_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Main Menu", callback_data="m:home")]])


async def show_garage(message, user_id):
    vehicles = _garage(user_id)
    buttons = [[InlineKeyboardButton(f"{v['year']} {v['make']} {v['model']}" + (" (Default)" if v['is_default'] else ""), callback_data=f"g:view:{v['id']}")] for v in vehicles]
    buttons.append([InlineKeyboardButton("Add Vehicle", callback_data="g:add")])
    buttons.append([InlineKeyboardButton("Main Menu", callback_data="m:home")])
    await message.reply_text("<b>My Garage</b>\n\nChoose a vehicle or add another one.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(buttons))


async def garage_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]
    user_id = update.effective_user.id
    if action == "grade":
        await query.edit_message_text("Condition Preference\n\nChoose the condition range you'd like to see.", reply_markup=_grade_keyboard(parts[2]))
    elif action == "setgrade":
        modes = {"best": ("Best Available", None, "grade"), "a": ("A Only", ["A"], "grade"), "ab": ("A - B", ["A", "B"], "grade"), "ac": ("A - C", ["A", "B", "C"], "grade"), "any": ("Any Condition", None, "grade")}
        mode, allowed, source = modes.get(parts[3], modes["any"])
        session = SESSIONS.get(user_id, {}).get(parts[2], {})
        session["grade_filter"] = {"grade_filter_mode": mode, "allowed_grades": allowed, "source_grade_preference": source}
        await query.edit_message_text(f"Condition preference: {mode}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Back to Results", callback_data=f"b:{parts[2]}")], [InlineKeyboardButton("Main Menu", callback_data="m:home")]]))
    elif action == "backgrade":
        await query.edit_message_text("Choose a condition preference.", reply_markup=_back_menu_keyboard())
    elif action == "save":
        pending = context.user_data.pop("pending_vehicle", None)
        if not pending or pending.get("token") != parts[2]:
            await query.edit_message_text("That vehicle request has expired.", reply_markup=_back_menu_keyboard())
            return
        _, created = _save_vehicle(user_id, pending["year"], pending["make"], pending["model"])
        await query.edit_message_text("Vehicle saved to My Garage." if created else "This vehicle is already in your garage.", reply_markup=_back_menu_keyboard())
    elif action == "add":
        context.user_data["garage_add"] = True
        await query.edit_message_text("Send a vehicle such as <code>2020 Infiniti Q50</code>.", parse_mode=ParseMode.HTML, reply_markup=_back_menu_keyboard())
    elif action == "use":
        vehicle = next((v for v in _garage(user_id) if str(v["id"]) == parts[2]), None)
        if not vehicle:
            await query.edit_message_text("That vehicle is no longer in your garage.", reply_markup=_back_menu_keyboard())
            return
        context.user_data["vehicle"] = vehicle
        context.user_data["awaiting_part"] = True
        await query.edit_message_text(f"I have your {vehicle['year']} {vehicle['make']} {vehicle['model']}. What part are you looking for?", reply_markup=_back_menu_keyboard())
    elif action == "view":
        vehicle = next((v for v in _garage(user_id) if str(v["id"]) == parts[2]), None)
        if vehicle:
            await query.edit_message_text(f"<b>{vehicle['year']} {vehicle['make']} {vehicle['model']}</b>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Find Parts", callback_data=f"g:use:{vehicle['id']}"), InlineKeyboardButton("Remove", callback_data=f"g:remove:{vehicle['id']}")], [InlineKeyboardButton("Set as Default", callback_data=f"g:default:{vehicle['id']}"), InlineKeyboardButton("Back", callback_data="m:garage")]]))
    elif action in ("remove", "default"):
        with _db() as db:
            if action == "remove":
                db.execute("DELETE FROM garage_vehicles WHERE id=? AND telegram_user_id=?", (parts[2], user_id))
            else:
                db.execute("UPDATE garage_vehicles SET is_default=0 WHERE telegram_user_id=?", (user_id,))
                db.execute("UPDATE garage_vehicles SET is_default=1 WHERE id=? AND telegram_user_id=?", (parts[2], user_id))
        await show_garage(query.message, user_id)


async def search_request(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.message or update.edited_message
    query = message.text.strip()
    user_id = update.effective_user.id
    navigation = query.casefold()
    if navigation in {"menu", "main menu"}:
        await _send_menu(message)
        return
    if navigation in {"start over", "new search"}:
        await new_search_command(update, context)
        return
    if navigation == "cancel":
        await cancel_command(update, context)
        return
    incoming_words = query.split()
    if incoming_words and incoming_words[0].isdigit() and len(incoming_words) >= 3:
        _new_search_state(user_id)
        context.user_data.pop("vehicle", None)
        context.user_data.pop("awaiting_part", None)
    if context.user_data.pop("awaiting_vehicle", False) and len(query.split()) >= 3 and query.split()[0].isdigit():
        vehicle_words = query.split()
        year, make, model = int(vehicle_words[0]), vehicle_words[1], " ".join(vehicle_words[2:])
        token = uuid.uuid4().hex[:8]
        context.user_data["pending_vehicle"] = {"token": token, "year": year, "make": make, "model": model}
        await message.reply_text(f"Save this vehicle?\n\n{html.escape(query)}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Save Vehicle", callback_data=f"g:save:{token}"), InlineKeyboardButton("Change Vehicle", callback_data="g:add")], [InlineKeyboardButton("Cancel", callback_data="m:home")]]))
        return
    if context.user_data.pop("garage_add", False):
        vehicle_text = query.split()
        if len(vehicle_text) < 3 or not vehicle_text[0].isdigit():
            await update.message.reply_text("Please send a vehicle like <code>2020 Infiniti Q50</code>.", parse_mode=ParseMode.HTML, reply_markup=_back_menu_keyboard())
            return
        year, make, model = int(vehicle_text[0]), vehicle_text[1], " ".join(vehicle_text[2:])
        token = uuid.uuid4().hex[:8]
        context.user_data["pending_vehicle"] = {"token": token, "year": year, "make": make, "model": model}
        await update.message.reply_text(f"Save this vehicle?\n\n{html.escape(query)}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Save Vehicle", callback_data=f"g:save:{token}"), InlineKeyboardButton("Change Vehicle", callback_data="g:add")], [InlineKeyboardButton("Cancel", callback_data="m:home")]]))
        return
    if context.user_data.get("vehicle") and not (words := query.split())[0].isdigit():
        vehicle = context.user_data["vehicle"]
        state = _state(user_id) or _new_search_state(user_id, vehicle)
        _clear_dependent_state(state)
        state["requested_part_text"] = query
        candidates = resolve_part_query(query, vehicle)
        if len(candidates) > 1:
            _transition(user_id, state, "PART_DISAMBIGUATION")
            state["part_candidates"] = candidates[:20]
            SESSIONS.setdefault(user_id, {})[state["search_id"]] = state
            await message.reply_text("I found a few possible matches.\n\nWhich part do you mean?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(item["source_label"], callback_data=f"q:{state['search_id']}:{index}")] for index, item in enumerate(candidates[:8])] + [[InlineKeyboardButton("Search Again", callback_data="m:find"), InlineKeyboardButton("Main Menu", callback_data="m:home")]]))
            return
        if not candidates:
            await message.reply_text("I couldn't match that part name yet.\n\nTry another name or choose from available parts.", reply_markup=_recovery_keyboard())
            return
        state["resolved_part"] = candidates[0]
        query = f"{vehicle['year']} {vehicle['make']} {vehicle['model']} {query}"
        context.user_data.pop("awaiting_part", None)

    words = query.split()
    if len(words) >= 3 and words[0].isdigit():
        models = []
        try:
            models = json.loads((Path(__file__).resolve().parent / "data" / "resolver_options.json").read_text()).get("models", [])
        except (OSError, ValueError):
            pass
        model = next((value for value in sorted(models, key=len, reverse=True) if _norm(" ".join(words[1:])).startswith(_norm(value))), None)
        if model:
            part_text = " ".join(words[1:])[len(model):].strip()
            vehicle = {"year": int(words[0]), "make": model.split()[0], "model": " ".join(model.split()[1:])}
            state = _new_search_state(user_id, vehicle)
            state["requested_part_text"] = part_text or None
            ranked = _part_matches(part_text) if part_text else []
            exact_candidate = ranked and ranked[0][1]["normalized_label"] == _norm(part_text)
            if ranked and (len(ranked) == 1 or exact_candidate):
                state["resolved_part"] = ranked[0][1]
                query = f"{words[0]} {model} {ranked[0][1]['source_label']}"
            elif ranked:
                _transition(user_id, state, "PART_DISAMBIGUATION")
                state["part_candidates"] = [item for _, item in ranked[:20]]
                SESSIONS.setdefault(user_id, {})[state["search_id"]] = state
                await message.reply_text("I found a few possible matches.\n\nWhich part do you mean?", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(item[1]["source_label"], callback_data=f"q:{state['search_id']}:{index}")] for index, item in enumerate(ranked[:8])] + [[InlineKeyboardButton("Try Another Name", callback_data="m:find"), InlineKeyboardButton("Main Menu", callback_data="m:home")]]))
                return
    if len(words) < 4 or not words[0].isdigit():
        await message.reply_text(
            "I can help with that.\n\nI still need the year, model, and part.\n\nExample: <code>2021 BMW M4 Spindle</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Try Again", callback_data="m:find"), InlineKeyboardButton("Main Menu", callback_data="m:home")]])
        )
        return

    status = await message.reply_text("Searching parts inventory...")
    active_state = _state(user_id) or _new_search_state(user_id)
    _transition(user_id, active_state, "SEARCHING")
    active_state["requested_part_text"] = query
    SESSIONS[user_id] = {active_state["search_id"]: active_state}

    try:
        result = await asyncio.to_thread(_run_engine_search, query)
        if _state(user_id) is not active_state or _state(user_id).get("search_id") != active_state["search_id"]:
            logging.info("STALE_SEARCH_RESULT_IGNORED")
            return
        active_state["search_in_progress"] = False
        result_status = result.get("status") if isinstance(result, dict) else "search_failed"

        if result_status != "ok":
            await send_search_error(status, result, update.effective_chat.id)
            return

        results = result.get("results", [])
        if not results:
            await status.edit_text("I couldn't find an available match for that exact part.\n\nWant to try another part?", reply_markup=_recovery_keyboard())
            return
        _save_state(user_id, {"request": query, "vehicle": result.get("vehicle"), "part": result.get("part"), "results": len(results)})
        await send_result_set(status, update, result, query)

    except Exception:
        active_state["search_in_progress"] = False
        logging.exception("Search request failed")
        await status.edit_text("I'm having trouble checking availability right now, but I saved your search.", reply_markup=_recovery_keyboard())


def _search_summary(result):
    vehicle = result.get("vehicle", {})
    search = result.get("search", {})
    pagination = result.get("pagination", {})
    title = f"{vehicle.get('year', '')} {vehicle.get('make', '')} {vehicle.get('model', '')}".strip()
    lines = [
        f"<b>{html.escape(title)}</b>",
        html.escape(str(result.get("part", ""))),
        f"{len(result.get('results', []))} parts found",
        "Choose an option below.",
    ]
    interchange = search.get("interchange")
    if interchange:
        lines.insert(2, html.escape(_customer_choice_label(interchange)))
    return "\n".join(lines)


def _customer_choice_label(label):
    return re.sub(r"\bLH\b", "Left", re.sub(r"\bRH\b", "Right", str(label), flags=re.I), flags=re.I)


def _recovery_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Modify Search", callback_data="m:find"), InlineKeyboardButton("New Search", callback_data="m:find")], [InlineKeyboardButton("Main Menu", callback_data="m:home")]])


def _refinement_keyboard(session_id, result):
    choices = result.get("search", {}).get("available_interchange_choices", [])
    buttons = [[InlineKeyboardButton("Condition", callback_data=f"g:grade:{session_id}")]]
    if not choices:
        return InlineKeyboardMarkup(buttons)
    buttons.insert(0, [InlineKeyboardButton("All Configurations", callback_data=f"r:{session_id}:b")])
    buttons.extend([[InlineKeyboardButton(_customer_choice_label(choice), callback_data=f"r:{session_id}:{index}")] for index, choice in enumerate(choices)])
    return InlineKeyboardMarkup(buttons)


def _result_page_keyboard(session_id, page, total):
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("Previous", callback_data=f"v:{session_id}:{page - 1}"))
    if page + 1 < total:
        buttons.append(InlineKeyboardButton("Next Results", callback_data=f"v:{session_id}:{page + 1}"))
    buttons.append(InlineKeyboardButton("Modify Search", callback_data="m:find"))
    buttons.append(InlineKeyboardButton("Search Another Part", callback_data="m:search_another"))
    buttons.append(InlineKeyboardButton("Choose Another Vehicle", callback_data="m:choose_vehicle"))
    buttons.append(InlineKeyboardButton("Main Menu", callback_data="m:home"))
    return InlineKeyboardMarkup([buttons])


async def _send_result_page(message, session_id, session, page):
    results = list(session.get("results", {}).items())
    page_size = 5
    total = max(1, (len(results) + page_size - 1) // page_size)
    page = max(0, min(page, total - 1))
    for index, item in results[page * page_size:(page + 1) * page_size]:
        await send_result(message, item, session_id, index)
    await message.reply_text(f"Showing results {page * page_size + 1}-{min((page + 1) * page_size, len(results))} of {len(results)}.", reply_markup=_result_page_keyboard(session_id, page, total))


def _grade_keyboard(session_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Best Available", callback_data=f"g:setgrade:{session_id}:best"), InlineKeyboardButton("A Only", callback_data=f"g:setgrade:{session_id}:a")],
        [InlineKeyboardButton("A - B", callback_data=f"g:setgrade:{session_id}:ab"), InlineKeyboardButton("A - C", callback_data=f"g:setgrade:{session_id}:ac")],
        [InlineKeyboardButton("Any Condition", callback_data=f"g:setgrade:{session_id}:any")],
        [InlineKeyboardButton("Back", callback_data=f"g:backgrade:{session_id}")],
    ])


def _broad_request(result, request):
    interchange = result.get("search", {}).get("interchange")
    if interchange and request.lower().endswith(" " + str(interchange).lower()):
        return request[: -(len(str(interchange)) + 1)].strip()
    return request


async def send_search_error(status_message, result, chat_id):
    status = result.get("status") if isinstance(result, dict) else "search_failed"
    if status in ("choice_not_found", "needs_interchange_choice"):
        raw_choices = result.get("available_choices", result.get("choices", []))
        choices = [item.get("label", "") if isinstance(item, dict) else str(item) for item in raw_choices]
        text = "Requested configuration: " + html.escape(str(result.get("requested_choice", "unknown")))
        if status == "needs_interchange_choice":
            text = "Which configuration do you need?"
        else:
            text += "\nAvailable: " + " | ".join(html.escape(str(choice)) for choice in choices)
        session_id = uuid.uuid4().hex[:10]
        state = _state(chat_id) or _new_search_state(chat_id)
        state.update({
            "stage": "AWAITING_INTERCHANGE",
            "search_id": session_id,
            "interchange_candidates": choices,
            "selected_interchange": None,
            "search_in_progress": False,
        })
        SESSIONS[chat_id] = {
            session_id: {
                "stage": "AWAITING_INTERCHANGE",
                "search_id": session_id,
                "search_in_progress": False,
                "request": result.get("base_request", result.get("request", "")),
                "results": {},
                "refinements": choices,
                "base_request": result.get("base_request", result.get("request", "")),
            }
        }
        SESSIONS[chat_id][session_id].update(state)
        buttons = [[InlineKeyboardButton(_customer_choice_label(choice), callback_data=f"r:{session_id}:{index}")] for index, choice in enumerate(choices)]
        await status_message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons))
        return
    messages = {
        "invalid_prompt": "Please send a year, vehicle, and part.",
        "vehicle_not_found": "I couldn't identify that vehicle. Try another vehicle.",
        "part_not_found": "I couldn't identify that part. Try another part.",
        "no_inventory": "I couldn't find an available match for that exact part.",
        "result_page_failure": "I'm having trouble checking availability right now, but I saved your search.",
        "pagination_failure": "I'm having trouble checking availability right now, but I saved your search.",
    }
    await status_message.edit_text(messages.get(status, "I'm having trouble checking availability right now, but I saved your search."), reply_markup=_recovery_keyboard())


def _field(result, *names):
    for name in names:
        value = result.get(name)
        if value not in (None, ""):
            return str(value)
    return ""


def _listing_caption(result):
    labels = (
        ("Vehicle", ("vehicle",)),
        ("Part", ("part",)),
        ("Description", ("description",)),
        ("Price", ("price",)),
        ("Stock", ("stock",)),
        ("Seller / Yard", ("supplier", "seller", "yard")),
        ("Location", ("location",)),
        ("Warranty", ("warranty",)),
        ("Grade / Condition", ("grade", "condition")),
        ("Distance", ("distance",)),
    )
    vehicle = " ".join(_field(result, key) for key in ("year", "make", "model") if _field(result, key))
    values = [("Vehicle", vehicle)] + [
        (label, _field(result, *names)) for label, names in labels[1:]
    ]
    lines = ["<b>ORDER PART LISTING</b>"]
    lines.extend(f"<b>{label}:</b> {html.escape(value)}" for label, value in values if value)
    return "\n".join(lines)


def _storefront_caption(result):
    part = html.escape(str(result.get("part", "Part"))).upper()
    vehicle = " ".join(
        html.escape(str(result.get(name, "")))
        for name in ("year", "make", "model")
        if result.get(name)
    )
    cells = result.get("raw_cells", [])
    description = result.get("customer_description") or sanitize_customer_text(result.get("description") or (cells[1] if len(cells) > 1 else ""))
    condition = result.get("grade") or result.get("condition") or ""
    lines = [f"<b>{part}</b>", vehicle]
    if description:
        lines.append(html.escape(str(description)))
    if condition:
        lines.append(f"Condition: {html.escape(str(condition))}")
    if result.get("stock"):
        lines.append(f"Stock #: {html.escape(str(result['stock']))}")
    if _customer_price(result):
        lines.append(f"Price: {html.escape(str(_customer_price(result)))}")
    image_count = len(result.get("images", []))
    if image_count:
        lines.append(f"{image_count} photo{'s' if image_count != 1 else ''} available")
    return "\n".join(lines)


def _image_path(photo):
    path = Path(photo)
    return path if path.is_absolute() else Path(__file__).resolve().parent / path


def _image_reference(photo):
    if str(photo).startswith(("https://", "http://")):
        return str(photo)
    return _image_path(photo)


async def send_result(update, result, session_id, result_index):
    """
    Accepts a result dictionary from the scraper.

    Supported fields:
      year
      make
      model
      part
      price
      yard
      location
      stock
      description
      phone
      url
      images / photos
    """

    if not isinstance(result, dict) or not result.get("orderable"):
        return
    target = getattr(update, "message", update)
    caption = _storefront_caption(result)
    valid_photos = []
    for photo in result.get("images", []):
        reference = _image_reference(photo)
        if isinstance(reference, Path) and not (reference.is_file() and reference.stat().st_size > 0):
            continue
        valid_photos.append(reference)
    if not valid_photos and result.get("primary_image"):
        valid_photos = [_image_reference(result["primary_image"])]
    valid_photos = valid_photos[:1]
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("View Photos", callback_data=f"p:{session_id}:{result_index}"),
        InlineKeyboardButton("Choose This Part", callback_data=f"s:{session_id}:{result_index}"),
    ]])
    if valid_photos:
        first = valid_photos[0]
        if isinstance(first, Path):
            with first.open("rb") as image:
                await target.reply_photo(
                    photo=image,
                    caption=caption[:1024],
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
        else:
            await target.reply_photo(
                photo=first,
                caption=caption[:1024],
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
    else:
        await target.reply_text(caption, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, session_id, result_index = query.data.split(":", 2)
    session = SESSIONS.get(update.effective_chat.id, {}).get(session_id, {})
    result = session.get("results", {}).get(result_index)
    if not result:
        await query.edit_message_text("That result has expired. Please search again.")
        return
    session["selected_listing"] = result_index
    await query.message.reply_text(
        "<b>Part selected</b>\n\n" + _storefront_caption(result) +
        "\n\nAdd this part to your cart when you're ready.",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Add to Cart", callback_data=f"a:{session_id}:{result_index}"), InlineKeyboardButton("View Photos", callback_data=f"p:{session_id}:{result_index}")], [InlineKeyboardButton("Back to Results", callback_data=f"b:{session_id}")]]),
    )


async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, session_id, result_index = query.data.split(":", 2)
    session = SESSIONS.get(update.effective_chat.id, {}).get(session_id, {})
    result = session.get("results", {}).get(result_index)
    if not result:
        await query.message.reply_text("That listing has expired. Please search again.")
        return
    _set_cart(update.effective_user.id, result)
    USER_CARTS[update.effective_user.id] = result
    await query.edit_message_text("Added to your cart.\n\n" + _cart_text(result), reply_markup=_cart_keyboard())


def _customer_price(result):
    return result.get("customer_price") or result.get("price") or "Price unavailable"


def _cart_text(result):
    vehicle = " ".join(str(result.get(key, "")) for key in ("year", "make", "model") if result.get(key))
    part = str(result.get("part") or "Part")
    return f"<b>Your Cart</b>\n\n{html.escape(vehicle)}\n{html.escape(part)}\n\nPrice: {html.escape(str(_customer_price(result)))}\n\nSubtotal: {html.escape(str(_customer_price(result)))}\nShipping: Calculated at checkout\nTotal: {html.escape(str(_customer_price(result)))} + shipping"


def _cart_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("Checkout", callback_data="c:checkout"), InlineKeyboardButton("Remove", callback_data="c:remove")], [InlineKeyboardButton("Continue Shopping", callback_data="m:find"), InlineKeyboardButton("Main Menu", callback_data="m:home")]])


async def cart_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    chat_id = update.effective_chat.id
    if action == "remove":
        _clear_cart(update.effective_user.id)
        USER_CARTS.pop(chat_id, None)
        await query.edit_message_text("Your cart is empty.", reply_markup=_back_menu_keyboard())
    elif action == "checkout":
        if not _cart(update.effective_user.id):
            await query.edit_message_text("Your cart is empty.", reply_markup=_back_menu_keyboard())
        else:
            context.user_data["checkout_state"] = "details"
            await query.edit_message_text("Checkout is not taking payments yet.\n\nPlease send your name and shipping address, or press Cancel.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="c:cancel")]]))
    elif action == "cancel":
        context.user_data.pop("checkout_state", None)
        await query.edit_message_text("Checkout cancelled.", reply_markup=_cart_keyboard() if _cart(update.effective_user.id) else _back_menu_keyboard())


async def show_cart(message, chat_id):
    result = _cart(chat_id)
    if not result:
        await message.reply_text("Your cart is empty.", reply_markup=_back_menu_keyboard())
    else:
        await message.reply_text(_cart_text(result), parse_mode=ParseMode.HTML, reply_markup=_cart_keyboard())


async def part_candidate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, token, index = query.data.split(":", 2)
    state = SESSIONS.get(update.effective_chat.id, {}).get(token, {})
    if not state or state.get("search_id") != token:
        await query.answer("This search has expired. Start a new search.", show_alert=False)
        return
    candidates = state.get("part_candidates", [])
    vehicle = state.get("vehicle")
    try:
        part = candidates[int(index)]["source_label"]
    except (IndexError, ValueError):
        await query.edit_message_text("That part option is no longer available.", reply_markup=_back_menu_keyboard())
        return
    context.user_data["vehicle"] = vehicle
    state["resolved_part"] = candidates[int(index)]
    _transition(update.effective_user.id, state, "AWAITING_INTERCHANGE")
    await query.edit_message_text("Checking available versions...")
    result = await asyncio.to_thread(_run_engine_search, f"{vehicle['year']} {vehicle['make']} {vehicle['model']} {part}")
    if result.get("status") != "ok":
        await send_search_error(query.message, result, update.effective_chat.id)
        return
    await send_result_set(query.message, update, result, f"{vehicle['year']} {vehicle['make']} {vehicle['model']} {part}")


async def candidate_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, token, index = query.data.split(":", 2)
    state = SESSIONS.get(update.effective_chat.id, {}).get(token, {})
    try:
        part = state["part_candidates"][int(index)]["source_label"]
    except (KeyError, IndexError, ValueError):
        await query.edit_message_text("That part option is no longer available.", reply_markup=_back_menu_keyboard())
        return
    vehicle = state["vehicle"]
    await query.edit_message_text("Searching inventory...")
    result = await asyncio.to_thread(search_parts, f"{vehicle['year']} {vehicle['make']} {vehicle['model']} {part}")
    if result.get("status") != "ok":
        await send_search_error(query.message, result, update.effective_chat.id)
        return
    await send_result_set(query.message, update, result, f"{vehicle['year']} {vehicle['make']} {vehicle['model']} {part}")


async def photos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, session_id, result_index = query.data.split(":", 2)
    session = SESSIONS.get(update.effective_chat.id, {}).get(session_id, {})
    result = session.get("results", {}).get(result_index)
    if not result:
        await query.message.reply_text("That listing has expired. Please search again.")
        return
    if result.get("gallery_status") == "unknown" and not result.get("images"):
        result = await asyncio.to_thread(capture_listing_gallery, result)
        session["results"][result_index] = result
    photos = [
        path for photo in result.get("images", [])
        if (path := _image_path(photo)).is_file() and path.stat().st_size > 0
    ]
    if len(photos) <= 1:
        await query.answer("No additional photos are available for this listing.", show_alert=False)
        return
    for start in range(1, len(photos), 10):
        batch = photos[start:start + 10]
        media = []
        handles = []
        try:
            for path in batch:
                handle = path.open("rb")
                handles.append(handle)
                media.append(InputMediaPhoto(media=handle))
            await query.message.reply_media_group(media=media)
        except Exception:
            logging.exception("Failed to send listing photo album")
            await query.message.reply_text("Some additional photos could not be displayed.")
        finally:
            for handle in handles:
                handle.close()


async def back_to_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, session_id = query.data.split(":", 1)
    session = SESSIONS.get(update.effective_chat.id, {}).get(session_id, {})
    result_map = session.get("results", {})
    if not result_map:
        await query.edit_message_text("That search has expired. Please search again.", reply_markup=_back_menu_keyboard())
        return
    await query.edit_message_text("Choose a part from your search results.")
    for index, result in result_map.items():
        await send_result(query.message, result, session_id, index)


async def send_result_set(status, update, result, query):
    chat_id = update.effective_chat.id
    state = _state(chat_id) or _new_search_state(chat_id)
    session_id = state["search_id"]
    results = result.get("results", [])
    _persist_search(result, query)
    state.update({"search_id": session_id, "search_in_progress": False, "request": query, "results": {str(i): item for i, item in enumerate(results)}, "refinements": result.get("search", {}).get("available_interchange_choices", []), "base_request": _broad_request(result, query), "interchange_candidates": result.get("search", {}).get("available_interchange_choices", []), "selected_interchange": result.get("search", {}).get("interchange")})
    _transition(chat_id, state, "SHOWING_RESULTS")
    SESSIONS[chat_id] = {session_id: state}
    await status.edit_text(_search_summary(result), parse_mode=ParseMode.HTML, reply_markup=_refinement_keyboard(session_id, result))
    target = getattr(update, "message", None) or status
    await _send_result_page(target, session_id, SESSIONS[chat_id][session_id], 0)


async def refinement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, session_id, choice_index = query.data.split(":", 2)
    session = SESSIONS.get(update.effective_chat.id, {}).get(session_id, {})
    if not session or session.get("search_id") != session_id:
        await query.answer("This search has expired. Start a new search.", show_alert=False)
        return
    base_request = session.get("base_request")
    choices = session.get("refinements", [])
    if session.get("search_in_progress"):
        await query.answer("This search is already running.")
        return
    if not base_request:
        await query.edit_message_text("That search has expired. Please search again.")
        return
    if choice_index == "b":
        request = base_request
    else:
        try:
            request = f"{base_request} {choices[int(choice_index)]}"
        except (IndexError, ValueError):
            await query.edit_message_text("That configuration is no longer available.")
            return
    status = await query.edit_message_text("Searching refined inventory...")
    _transition(update.effective_chat.id, session, "SEARCHING")
    session["search_in_progress"] = True
    selected_choice = None if choice_index == "b" else choices[int(choice_index)]
    result = await asyncio.to_thread(_run_engine_search, request, True, selected_choice)
    if result.get("status") != "ok":
        await send_search_error(status, result, update.effective_chat.id)
        return
    results = result.get("results", [])
    await send_result_set(status, update, result, request)


async def result_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, session_id, page = query.data.split(":", 2)
    session = SESSIONS.get(update.effective_chat.id, {}).get(session_id, {})
    if not session or session.get("search_id") != session_id:
        await query.answer("This search has expired. Start a new search.", show_alert=False)
        return
    await _send_result_page(query.message, session_id, session, int(page))


async def order_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action, session_id, result_index = query.data.split(":", 2)
    session = SESSIONS.get(update.effective_chat.id, {}).get(session_id, {})
    result = session.get("results", {}).get(result_index)
    key = (update.effective_chat.id, session_id, result_index)
    if action == "x":
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("Order cancelled. No transaction was made.")
        return
    if not result or key in ORDER_LOCKS:
        await query.message.reply_text("This order action is no longer available.")
        return
    ORDER_LOCKS.add(key)
    await query.edit_message_reply_markup(reply_markup=None)
    try:
        outcome = await asyncio.to_thread(place_order, result, confirmed=True)
        if outcome.get("success"):
            await query.message.reply_text("Order submitted. Stock: " + html.escape(str(outcome.get("stock", "unknown"))))
        else:
            await query.message.reply_text("Order was not completed. Please stop and verify with the seller.")
    except Exception:
        logging.exception("Order failed")
        await query.message.reply_text("Order could not be completed. Please try again later.")


async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    logging.exception(
        "Telegram error",
        exc_info=context.error
    )


def main():
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    print("=" * 55)
    print("PARTS DEAL FINDER — TELEGRAM")
    print("=" * 55)
    print("Starting bot...")

    app = Application.builder().token(TOKEN).post_init(set_command_menu).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("find", find_command))
    app.add_handler(CommandHandler("garage", garage_command))
    app.add_handler(CommandHandler("cart", cart_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("newsearch", new_search_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_command))

    app.add_handler(
        CallbackQueryHandler(selection, pattern=r"^s:[^:]+:\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(photos, pattern=r"^p:[^:]+:\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(add_to_cart, pattern=r"^a:[^:]+:\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(back_to_results, pattern=r"^b:[^:]+$")
    )
    app.add_handler(
        CallbackQueryHandler(cart_action, pattern=r"^c:(?:checkout|remove|cancel)$")
    )
    app.add_handler(
        CallbackQueryHandler(menu_action, pattern=r"^m:(?:find|find_text|garage|cart|orders|how|support|home|search_another|choose_vehicle)$")
    )
    app.add_handler(
        CallbackQueryHandler(garage_action, pattern=r"^g:(?:add|save|use|view|remove|default|grade|setgrade|backgrade):?.*$")
    )
    app.add_handler(
        CallbackQueryHandler(part_candidate, pattern=r"^q:[^:]+:\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(candidate_search, pattern=r"^qgo:[^:]+:\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(result_page, pattern=r"^v:[^:]+:\d+$")
    )
    app.add_handler(
        CallbackQueryHandler(refinement, pattern=r"^r:[^:]+:(?:\d+|b)$")
    )
    app.add_handler(
        CallbackQueryHandler(order_action, pattern=r"^[cx]:[^:]+:\d+$")
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search_request,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.UpdateType.EDITED_MESSAGE & filters.TEXT,
            search_request,
        )
    )

    app.add_error_handler(error_handler)

    print("BOT ONLINE")
    print("Waiting for Telegram messages...")
    print("=" * 55)

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
