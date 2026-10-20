"""Opt-in content protection for filesystem session sync.

Two independent settings, honored only by the ``local`` and ``onedrive``
targets (every other target refuses to sync while either is enabled):

``sync.redact_secrets: true``
    Each published copy is derived from its source instead of copied.
    JSON Lines records and JSON documents are parsed, secret-shaped strings
    are replaced with ``[REDACTED]`` and only the changed string tokens are
    re-encoded, so every other byte is preserved and every record stays
    valid JSON. A JSON Lines file with an incomplete final record or a
    malformed record is *deferred* as a whole: its last published copy (or
    no copy) is kept, a sanitized reason is reported, and the next ordinary
    pass retries it. Other text files are redacted as a stream. Database
    files, and binary files that contain a known token shape, are
    *withheld* (not published) and listed. Nothing is ever truncated or
    published raw because of its size.

``sync.session_files: essential``
    Each session publishes only :data:`ESSENTIAL_SESSION_FILES`; every other
    file -- including kinds added in the future -- stays local until the
    list names it. The same filter drives change detection.

Both default off; with both off nothing in this module runs. Matching is
pattern-based and best-effort (see :mod:`agent_logger.secret_patterns`).
"""

from __future__ import annotations

import codecs
import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agent_logger.secret_patterns import (
    ASSIGNMENT_KEY_END_RE,
    PATTERN_SET_VERSION,
    PEM_BEGIN_RE,
    PEM_END_RE,
    REDACTION_MARKER,
    bytes_may_contain_secret,
    is_sensitive_key,
    redact_sync_text,
    scan_stream_for_secret,
)
from agent_logger.sync.rescue_validation import ALLOWED_MEMBERS

SESSION_FILES_ALL = "all"
SESSION_FILES_ESSENTIAL = "essential"
SESSION_FILES_CHOICES = (SESSION_FILES_ALL, SESSION_FILES_ESSENTIAL)

#: Targets that implement protection; the others refuse to run with it on.
PROTECTED_TARGETS = frozenset({"local", "onedrive"})

#: ``session_files: essential`` -- the rescue set plus review annotations.
ESSENTIAL_SESSION_FILES = (*ALLOWED_MEMBERS, "review-annotations.json")

#: Destination directory (under the machine root) for per-session receipts.
RECEIPT_DIR = ".session-sync-projection"
RECEIPT_SCHEMA = 1
#: Bump when the transform itself (not the pattern set) changes its output.
TRANSFORM_VERSION = 1

#: Read size for streamed text and the binary/text classification. Text is
#: redacted in segments cut only where no rule can match across the cut, so
#: the result equals redacting the whole text at once.
TEXT_CHUNK = 1024 * 1024
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_SUMMARY_ENTRIES = 10
MAX_SUMMARY_PATH_CHARS = 256
MAX_MEMBER_CHARS = 1024

PUBLISHED = "published"
DEFERRED = "deferred"
WITHHELD = "withheld"
STATES = (PUBLISHED, DEFERRED, WITHHELD)

REASON_INCOMPLETE_RECORD = "incomplete-record"
REASON_MALFORMED_RECORD = "malformed-record"
REASON_MALFORMED_DOCUMENT = "malformed-document"
REASON_REDACTION_INVALID = "redaction-invalid"
REASON_BINARY_SECRET = "binary-secret-shape"
REASON_DATABASE = "database-withheld"
REASONS = (
    REASON_INCOMPLETE_RECORD,
    REASON_MALFORMED_RECORD,
    REASON_MALFORMED_DOCUMENT,
    REASON_REDACTION_INVALID,
    REASON_BINARY_SECRET,
    REASON_DATABASE,
)

#: What a deferred or withheld file's destination currently holds.
PRIOR_COPY_NONE = "none"
PRIOR_COPY_PROTECTED = "protected"
PRIOR_COPY_UNPROTECTED = "unprotected"
PRIOR_COPIES = (PRIOR_COPY_NONE, PRIOR_COPY_PROTECTED, PRIOR_COPY_UNPROTECTED)

