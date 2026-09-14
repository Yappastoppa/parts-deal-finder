# Customer search and quote service

This service is separate from `app.py` and `telegram_bot.py`. It calls `carpart_engine.search_parts()` in a child process; it never calls `place_order()`, the legacy checkout or Telegram. Engine, bot and legacy configuration files are unchanged.

## What is implemented

- `POST /api/search`: validated vehicle/part input; responds with a random search ID.
- `GET /api/search/<id>`: pending, configuration choices, public listing snapshots, or a generic failure.
- `GET /api/media/<id>`: local JPEGs addressed by opaque IDs, never supplier URLs or arbitrary files.
- `POST /api/quotes`: contact information, consent, listing IDs and an idempotency key. Returns a reference after the database transaction succeeds.
- `GET /api/health`: process health and live-search switch. This does not verify supplier credentials or connectivity.
- Private operator queue: `python -m backend.review list`, `show REFERENCE`, and `status REFERENCE --set reviewing` (also `new`, `quoted`, `closed`). `show` deliberately displays customer details to the authorized server operator. Do not paste that output into public logs.

Quote requests are stored in SQLite, independently of the existing storefront database. Price is “on request”; supplier prices are not represented as customer quotes. Missing mileage, location or photos are shown as unavailable. No payments, reservations, supplier orders, email or Telegram notifications are sent.

## Local preview

Use Python 3.12 on Linux. The API's validation/storage tests need only the standard library. Supplier execution needs the packages and Chromium below; install them in a separate virtual environment to leave the bot environment unchanged:

```sh
python -m venv .venv-website
.venv-website/bin/pip install -r backend/requirements.txt
.venv-website/bin/python -m playwright install --with-deps chromium
APF_ALLOWED_ORIGINS=http://127.0.0.1:8080 .venv-website/bin/python -m backend
```

In another terminal, serve `docs/` with `npx --yes http-server docs -p 8080 -c-1`. The committed `docs/scripts/config.js` has an empty API URL, so the static demo remains usable and does not collect quote contact details. For a connected local test, set its public `API_BASE_URL` to `http://127.0.0.1:8090`. Restore the empty URL before publishing until a real backend is ready.

The pinned Gunicorn startup, health endpoint and disabled-live-search guard have been tested locally. A supplier fixture is injected only by the tests; there is no remotely selectable fixture mode in the deployed API.

## Deployment configuration (not yet deployed)

Required decisions: inventory-source authorization, hosting account/budget, quote-review owner, contact/retention policy, customer pricing and shipping rules. Live supplier behavior has not been exercised in this change.

| Variable | Purpose |
| --- | --- |
| `APF_DATA_DIR` | Persistent private directory. Default: `data/website`, already ignored. Never inside `docs/`. |
| `APF_ALLOWED_ORIGINS` | Exact comma-separated website origins, e.g. `https://yappastoppa.github.io`. Origins have no repository path or trailing slash. No wildcard CORS. |
| `APF_LIVE_SEARCH` | Defaults off. Set `1` only after source authorization and secure credential configuration. |
| `CARPART_USERNAME`, `CARPART_PASSWORD` | Server secret settings used by the existing engine. No credentials belong in frontend configuration. |
| `APF_PORT` | Local API port, default `8090`. |

Production command after dependency installation:

```sh
.venv-website/bin/gunicorn -c backend/gunicorn.conf.py backend.api:application
```

Run one API worker/instance on one host behind an HTTPS reverse proxy, with a persistent private volume and a non-root service account. The single worker owns the in-process search limit and rate limiter; multiple instances need a shared job queue and shared rate limits first. Pin the reverse proxy's allowed hostnames, configure request/body/time limits, and disable body/query logging for customer endpoints. The API does not trust forwarded IP headers, so behind a proxy its 10 searches / 10 quote requests per ten minutes may apply to all visitors together. Configure trusted edge rate limiting before a public launch; CORS is not authentication or bot protection.

The search child has a 240-second deadline and its browser process group is terminated on exit/timeout. Search responses cap at 100 listings and 12 local JPEGs per listing. Source galleries can make searches slow; real-world timing and gallery coverage need validation before launch. Unavailable photos use clearly labeled illustrations. Static catalog data is a navigation aid, not verified fitment. Engine interchange/configuration choices are relayed; the prototype parser is not a full resolver.

After hosting is verified, put only the HTTPS API origin in `docs/scripts/config.js`. Pages still publishes `main` → `/docs`; it cannot execute Python. Do not publish or reverse-proxy the repository root or the legacy `app.py` checkout.

## Retention and operations

