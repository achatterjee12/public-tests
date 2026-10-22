import io
import urllib.error
from datetime import date

import bibliocommons as bc
import vega


def bib(bid, title="Game", fmt="VIDEO_GAME", edition="PlayStation 5", call="793.932 GAME PS5 1DISC",
        sub=""):
    return {"id": bid, "briefInfo": {"id": bid, "title": title, "subtitle": sub, "format": fmt,
                                     "edition": edition, "callNumber": call}}


def test_bibliocommons_ps5_detection():
    assert bc.is_ps5_game(bib("1"))
    assert bc.is_ps5_game(bib("2", edition="PS5, Deluxe edition", call=""))
    assert bc.is_ps5_game(bib("3", edition="", call="J 793.932 LEGO PS5 1DISC"))
    assert not bc.is_ps5_game(bib("4", edition="Nintendo Switch", call="GAME SWITCH Mario"))
    assert not bc.is_ps5_game(bib("5", edition="PlayStation 4", call="793.932 X PS4 1DISC"))
    assert not bc.is_ps5_game(bib("6", fmt="EBOOK", edition="", title="PlayStation 5 for dummies"))


def test_title_includes_subtitle():
    assert bc.title_of(bib("1", "Inspector Gadget", sub="Mad Time Party")) == \
        "Inspector Gadget: Mad Time Party"
    assert bc.title_of(bib("1", " Elden Ring ")) == "Elden Ring"


def _page(ids, page, pages, all_bibs):
    return {"catalogSearch": {"results": [{"representative": i} for i in ids],
                              "pagination": {"page": page, "pages": pages}},
            "entities": {"bibs": {i: all_bibs[i] for i in ids}}}


def test_search_follows_pages_and_stops(monkeypatch):
    all_bibs = {str(n): bib(str(n)) for n in range(5)}
    pages = {1: ["0", "1"], 2: ["2", "3"], 3: ["4"]}
    calls = []

    def fake_get(url, tries=3):
        n = int(url.rsplit("page=", 1)[1])
        calls.append(n)
        return _page(pages[n], n, 3, all_bibs)

    monkeypatch.setattr(bc, "_get", fake_get)
    assert [b["id"] for b in bc.search_bibs("sccl", "ps5")] == ["0", "1", "2", "3", "4"]
    assert calls == [1, 2, 3]


def test_search_stops_if_page_parameter_is_ignored(monkeypatch):
    all_bibs = {"0": bib("0"), "1": bib("1")}
    monkeypatch.setattr(bc, "_get", lambda url, tries=3: _page(["0", "1"], 1, 50, all_bibs))
    assert [b["id"] for b in bc.search_bibs("sccl", "ps5")] == ["0", "1"]  # not 50 pages of repeats


def test_branches_are_cleaned_and_deduplicated(monkeypatch):
    items = {"a": {"branchName": "Cupertino Library"}, "b": {"branch": {"name": "Los Altos Library"}},
             "c": {"branchName": "Cupertino Library"}, "d": {"branchName": "Northside Branch Library"}}
    monkeypatch.setattr(bc, "_get", lambda url, tries=3: {"entities": {"bibItems": items}})
    assert bc.branches_of("sccl", "X") == ["Cupertino", "Los Altos", "Northside"]


def test_branches_unknown_when_lookup_fails(monkeypatch):
    def boom(url, tries=3):
        raise OSError("down")
    monkeypatch.setattr(bc, "_get", boom)
    assert bc.branches_of("sccl", "X") == []


def test_fetch_unions_queries_and_filters(monkeypatch):
    games = {"1": bib("1", "Ghost"), "2": bib("2", "Mario", edition="Nintendo Switch", call="GAME SWITCH M"),
             "3": bib("3", "Dune")}
    by_query = {"playstation 5": ["1", "2"], "ps5": ["1", "3"]}
    monkeypatch.setattr(bc, "search_bibs", lambda slug, q: (games[i] for i in by_query[q]))
    monkeypatch.setattr(bc, "branches_of", lambda slug, i: ["Cupertino"] if i == "1" else [])
    got = bc.fetch("sccl", known={})
    assert [(i.id, i.title, i.library, i.branches) for i in got] == [
        ("1", "Ghost", "sccl", ["Cupertino"]), ("3", "Dune", "sccl", [])]
    assert got[0].url == "https://sccl.bibliocommons.com/v2/record/1"


