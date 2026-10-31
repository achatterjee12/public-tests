"""Add links to items.txt from a "Track items" GitHub issue, check them, and write a reply.

The issue body comes from the "Add items" box on index.html (or is typed by hand):
    https://shop.com/products/a
    https://shop.com/products/b | tags: bags
    tags: shoes, gifts          <- a line on its own: tags for every link in the issue
Any text that isn't a link or a tags line is ignored, so pasted junk is harmless.

Usage (in the workflow): python add_items.py <issue-body-file> <reply-file>
Prints the number of links added. Tracking parameters are stripped; duplicates skipped.
"""
import os
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import tracker

URL_RE = re.compile(r"https?://[^\s|<>\"']+")
# Ad/tracking parameters to remove. Anything else (variant, selectedSellerId, ...) is kept,
# because it can pick which product, colour or seller the price is for.
TRACKING = re.compile(
    r"^(utm_.*|gclid|gclsrc|gbraid|wbraid|gad_.*|dclid|fbclid|msclkid|yclid|ttclid|twclid|"
    r"igsh|igshid|srsltid|_ga|_gl|mc_cid|mc_eid|ref|ref_|referrer|source|sourceid|"
    r"wmlspartner|veh|cn|wl\d+|sid|ds_c|ds_.*|campaign|cmp|cid|clickid|irclickid|irgwc|"
    r"affiliate|aff_.*|psln|rpxt|dlrf|_pos|_sid|_ss|_psq|_fid|pr_.*)$", re.I)


def clean_url(url):
    url = url.rstrip(".,;:)]}")
    p = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    seen, kept = set(), []
    for k, v in query:            # drop repeated keys (Walmart sends wmlspartner twice)
        if k not in seen:
            seen.add(k)
            kept.append((k, v))
    return urlunparse((p.scheme, p.netloc.lower(), p.path, "", urlencode(kept), ""))


def same_item(a, b):
    """Duplicate check that ignores http/https, www., trailing slash and parameter order."""
    def key(u):
        p = urlparse(u)
        return (p.netloc.lower().removeprefix("www."), p.path.rstrip("/"),
                tuple(sorted(parse_qsl(p.query))))
    return key(a) == key(b)


def parse_body(body):
    """Returns (links, batch_tags). Each link: {"url", "tags"} with per-line tags."""
    links, batch_tags = [], []
    for line in body.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        m = URL_RE.search(line)
        if not m:
            key, _, value = line.partition(":")
            if key.strip().lower() in ("tags", "tag"):
                batch_tags += [t.strip().lower() for t in value.split(",") if t.strip()]
            continue
        tags = []
        for part in line[m.end():].split("|"):
            key, _, value = part.partition(":")
            if key.strip().lower() in ("tags", "tag"):
                tags += [t.strip().lower() for t in value.split(",") if t.strip()]
        links.append({"url": clean_url(m.group(0)), "tags": tags})
    return links, batch_tags


def add(body, items_path=tracker.ITEMS_PATH):
    """Appends new links to items.txt. Returns (added, duplicates, invalid)."""
    links, batch_tags = parse_body(body)
    existing = [it["url"] for it in tracker.spike.parse_items(items_path)]
    added, dupes, invalid = [], [], []
    for link in links:
        p = urlparse(link["url"])
        if p.scheme not in ("http", "https") or "." not in p.netloc:
            invalid.append(link["url"])
            continue
        if "instagram.com" in p.netloc or "tiktok.com" in p.netloc:
            invalid.append(link["url"] + " (a social media post, not a shop page)")
            continue
        if any(same_item(link["url"], u) for u in existing + [a["url"] for a in added]):
            dupes.append(link["url"])
            continue
        tags = list(dict.fromkeys(link["tags"] + batch_tags))
        added.append({"url": link["url"], "tags": tags})
    if added:
        text = open(items_path, encoding="utf-8").read()
        new = "".join(a["url"] + (f" | tags: {', '.join(a['tags'])}" if a["tags"] else "") + "\n"
                      for a in added)
        # insert before the commented-out "not trackable" notes at the end, if any
        marker = "\n# Not trackable yet"
        if marker in text:
            head, tail = text.split(marker, 1)
            text = head.rstrip("\n") + "\n" + new + marker + tail
        else:
            text = text.rstrip("\n") + "\n" + new
        with open(items_path, "w", encoding="utf-8") as f:
            f.write(text)
    return added, dupes, invalid


def money(price, currency):
    return f"{price:,.2f} {currency or ''}".strip()


def reply(added, dupes, invalid, results, page_url):
    out = []
    if added:
        out += [f"Added {len(added)} item{'s' if len(added) != 1 else ''}:", "",
                "| Item | Price | Stock | Tags |", "|---|---|---|---|"]
        for a in added:
            r = results.get(a["url"], {})
            name = (r.get("title") or tracker.title_from_url(a["url"])).replace("|", "/")
            if r.get("ok"):
                price = money(r["price"], r.get("currency"))
                if (r.get("currency") or "USD") != "USD" and r.get("usd") is not None:
                    price = f"{money(r['usd'], 'USD')} ({price})"
                stock = (r.get("availability") or "unknown").replace("_", " ")
            else:
                price, stock = "—", f"check failed: {r.get('error') or 'not checked'}"
            out.append(f"| [{name}]({a['url']}) | {price} | {stock} | {', '.join(a['tags']) or '—'} |")
    else:
        out.append("No new items were added.")
    if dupes:
        out += ["", "Already tracked (skipped):", *[f"- {u}" for u in dupes]]
    if invalid:
        out += ["", "Not added:", *[f"- {u}" for u in invalid]]
    out += ["", f"They'll show on the [price tracker page]({page_url}) in a minute or two."]
    return "\n".join(out) + "\n"


def main():
    body = open(sys.argv[1], encoding="utf-8").read()
    added, dupes, invalid = add(body)
    results = tracker.run(only_urls={a["url"] for a in added}) if added else {}
    page = os.environ.get("PAGE_URL", "https://achatterjee12.github.io/public-tests/shopping-price-tracker/")
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        f.write(reply(added, dupes, invalid, results, page))
    print(len(added))


if __name__ == "__main__":
    main()
