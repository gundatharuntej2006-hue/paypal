"""Fail-closed local browsing and error routes with actual HTTP responses."""

from dataclasses import replace
from http.server import BaseHTTPRequestHandler
import time

import pytest

from agentguard_workstream_c.agent.content import parse_product
from agentguard_workstream_c.agent.graph import run_shopping
from agentguard_workstream_c.agent.merchant_client import CATALOG, MerchantClient, MerchantError
from agentguard_workstream_c.agent.state import AgentConfig, CapturedPage
from agentguard_workstream_c.merchants.data import MERCHANTS
from agentguard_workstream_c.merchants.rendering import detail


@pytest.mark.parametrize("base", [
    "http://example.com:8010", "http://192.168.1.10:8010", "https://127.0.0.1:8010",
    "http://user:password@127.0.0.1:8010", "http://127.0.0.1:8010/path",
    "http://127.0.0.1:8010?key=value", "http://127.0.0.1:8010/#fragment",
])
def test_reject_nonloopback_or_ambiguous_base(base):
    with pytest.raises(MerchantError):
        MerchantClient(AgentConfig(base_url=base))


@pytest.mark.parametrize("url", [
    "https://api.paypal.com/", "http://example.com/merchants/merchant_a",
    "http://127.0.0.1:8001/scan/content", "/judge/intent", "/risk/score",
    "//example.com/merchants/merchant_a", "/merchants/merchant_a?attack=poison_01",
    "/merchants/merchant_a/products/usb-c-hub?attack=poison_01", "/merchants/%2e%2e",
    "/merchants/merchant_a#fragment", "/merchants/merchant_a\\other",
])
def test_reject_arbitrary_urls_before_network(agent_server, url):
    client = MerchantClient(AgentConfig(base_url=agent_server))
    with pytest.raises(MerchantError):
        client.fetch(url)


def test_only_same_origin_catalog_links_followed(agent_server):
    client = MerchantClient(AgentConfig(base_url=agent_server))
    page = CapturedPage(agent_server + "/", '<html><body>'
        '<base href="http://evil.invalid/">'
        '<a href="http://evil.invalid/merchants/merchant_a">External</a>'
        '<a href="/scan/content">Intelligence</a>'
        '<a href="//evil.invalid/merchants/merchant_b">External</a>'
        '<a href="/merchants/merchant_a">Local</a>'
        '<a href="/merchants/merchant_a?attack=poison_01">Unconfigured query</a>'
        '</body></html>')
    assert client.links(page, CATALOG) == (agent_server + "/merchants/merchant_a",)


@pytest.mark.parametrize("status,body,media_type", [
    (404, "not found", "text/html"), (503, "unavailable", "text/html"),
    (200, "<html><body>partial", "text/html"), (200, "{}", "application/json"),
    (200, "<html>" + "x" * 200 + "</html>", "text/html"),
])
def test_bad_responses_handled(server_factory, status, body, media_type):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            raw = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    with server_factory(Handler) as base:
        state = run_shopping("Buy a hub", AgentConfig(base_url=base, max_response_bytes=100))
    assert state["execution_errors"][0].startswith("discover_merchants:")
    assert not state["purchase_proposal"]


@pytest.mark.parametrize("external", [True, False])
def test_redirect_never_followed(server_factory, external):
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://example.com/merchants/merchant_a" if external else "/merchants/merchant_a")
            self.end_headers()

    with server_factory(Handler) as base:
        with pytest.raises(MerchantError, match="redirect"):
            MerchantClient(AgentConfig(base_url=base)).fetch("/")
    assert hits == ["/"]


def test_request_timeout(server_factory):
    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            time.sleep(0.15)
            self.send_response(200)
            self.end_headers()

    with server_factory(SlowHandler) as base:
        state = run_shopping("Buy a hub", AgentConfig(base_url=base, timeout=0.02))
    assert "transport" in state["execution_errors"][0]
    assert state["purchase_proposal"] is None


@pytest.mark.parametrize("stage", ["no_merchants", "no_products", "bad_product", "missing_attack_target"])
def test_conditional_termination(server_factory, stage):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                body = ('<html><body></body></html>' if stage == "no_merchants" else
                        '<html><body><a href="/merchants/merchant_b">Shop</a></body></html>')
            elif self.path == "/merchants/merchant_b":
                body = ('<html><body><a href="/merchants/merchant_b/products/travel-mug">Mug</a></body></html>'
                        if stage in {"bad_product", "missing_attack_target"} else '<html><body>Empty catalog</body></html>')
            else:
                body = (detail(MERCHANTS[1], MERCHANTS[1].products[0]) if stage == "missing_attack_target"
                        else '<html><body><main>No metadata</main></body></html>')
            raw = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    with server_factory(Handler) as base:
        state = run_shopping("Buy a hub", AgentConfig(base_url=base,
                              attack_id="poison_01" if stage == "missing_attack_target" else None))
    assert state["execution_errors"] and state["purchase_proposal"] is None
    assert state["reasoning_summary"] == ""
    if stage in {"no_merchants", "no_products"}:
        assert state["execution_errors"] == (stage,)
    else:
        assert state["captured_pages"]  # Retain even malformed/error evidence already fetched.


@pytest.mark.parametrize("bad", ["identity", "currency", "price", "duplicate"])
def test_invalid_product_metadata_rejected(bad):
    merchant = MERCHANTS[0]
    product = merchant.products[0]
    raw = detail(merchant, product)
    if bad == "identity":
        raw = raw.replace('content="merchant_a"', 'content="merchant_b"')
    elif bad == "currency":
        raw = raw.replace('content="USD"', 'content="usd"')
    elif bad == "price":
        raw = raw.replace('content="29.99"', 'content="NaN"')
    else:
        raw = raw.replace("</head>", '<meta name="price" content="1.00"></head>')
    with pytest.raises(MerchantError):
        parse_product(CapturedPage("http://127.0.0.1:8010/merchants/merchant_a/products/usb-c-hub", raw))


def test_localhost_is_pinned_and_config_validated():
    assert MerchantClient(AgentConfig(base_url="http://localhost:8010")).base_url == "http://127.0.0.1:8010"
    for override in ({"reader_mode": "invalid"}, {"timeout": 0}, {"max_products": 0}):
        with pytest.raises(ValueError):
            replace(AgentConfig(), **override)
