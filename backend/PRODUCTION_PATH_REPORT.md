# Production path completion — local verification, 2026-09-15

No commit, push, deployment, supplier order or payment was performed. This report supersedes the incomplete-path findings in CONTINUATION_AUDIT.md; that document remains the historical starting audit.

## 1. Root cause and complete search trace

Production was running the earlier implementation, not the working-tree patch. The committed search adapter starts a worker for every request, has no search cache, and calls the engine with `capture_galleries=True`. The prior production probe returned 404 for both new cache and admin routes and took about 81 seconds twice. This is a deployment-version mismatch, not evidence of a local key/TTL defect.

Current trace:

1. `POST /api/search` validates year/make/model/part/interchange and removes `force_refresh` from the parameter dictionary.
2. `canonical_cache_key` trims fields, uppercases make/model and lowercases part/interchange. Neither a search session ID nor an engine-returned part label is used.
3. SQLite `search_cache` is queried using that exact key. No process-local result cache is involved.
4. A fresh hit creates an already-completed public job; a stale hit serves the previous snapshot and attempts a background refresh.
5. A miss starts an isolated `backend.worker` process, which passes `capture_galleries=False` and the requested configuration into the unchanged `carpart_engine.search_parts`.
6. Engine configuration choices are returned to the customer. The selected label is included in the subsequent lookup and write key.
7. Successful metadata results become public listing snapshots plus private exact source records, and are written to SQLite under the original key.
8. `GET /api/search/{id}` returns the job and recomputes current customer prices/hiding. Completed browser jobs are no longer reused indefinitely; the next search goes through the backend cache. Pending jobs still resume after reload.

Diagnostic events: `CACHE_LOOKUP`, `CACHE_HIT`, `CACHE_AGE`, `CACHE_WRITE`, `CACHE_REFRESH_STARTED`. Logs contain a SHA-256 key and counts/ages/flags only, never source URLs, credentials, cookies or order actions. API responses distinguish `cache.hit=true/false`.

## 2. Exact key and TTL

For the tested configuration:

`2019|BMW|540I|spindle|awd, lh`

Diagnostic SHA-256:

`a397a6871242a848ee0b37fad187d7c839466542ab350002bec5729c555ae5db`

- Under 15 minutes: fresh cache hit.
- 15 minutes to under 24 hours: serve stale inventory immediately; attempt a bounded background refresh.
- At least 24 hours, explicit expire, or force refresh: foreground supplier search.
- Configuration-choice responses are not cached.
- Failed background refreshes retain the previous cache. Interrupted refresh flags reset at process startup.
- Hits increment persistent popularity counts. The authenticated popular-refresh endpoint selects the most-used stale entry among the recent cache list and refreshes one entry per call. No autonomous scheduler is enabled.

## 3. Real supplier timing

Final updated local implementation, empty dedicated directory, same returned configuration `AWD, LH`:

| Measurement | Seconds | Result |
|---|---:|---|
| Configuration choices | 6.269 | Six choices |
| First configured inventory | 8.725 | 38 listings, cache miss |
| Total choices + inventory | 14.994 | Excludes UI interaction time |
| Immediate identical configured search | 0.050 | 38 listings, CACHE HIT YES |
| New backend process, same directory | 0.038 | 38 listings, POST-RESTART CACHE HIT YES |

These timings include local WSGI request dispatch and polling; they do not include browser/network transport. The backend application process was exited and recreated using the same APF_DATA_DIR. This proves local process-independent SQLite cache reuse, not Railway volume attachment or production redeploy behavior.

The Phase 1 gate independently passed before later implementation: 25.380 seconds for choices, 9.045 seconds for inventory, and 0.019 seconds for the identical cached search.

## 4. APF_DATA_DIR and files

The variable is **APF_DATA_DIR**, not APP_DATA_DIR. The existing environment-based directory selection remains; no Railway path is hardcoded. The local live test used `/tmp/apf-final-production-smoke` through APF_DATA_DIR.

Startup creates the directory if necessary, initializes/migrates tables via SQLite, performs database writes, sets database mode 0600 and logs `Persistent data directory: <resolved path>`. A non-directory, unwritable directory or database initialization failure stops startup with a clear generic storage error. The static docs directory is rejected.

Files under APF_DATA_DIR:

- `quotes.sqlite3`: search cache/popularity, jobs, public listings, private listing metadata, fulfillment sources, order source snapshots, pricing, overrides/notes, quote orders/contact, admin sessions, settings, audit and photo cache. SQLite may create its transaction journal beside this file.
- `help_usage.sqlite3`: optional help request counters; no chat messages or provider key.
- `media/*.jpg`: verified gallery image bytes, referenced by opaque public media IDs.
- `backups/*.sqlite3`: created only by the explicit backup command.
- Temporary random job directories: request/result JSON and engine data/images; removed after completed workers. Daily cleanup handles abandoned directories and expired inventory/photo data.

