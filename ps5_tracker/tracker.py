"""Track new PS5 games at Mountain View Public Library.

A game counts as "new" if either:
  1. the catalog reports an added date within the last 24 hours, or
  2. it is not yet in the local SQLite database of known items.
The first run on an empty database only builds the baseline (nothing is
reported as new unless the catalog gave an added date within 24 hours).
"""
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

DB_PATH = "ps5_games.db"
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
        catalog_added TEXT, first_seen TEXT)""")
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
            db.execute("INSERT INTO items VALUES (?,?,?,?,?)",
                       (it.id, it.title, it.url,
                        it.added.isoformat() if it.added else None, now.isoformat()))
    db.commit()
    total = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    return new, total


def main():
    from catalog import fetch_ps5_items  # site-specific; see catalog.py
    db = open_db()
    new, total = check(fetch_ps5_items(), db)
    print(f"{total} PS5 games tracked; {len(new)} new in the last day")
    for it in new:
        print(f"  - {it.title} {it.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