_UTF8_BOM = b"\xef\xbb\xbf"
_SQLITE_MAGIC = b"SQLite format 3\x00"
_WAL_MAGICS = (b"\x37\x7f\x06\x82", b"\x37\x7f\x06\x83")
_DATABASE_SUFFIXES = (
    ".db",
    ".db-wal",
    ".db-shm",
    ".db-journal",
    ".sqlite",
    ".sqlite3",
    ".sqlite-wal",
    ".sqlite-shm",
    ".sqlite-journal",
    ".sqlite3-wal",
    ".sqlite3-shm",
    ".sqlite3-journal",
)

_BLANK_LINE = re.compile(rb"[ \t\r\n]*")
_JSON_WS = re.compile(r"[ \t\r\n]*")
_LEADING_NUMBER = re.compile(r"[ \t\r\n]*[-0-9]")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_scanstring = json.decoder.scanstring

Sink = Callable[[bytes], object]


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProtectionSettings:
    """Resolved ``sync.redact_secrets`` / ``sync.session_files``."""

    redact_secrets: bool = False
    session_files: str = SESSION_FILES_ALL

    @property
    def essential_only(self) -> bool:
        return self.session_files == SESSION_FILES_ESSENTIAL

    @property
    def enabled(self) -> bool:
        return self.redact_secrets or self.essential_only

    def transform_fingerprint(self) -> str:
        """Identify the byte transform behind a protected copy (``""`` if none).

        Receipts compare this, so changing only ``session_files`` keeps every
        still-valid redacted copy, while a new pattern set or transform
        version re-derives them all.
        """
        if not self.redact_secrets:
            return ""
        return f"redact=v{PATTERN_SET_VERSION}.{TRANSFORM_VERSION}.{RECEIPT_SCHEMA}"

    def fingerprint(self) -> str:
        """Identify everything that changes what a publication contains."""
        value = f"files={self.session_files}"
        if self.redact_secrets:
            value += f";{self.transform_fingerprint()}"
        return value

    def describe(self) -> str:
        redact = "on" if self.redact_secrets else "off"
        return f"redact_secrets={redact}, session_files={self.session_files}"

    def member_included(self, member: str) -> bool:
        """Whether a session-relative POSIX path is published."""
        return not self.essential_only or member in ESSENTIAL_SESSION_FILES

    def path_included(self, rel: str) -> bool:
        """Whether a source-root-relative POSIX path is published.

        ``essential`` publishes session members only: provenance sidecars and
        the session index stay local.
        """
        if not self.essential_only:
            return True
        parts = rel.split("/")
        return (
            len(parts) >= 3
            and parts[0] == "session-state"
            and "/".join(parts[2:]) in ESSENTIAL_SESSION_FILES
        )


def resolve_protection_settings(sync_section: Any) -> ProtectionSettings:
    """Validate the two settings from the ``sync`` config section."""
    if not isinstance(sync_section, dict):
        return ProtectionSettings()
    redact = sync_section.get("redact_secrets")
    if redact is None:
        redact = False
    if not isinstance(redact, bool):
        raise ValueError("sync.redact_secrets must be true or false")
    files = sync_section.get("session_files")
    if files is None:
        files = SESSION_FILES_ALL
    normalized = files.strip().lower() if isinstance(files, str) else None
    if normalized not in SESSION_FILES_CHOICES:
        raise ValueError(
            "sync.session_files must be one of: " + ", ".join(SESSION_FILES_CHOICES)
        )
    return ProtectionSettings(redact_secrets=redact, session_files=normalized)


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    """Result of deriving one protected copy."""

    state: str = PUBLISHED
    reason: str | None = None
    records: int = 0
    redacted: int = 0
    largest_record: int = 0
    #: Whether the derived bytes differ from the source bytes.
    changed: bool = False

    @property
    def published(self) -> bool:
        return self.state == PUBLISHED

    def stop(self, state: str, reason: str) -> Outcome:
        self.state = state
        self.reason = reason
        return self


