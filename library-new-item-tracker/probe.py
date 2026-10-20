"""One-off probe of three BiblioCommons catalogs; delete after the adapter is written."""
import json
import sys
from urllib.parse import quote

from playwright.sync_api import sync_playwright

LIBS = ["sccl", "sunnyvale", "sclibrary"]


def short(o, n=3500):
    s = o if isinstance(o, str) else json.dumps(o)
    return s[:n]


def main():
    with sync_playwright() as p:
        b = p.chromium.launch()
        for slug in LIBS:
            print(f"\n\n################ {slug}")
            page = b.new_page(viewport={"width": 1280, "height": 1600})
            seen = []

            def on_resp(r, seen=seen):
                if "gateway.bibliocommons.com" in r.url or "bibliocommons.com/api" in r.url:
                    try:
                        seen.append((r.request.method, r.url, r.status, r.text()[:400000]))
                    except Exception:
                        seen.append((r.request.method, r.url, r.status, ""))

            page.on("response", on_resp)
            url = f"https://{slug}.bibliocommons.com/v2/search?query={quote('playstation 5')}&searchType=smart"
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(2500)
            except Exception as e:
                print("goto failed:", e)
            print("final url:", page.url, "| title:", page.title())
            print("-- api requests:")
            for m, u, st, body in seen:
                print(" ", m, st, u[:260])
            for m, u, st, body in seen:
                if "/bibs/search" in u or "/search" in u and "bibs" in u:
                    print("\n-- SEARCH RESPONSE", u[:300])
                    try:
                        j = json.loads(body)
                    except Exception:
                        print(short(body)); continue
                    print("top keys:", list(j)[:12])
                    ents = (j.get("entities") or {}).get("bibs") or {}
                    print("n bibs in entities:", len(ents))
                    for k, v in list(ents.items())[:2]:
                        print("BIB", k, short(v, 2500))
                    print("catalogSearch keys:", short(list((j.get("catalogSearch") or {}).keys()), 600))
                    cs = j.get("catalogSearch") or {}
                    for key in ("facets", "pagination", "sorts", "sortOptions"):
                        if key in cs:
                            print(key, short(cs[key], 3000))
                    break
            print("-- page text (first 2500 chars):")
            print(page.inner_text("body")[:2500])
            page.close()
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
