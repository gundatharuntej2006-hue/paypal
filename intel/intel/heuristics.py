"""Stage 1 of the injection scanner: fast rules, no LLM.

A strong hit decides on its own (verdict: injection). Weak hits are hints for the LLM stage,
but two different weak kinds of wording, or one weak wording plus a hiding trick, count as strong.
"""

from __future__ import annotations

import base64
import binascii
import itertools
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser

from intel.models import SourceContext


@dataclass(frozen=True)
class Hit:
    ref: str
    rule: str
    strong: bool
    kind: str  # "semantic" (wording aimed at the agent) or "obfuscation" (a hiding trick)
    snippet: str


_F = re.IGNORECASE

# (rule, pattern, strong). Patterns run on normalized text; [^.\n] keeps a match inside one sentence.
_PATTERNS: list[tuple[str, re.Pattern[str], bool]] = [
    ("instruction_override", re.compile(
        r"\b(?:ignore|disregard|forget|override|bypass)\b(?!\s+to\b)[^.\n]{0,40}?"
        r"\b(?:previous|prior|above|earlier|preceding|all|your|these|system)\b[^.\n]{0,30}?"
        r"\b(?:instructions?|prompts?|rules?|guidelines?|directions?|directives?|budget|limits?|mandate"
        r"|constraints?|polic(?:y|ies)|restrictions?)\b"
        r"|\b(?:ignore|disregard|override|bypass)\s+(?:the|any)\s+(?:budget|spending limit|limits?|mandate|approval|caps?)\b",
        _F), True),
    ("agent_addressed", re.compile(
        r"\b(?:if you are an?|attention|note to|message (?:to|for)|dear|hey|hello|instructions? (?:to|for))\s+"
        r"(?:the\s+|any\s+|all\s+)?(?:ai|a\.i\.|llm|language model|assistant|agent|bot|chatbot"
        r"|shopping (?:assistant|agent|bot)|automated (?:agent|system|buyer))s?\b", _F), True),
    ("persona_override", re.compile(
        r"\b(?:you are now (?:an? |the |in )?(?:admin|administrator|system|developer|unrestricted|jailbroken|operating|acting)"
        r"|from now on,?\s+you\b|your new (?:instructions?|task|goal|role|objective)"
        r"|new (?:instructions?|directives?)\s*:|act as (?:an? )?(?:admin|administrator|system|developer)"
        r"|(?:enter|switch to|enable)\s+(?:admin|developer|debug|god|maintenance) mode)", _F), True),
    ("role_token", re.compile(
        r"<\|(?:im_start|im_end|system|assistant|user)\|>|\[/?inst\]|\[/?(?:system|admin)\]|<<sys>>"
        r"|^\s*#{2,}\s*(?:system|instructions?)\b", _F | re.MULTILINE), True),
    ("conceal_from_user", re.compile(
        r"\b(?:do not|don't|dont|never|without)\s+(?:tell|telling|inform|informing|mention|mentioning|notify"
        r"|notifying|alert|alerting|asking|confirming with|checking with)\s+(?:it\s+to\s+|this\s+to\s+)?"
        r"(?:the\s+|your\s+)?(?:user|human|owner|principal|buyer)\b", _F), True),
    ("payee_change", re.compile(
        r"\b(?:change|update|set|switch|replace)\s+(?:the\s+)?(?:payee|recipient|merchant(?:[ _]id)?|seller account"
        r"|paypal (?:email|account))\s+(?:to|with|=)", _F), True),
    ("approval_evasion", re.compile(
        r"\b(?:avoid|bypass|evade|circumvent|get around|skip|stay (?:under|below)"
        r"|keep (?:it|each|them|the (?:amount|total|payments?))\s+(?:under|below))\b[^.\n]{0,30}?"
        r"\b(?:approval|step-?up|verification|fraud checks?|spending limit|payment limit|threshold|mandate)\b", _F), True),
    ("urgent_notice_to_agent", re.compile(
        r"\b(?:important|urgent|critical)\s+(?:notice|message|instructions?|update)\s+(?:to|for)\s+(?:the\s+|all\s+)?"
        r"(?:ai|agents?|assistants?|bots?|llms?)\b", _F), True),
    # Weak: common enough in normal pages to need a second signal.
    ("fake_banner", re.compile(
        r"\b(?:system|admin|administrator|developer|platform|paypal)\s+(?:message|notice|override|alert|instruction"
        r"|directive|announcement)s?\b", _F), False),
    ("agent_directive", re.compile(
        r"\b(?:ai|llm|assistant|agent|bot)s?\s+(?:must|should|shall|needs? to|is required to|are required to"
        r"|is instructed to)\b", _F), False),
    ("role_line", re.compile(r"^\s*(?:system|assistant)\s*:", _F | re.MULTILINE), False),
    ("fake_authorization", re.compile(
        r"\b(?:pre-?approved|pre-?authori[sz]ed|already (?:been )?(?:approved|authori[sz]ed)"
        r"|approved by (?:the )?(?:user|owner|admin|administrator|principal|human)"
        r"|(?:the )?user (?:has )?(?:approved|authori[sz]ed|wants you to|asked you to|told you to))\b", _F), False),
    ("amount_change", re.compile(
        r"\b(?:change|increase|raise|set|update|double)\s+(?:the\s+)?(?:amount|price|total|quantity|qty)\s+(?:to|=)\s*\$?\d",
        _F), False),
    ("split_payment", re.compile(
        r"\b(?:split|divide|break up|spread)\b[^.\n]{0,30}?\b(?:payments?|purchases?|orders?|transactions?|charges?"
        r"|the (?:amount|total))\b", _F), False),
    ("payment_redirect", re.compile(
        r"\b(?:pay(?:ments?)?|send|sent|transfer|wire|remit)\b[^.\n]{0,50}?\b(?:instead|rather than"
        r"|new (?:merchant|payee|account|recipient)|different (?:merchant|payee|account|seller))\b", _F), False),
]
_NEGATED = re.compile(r"(?:\bdon'?t|\bdo not|\bnever|\bno need to)\s*$", _F)

