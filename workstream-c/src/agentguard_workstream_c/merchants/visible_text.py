"""Visible-text reader for our static fixture markup, not a browser/CSS engine.

Supports inline display:none, visibility:hidden, zero font size, negative absolute
left positioning, and HTML hidden attributes. Does not resolve stylesheets,
layout, scripts, or arbitrary CSS. Raw evidence is never passed through this
reader before being stored: extraction produces a separate text value.
"""

from html.parser import HTMLParser
import re

from .source_context import SourceContext, make_context

VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        style = dict(part.split(":", 1) for part in (attrs.get("style") or "").lower().split(";") if ":" in part)
        style = {key.strip(): value.strip() for key, value in style.items()}
        hidden = (
            "hidden" in attrs or tag in {"head", "script", "style", "template"}
            or style.get("display") == "none" or style.get("visibility") == "hidden"
            or re.fullmatch(r"0(?:px|em|rem|%)?", style.get("font-size", "")) is not None
            or (style.get("position") == "absolute" and style.get("left", "").startswith("-"))
        )
        inherited = bool(self.stack and self.stack[-1][1])
        if tag not in VOID_TAGS:
            self.stack.append((tag, inherited or hidden))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not self.stack or not self.stack[-1][1]:
            self.parts.append(data)


def visible_text(raw_html: str) -> str:
    """Extract the ordinary text view supported by the local fixture reader."""
    parser = _VisibleText()
    parser.feed(raw_html)
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def context_from_page(*, url: str, raw_html: str) -> SourceContext:
    """Evidence for an agent that consumes this reader's output, plus original HTML.

    Agents with another reader should instead pass their actual consumed text
    to make_context directly.
    """
    return make_context(url=url, text=visible_text(raw_html), html=raw_html, kind="html")
