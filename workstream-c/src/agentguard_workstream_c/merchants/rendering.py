"""Deterministic, escaped HTML with visible identity and machine-readable metadata."""

from html import escape

from .data import MERCHANTS
from .models import Merchant, Product


def page(title: str, body: str, metadata: str = "") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{escape(title)}</title>{metadata}</head><body>'
            f'<nav><a href="/">All merchants</a></nav>{body}</body></html>')


def catalog_path(merchant: Merchant) -> str:
    return f"/merchants/{merchant.merchant_id}"


def product_path(merchant: Merchant, product: Product) -> str:
    return f"{catalog_path(merchant)}/products/{product.product_id}"


def listing() -> str:
    entries = "".join(
        f'<li data-merchant-id="{escape(m.merchant_id)}"><a href="{catalog_path(m)}">'
        f'{escape(m.name)}</a> <code>{escape(m.merchant_id)}</code></li>' for m in MERCHANTS
    )
    return page("Mock merchants", f"<h1>Mock merchants</h1><ul>{entries}</ul>")


def catalog(merchant: Merchant) -> str:
    entries = "".join(
        f'<li><a href="{product_path(merchant, p)}">{escape(p.name)}</a> '
        f'{p.price} {p.currency} — {escape(p.category)}</li>' for p in merchant.products
    )
    return page(merchant.name, f'<main data-merchant-id="{merchant.merchant_id}">'
                f'<h1>{escape(merchant.name)}</h1><code>{merchant.merchant_id}</code>'
                f'<p>{escape(merchant.description)}</p><ul>{entries}</ul></main>')


def detail(merchant: Merchant, product: Product) -> str:
    fields = {"merchant_id": merchant.merchant_id, "merchant_name": merchant.name,
              "product_id": product.product_id, "product_name": product.name,
              "price": product.price, "currency": product.currency, "category": product.category}
    metadata = "".join(f'<meta name="{key}" content="{escape(value, quote=True)}">'
                       for key, value in fields.items())
    reviews = "".join(f'<li><strong>{escape(r.author)}</strong> {r.rating}/5: {escape(r.text)}</li>'
                      for r in product.reviews)
    body = (f'<main data-merchant-id="{merchant.merchant_id}" data-product-id="{product.product_id}">'
            f'<p>Sold by <a href="{catalog_path(merchant)}">{escape(merchant.name)}</a> '
            f'<code>{merchant.merchant_id}</code></p><h1>{escape(product.name)}</h1>'
            f'<p>{escape(product.description)}</p><p class="price">{product.price} {product.currency}</p>'
            f'<p>Category: {escape(product.category)}</p><h2>Reviews</h2><ul>{reviews}</ul></main>')
    return page(product.name, body, metadata)
