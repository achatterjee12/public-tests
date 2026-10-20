"""One-off probe of three BiblioCommons catalogs; delete after the adapter is written."""
import json
import sys
from urllib.parse import quote

from playwright.sync_api import sync_playwright

LIBS = ["sccl", "sunnyvale", "sclibrary"]
GW = "https://gateway.bibliocommons.com/v2/libraries/{slug}/bibs/search"


def short(o, n=3000):
    s = o if isinstance(o, str) else json.dumps(o)
    return s[:n]


def gw(page, url):
    """fetch() from inside the page, like the site's own scripts do."""
    return page.evaluate("""async (u) => {
        const r = await fetch(u, {headers: {accept: 'application/json'}});
        const t = await r.text();
        return {status: r.status, body: t.slice(0, 900000)};
    }""", url)


def describe(label, res):
    print(f"\n-- {label}: HTTP {res['status']}")
    try:
        j = json.loads(res["body"])
    except Exception:
        print(short(res["body"], 600)); return None
    if not isinstance(j, dict):
        print("non-dict:", short(j, 600)); return None
    print("top keys:", list(j)[:12])
    cs = j.get("catalogSearch") or {}
    print("catalogSearch keys:", list(cs)[:15])
    print("pagination:", short(cs.get("pagination"), 400))
    res_list = cs.get("results") or []
    print("n results:", len(res_list), "first:", short(res_list[:2], 400))
    ents = (j.get("entities") or {})
    print("entity types:", {k: len(v) for k, v in ents.items() if isinstance(v, dict)})
    return j


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        for slug in LIBS:
            print(f"\n\n################ {slug}")
            page = b.new_page(viewport={"width": 1280, "height": 1600})
            seen = []
            page.on("response", lambda r, s=seen: s.append((r.request.method, r.status, r.url)))
            q = quote("playstation 5")
            page.goto(f"https://{slug}.bibliocommons.com/v2/search?query={q}&searchType=smart",
                      wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2500)
            print("title:", page.title())
            print("-- XHR to gateway/api:")
            for m, st, u in seen:
                if "gateway.bibliocommons.com" in u or "/api/" in u:
                    print(" ", m, st, u[:230])
            print("-- window state keys:", page.evaluate(
                "Object.keys(window).filter(k => /STATE|state|BIBLIO|initial/i.test(k)).slice(0,15)"))

            base = GW.format(slug=slug)
            j = describe("search smart", gw(page, f"{base}?query={q}&searchType=smart&limit=20&locale=en-US"))
            if j:
                ents = (j.get("entities") or {}).get("bibs") or {}
                for k, v in list(ents.items())[:2]:
                    print("BIB", k, short(v, 3500))
                facets = (j.get("catalogSearch") or {}).get("facets")
                print("FACETS:", short(facets, 3500))
            for srt in ("newly_acquired", "newly_acquired_desc", "acquired_desc", "pubdate_desc"):
                jj = describe(f"sort={srt}", gw(page,
                    f"{base}?query={q}&searchType=smart&limit=5&locale=en-US&sort={srt}"))
                if jj:
                    ents = (jj.get("entities") or {}).get("bibs") or {}
                    print("titles in order:", [
                        (ents.get(r.get("representative") or r.get("id"), {}).get("briefInfo", {}) or {}).get("title")
                        for r in (jj.get("catalogSearch") or {}).get("results", [])])
            print("-- page text (first 1800 chars):")
            print(page.inner_text("body")[:1800])
            page.close()
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
