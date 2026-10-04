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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_SESSION_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")
_MAX_INPUT_BYTES = 64 * 1024
_GUIDANCE_MAX_BYTES = 4 * 1024
# The catalog producer's gate check (resolve_effective_config.py --check) does
# real work (parsing federated corpus config across every adopted local
# project) and has been measured taking ~10-11s on a real repo -- the OLD
# 10.0s budget here was tighter than what the script actually needs, so it
# reliably timed out and silently returned "" on EVERY session, making the
# whole command catalog (and therefore agent-index itself) invisible to every
# agent turn without a single visible error anywhere. The sessionStart hook
# itself (hooks.json) budgets 45s total; 20s per producer leaves headroom even
# running both sequentially, and _run_both_producers below runs them
# concurrently instead, so the realistic worst case is ~20s, not ~40s.
_SUBPROCESS_TIMEOUT_S = 20.0
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


def _run_producer(root: Path, payload: dict) -> str:
    """Run the command-catalog producer script.

    Does NOT pipe the session payload as this subprocess's stdin -- the
    producer script never reads it (it only needs ``cwd``, passed via the
    ``cwd=`` kwarg below). Piping unused bytes as ``input=`` here used to
    spawn the producer with an inherited, open stdin pipe; on Windows its own
    NESTED child process (``resolve_effective_config.py --check``, spawned
    via a plain PowerShell ``&``) inherited that SAME pipe handle without the
    producer ever redirecting it, so the outer ``subprocess.run().communicate()``
    call deadlocked waiting for a pipe its grandchild still held open --
    silently timing out after ``_SUBPROCESS_TIMEOUT_S`` on EVERY session,
    every time, on Windows. Explicit ``stdin=subprocess.DEVNULL`` guarantees no
    pipe handle is available to inherit at any depth; explicit ``cwd=`` (the
    session's real cwd, when it's a valid absolute directory) also makes this
    robust to the hook process's own working directory not matching the
    session's logical cwd -- the producer script's own ``Get-Location`` naturally
    reflects whatever directory the OS actually started it in.
    """
    argv = _producer_argv(root)
    if argv is None:
        return ""
    cwd = payload.get("cwd")
    resolved_cwd = cwd if isinstance(cwd, str) and cwd and os.path.isdir(cwd) else None
    try:
        completed = subprocess.run(
            argv, stdin=subprocess.DEVNULL, capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_S, check=False,
            env={**os.environ, "PYTHONPATH": ""}, cwd=resolved_cwd,
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


def _run_both_producers(root: Path, payload: dict) -> tuple[str, str]:
    """Run the two guidance producers CONCURRENTLY, not sequentially.

    Each does its own blocking ``subprocess.run`` (which releases the GIL
    while waiting), so a thread pool is enough to overlap them -- no process
    isolation needed. Previously they ran one after another as plain tuple
    elements, so a worst case of both legitimately taking close to
    ``_SUBPROCESS_TIMEOUT_S`` summed to roughly double that, eating most of
    the sessionStart hook's own 45s budget before any file-write work even
    started. Running them in parallel keeps the realistic worst case at
    roughly ONE producer's timeout, not the sum of both.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        future1 = pool.submit(_run_producer, root, payload)
        future2 = pool.submit(_scope_binding_context, root, payload)
        return future1.result(), future2.result()


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
    contexts = []
    for context in _run_both_producers(_plugin_root(), payload):
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
