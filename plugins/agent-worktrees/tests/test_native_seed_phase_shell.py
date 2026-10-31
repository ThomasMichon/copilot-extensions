"""Finite copied POSIX transport, not production backend/terminal activation."""

from __future__ import annotations

import os
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
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


def _slot(
    root: Path, version: str, *, exit_code: int = 0, tail: str = "", malformed: bool = False,
) -> bytes:
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
    if malformed:
        fields[0] = "malformed"
    python = slot / "bin" / "python"
    python.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'trap \'printf "%s" "$?" > "${0%/versions/*}/provider-status"\' EXIT\n'
        '[[ -z "${PYTHONPATH+x}${PYTHONHOME+x}${VIRTUAL_ENV+x}${__PYVENV_LAUNCHER__+x}" ]]\n'
        '[[ "$(cat)" == "" ]]\n'
        'printf "%s\\0" "$PWD" "$@" > "${0%/versions/*}/observed"\n'
        'printf "%s\\n" "$$" > "${0%/versions/*}/provider-pid"\n'
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
    with tempfile.TemporaryDirectory(prefix="native-seed-launch-") as directory:
        if script_path == SCRIPT:
            for name in ("native-seed-boundary.sh", "native-seed-reply.sh", "native-seed-setup.sh"):
                shutil.copy2(SCRIPT.with_name(name), Path(directory) / name)
            script_path = Path(directory) / SCRIPT.name
        result = run_caller_owned(
            [_bash(), "--noprofile", "--norc", "-c", script, "phase", _shell_path(script_path),
             *(_shell_path(argument) if isinstance(argument, Path) else argument
               for argument in arguments)],
            input_text=payload, timeout=15, max_stdout_bytes=8192, max_stderr_bytes=8192,
        )
        assert not list(script_path.parent.glob(".native-seed-phase.*"))
        return result


def test_finite_phase_streams_exact_reply_and_returns_provider_status(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    expected = _slot(root, "A")
    (root / "current-version").write_text("A\n", encoding="utf-8", newline="\n")
    result = _run('. "$1"; aw_native_seed_phase "$2" --phase prepare --output-format nul', [root])
    assert result.returncode == 0, result.stderr
    assert result.stdout.encode("utf-8") == expected
    assert not result.stderr


def test_control_lookup_ignores_exported_functions_and_preserves_backend_path(tmp_path: Path) -> None:
    result = _run(r'''
set -euo pipefail
. "$1"
env() { printf 'INTERCEPTED\n'; return 91; }
export -f env
export PATH="$2/no-utilities" FIXTURE_LITERAL="literal preserved"
output="$(_aw_native_seed_control env "$BASH" -c 'printf "%s|%s" "$PATH" "$FIXTURE_LITERAL"')"
[[ "$output" == "$PATH|$FIXTURE_LITERAL" ]]
declare -F env >/dev/null
''', [tmp_path])
    assert result.returncode == 0, result.stderr
    assert not result.stdout and not result.stderr


def _contract_slot(
    root: Path, version: str, *, supported=True, discovery_tail="", discovery_exit=0,
    contract_version=1,
) -> None:
    _slot(root, version)
    python = root / "versions" / version / "bin" / "python"
    preparation = python.read_text(encoding="utf-8")
    descriptor = json.dumps({
        "contract": "agent-worktrees.native-seed", "version": contract_version,
        "preparation_output_formats": ["nul"],
        "preparation_environment_modes": ["full", "execution-hint"],
        "nul_reply_schema": "agent-worktrees.native-seed-reply.v1",
        "setup_process_start_time_sources": ["provider"],
        "backend_execution_modes": ["posix-exec-env-v1"] if supported else ["posix-exec-v1"],
    })
    python.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n"
        'root="${0%/versions/*}"\n'
        'for argument; do\n'
        ' if [[ "$argument" == --describe-contract ]]; then\n'
        f'  printf "%s\\n" {shlex.quote(version)} >> "$root/described"\n'
        f"  {discovery_tail or ':'}\n"
        f"  printf '%s\\n' {shlex.quote(descriptor)}\n"
        f'  exit {discovery_exit}\n fi\n'
        ' if [[ "$argument" == -c ]]; then\n'
        f"  exec {shlex.quote(_shell_path(Path(sys.executable)))} \"$@\"\n"
        ' fi\ndone\n'
        f'[[ "$(cat "$root/current-version")" == {shlex.quote(version)} ]] || exit 3\n'
        f'printf "%s\\n" {shlex.quote(version)} >> "$root/effects"\n'
        + preparation,
        encoding="utf-8", newline="\n",
    )


