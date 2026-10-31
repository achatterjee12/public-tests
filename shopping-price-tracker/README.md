# Shopping price tracker — design & implementation plan

Status: **design / proposal**. Nothing is built yet; this document is the plan to review.

## 1. What it does

1. You send the app a product link (any online shop).
2. The app stores it in a small database and checks it **once a day**: current price and
   availability (in stock / out of stock / pre-order / unknown).
3. **Every price it gathers is kept permanently** (one record per item per check, never
   overwritten or deleted, including failed checks), so each item has a full price history.
4. A web page (GitHub Pages, like the other projects here) shows every tracked item:
   current price, lowest seen, availability, a small price-history chart, and when it was last checked.
   Clicking an item opens a **full price-history graph** (section 6).
5. **Categories / tags:** you can create tags (starting set: jewelry, bags, hats, books,
   baby stuff, perfumes, home stuff, shoes, knitting patterns) and give each item any number of them; the page filters and groups by tag (section 5).
6. Later: push notifications (ntfy, same as `library-new-item-tracker`) on a price drop,
   back in stock, or when a target price is reached.

## 2. Architecture

It follows the pattern already working in `library-new-item-tracker`: Python + SQLite,
run by a scheduled GitHub Actions workflow that commits the updated database and a JSON
file that a static `index.html` reads. No server to run or pay for.

```mermaid
flowchart LR
  A[You: send link] -->|GitHub issue form / items file / phone shortcut| B[Intake workflow]
  B --> DB[(prices.db SQLite)]
  C[Daily workflow - cron] --> F[Fetch page]
  F --> X[Extraction ladder]
  X -->|price, availability, title| V[Validate]
  V --> DB
  DB --> J[items.json]
  J --> P[index.html on GitHub Pages]
  V -->|drop / restock / target hit| N[ntfy push - phase 4]
```

### 2.1 The two hard problems (and they are separate)

**a) Fetching the page.** Many large retailers (Amazon, Walmart, Target, Best Buy) block
automated requests, especially from cloud IPs like GitHub Actions: you get a CAPTCHA,
a 403/503, or a stripped page. Decision: **free methods only, no paid scraping service.**
Options:

| Fetch method | Cost | Notes |
|---|---|---|
| Plain HTTP with browser-like headers (`curl_cffi`, which impersonates Chrome's TLS fingerprint) | free | Works for most small/medium shops and Shopify stores |
| Headless browser (Playwright/Chromium) | free, slower | For pages that only show the price after JavaScript runs |
| Run the job on a home machine (e.g. Raspberry Pi) | free | Home IP is rarely blocked; GitHub Actions still serves the page |
| Free retailer APIs (e.g. Best Buy developer API) | free, needs a key | Only if a shop you use offers one and its page is blocked |

If a shop turns out to be blocked from GitHub Actions and none of the free options work,
it is listed as unsupported rather than paid around.

**b) Reading price + availability from the page.** Decision: **no AI for now.** We use
the structured data shops already publish, plus small per-site code where needed.

### 2.2 Extraction ladder (no AI)

Each check tries these in order and stops at the first confident answer. The method used
is recorded with every check, so we can see what's actually working.

1. **Structured data — the "Google Shopping" method (primary).** To appear in Google
   Shopping and rich search results, shops embed a machine-readable schema.org `Product`
   → `Offer` block in the page (JSON-LD `<script type="application/ld+json">`, or
   microdata attributes): `price`, `priceCurrency`, `availability`
   (`https://schema.org/InStock`, `OutOfStock`, `PreOrder`, `LimitedAvailability`…), plus
   `name`, `image`, and sometimes `priceSpecification` / list price. Free, exact, and covers
   a large share of shops (all Shopify stores, most WooCommerce and BigCommerce stores, many
   big retailers). The parser handles the common real-world variations: `@graph` wrappers,
   lists of offers (`AggregateOffer` with `lowPrice`), variant offers matched by SKU / URL,
   prices given as strings with currency symbols, and multiple `Product` blocks on one page.
2. **Meta tags.** OpenGraph / Facebook product tags: `product:price:amount`,
   `og:price:amount`, `product:availability`.
3. **Site adapters** for important shops that need special handling, e.g. Shopify's
   `/products/<handle>.js` JSON endpoint, Next.js `__NEXT_DATA__` blobs (Walmart, Target),
   Amazon's `#corePrice` / `#availability` elements. Small, tested functions per site.
4. **Manual selectors (last resort, per item).** For an odd page none of the above
   handles, an item can carry hand-written CSS selectors for the price and stock text
   (e.g. in `items.txt`: `price_sel: .product-price`). Free and deterministic; if the
   selector stops matching, the check fails visibly instead of guessing.

If nothing finds a price, the check is saved as failed with the reason. AI extraction is
kept as a possible later add-on (it would slot in as a final step) but is out of scope now.

### 2.3 Validation (so we never notify on a wrong number)

