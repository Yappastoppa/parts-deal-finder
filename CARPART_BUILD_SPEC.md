# CAR-PART ENGINE — LOCKED BUILD SPEC

## Goal

Build one reusable Car-Part engine for Parts Deal Finder.

Do NOT rewrite or remove working Car-Part behavior unnecessarily.
Reuse the working Playwright/login/search/result/order/gallery techniques already developed.

## Public API

### search_parts(prompt)

Input example:

2021 BMW M4 spindle knuckle

Responsibilities:

1. Authenticate to Car-Part Pro using:
   CARPART_USERNAME
   CARPART_PASSWORD

2. Receive a natural-language vehicle/part search prompt.

3. Parse at minimum:
   year
   make
   model
   part

4. Perform the existing Car-Part search workflow.

5. Use saved profile:
   v1

IMPORTANT:
lowercase v1, NOT V1.

6. Preserve the established search behavior:
   - handle Car-Part's alternate/interchange search choice when presented
   - use the previously established preferred/second choice
   - allow year grace around requested year when necessary
   - do not destroy working filters

7. Scrape ONLY inventory listings that have ORDER PART.

Machine verification can use the underlying:
   tpw_buyPart(...)

Do NOT return non-orderable inventory.

8. Preserve all useful fields already scraped from each result.

Target result fields include where available:

   year
   make
   model
   part
   stock
   price
   miles
   grade
   description
   warranty
   supplier/yard
   location
   distance
   listing text

Do not fabricate missing values.

9. For every ORDER PART listing, preserve the order information WITHOUT
executing it.

Store enough information to identify/reopen the exact listing/order later.

Example:

   orderable: true
   order_action:
       function: tpw_buyPart
       button_id: ...
       token: ...

SEARCH MUST NEVER CALL tpw_buyPart.

10. Find the listing's real clickable result thumbnail.

11. Open the listing's actual ImageApp gallery through Playwright.

Do not manufacture gallery URLs when the real popup URL can be captured.

12. Save:

   gallery_url

13. Capture actual gallery image bytes through the browser session.

Known behavior already proven:

   gallery uses #thumbs
   thumbnail references can map to _web.jpg
   direct wsimgproxy access is unreliable
   browser response interception successfully receives the real image bytes

Therefore:

   DO NOT depend on externally hotlinking wsimgproxy.
   DO NOT use a separate unauthenticated/direct HTTP fetch as the primary method.

Let the real gallery/browser load the images and capture successful image
responses with response.body().

14. Save images under:

   images/<safe-stock-or-listing-id>/
       001.jpg
       002.jpg
       003.jpg
       ...

Requirements:

   deduplicate images
   never overwrite multiple images onto the same filename
   verify body is non-empty image data
   use stable ordering where possible
   skip already-downloaded valid images when appropriate

15. Add local image paths to the result:

   images: [
       "images/G92043/001.jpg",
       ...
   ]

16. search_parts(prompt) returns:

   list[dict]

It must NOT place an order.

## Progress

Searches must print concise progress so the process never appears frozen.

Example:

[1/6] Logging in
[2/6] Searching 2021 BMW M4 spindle
[3/6] Applying v1
[4/6] Parsing ORDER PART listings
[5/6] Capturing galleries 2/7
[6/6] Done — 7 orderable listings

Avoid giant debugging dumps during normal operation.

## JSON

Also save the latest search results to:

data/latest_results.json

Use valid JSON.

## Separate ordering API

Implement:

place_order(listing, confirmed=False)

Rules:

1. Completely separate from search_parts().
2. Never execute an order when confirmed=False.
3. Reopen/revalidate the exact listing if necessary.
4. Only execute the stored/revalidated ORDER PART action when confirmed=True.
5. Return a structured success/failure result.
6. Never automatically retry an order after an ambiguous response.
7. Search/gallery/image collection must never trigger ordering.

We already successfully tested the underlying order mechanism.
Do NOT generate unnecessary test orders.

## Test CLI

Provide:

python carpart_engine.py "2021 BMW M4 spindle knuckle"

It must:

1. run search_parts()
2. NOT order anything
3. print concise results
4. save data/latest_results.json
5. save captured gallery JPGs under images/
6. exit cleanly

## Telegram

Do NOT put scraper logic inside Telegram.

telegram_bot.py must import the engine.

Conceptually:

from carpart_engine import search_parts, place_order

A normal Telegram text message calls search_parts(message).

Show ORDER PART results with:
   vehicle
   part
   price
   stock
   yard/supplier
   useful condition/details
   available captured photos

Do not send all 69 photos automatically.
Default to a small useful preview, such as the first 3-5.

Keep the complete local gallery available.

Ordering must be a separate interaction.

User selects a specific result
        ↓
show exact listing/order summary
        ↓
explicit CONFIRM ORDER
        ↓
place_order(listing, confirmed=True)

No confirmation = no order.

## Existing proven test fixture

Known working gallery/listing:

2021 BMW M4
Spindle/Knuckle Assembly, Front
Stock G92043

The gallery was successfully opened.
69 gallery references were observed.
Real _web.jpg image bytes were captured through Playwright.
A real G92043_test.jpg was saved and visually opened successfully.

Do not waste time rediscovering this behavior.

## Safety / regression requirements

DO NOT:
   delete existing project files
   delete images/
   delete data/
   overwrite backups
   expose credentials
   hard-code Telegram token
   hard-code Car-Part credentials
   place an order during a search/test
   replace working search logic merely for stylistic reasons

Environment variables:
   CARPART_USERNAME
   CARPART_PASSWORD
   TELEGRAM_BOT_TOKEN

## Definition of done

The system is ready for Telegram testing when:

python carpart_engine.py "2021 BMW M4 spindle knuckle"

returns real ORDER PART listings,
writes structured JSON,
captures/openable listing JPGs,
and performs ZERO order actions.