# ---------- unicode tricks ----------

_TAG_CHARS = re.compile("[\U000E0000-\U000E007F]")
_ZERO_WIDTH = re.compile("[​‌⁠﻿᠎]")  # U+200D left out: emoji sequences use it
_BIDI = re.compile("[‪-‮⁦-⁩]")
_INVISIBLE = re.compile("[​-‍⁠﻿᠎­‪-‮⁦-⁩\U000E0000-\U000E007F]")
_HOMOGLYPHS = str.maketrans(
    {"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d",
     "А": "A", "В": "B", "Е": "E", "І": "I", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T",
     "Х": "X", "ο": "o", "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
     "Ι": "I", "Ο": "O"}
)
_SPACES = re.compile(r"[ \t\f\v\r]+")
_NON_ASCII_WORD = re.compile(r"\w*[^\x00-\x7f]\w*")
_BASE64 = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")


def normalize(text: str) -> str:
    """Drop invisible characters, fold compatibility forms and look-alike letters, collapse spaces."""
    text = unicodedata.normalize("NFKC", _INVISIBLE.sub("", text)).translate(_HOMOGLYPHS)
    return _SPACES.sub(" ", text)


def _mixed_script_word(text: str) -> str | None:
    for match in itertools.islice(_NON_ASCII_WORD.finditer(text), 2000):
        scripts = {unicodedata.name(ch, "").split(" ")[0] for ch in match.group() if ch.isalpha()}
        if "LATIN" in scripts and scripts & {"CYRILLIC", "GREEK"}:
            return match.group()
    return None


def _decoded_blobs(text: str) -> list[str]:
    decoded = []
    for match in itertools.islice(_BASE64.finditer(text), 20):
        blob = match.group()
        try:
            value = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True).decode("utf-8")
        except (binascii.Error, ValueError):
            continue
        if value and sum(ch.isprintable() for ch in value) >= 0.9 * len(value) and value.count(" ") >= 3:
            decoded.append(value)
    return decoded


# ---------- hidden HTML ----------

_HIDING_STYLES = [
    re.compile(r"display:none"),
    re.compile(r"visibility:hidden"),
    re.compile(r"(?<![-\w])color:transparent"),
    re.compile(r"opacity:0(?:\.0+)?(?:;|!|$)"),
    re.compile(r"font-size:(?:0(?:\.0+)?(?:px|pt|em|rem|%)?|(?:0?\.\d+|1(?:\.0+)?)px|0?\.0\d*(?:em|rem))(?:;|!|$)"),
    re.compile(r"(?:left|top|right|text-indent|margin-left|margin-top):-\d{3,}"),
    re.compile(r"clip:rect\(0"),
]
_ZERO_HEIGHT = re.compile(r"(?<![-\w])(?:max-)?height:0(?:px)?(?:;|!|$)")
_CSS_CLASS_RULE = re.compile(r"\.([\w-]+)\s*\{([^}]*)\}")
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_TEXT_ATTRIBUTES = ("alt", "title", "aria-label", "placeholder", "content", "value")


