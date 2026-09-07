#!/usr/bin/env python3
"""Write agent-index guidance as an exact-session side effect."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_SESSION_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")
_MAX_INPUT_BYTES = 64 * 1024
_GUIDANCE_MAX_BYTES = 4 * 1024
_SUBPROCESS_TIMEOUT_S = 10.0
_GUIDANCE_HEADER = "# Agent Index session guidance\n\n"


def _read_bounded_stdin() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    return b"" if len(raw) > _MAX_INPUT_BYTES else raw


def _producer_context(raw: bytes) -> str:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, TypeError, ValueError):
        return ""
    context = value.get("additionalContext") if isinstance(value, dict) else None
    return context.strip() if isinstance(context, str) else ""


def _plugin_root() -> Path:
    for name in ("COPILOT_PLUGIN_ROOT", "PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"):
        root = os.environ.get(name)
        if root:
            return Path(root)
    return Path(__file__).resolve().parents[1]


def _producer_argv(root: Path) -> list[str] | None:
    if os.name == "nt":
        script = root / "scripts" / "emit-command-catalog.ps1"
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        return (
            [shell, "-NoLogo", "-NoProfile", "-File", str(script)]
            if shell and script.is_file()
            else None
        )
    script = root / "scripts" / "emit-command-catalog.sh"
    shell = shutil.which("bash")
    return [shell, str(script)] if shell and script.is_file() else None


def _run_producer(root: Path, payload: bytes) -> str:
    argv = _producer_argv(root)
    if argv is None:
        return ""
    try:
        completed = subprocess.run(
            argv, input=payload, capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_S, check=False,
            env={**os.environ, "PYTHONPATH": ""},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return _producer_context(completed.stdout) if completed.returncode == 0 else ""


def _scope_binding_context(root: Path, payload: dict) -> str:
    script = root / "scripts" / "emit_scope_binding.py"
    if not script.is_file():
        return ""
    argv = [sys.executable, "-E", "-X", "utf8", str(script)]
    cwd = payload.get("cwd")
    if isinstance(cwd, str) and cwd and os.path.isabs(cwd) and os.path.isdir(cwd):
        argv.extend(["--cwd", cwd])
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True,
            timeout=_SUBPROCESS_TIMEOUT_S, check=False,
            env={**os.environ, "PYTHONPATH": ""},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    try:
        value = json.loads(completed.stdout)
    except (TypeError, ValueError):
        return ""
    context = value.get("additionalContext") if isinstance(value, dict) else None
    return context.strip() if isinstance(context, str) else ""


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return True
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def write_session_guidance(payload: dict, *, home: Path | None = None) -> bool:
    home = home or Path.home()
    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not _SESSION_IDENTIFIER.fullmatch(session_id):
        return False
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    contexts = []
    for context in (
        _run_producer(_plugin_root(), raw_payload),
        _scope_binding_context(_plugin_root(), payload),
    ):
        if context and context not in contexts:
            contexts.append(context)
    context = "\n\n".join(contexts)
    content = (
        _GUIDANCE_HEADER + context + "\n"
        if context
        else _GUIDANCE_HEADER
        + "No current agent-index session guidance was available. Treat "
        "guidance as unavailable for this session-start invocation.\n"
    )
    if context and len(content.encode("utf-8")) > _GUIDANCE_MAX_BYTES:
        content = (
            _GUIDANCE_HEADER
            + "Current agent-index session guidance was omitted because it "
            "exceeded the bounded file budget. Treat guidance as unavailable "
            "for this session-start invocation.\n"
        )
    temporary: Path | None = None
    try:
        copilot_root = home / ".copilot"
        copilot_root.mkdir(exist_ok=True)
        if _is_link_or_reparse(copilot_root):
            return False
        state_root = copilot_root / "session-state"
        state_root.mkdir(exist_ok=True)
        if _is_link_or_reparse(state_root):
            return False
        state_root = state_root.resolve()
        session_root = state_root / session_id
        session_root.mkdir(exist_ok=True)
        if _is_link_or_reparse(session_root):
            return False
        session_root = session_root.resolve()
        session_root.relative_to(state_root)
        instructions = session_root / "instructions"
        instructions.mkdir(exist_ok=True)
        if _is_link_or_reparse(instructions):
            return False
        target_dir = instructions / "agent-index"
        target_dir.mkdir(exist_ok=True)
        if _is_link_or_reparse(target_dir):
            return False
        target = target_dir / "session-guidance.instructions.md"
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=target_dir,
            prefix=".session-guidance.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, target)
        return True
    except (OSError, RuntimeError, ValueError):
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass
        return False


def main() -> int:
    try:
        raw = _read_bounded_stdin()
        payload = json.loads(raw) if raw.strip() else {}
        write_session_guidance(payload if isinstance(payload, dict) else {})
    except Exception:
        pass
    sys.stdout.write("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
