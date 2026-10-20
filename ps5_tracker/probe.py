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
