from datetime import datetime, timedelta, timezone
import json
import sqlite3

from tracker import (Item, check, empty_results, export_json, format_notification,
                     known_branches, open_db, visible_items, visible_new, where)

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
    assert format_notification(items[:2]) == "- Game 0 (Mountain View)\n- Game 1 (Mountain View)"
    lines = format_notification(items).split("\n")
    assert len(lines) == 11 and lines[-1] == "...and 2 more"


def test_each_library_is_baselined_separately():
    db = open_db(":memory:")
    a = [Item("1", "A", library="mvpl")]
    check(a, db, NOW)                                    # mvpl baseline
    later = NOW + timedelta(days=1)
    items = a + [Item("1", "A", library="sccl"), Item("2", "B", library="sccl")]
    new, total = check(items, db, later)                 # sccl's first fetch: silent
    assert new == [] and total == 3
    new, _ = check(items + [Item("3", "C", library="mvpl"), Item("3", "C", library="sccl")],
                   db, later + timedelta(days=1))
    assert sorted((i.library, i.id) for i in new) == [("mvpl", "3"), ("sccl", "3")]


def test_same_game_at_two_libraries_is_two_items():
    db = open_db(":memory:")
    check([Item("x", "Game", library="mvpl")], db, NOW)
    new, total = check([Item("x", "Game", library="mvpl"), Item("x", "Game", library="sunnyvale"),
                        Item("y", "Other", library="mvpl")], db, NOW + timedelta(days=1))
    assert total == 3                                    # sunnyvale baseline + mvpl y
    assert [(i.library, i.id) for i in new] == [("mvpl", "y")]


def test_empty_library_still_counts_as_baselined():
    db = open_db(":memory:")
    check([Item("1", "A", library="mvpl")], db, NOW, libraries=["mvpl", "sunnyvale"])
    new, _ = check([Item("1", "A", library="mvpl"), Item("9", "First PS5", library="sunnyvale")],
                   db, NOW + timedelta(days=1))
    assert [(i.library, i.id) for i in new] == [("sunnyvale", "9")]


def test_failed_library_is_left_alone():
    db = open_db(":memory:")
    check([Item("1", "A", library="mvpl"), Item("1", "A", library="sccl")], db, NOW)
    # sccl failed this run, so it is not in `libraries` and nothing changes for it
    new, total = check([Item("1", "A", library="mvpl")], db, NOW + timedelta(days=1),
                       libraries=["mvpl"])
    assert new == [] and total == 2


def test_branches_are_stored_and_updated(tmp_path):
    db = open_db(":memory:")
    check([Item("1", "A", library="sccl", branches=["Cupertino"])], db, NOW)
    check([Item("1", "A", library="sccl", branches=["Cupertino", "Los Altos"])],
          db, NOW + timedelta(days=1))
    check([Item("1", "A", library="sccl", branches=[])], db, NOW + timedelta(days=2))  # unknown: keep
    out = tmp_path / "g.json"
    export_json(db, str(out), NOW)
    data = json.loads(out.read_text())
    assert data["items"][0]["branches"] == ["Cupertino", "Los Altos"]
    assert data["items"][0]["library"] == "sccl"


def test_export_lists_libraries_with_counts(tmp_path):
    db = open_db(":memory:")
    check([Item("1", "A", library="sccl")], db, NOW, libraries=["sccl", "sunnyvale", "mvpl"])
    out = tmp_path / "g.json"
    export_json(db, str(out), NOW)
    libs = json.loads(out.read_text())["libraries"]
    assert [(l["key"], l["name"], l["total"]) for l in libs] == [
        ("mvpl", "Mountain View", 0), ("sunnyvale", "Sunnyvale", 0), ("sccl", "Santa Clara County", 1)]


