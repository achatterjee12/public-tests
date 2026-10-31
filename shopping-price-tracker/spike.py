"""Phase 0 spike: can we read price + availability for each link in items.txt without AI?

For every item it downloads the page and tries each extraction method separately, so we
can see which ones work per shop:
  jsonld     schema.org Product/Offer in <script type="application/ld+json"> ("Google Shopping" data)
  microdata  schema.org itemprop="price" / itemprop="availability" attributes
  meta       product:price:amount / og:price:amount / product:availability meta tags
  shopify    Shopify's public /products/<handle>.js endpoint (only for /products/ URLs)

Prints a Markdown report (also appended to $GITHUB_STEP_SUMMARY when set) and writes
spike_results.json. Nothing is saved to a database; this only answers "what works".

Usage: python spike.py [items.txt]
"""
import json
import os
import re
import sys
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse, urlunparse

try:  # curl_cffi impersonates Chrome's TLS fingerprint, which many shops require
    from curl_cffi import requests as http
    IMPERSONATE = {"impersonate": "chrome"}
except ImportError:  # plain requests as a fallback (local runs)
    import requests as http
    IMPERSONATE = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
BLOCK_MARKERS = ("robot or human", "px-captcha", "captcha", "access denied",
                 "are you a robot", "unusual traffic", "pardon our interruption")


# ---------- items.txt ----------

def parse_items(path):
    items = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        url, *rest = [p.strip() for p in line.split("|")]
        item = {"url": url, "tags": [], "target": None, "note": ""}
        for part in rest:
            key, _, value = part.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "tags":
                item["tags"] = [t.strip().lower() for t in value.split(",") if t.strip()]
            elif key == "target":
                item["target"] = float(value)
            elif key == "note":
                item["note"] = value
        items.append(item)
    return items


# ---------- normalising ----------

AVAILABILITY = {
    "instock": "in_stock", "instoreonly": "in_stock", "onlineonly": "in_stock",
    "limitedavailability": "limited",
    "outofstock": "out_of_stock", "soldout": "out_of_stock", "discontinued": "out_of_stock",
    "preorder": "preorder", "presale": "preorder", "backorder": "preorder",
}


def norm_availability(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "in_stock" if value else "out_of_stock"
    key = re.sub(r"[^a-z]", "", str(value).rsplit("/", 1)[-1].lower())
    return AVAILABILITY.get(key, "unknown")


def norm_price(value):
    """'$1,299.00', '1299', 1299.0 -> 1299.0; None if not a price."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d.,]", "", str(value))
    if not s:
        return None
    if "," in s and "." in s:          # 1,299.00 or 1.299,00
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:                      # 1,299 (thousands) or 12,99 (decimal)
        s = s.replace(",", "") if re.search(r",\d{3}$", s) else s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def result(method, price=None, currency=None, availability=None, title=None, detail=""):
    return {"method": method, "price": price, "currency": currency,
            "availability": availability, "title": title, "detail": detail,
            "ok": price is not None}


# ---------- HTML scanning ----------

class Scanner(HTMLParser):
    """Collects JSON-LD script bodies, meta tags and microdata price/availability."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.jsonld, self.meta, self.micro = [], {}, {}
        self._in_jsonld = False
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "script" and a.get("type", "").lower().strip() == "application/ld+json":
            self._in_jsonld, self._buf = True, []
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a:
                self.meta.setdefault(key, a["content"])
        prop = a.get("itemprop", "").lower()
        if prop in ("price", "pricecurrency", "availability", "name") and prop not in self.micro:
            val = a.get("content") or a.get("href") or a.get("value")
            if val:
                self.micro[prop] = val

    def handle_data(self, data):
        if self._in_jsonld:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._buf))
            self._in_jsonld = False


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def _types(node):
    t = node.get("@type", [])
    return {x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)}


def _offer_price(offer):
    for key in ("price", "lowPrice"):
        p = norm_price(offer.get(key))
        if p is not None:
            return p, offer.get("priceCurrency")
    spec = offer.get("priceSpecification")
    for s in (spec if isinstance(spec, list) else [spec] if spec else []):
        if isinstance(s, dict) and norm_price(s.get("price")) is not None:
            return norm_price(s.get("price")), s.get("priceCurrency") or offer.get("priceCurrency")
    return None, None


def extract_jsonld(scanner, url):
    blocks = []
    for raw in scanner.jsonld:
        try:
            blocks.append(json.loads(raw.strip()))
        except ValueError:
            try:  # some shops leave control characters/newlines inside strings
                blocks.append(json.loads(re.sub(r"[\x00-\x1f]", " ", raw)))
            except ValueError:
                continue
    products = [n for b in blocks for n in _walk(b) if _types(n) & {"product", "productgroup"}]
    if not products:
        return result("jsonld", detail=f"{len(scanner.jsonld)} JSON-LD blocks, no Product")
    variant_id = parse_qs(urlparse(url).query).get("variant", [None])[0]
    candidates = []  # (score, offer, product)
    for prod in products:
        offers = prod.get("offers") or []
        offers = offers if isinstance(offers, list) else [offers]
        for v in prod.get("hasVariant") or []:
            if isinstance(v, dict):
                vo = v.get("offers") or []
                offers += [dict(o, _variant=v) for o in (vo if isinstance(vo, list) else [vo])]
        for o in offers:
            if not isinstance(o, dict):
                continue
            score = 0
            if variant_id and variant_id in json.dumps(o):
                score += 10      # offer for the variant in the URL
            if norm_availability(o.get("availability")) == "in_stock":
                score += 1
            candidates.append((score, o, prod))
    priced = [c for c in candidates if _offer_price(c[1])[0] is not None]
    if not priced:
        return result("jsonld", title=products[0].get("name"),
                      detail=f"Product found, {len(candidates)} offers, none priced")
    priced.sort(key=lambda c: -c[0])
    _, offer, prod = priced[0]
    price, currency = _offer_price(offer)
    avail = norm_availability(offer.get("availability"))
    prices = sorted({_offer_price(c[1])[0] for c in priced})
    detail = f"{len(priced)} priced offers" + (f", range {prices[0]}–{prices[-1]}" if len(prices) > 1 else "")
    return result("jsonld", price, currency, avail, prod.get("name"), detail)