def test_setup_preparation_keeps_the_capability_checked_generation(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _contract_slot(root, "A", discovery_tail='printf "B\\n" > "$root/current-version"')
    _contract_slot(root, "B", supported=False)
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
. "${1%/*}/native-seed-setup.sh"
LAUNCH_SEED_NATIVE_ROOT="$2"
_aw_native_setup_require_contract
[[ "${AW_NATIVE_SEED_RUNTIME[0]}" == "$2/versions/A/bin/python" ]]
if _aw_native_setup_prepare "$2" --phase prepare --output-format nul; then exit 90; fi
''', [root])
    assert result.returncode == 0, result.stderr
    assert (root / "described").read_text().splitlines() == ["A"]
    assert not (root / "effects").exists()


def test_setup_discovery_cannot_cache_another_slots_descriptor_during_aba(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _contract_slot(root, "A", supported=False)
    _contract_slot(root, "B", discovery_tail='printf "A\\n" > "$root/current-version"')
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
. "${1%/*}/native-seed-setup.sh"
LAUNCH_SEED_NATIVE_ROOT="$2"
_aw_native_seed_runtime() {
    local slot="$1/versions/$(cat "$1/current-version")"
    AW_NATIVE_SEED_RUNTIME=("$slot/bin/python" -I -S -B -X utf8 "$slot/.runtime-bootstrap/runtime_launcher.py")
    printf 'B\n' > "$LAUNCH_SEED_NATIVE_ROOT/current-version"
}
if _aw_native_setup_require_contract; then exit 90; fi
''', [root])
    assert result.returncode == 0, result.stderr
    assert (root / "described").read_text().splitlines() == ["A"]
    assert not (root / "effects").exists()


@pytest.mark.parametrize("fault", ["oversized", "failed-provider"])
def test_descriptor_refuses_oversized_output_or_valid_json_from_a_failed_provider(
    tmp_path: Path, fault: str,
) -> None:
    root = tmp_path / "runtime"
    _contract_slot(
        root, "A", discovery_exit=7 if fault == "failed-provider" else 0,
        discovery_tail="printf '%65537s' x" if fault == "oversized" else "",
    )
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run('. "$1"; if aw_native_seed_supports_posix_setup "$2"; then exit 90; fi', [root])
    assert result.returncode == 0, result.stderr
    assert not result.stdout and not result.stderr
    assert not (root / "effects").exists()


@pytest.mark.parametrize("state", [
    "absent", "incompatible", "compatible-failure", "removed", "transient", "initial-transient",
    pytest.param("non-executable", marks=pytest.mark.skipif(os.name == "nt", reason="Native POSIX permissions")),
    pytest.param("lost-execute", marks=pytest.mark.skipif(os.name == "nt", reason="Native POSIX permissions")),
])
def test_return_handler_closes_quietly_only_when_delivery_is_unavailable(
    tmp_path: Path, state: str,
) -> None:
    root = tmp_path / "runtime"
    _contract_slot(root, "A", contract_version=2 if state == "incompatible" else 1)
    if state != "absent":
        (root / "current-version").write_text("A", encoding="utf-8")
    if state == "non-executable":
        (root / "versions" / "A" / "bin" / "python").chmod(0o600)
    result = _run(r'''
set -euo pipefail
. "$1"
state="$3"
aw_native_seed_phase() {
    if [[ "$2" == --describe-contract ]]; then
        [[ "$state" != initial-transient ]] || return 7
        if [[ "$state" == transient && -f "$1/callbacks" ]]; then return 7; fi
        _aw_native_seed_run_phase "$((SECONDS + 10))" _aw_native_seed_control env \
            -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV -u __PYVENV_LAUNCHER__ \
            "${AW_NATIVE_SEED_PHASE_RUNTIME[@]}" --describe-contract
    else
        printf 'attempted\n' >> "$1/callbacks"
        [[ "$state" != removed ]] || printf 'missing\n' > "$1/current-version"
        [[ "$state" != lost-execute ]] || builtin command -p chmod 600 "$1/versions/A/bin/python"
        return 7
    fi
}
aw_native_seed_return "$2" 'Return unconfirmed.' --phase returned --exit-code 7
''', [root, state])
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert result.stderr == (
        "Return unconfirmed.\n"
        if state in {"compatible-failure", "transient", "initial-transient", "non-executable", "lost-execute"}
        else ""
    )
    callbacks = root / "callbacks"
    assert (callbacks.read_text() if callbacks.exists() else "") == (
        "attempted\n" if state in {"compatible-failure", "removed", "transient", "lost-execute"} else ""
    )