class SourceChangedError(Exception):
    """The source changed between the classification and transform passes."""


class BoundedReader:
    """Read at most *limit* bytes of *source* (a snapshot of its size)."""

    def __init__(self, source: Any, limit: int) -> None:
        self._source = source
        self._origin = source.tell()
        self._limit = max(0, limit)
        self._remaining = self._limit

    def _budget(self, size: int | None) -> int:
        if size is None or size < 0 or size > self._remaining:
            return self._remaining
        return size

    def read(self, size: int | None = -1) -> bytes:
        budget = self._budget(size)
        if budget <= 0:
            return b""
        data = self._source.read(budget)
        self._remaining -= len(data)
        return data

    def readline(self, size: int | None = -1) -> bytes:
        budget = self._budget(size)
        if budget <= 0:
            return b""
        data = self._source.readline(budget)
        self._remaining -= len(data)
        return data

    def rewind(self) -> None:
        """Restart at the original position with the original budget."""
        self._source.seek(self._origin)
        self._remaining = self._limit


def _rewind(reader: Any) -> None:
    rewind = getattr(reader, "rewind", None)
    if rewind is not None:
        rewind()
    else:
        reader.seek(0)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant: {name}")


#: Strict RFC 8259 validation: ``NaN``/``Infinity`` are rejected and integers
#: of any length are accepted (no ``int`` digit limit).
_VALIDATOR = json.JSONDecoder(parse_int=float, parse_constant=_reject_constant)


def is_valid_json(text: str) -> bool:
    """Whether *text* is exactly one JSON value (surrounding whitespace ok)."""
    try:
        _VALIDATOR.decode(text)
    except (ValueError, RecursionError):
        return False
    return True


def _encode_string(value: str) -> str:
    if _LONE_SURROGATE.search(value):
        return json.dumps(value)
    return json.dumps(value, ensure_ascii=False)


def redact_json_text(text: str) -> tuple[str, int]:
    """Redact secret-shaped strings inside one valid JSON text.

    Every string -- keys included, nested at any depth, after escape
    decoding -- passes through :func:`redact_sync_text`; a string value
    directly under a credential-named key (``access_token``...) is replaced
    whole. Only changed string tokens are re-encoded: key order, whitespace,
    number spelling and untouched escapes keep their exact bytes. Returns
    ``(text, number_of_strings_changed)``.
    """
    pieces: list[str] = []
    count = 0
    cursor = 0
    sensitive_value_at = -1
    length = len(text)
    quote = text.find('"')
    while quote != -1:
        value, end = _scanstring(text, quote + 1, True)
        after = _JSON_WS.match(text, end).end()
        if after < length and text[after] == ":":
            replacement, changed = redact_sync_text(value)
            next_sensitive = _JSON_WS.match(text, after + 1).end() if is_sensitive_key(value) else -1
        elif quote == sensitive_value_at:
            replacement = REDACTION_MARKER
            changed = bool(value) and value != REDACTION_MARKER
            next_sensitive = -1
        else:
            replacement, changed = redact_sync_text(value)
            next_sensitive = -1
        if changed:
            pieces.append(text[cursor:quote])
            pieces.append(_encode_string(replacement))
            cursor = end
            count += 1
        sensitive_value_at = next_sensitive
        quote = text.find('"', end)
    if not count:
        return text, 0
    pieces.append(text[cursor:])
    return "".join(pieces), count


