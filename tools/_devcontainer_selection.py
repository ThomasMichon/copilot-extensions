"""Carry validated selection data, never a host bind, into the test container."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Callable

from plugin_test_selection import selection_from_payload, resolve_plugin_root

Canonicalize = Callable[[str], str]


def rewrite_selection(
    arguments: list[str], canonicalize: Canonicalize, replacement: str | None,
) -> tuple[list[str], str | None]:
    result = []
    selected = None
    iterator = iter(arguments)
    for argument in iterator:
        name, equal, value = argument.partition("=")
        if canonicalize(name) != "--selection-file":
            result.append(argument)
            continue
        if selected is not None:
            raise ValueError("--selection-file must be specified exactly once")
        selected = value if equal else next(iterator, "")
        if not selected or (not equal and selected.startswith("-")):
            raise ValueError(
                "--selection-file requires a path; use --selection-file=PATH "
                "for a path starting with '-'"
            )
        if replacement is not None:
            result.append("--selection-file=" + replacement)
    return result, selected


def read_selection(
    arguments: list[str], canonicalize: Canonicalize, repo: Path,
) -> bytes | None:
    _, path = rewrite_selection(arguments, canonicalize, None)
    if path is None:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    plugin = payload.get("plugin") if isinstance(payload, dict) else None
    if not isinstance(plugin, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", plugin):
        raise ValueError(
            "selection plugin must match [a-z0-9]+(-[a-z0-9]+)*, for example agent-dispatch"
        )
    selection = selection_from_payload(payload, plugin, resolve_plugin_root(repo, plugin))
    return json.dumps(selection.as_dict()).encode("utf-8")


def stage_selection(container_id: str, remote_user: str, payload: bytes) -> str:
    program = (
        "import sys,tempfile\n"
        "with tempfile.NamedTemporaryFile(prefix='ce-selection-',suffix='.json',dir='/tmp',delete=False) as f:\n"
        " f.write(sys.stdin.buffer.read())\n"
        " print(f.name)\n"
    )
    result = subprocess.run(
        ["docker", "exec", "-i", "-u", remote_user, container_id, "python", "-c", program],
        input=payload, capture_output=True, timeout=30, check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "could not stage exact test selection: " + result.stderr.decode(errors="replace")
        )
    path = result.stdout.decode().strip()
    if not re.fullmatch(r"/tmp/ce-selection-[a-zA-Z0-9_-]+\.json", path):
        raise RuntimeError("container returned an invalid selection path")
    return path
