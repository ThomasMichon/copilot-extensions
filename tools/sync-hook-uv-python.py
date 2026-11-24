#!/usr/bin/env python3
"""Vendor the canonical hook uv-Python helper into every adopting plugin.

Session hooks run before any plugin runtime exists, and plugins install
independently, so each adopting plugin must ship its own copy of the helper.
The canonical files live in ``libs/hook-uv-python/``. This script copies them
byte-identically into each adopter's ``scripts/`` directory.

``--check`` writes nothing. It fails when:

* a vendored copy is missing or differs from the canonical file;
* an adopter's ``write-session-guidance.sh`` (or ``.ps1``, when listed) does
  not call the helper, or still searches for a system Python;
* the helper's pinned version differs from the repository ``.python-version``.

Usage::

    tools/run-python tools/sync-hook-uv-python.py          # copy canonical -> plugins
    tools/run-python tools/sync-hook-uv-python.py --check  # verify (CI)
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
CANONICAL_DIR = REPO / "libs" / "hook-uv-python"
HELPER_NAMES = ("hook-uv-python.sh", "hook-uv-python.ps1")
HOOK_NAME = "write-session-guidance"

# plugin -> hook wrapper suffixes converted to the helper. agent-bridge and
# agent-mcp keep their PowerShell hooks on the materialized bootstrap selector.
ADOPTERS: dict[str, tuple[str, ...]] = {
    "agent-bridge": (".sh",),
    "agent-codespaces": (".sh", ".ps1"),
    "agent-containers": (".sh", ".ps1"),
    "agent-dispatch": (".sh", ".ps1"),
    "agent-index": (".sh", ".ps1"),
    "agent-machines": (".sh", ".ps1"),
    "agent-mcp": (".sh",),
    "agent-pull-requests": (".sh", ".ps1"),
    "agent-ssh": (".sh", ".ps1"),
    "agent-vault": (".sh", ".ps1"),
    "ai-attribution": (".sh", ".ps1"),
    "context-handoff": (".sh", ".ps1"),
}

CALL_MARKERS = {".sh": "hook_uv_python ", ".ps1": "Invoke-HookUvPython "}
SYSTEM_PYTHON_PATTERNS = {
    ".sh": re.compile(r"command -v python"),
    ".ps1": re.compile(r"@\('python3', 'python'"),
}
VERSION_PATTERNS = {
    "hook-uv-python.sh": re.compile(r"^HOOK_UV_PYTHON_VERSION=(\S+)$", re.M),
    "hook-uv-python.ps1": re.compile(r"^\$HookUvPythonVersion = '([^']+)'", re.M),
}


def pinned_version(repo: Path = REPO) -> str:
    pin = repo / ".python-version"
    text = pin.read_text(encoding="utf-8").strip() if pin.is_file() else ""
    match = re.match(r"(\d+\.\d+)", text)
    if not match:
        raise SystemExit(f"error: {pin} must name a Python major.minor version")
    return match.group(1)


def check(repo: Path = REPO) -> list[str]:
    problems: list[str] = []
    canonical_dir = repo / "libs" / "hook-uv-python"
    version = pinned_version(repo)
    for name in HELPER_NAMES:
        canonical = canonical_dir / name
        if not canonical.is_file():
            problems.append(f"missing canonical helper {canonical.relative_to(repo)}")
            continue
        match = VERSION_PATTERNS[name].search(canonical.read_text(encoding="utf-8"))
        if not match or match.group(1) != version:
            problems.append(
                f"{canonical.relative_to(repo)} pins "
                f"{match.group(1) if match else 'no version'}; .python-version is {version}"
            )
    for plugin, suffixes in ADOPTERS.items():
        scripts = repo / "plugins" / plugin / "scripts"
        for name in HELPER_NAMES:
            canonical = canonical_dir / name
            copy = scripts / name
            if not copy.is_file():
                problems.append(f"missing {copy.relative_to(repo)}")
            elif canonical.is_file() and copy.read_bytes() != canonical.read_bytes():
                problems.append(f"{copy.relative_to(repo)} differs from canonical")
        for suffix in suffixes:
            hook = scripts / f"{HOOK_NAME}{suffix}"
            if not hook.is_file():
                problems.append(f"missing adopter hook {hook.relative_to(repo)}")
                continue
            text = hook.read_text(encoding="utf-8")
            if CALL_MARKERS[suffix] not in text:
                problems.append(f"{hook.relative_to(repo)} does not call the uv-Python helper")
            if SYSTEM_PYTHON_PATTERNS[suffix].search(text):
                problems.append(f"{hook.relative_to(repo)} still searches for a system Python")
    return problems


def sync(repo: Path = REPO) -> int:
    canonical_dir = repo / "libs" / "hook-uv-python"
    written = 0
    for plugin in ADOPTERS:
        scripts = repo / "plugins" / plugin / "scripts"
        for name in HELPER_NAMES:
            data = (canonical_dir / name).read_bytes()
            copy = scripts / name
            if not copy.is_file() or copy.read_bytes() != data:
                copy.write_bytes(data)
                written += 1
            if name.endswith(".sh"):
                copy.chmod(copy.stat().st_mode | 0o111)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify only; write nothing")
    args = parser.parse_args(argv)
    if args.check:
        problems = check()
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            print(
                "Run: tools/run-python tools/sync-hook-uv-python.py "
                "(and wire each hook to the helper).",
                file=sys.stderr,
            )
            return 1
        print(f"hook-uv-python: {len(ADOPTERS)} adopters in sync")
        return 0
    written = sync()
    print(f"hook-uv-python: wrote {written} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
