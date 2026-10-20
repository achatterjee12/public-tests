"""Track new PS5 games at Mountain View Public Library.

A game counts as "new" if either:
  1. the catalog reports an added date within the last 24 hours, or
  2. it is not yet in the local SQLite database of known items.
The first run on an empty database only builds the baseline (nothing is
reported as new unless the catalog gave an added date within 24 hours).

Each item's first_seen time is its "added" time for the web page: the
catalog exposes no added date, so this is accurate to within one run.
Baseline items (the first run's catalog snapshot) are flagged and kept
off the "new" lists. Every run also exports games.json for index.html.
"""
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

DB_PATH = os.environ.get("PS5_DB", "ps5_games.db")
JSON_PATH = os.environ.get("PS5_JSON", "games.json")
WINDOW = timedelta(hours=24)


@dataclass
class Item:
    id: str                       # catalog record id (stable)
    title: str
    url: str = ""
    added: Optional[datetime] = None  # catalog "date added", tz-aware, if exposed


def open_db(path=DB_PATH):
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE IF NOT EXISTS items (
        id TEXT PRIMARY KEY, title TEXT, url TEXT,
        catalog_added TEXT, first_seen TEXT, baseline INTEGER DEFAULT 0)""")
    if "baseline" not in [r[1] for r in db.execute("PRAGMA table_info(items)")]:
        # database from before the baseline flag: its first run is the baseline
        db.execute("ALTER TABLE items ADD COLUMN baseline INTEGER DEFAULT 0")
        db.execute("UPDATE items SET baseline = 1 WHERE first_seen = "
                   "(SELECT MIN(first_seen) FROM items)")
        db.commit()
    return db


def check(items, db, now=None):
    """Store items; return (new_items, running_total)."""
    now = now or datetime.now(timezone.utc)
    baseline = db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    new = []
    for it in items:
        known = db.execute("SELECT 1 FROM items WHERE id=?", (it.id,)).fetchone()
        recent = it.added is not None and now - it.added <= WINDOW
        if recent or (not known and not baseline and it.added is None):
            new.append(it)
        if not known:
            db.execute("INSERT INTO items VALUES (?,?,?,?,?,?)",
                       (it.id, it.title, it.url,
                        it.added.isoformat() if it.added else None,
                        now.isoformat(), int(baseline)))
    db.commit()
    total = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    return new, total


def export_json(db, path=JSON_PATH, now=None):
    """Write everything index.html needs; it works out the 24h / 7d windows."""
    now = now or datetime.now(timezone.utc)
    rows = db.execute("SELECT id, title, url, first_seen, baseline FROM items "
                      "ORDER BY first_seen DESC, title").fetchall()
    with open(path, "w") as f:
        json.dump({
            "generated_at": now.isoformat(),
            "total": len(rows),
            "items": [{"id": r[0], "title": r[1], "url": r[2],
                       "added": r[3], "baseline": bool(r[4])} for r in rows],
        }, f, indent=1)


def main():
    from catalog import fetch_ps5_items  # site-specific; see catalog.py
    db = open_db()
    new, total = check(fetch_ps5_items(), db)
    export_json(db)
    print(f"{total} PS5 games tracked; {len(new)} new in the last day")
    for it in new:
        print(f"  - {it.title} {it.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
