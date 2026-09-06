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

- `docs/index.html`: universal search, vehicle controls and alphabetical expandable catalog.
- `docs/results.html`: stacked listings, five per page, accessible native photo dialog and selection buttons.
- `docs/cart.html`: cart and Saved Parts (`?view=saved`) persisted separately in browser localStorage.
- `docs/about.html`: preview scope and catalog limitations.
- `docs/styles/main.css`: responsive catalog styling with local system fonts.
- `docs/scripts/{main,catalog,search,results,cart}.js`: shared utilities, catalog, parser, listings and browser collections.
- `docs/data/car_data.json`: public year/model/part labels extracted from the existing resolver options. Chevy is displayed as Chevrolet. Makes without model data remain empty; model-specific years are unavailable and are not fabricated.
- `docs/data/demo_results.json`: six fictional 2021 BMW M4 Spindle listings. Other searches show a clear empty state.
- `docs/assets/`: original SVG placeholder illustrations and favicon; no scraped photos or third-party branding.

`searchParts(searchParams)` in `docs/scripts/results.js` is the backend integration interface. It currently fetches demo JSON and returns a listing array. A future API adapter can replace its internals while retaining the listing UI. No live inventory, fitment resolution, checkout, authentication, payment or backend deployment is implemented.

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
