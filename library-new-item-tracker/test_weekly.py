from datetime import datetime, timedelta, timezone

import weekly_summary as ws

NOW = datetime(2026, 10, 25, 16, 7, tzinfo=timezone.utc)
LIBS = [{"key": "mvpl", "name": "Mountain View", "total": 3, "since": "2026-10-01T00:00:00+00:00"},
        {"key": "sccl", "name": "Santa Clara County", "total": 2, "since": "2026-10-01T00:00:00+00:00"}]


def item(title, days_ago=1.0, library="mvpl", baseline=False, branches=()):
    added = NOW - timedelta(days=days_ago)
    return {"library": library, "id": title, "title": title, "url": "", "baseline": baseline,
            "added": added.isoformat(), "branches": list(branches)}


def data(items, checked_hours_ago=1):
    return {"generated_at": (NOW - timedelta(hours=checked_hours_ago)).isoformat(),
            "total": 1017, "libraries": LIBS, "items": items}


def test_zero_games_still_produces_a_message():
    msg = ws.build(data([item("Old baseline", 30, baseline=True), item("Too old", 8)]), NOW)
    assert msg["title"] == "Weekly PS5 summary: no new games"
    assert msg["body"].startswith("No new PS5 games in the last 7 days.")
    assert "Last daily check: Oct 25, 15:07 UTC. 1017 games tracked across 2 libraries." in msg["body"]
    assert (msg["priority"], msg["tags"]) == ("default", "calendar")


def test_lists_games_from_the_last_seven_days_newest_first():
    msg = ws.build(data([item("Older", 5), item("Newest", 0.5), item("Middle", 2),
                         item("Edge, just inside", 6.9), item("Just outside", 7.1),
                         item("Baseline", 3, baseline=True)]), NOW)
    lines = msg["body"].split("\n")
    assert msg["title"] == "Weekly PS5 summary: 4 new games"
    assert lines[0] == "4 new PS5 games in the last 7 days:"
    assert [l for l in lines if l.startswith("- ")] == [
        "- Newest (Mountain View)", "- Middle (Mountain View)", "- Older (Mountain View)",
        "- Edge, just inside (Mountain View)"]


def test_singular_and_branches():
    msg = ws.build(data([item("Wolverine", 1, library="sccl", branches=["Saratoga", "Cupertino"])]), NOW)
    assert msg["title"] == "Weekly PS5 summary: 1 new game"
    assert "1 new PS5 game in the last 7 days:" in msg["body"]
    assert "- Wolverine (Santa Clara County: Cupertino, Saratoga)" in msg["body"]


def test_long_lists_are_cut_with_a_pointer_to_the_page():
    msg = ws.build(data([item(f"Game {n}", 1 + n / 100) for n in range(40)]), NOW)
    games = [l for l in msg["body"].split("\n") if l.startswith("- ")]
    assert len(games) == ws.LIMIT
    assert "...and 15 more (see the tracker page)" in msg["body"]
    assert msg["title"] == "Weekly PS5 summary: 40 new games"
    assert len(msg["body"].encode()) < 4096          # ntfy's message size limit


def test_stale_data_becomes_a_high_priority_warning():
    msg = ws.build(data([item("A", 1)], checked_hours_ago=60), NOW)
    assert msg["title"] == "Weekly PS5 summary: daily check is behind"
    assert msg["body"].startswith("WARNING: the daily check last ran 2 days ago")
    assert (msg["priority"], msg["tags"]) == ("high", "warning")
    assert "- A (Mountain View)" in msg["body"]       # still lists what it knows
    ok = ws.build(data([item("A", 1)], checked_hours_ago=30), NOW)   # 30 h: a late run, not stale
    assert ok["priority"] == "default"


def test_main_writes_the_message_and_action_outputs(tmp_path, monkeypatch):
    import json
    now = datetime.now(timezone.utc)
    games = {"generated_at": now.isoformat(), "total": 1, "libraries": LIBS,
             "items": [{"library": "mvpl", "id": "1", "title": "Fresh", "url": "", "baseline": False,
                        "added": (now - timedelta(hours=3)).isoformat(), "branches": []}]}
    src, out, gh = tmp_path / "games.json", tmp_path / "msg.txt", tmp_path / "gh_output"
    src.write_text(json.dumps(games))
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh))
    assert ws.main(["weekly_summary.py", str(src), str(out)]) == 0
    assert "- Fresh (Mountain View)" in out.read_text()
    outputs = dict(l.split("=", 1) for l in gh.read_text().splitlines())
    assert outputs == {"title": "Weekly PS5 summary: 1 new game", "priority": "default", "tags": "calendar"}
