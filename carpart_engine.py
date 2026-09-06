import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


LOGIN_URL = "https://pro-ky.car-part.com/cgi-bin/proSearch.cgi"
PROFILE = "v1"
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
IMAGE_DIR = ROOT / "images"


class ChoiceNotFoundError(ValueError):
    def __init__(self, requested_choice, available_choices):
        self.requested_choice = requested_choice
        self.available_choices = available_choices
        super().__init__(f"Requested interchange choice not found: {requested_choice}")


class ChoiceRequiredError(ValueError):
    def __init__(self, choices):
        self.choices = choices
        super().__init__("An interchange choice is required")


def _parse_prompt(prompt):
    words = prompt.split()
    if len(words) < 4 or not words[0].isdigit():
        raise ValueError("Prompt must look like '2021 BMW M4 spindle knuckle'")
    requested_interchange = None
    remaining = " ".join(words[1:])
    match = re.search(r"\s+(w/(?:o\s+)?[^\s]+(?:\s+[^\s]+)?)$", remaining, re.I)
    if match:
        requested_interchange = match.group(1).strip()
        remaining = remaining[:match.start()].strip()
    return int(words[0]), remaining, requested_interchange


def _login(page):
    username = os.environ.get("CARPART_USERNAME")
    password = os.environ.get("CARPART_PASSWORD")
    if not username or not password:
        raise RuntimeError("CARPART_USERNAME and CARPART_PASSWORD must be set")
    page.goto(LOGIN_URL, wait_until="domcontentloaded")
    page.locator('input[name="username"]:visible').fill(username)
    page.locator('button:visible').first.click()
    page.locator('input[name="password"]:visible').wait_for(timeout=15000)
    page.locator('input[name="password"]:visible').fill(password)
    page.locator('button:visible').first.click()
    page.wait_for_load_state("domcontentloaded")


