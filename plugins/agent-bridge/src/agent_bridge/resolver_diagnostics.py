"""Bounded diagnostics and safe compatibility fallback for worktree resolution."""

from __future__ import annotations

import json
import os
import re

_INPUT_LIMIT = 65536
_SECRET_KEY = r"[\w-]*(?:token|secret|password|passwd|api[_-]?key|authorization|cookie|credential)[\w-]*"
_SECRET_ASSIGNMENT = re.compile(
    rf"""(?i)(\b{_SECRET_KEY}["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;]+)"""
)
_SECRET_ENV = re.compile(_SECRET_KEY, re.IGNORECASE)
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_UNKNOWN_OPTION = re.compile(
    r"(?:unrecognized arguments|no such option):[^\r\n]*--bridge\b",
    re.IGNORECASE,
)


def _safe_text(text: str, limit: int) -> str:
    text = text[:_INPUT_LIMIT]
    for name, value in os.environ.items():
        if value and _SECRET_ENV.fullmatch(name):
            text = text.replace(value, "[REDACTED]")
    text = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;\"']+", "Bearer [REDACTED]", text)
    text = re.sub(r"://[^/\s@]+@", "://[REDACTED]@", text)
    text = re.sub(
        r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b",
        "[REDACTED]",
        text,
    )
    text = _ANSI.sub("", text)
    text = " ".join(
        "".join(c for c in text if c.isprintable() or c.isspace()).split()
    )
    return text[:limit] + ("..." if len(text) > limit else "")


def structured_resolver_error(stdout: str) -> str | None:
    """Read only error fields, never launch plans or environment dictionaries."""
    text = stdout[-_INPUT_LIMIT:]
    decoder = json.JSONDecoder()
    cursor = 0
    for _ in range(32):
        start = text.find("{", cursor)
        if start < 0:
            return None
        try:
            envelope, end = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):
            cursor = start + 1
            continue
        cursor = end
        if not isinstance(envelope, dict):
            continue
        error = envelope.get("error")
        fields = envelope
        if isinstance(error, dict):
            fields = error
            error = error.get("message")
        if not isinstance(error, str) or not error.strip():
            continue
        message = _safe_text(error, 800)
        labels = [
            f"{key}={_safe_text(fields[key], 80)}"
            for key in ("stage", "code")
            if isinstance(fields.get(key), str) and fields[key].strip()
        ]
        return message + (f" ({', '.join(labels)})" if labels else "")
    return None


def resolver_failure_detail(stdout: str, stderr: str) -> str:
    """Prefer a structured error and append a separately bounded stderr hint."""
    error = structured_resolver_error(stdout)
    diagnostics = _safe_text(stderr, 400)
    if error:
        return error + (f"; stderr: {diagnostics}" if diagnostics else "")
    if diagnostics:
        return diagnostics
    # Do not echo opaque stdout: a launch-plan-shaped error response can carry
    # an environment dictionary. Missing diagnostics is preferable to a leak.
    return "resolver returned no structured error or stderr diagnostics"


def can_retry_legacy_resolve(
    *,
    exit_code: int,
    stdout: str,
    stderr: str,
    caller_owner_ref: str | None,
    caller_worktree: str | None,
) -> bool:
    """Only bridge-only, ownerless argparse skew may fall back to legacy flags."""
    return (
        not caller_owner_ref
        and not caller_worktree
        and exit_code == 2
        and structured_resolver_error(stdout) is None
        and _UNKNOWN_OPTION.search(stderr[:_INPUT_LIMIT]) is not None
    )