def _style_hides(style: str) -> bool:
    s = re.sub(r"\s+", "", style.lower())
    if not s:
        return False
    if any(p.search(s) for p in _HIDING_STYLES):
        return True
    if _ZERO_HEIGHT.search(s) and "overflow:hidden" in s:
        return True
    fg = re.search(r"(?<![-\w])color:([^;!]+)", s)
    bg = re.search(r"background(?:-color)?:([^;!]+)", s)
    return bool(fg and bg and fg.group(1) == bg.group(1))


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []  # (tag, hides its content)
        self.hidden_classes: set[str] = set()
        self.hidden_text: list[str] = []
        self.comments: list[str] = []
        self.attribute_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        for name in _TEXT_ATTRIBUTES:
            if len(attributes.get(name, "")) >= 15:
                self.attribute_text.append(attributes[name])
        if tag in _VOID_TAGS:
            return
        hides = (
            "hidden" in attributes
            or tag in ("noscript", "template")
            or _style_hides(attributes.get("style", ""))
            or bool(self.hidden_classes.intersection(attributes.get("class", "").split()))
        )
        self.stack.append((tag, hides))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        tags = [tag for tag, _ in self.stack]
        if "style" in tags:
            for name, body in _CSS_CLASS_RULE.findall(data):
                if _style_hides(body):
                    self.hidden_classes.add(name)
            return
        if "script" in tags or not data.strip():
            return
        if any(hides for _, hides in self.stack):
            self.hidden_text.append(data.strip())

    def handle_comment(self, data: str) -> None:
        if data.strip():
            self.comments.append(data.strip())


def extract_html(html: str) -> tuple[str, list[str], list[str]]:
    """Return (hidden text, comments, attribute text) from a page, all normalized."""
    page = _PageParser()
    try:
        page.feed(html)
        page.close()
    except Exception:  # malformed HTML: keep whatever was parsed
        pass
    return (
        normalize(" ".join(page.hidden_text)),
        [normalize(c) for c in page.comments],
        [normalize(a) for a in page.attribute_text],
    )


# ---------- scanning ----------


def _window(text: str, start: int, end: int) -> str:
    return text[max(0, start - 60) : end + 140].strip()


def _wording_hits(ref: str, text: str, prefix: str = "", force_strong: bool = False) -> list[Hit]:
    hits = []
    for rule, pattern, strong in _PATTERNS:
        for match in pattern.finditer(text):
            if rule == "instruction_override" and _NEGATED.search(text[max(0, match.start() - 15) : match.start()]):
                continue
            hits.append(Hit(ref, prefix + rule, strong or force_strong, "semantic", _window(text, match.start(), match.end())))
            break
    return hits


def scan_context(ctx: SourceContext) -> list[Hit]:
    ref, raw = ctx.ref, ctx.text
    hits: list[Hit] = []

    tags = _TAG_CHARS.findall(raw)
    if tags:
        message = "".join(chr(ord(c) - 0xE0000) for c in tags if 0x20 <= ord(c) - 0xE0000 <= 0x7E)
        hits.append(Hit(ref, "unicode_tag_smuggling", True, "obfuscation", message[:300] or "(invisible tag characters)"))
    if zero_width := len(_ZERO_WIDTH.findall(raw)):
        hits.append(Hit(ref, "zero_width_chars", False, "obfuscation", f"{zero_width} zero-width character(s)"))
    if _BIDI.search(raw):
        hits.append(Hit(ref, "bidi_control", False, "obfuscation", "text-direction control characters"))
    if word := _mixed_script_word(raw):
        hits.append(Hit(ref, "mixed_script", False, "obfuscation", word))

    visible = normalize(raw)
    hits += _wording_hits(ref, visible)
    for decoded in _decoded_blobs(visible):
        hits += _wording_hits(ref, decoded, "encoded:", force_strong=True) or [
            Hit(ref, "encoded_text", False, "obfuscation", decoded[:200])
        ]

    if ctx.html:
        hidden, comments, attributes = extract_html(ctx.html)
        found = _wording_hits(ref, hidden, "hidden_text:", force_strong=True)
        if found:
            hits += found
        elif len(hidden.split()) >= 6:
            hits.append(Hit(ref, "hidden_text", False, "obfuscation", hidden[:200]))
        for comment in comments:
            hits += _wording_hits(ref, comment, "html_comment:", force_strong=True)
        for text in attributes:
            hits += _wording_hits(ref, text, "html_attribute:", force_strong=True)

    if not any(h.strong for h in hits):
        wording = {h.rule for h in hits if h.kind == "semantic"}
        tricks = {h.rule for h in hits if h.kind == "obfuscation"}
        if len(wording) >= 2 or (wording and tricks):
            hits.append(Hit(ref, "combined_signals", True, "semantic", " + ".join(sorted(wording | tricks))))
    return hits


def scan_all(contexts: Iterable[SourceContext]) -> list[Hit]:
    return [hit for ctx in contexts for hit in scan_context(ctx)]
