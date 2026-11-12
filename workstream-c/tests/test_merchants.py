"""Exercise real HTTP routes, merchant identity, and complete HTML evidence."""

from dataclasses import replace
from decimal import Decimal
from html.parser import HTMLParser
import json
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from agentguard_workstream_c.merchants.data import MERCHANTS
from agentguard_workstream_c.merchants import rendering
from agentguard_workstream_c.merchants.server import create_server
from agentguard_workstream_c.merchants.source_context import make_context


class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metadata = {}
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and "name" in attrs:
            self.metadata[attrs["name"]] = attrs.get("content")
        if tag == "a":
            self.links.append(attrs.get("href"))


@pytest.fixture(scope="module")
def merchant_url():
    server = create_server(port=0)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def retrieve(url):
    with urlopen(url, timeout=5) as response:
        raw = response.read()
        assert int(response.headers["Content-Length"]) == len(raw)
        return response.headers["Content-Type"], raw.decode("utf-8")


def test_all_merchant_identities():
    assert {m.merchant_id for m in MERCHANTS} == {
        "merchant_a", "merchant_b", "merchant_c", "merchant_lookalike"
    }
    assert len({m.name for m in MERCHANTS}) == 4
    for merchant in MERCHANTS:
        assert merchant.name != merchant.merchant_id
        assert len(merchant.products) >= 2
        assert len({p.product_id for p in merchant.products}) == len(merchant.products)


def test_health_and_listing(merchant_url):
    content_type, body = retrieve(merchant_url + "/health")
    assert content_type == "application/json; charset=utf-8"
    assert json.loads(body) == {"status": "ok"}
    content_type, body = retrieve(merchant_url + "/")
    assert content_type == "text/html; charset=utf-8"
    parsed = PageMetadata()
    parsed.feed(body)
    for merchant in MERCHANTS:
        assert merchant.name in body
        assert merchant.merchant_id in body
        assert rendering.catalog_path(merchant) in parsed.links


@pytest.mark.parametrize("merchant", MERCHANTS, ids=lambda m: m.merchant_id)
def test_catalog_and_all_product_pages(merchant_url, merchant):
    _, catalog = retrieve(merchant_url + rendering.catalog_path(merchant))
    parsed = PageMetadata()
    parsed.feed(catalog)
    assert merchant.name in catalog
    assert merchant.merchant_id in catalog
    for product in merchant.products:
        path = rendering.product_path(merchant, product)
        assert path in parsed.links
        assert Decimal(product.price) > 0
        url = merchant_url + path
        _, raw_html = retrieve(url)
        assert raw_html == rendering.detail(merchant, product)
        assert raw_html.startswith("<!doctype html>")
        assert raw_html.endswith("</body></html>")
        metadata = PageMetadata()
        metadata.feed(raw_html)
        assert metadata.metadata == {
            "merchant_id": merchant.merchant_id, "merchant_name": merchant.name,
            "product_id": product.product_id, "product_name": product.name,
            "price": product.price, "currency": product.currency, "category": product.category,
        }
        assert product.description in raw_html
        assert all(review.text in raw_html for review in product.reviews)
        context = make_context(url=url, text=product.description, html=raw_html)
        assert context.html == raw_html
        assert context.text == product.description
        assert context.url == url


@pytest.mark.parametrize("path", [
    "/missing", "/merchants/missing", "/merchants/missing/products/usb-c-hub",
    "/merchants/merchant_a/products/missing", "/merchants/merchant_a/other/usb-c-hub",
    "/merchants/merchant_a/products/usb-c-hub/extra",
])
def test_missing_routes(merchant_url, path):
    with pytest.raises(HTTPError) as exc:
        retrieve(merchant_url + path)
    assert exc.value.code == 404
    exc.value.close()


def test_lookalike_remains_distinct(merchant_url):
    pages = []
    for merchant_id in ("merchant_a", "merchant_lookalike"):
        _, body = retrieve(f"{merchant_url}/merchants/{merchant_id}/products/usb-c-hub")
        metadata = PageMetadata()
        metadata.feed(body)
        assert metadata.metadata["merchant_id"] == merchant_id
        pages.append(metadata.metadata)
    assert "Northstar Office" in pages[0]["merchant_name"]
    assert "Northstar Office" in pages[1]["merchant_name"]
    assert pages[0]["merchant_name"] != pages[1]["merchant_name"]


def test_catalog_text_is_escaped():
    merchant = MERCHANTS[0]
    product = replace(merchant.products[0], name='<script>alert("x")</script>')
    body = rendering.detail(merchant, product)
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    metadata = PageMetadata()
    metadata.feed(body)
    assert metadata.metadata["product_name"] == product.name
