"""Bounded workflow nodes; only discovery/retrieval nodes have merchant HTTP access."""

from .content import parse_product, read_pages
from .merchant_client import MerchantClient, MerchantError
from .model import ModelRequest, ShoppingModel, amount_for, understand
from .state import PurchaseProposal, Selection, ShoppingState
from .trace import decision_trace


class ShoppingNodes:
    def __init__(self, client: MerchantClient, model: ShoppingModel):
        self.client = client
        self.model = model

    def understand_task(self, state: ShoppingState):
        return {"task_constraints": understand(state["original_task"]),
                "merchant_candidates": (), "product_candidates": (), "captured_pages": (),
                "source_contexts": (), "model_pages": (), "reader_mode": self.client.config.reader_mode,
                "selection": None, "selected_merchant": None, "selected_product": None,
                "purchase_proposal": None, "reasoning_summary": "", "source_references": (),
                "execution_errors": ()}

    def discover_merchants(self, state: ShoppingState):
        merchants, page = self.client.discover()
        return {"merchant_candidates": merchants, "captured_pages": (page,),
                "execution_errors": () if merchants else ("no_merchants",)}

    def retrieve_products(self, state: ShoppingState):
        pages = list(state["captured_pages"])
        products = []
        target_seen = self.client.attack is None
        # Preserve completed fetches even if a later request fails.
        try:
            for merchant in state["merchant_candidates"]:
                catalog = self.client.fetch(merchant.catalog_url)
                pages.append(catalog)
                urls = self.client.product_urls(catalog, merchant)
                if len(products) + len(urls) > self.client.config.max_products:
                    raise MerchantError("product traversal limit exceeded")
                for url in urls:
                    page = self.client.product_page(url)
                    pages.append(page)
                    product = parse_product(page)
                    products.append(product)
                    if self.client.attack is not None and page.url.endswith(self.client.attack.poisoned_path):
                        target_seen = True
            if not target_seen:
                raise MerchantError("requested attack target was not discovered")
        except (ValueError, OSError) as exc:
            return {"captured_pages": tuple(pages), "product_candidates": tuple(products),
                    "execution_errors": (f"retrieve_products: {exc}",)}
        return {"captured_pages": tuple(pages), "product_candidates": tuple(products),
                "execution_errors": () if products else ("no_products",)}

    def read_product_content(self, state: ShoppingState):
        contexts, pages = read_pages(state["captured_pages"], state["reader_mode"])
        return {"source_contexts": contexts, "model_pages": pages}

    def compare_products(self, state: ShoppingState):
        request = ModelRequest(
            original_task=state["original_task"], task_constraints=state["task_constraints"],
            product_candidates=state["product_candidates"], pages=state["model_pages"],
            source_references=tuple(c.ref for c in state["source_contexts"]),
        )
        try:
            selection = self.model.select(request)
        except Exception:
            # Provider exceptions may contain secrets/prompt text; retain a safe error only.
            return {"execution_errors": ("compare_products: model invocation failed",)}
        return {"selection": selection, "execution_errors": () if selection is not None else ("no_matching_product",)}

    def select_product(self, state: ShoppingState):
        selection = state["selection"]
        if not isinstance(selection, Selection):
            raise ValueError("model must return a validated Selection")
        product = next((p for p in state["product_candidates"]
                        if (p.merchant_id, p.product_id) == (selection.merchant_id, selection.product_id)), None)
        if product is None:
            raise ValueError("model selected a product that was not retrieved")
        contexts = {c.ref: c for c in state["source_contexts"]}
        if (any(ref not in contexts for ref in selection.source_references)
                or not any(contexts[ref].url == product.url for ref in selection.source_references)):
            raise ValueError("selection cites missing sources or omits its product page")
        merchant = next(m for m in state["merchant_candidates"] if m.merchant_id == product.merchant_id)
        return {"selected_product": product, "selected_merchant": merchant}

    def record_decision_trace(self, state: ShoppingState):
        return decision_trace(state)

    def prepare_purchase_proposal(self, state: ShoppingState):
        product = state["selected_product"]
        quantity = state["task_constraints"].quantity
        return {"purchase_proposal": PurchaseProposal(
            merchant_id=product.merchant_id, merchant_name=product.merchant_name,
            product_id=product.product_id, product_name=product.product_name,
            quantity=quantity, unit_price=product.unit_price,
            amount=amount_for(product.unit_price, quantity), currency=product.currency,
            category=product.category, source_references=tuple(state["source_references"]),
        )}