Search IDs, listings and photos expire after 24 hours. Run `python -m backend.review cleanup` daily to delete expired snapshots/media and abandoned private worker folders. Completed worker folders (including raw supplier authentication metadata) are removed immediately. A restart marks interrupted jobs failed; clients can retry. Search IDs are unguessable capabilities for public listing data, not customer accounts.

Quote records retain their own public listing snapshot and contact consent/version; expiring a search does not delete a quote. Quote contact retention and deletion procedures still need the business owner's approval before launch. Run `python -m backend.review backup` to create a consistent, verified SQLite snapshot in the private `backups/` subdirectory, with owner-only file permissions. This uses SQLite’s online backup API rather than copying an active database. Tests restore a snapshot to an independent database and verify quote/contact records. These local backups are not encrypted and do not include photo files; arrange encrypted off-host storage and an operational restore drill before accepting real requests.

Operators must review the queue manually; no notification destination is configured. Quotes are requests, not orders. Retrying the same request in the same page does not create a duplicate. After a successful submission, the confirmation reference and selected listing IDs are retained in tab session storage. Refreshing restores the receipt and requires an explicit “Start another quote” action to submit again. No contact details are stored in browser storage. A reload before a response arrives or with storage disabled can still lose the receipt; customers should keep their reference.

Search pages resume their last server search after refresh or return navigation without repeating the supplier request. Selected configuration remains in the page URL; “Refresh inventory” starts a new supplier search. Interrupted jobs return a retryable failure after five minutes.

## Tests

Run everything with `python tests/run_checks.py`. It creates its own temporary static server at a repository subpath, strips supplier/message credentials from the test environment, runs API and browser suites, and stops its test servers. Your manually started preview remains running. Development Playwright and Chromium must already be installed.

Individual checks:

```sh
python -m unittest discover -s tests -p 'test_*.py' -v
APF_PREVIEW_URL=http://127.0.0.1:8080/ python tests/browser_preview.py
APF_PREVIEW_URL=http://127.0.0.1:8080/ python tests/browser_quotes.py
```

Browser tests need development Playwright/Chromium. `browser_quotes.py` starts a temporary local API with fictional supplier data, follows the configuration/search/cart/quote flow, and verifies persistence and privacy boundaries. It never logs into the inventory source, sends messages or places orders. The demo regression test continues to exercise the unconnected Pages website.

Implementation references: [Gunicorn threaded worker settings](https://gunicorn.org/reference/settings/), [Playwright browser installation](https://playwright.dev/python/docs/browsers#install-browsers). Runtime versions are pinned in `requirements.txt`.

## Optional AI site helper

The static Help panel works immediately with built-in navigation guides and does not require a backend. It starts closed and supports Close, Escape, and clicking outside. It never labels built-in answers as AI.

AI help is a separate opt-in from supplier search:

1. Store `OPENAI_API_KEY` in the backend host's private secret settings. Never place it in `docs/`, Git, frontend configuration, or a browser request.
2. Set backend `APF_AI_HELP=1`. `APF_HELP_MODEL` defaults to `gpt-4.1-mini`; `APF_HELP_DAILY_LIMIT` defaults to 100 attempted requests per UTC day.
3. Add the website origin to `APF_ALLOWED_ORIGINS`, and set the public `HELP_API_BASE_URL` in `docs/scripts/config.js` to the HTTPS backend origin, without `/api`. This does not enable live supplier inventory.
4. Confirm `GET /api/help/status` returns `{"enabled":true}`. The panel checks availability only when opened. Normal deployment and provider account/billing setup are still required; automated tests use mocks only.

`POST /api/help` accepts only a message (600 characters), an allowlisted page name, and demo/live mode. It sends no cart, quote contact, supplier, browser-storage, or private file data. This is single-question navigation help; messages and answers are not persisted by the app. The OpenAI Responses request uses `store:false`, 280 output tokens, a 15-second timeout, and no tools. Provider data policies still apply; `store:false` is not a promise of zero provider retention.

A private `help_usage.sqlite3` stores only UTC day/request counts and preserves the budget across restarts. Limits are ten requests per IP per ten minutes and two concurrent provider calls per worker. The daily budget is shared by workers using the same data directory and counts failed attempts as well. The deployed worker configuration uses one worker. Closing the widget aborts the browser request; an already-started provider call may still finish and count toward usage.

The helper cannot inspect inventory, confirm fitment, execute actions, or take payments. Model output is rendered as plain text. Provider errors are generic and fall back to a built-in guide. External AI traffic is disabled by default.

Implementation references: [Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create) and [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
