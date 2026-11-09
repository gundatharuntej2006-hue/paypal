"""Bounded, loopback-only HTTP transport; no proxies, redirects, or generic tools."""

import ipaddress
import re
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ..attacks.fixtures import get_attack
from .state import AgentConfig, CapturedPage, MerchantCandidate

ID = r"[A-Za-z0-9_-]{1,64}"
CATALOG = re.compile(rf"/merchants/({ID})")
PRODUCT = re.compile(rf"/merchants/({ID})/products/({ID})")


class MerchantError(ValueError):
    """Explicit browsing or evidence failure with a bounded diagnostic."""


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MerchantError("HTTP redirects are not permitted")


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)


class MerchantClient:
    def __init__(self, config: AgentConfig):
        self.config = config
        parsed = urlsplit(config.base_url)
        if (parsed.scheme != "http" or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise MerchantError("base URL must be an HTTP loopback origin without credentials or a path")
        host = parsed.hostname
        # Pin localhost to a literal address, avoiding DNS rebinding and proxy lookup.
        host = "127.0.0.1" if host == "localhost" else host
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError
            port = parsed.port or 80
        except (ValueError, TypeError):
            raise MerchantError("base host must be a loopback IP address or localhost") from None
        authority = f"[{host}]" if ":" in host else host
        self.base_url = f"http://{authority}:{port}"
        self.attack = get_attack(config.attack_id) if config.attack_id is not None else None
        if config.attack_id is not None and self.attack is None:
            raise MerchantError("unknown attack_id")
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    def local_url(self, path_or_url: str) -> str:
        if (not isinstance(path_or_url, str) or "\\" in path_or_url
                or any(ord(c) < 33 for c in path_or_url) or path_or_url.startswith("//")):
            raise MerchantError("invalid local URL")
        url = urljoin(self.base_url + "/", path_or_url)
        parsed = urlsplit(url)
        if (f"{parsed.scheme}://{parsed.netloc}" != self.base_url or parsed.fragment
                or not (parsed.path == "/" or CATALOG.fullmatch(parsed.path) or PRODUCT.fullmatch(parsed.path))):
            raise MerchantError("URL is outside the configured merchant routes/origin")
        if parsed.query and (self.attack is None or parsed.path + "?" + parsed.query != self.attack.poisoned_path):
            raise MerchantError("unconfigured attack query")
        return url

    def fetch(self, path_or_url: str) -> CapturedPage:
        url = self.local_url(path_or_url)
        try:
            with self._opener.open(Request(url, headers={"Accept": "text/html"}), timeout=self.config.timeout) as response:
                if response.status != 200:
                    raise MerchantError("merchant response was not HTTP 200")
                if response.headers.get_content_type() != "text/html":
                    raise MerchantError("merchant response was not HTML")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise MerchantError("encoded responses are unsupported")
                raw = response.read(self.config.max_response_bytes + 1)
                if len(raw) > self.config.max_response_bytes:
                    raise MerchantError("response exceeds byte limit; nothing was truncated or consumed")
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None and (not declared_length.isdigit() or int(declared_length) != len(raw)):
                    raise MerchantError("incomplete HTTP response body")
                html = raw.decode("utf-8", errors="strict")
                if "<html" not in html.lower() or "</html>" not in html.lower():
                    raise MerchantError("incomplete or malformed merchant HTML")
                return CapturedPage(url=url, html=html)
        except HTTPError as exc:
            exc.close()
            raise MerchantError(f"merchant returned HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError, UnicodeError):
            raise MerchantError("merchant transport or UTF-8 decoding failed") from None

    def links(self, page: CapturedPage, pattern: re.Pattern) -> tuple[str, ...]:
        parser = _Links()
        parser.feed(page.html)
        parser.close()
        accepted = []
        for href in parser.links:
            try:
                url = self.local_url(urljoin(page.url, href))
            except MerchantError:
                continue  # Untrusted external/non-route links are never requested.
            parsed = urlsplit(url)
            if not parsed.query and pattern.fullmatch(parsed.path) and url not in accepted:
                accepted.append(url)
        return tuple(accepted)

    def discover(self) -> tuple[tuple[MerchantCandidate, ...], CapturedPage]:
        page = self.fetch("/")
        urls = self.links(page, CATALOG)
        if len(urls) > self.config.max_merchants:
            raise MerchantError("merchant traversal limit exceeded")
        candidates = tuple(MerchantCandidate(CATALOG.fullmatch(urlsplit(url).path)[1], url) for url in urls)
        return candidates, page

    def product_urls(self, catalog: CapturedPage, merchant: MerchantCandidate) -> tuple[str, ...]:
        return tuple(url for url in self.links(catalog, PRODUCT)
                     if PRODUCT.fullmatch(urlsplit(url).path)[1] == merchant.merchant_id)

    def product_page(self, url: str) -> CapturedPage:
        clean_url = self.local_url(url)
        if self.attack is not None and urlsplit(clean_url).path == self.attack.clean_path:
            return self.fetch(self.attack.poisoned_path)
        return self.fetch(clean_url)
