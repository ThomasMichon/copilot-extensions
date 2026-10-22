"""Secret-shaped string patterns shared by log rendering and session sync.

Matching is pattern-based: it replaces common credential shapes (GitHub
tokens, bearer tokens, JWTs, ``key=value`` style assignments) with
``[REDACTED]``. It is a best-effort filter, not a guarantee that every secret
is caught.
"""

from __future__ import annotations

import re

REDACTION_MARKER = "[REDACTED]"

# Patterns that look like secrets — redact on sight
SECRET_PATTERNS = [
    re.compile(r'(?i)(token|secret|password|apikey|api_key|credential)\s*[:=]\s*\S+'),
    re.compile(r'ghp_[A-Za-z0-9_]{36,}'),
    re.compile(r'ghu_[A-Za-z0-9_]{36,}'),
    re.compile(r'ghs_[A-Za-z0-9_]{36,}'),
    re.compile(r'github_pat_[A-Za-z0-9_]{22,}'),
    re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/]+=*'),
    re.compile(r'eyJ[A-Za-z0-9\-_]{20,}\.eyJ[A-Za-z0-9\-_]{20,}'),  # JWT
]


def redact(text: str) -> str:
    """Replace secret-shaped strings with [REDACTED]."""
    for pat in SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


# ---------------------------------------------------------------------------
# Session-sync redaction (opt-in ``sync.redact_secrets``).
#
# A superset of ``SECRET_PATTERNS`` that keeps the key of a ``key: value``
# assignment, also covers quoted values, and adds a few widely used
# credential shapes. Applied to decoded strings, never to raw JSON bytes.
# ---------------------------------------------------------------------------

#: Bump whenever the sync rules change: published copies record it, so a
#: changed rule set re-derives every protected copy on the next pass.
PATTERN_SET_VERSION = 1

_ASSIGNMENT_KEYWORDS = (
    r"token|secret|password|passwd|passphrase|apikey|api_key|api-key"
    r"|credentials?|private[_-]?key"
)

#: A private key block runs from its BEGIN header to the first END footer
#: (or the end of the text when the footer is missing).
PEM_BEGIN = r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
PEM_END = r"-----END [A-Z ]*PRIVATE KEY-----"
PEM_BEGIN_RE = re.compile(PEM_BEGIN)
PEM_END_RE = re.compile(PEM_END)

#: The text before an assignment's ``[:=]`` in the patterns below.
ASSIGNMENT_KEY_END_RE = re.compile(
    r"(?i)(?:" + _ASSIGNMENT_KEYWORDS + r"|authorization)(?:\\?[\"'])?\Z"
)

SYNC_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Private key blocks first: an assignment rule must not consume the
    # header (``private_key: -----BEGIN ...``) and leave the body behind.
    (re.compile(PEM_BEGIN + r"[\s\S]*?(?:" + PEM_END + r"|\Z)"), REDACTION_MARKER),
    # Quoted value: "password": "two words" / password: 'x' / escaped quotes.
    (
        re.compile(
            r"(?i)((?:" + _ASSIGNMENT_KEYWORDS + r"|authorization)"
            r"(?:\\?[\"'])?\s*[:=]\s*)(\\?[\"'])(?:(?!\2)(?:\\.|[^\r\n\\]))+(\2)?"
        ),
        r"\1\2[REDACTED]\3",
    ),
    # Unquoted value: password=x. Never re-matches an already redacted value.
    (
        re.compile(
            r"(?i)((?:" + _ASSIGNMENT_KEYWORDS + r")\s*[:=]\s*)"
            r"(?!\\?[\"']?\[REDACTED\])\S+"
        ),
        r"\1[REDACTED]",
    ),
    # Authorization header value, including its scheme word.
    (
        re.compile(
            r"(?i)((?<![A-Za-z0-9_])authorization\s*[:=]\s*)(?!\\?[\"'])"
            r"(?:[A-Za-z]+\s+)?[A-Za-z0-9\-._~+/]+=*"
        ),
        r"\1[REDACTED]",
    ),
    *((pattern, REDACTION_MARKER) for pattern in SECRET_PATTERNS[1:]),
    (re.compile(r"gh[or]_[A-Za-z0-9_]{36,}"), REDACTION_MARKER),
    (re.compile(r"(?i)(?<![A-Za-z0-9_])sig=[A-Za-z0-9%+/=]{16,}"), REDACTION_MARKER),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), REDACTION_MARKER),
    (
        re.compile(
            r"(?i)(?<![A-Za-z0-9_])(?:AccountKey|SharedAccessKey)=[A-Za-z0-9+/=]{20,}"
        ),
        REDACTION_MARKER,
    ),
    (
        re.compile(r"(?i)(?<![A-Za-z0-9_])bearer\s+[A-Za-z0-9\-._~+/]{16,}=*"),
        REDACTION_MARKER,
    ),
]

#: Every ``SYNC_PATTERNS`` match contains one of these (same ``re`` case
#: folding), so a string without one is returned untouched without running
#: each pattern.
_SYNC_ANCHOR_RE = re.compile(
    r"(?i)token|secret|passw(?:or)?d|passphrase|api[_-]?key|credential"
    r"|private[ _-]?key|authorization|gh[pousr]_|github_pat_|bearer|eyj"
    r"|sig=|akia|asia|accountkey|sharedaccesskey"
)

_SENSITIVE_KEY_SUFFIXES = (
    "token", "secret", "password", "passwd", "passphrase", "apikey",
    "privatekey", "authorization", "credential", "credentials",
)


def redact_sync_text(text: str) -> tuple[str, bool]:
    """Apply ``SYNC_PATTERNS``; return ``(text, changed)``. Idempotent."""
    if not _SYNC_ANCHOR_RE.search(text):
        return text, False
    redacted = text
    for pattern, replacement in SYNC_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted, redacted != text