@pytest.mark.parametrize("from_exit", ["direct", "trap", "setup"])
def test_compatible_return_handler_is_actually_invoked(tmp_path: Path, from_exit: str) -> None:
    root = tmp_path / "runtime"
    _contract_slot(root, "A")
    (root / "current-version").write_text("A", encoding="utf-8")
    ending = {
        "direct": 'aw_native_seed_return "$2" "Return unconfirmed." --phase returned --exit-code 7',
        "trap": 'trap \'aw_native_seed_return "$2" "Return unconfirmed." --phase returned --exit-code 7\' EXIT\nexit 7',
        "setup": '. "${1%/*}/native-seed-setup.sh"\nLAUNCH_SEED_NATIVE_ROOT="$2"\n'
                 'AW_NATIVE_SETUP_CALLBACK=(--record fixture)\ntrap _aw_native_setup_finish EXIT\nexit 7',
    }[from_exit]
    result = _run(r'''
set -euo pipefail
. "$1"
_aw_native_seed_supports_contract "$2" return
''' + ending, [root])
    assert result.returncode == (0 if from_exit == "direct" else 7), result.stderr
    assert not result.stdout and not result.stderr
    assert (root / "effects").read_text().splitlines() == ["A"]


@pytest.mark.parametrize("backend_code", [0, 7])
def test_uncertain_observer_warns_without_a_second_mutation_or_changed_exit(
    tmp_path: Path, backend_code: int,
) -> None:
    result = _run(r'''
set -euo pipefail
. "$1"
. "${1%/*}/native-seed-setup.sh"
LAUNCH_SEED_ID=seed
LAUNCH_SEED_NATIVE_ROOT="$2"
LAUNCH_SEED_RECORD=record
LAUNCH_ATTEMPT_ID=attempt
LAUNCH_RESOLUTION_ID=resolution
LAUNCH_PARENT_ID=parent
_aw_native_setup_require_contract() { AW_NATIVE_SEED_RUNTIME=(_fixture_exec); }
_aw_native_setup_prepare() {
    AW_NATIVE_SEED_ATTEMPT=attempt
    AW_NATIVE_SEED_RESOLUTION=resolution
    AW_NATIVE_SEED_OPERATION=operation
    AW_NATIVE_SEED_PARENT=parent
    AW_NATIVE_SEED_ENV=('AGENT_WORKTREES_LAUNCH_EXECUTION=fixture' 'PRIVATE=not-for-argv')
    AW_NATIVE_SEED_ARGV=(backend)
}
_fixture_exec() {
    while [[ "$1" != --completion-file ]]; do shift; done
    printf '3\n' > "$2"
    return "$FIXTURE_CODE"
}
_aw_native_seed_control() {
    local argument
    for argument; do [[ "$argument" != *not-for-argv* ]] || exit 92; done
    case "$1" in
        mktemp) builtin command -p mktemp "$LAUNCH_SEED_NATIVE_ROOT/.native-seed-exec.XXXXXXXX" ;;
        env)
            shift
            while [[ "$1" == -u ]]; do shift 2; done
            "$@" ;;
        *) builtin command -p "$@" ;;
    esac
}
aw_native_seed_phase() { printf 'UNEXPECTED MUTATION\n'; return 3; }
code=0
aw_native_setup_backend backend || code=$?
[[ "$code" == "$FIXTURE_CODE" ]]
'''.replace("set -euo pipefail", f"set -euo pipefail\nFIXTURE_CODE={backend_code}"), [tmp_path])
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert result.stderr == "Native backend exec is unconfirmed; inspect the retained handoff.\n"