def transform_jsonl(reader: Any, sink: Sink) -> Outcome:
    """Stream a JSON Lines file record by record into *sink*.

    Stops at the first record that cannot be proven complete and valid:
    the caller must then publish nothing for the file (the whole file is
    deferred). Blank lines pass through unchanged.
    """
    outcome = Outcome()
    first = True
    while True:
        line = reader.readline()
        if not line:
            return outcome
        terminated = line.endswith(b"\n")
        start = 0
        if first:
            first = False
            if line.startswith(_UTF8_BOM):
                start = len(_UTF8_BOM)
        if _BLANK_LINE.fullmatch(line, start):
            sink(line)
            continue
        outcome.records += 1
        outcome.largest_record = max(outcome.largest_record, len(line) - terminated - start)
        broken = REASON_MALFORMED_RECORD if terminated else REASON_INCOMPLETE_RECORD
        try:
            text = (line[start:] if start else line).decode("utf-8")
        except UnicodeDecodeError:
            return outcome.stop(DEFERRED, broken)
        if not is_valid_json(text):
            return outcome.stop(DEFERRED, broken)
        if not terminated and _LEADING_NUMBER.match(text):
            # A trailing number may still be growing: ``12`` of ``123``.
            return outcome.stop(DEFERRED, REASON_INCOMPLETE_RECORD)
        if bytes_may_contain_secret(line):
            redacted, count = redact_json_text(text)
            if count:
                if not is_valid_json(redacted):
                    return outcome.stop(DEFERRED, REASON_REDACTION_INVALID)
                try:
                    encoded = redacted.encode("utf-8")
                except UnicodeEncodeError:
                    return outcome.stop(DEFERRED, REASON_REDACTION_INVALID)
                sink(line[:start] + encoded if start else encoded)
                outcome.redacted += count
                outcome.changed = True
                continue
        sink(line)


def transform_json_document(raw: bytes, sink: Sink) -> Outcome:
    """Validate and redact one JSON document held in memory."""
    outcome = Outcome(largest_record=len(raw))
    start = len(_UTF8_BOM) if raw.startswith(_UTF8_BOM) else 0
    if _BLANK_LINE.fullmatch(raw, start):
        sink(raw)
        return outcome
    outcome.records = 1
    try:
        text = raw[start:].decode("utf-8")
    except UnicodeDecodeError:
        return outcome.stop(DEFERRED, REASON_MALFORMED_DOCUMENT)
    if not is_valid_json(text):
        return outcome.stop(DEFERRED, REASON_MALFORMED_DOCUMENT)
    if bytes_may_contain_secret(raw):
        redacted, count = redact_json_text(text)
        if count:
            if not is_valid_json(redacted):
                return outcome.stop(DEFERRED, REASON_REDACTION_INVALID)
            try:
                encoded = redacted.encode("utf-8")
            except UnicodeEncodeError:
                return outcome.stop(DEFERRED, REASON_REDACTION_INVALID)
            sink(raw[:start] + encoded)
            outcome.redacted = count
            outcome.changed = True
            return outcome
    sink(raw)
    return outcome


def _is_database(head: bytes) -> bool:
    return head.startswith(_SQLITE_MAGIC) or head[:4] in _WAL_MAGICS