- Structured data and the visible page should agree: if the page also shows a price, a
  large mismatch with the JSON-LD price is flagged (catches stale or variant-default data).
- Prices are stored as integer cents plus a currency code.
- Big jumps (e.g. drop over 60% or rise over 3×) are treated as suspicious: re-check with a
  different method before saving as real or notifying.
- A failed check (blocked, timeout, nothing found) is saved as a failed check with the
  error, never as "price = 0" or "out of stock".
- Variants (size/colour) are a known trap: the page's default variant may differ from the
  one you want. The URL is kept exactly as sent (Shopify `?variant=`, Amazon ASIN) and
  adapters respect it.

## 3. Data model (SQLite, `prices.db`)

```text
items
  id               INTEGER PK
  url              TEXT  -- exactly as sent
  canonical_url    TEXT  -- tracking params stripped; used to avoid duplicates
  retailer         TEXT  -- domain, e.g. "bestbuy.com"
  title            TEXT
  image_url        TEXT
  currency         TEXT
  target_price_cents INTEGER NULL   -- optional "tell me below this"
  selectors_json   TEXT NULL        -- optional manual selectors (2.2 step 4)
  active           INTEGER          -- 0 = paused/removed, history kept
  added_at, notes

tags
  id               INTEGER PK
  name             TEXT UNIQUE      -- stored lowercase, e.g. "baby stuff"
  color            TEXT NULL        -- optional, for the badge on the page
  created_at

item_tags                            -- many-to-many: an item can have any number of tags
  item_id          INTEGER  -> items.id
  tag_id           INTEGER  -> tags.id
  PRIMARY KEY (item_id, tag_id)

checks                               -- one row per item per run: THE PRICE HISTORY
                                     -- append-only: rows are never updated or deleted,
                                     -- even when an item is untracked (active = 0)
  id, item_id, checked_at
  ok               INTEGER
  price_cents      INTEGER NULL
  list_price_cents INTEGER NULL      -- "was" price, if shown
  availability     TEXT              -- in_stock | out_of_stock | preorder | limited | unknown
  method           TEXT              -- jsonld | microdata | meta | adapter:<name> | selector
  fetch_method     TEXT              -- http | browser
  http_status, error TEXT NULL

events                               -- what we notified about (dedupe + history)
  id, item_id, created_at
  kind             TEXT              -- price_drop | back_in_stock | target_hit | check_failing
  old_price_cents, new_price_cents, sent INTEGER
```

Each run also exports, for the web page:
- `items.json` — every item with its current state, tags, lowest/highest/average price,
  and a short recent history for the card sparkline;
- `history/<item_id>.json` — that item's **complete** price history (every successful
  check: date, price, list price, availability), loaded only when you open its graph, so
  the main page stays small as history grows for years.

Size check: 50 items × 1 check a day × 5 years ≈ 91,000 rows, a few MB of SQLite — fine to
keep in the repo indefinitely.

## 4. Sending links to the app

Recommended, in the order we'd build them:

1. **`items.txt` in the repo** — one line per item:
   `URL | tags: bags, baby stuff | target: 99.99 | note: for the baby shower`
   (everything after the URL is optional). Simplest; good for the spike and MVP. Tags named
   here are created automatically if they don't exist yet.
2. **GitHub issue form "Track an item"** — open an issue from the GitHub phone app, paste
   the link, and pick or type tags. A workflow on `issues: opened` adds it, runs a first
   check right away, replies with the detected title/price/availability, and closes the
   issue. Companion forms: "Untrack an item" and "Edit item" (change tags, target, note).
3. **Phone share sheet** — an iOS Shortcut / Android "HTTP Shortcuts" action that sends the
   shared URL to GitHub's `repository_dispatch` API (needs a fine-grained token on the
   phone limited to this repo). Share any product page → it's tracked.

## 5. Categories / tags

- **Tags, not fixed categories:** an item can have several tags (a diaper bag can be both
  `bags` and `baby stuff`). A single "category" is just using one tag per item.
- **Creating tags:** any tag used on an item is created automatically. Tags can also be
  created ahead of time, renamed, recoloured, merged or deleted, via a `tags.txt` file
  (`name | color`) or a "Manage tags" issue form. Deleting a tag removes it from items but
  never deletes the items or their history.
- **Tagging items:** when adding an item (section 4), or later with "Edit item".
- **On the page:** tag chips at the top filter the list (pick several to combine);
  an optional "group by tag" view shows one section per tag with a per-tag count and
  total current value; untagged items get their own group.
- Tags are also sent with notifications (e.g. "[books] Knitting book dropped to $19"), and in
  a later phase notification rules can be set per tag (e.g. only notify for `perfumes`).

Why not edit tags directly on the web page? GitHub Pages is static and can't write to the
database. If editing from the page matters to you, the page can open a pre-filled
"Edit item" issue with one click, which keeps everything in the same workflow.

## 6. Price-history graph

