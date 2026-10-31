# Shopping price tracker — design & implementation plan

Status: **design / proposal**. Nothing is built yet; this document is the plan to review.

## 1. What it does

1. You send the app a product link (any online shop).
2. The app stores it in a small database and checks it **once a day**: current price and
   availability (in stock / out of stock / pre-order / unknown).
3. Every check is kept, so each item has a price history.
4. A web page (GitHub Pages, like the other projects here) shows every tracked item:
   current price, lowest seen, availability, a small price-history chart, and when it was last checked.
5. Later: push notifications (ntfy, same as `library-new-item-tracker`) on a price drop,
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
a 403/503, or a stripped page. **An AI model does not fix this** — if we can't download
the real page, there is nothing for the AI to read. Options, cheapest first:

| Fetch method | Cost | Notes |
|---|---|---|
| Plain HTTP with browser-like headers (`curl_cffi`, which impersonates Chrome's TLS fingerprint) | free | Works for most small/medium shops and Shopify stores |
| Headless browser (Playwright/Chromium) | free, slower | For pages that only show the price after JavaScript runs |
| Claude's server-side `web_fetch` tool | API cost | Fetched from Anthropic's side; worth testing on blocked sites in the spike |
| Scraping API (ScraperAPI, ZenRows, Zyte, etc.) | paid, per request | Residential proxies + CAPTCHA handling; only for the sites that need it |
| Run the job on a home machine (e.g. Raspberry Pi) | free | Home IP is rarely blocked; GitHub Actions still serves the page |
| Retailer APIs (Amazon PA-API, Best Buy API, Keepa for Amazon history) | free–paid, needs keys | Most reliable where available, but per-retailer work |

**b) Reading price + availability from the page.** This is where we have choices besides AI:

### 2.2 Extraction ladder (cheap and exact first, AI last)

Each check tries these in order and stops at the first confident answer. The method used
is recorded with every check, so we can see what's actually working.

1. **Structured data (schema.org JSON-LD / microdata).** Most shops put a machine-readable
   `Product` → `Offer` block in the page for Google Shopping: `price`, `priceCurrency`,
   `availability` (`https://schema.org/InStock`, `OutOfStock`, `PreOrder`…). Free, exact,
   and covers a large share of shops (all Shopify stores, many others).
2. **Meta tags.** OpenGraph / Facebook product tags: `product:price:amount`,
   `og:price:amount`, `product:availability`.
3. **Site adapters** for important shops that need special handling, e.g. Shopify's
   `/products/<handle>.js` JSON endpoint, Next.js `__NEXT_DATA__` blobs (Walmart, Target),
   Amazon's `#corePrice` / `#availability` elements. Small, tested functions per site.
4. **Learned recipe.** When step 5 succeeds, the AI also returns *where* on the page the
   price and stock text were found (CSS selectors). We save that recipe per item and try it
   first next time — free and deterministic. If the recipe stops matching (site redesign),
   we fall back to step 5 and learn a new one. This means the AI is called roughly once per
   item, not once per day.
5. **AI extraction (Claude API).** Strip the page to its visible text plus relevant
   attributes (drop scripts, styles, nav, footer; keep roughly the main content, a few
   thousand tokens), send it with a strict JSON schema via structured outputs:
   ```json
   {"title": "...", "price": 129.99, "currency": "USD", "list_price": 159.99,
    "availability": "in_stock|out_of_stock|preorder|limited|unknown",
    "price_text": "$129.99", "price_selector": "...", "availability_selector": "...",
    "confidence": "high|medium|low"}
   ```

### 2.3 Validation (so we never notify on a wrong number)

- The AI's `price_text` must appear **verbatim** in the page text, or the answer is rejected
  (stops hallucinated prices).
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
  recipe_json      TEXT NULL        -- learned selectors (2.2 step 4)
  active           INTEGER          -- 0 = paused/removed, history kept
  added_at, notes

checks                               -- one row per item per run
  id, item_id, checked_at
  ok               INTEGER
  price_cents      INTEGER NULL
  list_price_cents INTEGER NULL      -- "was" price, if shown
  availability     TEXT              -- in_stock | out_of_stock | preorder | limited | unknown
  method           TEXT              -- jsonld | meta | adapter:<name> | recipe | llm
  fetch_method     TEXT              -- http | browser | web_fetch | scraper_api
  http_status, error TEXT NULL

events                               -- what we notified about (dedupe + history)
  id, item_id, created_at
  kind             TEXT              -- price_drop | back_in_stock | target_hit | check_failing
  old_price_cents, new_price_cents, sent INTEGER
