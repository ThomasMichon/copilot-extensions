"""The real hook preserves every staged path without oversized child argv."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "failed_guard", ["", "ruff", "check-skills.py", "check-effort-vision-structure.py"],
)
def test_pre_commit_batches_every_staged_path_and_keeps_failures(tmp_path, failed_guard):
    bash = shutil.which("bash")
    if os.name == "nt":
        git_path = shutil.which("git")
        git = Path(git_path).resolve() if git_path else None
        bash = next((
            str(path) for path in (
                git.parent.parent / "bin" / "bash.exe",
                git.parent / "bash.exe",
            ) if path.is_file()
        ), None) if git else None
    if bash is None:
        pytest.skip("native bash unavailable")
    root = tmp_path / "repo"
    hooks = root / "tools" / "hooks"
    hooks.mkdir(parents=True)
    hook = hooks / "pre-commit"
    shutil.copyfile(Path(__file__).parent / "hooks" / "pre-commit", hook)
    bins = tmp_path / "bin"
    bins.mkdir()
    log = tmp_path / "calls"
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    paths = {
        "python": [f"plugins/a long path/{index:04d}-{'x' * 80}.py" for index in range(400)],
        "skills": [f"plugins/skill {index}/SKILL.md" for index in range(70)],
        "docs": [f"efforts/active/effort {index}/README.md" for index in range(70)],
    }
    assert len(subprocess.list2cmdline(paths["python"])) > 32767
    for name, values in paths.items():
        (manifests / name).write_bytes(b"".join(value.encode() + b"\0" for value in values))
    git = """#!/usr/bin/env bash
kind=
for arg in "$@"; do
    case "$arg" in
        '*.py') kind=python ;;
        '*SKILL.md') kind=skills ;;
        'efforts/**/README.md') kind=docs ;;
    esac
done
[[ -n "$kind" ]] || exit 96
cat "$HOOK_MANIFESTS/$kind"
"""
    guard = """#!/usr/bin/env bash
guard=${0##*/}
if [[ "$guard" == ruff ]]; then
    shift 4
else
    guard=${1##*/}
    shift
fi
[[ "${1:-}" != -- ]] || shift
printf '%s\\0' "$guard" "$#" "$@" >> "$HOOK_LOG"
(( $# <= 32 )) || exit 97
[[ "$guard" != "$HOOK_FAIL" ]] || exit 23
"""
    for name, content in (("git", git), ("ruff", guard), ("python3", guard)):
        executable = bins / name
        executable.write_text(content, encoding="utf-8", newline="\n")
        executable.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(bins) + os.pathsep + os.environ["PATH"],
        "HOOK_LOG": str(log),
        "HOOK_MANIFESTS": str(manifests),
        "HOOK_FAIL": failed_guard,
        "HOOK_BIN": str(bins),
        "HOOK_FILE": str(hook),
    }
    env.pop("COPILOT_EXTENSIONS_HOOK_BASH", None)
    launch = (
        'export PATH="$(cygpath -u "$HOOK_BIN"):$PATH"; exec bash "$HOOK_FILE"'
        if os.name == "nt" else
        'export PATH="$HOOK_BIN:$PATH"; exec bash "$HOOK_FILE"'
    )
    result = subprocess.run(
        [bash, "-c", launch], cwd=root, env=env, capture_output=True, text=True,
        timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == bool(failed_guard), result.stdout + result.stderr
    values = log.read_bytes().split(b"\0")
    seen: dict[str, list[str]] = {}
    while values and values[0]:
        name, count = values[0].decode(), int(values[1])
        assert 0 < count <= 32
        seen.setdefault(name, []).extend(value.decode() for value in values[2:2 + count])
        values = values[2 + count:]
    assert seen == {
        "ruff": paths["python"],
        "check-skills.py": paths["skills"],
        "check-effort-vision-structure.py": paths["docs"],
    }
