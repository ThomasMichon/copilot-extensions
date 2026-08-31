#!/usr/bin/env python3
"""Compose bounded, read-only agent-worktrees context for aggregation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


MAX_CONTEXT_BYTES = 1_600
_DISPLAY_ORDER = (
    "marketplace",
    "binding",
    "machine",
    "conduct",
    "nudge",
)
_ADMISSION_ORDER = (
    "binding",
    "conduct",
    "machine",
    "marketplace",
    "nudge",
)


def _context(raw: str) -> str:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return ""
    context = value.get("additionalContext") if isinstance(value, dict) else None
    return context.strip() if isinstance(context, str) else ""


def _compact_binding(context: str) -> str:
    marker = "[agent-worktrees] This Copilot session"
    offset = context.rfind(marker)
    return context[offset:] if offset >= 0 else context


def _compact_machine(context: str) -> str:
    keep = {
        "Machine",
        "Hostname",
        "Environment",
        "Platform",
        "Role",
        "Project",
        "Binstub",
    }
    lines = [
        line.strip()
        for line in context.splitlines()
        if ":" in line and line.split(":", 1)[0].strip() in keep
    ]
    return "\n".join(lines)


def _strip_owner(context: str) -> str:
    lines = context.splitlines()
    if lines and lines[0].startswith("[owner: agent-worktrees@"):
        return "\n".join(lines[1:]).strip()
    return context


def _compose(version: str, fragments: dict[str, str]) -> str:
    owner = f"[owner: agent-worktrees@{version}]"
    selected: set[str] = set()
    for name in _ADMISSION_ORDER:
        fragment = fragments.get(name, "").strip()
        if not fragment:
            continue
        candidate = "\n\n".join(
            [
                owner,
                *[
                    fragments[item].strip()
                    for item in _DISPLAY_ORDER
                    if item in selected or item == name
                ],
            ]
        )
        if len(candidate.encode("utf-8")) <= MAX_CONTEXT_BYTES:
            selected.add(name)
    context = "\n\n".join(
        [
            owner,
            *[
                fragments[name].strip()
                for name in _DISPLAY_ORDER
                if name in selected
            ],
        ]
    )
    return json.dumps(
        {"additionalContext": context},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _command(script: Path, *args: str) -> list[str] | None:
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if powershell is None:
            return None
        return [
            powershell,
            "-NoProfile",
            "-File",
            str(script.with_suffix(".ps1")),
            *args,
        ]
    return ["bash", str(script.with_suffix(".sh")), *args]


def _run(
    script: Path,
    payload: bytes,
    *args: str,
    deadline: float,
    timeout: float = 1.0,
) -> str:
    command = _command(script, *args)
    if command is None:
        return ""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return ""
    try:
        result = subprocess.run(
            command,
            input=payload,
            capture_output=True,
            timeout=min(timeout, remaining),
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    try:
        return _context(result.stdout.decode("utf-8", errors="strict"))
    except UnicodeDecodeError:
        return ""


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
        version = manifest["version"]
        if not isinstance(version, str) or not version:
            raise ValueError
        payload = sys.stdin.buffer.read(64 * 1024 + 1)
        if len(payload) > 64 * 1024:
            raise ValueError
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        sys.stdout.write("{}")
        return 0

    scripts = root / "scripts"
    deadline = time.monotonic() + 3.5
    fragments = {
        "marketplace": _run(
            scripts / "marketplace-overrides",
            payload,
            "--context-only",
            deadline=deadline,
        ),
        "binding": _compact_binding(
            _run(
                scripts / "register-session",
                payload,
                "--context-only",
                deadline=deadline,
            )
        ),
        "machine": _compact_machine(
            _run(scripts / "session-machine", payload, deadline=deadline)
        ),
        "conduct": _strip_owner(
            _run(
                scripts / "session-conduct",
                payload,
                "--aggregate",
                deadline=deadline,
                timeout=2.0,
            )
        ),
        "nudge": _run(
            scripts / "register-nudge",
            payload,
            "--context-only",
            deadline=deadline,
        ),
    }
    sys.stdout.write(_compose(version, fragments))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
