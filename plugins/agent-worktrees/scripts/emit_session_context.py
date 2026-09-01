#!/usr/bin/env python3
"""Compose bounded, read-only agent-worktrees context for aggregation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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


def _serialize(context: str) -> str:
    return json.dumps(
        {"additionalContext": context},
        ensure_ascii=False,
        separators=(",", ":"),
    )


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
        if len(_serialize(candidate).encode("utf-8")) <= MAX_CONTEXT_BYTES:
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
    output = _serialize(context)
    if len(output.encode("utf-8")) > MAX_CONTEXT_BYTES:
        output = _serialize(owner)
    if len(output.encode("utf-8")) > MAX_CONTEXT_BYTES:
        output = "{}"
    return output


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
    jobs = {
        "marketplace": (
            scripts / "marketplace-overrides",
            payload,
            ("--await-context",),
            3.4,
        ),
        "binding": (
            scripts / "register-session",
            payload,
            ("--await-context",),
            3.4,
        ),
        "machine": (scripts / "session-machine", payload, (), 1.0),
        "conduct": (
            scripts / "session-conduct",
            payload,
            ("--aggregate",),
            2.0,
        ),
        "nudge": (
            scripts / "register-nudge",
            payload,
            ("--await-context",),
            3.4,
        ),
    }
    with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
        futures = {
            name: executor.submit(
                _run,
                script,
                job_payload,
                *args,
                deadline=deadline,
                timeout=timeout,
            )
            for name, (script, job_payload, args, timeout) in jobs.items()
        }
        raw_fragments = {
            name: future.result() for name, future in futures.items()
        }
    fragments = {
        **raw_fragments,
        "binding": _compact_binding(raw_fragments["binding"]),
        "machine": _compact_machine(raw_fragments["machine"]),
        "conduct": _strip_owner(raw_fragments["conduct"]),
    }
    sys.stdout.write(_compose(version, fragments))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
