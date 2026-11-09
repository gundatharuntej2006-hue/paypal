"""Real HTTP coverage of every poisoned variant and unchanged clean pages."""

from dataclasses import replace
from hashlib import sha256
from html import escape, unescape
from html.parser import HTMLParser
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from agentguard_workstream_c.attacks.fixtures import ATTACKS, get_attack
from agentguard_workstream_c.attacks.poisoning import poisoned_detail
from agentguard_workstream_c.merchants import rendering
from agentguard_workstream_c.merchants.data import MERCHANTS, get_merchant, get_product
from agentguard_workstream_c.merchants.server import create_server
from agentguard_workstream_c.merchants.source_context import validate_contexts
from agentguard_workstream_c.merchants.visible_text import context_from_page, visible_text

EXPECTED_CATEGORIES = (
    "hidden_css", "html_comment", "zero_size", "offscreen", "malicious_review",
    "system_impersonation", "admin_impersonation", "budget_override", "quantity_increase",
    "amount_tampering", "merchant_spoofing", "task_override", "shipping_fee",
    "currency_substitution", "approval_bypass",
)

# UTF-8 SHA-256 of all eight clean pages at the mock-merchant checkpoint.
CLEAN_HASHES = {
    ('merchant_a', 'usb-c-hub'): 'f248d8bf81844c3083e222e6c7075fb203122e6bb38ec606b6761da1e5099b13',
    ('merchant_a', 'desk-lamp'): '8b1418816e1d0dc371bd6d8134de61ad12a221e40557f96c75d2e9b1b58342b9',
    ('merchant_b', 'travel-mug'): 'acea786cefc877f17b81124bbdc68ab32b1ae4765a9d5b2e61d549cefaa10684',
    ('merchant_b', 'storage-baskets'): '389b1594c30c31651a3ce7f1cb5386aa82ca3ee47cb393653a229b91d4920977',
    ('merchant_c', 'daypack'): 'b68a616ffca6ea3c38e1e3f76940e0e3fc11b9fb61cb9c9a2e3130836ef7a48d',
    ('merchant_c', 'water-bottle'): '16dfc414866f164d361da800b96d1c562cd2646ce28c6a201a677ef328f4b8ab',
    ('merchant_lookalike', 'usb-c-hub'): '482be9223c17b61a2f0aedd337e2a4b3356cee5057019e9228aaaa7b27e0e19e',
    ('merchant_lookalike', 'desk-lamp'): 'c8f05b92169c33068a376d8325bbadb21934420fdf09be2827c6131b355ef1ac',
}


class Markup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.metadata = {}
        self.elements = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "meta" and "name" in attrs:
            self.metadata[attrs["name"]] = attrs.get("content")