def test_stale_native_env_script_envelope_refuses_before_sourcing(tmp_path: Path) -> None:
    primer = tmp_path / "prime.sh"
    primer.write_text("exit 91\n", encoding="utf-8")
    result = _run(
        'exec "$BASH" "$2" --launch-seed-preparation seed --env-script "$3"',
        [SCRIPT.with_name("default-setup.sh"), primer],
    )
    assert result.returncode == 3, result.stderr
    assert result.stderr == "Native POSIX setup cannot own env_script; no backend started.\n"


def test_setup_does_not_replace_a_preexisting_exit_trap_before_admission(tmp_path: Path) -> None:
    result = _run(r'''
set -euo pipefail
. "$1"
. "${1%/*}/native-seed-setup.sh"
LAUNCH_SEED_PREPARATION=seed
LAUNCH_SEED_RECORD=record
LAUNCH_SEED_NATIVE_ROOT="$2"
LAUNCH_ATTEMPT_ID=""
LAUNCH_RESOLUTION_ID=""
LAUNCH_PARENT_ID=""
_aw_native_setup_require_contract() { printf 'UNEXPECTED PROVIDER\n'; return 3; }
trap 'exit 1' EXIT
if aw_native_setup_begin backend; then exit 90; fi
exit 5
''', [tmp_path])
    assert result.returncode == 1, result.stderr
    assert not result.stdout and not result.stderr


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


def test_preparation_completion_is_not_exported_even_with_caller_allexport(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _slot(root, "A", tail=(
        '[[ -z "${_aw_native_seed_completion+x}${_aw_native_seed_control+x}'
        '${_aw_native_seed_deadline+x}" ]]'
    ))
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
set -a
aw_native_seed_prepare "$2" --phase prepare --output-format nul
case $- in *a*) : ;; *) exit 91 ;; esac
set +a
[[ "${AW_NATIVE_SEED_ARGV[0]}" == A ]]
''', [root])
    assert result.returncode == 0, result.stderr
    assert not result.stdout and not result.stderr
    assert (root / "provider-status").read_text() == "0"


def test_missing_private_control_path_refuses_before_starting_a_provider(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _slot(root, "A")
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
_aw_native_seed_control() {
    [[ "$1" != mktemp ]] || return 1
    builtin command -p "$@"
}
AW_NATIVE_SEED_OPERATION=previous
AW_NATIVE_SEED_ARGV=(previous)
if aw_native_seed_prepare "$2" --phase prepare --output-format nul; then exit 90; fi
[[ -z "$AW_NATIVE_SEED_OPERATION" && -z "${AW_NATIVE_SEED_ARGV[0]+x}" ]]
''', [root])
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert result.stderr == "Native preparation is unconfirmed; no backend started.\n"
    assert not (root / "provider-pid").exists()


def test_preparation_preserves_caller_noclobber(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _slot(root, "A")
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
set -C
aw_native_seed_prepare "$2" --phase prepare --output-format nul
case $- in *C*) : ;; *) exit 91 ;; esac
[[ "${AW_NATIVE_SEED_ARGV[0]}" == A ]]
''', [root])
    assert result.returncode == 0, result.stderr
    assert not result.stdout and not result.stderr


def test_control_write_error_is_bounded_and_does_not_expose_its_path(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    _slot(root, "A")
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
failed_control="$2/missing/private-control-path"
_aw_native_seed_control() {
    if [[ "$1" == mktemp ]]; then printf '%s\n' "$failed_control"
    else builtin command -p "$@"; fi
}
if aw_native_seed_prepare "$2" --phase prepare --output-format nul; then exit 90; fi
[[ -z "$AW_NATIVE_SEED_OPERATION" && -z "${AW_NATIVE_SEED_ARGV[0]+x}" ]]
''', [root])
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert result.stderr == "Native preparation is unconfirmed; no backend started.\n"
    assert (root / "provider-status").read_text() == "0"


@pytest.mark.parametrize("exit_code", [0, 7])
def test_provider_cannot_forge_the_supervisors_per_call_completion(
    tmp_path: Path, exit_code: int,
) -> None:
    root = tmp_path / "runtime"
    _slot(
        root, "A", exit_code=exit_code,
        tail="printf '%s\\0' agent-worktrees.native-phase-complete.v1:forged",
    )
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
    assert (root / "provider-status").read_text() == str(exit_code)


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


