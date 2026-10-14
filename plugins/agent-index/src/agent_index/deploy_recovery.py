"""Render the native recovery outcome without disguising refused authority."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Any


def render_recovery(
    recovery: dict[str, Any], *, json_output: bool, recover_only: bool,
    emit: Callable[[dict[str, Any]], Any],
) -> int | None:
    if recovery.get("refused"):
        from zdd.breadcrumb import refusal_reason

        reason = refusal_reason(recovery)
        if json_output:
            emit({"ok": False, "error": reason, "recovery": recovery})
        else:
            print(f"[FAIL] {reason}", file=sys.stderr)
        return 1
    if recover_only:
        if json_output:
            emit(recovery)
        elif recovery.get("recovered"):
            print(f"[OK] {recovery.get('reason')}")
        else:
            print(f"[>] {recovery.get('reason')}")
        return 0
    if recovery.get("recovered") and not json_output:
        print(f"[>] Recovered a prior aborted cutover: {recovery.get('reason')}")
    return None
