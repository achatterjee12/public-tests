"""Offline tests for the spike's parsers (no network)."""
import os
import tempfile
import unittest

import spike


def scan(html):
    s = spike.Scanner()
    s.feed(html)
    return s


class TestNormalise(unittest.TestCase):
    def test_prices(self):
        for raw, want in [("$1,299.00", 1299.0), ("1299", 1299.0), (49.5, 49.5),
                          ("12,99 €", 12.99), ("1.299,00", 1299.0), ("1,299", 1299.0),
                          ("", None), (None, None), ("Sold out", None)]:
            self.assertEqual(spike.norm_price(raw), want, raw)

    def test_availability(self):
        self.assertEqual(spike.norm_availability("https://schema.org/InStock"), "in_stock")
        self.assertEqual(spike.norm_availability("http://schema.org/OutOfStock"), "out_of_stock")
        self.assertEqual(spike.norm_availability("SoldOut"), "out_of_stock")
        self.assertEqual(spike.norm_availability("PreOrder"), "preorder")
        self.assertEqual(spike.norm_availability(True), "in_stock")
        self.assertEqual(spike.norm_availability("weird"), "unknown")
        self.assertIsNone(spike.norm_availability(None))


class TestJsonLd(unittest.TestCase):
    def test_simple_product(self):
        html = """<script type="application/ld+json">{"@context":"https://schema.org",
          "@type":"Product","name":"Bag","offers":{"@type":"Offer","price":"395.00",
          "priceCurrency":"USD","availability":"https://schema.org/InStock"}}</script>"""
        r = spike.extract_jsonld(scan(html), "https://x.com/products/bag")
        self.assertEqual((r["price"], r["currency"], r["availability"], r["title"]),
                         (395.0, "USD", "in_stock", "Bag"))

    def test_graph_and_variant_from_url(self):
        html = """<script type="application/ld+json">{"@graph":[{"@type":"WebPage"},
          {"@type":["Product"],"name":"Shoe","offers":[
            {"price":100,"priceCurrency":"USD","availability":"InStock","url":"/p?variant=1"},
            {"price":120,"priceCurrency":"USD","availability":"OutOfStock","url":"/p?variant=2"}]}]}
          </script>"""
        r = spike.extract_jsonld(scan(html), "https://x.com/p?variant=2")
        self.assertEqual((r["price"], r["availability"]), (120.0, "out_of_stock"))

    def test_aggregate_offer_and_price_spec(self):
        html = """<script type="application/ld+json">[{"@type":"Product","name":"A",
          "offers":{"@type":"AggregateOffer","lowPrice":"20","priceCurrency":"USD"}}]</script>
          <script type="application/ld+json">{"@type":"Product","name":"B","offers":{
          "priceSpecification":{"price":"30.5","priceCurrency":"GBP"}}}</script>"""
        r = spike.extract_jsonld(scan(html), "https://x.com/")
        self.assertIn(r["price"], (20.0, 30.5))

    def test_no_product(self):
        r = spike.extract_jsonld(scan('<script type="application/ld+json">{"@type":"Organization"}</script>'), "u")
        self.assertFalse(r["ok"])

    def test_broken_json_is_skipped(self):
        r = spike.extract_jsonld(scan('<script type="application/ld+json">{oops</script>'), "u")
        self.assertFalse(r["ok"])


class TestMetaAndMicrodata(unittest.TestCase):
    def test_meta(self):
        html = """<meta property="og:title" content="Gloves">
          <meta property="product:price:amount" content="185.00">
          <meta property="product:price:currency" content="USD">
          <meta property="product:availability" content="instock">"""
        r = spike.extract_meta(scan(html))
        self.assertEqual((r["price"], r["currency"], r["availability"]), (185.0, "USD", "in_stock"))

    def test_microdata(self):
        html = """<div itemscope itemtype="https://schema.org/Product"><span itemprop="name" content="Hat"></span>
          <span itemprop="price" content="45.00"></span><meta itemprop="priceCurrency" content="USD">
          <link itemprop="availability" href="https://schema.org/OutOfStock"></div>"""
        r = spike.extract_microdata(scan(html))
        self.assertEqual((r["price"], r["currency"], r["availability"]), (45.0, "USD", "out_of_stock"))


class TestItemsFile(unittest.TestCase):
    def test_parse(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("# comment\n\nhttps://a.com/p | tags: Bags, baby stuff | target: 99.5 | note: hi\n"
                    "https://b.com/q\n")
        try:
            items = spike.parse_items(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(items[0], {"url": "https://a.com/p", "tags": ["bags", "baby stuff"],
                                    "target": 99.5, "note": "hi"})
        self.assertEqual(items[1]["tags"], [])

    def test_repo_items_file_parses(self):
        here = os.path.dirname(os.path.abspath(__file__))
        items = spike.parse_items(os.path.join(here, "items.txt"))
        self.assertTrue(items)
        self.assertTrue(all(i["url"].startswith("https://") for i in items))


class TestBlocked(unittest.TestCase):
    def test_blocked(self):
        self.assertTrue(spike.looks_blocked(403, ""))
        self.assertTrue(spike.looks_blocked(200, "<title>Robot or human?</title>"))
        self.assertFalse(spike.looks_blocked(200, "<html>" + "x" * 60000 + "captcha</html>"))


if __name__ == "__main__":
    unittest.main()
