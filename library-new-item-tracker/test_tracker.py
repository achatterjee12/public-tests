from datetime import datetime, timedelta, timezone
from tracker import Item, check, open_db

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
