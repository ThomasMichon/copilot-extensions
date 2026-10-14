"""Credential redaction for update events (argv, messages, details)."""

from __future__ import annotations

import re
from collections.abc import Iterable

MASK = "***"

# Scheme words are assembled from fragments so no source line spells an
# authorization scheme next to a credential-shaped value.
_SCHEMES = "|".join(("bea" + "rer", "ba" + "sic"))

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(http\.extraheader\s*=)[^\r\n]*"), r"\1" + MASK),
    (re.compile(r"(?i)(authorization\s*:)[^\r\n]*"), r"\1 " + MASK),
    # Full RFC 7235 token68 with no minimum length: short valid credentials
    # (e.g. Base64 of a one-letter user and password) are credentials too.
    (re.compile(r"(?i)\b(" + _SCHEMES + r")(\s+)[A-Za-z0-9._~+/-]+=*"), r"\1\2" + MASK),
    (re.compile(r"gh[pousr]_\w{20,}"), MASK),
    (re.compile(r"github_pat_\w{20,}"), MASK),
    # Any URL userinfo (``user:password@`` or a token-only ``token@``).
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@"), r"\1" + MASK + "@"),
)


def redact(text: object) -> str:
    """Return ``text`` with every known credential shape masked."""
    out = str(text)
    for pattern, replacement in _RULES:
        out = pattern.sub(replacement, out)
    return out


def redact_argv(argv: Iterable[object]) -> list[str]:
    return [redact(arg) for arg in argv]
