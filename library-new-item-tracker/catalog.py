"""Fetcher for the Mountain View Public Library catalog (Innovative Vega Discover).

The public catalog is a single-page app backed by an anonymous JSON API on
na5.iiivega.com. Findings from probing it:
  * results carry no per-item "date added", but the API supports
    sorting="catalogDate" and a catalogDate filter limited to
    week | month | quarter | year;
  * game records have materialTypes ["m"], edition "PlayStation 5." and call
    numbers like "GAME PS5 <title>".
So we fetch every PS5 game and let tracker.py diff against its database.
"""
import json
import re
import urllib.request
import uuid

from tracker import Item

API = "https://na5.iiivega.com/api/search-result/search/format-groups"
SITE = "https://librarycatalog.mountainview.gov"
PAGE_SIZE = 50
GAME_TYPE = "m"
PS5 = re.compile(r"playstation ?5|\bps ?5\b", re.I)


def _post(body, anon_id):
    req = urllib.request.Request(API, json.dumps(body).encode(), {
        "content-type": "application/json",
        "accept": "application/json",
        "api-version": "2",
        "iii-customer-domain": "mvpl.na5.iiivega.com",
        "iii-host-domain": "librarycatalog.mountainview.gov",
        "anonymous-user-id": anon_id,
        "user-agent": "Mozilla/5.0 (ps5-tracker)",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def is_ps5_game(rec):
    for tab in rec.get("materialTabs", []):
        if GAME_TYPE not in tab.get("materialTypes", []):
            continue
        texts = [tab.get("callNumber", "")] + [
            f"{e.get('edition', '')} {e.get('callNumber', '')}"
            for e in tab.get("editions", [])]
        if any(PS5.search(t or "") for t in texts):
            return True
    return False


def fetch_ps5_items(catalog_date=None):
    """All PS5 games in the catalog, newest-added first.

    catalog_date: optional server-side window (week|month|quarter|year).
    """
    anon = str(uuid.uuid4())
    items, page = [], 0
    while True:
        body = {"searchText": "playstation 5", "searchType": "everything",
                "sorting": "catalogDate", "sortOrder": "desc",
                "pageNum": page, "pageSize": PAGE_SIZE,
                "resourceType": "FormatGroup"}
        if catalog_date:
            body["catalogDate"] = catalog_date
        res = _post(body, anon)
        for rec in res["data"]:
            if is_ps5_game(rec):
                items.append(Item(rec["id"], rec["title"].strip(),
                                  f"{SITE}/search/card?id={rec['id']}"))
        page += 1
        if page >= res["totalPages"]:
            return items


if __name__ == "__main__":
    # Diagnostic: python catalog.py
    for window in (None, "week", "month", "quarter", "year"):
        got = fetch_ps5_items(window)
        print(f"catalogDate={window}: {len(got)} PS5 games; newest: "
              f"{[i.title for i in got[:5]]}")
