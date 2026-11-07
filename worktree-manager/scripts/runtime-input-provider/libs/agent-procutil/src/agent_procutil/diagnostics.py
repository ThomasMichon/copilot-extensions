"""Bounded subprocess diagnostics, extracted from Bridge resolver diagnostics."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping

_INPUT_LIMIT = 65536
_SECRET_KEY = r"[\w-]*(?:token|secret|password|passwd|api[_-]?key|authorization|cookie|credential)[\w-]*"
_SECRET_ASSIGNMENT = re.compile(
    rf"""(?i)(\b{_SECRET_KEY}["']?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\r\n]+)"""
)
_SECRET_ENV = re.compile(_SECRET_KEY, re.IGNORECASE)
_ANSI = re.compile(
    r"(?:\x1b\]|\x9d).*?(?:\x07|\x1b\\|\x9c|$)"
    r"|(?:\x1b[PX^_]|[\x90\x98\x9e\x9f]).*?(?:\x1b\\|\x9c|$)"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
    r"|\x1b[ -/]*[0-~]",
    re.DOTALL,
)
_JSON_FRAME = re.compile(r'(?m)^[ \t]*(?=[{\["])')


def _normalized_text(text: str) -> str:
    text = _ANSI.sub("", text[:_INPUT_LIMIT])
    return " ".join(
        "".join(c for c in text if c.isprintable() or c.isspace()).split()
    )


def _safe_text(
    text: str, limit: int, environment: Mapping[str, str] | None = None,
) -> str:
    # Cleanup must precede matching: otherwise it can reconstruct a sensitive
    # key or value only after the redactors have already inspected the text.
    text = _normalized_text(text)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer [REDACTED]", text)
    text = re.sub(r"://[^/\s@]+@", "://[REDACTED]@", text)
    text = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b",
        "[REDACTED]",
        text,
    )
    text = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", text)
    # Literal values can overlap labels or schemes, so replace them only after
    # the structural redactors have inspected the complete diagnostic.
    values = {
        _normalized_text(value)
        for source in (os.environ, environment or {})
        for name, value in source.items()
        if value and _SECRET_ENV.fullmatch(name)
    } - {""}
    if values:
        pattern = "|".join(re.escape(value) for value in sorted(values, key=len, reverse=True))
        text = re.sub(pattern, "[REDACTED]", text)
    return text[:limit] + ("..." if len(text) > limit else "")


def _json_objects(stdout: str):
    if len(stdout) > _INPUT_LIMIT:
        return
    text = stdout
    decoder = json.JSONDecoder()
    cursor = 0
    for _ in range(32):
        frame = _JSON_FRAME.search(text, cursor)
        if frame is None:
            return
        start = frame.end()
        try:
            envelope, end = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):
            # A failed outer decode cannot authorize searching inside its
            # nested objects, strings, or environment fields.
            return
        line_end = text.find("\n", end)
        suffix = text[end:line_end] if line_end >= 0 else text[end:]
        if suffix.strip():
            return
        cursor = end
        if not isinstance(envelope, dict):
            continue
        yield envelope


def structured_resolver_error(
    stdout: str, *, environment: Mapping[str, str] | None = None,
) -> str | None:
    """Read only error fields, never launch plans or environment dictionaries."""
    for envelope in _json_objects(stdout):
        error = envelope.get("error")
        fields = envelope
        if isinstance(error, dict):
            fields = error
            error = error.get("message")
        if not isinstance(error, str) or not error.strip():
            continue
        message = _safe_text(error, 800, environment)
        labels = [
            f"{key}={_safe_text(fields[key], 80, environment)}"
            for key in ("stage", "code")
            if isinstance(fields.get(key), str) and fields[key].strip()
        ]
        return message + (f" ({', '.join(labels)})" if labels else "")
    return None


def resolver_failure_detail(
    stdout: str, stderr: str, *, environment: Mapping[str, str] | None = None,
) -> str:
    """Prefer a structured error and append a separately bounded stderr hint."""
    error = structured_resolver_error(stdout, environment=environment)
    diagnostics = _safe_text(stderr, 400, environment)
    if error:
        return error + (f"; stderr: {diagnostics}" if diagnostics else "")
    if diagnostics:
        return diagnostics
    # Do not echo opaque stdout: a launch-plan-shaped error response can carry
    # an environment dictionary. Missing diagnostics is preferable to a leak.
    return "resolver returned no structured error or stderr diagnostics"


safe_diagnostic_text = _safe_text
normalized_diagnostic_text = _normalized_text


def structured_diagnostic_fields(
    stdout: str, *, environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Select bounded diagnostic strings, never arbitrary envelope contents."""
    for envelope in _json_objects(stdout):
        error = envelope.get("error")
        source = error if isinstance(error, dict) else envelope
        fields = {}
        if isinstance(error, str) and error.strip():
            fields["error"] = _safe_text(error, 800, environment)
        for key in ("message", "reason", "stage", "code"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                fields[key] = _safe_text(value, 800 if key == "message" else 160, environment)
        if fields:
            return fields
    return {}