Supplier login credentials remain environment variables. Private supplier action tokens and source URLs are deliberately retained in the protected database as fulfillment metadata and must never be served as static files. Orders copy those exact source records, so expiring inventory does not destroy fulfillment references.

Local process recreation also retained pricing, override notes, an internal order and seven audit entries. Fixture tests cover settings and private backup behavior. Existing SQLite tables remain compatible; new source and photo tables are additive. Old quotes without exact actions remain reviewable but cannot pass the safe handoff source check.

## 5. Admin routes and authentication

Admin page: `docs/admin.html`, intended Pages URL `https://yappastoppa.github.io/parts-deal-finder/admin.html` after a future frontend publish.

All following paths are prefixed with `/api/admin`:

| Method | Path | Purpose |
|---|---|---|
| POST | `/login` | Password login; only unprivileged admin operation |
| GET | `/session` | Validate session |
| POST | `/logout` | Revoke session |
| GET | `/dashboard` | Counts |
| GET | `/cache` | List cached searches and popularity counts |
| GET | `/cache/{key}` | Inspect public cached payload/current prices |
| POST | `/cache/{key}/refresh` | Start immediate refresh |
| POST | `/cache/{key}/expire` | Expire for next search |
| DELETE | `/cache/{key}` | Delete cache |
| POST | `/cache/refresh-popular` | Refresh one popular stale entry |
| GET, POST | `/pricing-rules` | List/save rules |
| DELETE | `/pricing-rules/{id}` | Delete rule |
| GET, POST | `/listings/{id}` | Supplier/customer price view, hide, price override, notes |
| GET | `/orders` | Internal quote orders and public item snapshots |
| POST | `/orders/{reference}/status` | Validated transition |
| POST | `/orders/{reference}/prepare-supplier-order` | Local reference readiness check, audit only |
| POST | `/orders/{reference}/place-supplier-order` | Disabled, HTTP 501 |
| GET, POST | `/settings` | Settings |
| GET | `/audit` | Audit history |

Encode cache keys as URL path components. The key may contain commas and punctuation in valid configuration labels.

Authentication is enforced in Python before privileged route dispatch. Login compares APF_ADMIN_PASSWORD using constant-time comparison, is rate-limited, and creates a random 64-hex bearer session in SQLite with an eight-hour expiry. Browser sessionStorage holds the token; credentials are never bundled in JS. Logout invalidates the server session. Local checks confirmed 401 without a session and 200 with a valid session. Production remains unchanged and still needs the patch deployed later.

Customer routes: `GET /api/health`, `GET /api/settings/public`, `POST /api/search`, `GET /api/search/{id}`, `GET /api/search-cache`, `POST /api/listing/{id}/photos`, `GET /api/photos/{id}`, `GET /api/media/{id}`, `POST /api/quotes`; existing optional help routes remain.

## 6. Exact source and checkout

The engine already emits the exact action in its orderable row parser. It was lost in `SearchService._execute`, which previously saved only gallery metadata and results URL. No engine change was needed.

The boundary now requires engine `orderable=true`, a results URL, and action button ID/token before publishing a customer listing. Mapping:

`public listing_id → fulfillment_sources → source_results_url + order_action + supplier_price + stock + search configuration`

Checkout, within one SQLite transaction:

- Validate the exact current listing IDs; reject hidden, expired, non-orderable or source-less listings.
- Compute and snapshot customer price from current rules/override; ignore client price fields.
- Snapshot vehicle, part and selected configuration.
- Store contact name, email, phone, delivery postal code, optional notes and consent.
- Copy each private source into `order_sources`, keyed by internal order reference and listing ID.
- Create the internal quote order as `needs_review` and audit creation.
- Preserve idempotency: retrying the same accepted request returns the same reference.

The UI remains Choose This Part → Cart → Request a Quote → internal review record. It does not claim reservation or accept payment. Postal code is collected for delivery quoting; a full street address remains a manual confirmation step.

Review progression: `needs_review → customer_confirmed → ready_for_supplier_order`, with optional reviewing/quoted states, cancellation and return to review. Unsupported jumps, including setting supplier_ordered through this API, are rejected. The CLI also validates transitions.

Preparation checks the persisted references and records an audit entry. It makes **no supplier network request**, does not open or resume a supplier checkout, and returns no action token or private URL. Actual source/session revalidation, fitment/stock/shipping confirmation, address confirmation and any supplier purchase remain operator work. The executable supplier-order endpoint stays disabled.

## 7. Pricing and overrides

1. Manual listing override.
2. Supplier-specific rule.
3. Part-specific rule.
4. Make-specific rule.
5. Global rule.
6. Supplier price unchanged if no rule.

