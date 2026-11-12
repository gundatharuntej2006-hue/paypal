"""Read actual merchant markup and preserve the exact configured model content."""

from decimal import Decimal
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit

from ..merchants.source_context import make_context, validate_contexts
from ..merchants.visible_text import VOID_TAGS, visible_text
from .merchant_client import MerchantError, PRODUCT
from .state import CapturedPage, PageContent, ProductCandidate, ReaderMode


class _ProductHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.metadata = {}
        self.main = None
        self.stack = []
        self.after_heading = False
        self.paragraphs = []
        self.reviews = []
        self.collect = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and "name" in attrs:
            key = attrs["name"]
            if key in self.metadata:
                raise MerchantError("duplicate product metadata")
            self.metadata[key] = attrs.get("content")
        if tag == "main":
            if self.main is not None:
                raise MerchantError("multiple product main elements")
            self.main = attrs
        if tag == "p" and self.stack and self.stack[-1] == "main" and self.after_heading:
            self.collect = ("p", [])
        elif tag == "li" and "main" in self.stack:
            self.collect = ("li", [])
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag == "h1":
            self.after_heading = True
        if self.collect is not None and self.collect[0] == tag:
            text = " ".join(" ".join(self.collect[1]).split())
            (self.paragraphs if tag == "p" else self.reviews).append(text)
            self.collect = None
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()

    def handle_data(self, data):
        if self.collect is not None:
            self.collect[1].append(data)


def parse_product(page: CapturedPage) -> ProductCandidate:
    parser = _ProductHTML()
    parser.feed(page.html)
    parser.close()
    required = ("merchant_id", "merchant_name", "product_id", "product_name", "price", "currency", "category")
    if any(not isinstance(parser.metadata.get(key), str) or not parser.metadata[key] for key in required):
        raise MerchantError("missing product metadata")
    metadata = parser.metadata
    path = PRODUCT.fullmatch(urlsplit(page.url).path)
    if (path is None or parser.main is None or parser.stack
            or (metadata["merchant_id"], metadata["product_id"]) != path.groups()
            or parser.main.get("data-merchant-id") != metadata["merchant_id"]
            or parser.main.get("data-product-id") != metadata["product_id"]):
        raise MerchantError("product identity/path or markup mismatch")
    if (not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", metadata["price"])
            or len(metadata["price"]) > 32 or not re.fullmatch(r"[A-Z]{3}", metadata["currency"])):
        raise MerchantError("invalid product price/currency")
    if not parser.paragraphs:
        raise MerchantError("missing product description")
    return ProductCandidate(
        merchant_id=metadata["merchant_id"], merchant_name=metadata["merchant_name"],
        product_id=metadata["product_id"], product_name=metadata["product_name"],
        unit_price=Decimal(metadata["price"]), currency=metadata["currency"],
        category=metadata["category"], description=parser.paragraphs[0],
        reviews=tuple(parser.reviews), url=page.url,
    )


def read_pages(pages: tuple[CapturedPage, ...], mode: ReaderMode):
    if mode not in ("visible_text", "raw_html"):
        raise ValueError("invalid reader mode")
    contexts = validate_contexts(
        make_context(url=page.url, html=page.html,
                     text=visible_text(page.html) if mode == "visible_text" else page.html,
                     kind="html")
        for page in pages
    )
    consumed = tuple(PageContent(source_ref=c.ref, url=c.url, content=c.text) for c in contexts)
    return contexts, consumed
