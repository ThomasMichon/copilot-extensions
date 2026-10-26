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


def absolute_hint_file_argv(raw_args: list[str]) -> list[str]:
    """Preserve caller-relative hint paths when the hosted child changes cwd."""
    forwarded = list(raw_args)
    if not raw_args or raw_args[0] != "index":
        return forwarded
    for index, value in enumerate(raw_args):
        option, separator, path = value.partition("=")
        if option != "--" and option.startswith("--") and "--change-hints-file".startswith(option):
            if separator:
                forwarded[index] = option + "=" + str(Path(path).absolute())
            elif index + 1 < len(raw_args):
                forwarded[index + 1] = str(Path(raw_args[index + 1]).absolute())
    return forwarded


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
