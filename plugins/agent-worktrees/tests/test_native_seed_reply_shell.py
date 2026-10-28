"""Dormant Bash preparation reader; not terminal or live-removal qualification."""

from __future__ import annotations

import os
from pathlib import Path
import shutil

import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees.launch_seed_native import nul_reply

pytestmark = pytest.mark.guard
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "native-seed-reply.sh"


def _run(script, arguments=(), *, payload=""):
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    bash = str(git_bash) if os.name == "nt" and git_bash.is_file() else (
        shutil.which("bash") if os.name != "nt" else None
    )
    if not bash:
        pytest.skip("Bash is unavailable")
    return run_caller_owned(
        [bash, "--noprofile", "--norc", "-c", script, "reader", SCRIPT.as_posix(), *arguments],
        input_text=payload, timeout=15, max_stdout_bytes=8192, max_stderr_bytes=8192,
    )


def _reply(environment=None):
    return {
        "version": 1, "attempt_id": "a" * 32, "resolution_id": "b" * 32,
        "operation_id": "c" * 32, "parent_id": "d" * 32, "setup_seed_id": "seed-a",
        "environment": environment, "argv": ["copilot", ""],
    }


def test_reader_preserves_literal_fields_without_applying_environment_or_running_argv(tmp_path):
    forbidden = tmp_path / "must-not-run"
    prepared = _reply({"PATH": "not applied", "EMPTY": "", "ODD-NAME": "value=\n\u03a9"})
    prepared["argv"] += [
        f"$(touch '{forbidden.as_posix()}')", "' \" ; *", "2026-10-27T00:00:00Z", "\u03a9\n\n",
    ]
    result = _run(r'''
set -euo pipefail
. "$1"
original_path="$PATH"
aw_native_seed_read_reply
[[ "$PATH" == "$original_path" && -z "${EMPTY+x}" ]]
printf '%s\0' "$AW_NATIVE_SEED_ATTEMPT" "$AW_NATIVE_SEED_RESOLUTION" \
    "$AW_NATIVE_SEED_OPERATION" "$AW_NATIVE_SEED_PARENT" "$AW_NATIVE_SEED_SETUP_ID" \
    "$AW_NATIVE_SEED_ENV_COUNT" "${AW_NATIVE_SEED_ENV[@]}" "${AW_NATIVE_SEED_ARGV[@]}"
''', payload=nul_reply(prepared).decode("utf-8"))
    assert result.returncode == 0, result.stderr
    assert result.stdout.split("\0") == [
        prepared["attempt_id"], prepared["resolution_id"], prepared["operation_id"],
        prepared["parent_id"], prepared["setup_seed_id"], "3",
        *(f"{name}={value}" for name, value in prepared["environment"].items()),
        *prepared["argv"], "",
    ]
    assert not result.stderr and not forbidden.exists()


@pytest.mark.parametrize("environment,count", [(None, "-1"), ({}, "0")])
def test_reader_distinguishes_inherit_from_empty_replacement(environment, count):
    result = _run(r'''
set -euo pipefail
. "$1"
aw_native_seed_read_reply
[[ -z "${AW_NATIVE_SEED_ENV[0]+x}" ]]
printf '%s' "$AW_NATIVE_SEED_ENV_COUNT"
''', payload=nul_reply(_reply(environment)).decode("utf-8"))
    assert result.returncode == 0, result.stderr
    assert result.stdout == count and not result.stderr


def test_invalid_frames_clear_previous_reply_without_evaluating_counts_or_leaking_data(tmp_path):
    forbidden = tmp_path / "must-not-run"
    valid = nul_reply(_reply({"SECRET": "must-not-appear"})).decode("utf-8")
    fields = valid[:-1].split("\0")
    malformed = [valid[:-1], valid + "tail", valid + "\0", "", valid.replace("complete", "unfinished")]
    for index, value in [
        (0, "unknown"), (1, ""), (2, "wrong-id"), (3, "A" * 32), (4, "wrong-parent"),
        (6, "-2"), (6, "01"), (6, "999999999999999999999999"),
        (6, f"index[$(touch '{forbidden.as_posix()}')]"), (7, ""), (7, "BAD=NAME"),
        (9, "0"), (9, "01"), (9, "1+1"), (10, ""),
    ]:
        changed = fields.copy()
        changed[index] = value
        malformed.append("\0".join(changed) + "\0")
    paths = []
    for index, frame in enumerate(malformed):
        path = tmp_path / f"frame-{index}"
        path.write_bytes(frame.encode("utf-8"))
        paths.append(path.as_posix())
    result = _run(r'''
set -euo pipefail
. "$1"
shift
for frame in "$@"; do
    AW_NATIVE_SEED_ARGV=(previous)
    AW_NATIVE_SEED_ENV=(previous)
    AW_NATIVE_SEED_OPERATION=previous
    if aw_native_seed_read_reply < "$frame"; then exit 90; else code=$?; fi
    [[ $code == 3 && -z "$AW_NATIVE_SEED_OPERATION" && \
        -z "${AW_NATIVE_SEED_ARGV[0]+x}" && -z "${AW_NATIVE_SEED_ENV[0]+x}" ]]
done
''', paths)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stdout
    assert result.stderr.splitlines() == [
        "Native preparation reply is invalid; no backend started.",
    ] * len(malformed)
    assert not forbidden.exists()
