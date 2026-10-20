"""Track new PS5 games at several local libraries.

An item is one library's catalog record, so the same game at two libraries is
two items. A game counts as "new" if either:
  1. the catalog reports an added date within the last 24 hours, or
  2. it is not yet in the local SQLite database of known items.
Each library is baselined separately: on its first successful fetch its whole
catalog snapshot is recorded without being reported as new, so adding a library
later does not flood the notifications.

The catalogs expose no added date, so an item's first_seen time is its "added"
time for the web page (accurate to within one run). Baseline items are flagged
and kept off the "new" lists. Every run also exports games.json for index.html.
"""
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

DB_PATH = os.environ.get("PS5_DB", "ps5_games.db")
JSON_PATH = os.environ.get("PS5_JSON", "games.json")
NEW_FILE = os.environ.get("PS5_NEW_FILE")  # if set, new-game message is written here
WINDOW = timedelta(hours=24)

# Display names, in the order shown on the page.
LIBRARY_NAMES = {
    "mvpl": "Mountain View",
    "sunnyvale": "Sunnyvale",
    "sclibrary": "Santa Clara City",
    "sccl": "Santa Clara County",
}


@dataclass
class Item:
    id: str                       # catalog record id (stable within one library)
    title: str
    url: str = ""
    added: Optional[datetime] = None  # catalog "date added", tz-aware, if exposed
    library: str = "mvpl"         # key of LIBRARY_NAMES
    branches: list = field(default_factory=list)  # branches holding it, if known


SCHEMA = """CREATE TABLE {name} (
    library TEXT NOT NULL, id TEXT NOT NULL, title TEXT, url TEXT,
    catalog_added TEXT, first_seen TEXT, baseline INTEGER DEFAULT 0, branches TEXT,
    PRIMARY KEY (library, id))"""


def open_db(path=DB_PATH):
    db = sqlite3.connect(path)
    cols = [r[1] for r in db.execute("PRAGMA table_info(items)")]
    if not cols:
        db.execute(SCHEMA.format(name="items"))
    elif "library" not in cols:
        _migrate_single_library(db, cols)
    db.execute("CREATE TABLE IF NOT EXISTS libraries "
               "(library TEXT PRIMARY KEY, baselined_at TEXT)")
    db.execute("INSERT OR IGNORE INTO libraries "
               "SELECT library, MIN(first_seen) FROM items GROUP BY library")
    db.commit()
    return db


def _migrate_single_library(db, cols):
    """Databases from before multi-library support only held Mountain View."""
    if "baseline" not in cols:
        # even older: the first run (earliest first_seen) was the baseline
        db.execute("ALTER TABLE items ADD COLUMN baseline INTEGER DEFAULT 0")
        db.execute("UPDATE items SET baseline = 1 WHERE first_seen = "
                   "(SELECT MIN(first_seen) FROM items)")
    db.execute(SCHEMA.format(name="items_new"))
    db.execute("INSERT INTO items_new SELECT 'mvpl', id, title, url, catalog_added, "
               "first_seen, baseline, NULL FROM items")
    db.execute("DROP TABLE items")
    db.execute("ALTER TABLE items_new RENAME TO items")


def check(items, db, now=None, libraries=None):
    """Store items; return (new_items, running_total).

    libraries: keys of the libraries fetched successfully this run (default: those
    appearing in items). A library not yet baselined has this run recorded as its
    baseline, even if it returned no games.
    """
    now = now or datetime.now(timezone.utc)
    fetched = set(libraries) if libraries is not None else {it.library for it in items}
    baselined = {r[0] for r in db.execute("SELECT library FROM libraries")}
    new = []
    for it in items:
        row = db.execute("SELECT branches FROM items WHERE library=? AND id=?",
                         (it.library, it.id)).fetchone()
        first_run = it.library not in baselined
        recent = it.added is not None and now - it.added <= WINDOW
        if recent or (row is None and not first_run and it.added is None):
            new.append(it)
        branches = sorted(it.branches)
        if row is None:
            db.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?,?)",
                       (it.library, it.id, it.title, it.url,
                        it.added.isoformat() if it.added else None,
                        now.isoformat(), int(first_run), json.dumps(branches)))
        elif branches and json.loads(row[0] or "[]") != branches:
            db.execute("UPDATE items SET branches=? WHERE library=? AND id=?",
                       (json.dumps(branches), it.library, it.id))
    for lib in fetched - baselined:
        db.execute("INSERT INTO libraries VALUES (?,?)", (lib, now.isoformat()))
    db.commit()
    total = db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    return new, total


