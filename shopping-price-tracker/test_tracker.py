"""Offline tests for tracker.py (no network: check_item is not called)."""
import json
import os
import tempfile
import unittest

import tracker


class TestTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = tracker.connect(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def items(self, *specs):
        return [{"url": u, "tags": t, "target": None, "note": ""} for u, t in specs]

    def ok(self, price, avail="in_stock"):
        return {"ok": True, "price": price, "currency": "USD", "availability": avail,
                "title": "Thing", "image": "https://img/x.jpg", "method": "jsonld",
                "status": 200, "error": None}

    def test_sync_tags_and_deactivate(self):
        ids = tracker.sync(self.db, self.items(("https://a/p", ["bags"]), ("https://b/q", [])),
                           [("bags", "#111"), ("hats", None)])
        self.assertEqual(len(ids), 2)
        tracker.sync(self.db, self.items(("https://a/p", ["bags", "new tag"])), [])
        active = [r[0] for r in self.db.execute("SELECT url FROM items WHERE active=1")]
        self.assertEqual(active, ["https://a/p"])
        names = {r[0] for r in self.db.execute("SELECT name FROM tags")}
        self.assertEqual(names, {"bags", "hats", "new tag"})

    def test_history_is_append_only_and_exported(self):
        (iid,) = tracker.sync(self.db, self.items(("https://a/p", ["bags"])), [("bags", "#111")])
        tracker.record(self.db, iid, self.ok(100.0))
        tracker.record(self.db, iid, {**self.ok(None), "ok": False, "price": None,
                                      "error": "blocked", "availability": None})
        tracker.record(self.db, iid, self.ok(80.0, "out_of_stock"))
        out = tracker.export(self.db, os.path.join(self.tmp.name, "items.json"),
                             os.path.join(self.tmp.name, "history"))
        item = out["items"][0]
        self.assertEqual((item["price"], item["low"], item["high"], item["avg"]), (8000, 8000, 10000, 9000))
        self.assertEqual(item["availability"], "out_of_stock")
        self.assertEqual(item["checks"], 3)
        self.assertEqual(item["tags"], ["bags"])
        self.assertEqual(out["tags"][0], {"name": "bags", "color": "#111", "count": 1})
        hist = json.load(open(os.path.join(self.tmp.name, "history", f"{iid}.json")))
        self.assertEqual([h["ok"] for h in hist], [True, False, True])
        self.assertIsNone(hist[1]["price"])  # failed check kept, never stored as 0

    def test_no_checks_yet(self):
        tracker.sync(self.db, self.items(("https://a/p", [])), [])
        item = tracker.export(self.db, os.path.join(self.tmp.name, "i.json"),
                              os.path.join(self.tmp.name, "h"))["items"][0]
        self.assertIsNone(item["price"])
        self.assertIsNone(item["last_checked"])

    def test_title_from_url(self):
        self.assertEqual(tracker.title_from_url("https://www.walmart.com/ip/4-Pack-Overnight-Oats/20227520315"),
                         "4 Pack Overnight Oats")
        self.assertEqual(tracker.title_from_url("https://x.com/products/the-midi-stockholm"), "The midi stockholm")
        self.assertEqual(tracker.title_from_url("https://www.shop.com/"), "shop.com")


if __name__ == "__main__":
    unittest.main()
