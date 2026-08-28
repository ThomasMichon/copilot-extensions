#!/usr/bin/env python3
"""Emit effort-enforcement context for an adopting repository."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

MAX_PAYLOAD_BYTES = 65_536
MAX_CONFIG_BYTES = 4_096
MAX_MANIFEST_BYTES = 4_096
MAX_CONTEXT_BYTES = 1_024
CONFIG = Path(".copilot-extensions/efforts/config.json")
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-dev[0-9]+)?")
GIT_ENV_PREFIXES = ("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")
GIT_ENV_NAMES = {
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_PREFIX",
    "GIT_SUPER_PREFIX",
    "GIT_QUARANTINE_PATH",
    "GIT_NAMESPACE",
    "GIT_CONFIG",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_COUNT",
}


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    seen: set[str] = set()
    for key, value in pairs:
        normalized = key.casefold()
        if normalized in seen:
            raise ValueError("duplicate or case-conflicting object key")
        seen.add(normalized)
        result[key] = value
    return result


def _load_json(raw: bytes) -> object:
    return json.loads(
        raw.decode("utf-8", errors="strict"),
        object_pairs_hook=_strict_object,
    )


def _diagnostic(message: str) -> None:
    print(f"[efforts] {message}", file=sys.stderr)


def _emit_empty() -> int:
    sys.stdout.write("{}")
    return 0


def _read_bounded(path: Path, limit: int) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("file is not a bounded regular file")
        raw = os.read(descriptor, limit + 1)
        if len(raw) > limit or b"\0" in raw:
            raise ValueError("file is oversized or contains NUL")
        return raw
    finally:
        os.close(descriptor)


def _has_symlink(root: Path, relative: Path) -> bool:
    current = root
    for part in relative.parts:
        current = current / part
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except FileNotFoundError:
            return False
    return False


def _has_symlink_in_path(path: Path) -> bool:
    current = path
    while True:
        try:
            if stat.S_ISLNK(current.lstat().st_mode):
                return True
        except FileNotFoundError:
            return False
        if current == current.parent:
            return False
        current = current.parent


def _clean_git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in GIT_ENV_NAMES
        and not any(key.startswith(prefix) for prefix in GIT_ENV_PREFIXES)
    }
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    return environment


def _payload_cwd() -> Path | None:
    raw = sys.stdin.buffer.read(MAX_PAYLOAD_BYTES + 1)
    if len(raw) > MAX_PAYLOAD_BYTES or b"\0" in raw:
        _diagnostic("missing or malformed sessionStart payload; no policy context emitted")
        return None
    try:
        payload = _load_json(raw)
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        if (
            not isinstance(cwd, str)
            or not os.path.isabs(cwd)
            or any(ord(character) < 32 for character in cwd)
        ):
            raise ValueError
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        _diagnostic("missing or malformed sessionStart payload; no policy context emitted")
        return None
    path = Path(cwd)
    return path if path.is_dir() else None


def _repository_root(cwd: Path) -> Path | None:
    git = shutil.which("git")
    if not git:
        return None
    try:
        result = subprocess.run(
            [git, "-C", str(cwd), "rev-parse", "--show-toplevel"],
            env=_clean_git_environment(),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None
    if result.returncode != 0:
        return None
    root_text = result.stdout.strip()
    if not root_text or not os.path.isabs(root_text):
        return None
    root = Path(os.path.realpath(root_text))
    try:
        cwd_real = Path(os.path.realpath(cwd))
        if os.path.commonpath((root, cwd_real)) != str(root):
            return None
    except ValueError:
        return None
    return root


def _is_valid_config(raw: bytes) -> bool:
    try:
        data = _load_json(raw)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return False
    return (
        isinstance(data, dict)
        and set(data) == {"version", "enforcement"}
        and type(data["version"]) is int
        and data["version"] == 1
        and data["enforcement"] == "required"
    )


def _read_committed_config(root: Path) -> bytes | None:
    git = shutil.which("git")
    if not git:
        return None
    environment = _clean_git_environment()
    try:
        head_result = subprocess.run(
            [git, "-C", str(root), "rev-parse", "--verify", "HEAD"],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        head = head_result.stdout.strip()
        if head_result.returncode != 0 or re.fullmatch(r"[0-9a-fA-F]{40,64}", head) is None:
            return None
        tree_result = subprocess.run(
            [
                git,
                "-C",
                str(root),
                "ls-tree",
                head,
                "--",
                CONFIG.as_posix(),
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        tree_match = re.fullmatch(
            rf"(?:100644|100755) blob ([0-9a-fA-F]{{40,64}})\t"
            rf"{re.escape(CONFIG.as_posix())}\n?",
            tree_result.stdout,
        )
        if tree_result.returncode != 0 or tree_match is None:
            return None
        object_name = tree_match.group(1)
        size_result = subprocess.run(
            [git, "-C", str(root), "cat-file", "-s", object_name],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        size_text = size_result.stdout.strip()
        if (
            size_result.returncode != 0
            or not size_text.isascii()
            or not size_text.isdecimal()
            or int(size_text) > MAX_CONFIG_BYTES
        ):
            return None
        blob_result = subprocess.run(
            [git, "-C", str(root), "cat-file", "blob", object_name],
            env=environment,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
        return None
    if (
        blob_result.returncode != 0
        or len(blob_result.stdout) > MAX_CONFIG_BYTES
        or b"\0" in blob_result.stdout
    ):
        return None
    return blob_result.stdout


def _is_adopting(
    root: Path,
    *,
    diagnostics: bool = True,
    require_committed: bool = False,
) -> bool:
    path = root / CONFIG
    if not path.exists() and not path.is_symlink():
        return False
    if _has_symlink(root, CONFIG):
        if diagnostics:
            _diagnostic("repository effort config is not a contained regular file")
        return False
    try:
        raw = _read_bounded(path, MAX_CONFIG_BYTES)
    except (OSError, ValueError):
        if diagnostics:
            _diagnostic("repository effort config is malformed; no policy context emitted")
        return False
    if not _is_valid_config(raw):
        if diagnostics:
            _diagnostic("repository effort config is malformed; no policy context emitted")
        return False
    if not require_committed:
        return True
    committed = _read_committed_config(root)
    return committed is not None and _is_valid_config(committed)


def _plugin_version() -> str | None:
    manifest = Path(__file__).resolve().parent.parent / "plugin.json"
    try:
        raw = _read_bounded(manifest, MAX_MANIFEST_BYTES)
        data = _load_json(raw)
        version = data.get("version") if isinstance(data, dict) else None
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(version, str)
        or len(version) > 64
        or VERSION_PATTERN.fullmatch(version) is None
    ):
        return None
    return version


def _kernel(version: str) -> str:
    return (
        f"[owner: efforts@{version}]\n"
        "Efforts are required. For substantial multi-step work, use "
        "`planning-efforts` to create or resume the canonical effort, not a new "
        "plan document. Review its plan before implementation and execute in "
        "waves. Only the rightful head drives the next slice; superseded sessions "
        "assist or hand off. Continue until the effort is explicitly Done and "
        "each Plan and Validation Plan item is resolved or transferred to a named "
        "tracked objective. A completed phase, PR, "
        "handoff, or session is not completion. Pause only for genuine "
        "uncertainty, prerequisites, required review, or required "
        "safety/admin confirmation. Handoffs name the effort and next slice; "
        "bounded predecessor ramp-up covers only immediate activity. Keep "
        "cross-repo planning host-owned by default. Valid adoption only permits "
        "an explicitly selected target-owned sub-effort referenced one-way."
    )


def _emit_adoption_capability(raw_target: str) -> int:
    if (
        not raw_target
        or not os.path.isabs(raw_target)
        or any(ord(character) < 32 for character in raw_target)
    ):
        return _emit_empty()
    target = Path(raw_target)
    if _has_symlink_in_path(target) or not target.is_dir():
        return _emit_empty()
    root = _repository_root(target)
    if root is None or not _is_adopting(
        root,
        diagnostics=False,
        require_committed=True,
    ):
        return _emit_empty()
    sys.stdout.write(
        '{"version":1,"capability":"efforts","adopted":true}'
    )
    return 0


def main() -> int:
    if len(sys.argv) > 1:
        if len(sys.argv) == 3 and sys.argv[1] == "--check-adoption":
            return _emit_adoption_capability(sys.argv[2])
        _diagnostic("invalid arguments; no policy context emitted")
        return _emit_empty()
    cwd = _payload_cwd()
    if cwd is None:
        return _emit_empty()
    root = _repository_root(cwd)
    if root is None or not _is_adopting(root):
        return _emit_empty()
    version = _plugin_version()
    if version is None:
        _diagnostic("plugin manifest is missing or malformed; no policy context emitted")
        return _emit_empty()
    context = _kernel(version)
    if len(context.encode("utf-8")) >= MAX_CONTEXT_BYTES:
        _diagnostic("policy context exceeds its byte budget; no policy context emitted")
        return _emit_empty()
    sys.stdout.write(
        json.dumps(
            {"additionalContext": context},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
