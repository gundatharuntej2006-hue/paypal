"""Add inert fixture markup to a clean page without changing catalog data."""

from html import escape

from .fixtures import AttackFixture
from ..merchants.models import Merchant, Product
from ..merchants.rendering import detail


def poisoned_detail(merchant: Merchant, product: Product, attack: AttackFixture) -> str:
    if (merchant.merchant_id, product.product_id) != (attack.merchant_id, attack.product_id):
        raise ValueError("attack target does not match merchant/product")
    clean = detail(merchant, product)
    text = escape(attack.malicious_content, quote=False)
    marker = f'data-attack-id="{escape(attack.attack_id, quote=True)}"'
    match attack.delivery:
        case "css_hidden":
            fragment = f'<div {marker} style="display:none">{text}</div>'
        case "comment":
            # Comments cannot contain '--'; reject rather than altering the evidence.
            if "--" in text or text.endswith("-"):
                raise ValueError("payload cannot be represented as an HTML comment")
            fragment = f'<!-- attack_id={attack.attack_id}: {text} -->'
        case "zero_size":
            fragment = f'<span {marker} style="font-size:0;line-height:0">{text}</span>'
        case "offscreen":
            fragment = f'<div {marker} style="position:absolute;left:-10000px;top:0">{text}</div>'
        case "review":
            fragment = f'<li {marker}><strong>Verified shopper</strong> 5/5: {text}</li>'
        case "banner":
            fragment = f'<aside {marker} class="merchant-notice">{text}</aside>'
        case _:
            raise ValueError("unsupported delivery mechanism")
    if attack.delivery == "review":
        body = clean.replace("<h2>Reviews</h2><ul>", "<h2>Reviews</h2><ul>" + fragment, 1)
    elif attack.delivery == "banner":
        body = clean.replace("<h1>", fragment + "<h1>", 1)
    else:
        body = clean.replace("</main>", fragment + "</main>", 1)
    metadata = f'<meta name="attack_id" content="{escape(attack.attack_id, quote=True)}">'
    return body.replace("</head>", metadata + "</head>", 1)
