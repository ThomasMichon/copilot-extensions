"""Finite copied POSIX transport, not production backend/terminal activation."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

import pytest
from agent_procutil.owned_process import run_caller_owned

from agent_worktrees.launch_seed_native import nul_reply

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "native-seed-boundary.sh"


def _bash() -> str:
    if os.name == "nt":
        candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
        if candidate.is_file():
            return str(candidate)
    elif native := shutil.which("bash"):
        return native
    pytest.skip("Bash is unavailable")


def _slot(root: Path, version: str, *, exit_code: int = 0, tail: str = "") -> bytes:
    slot = root / "versions" / version
    (slot / "bin").mkdir(parents=True)
    (slot / ".runtime-bootstrap").mkdir()
    (slot / ".runtime-bootstrap" / "runtime_launcher.py").write_text("# fixture\n", encoding="utf-8")
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    fields = nul_reply({
        "version": 1, "attempt_id": "a" * 32, "resolution_id": "b" * 32,
        "operation_id": "c" * 32, "parent_id": None, "setup_seed_id": "",
        "environment": {"PRIVATE": "private value\n\n", "EMPTY": "", "ODD-NAME": "v=1"},
        "argv": [version, "", "single ' double \" ; $literal", "\u03a9\n\n"],
    }).decode("utf-8").split("\0")[:-1]
    python = slot / "bin" / "python"
    python.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'trap \'printf "%s" "$?" > "${0%/versions/*}/provider-status"\' EXIT\n'
        '[[ -z "${PYTHONPATH+x}${PYTHONHOME+x}${VIRTUAL_ENV+x}${__PYVENV_LAUNCHER__+x}" ]]\n'
        '[[ "$(cat)" == "" ]]\n'
        'printf "%s\\0" "$PWD" "$@" > "${0%/versions/*}/observed"\n'
        "printf '%s\\0' " + " ".join(map(shlex.quote, fields)) + "\n"
        + tail + f"\nexit {exit_code}\n",
        encoding="utf-8", newline="\n",
    )
    python.chmod(0o700)
    return b"\0".join(field.encode("utf-8") for field in fields) + b"\0"


def _shell_path(path: Path) -> str:
    value = path.as_posix()
    return f"/{path.drive[0].lower()}{value[2:]}" if os.name == "nt" and path.drive else value


def _run(
    script: str, arguments: list[Path | str], *, payload: str = "", script_path: Path = SCRIPT,
) -> subprocess.CompletedProcess[str]:
    return run_caller_owned(
        [_bash(), "--noprofile", "--norc", "-c", script, "phase", _shell_path(script_path),
         *(_shell_path(argument) if isinstance(argument, Path) else argument for argument in arguments)],
        input_text=payload, timeout=15, max_stdout_bytes=8192, max_stderr_bytes=8192,
    )


def test_finite_phase_streams_exact_reply_and_returns_provider_status(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    expected = _slot(root, "A")
    (root / "current-version").write_text("A\n", encoding="utf-8", newline="\n")
    result = _run('. "$1"; aw_native_seed_phase "$2" --phase prepare --output-format nul', [root])
    assert result.returncode == 0, result.stderr
    assert result.stdout.encode("utf-8") == expected
    assert not result.stderr


def test_finite_phase_preserves_pipe_fields_cwd_stdin_and_caller_descriptors(tmp_path: Path) -> None:
    root = tmp_path / "runtime with spaces"
    _slot(root, "A")
    (root / "current-version").write_text("A\n", encoding="utf-8", newline="\n")
    caller_input = tmp_path / "caller-input"
    caller_input.write_text("retained\n", encoding="utf-8", newline="\n")
    result = _run(r'''
set -euo pipefail
. "$1"
cd "$3"
export PYTHONPATH=invalid PYTHONHOME=invalid VIRTUAL_ENV=invalid __PYVENV_LAUNCHER__=invalid
exec 3<"$4"
aw_native_seed_prepare "$2" --phase prepare --output-format nul --record "record with spaces" -- "literal" ""
IFS= read -r retained <&3
[[ "$retained" == retained && "$PYTHONPATH" == invalid && -z "${PRIVATE+x}" ]]
IFS= read -r retained
[[ "$retained" == "caller stdin" ]]
printf '%s\0' "$AW_NATIVE_SEED_ENV_COUNT" "${AW_NATIVE_SEED_ENV[@]}" "${AW_NATIVE_SEED_ARGV[@]}"
''', [root, tmp_path, caller_input], payload="caller stdin\n")
    status_path = root / "provider-status"
    assert result.returncode == 0, (
        result.stderr, status_path.read_text() if status_path.exists() else "provider not entered",
    )
    assert result.stdout.split("\0") == [
        "3", "PRIVATE=private value\n\n", "EMPTY=", "ODD-NAME=v=1",
        "A", "", "single ' double \" ; $literal", "\u03a9\n\n", "",
    ]
    assert not result.stderr
    observed = (root / "observed").read_text(encoding="utf-8").split("\0")
    assert observed[0] == _shell_path(tmp_path)
    assert observed[1:6] == ["-I", "-S", "-B", "-X", "utf8"]
    assert observed[-10:] == [
        "--phase", "prepare", "--output-format", "nul",
        "--record", "record with spaces", "--", "literal", "", "",
    ]


@pytest.mark.parametrize("exit_code", [1, 3, 7])
def test_complete_frame_from_failed_provider_never_authorizes_dispatch(
    tmp_path: Path, exit_code: int,
) -> None:
    root = tmp_path / "runtime"
    _slot(root, "A", exit_code=exit_code)
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
AW_NATIVE_SEED_OPERATION=previous
AW_NATIVE_SEED_ARGV=(previous)
code=0
aw_native_seed_prepare "$2" --phase prepare --output-format nul || code=$?
[[ "$code" == 3 && -z "$AW_NATIVE_SEED_OPERATION" && -z "${AW_NATIVE_SEED_ARGV[0]+x}" ]]
''', [root])
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert result.stderr == "Native preparation is unconfirmed; no backend started.\n"
    assert "private value" not in result.stderr
    assert (root / "provider-status").read_text() == str(exit_code)


def test_each_boundary_reselects_current_slot_and_never_falls_back(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _slot(root, "A")
    _slot(root, "B")
    result = _run(r'''
set -euo pipefail
. "$1"
printf 'A\n' > "$2/current-version"
aw_native_seed_prepare "$2" --phase prepare --output-format nul
[[ "${AW_NATIVE_SEED_ARGV[0]}" == A ]]
printf 'B\n' > "$2/current-version"
aw_native_seed_prepare "$2" --phase prepare --output-format nul
[[ "${AW_NATIVE_SEED_ARGV[0]}" == B ]]
printf 'missing\n' > "$2/current-version"
if aw_native_seed_prepare "$2" --phase prepare --output-format nul; then exit 90; fi
[[ -z "$AW_NATIVE_SEED_OPERATION" && -z "${AW_NATIVE_SEED_ARGV[0]+x}" ]]
''', [root])
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert result.stderr == "Native preparation is unconfirmed; no backend started.\n"
    assert "B" in (root / "observed").read_text(encoding="utf-8")


def test_copied_phase_uses_replacement_runtime_without_original_scripts(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    original = root / "scripts"
    copied = tmp_path / "launch-owned"
    original.mkdir(parents=True)
    copied.mkdir()
    for name in ("native-seed-boundary.sh", "native-seed-reply.sh"):
        shutil.copy2(SCRIPT.with_name(name), original / name)
        shutil.copy2(original / name, copied / name)
    shutil.rmtree(root)
    _slot(root, "replacement")
    (root / "current-version").write_text("replacement", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
aw_native_seed_prepare "$2" --phase prepare --output-format nul
[[ "${AW_NATIVE_SEED_ARGV[0]}" == replacement ]]
''', [root], script_path=copied / SCRIPT.name)
    assert result.returncode == 0, result.stderr
    assert not result.stdout and not result.stderr
    assert not original.exists()


def test_finite_collection_restores_shell_options_without_tracing_reply_fields(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _slot(root, "A")
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
shopt -s nocasematch
set -x
aw_native_seed_prepare "$2" --phase prepare --output-format nul
case $- in *x*) : ;; *) exit 91 ;; esac
shopt -q nocasematch
printf 'missing' > "$2/current-version"
if aw_native_seed_prepare "$2" --phase prepare --output-format nul; then exit 90; fi
case $- in *x*) : ;; *) exit 91 ;; esac
shopt -q nocasematch
set +x
''', [root])
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert "private value" not in result.stderr and "PRIVATE=" not in result.stderr
    assert "Native preparation is unconfirmed; no backend started." in result.stderr


def test_successful_provider_with_trailing_bytes_still_refuses_reply(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _slot(root, "A", tail="printf 'trailing-private-data'")
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
if aw_native_seed_prepare "$2" --phase prepare --output-format nul; then exit 90; fi
[[ -z "$AW_NATIVE_SEED_OPERATION" && -z "${AW_NATIVE_SEED_ARGV[0]+x}" ]]
''', [root])
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert result.stderr == "Native preparation is unconfirmed; no backend started.\n"
    assert (root / "provider-status").read_text() == "0"


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX job-group and timeout qualification")
def test_hung_provider_is_settled_before_return_without_prompt_files(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _slot(root, "A", tail='printf "%s\\n" "$$" > "${0%/versions/*}/provider-pid"\n'
          "trap '' TERM\nwhile :; do sleep 1; done")
    (root / "current-version").write_text("A", encoding="utf-8")
    started = time.monotonic()
    result = _run(r'''
set -euo pipefail
. "$1"
code=0
aw_native_seed_prepare "$2" --phase prepare --output-format nul || code=$?
[[ "$code" == 3 && -z "$AW_NATIVE_SEED_OPERATION" && -z "${AW_NATIVE_SEED_ARGV[0]+x}" ]]
IFS= read -r provider < "$2/provider-pid"
! kill -0 "$provider" 2>/dev/null
''', [root])
    assert result.returncode == 0, result.stderr
    assert 9 <= time.monotonic() - started < 14
    assert not result.stdout
    assert result.stderr == "Native preparation is unconfirmed; no backend started.\n"
    assert {path.name for path in root.iterdir()} == {
        "versions", "current-version", "observed", "provider-pid",
    }


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX descendant-group cleanup")
def test_provider_cannot_leave_a_descendant_holding_its_reply_pipe(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _slot(root, "A", tail='sleep 60 &\nprintf "%s\\n" "$!" > "${0%/versions/*}/descendant-pid"')
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
if aw_native_seed_prepare "$2" --phase prepare --output-format nul; then exit 90; fi
[[ -z "$AW_NATIVE_SEED_OPERATION" && -z "${AW_NATIVE_SEED_ARGV[0]+x}" ]]
IFS= read -r descendant < "$2/descendant-pid"
state="$(ps -o stat= -p "$descendant" || true)"
[[ -z "$state" || "$state" == *Z* ]]
''', [root])
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert result.stderr == "Native preparation is unconfirmed; no backend started.\n"
