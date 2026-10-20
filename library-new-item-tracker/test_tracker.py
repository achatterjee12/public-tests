from datetime import datetime, timedelta, timezone
import json
import sqlite3

from tracker import Item, check, export_json, format_notification, open_db

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def test_baseline_then_new_item_without_date():
    db = open_db(":memory:")
    assert check([Item("1", "A"), Item("2", "B")], db, NOW)[0] == []  # baseline
    new, total = check([Item("1", "A"), Item("2", "B"), Item("3", "C")], db, NOW)
    assert [i.id for i in new] == ["3"] and total == 3
    assert check([Item("3", "C")], db, NOW)[0] == []  # not reported twice


def test_added_date_window():
    db = open_db(":memory:")
    fresh = Item("1", "Fresh", added=NOW - timedelta(hours=3))
    old = Item("2", "Old", added=NOW - timedelta(days=3))
    new, _ = check([fresh, old], db, NOW)
    assert [i.id for i in new] == ["1"]


def test_baseline_flag_and_export(tmp_path):
    db = open_db(":memory:")
    check([Item("1", "A")], db, NOW)                      # baseline run
    check([Item("1", "A"), Item("2", "B")], db, NOW + timedelta(days=1))
    out = tmp_path / "games.json"
    export_json(db, str(out), NOW + timedelta(days=1))
    data = json.loads(out.read_text())
    assert data["total"] == 2
    assert {i["id"]: i["baseline"] for i in data["items"]} == {"1": True, "2": False}
    assert data["items"][0]["id"] == "2"                  # newest first


def test_old_database_is_migrated(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE items (id TEXT PRIMARY KEY, title TEXT, url TEXT, "
                "catalog_added TEXT, first_seen TEXT)")
    old.executemany("INSERT INTO items VALUES (?,?,?,?,?)", [
        ("1", "A", "", None, "2026-10-20T19:00:00+00:00"),
        ("2", "B", "", None, "2026-10-20T19:00:00+00:00"),
        ("3", "C", "", None, "2026-10-22T14:00:00+00:00")])
    old.commit(); old.close()
    db = open_db(path)
    got = dict(db.execute("SELECT id, baseline FROM items"))
    assert got == {"1": 1, "2": 1, "3": 0}


def test_format_notification():
    items = [Item(str(n), f"Game {n}") for n in range(12)]
    assert format_notification(items[:2]) == "- Game 0\n- Game 1"
    lines = format_notification(items).split("\n")
    assert len(lines) == 11 and lines[-1] == "...and 2 more"