```

Each run also exports `items.json` (current state + compact history) for the web page.

## 4. Sending links to the app

Recommended, in the order we'd build them:

1. **`items.txt` in the repo** — one URL per line (optionally `URL | target price | note`).
   Simplest; good for the spike and MVP.
2. **GitHub issue form "Track an item"** — open an issue from the GitHub phone app, paste
   the link. A workflow on `issues: opened` adds it, runs a first check right away, replies
   with the detected title/price/availability, and closes the issue. Same form with
   "untrack" to remove.
3. **Phone share sheet** — an iOS Shortcut / Android "HTTP Shortcuts" action that sends the
   shared URL to GitHub's `repository_dispatch` API (needs a fine-grained token on the
   phone limited to this repo). Share any product page → it's tracked.

## 5. Notifications (phase 4)

Reuse the `notify.sh` + ntfy setup from `library-new-item-tracker`, with its own secret
(`SHOPPING_PRICE_TRACKER_NTFY_TOPIC`). Rules:

- **Price drop:** at least 5% *or* $5 below the last price we notified about (configurable),
  so small wobbles don't spam.
- **New low:** lowest price in the item's history.
- **Target hit:** price at or below the item's target.
- **Back in stock:** previous successful check was out of stock, now in stock.
- **Check failing:** an item failed 3 days in a row (link dead or site blocking).
- Plus a weekly summary that doubles as a heartbeat, like the PS5 tracker.

## 6. Proposed files

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
    recipe.py          apply / learn CSS-selector recipes
    llm.py             Claude structured-output extraction + verbatim check
  validate.py          sanity rules from 2.3
  notify.sh            ntfy (phase 4)
  spike.py             phase 0 experiment runner
  index.html           web page
  items.json, prices.db  generated, committed by the workflow
  test_*.py            tests with saved HTML fixtures (no network in tests)
.github/workflows/
  price-tracker.yml       daily cron + manual run
  price-tracker-intake.yml  issue form / repository_dispatch intake (phase 3)
```

## 7. Implementation plan

### Phase 0 — Spike: find out what actually works (do this first)
- You send 10–20 real links from the shops you actually use.
- `spike.py` runs every fetch method × every extraction method on each link, from GitHub
  Actions (cloud IP) and from this dev container, and prints a table: blocked? which method
  found the price? did it match the real price?
- **Decision output:** which shops work for free, which need the AI step, which need a
  browser or a paid fetch service. This decides how much of phases 2/5 we need.

### Phase 1 — MVP tracker (no AI)
- SQLite schema, `items.txt` intake, URL canonicalisation and dedupe.
- Fetch with `curl_cffi`, Playwright fallback.
- Structured data + meta tag extraction, a couple of adapters for shops from the spike.
- Validation rules, failed checks recorded properly.
- Daily GitHub Actions workflow that commits `prices.db` + `items.json`.
- Tests with saved HTML fixtures.

### Phase 2 — AI fallback + learned recipes
- `llm.py`: page-to-text trimming, Claude call with structured outputs, verbatim price check.
- Recipe learning and reuse; re-learn on breakage.
- Per-run AI call cap and cost log in the run summary.
- API key as a repository secret (`ANTHROPIC_API_KEY`); the AI step is skipped if unset.

### Phase 3 — Web page + easier intake
- `index.html`: item cards (image, title, shop, price, change vs. last week, lowest seen,
  availability badge, sparkline, last checked, link), sort/filter, light/dark theme;
  card added to the repo's home page.
- Issue-form intake workflow with an immediate first check and a reply.
- Optional share-sheet shortcut instructions.

### Phase 4 — Notifications
- `notify.sh` + rules in section 5, `events` table for dedupe, weekly summary/heartbeat,
  failure alerts.

### Phase 5 — Hardening (only as needed after real use)
- Paid fetch service or home-machine runner for blocked shops.
- More adapters for frequently used shops.
- Currency handling if non-USD shops are added.

## 8. AI cost estimate

Default model per our Claude setup is Claude Opus 5.5 (`claude-opus-5-5`, $4 / $20 per
million input/output tokens). A trimmed page of ~8,000 tokens in and ~300 out costs about
**$0.04 per AI extraction**. Claude Haiku 5.5 (`claude-haiku-5-5`, $0.10 / $0.50) would be
about **$0.001** — worth comparing in the spike, since this is a simple extraction task.
Because of learned recipes and structured data, most daily checks use no AI at all; with
30 items, expect a few AI calls a week (initial learning + site redesigns), i.e. cents per
month. A hard daily cap keeps a bad day from costing more.

## 9. Things to decide

1. **Which shops** you mainly buy from (decides adapters and whether blocking is a problem).
2. **Public repo:** the tracked links and prices would be visible on GitHub and the page.
   Fine for shopping, but a private repo is an option.
3. **AI model:** Opus 5.5 (default) vs. Haiku 5.5 (much cheaper) — the spike can compare accuracy.
4. **Paid fetching:** OK to use a scraping service for blocked shops if the spike shows it's needed?
5. **Intake:** start with `items.txt` + issue form, or do you want the phone share-sheet from the start?
