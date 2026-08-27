"""Process-boundary runtime for plugin-contributed Picker pivots."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .plugin_contracts import PivotContract

LIST_TIMEOUT = 20
MAX_OUTPUT_BYTES = 5 * 1024 * 1024


class PivotLoadError(RuntimeError):
    """A contributed pivot command could not produce a usable snapshot."""


@dataclass(frozen=True)
class PivotPayload:
    rows: tuple[dict[str, object], ...]
    summary: Mapping[str, object]


def format_argv(
    template: Sequence[str],
    context: Mapping[str, object],
) -> list[str]:
    """Substitute whole argv tokens from Picker context."""

    class _Default(dict):
        def __missing__(self, key: str) -> str:
            return ""

    values = _Default({
        key: "" if value is None else str(value)
        for key, value in context.items()
    })
    argv: list[str] = []
    for item in template:
        try:
            argv.append(item.format_map(values))
        except (KeyError, IndexError, ValueError):
            argv.append(item)
    return argv


def parse_list_payload(
    data: object,
    *,
    items_field: str = "entries",
) -> PivotPayload:
    """Normalize the contract's bare-array and entries/summary shapes."""
    if isinstance(data, Mapping):
        raw_rows = data.get(items_field, [])
        raw_summary = data.get("summary", {})
        if not isinstance(raw_rows, list):
            raise PivotLoadError(
                f"list command returned a non-array `{items_field}` field")
        if not isinstance(raw_summary, Mapping):
            raise PivotLoadError("list command returned a non-object `summary` field")
    elif isinstance(data, list):
        raw_rows = data
        raw_summary = {}
    else:
        raise PivotLoadError("list command must return an array or object")

    rows = tuple(dict(row) for row in raw_rows if isinstance(row, Mapping))
    return PivotPayload(rows=rows, summary=dict(raw_summary))


def load_pivot(
    pivot: PivotContract,
    context: Mapping[str, object],
    *,
    timeout: int = LIST_TIMEOUT,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
) -> PivotPayload:
    """Run one pivot list command and parse its JSON snapshot."""
    argv = format_argv(pivot.list_cmd, context)
    if not argv:
        raise PivotLoadError("empty list command")
    executable = shutil.which(argv[0])
    if executable is None:
        raise PivotLoadError(f"{argv[0]} not found on PATH")
    argv[0] = executable

    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            proc = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout,
                check=False,
                env={**os.environ, "PYTHONUTF8": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            raise PivotLoadError(f"list command timed out after {timeout}s") from exc
        except OSError as exc:
            raise PivotLoadError(
                f"could not run {pivot.label} list command: {exc}"
            ) from exc

        if stdout.tell() > max_output_bytes or stderr.tell() > max_output_bytes:
            raise PivotLoadError(
                f"list command output exceeded {max_output_bytes} bytes"
            )
        stdout.seek(0)
        stderr.seek(0)
        stdout_text = stdout.read().decode("utf-8", errors="replace")
        stderr_text = stderr.read().decode("utf-8", errors="replace")

    if proc.returncode != 0:
        detail = stderr_text.strip().splitlines()
        message = detail[-1] if detail else f"exit {proc.returncode}"
        raise PivotLoadError(message[:200])
    try:
        data = json.loads(stdout_text or "[]")
    except ValueError as exc:
        raise PivotLoadError("list command did not print JSON") from exc
    return parse_list_payload(data, items_field=pivot.items_field)
