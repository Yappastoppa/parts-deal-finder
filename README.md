# parts-deal-finder

AUTO PART FINDER is the standalone customer-facing catalog in [`docs/`](docs/index.html). The existing Python storefront, Car-Part engine and Telegram bot remain separate.

## Preview

Serve only `docs/` with any static HTTP server. No Python backend or build step is required. For example, with Node.js:

```sh
npx --yes http-server docs -p 8080
```

Open `http://localhost:8080/`. An alternative, if Python is installed, is `python -m http.server 8080 --directory docs`. Opening HTML with `file://` will not load fetched JSON correctly.

For GitHub Pages, select **Settings → Pages → Deploy from a branch → your publishing branch → `/docs`**. No Pages configuration was present in the local repository. Remote repository settings were not inspected or changed. Publish only this directory; the repository root contains backend data and private runtime artifacts.

## Frontend

- `docs/index.html`: universal search, aligned vehicle controls and an expandable catalog. Twelve common makes appear first; View all makes and the A–Z filter expose the complete catalog.
- `docs/results.html`: five listings per page, position/price filters, price/mileage sorting, native photo dialog, and comparison of up to three parts across result pages. Unknown prices stay last when sorting and are excluded by an active price limit.
- `docs/cart.html`: cart and Saved Parts (`?view=saved`) persisted separately in browser localStorage.
- `docs/about.html`: preview scope and catalog limitations.
- `docs/styles/{main,features,theme}.css`: responsive layout, discovery controls, and a modern ivory, graphite, and copper theme inspired by the earlier storefront. Uses local system fonts.
- `docs/scripts/{main,catalog,search,results,cart}.js`: shared utilities, catalog, parser, listings and browser collections. `discovery.js` supplies filters and comparison; `recent.js` keeps the five most recent vehicle/part searches in sessionStorage for the current tab, with a clear action. Comparison IDs survive refresh and navigation in the current tab for the same search; only matching searches restore the selection. Filters, sort and page are preserved in the results URL.
- `docs/data/car_data.json`: public year/model/part labels extracted from the existing resolver options. Chevy is displayed as Chevrolet. Makes without model data remain empty; model-specific years are unavailable and are not fabricated.
- `docs/data/demo_results.json`: six fictional 2021 BMW M4 Spindle listings. Other searches show a clear empty state.
- `docs/assets/`: original SVG placeholder illustrations and favicon; no scraped photos or third-party branding.

`searchParts(searchParams)` in `docs/scripts/results.js` now supports demo data and the separate customer API. The public `docs/scripts/config.js` keeps demo mode enabled. The new `docs/quote.html` collects contact details only when an API is configured and current supplier listings are selected. The backend adapter and private quote queue are implemented and tested with fixtures; supplier access and deployment have not been activated. No payment, automatic ordering or notification integration is enabled. See [backend setup and operations](backend/README.md).

The parser matches the longest known make/model label. Unknown models can be entered separately in Browse By Vehicle. Catalog year options are general search options, not verified model fitment.

## Verification

Chromium tests were run against `/parts-deal-finder/` on a static server, including all pages at 375px and 768px widths. Covered: asset loading, catalog selection, parser validation, search navigation, five-listing pagination, gallery navigation/close/Escape, cart add/deduplication/removal/refresh persistence, Saved Parts, empty results, malformed/blocked storage, empty makes and failed data fetching. Normal browsing produced no console errors or HTTP failures. Desktop and mobile screenshots were visually reviewed.

The repeatable check is `tests/browser_preview.py`; it requires the existing development Playwright installation and Chromium, not the frontend. Start a static server, then run:

```sh
APF_PREVIEW_URL=http://localhost:8080/ python tests/browser_preview.py
```

For repository-subpath verification, serve a parent directory containing a `parts-deal-finder` link to `docs`, then set `APF_PREVIEW_URL=http://localhost:8080/parts-deal-finder/`. Test screenshots are written under `/tmp/apf-*.png`.

## Repository audit

The existing `app.py` renders HTML and uses server APIs; `static/site.js` uses root-relative API and image paths, so that storefront cannot run unchanged on Pages. The root `data/` includes scraped result JSON and a SQLite database, and `images/` contains 1,565 existing files. None were copied into the public site. There was no standalone vehicle make JSON; the reusable source was `data/resolver_options.json`.

At the start of this work, only `.gitignore` and this README were tracked; the application, engine, bot, backups, data and images were untracked. The first website commit includes the reviewed application, engine, bot, business configuration, legacy static source and engine specification. Business configuration reads environment variables and contains no private values. Runtime data, downloaded images, historical backups and debug scripts stay local under the ignore rules. Python behavior is unchanged. Frontend credential-pattern and sensitive-literal checks found no secrets. Live scraper/bot behavior was not exercised; no orders or external messages were sent.

## Search and quote milestone

The `backend/` service validates customer requests, runs the existing engine in an isolated process, filters supplier data into public listing snapshots, and stores quote requests with server-validated listing IDs. It exposes no legacy checkout or public admin route. The private review queue is operated from the server command line. Existing engine, bot, app and business configuration files remain unchanged.

Run `python -m unittest discover -s tests -p "test_*.py" -v` for isolated API checks and `python tests/browser_quotes.py` with the preview server running for the browser quote flow. These use fictional fixtures and do not authenticate to the supplier. Hosting, source authorization, operator workflow and live-data verification are the next deployment steps.

Recovery improvements: connected searches resume on page refresh, quote receipts survive refresh in the same tab, and the operator can create a verified private database backup with `python -m backend.review backup`. Run the complete isolated API and browser checks with `python tests/run_checks.py`; no supplier credentials or manual preview server are needed.

The catalog theme has been refined with a compact masthead, a visible cart control, consistent form spacing and clearer listing details. Browser checks cover common/all/letter catalog views and 320px, 375px and 768px layouts; desktop and mobile screenshots are reviewed alongside the interaction tests.

The latest interface adds a full-width search workspace, instant make filtering, global search (Ctrl/Cmd+K or `/`), and a native quick-view drawer for part details and cart/saved actions. All search, filtering, and comparison features work in the static demo; no AI service, backend connection, or supplier access is implied by the interface.

A small Help button is available on every page. Its panel gives short navigation guides and built-in answers about search, comparison, photos, cart and saved parts. It opens only on demand and closes with Close, Escape, or an outside click. Questions stay in page memory only. Optional AI site help is implemented in the separate backend and disabled by default; activation requires private server configuration as documented in `backend/README.md`. It does not enable supplier search or place orders.

Workflow upgrade: Edit search restores vehicle and part fields, Saved buttons toggle and reflect browser state, cart removals offer Undo, and demo carts explain why quotes are unavailable. Unknown prices are excluded from the displayed subtotal. Model lists load on expansion, and failed catalog requests have an in-page retry. No additional tracking or backend activation is introduced.
