"""Libraries on BiblioCommons: Santa Clara County, Santa Clara City and Sunnyvale.

Each catalog site is backed by an anonymous JSON gateway. Findings from probing it:
  * GET /v2/libraries/<slug>/bibs/search returns 25 records per page (the limit
    parameter is ignored; use page=N). f_FORMAT=VIDEO_GAME filters to games and
    sort=newly_acquired lists the newest first;
  * records carry no "date added"; a game's platform is in briefInfo.edition and
    callNumber (e.g. "PlayStation 5", "PS5, PlayStation 5", "793.932 GHOST PS5");
  * GET /v2/libraries/<slug>/bibs/<id>/availability lists every copy with its
    branch, which is how the branches that hold a game are found.
"""
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from tracker import Item

GATEWAY = "https://gateway.bibliocommons.com/v2/libraries/{slug}"
QUERIES = ("playstation 5", "ps5")  # their results overlap; the union is more complete
PS5 = re.compile(r"playstation ?5|\bps ?5\b", re.I)
BRANCH_SUFFIX = re.compile(r"\s+(Branch\s+)?Library$", re.I)
WORKERS = 6
HEADERS = {"accept": "application/json",
           "user-agent": "Mozilla/5.0 (X11; Linux x86_64) ps5-tracker"}


def _get(url, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)


def is_ps5_game(bib):
    info = bib.get("briefInfo") or {}
    return info.get("format") == "VIDEO_GAME" and any(
        PS5.search(str(info.get(k) or "")) for k in ("edition", "callNumber", "title", "subtitle"))


def title_of(bib):
    info = bib["briefInfo"]
    sub = (info.get("subtitle") or "").strip()
    return f"{info['title'].strip()}: {sub}" if sub else info["title"].strip()


def search_bibs(slug, query):
    """Every video-game record matching query, newest acquired first."""
    base = GATEWAY.format(slug=slug) + "/bibs/search?" + urllib.parse.urlencode({
        "query": query, "searchType": "smart", "sort": "newly_acquired",
        "f_FORMAT": "VIDEO_GAME", "locale": "en-US"})
    seen, page = set(), 1
    while True:
        data = _get(f"{base}&page={page}")
        cs, ents = data["catalogSearch"], (data.get("entities") or {}).get("bibs") or {}
        fresh = [r["representative"] for r in cs["results"] if r["representative"] not in seen]
        if not fresh:   # page parameter ignored or past the end: stop, never loop
            return
        for bib_id in fresh:
            seen.add(bib_id)
            if bib_id in ents:
                yield ents[bib_id]
        if page >= cs["pagination"]["pages"]:
            return
        page += 1


def branches_of(slug, bib_id):
    """Sorted branch names holding the record; [] if unknown."""
    try:
        data = _get(GATEWAY.format(slug=slug) + f"/bibs/{bib_id}/availability?locale=en-US")
        items = (data.get("entities") or {}).get("bibItems") or {}
    except Exception:
        return []
    names = {BRANCH_SUFFIX.sub("", (i.get("branchName") or (i.get("branch") or {}).get("name") or "").strip())
             for i in items.values()}
    return sorted(n for n in names if n)


def fetch(slug, library=None):
    """All PS5 games at one BiblioCommons library, as Items."""
    library = library or slug
    bibs = {}
    for query in QUERIES:
        for bib in search_bibs(slug, query):
            if is_ps5_game(bib):
                bibs.setdefault(bib["id"], bib)
    with ThreadPoolExecutor(WORKERS) as pool:
        branches = list(pool.map(lambda i: branches_of(slug, i), bibs))
    return [Item(bid, title_of(bib), f"https://{slug}.bibliocommons.com/v2/record/{bid}",
                 library=library, branches=br)
            for (bid, bib), br in zip(bibs.items(), branches)]