def test_vega_ps5_detection():
    ps5 = {"materialTabs": [{"materialTypes": ["m"], "callNumber": "GAME PS5 Sonic",
                             "editions": [{"edition": "PlayStation 5.", "callNumber": "GAME PS5 Sonic"}]}]}
    switch = {"materialTabs": [{"materialTypes": ["m"], "callNumber": "GAME SWITCH Mario",
                                "editions": [{"edition": "Nintendo Switch.", "callNumber": "GAME SWITCH Mario"}]}]}
    ebook = {"materialTabs": [{"materialTypes": ["1"], "callNumber": "", "editions": [
        {"edition": "PlayStation 5 guide", "callNumber": ""}]}]}
    assert vega.is_ps5_game(ps5) and not vega.is_ps5_game(switch) and not vega.is_ps5_game(ebook)


def test_lookups_prioritise_missing_and_respect_cap():
    ids = [str(n) for n in range(10)]
    known = {i: ["Cupertino"] for i in ids[:6]}              # 4..9 have nothing stored
    got = bc.lookups_needed(ids, known, today=date(2026, 10, 20), cap=100)
    assert got[:4] == ["6", "7", "8", "9"]                    # missing ones first, in order
    assert bc.lookups_needed(ids, known, today=date(2026, 10, 20), cap=2) == ["6", "7"]


def test_lookups_rotate_through_stored_items():
    ids = [f"S{n}" for n in range(300)]
    known = {i: ["Cupertino"] for i in ids}                   # everything already stored
    seen = set()
    for d in range(bc.REFRESH_DAYS):
        day = date.fromordinal(date(2026, 10, 20).toordinal() + d)
        todo = bc.lookups_needed(ids, known, today=day, cap=10_000)
        assert 0 < len(todo) < 60                              # a slice, never everything
        seen.update(todo)
    assert seen == set(ids)                                    # all refreshed within the cycle
    assert bc.lookups_needed(ids, known, today=date(2026, 10, 20)) == \
        bc.lookups_needed(ids, known, today=date(2026, 10, 20))  # deterministic


def test_fetch_only_looks_up_what_is_needed(monkeypatch):
    games = {str(n): bib(str(n), f"G{n}") for n in range(3)}
    monkeypatch.setattr(bc, "search_bibs", lambda slug, q: iter(games.values()) if q == "ps5" else iter([]))
    asked = []
    monkeypatch.setattr(bc, "branches_of", lambda slug, i: asked.append(i) or ["Los Altos"])
    known = {"0": ["Cupertino"]}                               # 0 already stored; 1 and 2 are not
    # pick a day on which 0 is not due for its periodic refresh
    today = next(d for d in (date.fromordinal(739000 + n) for n in range(bc.REFRESH_DAYS))
                 if "0" not in bc.lookups_needed(["0", "1", "2"], known, today=d))
    by = {i.id: i.branches for i in bc.fetch("sccl", known=known, today=today)}
    assert sorted(asked) == ["1", "2"]
    assert by == {"0": ["Cupertino"], "1": ["Los Altos"], "2": ["Los Altos"]}


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_get_backs_off_when_throttled(monkeypatch):
    waits, calls = [], []

    def fake_urlopen(req, timeout=0):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
        return _Resp(b'{"ok": true}')

    monkeypatch.setattr(bc.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(bc.time, "sleep", waits.append)
    assert bc._get("https://example.test/x") == {"ok": True}
    assert waits[:2] == [bc.THROTTLE_WAIT, bc.THROTTLE_WAIT * 2]  # long, doubling waits for 403
    assert waits[-1] == bc.REQUEST_DELAY                           # then the normal spacing


def test_get_gives_up_after_three_tries(monkeypatch):
    monkeypatch.setattr(bc.time, "sleep", lambda s: None)

    def always_403(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(bc.urllib.request, "urlopen", always_403)
    try:
        bc._get("https://example.test/x")
        assert False, "should have raised"
    except urllib.error.HTTPError as e:
        assert e.code == 403


def test_throttling_stops_further_branch_lookups(monkeypatch):
    monkeypatch.setattr(bc, "_throttled", False)
    monkeypatch.setattr(bc.time, "sleep", lambda s: None)
    calls = []

    def always_403(req, timeout=0):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(bc.urllib.request, "urlopen", always_403)
    assert bc.branches_of("sccl", "A") == []
    used = len(calls)                      # the first lookup used its retries
    assert used == 3 and bc._throttled
    assert bc.branches_of("sccl", "B") == [] and len(calls) == used   # no more requests
    monkeypatch.setattr(bc, "_throttled", False)
