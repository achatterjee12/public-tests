"""One-off probe (round 3); delete after the adapter is written."""
import json
import sys
from urllib.parse import quote

from playwright.sync_api import sync_playwright

GW = "https://gateway.bibliocommons.com/v2/libraries/{slug}"


def short(o, n=2500):
    s = o if isinstance(o, str) else json.dumps(o)
    return s[:n]


def gw(page, url):
    return page.evaluate("""async (u) => {
        const r = await fetch(u, {headers: {accept: 'application/json'}});
        const t = await r.text();
        return {status: r.status, body: t.slice(0, 900000)};
    }""", url)


def js(res):
    try:
        return json.loads(res["body"])
    except Exception:
        return None


def titles(j):
    ents = (j.get("entities") or {}).get("bibs") or {}
    out = []
    for r in (j.get("catalogSearch") or {}).get("results", []):
        b = ents.get(r.get("representative"), {}).get("briefInfo", {})
        out.append(f"{b.get('title')} [{b.get('format')}|{b.get('edition')}|{b.get('callNumber')}]")
    return out


def search(page, slug, **params):
    qs = "&".join(f"{k}={quote(str(v))}" for k, v in params.items())
    res = gw(page, f"{GW.format(slug=slug)}/bibs/search?{qs}&locale=en-US")
    j = js(res)
    if not j or "catalogSearch" not in j:
        return res["status"], None, short(res["body"], 300)
    return res["status"], j, (j["catalogSearch"].get("pagination") or {})


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        for slug in ["sunnyvale", "sccl"]:
            print(f"\n\n################ {slug}")
            page = b.new_page()
            page.goto(f"https://{slug}.bibliocommons.com/", wait_until="networkidle", timeout=60000)

            if slug == "sunnyvale":
                for q in ["ps5", "playstation", "playstation 5", "video game", "video games",
                          "nintendo switch", "xbox", "PS5 game", "game"]:
                    st, j, info = search(page, slug, query=q, searchType="smart", limit=10)
                    print(f"query={q!r}: HTTP {st} pagination={info if j else info}")
                    if j:
                        for t in titles(j)[:6]:
                            print("    ", t)
                st, j, info = search(page, slug, query="*", searchType="smart", limit=5,
                                     **{"f_FORMAT": "VIDEO_GAME"})
                print("format filter VIDEO_GAME ->", st, info)
                if j:
                    for t in titles(j)[:5]:
                        print("    ", t)
            else:
                # page size and format filter
                for lim in (25, 50, 100):
                    st, j, info = search(page, slug, query="playstation 5", searchType="smart", limit=lim)
                    print(f"limit={lim}: HTTP {st} {info if j else info} n={len((j or {}).get('catalogSearch',{}).get('results',[]))}")
                for key in ("f_FORMAT", "f_format", "formats"):
                    st, j, info = search(page, slug, query="playstation 5", searchType="smart",
                                         limit=5, **{key: "VIDEO_GAME"})
                    print(f"filter {key}=VIDEO_GAME: HTTP {st} {info}")
                    if j:
                        print("   ", titles(j)[:5])
                # how many pages of PS5 video games in total via plain scan
                st, j, info = search(page, slug, query="ps5", searchType="smart", limit=100,
                                     sort="newly_acquired")
                print("query=ps5 newly_acquired limit=100:", st, info)
                if j:
                    ts = titles(j)
                    print("   n", len(ts), "first 12:", ts[:12])
                    print("   non-VIDEO_GAME in page:", [t for t in ts if "VIDEO_GAME" not in t][:6])
                # branch-level availability for the first PS5 result
                st, j, info = search(page, slug, query="playstation 5", searchType="smart", limit=3)
                first = (j["catalogSearch"]["results"][0]["representative"]) if j else None
                print("first bib id:", first)
                for path in (f"/bibs/{first}/availability", f"/bibs/{first}/availability?locale=en-US",
                             f"/availability/{first}", f"/bibs/{first}"):
                    res = gw(page, GW.format(slug=slug) + path)
                    print(f"\n-- GET {path}: HTTP {res['status']}")
                    print(short(res["body"], 3500))
                # sortBys list
                res = gw(page, f"{GW.format(slug=slug)}/bibs/search?query=ps5&searchType=smart&limit=1&locale=en-US")
                jj = js(res) or {}
                print("\nsortBys:", short((jj.get("catalogSearch") or {}).get("sortBys"), 1500))
            page.close()
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