- **Card sparkline:** small line of the last 90 days on every item card on the main page.
- **Item detail view** (click a card): a full-size interactive chart built from
  `history/<item_id>.json`:
  - price over time as a step line (price holds until the next check);
  - "was"/list price as a faint second line where the shop shows one;
  - out-of-stock periods shaded, failed checks shown as gaps (not as zero);
  - lowest-ever and target price as horizontal reference lines;
  - range buttons: 1M / 3M / 6M / 1Y / All; hover shows date, price, availability;
  - summary under the chart: current, lowest (and when), highest, average, change vs. 30 days ago;
  - the full table of every recorded check, with a CSV download.
- Built with a small charting library loaded from a CDN (e.g. Chart.js), light and dark themes,
  works on a phone.

## 7. Notifications (phase 4)

Reuse the `notify.sh` + ntfy setup from `library-new-item-tracker`, with its own secret
(`SHOPPING_PRICE_TRACKER_NTFY_TOPIC`). Rules:

- **Price drop:** at least 5% *or* $5 below the last price we notified about (configurable),
  so small wobbles don't spam.
- **New low:** lowest price in the item's history.
- **Target hit:** price at or below the item's target.
- **Back in stock:** previous successful check was out of stock, now in stock.
- **Check failing:** an item failed 3 days in a row (link dead or site blocking).
- Plus a weekly summary that doubles as a heartbeat, like the PS5 tracker.

## 8. Proposed files

```text
shopping-price-tracker/
  README.md            this document
  items.txt            links to track (MVP intake)
  tracker.py           daily run: load items, check each, save, export items.json
  db.py                schema + queries
  fetch.py             http (curl_cffi) -> browser (Playwright) fallbacks
  extract/
    structured.py      JSON-LD, microdata, meta tags
    adapters.py        per-site handlers (shopify, next_data, amazon, ...)
    selectors.py       manual per-item CSS selectors
  validate.py          sanity rules from 2.3
  notify.sh            ntfy (phase 4)
  tags.txt             tag list and badge colours
  spike.py             phase 0 experiment runner
  index.html           web page: item list, tag filters, price-history graph
  items.json, history/, prices.db  generated, committed by the workflow
  test_*.py            tests with saved HTML fixtures (no network in tests)
.github/workflows/
  price-tracker.yml       daily cron + manual run
  price-tracker-intake.yml  issue form / repository_dispatch intake (phase 3)
```

## 9. Implementation plan

### Phase 0 — Spike: find out what actually works (do this first)
- You send 10–20 real links from the shops you actually use.
- `spike.py` runs every fetch method × every extraction method on each link, from GitHub
  Actions (cloud IP) and from this dev container, and prints a table: blocked? which method
  found the price? did it match the real price?
- **Decision output:** which shops work with structured data alone, which need a site
  adapter or the browser, and which are blocked (unsupported, or try the home-machine runner).

### Phase 1 — MVP tracker
- SQLite schema (items, tags, item_tags, append-only checks), `items.txt` intake with tags,
  URL canonicalisation and dedupe.
- Fetch with `curl_cffi`, Playwright fallback.
- Structured data (JSON-LD + microdata) + meta tag extraction, manual selectors.
- Validation rules, failed checks recorded properly.
- Daily GitHub Actions workflow that commits `prices.db`, `items.json` and `history/`.
- Tests with saved HTML fixtures.

### Phase 2 — Site adapters
- Adapters for the shops from your links that structured data doesn't cover well
  (Shopify `.js` endpoint, `__NEXT_DATA__`, shop-specific HTML), each with saved-page tests.
- Variant handling (match the offer to the variant/SKU in the URL).

### Phase 3 — Web page + easier intake
- `index.html`: item cards (image, title, shop, tags, price, change vs. last week, lowest
  seen, availability badge, sparkline, last checked, link), sort, tag filter chips and
  group-by-tag view, light/dark theme; card added to the repo's home page.
- Item detail view with the full price-history graph and history table (section 6).
- Issue forms: Track / Untrack / Edit item / Manage tags, with an immediate first check and a reply.
- Optional share-sheet shortcut instructions.

### Phase 4 — Notifications
- `notify.sh` + rules in section 7, `events` table for dedupe, weekly summary/heartbeat,
  failure alerts.

### Phase 5 — Hardening (only as needed after real use)
- Home-machine runner for blocked shops, if any matter enough (no paid services).
- More adapters for frequently used shops.
- Currency handling if non-USD shops are added.

## 10. Decisions

Decided:
- **No AI** for price/availability for now; structured data ("Google Shopping" schema.org
  markup) is the primary method.
- **Public repo** is fine (tracked links and prices are visible on GitHub and the page).
- **No paid scraping service.**
- **Starting tags** (in `tags.txt`): jewelry, bags, hats, books, baby stuff, perfumes,
  home stuff, shoes, knitting patterns. More can be added any time.

Still open:
1. **Your links** (10–20 real product links) for the phase 0 spike — on their way.
2. **Intake:** start with `items.txt` + issue form, or do you want the phone share-sheet from the start?
