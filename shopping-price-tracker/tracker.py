"""Daily shopping price tracker.

Reads items.txt (links + tags) and tags.txt (tag colours), checks every item once, and
appends one row per item to the `checks` table of prices.db. Rows are never changed or
deleted, so the table is the full price history. Then exports, for index.html:
  items.json            every item with current state, tags, stats and a 90-day sparkline
  history/<id>.json     that item's complete price history (loaded when its graph opens)

Extraction is the no-AI ladder from the design (README section 2.2), reusing spike.py's
parsers. Order: Shopify product endpoint, schema.org JSON-LD, meta tags, microdata.
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import fx
import spike

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PRICE_DB", os.path.join(HERE, "prices.db"))
ITEMS_PATH = os.path.join(HERE, "items.txt")
TAGS_PATH = os.path.join(HERE, "tags.txt")
JSON_PATH = os.path.join(HERE, "items.json")
HISTORY_DIR = os.path.join(HERE, "history")
# Price order. The page's own data (jsonld/meta/microdata) comes first because it states the
# currency the shopper sees; Shopify's /products/<x>.js gives prices in the shop's *base*
# currency with no currency code (e.g. INR on an Indian shop's US storefront), so it is used
# for the price only when nothing else has one. It is still the best source for stock status.
METHOD_ORDER = ["jsonld", "meta", "microdata", "shopify"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY, url TEXT UNIQUE NOT NULL, retailer TEXT, title TEXT,
  image_url TEXT, currency TEXT, target_price_cents INTEGER, note TEXT,
  active INTEGER NOT NULL DEFAULT 1, added_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, color TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS item_tags (
  item_id INTEGER NOT NULL REFERENCES items(id), tag_id INTEGER NOT NULL REFERENCES tags(id),
  PRIMARY KEY (item_id, tag_id));
CREATE TABLE IF NOT EXISTS checks (
  id INTEGER PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id),
  checked_at TEXT NOT NULL, ok INTEGER NOT NULL, price_cents INTEGER, currency TEXT,
  availability TEXT, method TEXT, fetch_method TEXT, http_status INTEGER, error TEXT);
CREATE INDEX IF NOT EXISTS checks_item_time ON checks(item_id, checked_at);
"""


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path=DB_PATH):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    db.executescript(fx.SCHEMA)
    cols = {r[1] for r in db.execute("PRAGMA table_info(checks)")}
    for col, typ in (("usd_cents", "INTEGER"), ("fx_rate", "REAL"), ("fx_source", "TEXT")):
        if col not in cols:  # databases created before currency conversion
            db.execute(f"ALTER TABLE checks ADD COLUMN {col} {typ}")
    return db


# ---------- syncing items.txt / tags.txt into the database ----------

def parse_tags(path):
    tags = []
    if not os.path.exists(path):
        return tags
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, color = (p.strip() for p in line.partition("|"))
        tags.append((name.lower(), color or None))
    return tags


def tag_id(db, name, color=None):
    db.execute("INSERT OR IGNORE INTO tags(name, color, created_at) VALUES (?,?,?)",
               (name, color, now_iso()))
    if color:
        db.execute("UPDATE tags SET color=? WHERE name=?", (color, name))
    return db.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()[0]


def sync(db, items, tags):
    """items.txt is the source of truth: add new items, update tags/target/note, and mark
    items no longer listed as inactive (their history is kept)."""
    for name, color in tags:
        tag_id(db, name, color)
    ids = []
    for it in items:
        target = round(it["target"] * 100) if it["target"] is not None else None
        db.execute("""INSERT INTO items(url, retailer, target_price_cents, note, active, added_at)
                      VALUES (?,?,?,?,1,?)
                      ON CONFLICT(url) DO UPDATE SET target_price_cents=excluded.target_price_cents,
                        note=excluded.note, active=1""",
                   (it["url"], urlparse(it["url"]).netloc.removeprefix("www."), target,
                    it["note"], now_iso()))
        iid = db.execute("SELECT id FROM items WHERE url=?", (it["url"],)).fetchone()[0]
        db.execute("DELETE FROM item_tags WHERE item_id=?", (iid,))
        for t in it["tags"]:
            db.execute("INSERT OR IGNORE INTO item_tags VALUES (?,?)", (iid, tag_id(db, t)))
        ids.append(iid)
    marks = ",".join("?" * len(ids)) or "NULL"
    db.execute(f"UPDATE items SET active=0 WHERE id NOT IN ({marks})", ids)
    db.commit()
    return ids


# ---------- checking ----------

def image_from(row, scanner_meta):
    img = scanner_meta.get("og:image") or scanner_meta.get("og:image:secure_url")
    if img and img.startswith("//"):
        img = "https:" + img
    return img


