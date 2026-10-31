"""Offline tests for currency conversion (no network: fetchers are faked)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import fx
import tracker


class TestParseGoogle(unittest.TestCase):
    # Markup as served to GitHub Actions on 2026-10-31.
    def test_embedded_data(self):
        html = ('<span jsname="Pdsbrc" class=""><span>0.0105</span></span>...'
                'AF_initDataCallback({key: \'ds:2\', hash: \'7\', data:[[[["/g/11bvvzslq9",null,"INR / USD",3,null,'
                '[0.01053602,5.0E-7,0.00474585,4,4,4],null,0.01053552')
        self.assertEqual(fx.parse_google(html, "INR"), 0.01053602)
        self.assertIsNone(fx.parse_google(html.replace("INR / USD", "SEK / USD").replace("Pdsbrc", "x"), "INR"))

    def test_on_screen_number(self):
        self.assertEqual(fx.parse_google('<div class="N6SYTe"><span jsname="Pdsbrc" class=""><span>0.1018</span></span>', "SEK"), 0.1018)

    def test_attribute(self):
        html = '<div jsname="ip75Cb" data-source="INR" data-target="USD" data-last-price="0.011853" data-tz="">'
        self.assertEqual(fx.parse_google(html), 0.011853)

    def test_visible_number(self):
        html = '<div class="YMlKec fxKbKc">0.094317</div>'
        self.assertEqual(fx.parse_google(html), 0.094317)

    def test_missing(self):
        self.assertIsNone(fx.parse_google("<html>Before you continue to Google</html>"))


class TestRates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = tracker.connect(os.path.join(self.tmp.name, "t.db"))
        self.calls = []

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def fetcher(self, name, value):
        def f(cur):
            self.calls.append((name, cur))
            return value
        return (name, f)

    def test_usd_is_identity(self):
        self.assertEqual(fx.Rates(self.db, []).get("USD")["rate"], 1.0)

    def test_google_first_then_cached(self):
        r = fx.Rates(self.db, [self.fetcher("Google Finance", 0.012), self.fetcher("ECB", 0.5)])
        self.assertEqual(r.get("INR")["source"], "Google Finance")
        r.get("inr")
        self.assertEqual(self.calls, [("Google Finance", "INR")])

    def test_fallback_to_ecb_then_saved(self):
        r = fx.Rates(self.db, [self.fetcher("Google Finance", None), self.fetcher("European Central Bank", 0.094)])
        self.assertEqual(r.get("SEK"), {"rate": 0.094, "source": "European Central Bank", "as_of": r.get("SEK")["as_of"]})
        offline = fx.Rates(self.db, [self.fetcher("Google Finance", None)])
        self.assertEqual(offline.get("SEK")["source"], "European Central Bank (saved)")
        self.assertIsNone(offline.get("EUR"))

    def test_saved_rate_too_old(self):
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        self.db.execute("INSERT INTO fx_rates(currency, rate_to_usd, source, fetched_at) VALUES ('GBP', 1.3, 'x', ?)", (old,))
        self.assertIsNone(fx.Rates(self.db, [self.fetcher("g", None)]).get("GBP"))


class TestConvertedExport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = tracker.connect(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def res(self, price, cur):
        return {"ok": True, "price": price, "currency": cur, "availability": "in_stock", "title": "Bag",
                "image": None, "method": "jsonld", "status": 200, "error": None}

    def export(self):
        return tracker.export(self.db, os.path.join(self.tmp.name, "i.json"), os.path.join(self.tmp.name, "h"))

    def test_converted_to_usd(self):
        (iid,) = tracker.sync(self.db, [{"url": "https://a/p", "tags": [], "target": None, "note": ""}], [])
        r = self.res(29990.0, "INR")
        tracker.record(self.db, iid, r, {"rate": 0.012, "source": "Google Finance"})
        self.assertAlmostEqual(r["usd"], 359.88)
        item = self.export()["items"][0]
        self.assertEqual((item["currency"], item["price"], item["local_currency"], item["local_price"]),
                         ("USD", 35988, "INR", 2999000))
        self.assertEqual(item["fx"]["source"], "Google Finance")

    def test_backfill_older_checks(self):
        (iid,) = tracker.sync(self.db, [{"url": "https://a/p", "tags": [], "target": None, "note": ""}], [])
        tracker.record(self.db, iid, self.res(2995.0, "SEK"))           # before conversion existed
        self.assertEqual(self.export()["items"][0]["currency"], "SEK")  # stays in its own currency
        rates = fx.Rates(self.db, [("Google Finance", lambda cur: 0.1)])
        tracker.backfill(self.db, rates)
        item = self.export()["items"][0]
        self.assertEqual((item["currency"], item["price"]), ("USD", 29950))


if __name__ == "__main__":
    unittest.main()