def extract_microdata(scanner):
    m = scanner.micro
    return result("microdata", norm_price(m.get("price")), m.get("pricecurrency"),
                  norm_availability(m.get("availability")), m.get("name"),
                  "" if m else "no itemprop price")


def extract_meta(scanner):
    m = scanner.meta
    price = norm_price(m.get("product:price:amount") or m.get("og:price:amount"))
    currency = m.get("product:price:currency") or m.get("og:price:currency")
    avail = norm_availability(m.get("product:availability") or m.get("og:availability"))
    return result("meta", price, currency, avail, m.get("og:title"),
                  "" if price is not None else "no product price meta tags")


# ---------- fetching ----------

def fetch(url):
    try:
        r = http.get(url, headers=HEADERS, timeout=30, allow_redirects=True, **IMPERSONATE)
        return r.status_code, str(r.url), r.text, None
    except Exception as e:  # network errors are results here, not crashes
        return None, url, "", f"{type(e).__name__}: {e}"[:200]


def looks_blocked(status, text):
    if status in (403, 429, 503):
        return True
    head = text[:20000].lower()
    return len(text) < 50000 and any(m in head for m in BLOCK_MARKERS)


def extract_shopify(url):
    p = urlparse(url)
    m = re.search(r"/products/([^/?#]+)", p.path)
    if not m:
        return None
    js_url = urlunparse((p.scheme, p.netloc, f"/products/{m.group(1)}.js", "", "", ""))
    status, _, text, err = fetch(js_url)
    if err or status != 200:
        return result("shopify", detail=f"{js_url} -> {err or status}")
    try:
        data = json.loads(text)
    except ValueError:
        return result("shopify", detail="not JSON (probably not a Shopify store)")
    variants = data.get("variants") or []
    want = parse_qs(p.query).get("variant", [None])[0]
    v = next((x for x in variants if str(x.get("id")) == want), None) \
        or next((x for x in variants if x.get("available")), None) \
        or (variants[0] if variants else {})
    cents = v.get("price", data.get("price"))
    price = cents / 100 if isinstance(cents, (int, float)) else norm_price(cents)
    avail = norm_availability(v.get("available", data.get("available")))
    detail = f"{len(variants)} variants, {sum(1 for x in variants if x.get('available'))} available"
    return result("shopify", price, None, avail, data.get("title"), detail)


# ---------- report ----------

def check(item):
    url = item["url"]
    status, final_url, text, err = fetch(url)
    row = {"url": url, "tags": item["tags"], "status": status, "final_url": final_url,
           "bytes": len(text), "error": err, "blocked": False, "methods": []}
    if err:
        return row
    row["blocked"] = looks_blocked(status, text)
    sc = Scanner()
    try:
        sc.feed(text)
    except Exception as e:
        row["error"] = f"HTML parse: {e}"
    row["methods"] = [extract_jsonld(sc, url), extract_microdata(sc), extract_meta(sc)]
    shop = extract_shopify(url)
    if shop:
        row["methods"].append(shop)
    return row


def fmt(r):
    if not r["ok"]:
        return "—"
    s = f"{r['price']:.2f}" + (f" {r['currency']}" if r["currency"] else "")
    return s + (f" / {r['availability']}" if r["availability"] else "")


def report(rows):
    names = ["jsonld", "microdata", "meta", "shopify"]
    out = ["## Price tracker spike", "",
           "| # | Item | HTTP | Blocked? | " + " | ".join(names) + " |",
           "|---|---|---|---|" + "---|" * len(names)]
    for i, row in enumerate(rows, 1):
        by = {m["method"]: m for m in row["methods"]}
        title = next((m["title"] for m in row["methods"] if m.get("title")), "") or ""
        host = urlparse(row["url"]).netloc.replace("www.", "")
        label = f"{host}: {title[:50]}" if title else host
        cells = [fmt(by[n]) if n in by else "n/a" for n in names]
        status = row["status"] if row["status"] is not None else f"error: {row['error']}"
        out.append(f"| {i} | {label} | {status} | {'yes' if row['blocked'] else 'no'} | "
                   + " | ".join(cells) + " |")
    ok = sum(1 for r in rows if any(m["ok"] for m in r["methods"]))
    out += ["", f"**{ok} of {len(rows)} items** gave a price with at least one method.", "",
            "<details><summary>Details per method</summary>", ""]
    for i, row in enumerate(rows, 1):
        out.append(f"- **{i}.** {row['url']} (final: {row['final_url']}, {row['bytes']} bytes)")
        for m in row["methods"]:
            out.append(f"  - {m['method']}: {fmt(m)} {('— ' + m['detail']) if m['detail'] else ''}")
    out += ["", "</details>"]
    return "\n".join(out)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__) or ".", "items.txt")
    rows = [check(item) for item in parse_items(path)]
    text = report(rows)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text + "\n")
    with open("spike_results.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


if __name__ == "__main__":
    main()
