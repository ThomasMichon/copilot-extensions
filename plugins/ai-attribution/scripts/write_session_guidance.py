#!/usr/bin/env python3
"""Write ai-attribution guidance as an output-free sessionStart side effect.

This writer invokes the plugin-owned policy producer and atomically writes the
result to the session-scoped file used by the static pointer projection.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_SESSION_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")
_MAX_INPUT_BYTES = 64 * 1024
_GUIDANCE_MAX_BYTES = 4 * 1024
_SUBPROCESS_TIMEOUT_S = 10.0
_GIT_SUBPROCESS_TIMEOUT_S = 5.0
_GUIDANCE_HEADER = "# AI attribution session guidance\n\n"

#: The full policy computation (spawn a fresh shell interpreter to run
#: emit-policy.*, which itself shells out to git and reads config files) costs
#: real per-session subprocess-spawn overhead for a result that is stable per
#: repository, not per session -- see copilot-extensions#2619's sibling
#: investigation. Cache the computed guidance per repo root, invalidated by a
#: bounded TTL rather than by replicating emit-policy's own config-precedence
#: resolution here (which would risk drifting out of sync with it). A cache
#: read/write failure of any kind always falls through to a full recompute; a
#: stale-but-unexpired cache is the only risk this introduces, bounded by the
#: TTL below.
_CACHE_FORMAT_VERSION = 1
_CACHE_TTL_SECONDS = int(os.environ.get("AI_ATTRIBUTION_CACHE_TTL_SECONDS", "3600"))
#: Escape hatch for an operator (or a future setup/refresh skill step) who
#: knows policy/config changed and does not want to wait out the TTL.
_FORCE_REFRESH_ENV = "AI_ATTRIBUTION_FORCE_REFRESH"


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


def _contributor_argv(root: Path) -> list[str] | None:
    if os.name == "nt":
        script = root / "scripts" / "emit-policy.ps1"
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not shell or not script.is_file():
            return None
        return [shell, "-NoLogo", "-NoProfile", "-File", str(script)]
    script = root / "scripts" / "emit-policy.sh"
    shell = shutil.which("bash")
    if not shell or not script.is_file():
        return None
    return [shell, str(script)]


def _run_contributor(root: Path, payload: bytes) -> str:
    argv = _contributor_argv(root)
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


def _repo_cache_key(cwd: str) -> str:
    """A stable per-repository cache key, keyed by the git toplevel when one
    can be resolved (grouping every subdirectory of the same repo onto one
    cache entry), falling back to the raw ``cwd`` string otherwise. The git
    call here is a single cheap process (typically well under 100ms), not the
    full emit-policy shell-script invocation this cache exists to avoid."""
    root = cwd
    try:
        completed = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=_GIT_SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        if completed.returncode == 0:
            candidate = completed.stdout.strip()
            if candidate:
                root = candidate
    except (OSError, subprocess.SubprocessError):
        pass
    digest = hashlib.sha256(root.encode("utf-8", "surrogateescape")).hexdigest()
    return digest[:16]


def _cache_dir(home: Path) -> Path:
    return home / ".copilot" / "ai-attribution-cache"


def _read_cached_guidance(home: Path, cwd: str) -> str | None:
    try:
        path = _cache_dir(home) / f"{_repo_cache_key(cwd)}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != _CACHE_FORMAT_VERSION:
        return None
    computed_at = data.get("computed_at")
    guidance = data.get("guidance")
    if not isinstance(computed_at, (int, float)) or not isinstance(guidance, str):
        return None
    if time.time() - computed_at > _CACHE_TTL_SECONDS:
        return None
    return guidance


def _write_cached_guidance(home: Path, cwd: str, guidance: str) -> None:
    try:
        directory = _cache_dir(home)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{_repo_cache_key(cwd)}.json"
        data = {
            "version": _CACHE_FORMAT_VERSION,
            "computed_at": time.time(),
            "guidance": guidance,
        }
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".ai-attribution-cache.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(data, handle)
        os.replace(temporary, path)
    except OSError:
        pass


def _run_contributor_cached(root: Path, payload: bytes, *, home: Path, cwd: str | None) -> str:
    """`_run_contributor`, but reused from a per-repository cache when one is
    fresh, so a session whose repository and config haven't changed since the
    last computation skips the (much costlier) shell-script invocation
    entirely. Never used when ``cwd`` is absent/invalid, or when the operator
    has set the force-refresh escape hatch."""
    force_refresh = bool(os.environ.get(_FORCE_REFRESH_ENV))
    if cwd and not force_refresh:
        cached = _read_cached_guidance(home, cwd)
        if cached is not None:
            return cached
    guidance = _run_contributor(root, payload)
    if cwd and guidance:
        _write_cached_guidance(home, cwd, guidance)
    return guidance


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

    raw_payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    payload_cwd = payload.get("cwd")
    cwd = payload_cwd if isinstance(payload_cwd, str) and payload_cwd else None
    guidance = _run_contributor_cached(_plugin_root(), raw_payload, home=home, cwd=cwd)
    if guidance:
        content = _GUIDANCE_HEADER + guidance + "\n"
    else:
        content = (
            _GUIDANCE_HEADER
            + "No current AI attribution guidance was available. Treat "
            "guidance as unavailable for this session-start invocation.\n"
        )
    if guidance and len(content.encode("utf-8")) > _GUIDANCE_MAX_BYTES:
        content = (
            _GUIDANCE_HEADER
            + "Current AI attribution guidance was omitted because it exceeded "
            "the bounded file budget. Treat guidance as unavailable for this "
            "session-start invocation.\n"
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
        target_dir = instructions_dir / "ai-attribution"
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