@pytest.mark.parametrize("malformed", ["header", "completion"])
def test_rejected_hung_provider_is_settled_before_return(
    tmp_path: Path, malformed: str,
) -> None:
    root = tmp_path / "runtime"
    _slot(
        root, "A", malformed=malformed == "header",
        tail=(
            "printf '%s\\0' malformed-completion\n" if malformed == "completion" else ""
        ) + "trap '' TERM\nwhile :; do sleep 1; done",
    )
    (root / "current-version").write_text("A", encoding="utf-8")
    result = _run(r'''
set -euo pipefail
. "$1"
code=0
aw_native_seed_prepare "$2" --phase prepare --output-format nul || code=$?
[[ "$code" == 3 && -z "$AW_NATIVE_SEED_OPERATION" && -z "${AW_NATIVE_SEED_ARGV[0]+x}" ]]
IFS= read -r provider < "$2/provider-pid"
if kill -0 "$provider" 2>/dev/null; then
    printf 'Provider is still alive after refusal.\n' >&2
    exit 90
fi
''', [root])
    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert result.stderr == "Native preparation is unconfirmed; no backend started.\n"


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


@pytest.mark.skipif(os.name == "nt", reason="Native POSIX escaped-session stdout deadline")
@pytest.mark.parametrize("malformed", [False, True])
@pytest.mark.parametrize("phase", ["prepare", "descriptor"])
def test_escaped_stdout_never_extends_the_preparation_deadline(
    tmp_path: Path, malformed: bool, phase: str,
) -> None:
    root = tmp_path / "runtime"
    ready = root / "escaped-ready"
    child = (
        "import os,signal,time; from pathlib import Path; "
        "signal.alarm(12); os.setsid(); "
        f"Path({str(ready)!r}).write_text(str(os.getpid())); "
        "time.sleep(13)"
    )
    tail = (
        f"{shlex.quote(sys.executable)} -I -S -B -c {shlex.quote(child)} &\n"
        'for ((index=0; index<100; index++)); do\n'
        '    [[ ! -f "${0%/versions/*}/escaped-ready" ]] || break\n'
        '    sleep 0.01\n'
        'done\n'
        '[[ -f "${0%/versions/*}/escaped-ready" ]]'
    )
    if phase == "descriptor":
        _contract_slot(root, "A", contract_version=2 if malformed else 1, discovery_tail=tail)
    else:
        _slot(root, "A", malformed=malformed, tail=tail)
    (root / "current-version").write_text("A", encoding="utf-8")
    clock = f"{shlex.quote(sys.executable)} -I -S -B -c 'import time; print(time.monotonic())'"
    started = time.monotonic()
    try:
        result = _run(r'''
set -euo pipefail
. "$1"
started="$(''' + clock + r''')"
code=0
if [[ "$3" == descriptor ]]; then
    aw_native_seed_supports_posix_setup "$2" || code=$?
else
    aw_native_seed_prepare "$2" --phase prepare --output-format nul || code=$?
fi
finished="$(''' + clock + r''')"
printf '%s %s %s\n' "$code" "$started" "$finished"
if [[ "$code" != 0 && "$3" != descriptor ]]; then
    [[ -z "$AW_NATIVE_SEED_OPERATION" && -z "${AW_NATIVE_SEED_ARGV[0]+x}" ]]
fi
''', [root, phase])
    finally:
        # This deliberately out-of-group writer has its own hard TTL. Observe
        # retirement even when the preparation assertion or outer owner fails.
        if ready.exists():
            pid = int(ready.read_text())
            while True:
                state = run_caller_owned(
                    ["ps", "-o", "stat=", "-p", str(pid)], timeout=1,
                    max_stdout_bytes=128, max_stderr_bytes=128,
                )
                if not state.stdout.strip() or "Z" in state.stdout:
                    break
                assert time.monotonic() - started < 14, "Escaped fixture exceeded its hard TTL"
                time.sleep(0.05)
    assert ready.exists(), result.stderr
    assert result.returncode == 0, result.stderr
    code, began, finished = result.stdout.split()
    assert code == "3", result.stdout
    assert float(finished) - float(began) < 11, result.stdout
    assert result.stderr == (
        "" if phase == "descriptor" else "Native preparation is unconfirmed; no backend started.\n"
    )