@pytest.fixture(scope="module")
def poison_url():
    server = create_server(port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def fetch(url):
    with urlopen(url, timeout=5) as response:
        assert response.status == 200
        assert response.headers["Content-Type"] == "text/html; charset=utf-8"
        raw = response.read()
        assert int(response.headers["Content-Length"]) == len(raw)
        return raw.decode("utf-8")


def test_fixture_inventory():
    assert len(ATTACKS) == 15
    assert [a.attack_id for a in ATTACKS] == [f"poison_{n:02d}" for n in range(1, 16)]
    assert tuple(a.category for a in ATTACKS) == EXPECTED_CATEGORIES
    assert len({a.malicious_content for a in ATTACKS}) == len(ATTACKS)
    assert len({a.poisoned_path for a in ATTACKS}) == len(ATTACKS)
    for attack in ATTACKS:
        assert get_attack(attack.attack_id) is attack
        assert all((attack.name, attack.delivery, attack.expected_unprotected_behavior, attack.explanation))
        merchant = get_merchant(attack.merchant_id)
        assert merchant is not None and get_product(merchant, attack.product_id) is not None
    assert get_attack("missing") is None


@pytest.mark.parametrize("attack", ATTACKS, ids=lambda a: a.attack_id)
def test_poisoned_http_and_evidence(poison_url, attack):
    merchant = get_merchant(attack.merchant_id)
    product = get_product(merchant, attack.product_id)
    clean_before = fetch(poison_url + attack.clean_path)
    url = poison_url + attack.poisoned_path
    raw = fetch(url)
    assert raw == fetch(url) == poisoned_detail(merchant, product, attack)
    assert raw.startswith("<!doctype html>") and raw.endswith("</body></html>")
    assert attack.malicious_content in unescape(raw)
    assert "<script" not in raw.lower()
    parsed = Markup()
    parsed.feed(raw)
    assert parsed.metadata == {
        "merchant_id": attack.merchant_id, "merchant_name": merchant.name,
        "product_id": attack.product_id, "product_name": product.name,
        "price": product.price, "currency": product.currency, "category": product.category,
        "attack_id": attack.attack_id,
    }
    context = context_from_page(url=url, raw_html=raw)
    assert context.html == raw and context.url == url and context.kind == "html"
    assert context.text == visible_text(raw)
    assert context == context_from_page(url=url, raw_html=raw)
    assert len(validate_contexts([context])) == 1
    assert context.ref != context_from_page(url=poison_url + attack.clean_path, raw_html=clean_before).ref
    if attack.delivery in {"css_hidden", "comment", "zero_size", "offscreen"}:
        assert attack.malicious_content not in context.text
        assert context.text == visible_text(clean_before)
        if attack.delivery == "comment":
            assert f"<!-- attack_id={attack.attack_id}:" in raw
        else:
            element = next(attrs for _, attrs in parsed.elements if attrs.get("data-attack-id") == attack.attack_id)
            assert {"css_hidden": "display:none", "zero_size": "font-size:0", "offscreen": "left:-10000px"}[attack.delivery] in element["style"]
    else:
        assert attack.malicious_content in context.text
        tag = next(tag for tag, attrs in parsed.elements if attrs.get("data-attack-id") == attack.attack_id)
        assert tag == ("li" if attack.delivery == "review" else "aside")
        if attack.delivery == "review":
            assert raw.index("<h2>Reviews</h2>") < raw.index(escape(attack.malicious_content, quote=False))
    assert fetch(poison_url + attack.clean_path) == clean_before
    assert sha256(clean_before.encode("utf-8")).hexdigest() == CLEAN_HASHES[(attack.merchant_id, attack.product_id)]


@pytest.mark.parametrize("merchant", MERCHANTS, ids=lambda m: m.merchant_id)
def test_normal_browsing_stays_clean(poison_url, merchant):
    assert fetch(poison_url + "/") == rendering.listing()
    assert fetch(poison_url + rendering.catalog_path(merchant)) == rendering.catalog(merchant)
    for product in merchant.products:
        raw = fetch(poison_url + rendering.product_path(merchant, product))
        assert sha256(raw.encode("utf-8")).hexdigest() == CLEAN_HASHES[(merchant.merchant_id, product.product_id)]
        assert 'name="attack_id"' not in raw and "?attack=" not in raw


@pytest.mark.parametrize("query,status", [
    ("?attack=missing", 404), ("?attack=", 400), ("?attack", 400),
    ("?attack=poison_01&attack=poison_02", 400), ("?attack=poison_03", 404),
])
def test_invalid_attack_ids(poison_url, query, status):
    with pytest.raises(HTTPError) as exc:
        fetch(poison_url + ATTACKS[0].clean_path + query)
    assert exc.value.code == status
    exc.value.close()


def test_target_mismatch_rejected():
    merchant = MERCHANTS[1]
    with pytest.raises(ValueError, match="target"):
        poisoned_detail(merchant, merchant.products[0], ATTACKS[0])


@pytest.mark.parametrize("delivery,payload", [("comment", "unsafe -- comment"), ("invalid", "payload")])
def test_unsupported_markup_rejected(delivery, payload):
    merchant = MERCHANTS[0]
    attack = replace(ATTACKS[0], delivery=delivery, malicious_content=payload)
    with pytest.raises(ValueError):
        poisoned_detail(merchant, merchant.products[0], attack)


def test_visible_reader_nested_hidden_elements_and_entities():
    raw = ('<html><head><title>Not page text</title></head><body>'
           '<p>Tea &amp; coffee</p><div hidden><b>secret</b><br>still secret</div>'
           '<!-- comment secret --><div style="visibility: hidden">other secret</div>'
           '<script>script secret</script><style>style secret</style>'
           '<p>Visible ending</p></body></html>')
    assert visible_text(raw) == "Tea & coffee Visible ending"
