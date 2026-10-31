"""Offline tests for add_items.py (no network)."""
import os
import tempfile
import unittest

import add_items


class TestCleanUrl(unittest.TestCase):
    def test_strips_tracking_keeps_product_params(self):
        url = ("https://www.walmart.com/ip/Jars/20227520315?wmlspartner=wlpa&selectedSellerId=103054767"
               "&sourceid=dsn&veh=dsn&wmlspartner=x&wl0=&wl12=abc&gclsrc=aw.ds&gad_source=1&gbraid=z&sid=c3")
        self.assertEqual(add_items.clean_url(url),
                         "https://www.walmart.com/ip/Jars/20227520315?selectedSellerId=103054767")
        self.assertEqual(add_items.clean_url("https://a.com/products/x?variant=42&utm_source=ig#reviews"),
                         "https://a.com/products/x?variant=42")
        self.assertEqual(add_items.clean_url("https://Shop.com/p/x/."), "https://shop.com/p/x/")

    def test_same_item(self):
        self.assertTrue(add_items.same_item("https://www.a.com/p/x/", "http://a.com/p/x"))
        self.assertFalse(add_items.same_item("https://a.com/p/x?variant=1", "https://a.com/p/x?variant=2"))


class TestParseAndAdd(unittest.TestCase):
    BODY = """Paste below
- https://a.com/products/one?utm_source=x
https://b.com/products/two | tags: Bags
not a link
https://a.com/products/one
https://www.instagram.com/reel/abc/
tags: shoes, gifts
https://c.com/products/three."""

    def test_parse(self):
        links, tags = add_items.parse_body(self.BODY)
        self.assertEqual([l["url"] for l in links], [
            "https://a.com/products/one", "https://b.com/products/two", "https://a.com/products/one",
            "https://www.instagram.com/reel/abc/", "https://c.com/products/three"])
        self.assertEqual(links[1]["tags"], ["bags"])
        self.assertEqual(tags, ["shoes", "gifts"])

    def test_add_appends_before_notes_and_skips_dupes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# header\n\nhttps://c.com/products/three | tags: hats\n\n# Not trackable yet: notes\n#   x\n")
        try:
            added, dupes, invalid = add_items.add(self.BODY, f.name)
            text = open(f.name).read()
        finally:
            os.unlink(f.name)
        self.assertEqual([a["url"] for a in added], ["https://a.com/products/one", "https://b.com/products/two"])
        self.assertEqual(added[1]["tags"], ["bags", "shoes", "gifts"])
        self.assertEqual(len(dupes), 2)
        self.assertEqual(len(invalid), 1)
        self.assertIn("https://b.com/products/two | tags: bags, shoes, gifts\n\n# Not trackable yet", text)
        self.assertTrue(text.index("products/one") > text.index("products/three"))

    def test_reply(self):
        added = [{"url": "https://a.com/products/one", "tags": ["bags"]},
                 {"url": "https://b.com/products/two", "tags": []}]
        results = {"https://a.com/products/one": {"ok": True, "price": 1299.5, "currency": "USD",
                                                   "availability": "in_stock", "title": "One | Bag"},
                   "https://b.com/products/two": {"ok": False, "error": "blocked"}}
        text = add_items.reply(added, ["https://d.com/x"], [], results, "https://page/")
        self.assertIn("| [One / Bag](https://a.com/products/one) | 1,299.50 USD | in stock | bags |", text)
        self.assertIn("check failed: blocked", text)
        self.assertIn("Already tracked", text)


if __name__ == "__main__":
    unittest.main()
