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
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from tracker import Item

GATEWAY = "https://gateway.bibliocommons.com/v2/libraries/{slug}"
QUERIES = ("playstation 5", "ps5")  # their results overlap; the union is more complete
PS5 = re.compile(r"playstation ?5|\bps ?5\b", re.I)
BRANCH_SUFFIX = re.compile(r"\s+(Branch\s+)?Library$", re.I)
WORKERS = 4
# Branch lookups are one request per game and the gateway answers HTTP 403 to bursts
# of a few hundred. So each run looks up branches only for games that have none stored
# yet (newest first) plus a rolling slice of the rest, up to MAX_BRANCH_LOOKUPS.
MAX_BRANCH_LOOKUPS = 150
REFRESH_DAYS = 14  # every stored branch list is re-checked about this often
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


def lookups_needed(ids, known, today=None, cap=MAX_BRANCH_LOOKUPS):
    """Which record ids to look branches up for this run (ids are newest first).

    known: {id: stored branch list}. Ids with nothing stored come first; then ids
    whose turn in the REFRESH_DAYS-day rotation it is today.
    """
    day = (today or date.today()).toordinal()
    missing = [i for i in ids if not known.get(i)]
    due = [i for i in ids if known.get(i) and (zlib.crc32(i.encode()) + day) % REFRESH_DAYS == 0]
    return (missing + due)[:cap]


def fetch(slug, library=None, known=None, today=None):
    """All PS5 games at one BiblioCommons library, as Items.

    known: {record id: branches stored by earlier runs}, used to limit branch lookups.
    """
    library, known = library or slug, known or {}
    bibs = {}
    for query in QUERIES:
        for bib in search_bibs(slug, query):
            if is_ps5_game(bib):
                bibs.setdefault(bib["id"], bib)
    todo = lookups_needed(list(bibs), known, today)
    print(f"{slug}: {len(bibs)} PS5 games, looking up branches for {len(todo)}", flush=True)
    with ThreadPoolExecutor(WORKERS) as pool:
        looked_up = dict(zip(todo, pool.map(lambda i: branches_of(slug, i), todo)))
    return [Item(bid, title_of(bib), f"https://{slug}.bibliocommons.com/v2/record/{bid}",
                 library=library, branches=looked_up.get(bid) or known.get(bid) or [])
            for bid, bib in bibs.items()]