class _TextProbe:
    """Incrementally decide whether a byte stream is UTF-8 text without NUL."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.text = True

    def feed(self, data: bytes, final: bool = False) -> None:
        if not self.text:
            return
        if b"\x00" in data:
            self.text = False
            return
        try:
            self._decoder.decode(data, final)
        except UnicodeDecodeError:
            self.text = False


class _ProbedReader:
    """Pass reads through, probing every byte that is read."""

    def __init__(self, reader: Any, probe: _TextProbe) -> None:
        self._reader = reader
        self._probe = probe

    def read(self, size: int | None = -1) -> bytes:
        data = self._reader.read(size)
        self._probe.feed(data)
        return data


def _read_up_to(reader: Any, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = reader.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# Streamed text redaction.
#
# Private key blocks are removed first by a small state machine that holds
# back only a possible, still incomplete BEGIN/END header. The remaining
# text is redacted in segments cut just after a newline. Apart from the key
# blocks, a sync rule can match across a newline only through a whitespace
# run (``\s*`` around ``[:=]``, after a scheme word, after ``Bearer``), so a
# cut is safe unless the text around that run could complete such a match.
# Every replacement keeps its first character or starts with ``[`` and ends
# with ``]`` or a quote, so an earlier rule never makes a later rule match
# across a cut that was safe in the input. Each segment therefore redacts
# exactly as it would inside the whole text.
# ---------------------------------------------------------------------------

_NON_SPACE = re.compile(r"\S")
_BEARER_END = re.compile(r"(?i)bearer\Z")
_AUTHORIZATION_END = re.compile(r"(?i)authorization\Z")
_TOKEN_CHAR = re.compile(r"(?i)[A-Za-z0-9\-._~+/]")
_LETTERS = re.compile(r"(?i)[A-Za-z]*")
_HEADER_REST = re.compile(r"[A-Z ]*-{0,4}")
_PEM_BEGIN_OPENER = "-----BEGIN "
_PEM_END_OPENER = "-----END "
#: Longer than any keyword (plus quote) the tail checks look for.
_TAIL_WINDOW = 64


def _tail_is(pattern: re.Pattern[str], text: str, hi: int) -> bool:
    """Whether ``text[:hi]`` ends with a match of *pattern* (``...\\Z``)."""
    return hi > 0 and pattern.search(text, max(0, hi - _TAIL_WINDOW), hi) is not None


def _last_non_space(text: str, lo: int, hi: int) -> int:
    """Index just past the last non-whitespace character in ``text[lo:hi]``,
    or *lo* when there is none."""
    pos = hi
    while pos > lo:
        start = max(lo, pos - 4096)
        stripped = text[start:pos].rstrip()
        if stripped:
            return start + len(stripped)
        pos = start
    return lo


def _letter_run_start(text: str, lo: int, hi: int) -> int:
    """Start of the run of (case-folded) ASCII letters ending at *hi*."""
    pos = hi
    while pos > lo:
        start = max(lo, pos - 256)
        window = text[start:pos][::-1]
        run = _LETTERS.match(window).end()  # type: ignore[union-attr]
        if run < len(window):
            return pos - run
        pos = start
    return lo


def _scheme_after_authorization(text: str, j: int) -> bool:
    """Whether ``text[:j]`` ends with ``authorization\\s*[:=]\\s*<letters>``."""
    run = _letter_run_start(text, 0, j)
    if run == j:
        return False
    k = _last_non_space(text, 0, run)
    if k == 0 or text[k - 1] not in ":=":
        return False
    return _tail_is(_AUTHORIZATION_END, text, _last_non_space(text, 0, k - 1))


def _cut_is_safe(text: str, j: int, nxt: int | None) -> bool:
    """Whether no rule can match across the whitespace run ``text[j:nxt]``.

    ``text[j - 1]`` is the last non-whitespace character before the run and
    ``text[nxt]`` the first after it (``None``: not read yet, so anything
    may follow). The checks are conservative supersets of the rules.
    """
    if text[j - 1] in ":=" and _tail_is(
        ASSIGNMENT_KEY_END_RE, text, _last_non_space(text, 0, j - 1)
    ):
        return False  # ``password:`` <run> value
    if (nxt is None or text[nxt] in ":=") and _tail_is(ASSIGNMENT_KEY_END_RE, text, j):
        return False  # ``password`` <run> ``: value``
    if nxt is None or _TOKEN_CHAR.match(text, nxt):
        if _tail_is(_BEARER_END, text, j) or _scheme_after_authorization(text, j):
            return False  # ``Bearer`` / ``Authorization: Basic`` <run> token
    return True


def _safe_cut(text: str) -> int:
    """The last position just after a newline where *text* can be split
    without changing what the sync rules match (0: none).

    *text* must start where the whole text starts or at an earlier safe cut.
    """
    pos = len(text)
    while True:
        newline = text.rfind("\n", 0, pos)
        if newline < 0:
            return 0
        cut = newline + 1
        j = _last_non_space(text, 0, cut)
        if j == 0:
            # Only whitespace since the previous cut, which was safe for
            # this same run.
            return cut
        found = _NON_SPACE.search(text, cut)
        if _cut_is_safe(text, j, found.start() if found else None):
            return cut
        # Every newline in this run shares its verdict; try an earlier run.
        pos = j


def _holdback(buf: str, opener: str) -> int:
    """Where a private key header that *buf* may still complete starts, or
    ``len(buf)`` when there is none (*opener* is ``-----BEGIN `` or
    ``-----END ``)."""
    start = buf.rfind(opener)
    if start >= 0 and _HEADER_REST.fullmatch(buf, start + len(opener)):
        return start
    for size in range(min(len(opener) - 1, len(buf)), 0, -1):
        if buf.endswith(opener[:size]):
            return len(buf) - size
    return len(buf)


class _TextRedactor:
    """Redact text fed in pieces exactly as :func:`redact_sync_text` would
    redact the whole text, writing UTF-8 to *sink* as it goes."""

    def __init__(self, sink: Sink, outcome: Outcome) -> None:
        self._sink = sink
        self._outcome = outcome
        #: Text not yet checked for a private key block.
        self._raw = ""
        self._in_key = False
        #: Key-free text waiting for a safe cut.
        self._pieces: list[str] = []
        self._pending = 0
        self._next_attempt = TEXT_CHUNK

    def feed(self, text: str) -> None:
        self._raw += text
        self._strip_keys(final=False)
        self._flush(final=False)

    def close(self) -> None:
        self._strip_keys(final=True)
        self._flush(final=True)

    def _push(self, text: str) -> None:
        if text:
            self._pieces.append(text)
            self._pending += len(text)

    def _strip_keys(self, final: bool) -> None:
        """Replace each private key block (to its END footer, or to the end
        of the text) with the marker, holding back an incomplete header."""
        raw = self._raw
        while True:
            if self._in_key:
                end = PEM_END_RE.search(raw)
                if end is None:
                    raw = "" if final else raw[_holdback(raw, _PEM_END_OPENER):]
                    break
                self._in_key = False
                raw = raw[end.end():]
                continue
            begin = PEM_BEGIN_RE.search(raw)
            if begin is None:
                keep = len(raw) if final else _holdback(raw, _PEM_BEGIN_OPENER)
                self._push(raw[:keep])
                raw = raw[keep:]
                break
            self._push(raw[: begin.start()])
            self._push(REDACTION_MARKER)
            self._outcome.redacted += 1
            self._outcome.changed = True
            self._in_key = True
            raw = raw[begin.end():]
        self._raw = raw

    def _emit(self, segment: str) -> None:
        redacted, changed = redact_sync_text(segment)
        if changed:
            added = redacted.count(REDACTION_MARKER) - segment.count(REDACTION_MARKER)
            self._outcome.redacted += max(1, added)
            self._outcome.changed = True
        self._sink(redacted.encode("utf-8"))

    def _flush(self, final: bool) -> None:
        if final:
            text = "".join(self._pieces)
            self._pieces = []
            self._pending = 0
            if text:
                self._emit(text)
            return
        if self._pending < self._next_attempt:
            return
        text = "".join(self._pieces)
        cut = _safe_cut(text)
        if cut:
            self._emit(text[:cut])
        rest = text[cut:]
        self._pieces = [rest] if rest else []
        self._pending = len(rest)
        # Without a safe cut, wait until the pending text doubles so that a
        # long unsplittable region is rescanned only O(log n) times.
        self._next_attempt = max(TEXT_CHUNK, 2 * len(rest))


def _scan_binary(source: Any, sink: Sink, outcome: Outcome) -> Outcome:
    probe = _TextProbe()
    if scan_stream_for_secret(_ProbedReader(source, probe), sink):
        return outcome.stop(WITHHELD, REASON_BINARY_SECRET)
    probe.feed(b"", final=True)
    if probe.text:
        # Classified as binary, read back as text: publishing it unredacted
        # could leak, so retry the file on the next pass.
        raise SourceChangedError("source changed from binary to text during copy")
    return outcome


def _redact_text(source: Any, sink: Sink, outcome: Outcome) -> Outcome:
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    redactor = _TextRedactor(sink, outcome)
    try:
        while True:
            chunk = source.read(TEXT_CHUNK)
            if not chunk:
                break
            if b"\x00" in chunk:
                raise SourceChangedError("source changed from text to binary during copy")
            redactor.feed(decoder.decode(chunk))
        redactor.feed(decoder.decode(b"", True))
    except UnicodeDecodeError as exc:
        raise SourceChangedError("source changed from text to binary during copy") from exc
    redactor.close()
    return outcome


def _transform_other(reader: Any, size: int, sink: Sink) -> Outcome:
    """Redact a text file as a stream, or scan a binary one.

    The file is classified first (UTF-8 without NUL is text), reading at
    most one chunk when it is binary or fits one chunk; a longer text file
    is read twice. Memory stays bounded by the chunk size plus the longest
    region that a rule could still match across (normally one line).
    """
    outcome = Outcome(largest_record=size)
    head = _read_up_to(reader, TEXT_CHUNK)
    if _is_database(head):
        return outcome.stop(WITHHELD, REASON_DATABASE)
    probe = _TextProbe()
    probe.feed(head)
    complete = len(head) < TEXT_CHUNK
    while not complete and probe.text:
        chunk = reader.read(TEXT_CHUNK)
        if not chunk:
            break
        probe.feed(chunk)
    probe.feed(b"", final=True)
    if complete:
        source: Any = io.BytesIO(head)
    else:
        _rewind(reader)
        source = reader
    if probe.text:
        return _redact_text(source, sink, outcome)
    return _scan_binary(source, sink, outcome)


def transform_file(member: str, reader: Any, size: int, sink: Sink) -> Outcome:
    """Derive the protected copy of one file, writing it to *sink*.

    *reader* must yield at most *size* bytes (see :class:`BoundedReader`)
    and, for files other than JSON and JSON Lines, support ``rewind()`` or
    ``seek(0)``. When the outcome is not :data:`PUBLISHED`, whatever reached
    *sink* must be discarded. :class:`SourceChangedError` means the source
    changed while it was read; nothing may be published for it.
    """
    name = member.rsplit("/", 1)[-1].lower()
    if name.endswith(_DATABASE_SUFFIXES):
        return Outcome().stop(WITHHELD, REASON_DATABASE)
    if name.endswith(".jsonl"):
        return transform_jsonl(reader, sink)
    if name.endswith(".json"):
        raw = _read_up_to(reader, size)
        if _is_database(raw):
            return Outcome().stop(WITHHELD, REASON_DATABASE)
        return transform_json_document(raw, sink)
    return _transform_other(reader, size, sink)


# ---------------------------------------------------------------------------
# Receipts: metadata-only records of what each destination copy came from
# ---------------------------------------------------------------------------


@dataclass
class Receipt:
    settings: str
    files: dict[str, dict[str, Any]] = field(default_factory=dict)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def safe_member(member: Any) -> bool:
    """Whether *member* is a plain relative POSIX path inside a session."""
    if not isinstance(member, str) or not member or len(member) > MAX_MEMBER_CHARS:
        return False
    if member.startswith("/") or "\\" in member or ":" in member:
        return False
    return all(part not in ("", ".", "..") for part in member.split("/"))


def _valid_entry(entry: Any) -> bool:
    if not isinstance(entry, dict) or entry.get("state") not in STATES:
        return False
    if not all(_is_int(entry.get(key)) for key in ("source_size", "source_mtime_ns")):
        return False
    has_dest = all(_is_int(entry.get(key)) for key in ("dest_size", "dest_mtime_ns"))
    if entry["state"] == PUBLISHED:
        return has_dest
    if entry.get("reason") not in REASONS:
        return False
    return entry.get("prior_copy") in PRIOR_COPIES


def decode_receipt(raw: bytes, session_id: str) -> Receipt | None:
    """Parse a receipt; ``None`` for anything absent, oversized or foreign."""
    if len(raw) > MAX_RECEIPT_BYTES:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if (
        not isinstance(data, dict)
        or data.get("schema") != RECEIPT_SCHEMA
        or data.get("session_id") != session_id
        or not isinstance(data.get("settings"), str)
        or not isinstance(data.get("files"), dict)
    ):
        return None
    files = {
        member: entry
        for member, entry in data["files"].items()
        if safe_member(member) and _valid_entry(entry)
    }
    return Receipt(settings=data["settings"], files=files)


def encode_receipt(session_id: str, settings: str, files: dict[str, dict[str, Any]]) -> bytes | None:
    """Serialize a receipt; ``None`` when it would exceed the size cap."""
    payload = {
        "schema": RECEIPT_SCHEMA,
        "session_id": session_id,
        "settings": settings,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": files,
    }
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return data if len(data) <= MAX_RECEIPT_BYTES else None


def reusable_entry(
    receipt: Receipt | None, fingerprint: str, member: str, source_size: int, source_mtime_ns: int
) -> dict[str, Any] | None:
    """The receipt entry still valid for an unchanged source, if any."""
    if receipt is None or receipt.settings != fingerprint:
        return None
    entry = receipt.files.get(member)
    if entry is None:
        return None
    if entry["source_size"] != source_size or entry["source_mtime_ns"] != source_mtime_ns:
        return None
    return entry


def _protected_destination(entry: dict[str, Any] | None) -> tuple[int, int] | None:
    if entry is None or not all(_is_int(entry.get(k)) for k in ("dest_size", "dest_mtime_ns")):
        return None
    if entry.get("state") == PUBLISHED or entry.get("prior_copy") == PRIOR_COPY_PROTECTED:
        return entry["dest_size"], entry["dest_mtime_ns"]
    return None


def classify_prior_copy(
    previous: dict[str, Any] | None, dest: tuple[int, int] | None
) -> str:
    """What the destination holds for a file that is not being published.

    *dest* is the destination file's ``(size, mtime_ns)``, or ``None`` when
    there is no regular file there.
    """
    if dest is None:
        return PRIOR_COPY_NONE
    if _protected_destination(previous) == dest:
        return PRIOR_COPY_PROTECTED
    return PRIOR_COPY_UNPROTECTED


def published_entry(
    source_size: int, source_mtime_ns: int, dest_size: int, dest_mtime_ns: int, outcome: Outcome
) -> dict[str, Any]:
    return {
        "state": PUBLISHED,
        "source_size": source_size,
        "source_mtime_ns": source_mtime_ns,
        "dest_size": dest_size,
        "dest_mtime_ns": dest_mtime_ns,
        "records": outcome.records,
        "redacted": outcome.redacted,
    }


def unpublished_entry(
    state: str,
    reason: str,
    source_size: int,
    source_mtime_ns: int,
    prior_copy: str,
    dest: tuple[int, int] | None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "state": state,
        "reason": reason,
        "source_size": source_size,
        "source_mtime_ns": source_mtime_ns,
        "prior_copy": prior_copy,
    }
    if prior_copy == PRIOR_COPY_PROTECTED and dest is not None:
        entry["dest_size"], entry["dest_mtime_ns"] = dest
    return entry


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def ascii_text(value: Any, limit: int = MAX_SUMMARY_PATH_CHARS) -> str:
    """Printable-ASCII rendering of *value*, so status stays small and inert."""
    text = str(value)[:limit]
    return "".join(char if " " <= char <= "~" else "?" for char in text)


def summarize(
    settings: ProtectionSettings,
    *,
    published: int,
    redacted: int,
    deferred: list[dict[str, str]],
    withheld: list[dict[str, str]],
    unselected: int = 0,
) -> dict[str, Any]:
    """Bounded, metadata-only status block for ``sync-meta.json``.

    *unselected* counts source files left local because ``session_files``
    does not list them.
    """

    def bounded(items: list[dict[str, str]]) -> list[dict[str, str]]:
        return [
            {key: ascii_text(value, MAX_SUMMARY_PATH_CHARS) for key, value in item.items()}
            for item in items[:MAX_SUMMARY_ENTRIES]
        ]

    return {
        "settings": settings.fingerprint(),
        "published_files": published,
        "unselected_files": unselected,
        "redacted_strings": redacted,
        "deferred_count": len(deferred),
        "withheld_count": len(withheld),
        "deferred": bounded(deferred),
        "withheld": bounded(withheld),
    }
