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

import spike

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("PRICE_DB", os.path.join(HERE, "prices.db"))
ITEMS_PATH = os.path.join(HERE, "items.txt")
TAGS_PATH = os.path.join(HERE, "tags.txt")
JSON_PATH = os.path.join(HERE, "items.json")
HISTORY_DIR = os.path.join(HERE, "history")
METHOD_ORDER = ["shopify", "jsonld", "meta", "microdata"]

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
    results.sort(key=lambda r: METHOD_ORDER.index(r["method"]))
    best = next((r for r in results if r["ok"]), None)
    out["title"] = next((r["title"] for r in results if r.get("title")), None)
    out["currency"] = next((r["currency"] for r in results if r.get("currency")), None)
    out["image"] = image_from(out, sc.meta)
    if not best:
        out["error"] = out["error"] or "no price found on the page"
        return out
    out.update(ok=True, price=best["price"], method=best["method"],
               availability=best["availability"]
               or next((r["availability"] for r in results if r.get("availability")), "unknown"))
    return out


def record(db, iid, res):
    when = now_iso()
    cents = round(res["price"] * 100) if res["ok"] else None
    db.execute("""INSERT INTO checks(item_id, checked_at, ok, price_cents, currency, availability,
                  method, fetch_method, http_status, error) VALUES (?,?,?,?,?,?,?,?,?,?)""",
               (iid, when, int(res["ok"]), cents, res["currency"], res["availability"],
                res["method"], "http", res["status"], res["error"]))
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
        good = [c for c in checks if c["ok"]]
        last, last_ok = (checks[-1] if checks else None), (good[-1] if good else None)
        prices = [c["price_cents"] for c in good]
        low = min(good, key=lambda c: (c["price_cents"], c["checked_at"])) if good else None
        tags = [r["name"] for r in db.execute(
            """SELECT t.name FROM item_tags it JOIN tags t ON t.id=it.tag_id
               WHERE it.item_id=? ORDER BY t.id""", (it["id"],))]
        month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        before = [c for c in good if c["checked_at"] <= month_ago]
        out_items.append({
            "id": it["id"], "url": it["url"], "retailer": it["retailer"],
            "title": it["title"] or title_from_url(it["url"]), "image": it["image_url"],
            "currency": it["currency"] or "USD", "tags": tags, "note": it["note"],
            "target": it["target_price_cents"], "added_at": it["added_at"],
            "price": last_ok["price_cents"] if last_ok else None,
            "availability": last_ok["availability"] if last_ok else None,
            "last_checked": last["checked_at"] if last else None,
            "last_ok": bool(last and last["ok"]),
            "last_error": last["error"] if last and not last["ok"] else None,
            "low": low["price_cents"] if low else None,
            "low_at": low["checked_at"] if low else None,
            "high": max(prices) if prices else None,
            "avg": round(sum(prices) / len(prices)) if prices else None,
            "price_30d_ago": before[-1]["price_cents"] if before else None,
            "checks": len(checks),
            "spark": [[c["checked_at"][:10], c["price_cents"]] for c in good if c["checked_at"] >= cutoff],
        })
        hist = [{"t": c["checked_at"], "ok": bool(c["ok"]), "price": c["price_cents"],
                 "availability": c["availability"], "method": c["method"], "error": c["error"]}
                for c in checks]
        with open(os.path.join(history_dir, f"{it['id']}.json"), "w", encoding="utf-8") as f:
            json.dump(hist, f, separators=(",", ":"))
    data = {"generated_at": now_iso(),
            "tags": [{"name": r["name"], "color": r["color"], "count": r["n"]} for r in tag_rows],
            "items": out_items}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    return data


def run(only_urls=None):
    """Check every item, or only those whose URL is in only_urls. Returns {url: result}."""
    db = connect()
    items = spike.parse_items(ITEMS_PATH)
    ids = sync(db, items, parse_tags(TAGS_PATH))
    lines = ["| Item | Result |", "|---|---|"]
    results = {}
    for it, iid in zip(items, ids):
        if only_urls is not None and it["url"] not in only_urls:
            continue
        res = check_item(it["url"])
        record(db, iid, res)
        results[it["url"]] = res
        if res["ok"]:
            lines.append(f"| {res['title'] or it['url']} | {res['price']:.2f} {res['currency'] or ''}"
                         f" / {res['availability']} ({res['method']}) |")
        else:
            lines.append(f"| {it['url']} | failed: {res['error']} |")
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
