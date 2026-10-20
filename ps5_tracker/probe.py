"""One-off probe: load the Vega Discover catalog in a real browser and log
what the page requests, so the real fetcher can be written against it.
Prints a summary to stdout (visible in the Actions log) and writes full
captures to probe_out/ (uploaded as an artifact)."""
import json
import os
import sys
from urllib.parse import quote

from playwright.sync_api import sync_playwright

BASE = "https://librarycatalog.mountainview.gov"
QUERIES = {
    "search": f"{BASE}/search?query={quote('playstation 5')}&searchType=everything&pageSize=20",
}
OUT = "probe_out"
os.makedirs(OUT, exist_ok=True)


API = "https://na5.iiivega.com/api/search-result/search/format-groups"


def show(title, obj, limit=6000):
    print(f"\n##### {title}")
    print((obj if isinstance(obj, str) else json.dumps(obj, indent=1))[:limit])


def deep_probe(page, seen):
    base = next((e for e in seen if "/search/format-groups" in e["url"]
                 and e.get("req_headers")), None)
    if not base:
        print("!! no search POST captured")
        return
    drop = {"content-length", "host", "cookie"}
    headers = {k: v for k, v in base["req_headers"].items() if k.lower() not in drop}
    show("search request headers", headers)
    body = json.loads(base["post_data"])

    def call(label, **changes):
        b = {**body, **changes}
        r = page.request.post(API, data=json.dumps(b), headers=headers)
        try:
            j = r.json()
        except Exception:
            show(f"{label}: HTTP {r.status}", r.text()[:800])
            return None
        if r.status >= 400:
            show(f"{label}: HTTP {r.status}", j, 800)
            return None
        print(f"\n##### {label}: HTTP {r.status} totalResults={j.get('totalResults')} "
              f"keys={list(j)[:8]}")
        return j

    j = call("baseline")
    if j and j.get("data"):
        show("FULL first result item", j["data"][0], 9000)
        show("all key names across first item (recursive)", sorted(
            {k for k in json.dumps(j["data"][0]).replace('"', ' ').split()
             if k.endswith(":")}))
    # sort / date-filter experiments
    for srt in ("catalogDate", "newest", "dateAdded", "publicationDate", "title"):
        r = call(f"sorting={srt}", sorting=srt, sortOrder="desc", pageSize=3)
        if r and r.get("data"):
            print([d.get("title") for d in r["data"]])
    for cd in ("last_24_hours", "1", 1, "24h", "day", "LAST_DAY", "week"):
        call(f"catalogDate={cd!r}", catalogDate=cd, pageSize=3)

    # drive the real UI filter and capture what it sends
    page.evaluate("document.querySelector('#onetrust-consent-sdk')?.remove()")
    n = len(seen)
    for label in ("Recently Added", "Format"):
        try:
            page.get_by_text(label, exact=True).first.click(timeout=5000)
            page.wait_for_timeout(2500)
            print(f"\n##### after clicking {label!r}; page text near it:")
            print(page.inner_text("body")[:2500])
            for e in seen[n:]:
                if e.get("post_data"):
                    show(f"UI request after {label}: {e['url']}", e["post_data"], 1500)
            n = len(seen)
            opts = page.locator("app-facet-block, .facet-block, [class*=facet]").filter(
                has_text=label).locator("label, li, button, a, input[type=checkbox]")
            print(f"{label}: {opts.count()} clickable options:",
                  [opts.nth(i).inner_text()[:40] for i in range(min(opts.count(), 12))])
            for i in range(min(opts.count(), 12)):
                txt = opts.nth(i).inner_text().strip()
                if label == "Format" and not txt.startswith("Game"):
                    continue
                if label == "Recently Added" and i > 3:
                    break
                try:
                    opts.nth(i).click(timeout=4000)
                    page.wait_for_timeout(2500)
                except Exception as ex:
                    print("option click failed", txt, str(ex)[:120]); continue
                for e in seen[n:]:
                    if e.get("post_data") and "format-groups" in e["url"] and "search/" in e["url"]:
                        show(f"UI search request after {label}={txt!r}", e["post_data"], 1500)
                n = len(seen)
                if label == "Format":
                    break
        except Exception as ex:
            print(f"click {label} failed: {ex}")


def main():
    seen = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 1800})

        def on_response(resp):
            ctype = resp.headers.get("content-type", "")
            entry = {"url": resp.url, "status": resp.status, "type": ctype,
                     "method": resp.request.method}
            if "json" in ctype:
                try:
                    entry["body"] = resp.text()[:200_000]
                    entry["post_data"] = resp.request.post_data
                    if "/search/format-groups" in resp.url:
                        entry["req_headers"] = dict(resp.request.headers)
                except Exception as e:  # body may be unavailable
                    entry["body_error"] = str(e)
            seen.append(entry)

        page.on("response", on_response)
        for name, url in QUERIES.items():
            print(f"== loading {url}")
            page.goto(url, wait_until="networkidle", timeout=60_000)
            page.wait_for_timeout(3000)
            page.screenshot(path=f"{OUT}/{name}.png", full_page=True)
            with open(f"{OUT}/{name}.txt", "w") as f:
                f.write(page.inner_text("body"))
            with open(f"{OUT}/{name}.html", "w") as f:
                f.write(page.content())
            print(f"final url: {page.url}\ntitle: {page.title()}")
            print("-- page text (first 3000 chars) --")
            print(page.inner_text("body")[:3000])
        deep_probe(page, seen)
        browser.close()

    with open(f"{OUT}/requests.json", "w") as f:
        json.dump(seen, f, indent=1)
    print("\n== requests (non-static) ==")
    for e in seen:
        if any(x in e["type"] for x in ("image", "font", "css", "javascript")):
            continue
        print(e["method"], e["status"], e["type"].split(";")[0], e["url"][:200])
    print("\n== JSON bodies (first 1500 chars each) ==")
    for e in seen:
        if "body" in e:
            print(f"--- {e['method']} {e['url'][:200]}")
            if e.get("post_data"):
                print("POST DATA:", e["post_data"][:1000])
            print(e["body"][:1500])
    return 0


if __name__ == "__main__":
    sys.exit(main())