Within one scope, the most recently updated rule wins, with rule ID as a deterministic tie-breaker. Percent/fixed rules support minimum margin. Nonfinite, negative and oversized markup inputs are rejected. Missing supplier price stays null / Price on request unless a manual sell price exists.

The original supplier_price never changes. Customer responses carry price/customer_price; internal orders snapshot customer_price separately from the private source supplier_price. Hide/show and notes are persisted per listing ID. Refreshing inventory creates new listing IDs; overrides do not automatically transfer to a different supplier row.

## 8. Photo behavior

The previous deployed worker waited for galleries (`capture_galleries=True`). Current initial searches explicitly pass False: all returned listing metadata is cached before any gallery work. The engine still completes its metadata search before status=ok; no partial-row streaming was introduced.

After render, IntersectionObserver queues galleries only for visible cards, one at a time in that browser. A listing's photo request is shared with View Photos. Since the unchanged engine exposes a full-gallery capture operation, visible-card loading fetches that listing's gallery and uses its first photo as the card image; full View Photos reuses the result. Server photo cache persists under APF_DATA_DIR.

No-photo listings bypass the photo worker. Separate job directories and exact gallery metadata preserve ownership; engine GUID filtering is unchanged. The browser guards against late responses replacing another listing's dialog.

Real checks: no-photo result in 0.003 seconds; one gallery-marked listing legitimately yielded no images; another returned five images in 5.984 seconds and a verified JPEG was served through the local media endpoint. No photos were borrowed to fill missing galleries.

## 9. Changes in this continuation

Backend: `backend/api.py`, `backend/admin.py`, `backend/domain.py`, `backend/pricing.py`, `backend/search.py`, `backend/store.py`, `backend/review.py`.

Frontend: `docs/admin.html`, `docs/scripts/admin.js`, `docs/scripts/api.js`, `docs/scripts/results.js`.

Tests: `tests/test_quote_api.py`, `tests/test_cache.py`, `tests/test_search_adapter.py`, `tests/test_pricing.py`, `tests/test_production_path.py` (new), `tests/browser_quotes.py`, `tests/browser_photos.py` (new), `tests/run_checks.py`.

Documentation: this report, `backend/README.md`, and a supersession note in `backend/CONTINUATION_AUDIT.md`.

The earlier patch's other uncommitted files remain in place. `carpart_engine.py` is unchanged.

## 10. Manual Railway steps — not executed

1. Before changing storage, back up any existing production quotes/database and preserve that backup outside the disposable container. Mounting a new path does not migrate existing data.
2. Attach a persistent volume to the **backend service**, mounted at `/data` (or another chosen absolute directory).
3. Set backend variable `APF_DATA_DIR=/data` to match that mount. The variable alone does not create a Railway volume.
4. Set a strong private `APF_ADMIN_PASSWORD`. Retain the existing private CARPART_USERNAME/CARPART_PASSWORD, `APF_LIVE_SEARCH=1`, and `APF_ALLOWED_ORIGINS=https://yappastoppa.github.io`.
5. Keep one backend instance with the existing one-worker Gunicorn configuration. Keep the existing Docker start command and PORT handling.
6. When a deployment is separately authorized, deploy the completed backend patch and publish its docs frontend. No API origin change is required.
7. Confirm the startup path log points at the mount, admin/cache routes exist, and a controlled cache/order survives a production restart/redeploy. Local tests cannot certify a Railway mount.
8. Arrange private backups and daily cleanup. Popular refresh is an authenticated on-demand endpoint; any periodic caller must be configured separately and must reuse this service's bounded search slot.

Railway volumes persist data across deployments and are mounted at runtime; see [Railway volume documentation](https://docs.railway.com/volumes). Do not perform database initialization in a build step expecting that runtime mount.

## Safety totals

Supplier orders placed: **0**. Payments submitted: **0**. Production checkout records created: **0**. One internal order was created locally using real inventory and fictional contact details, solely to verify the safe checkout path.

## Final validation

`python tests/run_checks.py`: **44 unit tests and five browser suites passed** (demo, connected quote, help, admin, photos). Tests cover cache reuse/stale refresh/force refresh, restart cache hits/popularity, source retention after inventory removal, untrusted/hidden checkout rejection, current-price snapshots, precedence/null prices, numeric validation, admin protection/logout, cache route lifecycle, status enforcement/preparation, startup failure, media allowlists and backup/restore. Browser tests cover metadata-first photo loading, one shared gallery request and late-response ownership.

Fixture expectations were updated only for deliberate contract changes: complete orderable source metadata is now required, and an unmodified supplier price is the requested no-rule default. The below-price browser filter now uses $100 against the $125 fixture so it continues to test exclusion. No assertions were disabled to obtain passing tests.

`git diff --check`: clean. Engine diff: empty. All test servers and foreground smoke processes exited. Live smoke timing and admin/gallery results were separately verified against real supplier inventory through the local backend; the automated regression suite itself uses fixtures.