def known_branches(db):
    """{library: {record id: stored branch list}}, so fetchers can skip repeat lookups."""
    known = {}
    for lib, id_, branches in db.execute("SELECT library, id, branches FROM items"):
        known.setdefault(lib, {})[id_] = json.loads(branches or "[]")
    return known


def export_json(db, path=JSON_PATH, now=None):
    """Write everything index.html needs; it works out the 24h / 7d windows."""
    now = now or datetime.now(timezone.utc)
    rows = db.execute("SELECT library, id, title, url, first_seen, baseline, branches "
                      "FROM items ORDER BY first_seen DESC, title").fetchall()
    counts = dict(db.execute("SELECT library, COUNT(*) FROM items GROUP BY library"))
    since = dict(db.execute("SELECT library, baselined_at FROM libraries"))
    order = {k: n for n, k in enumerate(LIBRARY_NAMES)}
    libs = sorted(since, key=lambda k: order.get(k, len(order)))
    with open(path, "w") as f:
        json.dump({
            "generated_at": now.isoformat(),
            "total": len(rows),
            "libraries": [{"key": k, "name": LIBRARY_NAMES.get(k, k),
                           "total": counts.get(k, 0), "since": since[k]} for k in libs],
            "items": [{"library": r[0], "id": r[1], "title": r[2], "url": r[3],
                       "added": r[4], "baseline": bool(r[5]),
                       "branches": json.loads(r[6] or "[]")} for r in rows],
        }, f, indent=1)


def where(it, max_branches=3):
    """'Santa Clara County: Cupertino, Los Altos' (library plus branches if known)."""
    name = LIBRARY_NAMES.get(it.library, it.library)
    if not it.branches:
        return name
    shown = sorted(it.branches)[:max_branches]
    more = len(it.branches) - len(shown)
    return f"{name}: {', '.join(shown)}" + (f" +{more}" if more > 0 else "")


def format_notification(new, limit=10):
    """Message body for the push notification: one game per line."""
    lines = [f"- {it.title} ({where(it)})" for it in new[:limit]]
    if len(new) > limit:
        lines.append(f"...and {len(new) - limit} more")
    return "\n".join(lines)


EMPTY_ALERT_MIN = 5  # a library that had at least this many games and now has none


def empty_results(results, db):
    """Libraries that returned nothing although they had many games before.

    That almost certainly means a catalog change broke the fetcher, so it is
    reported as a failure instead of being stored as 'no games'.
    """
    counts = dict(db.execute("SELECT library, COUNT(*) FROM items GROUP BY library"))
    return [lib for lib, found in results.items()
            if not found and counts.get(lib, 0) >= EMPTY_ALERT_MIN]


def main():
    from catalog import fetch_all  # site-specific; see catalog.py
    db = open_db()
    results, errors = fetch_all(known_branches(db))
    for lib in empty_results(results, db):
        errors[lib] = "returned no games although it had many before"
        del results[lib]
    items = [it for found in results.values() for it in found]
    new, total = check(items, db, libraries=results)
    export_json(db)
    print(f"{total} PS5 games tracked across {len(results)} libraries; "
          f"{len(new)} new in the last day")
    for lib, found in results.items():
        print(f"  {LIBRARY_NAMES.get(lib, lib)}: {len(found)} games")
    for it in new:
        print(f"  - {it.title} ({where(it)}) {it.url}")
    if NEW_FILE and new:  # no file on quiet days, so notify.sh sends nothing
        with open(NEW_FILE, "w") as f:
            f.write(format_notification(new))
    for lib, err in errors.items():
        print(f"::error::{LIBRARY_NAMES.get(lib, lib)} failed: {err}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