def _search(page, year, vehicle_and_part, requested_interchange=None):
    page.goto("https://cpprohomeky.car-part.com/", wait_until="domcontentloaded")

    cache_path = DATA_DIR / "resolver_options.json"
    cache = {
        "years": [],
        "models": [],
        "parts": [],
    }
    if cache_path.exists():
        try:
            cache.update(json.loads(cache_path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def option_labels(selector):
        return [
            label.strip()
            for label in page.locator(f"{selector} option").all_text_contents()
            if label.strip() and not label.strip().lower().startswith("select ")
        ]

    live_options = {
        "years": option_labels("#year_dropdown"),
        "models": option_labels("#model_dropdown"),
        "parts": option_labels("#part_dropdown"),
    }
    live_part_options = page.locator("#part_dropdown option").evaluate_all(
        "options => options.map(option => ({source_label: option.textContent.trim(), source_value: option.value}))"
        ".filter(option => option.source_label && !option.source_label.toLowerCase().startsWith('select '))"
    )
    for name, values in live_options.items():
        cache[name] = sorted(set(cache.get(name, [])).union(values))
    cache["part_options"] = live_part_options or cache.get("part_options", [])
    DATA_DIR.mkdir(exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2), encoding="utf-8")

    words = vehicle_and_part.split()
    if len(words) < 2:
        raise ValueError("SEARCH_FAILED: prompt needs a vehicle and part")

    def normalize(value):
        return re.findall(r"[a-z0-9]+", value.lower())

    def type_smartbox(selector, value, list_selector):
        box = page.locator(selector)
        box.click()
        box.press("Control+A")
        box.press("Backspace")
        box.type(value, delay=25)
        try:
            page.wait_for_function(
                """selector => {
                    const element = document.querySelector(selector);
                    return element && getComputedStyle(element).display !== 'none' &&
                        element.querySelectorAll('a').length > 0;
                }""",
                arg=list_selector,
                timeout=5000,
            )
        except PlaywrightTimeoutError:
            return []
        return page.locator(f"{list_selector} a:visible").all()

    def choose_suggestion(links, requested_tokens):
        requested = normalize(" ".join(requested_tokens))
        ranked = []
        for link in links:
            label = link.inner_text().strip()
            tokens = normalize(label)
            if all(token in tokens for token in requested):
                exact = tokens == requested
                ranked.append((not exact, -len(tokens), label, link))
        if not ranked:
            return None
        ranked.sort(key=lambda item: item[:3])
        return ranked[0][3]

    vehicle_link = None
    vehicle_words = None
    for end in range(len(words), 1, -1):
        candidate = words[:end]
        cached_vehicle = next(
            (
                label
                for label in cache["models"]
                if normalize(label) == normalize(" ".join(candidate))
            ),
            None,
        )
        links = type_smartbox(
            "#vin_text",
            f"{year} {cached_vehicle or ' '.join(candidate)}",
            "#vin_text_list",
        )
        vehicle_link = choose_suggestion(links, candidate)
        if vehicle_link:
            vehicle_words = candidate
            break
    if not vehicle_link:
        raise ValueError("VEHICLE_NOT_RESOLVED: Car-Part returned no matching vehicle")

    selected_vehicle = vehicle_link.inner_text().strip()
    vehicle_link.click()
    page.wait_for_function(
        "year => document.querySelector('[name=userDate]')?.value === String(year) && "
        "document.querySelector('[name=userModel]')?.value",
        arg=year,
        timeout=5000,
    )
    selected_year = page.locator('[name="userDate"]').input_value()
    selected_model = page.locator('[name="userModel"]').input_value()
    if selected_year != str(year) or not selected_model:
        raise ValueError("VEHICLE_NOT_RESOLVED: Car-Part did not accept vehicle")
    print(f"[resolver] vehicle suggestion: {selected_vehicle}", flush=True)

    part_words = words[len(vehicle_words):]
    if not part_words:
        raise ValueError("PART_NOT_RESOLVED: prompt needs a requested part")
    cached_part = next(
        (
            label
            for label in cache["parts"]
            if all(token in normalize(label) for token in normalize(" ".join(part_words)))
        ),
        None,
    )
    part_links = type_smartbox(
        "#part_text",
        cached_part or " ".join(part_words),
        "#part_text_list",
    )
    part_link = choose_suggestion(part_links, part_words)
    if not part_link and len(part_words) > 1:
        for end in range(len(part_words) - 1, 0, -1):
            candidate_words = part_words[:end]
            part_links = type_smartbox(
                "#part_text",
                " ".join(candidate_words),
                "#part_text_list",
            )
            part_link = choose_suggestion(part_links, candidate_words)
            if part_link:
                if requested_interchange is None:
                    requested_interchange = " ".join(part_words[len(candidate_words):]).strip() or None
                break
    if not part_link:
        raise ValueError("PART_NOT_RESOLVED: Car-Part returned no matching part")
    part_label = part_link.inner_text().strip()
    print(f"[resolver] part suggestion: {part_label}", flush=True)
    part_link.click()
    page.wait_for_function(
        "() => typeof parts_count !== 'undefined' && Number(parts_count) > 0",
        timeout=5000,
    )
    if page.evaluate("Number(parts_count)") < 1:
        raise ValueError("PART_NOT_RESOLVED: Car-Part did not save the selected part")

    make, model = selected_model.split(maxsplit=1)
    page.locator('input[name="userDate2"]').evaluate(
        "(element, value) => { element.value = value; }",
        str(year + 4),
    )
    try:
        with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
            page.evaluate("searchgo()")
    except PlaywrightTimeoutError:
        page.wait_for_load_state("domcontentloaded")

    choice_metadata = {
        "vehicle": selected_model,
        "part": part_label,
        "interchange_choices": [],
        "non_interchange_choices": [],
        "checked_by_site": [],
        "selected_interchange": None,
        "selected_mode": None,
        "selected_year_range": None,
    }
    text = page.locator("body").inner_text().lower()
    if "interchange" in text or "alternate" in text:
        model_choices = page.locator('input[name="dbModel"]')
        if model_choices.count() > 1:
            model_choices.last.check()
            page.locator('input[name="confirm_yes"]').evaluate(
                "(element, value) => { element.value = value; }",
                "yes",
            )
            with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                page.locator("form").evaluate("form => form.submit()")
        elif page.locator('input[name="dummyVar"]:visible').count():
            requested_model = selected_model
            choice = None
            broad_choice = None
            choice_options = []
            for candidate in page.locator('input[name="dummyVar"]:visible').all():
                candidate_id = candidate.get_attribute("id") or ""
                label = page.locator(f'label[for="{candidate_id}"]').inner_text().strip()
                option = {
                    "label": label,
                    "raw_value": candidate.get_attribute("value") or "",
                    "data_type": candidate.get_attribute("data-type") or "",
                    "checked_by_site": candidate.is_checked(),
                }
                target = (
                    choice_metadata["interchange_choices"]
                    if option["data_type"] == "int"
                    else choice_metadata["non_interchange_choices"]
                )
                target.append(option)
                choice_options.append(option)
                if option["checked_by_site"]:
                    choice_metadata["checked_by_site"].append(option)
                if label.lower() == f"search using only {requested_model}".lower():
                    broad_choice = candidate
                if requested_interchange and label.lower() == requested_interchange.lower():
                    choice = candidate
                    choice_metadata["selected_interchange"] = label
                    choice_metadata["selected_mode"] = "interchange"
            if requested_interchange and choice is None:
                raise ChoiceNotFoundError(
                    requested_interchange,
                    [item["label"] for item in choice_metadata["interchange_choices"]],
                )
            if choice is None:
                choice = broad_choice
                if choice is not None:
                    choice_metadata["selected_mode"] = "Search using only " + requested_model
            if choice is None and choice_options:
                raise ChoiceRequiredError(choice_options)
            if choice is None:
                raise ValueError("INTERCHANGE_NOT_RESOLVED: broad model choice was not found")
            page.locator('[name="userDate"]').evaluate(
                "(element, value) => { element.value = value; }",
                str(year - 1),
            )
            page.locator('[name="userDate2"]').evaluate(
                "(element, value) => { element.value = value; }",
                str(year + 8),
            )
            choice_metadata["selected_year_range"] = {"start": year - 1, "end": year + 8}
            choice.click()
            with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
                page.locator('input[type="image"][name="Search Car Part Inventory"]').click()
        else:
            choices = page.locator("button:visible, input:visible")
            for index in range(choices.count()):
                choice = choices.nth(index)
                label = (choice.get_attribute("value") or choice.inner_text()).lower()
                if "preferred" in label or "second" in label or "choice 2" in label:
                    choice.click()
                    page.wait_for_load_state("domcontentloaded")
                    break
    if choice_metadata["selected_year_range"] is None:
        choice_metadata["selected_year_range"] = {
            "start": int(page.locator('[name="userDate"]').input_value()),
            "end": int(page.locator('[name="userDate2"]').input_value()),
        }
    return page.url, make, model, part_label, choice_metadata


def _order_action(href):
    match = re.search(r"tpw_buyPart\(['\"]([^'\"]+)['\"],['\"]([^'\"]+)", href or "")
    if not match:
        return None
    return {"function": "tpw_buyPart", "button_id": match.group(1), "token": match.group(2)}


def _gallery_trigger(row):
    for anchor in row.find_all("a", href=True):
        href = anchor["href"]
        if "imageapp" in href.lower():
            return {"href": href}
        match = re.search(r"(https?://[^'\"]*imageapp[^'\"]*)", anchor.get("onclick", ""), re.I)
        if match:
            return {"href": match.group(1)}
    thumbnail = row.find("img", onclick=re.compile(r"popupImg", re.I))
    if thumbnail:
        return {"onclick": thumbnail.get("onclick")}
    return None


def _gallery_url_from_trigger(trigger):
    onclick = (trigger or {}).get("onclick", "")
    match = re.search(r"popupImg\(['\"]([^'\"]+)['\"]", onclick)
    if not match:
        return None
    return "https://imageappky.car-part.com/image?" + match.group(1)


def _parse_listings(html, source_url, year, make, model, part):
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    for row in soup.find_all("tr"):
        action_link = next(
            (anchor for anchor in row.find_all("a", href=True) if "tpw_buyPart" in anchor["href"]),
            None,
        )
        if not action_link:
            continue
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
        action = _order_action(action_link["href"])
        if not action:
            continue
        values = cells[-8:] if len(cells) >= 8 else cells
        result = {
            "orderable": True,
            "source_results_url": source_url,
            "order_action": action,
            "year": year,
            "make": make,
            "model": model,
            "part": part,
            "listing_text": " | ".join(cells),
            "raw_cells": cells,
        }
        thumbnail = row.find("img")
        if thumbnail and thumbnail.get("src"):
            result["primary_image"] = thumbnail["src"]
        names = ("year_part_model", "description", "grade", "warranty", "price", "distance", "stock", "supplier")
        for name, value in zip(names, values):
            if value:
                result[name] = value
        result["gallery_trigger"] = _gallery_trigger(row)
        result["gallery_url"] = _gallery_url_from_trigger(result["gallery_trigger"])
        result["gallery_status"] = "unknown" if result.get("gallery_trigger") else "none"
        listings.append(result)
    return listings


def _capture_gallery(page, listing):
    gallery_url = listing.get("gallery_url")
    gallery_page = page
    trigger = listing.get("gallery_trigger") or {}
    if not gallery_url and trigger.get("onclick"):
        thumbnail = None
        for candidate in page.locator('img[onclick*="popupImg"]').all():
            if candidate.get_attribute("onclick") == trigger["onclick"]:
                thumbnail = candidate
                break
        if thumbnail:
            try:
                with page.expect_popup(timeout=10000) as popup_info:
                    thumbnail.click()
                gallery_page = popup_info.value
                gallery_page.wait_for_load_state("domcontentloaded")
                gallery_url = gallery_page.url
                listing["gallery_url"] = gallery_url
            except PlaywrightTimeoutError:
                pass
    stock = listing.get("listing_id") or listing.get("stock") or "listing"
    if not gallery_url:
        listing["images"] = []
        return
    gallery_query = dict(parse_qsl(urlsplit(gallery_url).query, keep_blank_values=True))
    part_guid = re.sub(r"[^A-Fa-f0-9]", "", gallery_query.get("partGUID", "")).lower()
    folder = IMAGE_DIR / re.sub(r"[^A-Za-z0-9_.-]+", "_", stock)
    folder.mkdir(parents=True, exist_ok=True)
    captured = {}
    captured_urls = set()

    def on_response(response):
        if response.request.resource_type != "image" or response.status != 200:
            return
        image_url = response.url.lower()
        if "_web.jpg" not in image_url or "wsimgpro" not in image_url:
            return
        if part_guid and part_guid not in image_url.rsplit("/", 1)[-1]:
            return
        if response.url in captured_urls:
            return
        captured_urls.add(response.url)
        try:
            body = response.body()
        except Exception:
            return
        if body and body[:2] == b"\xff\xd8":
            captured.setdefault(hashlib.sha256(body).digest(), body)

    gallery_page.on("response", on_response)
    try:
        if gallery_page is page:
            page.goto(gallery_url, wait_until="domcontentloaded")
        gallery_page.locator("#thumbs").first.wait_for(timeout=10000)
        gallery_page.wait_for_timeout(2500)
    except PlaywrightTimeoutError:
        pass
    finally:
        gallery_page.remove_listener("response", on_response)
        if gallery_page is not page:
            gallery_page.close()

    paths = []
    for index, body in enumerate(captured.values(), 1):
        path = folder / f"{index:03d}.jpg"
        path.write_bytes(body)
        paths.append(str(path.relative_to(ROOT)))
    listing["images"] = paths


def capture_listing_gallery(listing):
    source_url = listing.get("source_results_url")
    if not source_url:
        listing["gallery_status"] = "none"
        return listing
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            _login(page)
            page.goto(source_url, wait_until="domcontentloaded", timeout=30000)
            _capture_gallery(page, listing)
            listing["gallery_status"] = "loaded" if listing.get("images") else "none"
        except Exception as error:
            listing["gallery_status"] = "error"
            listing["gallery_error"] = type(error).__name__
        finally:
            browser.close()
    return listing


def _result_page_fingerprint(html):
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True).lower()
        if "stock" in text and "availability" in text:
            return tuple(
                row.get_text(" ", strip=True)
                for row in table.find_all("tr")
            )
    return ()