def test_single_library_database_is_migrated_to_mvpl(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE items (id TEXT PRIMARY KEY, title TEXT, url TEXT, "
                "catalog_added TEXT, first_seen TEXT, baseline INTEGER DEFAULT 0)")
    old.executemany("INSERT INTO items VALUES (?,?,?,?,?,?)", [
        ("1", "A", "u1", None, "2026-10-20T19:00:00+00:00", 1),
        ("2", "B", "u2", None, "2026-10-22T14:00:00+00:00", 0)])
    old.commit(); old.close()
    db = open_db(path)
    assert db.execute("SELECT library, id, baseline FROM items ORDER BY id").fetchall() == [
        ("mvpl", "1", 1), ("mvpl", "2", 0)]
    assert [r[0] for r in db.execute("SELECT library FROM libraries")] == ["mvpl"]
    # the migrated library is already baselined, so a new game is announced
    new, _ = check([Item("1", "A"), Item("2", "B"), Item("3", "C")], db, NOW)
    assert [i.id for i in new] == ["3"]


def test_empty_results_flags_a_library_that_lost_its_games():
    db = open_db(":memory:")
    check([Item(str(n), f"G{n}", library="mvpl") for n in range(5)] +
          [Item("s", "S", library="sunnyvale")], db, NOW, libraries=["mvpl", "sunnyvale", "sccl"])
    # mvpl had 5 games; sunnyvale only 1 (could really have dropped it); sccl never had any
    assert empty_results({"mvpl": [], "sunnyvale": [], "sccl": []}, db) == ["mvpl"]
    assert empty_results({"mvpl": [Item("1", "A")]}, db) == []


def test_where_formatting():
    assert where(Item("1", "A", library="sunnyvale")) == "Sunnyvale"
    assert where(Item("1", "A", library="sccl", branches=["Los Altos", "Cupertino"])) == \
        "Santa Clara County: Cupertino, Los Altos"
    assert where(Item("1", "A", library="sccl", branches=list("DCBA"))) == \
        "Santa Clara County: A, B, C +1"


def test_known_branches_feeds_the_fetchers():
    db = open_db(":memory:")
    check([Item("1", "A", library="sccl", branches=["Cupertino", "Los Altos"]),
           Item("2", "B", library="sccl"), Item("3", "C", library="mvpl")], db, NOW)
    assert known_branches(db) == {"sccl": {"1": ["Cupertino", "Los Altos"], "2": []},
                                  "mvpl": {"3": []}}


def test_games_only_at_excluded_branches_are_hidden_but_kept():
    db = open_db(":memory:")
    check([Item("1", "Only Gilroy", library="sccl", branches=["Gilroy", "Morgan Hill"]),
           Item("2", "Gilroy and Cupertino", library="sccl", branches=["Cupertino", "Gilroy"]),
           Item("3", "Branches unknown yet", library="sccl"),
           Item("4", "Bookmobile only", library="sccl", branches=["Bookmobile Department"]),
           Item("5", "Elsewhere", library="mvpl")], db, NOW)
    shown = {i["id"]: i["branches"] for i in visible_items(db)}
    assert shown == {"2": ["Cupertino"], "3": [], "5": []}
    assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 5   # nothing is deleted


def test_export_counts_only_visible_games(tmp_path):
    db = open_db(":memory:")
    check([Item("1", "Hidden", library="sccl", branches=["Gilroy"]),
           Item("2", "Shown", library="sccl", branches=["Campbell"])], db, NOW)
    out = tmp_path / "g.json"
    export_json(db, str(out), NOW)
    data = json.loads(out.read_text())
    assert data["total"] == 1 and [l["total"] for l in data["libraries"]] == [1]
    assert [i["title"] for i in data["items"]] == ["Shown"]


def test_hidden_games_are_not_announced_and_names_leave_out_excluded_branches():
    gilroy = Item("1", "Gilroy only", library="sccl", branches=["Gilroy"])
    both = Item("2", "Both", library="sccl", branches=["Cupertino", "Morgan Hill"])
    unknown = Item("3", "Unknown", library="sccl")
    assert visible_new([gilroy, both, unknown]) == [both, unknown]
    assert where(both) == "Santa Clara County: Cupertino"
    assert format_notification([both]) == "- Both (Santa Clara County: Cupertino)"


def test_changing_the_exclusions_needs_no_refetch(monkeypatch):
    import tracker
    db = open_db(":memory:")
    check([Item("1", "G", library="sccl", branches=["Gilroy"])], db, NOW)
    assert visible_items(db) == []
    monkeypatch.setitem(tracker.EXCLUDED_BRANCHES, "sccl", set())      # now keep Gilroy too
    assert [i["id"] for i in visible_items(db)] == ["1"]


def test_export_lists_the_exclusions(tmp_path):
    db = open_db(":memory:")
    check([Item("1", "A", library="sccl", branches=["Campbell"])], db, NOW)
    out = tmp_path / "g.json"
    export_json(db, str(out), NOW)
    ex = json.loads(out.read_text())["excluded_branches"]
    assert ex == [{"library": "Santa Clara County",
                   "branches": ["Bookmobile Department", "Gilroy", "Morgan Hill"]}]
