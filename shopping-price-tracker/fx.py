"""Exchange rates to US dollars, for items priced in other currencies.

Primary source: Google Finance's public quote page (https://www.google.com/finance/quote/INR-USD).
It has no official API, so the rate is read from the page; if Google changes the page or
refuses the request, the European Central Bank's daily reference rates (via the free
frankfurter.app API) are used instead. Each rate used is saved in the fx_rates table with
its source and time, and if both sources fail, the latest saved rate (up to 7 days old) is used.
"""
import re
import sys
from datetime import datetime, timedelta, timezone

import spike

GOOGLE_URL = "https://www.google.com/finance/quote/{cur}-USD?hl=en"
ECB_URL = "https://api.frankfurter.app/latest?from={cur}&to=USD"
MAX_STALE = timedelta(days=7)

SCHEMA = """
CREATE TABLE IF NOT EXISTS fx_rates (
  id INTEGER PRIMARY KEY, currency TEXT NOT NULL, rate_to_usd REAL NOT NULL,
  source TEXT NOT NULL, fetched_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS fx_currency_time ON fx_rates(currency, fetched_at);
"""


def parse_google(html, cur=None):
    """The rate from a Google Finance currency quote page, or None.

    Tried in order (Google's markup changes now and then, so there are several):
      1. the page's embedded data: ..."INR / USD",3,null,[0.01053602, ...]  (full precision)
      2. older pages: data-last-price="0.0105" attribute
      3. the number shown on screen: <span jsname="Pdsbrc"><span>0.0105</span>  (4 digits)
      4. older pages: <div class="YMlKec fxKbKc">0.0105</div>
    """
    pair = re.escape(f"{cur} / USD") if cur else r"[A-Z]{3} / USD"
    patterns = [rf'"{pair}",\d+,null,\[([0-9.eE+-]+),',
                r'data-last-price="([0-9.eE+-]+)"',
                r'jsname="Pdsbrc"[^>]*>\s*<span>\s*([0-9][0-9.,]*)\s*</span>',
                r'class="YMlKec fxKbKc"[^>]*>\s*([0-9][0-9.,]*)\s*<']
    for pattern in patterns:
        m = re.search(pattern, html)
        if m:
            try:
                rate = float(m.group(1).replace(",", ""))
            except ValueError:
                continue
            if rate > 0:
                return rate
    return None


def from_google(cur):
    url = GOOGLE_URL.format(cur=cur)
    status, final_url, text, err = spike.fetch(url)
    rate = parse_google(text, cur) if status == 200 and not err else None
    if rate is None:  # say why in the run log, so a Google page change is easy to spot
        title = re.search(r"<title>(.*?)</title>", text or "", re.S)
        print(f"Google Finance gave no {cur} rate: status={status} error={err} final={final_url} "
              f"bytes={len(text or '')} title={title.group(1).strip()[:80] if title else None!r}", file=sys.stderr)
    return rate


def from_ecb(cur):
    import json
    status, _, text, err = spike.fetch(ECB_URL.format(cur=cur))
    if status != 200 or err:
        return None
    try:
        rate = json.loads(text)["rates"]["USD"]
        return float(rate) if rate > 0 else None
    except (ValueError, KeyError, TypeError):
        return None


class Rates:
    """Fetches each currency's rate at most once per run and records it in the database."""

    def __init__(self, db, fetchers=None):
        self.db = db
        self.db.executescript(SCHEMA)
        self.fetchers = fetchers or [("Google Finance", from_google), ("European Central Bank", from_ecb)]
        self.cache = {}

    def get(self, cur):
        """Returns {"rate", "source", "as_of"} for 1 unit of cur in USD, or None."""
        cur = (cur or "USD").upper()
        if cur == "USD":
            return {"rate": 1.0, "source": None, "as_of": None}
        if cur in self.cache:
            return self.cache[cur]
        found = None
        for source, fetch in self.fetchers:
            rate = fetch(cur)
            if rate:
                now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
                self.db.execute("INSERT INTO fx_rates(currency, rate_to_usd, source, fetched_at) VALUES (?,?,?,?)",
                                (cur, rate, source, now))
                self.db.commit()
                found = {"rate": rate, "source": source, "as_of": now}
                break
        if not found:
            found = latest(self.db, cur, max_age=MAX_STALE)
        self.cache[cur] = found
        return found


def latest(db, cur, max_age=None):
    row = db.execute("SELECT * FROM fx_rates WHERE currency=? ORDER BY fetched_at DESC LIMIT 1",
                     (cur,)).fetchone()
    if not row:
        return None
    if max_age and datetime.fromisoformat(row["fetched_at"]) < datetime.now(timezone.utc) - max_age:
        return None
    return {"rate": row["rate_to_usd"], "source": row["source"] + " (saved)", "as_of": row["fetched_at"]}


def debug_google(cur, near):
    """Prints the page markup around numbers close to the expected rate (to update the parser)."""
    _, _, text, _ = spike.fetch(GOOGLE_URL.format(cur=cur))
    print(f"--- {cur}: data-last-price={'data-last-price' in text} YMlKec={text.count('YMlKec')}")
    lead = f"{near:.6f}"[:5]      # e.g. 0.010 for 0.010532
    for i, m in enumerate(re.finditer(re.escape(lead) + r"\d*", text)):
        if i >= 8:
            break
        print("   ...", text[max(0, m.start() - 160):m.end() + 60].replace("\n", " "), "...")


if __name__ == "__main__":  # diagnostics: python fx.py INR SEK
    for c in sys.argv[1:] or ["INR"]:
        g, e = from_google(c), from_ecb(c)
        print(c, "google:", g, "ecb:", e)
        if g is None and e:
            debug_google(c, e)
