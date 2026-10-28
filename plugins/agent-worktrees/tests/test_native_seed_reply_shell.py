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


def test_reader_suppresses_private_fields_and_restores_caller_xtrace():
    private_argument = "private-argument-must-not-be-logged"
    private_environment = "private-environment-must-not-be-logged"
    prepared = _reply({"PRIVATE": private_environment})
    prepared["argv"].append(private_argument)
    result = _run(r'''
set -euo pipefail
. "$1"
set -x
aw_native_seed_read_reply
valid_flags="$-"
if aw_native_seed_read_reply; then exit 90; else code=$?; fi
[[ "$code" == 3 ]]
invalid_flags="$-"
set +x
printf '%s\n' "$valid_flags" "$invalid_flags"
''', payload=nul_reply(prepared).decode("utf-8"))
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.splitlines()) == 2
    assert all("x" in flags for flags in result.stdout.splitlines())
    assert private_argument not in result.stderr
    assert private_environment not in result.stderr
    assert "Native preparation reply is invalid; no backend started." in result.stderr


def test_reader_requires_real_eof_not_an_interrupted_or_failed_read(tmp_path):
    frame = tmp_path / "valid-frame"
    frame.write_bytes(nul_reply(_reply()))
    result = _run(r'''
set -euo pipefail
. "$1"
read() {
    local code=0
    builtin read "$@" || code=$?
    if [[ "${!#}" == extra && "$code" == 1 ]]; then return "$injected_status"; fi
    return "$code"
}
for injected_status in 1 2 130 138 142; do
    code=0
    aw_native_seed_read_reply < "$2" || code=$?
    printf '%s:%s\n' "$injected_status" "$code"
done
''', [frame.as_posix()])
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["1:0", "2:3", "130:3", "138:3", "142:3"]
    assert result.stderr.splitlines() == [
        "Native preparation reply is invalid; no backend started.",
    ] * 4


def test_invalid_frames_clear_previous_reply_without_evaluating_counts_or_leaking_data(tmp_path):
    forbidden = tmp_path / "must-not-run"
    valid = nul_reply(_reply({"SECRET": "must-not-appear"})).decode("utf-8")
    fields = valid[:-1].split("\0")
    malformed = [
        valid[:-1], valid + "tail", valid + "\0", "", valid.replace("complete", "unfinished"),
        valid.replace("complete", "COMPLETE"),
    ]
    for index, value in [
        (0, "unknown"), (0, fields[0].upper()), (1, ""), (2, "wrong-id"),
        (3, "A" * 32), (4, "wrong-parent"),
        (6, "-2"), (6, "01"), (6, "999999999999999999999999"),
        (6, f"index[$(touch '{forbidden.as_posix()}')]"), (7, ""), (7, "BAD=NAME"),
        (9, "0"), (9, "01"), (9, "1+1"), (10, ""),
    ]:
        changed = fields.copy()
        changed[index] = value
        malformed.append("\0".join(changed) + "\0")
    valid_path = tmp_path / "valid-frame"
    valid_path.write_bytes(valid.encode("utf-8"))
    paths = [valid_path.as_posix()]
    for index, frame in enumerate(malformed):
        path = tmp_path / f"frame-{index}"
        path.write_bytes(frame.encode("utf-8"))
        paths.append(path.as_posix())
    result = _run(r'''
set -euo pipefail
. "$1"
valid="$2"
shift 2
assert_mode() {
    local actual
    if shopt -q nocasematch; then actual=on; else actual=off; fi
    [[ "$actual" == "$nocase" ]]
}
original_locale="${LC_ALL-unset}"
for nocase in off on; do
    if [[ "$nocase" == on ]]; then shopt -s nocasematch; else shopt -u nocasematch; fi
    aw_native_seed_read_reply < "$valid"
    assert_mode
    [[ "${LC_ALL-unset}" == "$original_locale" ]]
    for frame in "$@"; do
        AW_NATIVE_SEED_ARGV=(previous)
        AW_NATIVE_SEED_ENV=(previous)
        AW_NATIVE_SEED_OPERATION=previous
        if aw_native_seed_read_reply < "$frame"; then exit 90; else code=$?; fi
        [[ $code == 3 && -z "$AW_NATIVE_SEED_OPERATION" && \
            -z "${AW_NATIVE_SEED_ARGV[0]+x}" && -z "${AW_NATIVE_SEED_ENV[0]+x}" ]]
        assert_mode
        [[ "${LC_ALL-unset}" == "$original_locale" ]]
    done
done
''', paths)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stdout
    assert result.stderr.splitlines() == [
        "Native preparation reply is invalid; no backend started.",
    ] * (len(malformed) * 2)
    assert not forbidden.exists()
