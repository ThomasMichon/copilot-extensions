#!/usr/bin/env python3
"""Write agent-containers guidance as a sessionStart side effect.

The existing command-catalog contributor remains a best-effort
additionalContext channel. This writer invokes it fresh and atomically writes
its output to the session-scoped guidance file.
"""

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
_GUIDANCE_HEADER = "# Agent Containers session guidance\n\n"


def _read_bounded_stdin() -> bytes:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    return b"" if len(raw) > _MAX_INPUT_BYTES else raw


def _additional_context(raw: bytes) -> str:
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


def _contributor_argv(root: Path, stem: str) -> list[str] | None:
    if os.name == "nt":
        script = root / "scripts" / f"{stem}.ps1"
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not shell or not script.is_file():
            return None
        return [shell, "-NoLogo", "-NoProfile", "-File", str(script)]
    script = root / "scripts" / f"{stem}.sh"
    shell = shutil.which("bash")
    if not shell or not script.is_file():
        return None
    return [shell, str(script)]


def _run_contributor(root: Path, stem: str, payload: bytes) -> str:
    argv = _contributor_argv(root, stem)
    if argv is None:
        return ""
    try:
        completed = subprocess.run(
            argv,
            input=payload,
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
            check=False,
            env={**os.environ, "PYTHONPATH": ""},
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return _additional_context(completed.stdout)


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
    if (
        not isinstance(session_id, str)
        or not _SESSION_IDENTIFIER.fullmatch(session_id)
    ):
        return False

    root = _plugin_root()
    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    contexts = [
        context
        for context in (
            _run_contributor(root, "emit-command-catalog", raw_payload),
        )
        if context
    ]
    if contexts:
        content = _GUIDANCE_HEADER + "\n\n".join(contexts) + "\n"
    else:
        content = (
            _GUIDANCE_HEADER
            + "No current agent-containers session guidance was available. Treat "
            "guidance as unavailable for this session-start invocation.\n"
        )
    if contexts and len(content.encode("utf-8")) > _GUIDANCE_MAX_BYTES:
        content = (
            _GUIDANCE_HEADER
            + "Current agent-containers session guidance was omitted because it "
            "exceeded the bounded file budget. Treat guidance as unavailable "
            "for this session-start invocation.\n"
        )

    temporary: Path | None = None
    try:
        copilot_root = home / ".copilot"
        copilot_root.mkdir(exist_ok=True)
        if _is_link_or_reparse(copilot_root):
            return False

        unresolved_state_root = copilot_root / "session-state"
        unresolved_state_root.mkdir(exist_ok=True)
        if _is_link_or_reparse(unresolved_state_root):
            return False
        state_root = unresolved_state_root.resolve()

        unresolved_session_root = state_root / session_id
        unresolved_session_root.mkdir(exist_ok=True)
        if _is_link_or_reparse(unresolved_session_root):
            return False
        session_root = unresolved_session_root.resolve()
        session_root.relative_to(state_root)

        instructions_dir = session_root / "instructions"
        instructions_dir.mkdir(exist_ok=True)
        if _is_link_or_reparse(instructions_dir):
            return False
        target_dir = instructions_dir / "agent-containers"
        target_dir.mkdir(exist_ok=True)
        if _is_link_or_reparse(target_dir):
            return False

        target = target_dir / "session-guidance.instructions.md"
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target_dir,
            prefix=".session-guidance.",
            suffix=".tmp",
            delete=False,
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
        if not isinstance(payload, dict):
            payload = {}
        write_session_guidance(payload)
    except Exception:
        pass
    sys.stdout.write("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())