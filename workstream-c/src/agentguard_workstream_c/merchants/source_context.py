"""Internal source evidence based on Atul's schema, not a gateway request format.

Callers supply the text actually read by the agent; this module never extracts,
sanitizes, truncates, or normalizes the accompanying original HTML.
"""

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Iterable, Literal

ContextKind = Literal["html", "text", "review", "email", "invoice", "tool_output"]
KINDS = ("html", "text", "review", "email", "invoice", "tool_output")
MAX_CONTEXTS = 50
MAX_TEXT = 200_000
MAX_HTML = 1_000_000


@dataclass(frozen=True)
class SourceContext:
    ref: str
    text: str
    kind: ContextKind = "text"
    url: str | None = None
    html: str | None = None

    def __post_init__(self) -> None:
        for name in ("ref", "text"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} must be a string")
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        for name in ("url", "html"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} must be a string or None")
        if len(self.text) > MAX_TEXT:
            raise ValueError(f"text exceeds {MAX_TEXT} characters")
        if self.html is not None and len(self.html) > MAX_HTML:
            raise ValueError(f"html exceeds {MAX_HTML} characters")

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


def make_context(*, text: str, url: str | None = None, html: str | None = None,
                 kind: ContextKind = "html") -> SourceContext:
    """Assign a content-stable reference; changed evidence gets a new reference."""
    # Validate before hashing, including oversized/non-string input rejection.
    evidence = SourceContext(ref="", kind=kind, url=url, text=text, html=html)
    payload = json.dumps([kind, url, text, html], ensure_ascii=True, separators=(",", ":"))
    ref = "ctx_" + sha256(payload.encode("utf-8")).hexdigest()
    return SourceContext(ref=ref, kind=evidence.kind, url=url, text=text, html=html)


def validate_contexts(contexts: Iterable[SourceContext]) -> tuple[SourceContext, ...]:
    """Validate a bounded collection without truncating any evidence."""
    result = []
    for context in contexts:
        if len(result) == MAX_CONTEXTS:
            raise ValueError(f"no more than {MAX_CONTEXTS} contexts are allowed")
        if not isinstance(context, SourceContext):
            raise TypeError("each context must be a SourceContext")
        result.append(context)
    return tuple(result)
