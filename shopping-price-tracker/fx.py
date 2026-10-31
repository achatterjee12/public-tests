"""Exchange rates to US dollars, for items priced in other currencies.

Primary source: Google Finance's public quote page (https://www.google.com/finance/quote/INR-USD).
It has no official API, so the rate is read from the page; if Google changes the page or
refuses the request, the European Central Bank's daily reference rates (via the free
frankfurter.app API) are used instead. Each rate used is saved in the fx_rates table with
its source and time, and if both sources fail, the latest saved rate (up to 7 days old) is used.
"""
import re
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


def parse_google(html):
    """The rate from a Google Finance currency quote page, or None."""
    for pattern in (r'data-last-price="([0-9.eE+-]+)"',            # attribute on the quote block
                    r'class="YMlKec fxKbKc"[^>]*>\s*([0-9][0-9.,]*)\s*<'):  # the big number shown
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
    status, _, text, err = spike.fetch(GOOGLE_URL.format(cur=cur))
    return parse_google(text) if status == 200 and not err else None


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
