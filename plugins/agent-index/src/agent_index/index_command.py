"""Direct indexing CLI dispatch with explicit callable-bound hint execution."""

from __future__ import annotations

import argparse
import contextlib
import io
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .indexing.change_hints import ChangeHints
from .indexing.change_hints_contract import require_execution_v1, selected_engine, validate_scope


def run_index_command(
    args: argparse.Namespace,
    emit: Callable[[Any], int],
    emit_error: Callable[[Exception], int],
) -> int:
    """Execute legacy requests unchanged, never downgrading required hints."""
    try:
        hints_file = getattr(args, "change_hints_file", None)
        hints = (
            ChangeHints.from_json(Path(hints_file).read_text(encoding="utf-8"))
            if hints_file is not None else None
        )
        if hints is not None:
            validate_scope(args.source, args.full, hints)
        run_reindex = selected_engine()
        kwargs: dict[str, Any] = {"full": args.full, "source": args.source}
        if hints is not None:
            require_execution_v1(run_reindex)
            kwargs["change_hints"] = hints
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = run_reindex(**kwargs)
        emit(result)
        return 1 if result.get("sources_failed") else 0
    except Exception as exc:
        return emit_error(exc)
