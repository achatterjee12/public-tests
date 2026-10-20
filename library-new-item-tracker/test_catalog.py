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
    got = bc.fetch("sccl")
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