def _result_row_count(html):
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        text = table.get_text(" ", strip=True).lower()
        if "stock" in text and "availability" in text:
            return max(0, len(table.find_all("tr")) - 1)
    return 0


def set_results_page(url, page_number):
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    replaced = False
    updated = []
    for key, value in query:
        if key == "userPage":
            if not replaced:
                updated.append((key, str(page_number)))
                replaced = True
        else:
            updated.append((key, value))
    if not replaced:
        updated.append(("userPage", str(page_number)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(updated), parts.fragment))


def _results_page_number(url):
    for key, value in parse_qsl(urlsplit(url).query, keep_blank_values=True):
        if key == "userPage":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def _pagination_total_pages(page):
    selector = page.locator("#bottomNav select#userPage")
    if not selector.count():
        raise ValueError("PAGINATION_NOT_FOUND: Car-Part returned no page selector")
    options = selector.locator("option").all()
    for option in options:
        match = re.search(r"\bof\s+(\d+)\b", option.inner_text(), re.I)
        if match:
            return int(match.group(1))
    values = [int(option.get_attribute("value")) for option in options if (option.get_attribute("value") or "").isdigit()]
    if values:
        return max(values)
    raise ValueError("PAGINATION_NOT_FOUND: Car-Part returned no total page count")


def _current_results_page(page):
    page_number = _results_page_number(page.url)
    if page_number is not None:
        return page_number
    selector = page.locator("#bottomNav select#userPage")
    if selector.count() and selector.input_value().isdigit():
        return int(selector.input_value())
    return None


