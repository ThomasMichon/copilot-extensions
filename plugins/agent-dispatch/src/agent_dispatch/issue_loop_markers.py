"""Marker comment encode/decode helpers shared by repository issue loops.

Extracted from ``repository_issue_loops`` (kept module-size-neutral) so the
provider adapters can both compose an HTML-comment state marker and recover
the loop/occurrence/state it encodes from a previously posted comment body,
without duplicating the payload schema or its validation.
"""

from __future__ import annotations

import html
import json
import math
import re
from typing import Any

_MARKER_RE = re.compile(
    r"<!-- agent-dispatch:repository-issue-loop:v1 "
    r"(?P<payload>\{.*\}) -->"
)
# Azure DevOps work-item comments are stored/sanitized as HTML server-side,
# which strips HTML comments (`<!-- ... -->`) from the persisted `text`
# entirely -- confirmed by posting one against a real work item and reading
# it back with the comment silently gone. A bracket-delimited marker with no
# HTML-special characters survives that sanitization unchanged, so the
# Azure DevOps adapter uses this form instead; both forms are recognized on
# parse so either provider's history can be read back.
_MARKER_PLAIN_RE = re.compile(
    r"\[agent-dispatch:repository-issue-loop:v1 (?P<payload>\{.*\})\]"
)


def _marker(payload: dict[str, Any]) -> str:
    return (
        "<!-- agent-dispatch:repository-issue-loop:v1 "
        + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        + " -->"
    )


def _marker_plain(payload: dict[str, Any]) -> str:
    return (
        "[agent-dispatch:repository-issue-loop:v1 "
        + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        + "]"
    )


def validate_marker_payload(
    value: Any, *, issue_number: int
) -> dict[str, Any] | None:
    """Validate a decoded marker payload's ``loop``/``occurrence``/``state``/
    ``at``/``label``/``issue``/``task_id``/``reason`` schema, independent
    of where the payload came from -- a parsed comment body (:func:`_parse_marker`)
    or a script-provider-reported reservation object, which never passed
    through a comment at all and so needs the identical field-level
    validation applied directly."""
    if not isinstance(value, dict):
        return None
    if set(value) - {
        "loop",
        "occurrence",
        "state",
        "at",
        "label",
        "issue",
        "task_id",
        "reason",
    }:
        return None
    loop = value.get("loop")
    occurrence = value.get("occurrence")
    state = value.get("state")
    at = value.get("at")
    label = value.get("label")
    issue = value.get("issue")
    task_id = value.get("task_id")
    reason = value.get("reason")
    if (
        not isinstance(loop, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", loop)
        or isinstance(occurrence, bool)
        or not isinstance(occurrence, int)
        or occurrence < 0
        or state not in {"reserved", "claimed", "released"}
        or isinstance(at, bool)
        or not isinstance(at, (int, float))
        or (isinstance(at, float) and not math.isfinite(at))
        or not isinstance(label, str)
        or not label
        or isinstance(issue, bool)
        or issue != issue_number
    ):
        return None
    if isinstance(at, int):
        try:
            float(at)
        except OverflowError:
            # An arbitrarily large JSON/YAML integer (e.g. far beyond any
            # float's range) is itself a "finite" int, but downstream
            # arithmetic (`plan()`'s own `float(...)` conversion) still
            # needs a float -- reject it here as a malformed marker
            # instead of letting that conversion abort the tick later.
            return None
    if state == "claimed" and (
        not isinstance(task_id, str) or not task_id
    ):
        return None
    if state == "released" and (
        not isinstance(reason, str)
        or not reason
        or (task_id is not None and (not isinstance(task_id, str) or not task_id))
    ):
        return None
    if state == "reserved" and (task_id is not None or reason is not None):
        return None
    return dict(value)


def _parse_marker(
    body: str,
    *,
    author: str,
    expected_author: str,
    issue_number: int,
) -> dict[str, Any] | None:
    if author.casefold() != expected_author.casefold():
        return None
    # Azure DevOps returns comment `text` with HTML entities escaped (e.g.
    # `"` -> `&quot;`) even though the JSON payload's quotes were posted
    # literally -- confirmed by round-tripping a real comment. Unescaping is
    # a no-op for GitHub's plain-text comments, so it is safe to apply
    # unconditionally before matching either marker form.
    body = html.unescape(body)
    match = _MARKER_RE.search(body) or _MARKER_PLAIN_RE.search(body)
    if not match:
        return None
    try:
        value = json.loads(match.group("payload"))
    except ValueError:
        return None
    validated = validate_marker_payload(value, issue_number=issue_number)
    if validated is None:
        return None
    return {**validated, "comment_author": author}