def normalize_key_name(key: str) -> str:
    return key.lower().replace("_", "").replace("-", "")


def is_sensitive_key(key: str) -> bool:
    """Whether a JSON object key names a credential (``access_token``...)."""
    return normalize_key_name(key).endswith(_SENSITIVE_KEY_SUFFIXES)


#: UTF-8 encodings of the non-ASCII characters that ``re`` case-insensitive
#: matching (or ``str.lower``) equates with an ASCII letter: U+0130, U+0131,
#: U+017F and U+212A. Raw bytes carrying one can spell a keyword that a
#: plain ASCII search misses.
CASEFOLD_SPECIAL_UTF8 = (b"\xc4\xb0", b"\xc4\xb1", b"\xc5\xbf", b"\xe2\x84\xaa")

_PREFILTER_STRIPPED = (
    b"token", b"secret", b"password", b"passwd", b"passphrase", b"apikey",
    b"credential", b"privatekey", b"private key", b"authorization", b"bearer",
    b"eyj", b"sig=", b"akia", b"asia", b"accountkey", b"sharedaccesskey",
    b"githubpat",
)
_PREFILTER_LOWERED = (b"ghp_", b"ghu_", b"ghs_", b"gho_", b"ghr_")
#: A JSON ``\\u`` escape that decodes to printable ASCII or a case-folding
#: special can spell a keyword the raw bytes do not show.
_RELEVANT_UNICODE_ESCAPE = re.compile(
    rb"\\u(?:00[2-7][0-9A-Fa-f]|013[01]|017[Ff]|212[Aa])"
)


def bytes_may_contain_secret(raw: bytes) -> bool:
    """Cheap, sound pre-check for one raw JSON text.

    ``False`` guarantees that no decoded string in *raw* would be changed by
    :func:`redact_sync_text` and no object key in it is
    :func:`is_sensitive_key`. It never decides validity.
    """
    lowered = raw.lower()
    if any(marker in lowered for marker in _PREFILTER_LOWERED):
        return True
    stripped = lowered.replace(b"_", b"").replace(b"-", b"")
    if any(marker in stripped for marker in _PREFILTER_STRIPPED):
        return True
    if any(special in raw for special in CASEFOLD_SPECIAL_UTF8):
        return True
    return _RELEVANT_UNICODE_ESCAPE.search(raw) is not None


#: Existence checks for non-text content. Every match is bounded well below
#: ``SCAN_OVERLAP`` so a shape split across two reads is still found.
BYTES_SCAN_PATTERNS = (
    re.compile(rb"gh[pousr]_[A-Za-z0-9_]{36}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{22}"),
    re.compile(rb"eyJ[A-Za-z0-9\-_]{20,1024}\.eyJ[A-Za-z0-9\-_]{20}"),
    re.compile(rb"-----BEGIN [A-Z ]{0,64}PRIVATE KEY-----"),
    re.compile(rb"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9])"),
    re.compile(
        rb"(?i)(?<![A-Za-z0-9_])(?:AccountKey|SharedAccessKey)=[A-Za-z0-9+/=]{20}"
    ),
    re.compile(rb"(?i)(?<![A-Za-z0-9_])sig=[A-Za-z0-9%+/=]{16}"),
    re.compile(rb"(?i)(?<![A-Za-z0-9_])bearer[ \t\r\n]{1,64}[A-Za-z0-9\-._~+/]{16}"),
    re.compile(
        rb"(?i)(?:token|secret|password|passwd|passphrase|apikey|api_key|api-key"
        rb"|credential|private[_-]?key)[ \t]{0,64}[:=][ \t]{0,64}[^\s\"']"
    ),
    re.compile(
        rb"(?i)(?:token|secret|password|passwd|passphrase|apikey|api_key|api-key"
        rb"|credential|private[_-]?key|authorization)\\?[\"'][ \t]{0,64}[:=]"
        rb"[ \t]{0,64}\\?[\"'](?!\\?[\"'])[^\r\n]"
    ),
)

SCAN_CHUNK = 1024 * 1024
SCAN_OVERLAP = 64 * 1024


def scan_stream_for_secret(
    source, sink=None, *, chunk_size: int = SCAN_CHUNK, overlap: int = SCAN_OVERLAP,
) -> bool:
    """Whether *source* (a binary stream) contains a ``BYTES_SCAN_PATTERNS``
    shape, reading bounded chunks.

    Scanned bytes are also handed to *sink* (when given) in the same pass,
    so a caller can copy while scanning; scanning stops at the first hit.
    Each window re-scans the previous ``overlap`` bytes, so a shape that
    spans a read boundary is still seen whole, and the last ``overlap``
    scanned bytes are held back from *sink* until the next window clears
    them -- no part of a detected shape ever reaches *sink*.
    """
    tail = b""
    pending = b""  # scanned bytes not yet released to *sink*
    offset = 0  # stream offset of the window's first byte
    while True:
        chunk = source.read(chunk_size)
        if not chunk:
            if sink is not None and pending:
                sink(pending)
            return False
        window = tail + chunk
        # Past the stream start, byte 0 only provides look-behind context;
        # the previous window already tried a match starting there.
        start = 0 if offset == 0 else 1
        if any(pattern.search(window, start) for pattern in BYTES_SCAN_PATTERNS):
            return True
        if sink is not None:
            ready = pending + chunk
            cut = max(0, len(ready) - overlap)
            if cut:
                sink(ready[:cut])
            pending = ready[cut:]
        tail = window[-overlap:]
        offset += len(window) - len(tail)
