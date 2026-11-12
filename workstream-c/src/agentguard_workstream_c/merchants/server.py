"""Read-only local merchant HTTP server; no payments or external service calls."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .data import get_merchant, get_product
from . import rendering
from ..attacks.fixtures import get_attack
from ..attacks.poisoning import poisoned_detail

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8010


class MerchantHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed_url = urlsplit(self.path)
        path = parsed_url.path
        if path == "/health":
            self.respond(200, '{"status":"ok"}', "application/json")
            return
        if path == "/":
            self.respond(200, rendering.listing())
            return
        parts = path.strip("/").split("/")
        if len(parts) in (2, 4) and parts[0] == "merchants":
            merchant = get_merchant(parts[1])
            if merchant is None:
                self.respond(404, rendering.page("Not found", "<h1>Merchant not found</h1>"))
                return
            if len(parts) == 2:
                self.respond(200, rendering.catalog(merchant))
                return
            if parts[2] == "products":
                product = get_product(merchant, parts[3])
                if product is not None:
                    query = parse_qs(parsed_url.query, keep_blank_values=True)
                    if "attack" in query:
                        ids = query["attack"]
                        if len(ids) != 1 or not ids[0]:
                            self.respond(400, rendering.page("Bad request", "<h1>Supply one nonempty attack ID</h1>"))
                            return
                        attack = get_attack(ids[0])
                        if attack is None or (attack.merchant_id, attack.product_id) != (merchant.merchant_id, product.product_id):
                            self.respond(404, rendering.page("Not found", "<h1>Attack variant not found for this product</h1>"))
                            return
                        self.respond(200, poisoned_detail(merchant, product, attack))
                        return
                    self.respond(200, rendering.detail(merchant, product))
                    return
                self.respond(404, rendering.page("Not found", "<h1>Product not found</h1>"))
                return
        self.respond(404, rendering.page("Not found", "<h1>Page not found</h1>"))

    def respond(self, status: int, body: str, media_type: str = "text/html") -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{media_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)


def create_server(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """Create a server; port zero is supported for isolated integration tests."""
    return ThreadingHTTPServer((host, port), MerchantHandler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    with create_server(args.host, args.port) as server:
        print(f"Mock merchants: http://{args.host}:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