def check_item(url):
    """Returns a dict: ok, price, currency, availability, title, image, method, status, error."""
    status, final_url, text, err = spike.fetch(url)
    out = {"ok": False, "price": None, "currency": None, "availability": None, "title": None,
           "image": None, "method": None, "status": status, "error": err}
    if err:
        return out
    if spike.looks_blocked(status, text) or "/blocked" in urlparse(final_url).path:
        out["error"] = "blocked by the shop (bot protection)"
        return out
    sc = spike.Scanner()
    try:
        sc.feed(text)
    except Exception as e:
        out["error"] = f"HTML parse error: {e}"
    results = [spike.extract_jsonld(sc, url), spike.extract_meta(sc), spike.extract_microdata(sc)]
    shop = spike.extract_shopify(url)
    if shop:
        results.append(shop)
    out["image"] = image_from(out, sc.meta)
    out.update(pick(results))
    if not out["ok"]:
        out["error"] = out["error"] or "no price found on the page"
    return out


def pick(results):
    """Combines the extraction results into one answer. Price and currency always come from
    the same method; stock status prefers Shopify's variant data when present."""
    results = sorted(results, key=lambda r: METHOD_ORDER.index(r["method"]))
    title = next((r["title"] for r in results if r.get("title")), None)
    best = next((r for r in results if r["ok"]), None)
    if not best:
        return {"ok": False, "title": title, "price": None, "currency": None,
                "availability": None, "method": None}
    shop = next((r for r in results if r["method"] == "shopify" and r.get("availability")), None)
    avail = (shop["availability"] if shop else None) or best["availability"] \
        or next((r["availability"] for r in results if r.get("availability")), None) or "unknown"
    return {"ok": True, "title": title, "price": best["price"], "currency": best["currency"],
            "availability": avail, "method": best["method"]}