def _native_page_url(page, page_number):
    links = page.locator("#bottomNav a.linkshowwaitspinner[href*='userPage=']").all()
    for link in links:
        href = link.get_attribute("href") or ""
        if _results_page_number(href) == page_number:
            return urljoin(page.url, href)
    return None


def _navigate_native_page(page, page_number):
    native_url = _native_page_url(page, page_number)
    if native_url:
        page.goto(native_url, wait_until="domcontentloaded", timeout=30000)
        return
    selector = page.locator("#bottomNav select#userPage")
    if not selector.count():
        raise ValueError(f"PAGINATION_NOT_FOUND: no native control for page {page_number}")
    with page.expect_navigation(wait_until="domcontentloaded", timeout=30000):
        selector.select_option(str(page_number))


def _search_parts_impl(prompt: str, capture_galleries=True, requested_interchange=None):
    year, vehicle_and_part, parsed_interchange = _parse_prompt(prompt)
    requested_interchange = requested_interchange or parsed_interchange
    print("[1/6] Logging in", flush=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            _login(page)
            print(f"[2/6] Searching {year} {vehicle_and_part}", flush=True)
            source_url, make, model, part, choice_metadata = _search(
                page, year, vehicle_and_part, requested_interchange
            )
            try:
                total_pages = _pagination_total_pages(page)
            except ValueError as error:
                if _result_row_count(page.content()) > 0:
                    total_pages = 1
                else:
                    raise error
            print(f"[pagination] total pages detected: {total_pages}", flush=True)
            if (_current_results_page(page) or 1) != 1:
                _navigate_native_page(page, 1)
            print(f"[3/6] Applying {PROFILE}", flush=True)
            print("[4/6] Parsing ORDER PART listings", flush=True)
            listings = []
            visited_fingerprints = set()
            page_number = _current_results_page(page) or 1
            total_rows_seen = 0
            partial = False
            first_page_url = page.url
            while page_number <= total_pages:
                try:
                    page_html = page.content()
                except Exception:
                    print("[pagination] result page could not be read; stopping", flush=True)
                    partial = bool(listings)
                    break
                fingerprint = _result_page_fingerprint(page_html)
                if fingerprint in visited_fingerprints:
                    print(f"[pagination] repeated page {page_number}; stopping", flush=True)
                    partial = True
                    break
                visited_fingerprints.add(fingerprint)
                page_listings = _parse_listings(page_html, page.url, year, make, model, part)
                total_rows = _result_row_count(page_html)
                total_rows_seen += total_rows
                print(
                    f"[pagination] page {page_number}: {total_rows} rows, "
                    f"{len(page_listings)} orderable",
                    flush=True,
                )
                for listing in page_listings:
                    listing["source_page"] = page_number
                    listing["search_choice_metadata"] = choice_metadata
                listings.extend(page_listings)
                if not total_rows:
                    if page_number < total_pages and listings:
                        partial = True
                    break
                if page_number == total_pages:
                    break
                previous_fingerprint = fingerprint
                requested_page = page_number + 1
                print(f"[pagination] requesting native userPage={requested_page}", flush=True)
                try:
                    _navigate_native_page(page, requested_page)
                except Exception:
                    try:
                        _navigate_native_page(page, requested_page)
                    except Exception:
                        print("[pagination] page navigation failed; stopping", flush=True)
                        partial = True
                        break
                returned_page = _current_results_page(page)
                if returned_page != requested_page:
                    print("[pagination] returned page number changed; stopping", flush=True)
                    partial = True
                    break
                if _result_page_fingerprint(page.content()) == previous_fingerprint:
                    print("[pagination] result fingerprint repeated; stopping", flush=True)
                    partial = True
                    break
                page_number += 1

            unique_listings = []
            seen_listing_keys = set()
            for listing in listings:
                action = listing.get("order_action", {})
                key = (
                    listing.get("stock", ""),
                    action.get("button_id", ""),
                    listing.get("supplier", ""),
                    listing.get("price", ""),
                    listing.get("listing_text", ""),
                )
                if key in seen_listing_keys:
                    continue
                seen_listing_keys.add(key)
                unique_listings.append(listing)
            listings = unique_listings
            print(
                f"[pagination] pages scanned: {page_number}; rows seen: {total_rows_seen}; "
                f"unique orderable: {len(listings)}",
                flush=True,
            )
            stock_counts = {}
            for listing in listings:
                stock = listing.get("stock") or "listing"
                stock_counts[stock] = stock_counts.get(stock, 0) + 1
            for listing in listings:
                stock = listing.get("stock") or "listing"
                if stock_counts[stock] > 1:
                    listing["listing_id"] = (
                        f"{stock}_{listing['order_action']['button_id']}"
                    )
            if page_number > 1 and capture_galleries:
                try:
                    page.goto(first_page_url, wait_until="domcontentloaded")
                except Exception:
                    capture_galleries = False
            if capture_galleries:
                print(f"[5/6] Capturing galleries 0/{len(listings)}", flush=True)
                for index, listing in enumerate(listings, 1):
                    print(f"[5/6] Capturing galleries {index}/{len(listings)}", flush=True)
                    try:
                        _capture_gallery(page, listing)
                    except Exception as error:
                        listing["gallery_error"] = type(error).__name__
                        listing["images"] = []
            else:
                for listing in listings:
                    listing["images"] = []
        finally:
            browser.close()
    image_count = sum(len(item.get("images", [])) for item in listings)
    result = {
        "status": "ok",
        "request": prompt,
        "vehicle": {"year": year, "make": make, "model": model},
        "part": part,
        "search": {
            "mode": choice_metadata["selected_mode"],
            "interchange": choice_metadata["selected_interchange"],
            "available_interchange_choices": [
                item["label"] for item in choice_metadata["interchange_choices"]
            ],
            "available_non_interchange_choices": [
                item["label"] for item in choice_metadata["non_interchange_choices"]
            ],
            "choices": choice_metadata,
            "year_start": choice_metadata["selected_year_range"]["start"],
            "year_end": choice_metadata["selected_year_range"]["end"],
        },
        "pagination": {
            "total_pages": total_pages,
            "pages_visited": page_number,
            "inventory_rows_seen": total_rows_seen,
        },
        "results": listings,
    }
    if partial and listings:
        result["partial"] = True
        print(f"SEARCH_PARTIAL_SUCCESS RESULTS_PRESERVED={len(listings)}", flush=True)
    if not listings:
        result["status"] = "no_inventory"
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "latest_results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"[6/6] Done — {len(listings)} orderable listings, {image_count} images", flush=True)
    return result


def search_parts(prompt: str, capture_galleries=True, requested_interchange=None):
    try:
        return _search_parts_impl(prompt, capture_galleries, requested_interchange)
    except ChoiceRequiredError as error:
        return {
            "status": "needs_interchange_choice",
            "request": prompt,
            "choices": error.choices,
            "results": [],
        }
    except ChoiceNotFoundError as error:
        try:
            year, base_request, _ = _parse_prompt(prompt)
            base_prompt = f"{year} {base_request}"
        except ValueError:
            base_prompt = prompt
        return {
            "status": "choice_not_found",
            "request": prompt,
            "base_request": base_prompt,
            "requested_choice": error.requested_choice,
            "available_choices": error.available_choices,
            "results": [],
        }
    except ValueError as error:
        message = str(error)
        status = "invalid_prompt" if message.startswith("Prompt must") else "search_failed"
        if "VEHICLE_NOT_RESOLVED" in message:
            status = "vehicle_not_found"
        elif "PART_NOT_RESOLVED" in message:
            status = "part_not_found"
        elif "PAGINATION" in message:
            status = "pagination_failure"
        return {"status": status, "request": prompt, "error": message, "results": []}


def place_order(listing, confirmed=False):
    if not confirmed:
        return {"success": False, "status": "confirmation_required", "message": "Order not executed"}
    action = listing.get("order_action", {})
    source_url = listing.get("source_results_url")
    if not action.get("button_id") or not action.get("token") or not source_url:
        return {"success": False, "status": "invalid_listing", "message": "Missing revalidation data"}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            _login(page)
            page.goto(source_url, wait_until="domcontentloaded")
            page.evaluate("([buttonId, token]) => tpw_buyPart(buttonId, token)", [action["button_id"], action["token"]])
            return {"success": True, "status": "submitted", "stock": listing.get("stock")}
        except Exception as exc:
            return {"success": False, "status": "failed", "message": str(exc)}
        finally:
            browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python carpart_engine.py \"2021 BMW M4 spindle knuckle\"")
    search_result = search_parts(sys.argv[1])
    for result in search_result.get("results", []):
        print(f"{result.get('stock', '?')} {result.get('price', '?')} {len(result.get('images', []))} images")