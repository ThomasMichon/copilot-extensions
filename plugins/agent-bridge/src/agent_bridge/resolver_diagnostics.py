"""Bridge compatibility exports and its unchanged legacy-resolve retry policy."""

from __future__ import annotations

import re

from agent_procutil.diagnostics import (
    _INPUT_LIMIT,
    _normalized_text as _normalized_text,
    _safe_text as _safe_text,
    resolver_failure_detail as resolver_failure_detail,
    structured_resolver_error as structured_resolver_error,
)

_UNKNOWN_OPTION = re.compile(
    r"(?:unrecognized arguments|no such option):[^\r\n]*--bridge\b",
    re.IGNORECASE,
)


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