def record(db, iid, res, rate=None):
    """Saves one check. rate: {"rate", "source"} converting res's currency to USD, if known."""
    when = now_iso()
    cents = round(res["price"] * 100) if res["ok"] else None
    usd = round(cents * rate["rate"]) if cents is not None and rate else None
    db.execute("""INSERT INTO checks(item_id, checked_at, ok, price_cents, currency, availability,
                  method, fetch_method, http_status, error, usd_cents, fx_rate, fx_source)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
               (iid, when, int(res["ok"]), cents, res["currency"], res["availability"],
                res["method"], "http", res["status"], res["error"], usd,
                rate["rate"] if rate and rate["source"] else None, rate["source"] if rate else None))
    res["usd"] = usd / 100 if usd is not None else None
    for col in ("title", "currency"):
        if res.get(col):
            db.execute(f"UPDATE items SET {col}=? WHERE id=?", (res[col], iid))
    if res.get("image"):
        db.execute("UPDATE items SET image_url=? WHERE id=?", (res["image"], iid))
    db.commit()


# ---------- export for the web page ----------

def title_from_url(url):
    """Readable fallback name from the link, e.g. /ip/4-Pack-Overnight-Oats.../123 -> '4 Pack Overnight Oats...'."""
    parts = [p for p in urlparse(url).path.split("/") if p and not p.isdigit() and len(p) > 3]
    if not parts:
        return urlparse(url).netloc.removeprefix("www.")
    words = max(parts, key=len).replace("-", " ").replace("_", " ").strip()
    words = words[:1].upper() + words[1:]
    return words if len(words) <= 70 else words[:67].rsplit(" ", 1)[0] + "…"


def export(db, json_path=JSON_PATH, history_dir=HISTORY_DIR):
    os.makedirs(history_dir, exist_ok=True)
    tag_rows = db.execute("""SELECT t.name, t.color, COUNT(it.item_id) AS n FROM tags t
                             LEFT JOIN item_tags it ON it.tag_id=t.id
                             LEFT JOIN items i ON i.id=it.item_id AND i.active=1
                             GROUP BY t.id ORDER BY t.id""").fetchall()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    out_items = []
    for it in db.execute("SELECT * FROM items WHERE active=1 ORDER BY id"):
        checks = db.execute("SELECT * FROM checks WHERE item_id=? ORDER BY checked_at",
                            (it["id"],)).fetchall()
        # Everything on the page is in US dollars. A non-USD price uses the rate saved with
        # its check; if no rate was ever available, the item stays in its own currency.
        local_cur = it["currency"] or "USD"
        converted = local_cur == "USD" or any(c["usd_cents"] is not None for c in checks)

        def val(c):
            if not converted:
                return c["price_cents"]
            return c["price_cents"] if (c["currency"] or "USD") == "USD" else c["usd_cents"]
        good = [c for c in checks if c["ok"] and val(c) is not None]
        last, last_ok = (checks[-1] if checks else None), (good[-1] if good else None)
        prices = [val(c) for c in good]
        low = min(good, key=lambda c: (val(c), c["checked_at"])) if good else None
        tags = [r["name"] for r in db.execute(
            """SELECT t.name FROM item_tags it JOIN tags t ON t.id=it.tag_id
               WHERE it.item_id=? ORDER BY t.id""", (it["id"],))]
        month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        before = [c for c in good if c["checked_at"] <= month_ago]
        out_items.append({
            "id": it["id"], "url": it["url"], "retailer": it["retailer"],
            "title": it["title"] or title_from_url(it["url"]), "image": it["image_url"],
            "currency": "USD" if converted else local_cur, "tags": tags, "note": it["note"],
            "local_currency": local_cur if local_cur != "USD" else None,
            "local_price": last_ok["price_cents"] if last_ok and local_cur != "USD" else None,
            "fx": ({"rate": last_ok["fx_rate"], "source": last_ok["fx_source"], "as_of": last_ok["checked_at"]}
                   if last_ok and local_cur != "USD" and last_ok["fx_rate"] else None),
            "target": it["target_price_cents"], "added_at": it["added_at"],
            "price": val(last_ok) if last_ok else None,
            "availability": last_ok["availability"] if last_ok else None,
            "last_checked": last["checked_at"] if last else None,
            "last_ok": bool(last and last["ok"]),
            "last_error": last["error"] if last and not last["ok"] else None,
            "low": val(low) if low else None,
            "low_at": low["checked_at"] if low else None,
            "high": max(prices) if prices else None,
            "avg": round(sum(prices) / len(prices)) if prices else None,
            "price_30d_ago": val(before[-1]) if before else None,
            "checks": len(checks),
            "spark": [[c["checked_at"][:10], val(c)] for c in good if c["checked_at"] >= cutoff],
        })
        hist = [{"t": c["checked_at"], "ok": bool(c["ok"]) and (not c["ok"] or val(c) is not None),
                 "price": val(c) if c["ok"] else None,
                 "local": c["price_cents"] if c["ok"] and (c["currency"] or "USD") != "USD" else None,
                 "currency": c["currency"], "fx_rate": c["fx_rate"], "fx_source": c["fx_source"],
                 "availability": c["availability"], "method": c["method"],
                 "error": c["error"] if not c["ok"] else (None if val(c) is not None else "no exchange rate")}
                for c in checks]
        with open(os.path.join(history_dir, f"{it['id']}.json"), "w", encoding="utf-8") as f:
            json.dump(hist, f, separators=(",", ":"))
    data = {"generated_at": now_iso(),
            "tags": [{"name": r["name"], "color": r["color"], "count": r["n"]} for r in tag_rows],
            "items": out_items}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    return data


def backfill(db, rates):
    """Gives older non-USD checks (from before conversion existed) today's rate, once."""
    for (cur,) in db.execute("""SELECT DISTINCT currency FROM checks WHERE ok=1 AND usd_cents IS NULL
                                AND currency IS NOT NULL AND currency != 'USD'""").fetchall():
        rate = rates.get(cur)
        if rate and rate["source"]:
            db.execute("""UPDATE checks SET usd_cents=CAST(ROUND(price_cents * ?) AS INTEGER), fx_rate=?,
                          fx_source=? WHERE ok=1 AND usd_cents IS NULL AND currency=?""",
                       (rate["rate"], rate["rate"], rate["source"] + " (applied later)", cur))
    db.commit()


def run(only_urls=None):
    """Check every item, or only those whose URL is in only_urls. Returns {url: result}."""
    db = connect()
    items = spike.parse_items(ITEMS_PATH)
    ids = sync(db, items, parse_tags(TAGS_PATH))
    lines = ["| Item | Result |", "|---|---|"]
    results = {}
    rates = fx.Rates(db)
    for it, iid in zip(items, ids):
        if only_urls is not None and it["url"] not in only_urls:
            continue
        res = check_item(it["url"])
        rate = rates.get(res["currency"]) if res["ok"] else None
        record(db, iid, res, rate)
        results[it["url"]] = res
        if res["ok"]:
            usd = f" = {res['usd']:.2f} USD ({rate['source']})" if rate and rate["source"] and res.get("usd") else ""
            lines.append(f"| {res['title'] or it['url']} | {res['price']:.2f} {res['currency'] or ''}{usd}"
                         f" / {res['availability']} ({res['method']}) |")
        else:
            lines.append(f"| {it['url']} | failed: {res['error']} |")
    backfill(db, rates)
    export(db)
    ok = sum(1 for r in results.values() if r["ok"])
    summary = "\n".join(["## Price check", "", *lines, "",
                         f"{ok} of {len(results)} items checked successfully."])
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(summary + "\n")
    return results


def main():
    run()


if __name__ == "__main__":
    main()
