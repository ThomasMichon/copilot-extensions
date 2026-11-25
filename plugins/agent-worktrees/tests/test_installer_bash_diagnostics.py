"""Native Bash reservation and composed installer-exit contracts."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from test_installer_diagnostics import (
    SCRIPTS,
    load_diagnostics,
    native_bash,
    native_environment,
)

pytestmark = pytest.mark.skipif(native_bash() is None, reason="native Bash unavailable")
CANONICAL = SCRIPTS.parents[2] / "libs" / "installer-engine"
_POST_INITIALIZATION = """
export COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY="$HOME/.copilot-extensions/installer-diagnostics/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
export COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
export COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING=0
INSTALLER_DIAGNOSTICS_OWNER=true
INSTALLER_DIAGNOSTICS_LOG="$COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY/$COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER.log"
"""


@pytest.fixture
def post_initialization(tmp_path: Path) -> Path:
    directory = tmp_path / ".copilot-extensions" / "installer-diagnostics" / ("a" * 32)
    directory.mkdir(parents=True)
    (directory / (("b" * 32) + ".log")).touch()
    (directory / "missing-diagnostics.json").mkdir()
    return directory


def _unread_pipe_run(
    tmp_path: Path, program: str, *arguments: str,
) -> subprocess.CompletedProcess[str]:
    set_blocking: Callable[[int, bool], None] | None = getattr(os, "set_blocking", None)
    get_blocking: Callable[[int], bool] | None = getattr(os, "get_blocking", None)
    if set_blocking is None or get_blocking is None:
        pytest.skip("pipe blocking controls require Python 3.12 or newer on Windows")
    environment = native_environment(
        host="powershell.exe" if os.name == "nt" else None,
        HOME=tmp_path.as_posix(), USERPROFILE=str(tmp_path),
    )
    for key in ("ATTEMPT", "WRITER", "DIRECTORY", "MISSING"):
        environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_" + key, None)
    reader, writer = os.pipe()
    process = None
    try:
        set_blocking(writer, False)
        filled = 0
        while True:
            try:
                filled += os.write(writer, b"x")
            except BlockingIOError:
                break
        assert filled > 0
        set_blocking(writer, True)
        output = tmp_path / "native.stdout"
        with output.open("wb") as stream:
            process = subprocess.Popen(
                [cast(str, native_bash()), "-c", program, "bash", *arguments],
                env=environment, stdin=subprocess.DEVNULL, stdout=stream, stderr=writer,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            process.wait(timeout=15)
        assert get_blocking(writer), "presentation must not change the inherited pipe mode"
        os.close(writer)
        writer = -1
        set_blocking(reader, False)
        remaining = bytearray()
        while True:
            try:
                chunk = os.read(reader, 65536)
            except BlockingIOError:
                pytest.fail("diagnostic writer survived installer exit and retained its pipe")
            if not chunk:
                break
            remaining.extend(chunk)
        assert remaining == b"x" * filled, "the full pipe must remain unread during execution"
        return subprocess.CompletedProcess(
            process.args, process.returncode, output.read_text(encoding="utf-8"), "",
        )
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        if writer != -1:
            os.close(writer)
        os.close(reader)


@pytest.mark.parametrize("native_exit", [0, 37])
@pytest.mark.parametrize("helper_present", [True, False])
def test_native_entry_unread_stderr_preserves_result(
    tmp_path: Path, native_exit: int, helper_present: bool,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    source = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    entry, separator, _ = source.partition("# A structured caller supplies")
    assert separator, "exercise the actual pre-admission entry and composed exit trap"
    if helper_present:
        # Invalid plugin reaches the real Bash warning without selecting Python
        # or delegating Git Bash's Windows privacy checks to PowerShell.
        (scripts / "installer-diagnostics.sh").write_text(
            (CANONICAL / "installer-diagnostics.sh").read_text(encoding="utf-8"), encoding="utf-8",
        )
        entry = entry.replace(
            "installer_diagnostics_init agent-worktrees", "installer_diagnostics_init INVALID",
        )
    entry += """
_release_versioned_slot_lease() { printf 'released' > "$HOME/released"; return 23; }
printf '{"missing":%s}' "$COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING"
""" + f"\nexit {native_exit}\n"
    path = scripts / "install.sh"
    path.write_text(entry, encoding="utf-8")
    result = _unread_pipe_run(tmp_path, 'set -euo pipefail; source "$1"', path.as_posix())
    assert result.returncode == native_exit
    assert json.loads(result.stdout) == {"missing": 1}
    assert (tmp_path / "released").read_text(encoding="utf-8") == "released"


@pytest.mark.parametrize("native_exit", [0, 37])
@pytest.mark.parametrize("failure", ["source", "initialization"])
def test_failed_optional_helper_restores_native_entry_fallbacks(
    tmp_path: Path, native_exit: int, failure: str,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "installer-diagnostics.sh").write_text(
        "installer_diagnostics_complete() { printf corrupted-completion; return 91; }\n"
        "installer_diagnostics_write() { printf corrupted-write; return 94; }\n"
        "installer_diagnostics_run() { return 92; }\n"
        "installer_diagnostics_present() { printf corrupted-warning; return 93; }\n"
        "installer_diagnostics_init() { return 42; }\n"
        + ("return 42\n" if failure == "source" else ""),
        encoding="utf-8",
    )
    entry, separator, _ = (SCRIPTS / "install.sh").read_text(encoding="utf-8").partition(
        "# A structured caller supplies",
    )
    assert separator
    entry += """
_release_versioned_slot_lease() { printf released > "$HOME/released"; return 23; }
installer_diagnostics_run "$BASH" -c 'printf native-body; exit "$1"' bash """ + str(native_exit) + "\n"
    path = scripts / "install.sh"
    path.write_text(entry, encoding="utf-8")
    result = _unread_pipe_run(tmp_path, 'set -euo pipefail; source "$1"', path.as_posix())
    assert result.returncode == native_exit
    assert result.stdout == "native-body"
    assert (tmp_path / "released").read_text(encoding="utf-8") == "released"


@pytest.mark.parametrize("native_exit", [0, 37])
@pytest.mark.parametrize("failure", ["syntax", "storage"])
def test_optional_setup_errors_cannot_block_on_unread_stderr(
    tmp_path: Path, native_exit: int, failure: str,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "installer-diagnostics.sh").write_text(
        "invalid(\n" if failure == "syntax" else (
            CANONICAL / "installer-diagnostics.sh"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    entry, separator, _ = (SCRIPTS / "install.sh").read_text(encoding="utf-8").partition(
        "# A structured caller supplies",
    )
    assert separator
    if failure == "storage":
        entry = entry.replace(
            "installer_diagnostics_init agent-worktrees",
            "OSTYPE=linux-gnu installer_diagnostics_init agent-worktrees",
        )
    entry += """
_release_versioned_slot_lease() { printf released > "$HOME/released"; }
installer_diagnostics_run "$BASH" -c 'printf native-body; exit "$1"' bash """ + str(native_exit) + "\n"
    path = scripts / "install.sh"
    path.write_text(entry, encoding="utf-8")
    result = _unread_pipe_run(tmp_path, """
set -euo pipefail
mkdir() { printf 'injected diagnostic storage failure\\n' >&2; return 13; }
source "$1"
""", path.as_posix())
    assert result.returncode == native_exit
    assert result.stdout == "native-body"
    assert (tmp_path / "released").read_text(encoding="utf-8") == "released"


@pytest.mark.parametrize("native_exit", [0, 37])
def test_native_announcement_unread_stderr_preserves_result(
    tmp_path: Path, native_exit: int,
) -> None:
    source = (CANONICAL / "installer-diagnostics.sh").read_text(encoding="utf-8")
    announcement = re.search(
        r'installer_diagnostics_prune "\$root"\n(.*?)^\}', source, re.MULTILINE | re.DOTALL,
    )
    assert announcement, "exercise the actual POSIX initialization announcement on Git Bash too"
    result = _unread_pipe_run(tmp_path, """
set -euo pipefail
source "$1"
directory="$HOME"
""" + announcement.group(1) + """
printf '{"native":%s}' "$2"
exit "$2"
""", (CANONICAL / "installer-diagnostics.sh").as_posix(), str(native_exit))
    assert result.returncode == native_exit
    assert json.loads(result.stdout) == {"native": native_exit}


def test_native_capture_unread_stderr_preserves_command_result(tmp_path: Path) -> None:
    result = _unread_pipe_run(tmp_path, """
set -euo pipefail
source "$1"
export COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY="$HOME"
INSTALLER_DIAGNOSTICS_LOG="$HOME/captured.log"
: > "$INSTALLER_DIAGNOSTICS_LOG"
if installer_diagnostics_run "$BASH" -c \
    'printf stdout-record; printf stderr-record >&2; exit 37'; then
    rc=0
else
    rc=$?
fi
exit "$rc"
""", (CANONICAL / "installer-diagnostics.sh").as_posix())
    assert result.returncode == 37
    assert result.stdout == "stdout-record"
    assert (tmp_path / "captured.log").read_text(encoding="utf-8") == "stdout-recordstderr-record"


@pytest.mark.parametrize("native_exit", [0, 37])
def test_direct_initialization_failure_cannot_block_on_unread_stderr(
    tmp_path: Path, native_exit: int,
) -> None:
    result = _unread_pipe_run(tmp_path, """
set -euo pipefail
source "$1"
mkdir() { printf 'injected directory error\\n' >&2; return 13; }
powershell.exe() { printf 'injected native helper error\\n' >&2; return 13; }
installer_diagnostics_init agent-worktrees
if installer_diagnostics_run "$BASH" -c 'printf native-body; exit "$1"' bash "$2"; then
    rc=0
else
    rc=$?
fi
printf '%s' "$COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING" > "$HOME/missing"
exit "$rc"
""", (SCRIPTS / "installer-diagnostics.sh").as_posix(), str(native_exit))
    assert result.returncode == native_exit
    assert result.stdout == "native-body"
    assert (tmp_path / "missing").read_text(encoding="utf-8") == "1"


@pytest.mark.parametrize("native_exit", [0, 37])
def test_failed_posix_log_open_leaves_native_fallback_enabled(
    tmp_path: Path, native_exit: int,
) -> None:
    directory = tmp_path / ".copilot-extensions" / "installer-diagnostics" / ("a" * 32)
    directory.mkdir(parents=True)
    (directory / "attempt.json").write_text('{"plugin":"agent-worktrees"}', encoding="utf-8")
    (directory / (("b" * 32) + ".log")).mkdir()
    result = subprocess.run(
        [cast(str, native_bash()), "-c", """
set -euo pipefail
source "$1"
unset COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY COPILOT_EXTENSIONS_DIAGNOSTICS_WRITER
unset COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING
export COPILOT_EXTENSIONS_DIAGNOSTICS_ATTEMPT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
OSTYPE=linux-gnu
installer_diagnostics_private() { [[ -d "$1" ]]; }
od() { printf '%s' bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb; }
installer_diagnostics_init agent-worktrees
printf '%s' "$COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING" > "$HOME/missing"
installer_diagnostics_run "$BASH" -c 'printf native-body; exit "$1"' bash "$2"
""", "bash", (SCRIPTS / "installer-diagnostics.sh").as_posix(), str(native_exit)],
        env=native_environment(HOME=tmp_path.as_posix(), USERPROFILE=str(tmp_path)),
        capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == native_exit
    assert result.stdout == "native-body"
    assert "unbound variable" not in result.stderr
    assert (tmp_path / "missing").read_text(encoding="utf-8") == "1"


@pytest.mark.parametrize("native_exit", [0, 37])
@pytest.mark.parametrize("failure", ["log", "completion", "copy"])
def test_actual_exit_storage_failure_preserves_result_on_unread_stderr(
    tmp_path: Path, post_initialization: Path, native_exit: int, failure: str,
) -> None:
    if failure == "completion":
        (post_initialization / (("b" * 32) + ".completed.json")).mkdir()
    source = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    function = re.search(r"^_installer_exit\(\) \{\n.*?^\}", source, re.MULTILINE | re.DOTALL)
    assert function
    program = """
set -euo pipefail
source "$1"
""" + _POST_INITIALIZATION + """
directory="$COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY"
case "$2" in
    log) INSTALLER_DIAGNOSTICS_LOG="$directory" ;;
    copy) cp() { printf 'injected completion copy error\\n' >&2; return 13; } ;;
esac
_release_versioned_slot_lease() { printf released > "$HOME/released"; }
""" + function.group() + """
trap '_installer_exit' EXIT
printf native-body
exit "$3"
"""
    result = _unread_pipe_run(
        tmp_path, program, (SCRIPTS / "installer-diagnostics.sh").as_posix(), failure, str(native_exit),
    )
    assert result.returncode == native_exit
    assert result.stdout == "native-body"
    assert (tmp_path / "released").read_text(encoding="utf-8") == "released"


@pytest.mark.parametrize("native_exit", [0, 37])
@pytest.mark.parametrize("failure", ["nonce", "capture", "redirect", "archive", "size"])
def test_capture_metadata_failure_never_replays_native_command(
    tmp_path: Path, post_initialization: Path, native_exit: int, failure: str,
) -> None:
    if failure == "capture":
        (post_initialization / (("c" * 32) + ".stdout.raw")).mkdir()
    program = """
set -euo pipefail
source "$1"
""" + _POST_INITIALIZATION + """
directory="$COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY"
failure="$2"
_native_test_id=cccccccccccccccccccccccccccccccc
od() {
    if [[ "$failure" == nonce ]]; then printf 'injected nonce error\\n' >&2; return 13; fi
    builtin printf '%s' "$_native_test_id"
}
case "$failure" in
    redirect)
        printf() {
            if [[ "${2:-}" == 'Private raw transport; inherited writers may remain.' ]]; then
                mv "$directory/$_native_test_id.stdout.raw" "$directory/original-stdout"
                mkdir "$directory/$_native_test_id.stdout.raw"
            fi
            builtin printf "$@"
        } ;;
    archive) INSTALLER_DIAGNOSTICS_LOG="$directory" ;;
    size) wc() { printf 'injected size error\\n' >&2; return 13; } ;;
esac
if installer_diagnostics_run "$BASH" -c \
    'printf native-body; printf run >> "$1"; exit "$2"' bash "$HOME/native-runs" "$3"; then
    rc=0
else
    rc=$?
fi
printf '%s' "$COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING" > "$HOME/missing"
exit "$rc"
"""
    result = _unread_pipe_run(
        tmp_path, program, (SCRIPTS / "installer-diagnostics.sh").as_posix(), failure, str(native_exit),
    )
    assert result.returncode == native_exit
    if failure in {"archive", "size"}:
        assert result.stdout in ({""} if failure == "size" else {"", "native-body"})
        raw = list((tmp_path / ".copilot-extensions" / "installer-diagnostics").glob("*/*.stdout.raw"))
        assert len(raw) == 1 and raw[0].read_bytes() == b"native-body"
    else:
        assert result.stdout == "native-body"
    assert (tmp_path / "native-runs").read_text(encoding="utf-8") == "run"
    assert (tmp_path / "missing").read_text(encoding="utf-8") == "1"


def test_prune_keeps_evidence_when_its_storage_scan_fails(
    tmp_path: Path, post_initialization: Path,
) -> None:
    old = post_initialization.parent / ("1" * 32)
    old.mkdir()
    (old / "completed.json").write_text('{"exit":0}', encoding="utf-8")
    (old / "attempt.json").write_text(
        '{"schema":"copilot-extensions.installer-diagnostics"}', encoding="utf-8",
    )
    result = _unread_pipe_run(tmp_path, """
set -euo pipefail
source "$1"
""" + _POST_INITIALIZATION + """
root="${COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY%/*}"
old="$root/11111111111111111111111111111111"
installer_diagnostics_private() { [[ "$1" == "$old" ]]; }
find() {
    printf 'find\\n' >> "$HOME/find-calls"
    if [[ "$*" == *-mtime* ]]; then printf '%s\\n' "$old/completed.json"; return 0; fi
    printf 'injected scan failure\\n' >&2
    return 13
}
rm() { printf removed > "$HOME/removed"; return 99; }
installer_diagnostics_prune "$root"
[[ -d "$old" ]]
""", (SCRIPTS / "installer-diagnostics.sh").as_posix())
    assert result.returncode == 0
    assert (tmp_path / "find-calls").read_text(encoding="utf-8").splitlines() == ["find", "find"]
    assert not (tmp_path / "removed").exists()


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX reservation parser; Windows delegates to PowerShell",
)
@pytest.mark.parametrize("formatting", ["canonical", "multiline", "wrong-plugin"])
def test_python_reserved_attempt_enters_native_bash_without_bootstrap_python(
    tmp_path: Path, formatting: str,
) -> None:
    diagnostics = load_diagnostics()
    environment = diagnostics.reserve_launch(native_environment(
        HOME=str(tmp_path), USERPROFILE=str(tmp_path),
    ))
    directory = Path(environment["COPILOT_EXTENSIONS_DIAGNOSTICS_DIRECTORY"])
    receipt = directory / "attempt.json"
    if formatting != "canonical":
        record = json.loads(receipt.read_text(encoding="utf-8"))
        if formatting == "wrong-plugin":
            record["plugin"] = "another-plugin"
        receipt.write_text(
            json.dumps(record, indent=2).replace('": ', '"\n:\t') + "\n", encoding="utf-8",
        )
    result = subprocess.run(
        [cast(str, native_bash()), "-c", """
set -euo pipefail
source "$1"
installer_diagnostics_init agent-worktrees
installer_diagnostics_write 'native reservation entered'
installer_diagnostics_complete 37
printf '%s' "$COPILOT_EXTENSIONS_DIAGNOSTICS_MISSING"
exit 37
""", "bash", str(SCRIPTS / "installer-diagnostics.sh")],
        env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 37, result.stderr
    assert result.stdout == ("1" if formatting == "wrong-plugin" else "0")
    writers = list(directory.glob("*.start.json"))
    if formatting == "wrong-plugin":
        assert not writers and "missing diagnostics" in result.stderr
    else:
        assert len(writers) == 1
        writer = writers[0].name.removesuffix(".start.json")
        archive = (directory / f"{writer}.log").read_text(encoding="utf-8")
        completion = json.loads(
            (directory / f"{writer}.completed.json").read_text(encoding="utf-8"),
        )
        assert "native reservation entered" in archive
        assert completion["exit"] == 37
        assert not (directory / "completed.json").exists(), "caller owns attempt completion"


@pytest.mark.parametrize(
    "native_exit,release_failure", [(0, False), (37, False), (0, True), (37, True)],
)
def test_actual_installer_exit_trap_composes_release_and_native_writer_completion(
    tmp_path: Path, native_exit: int, release_failure: bool,
) -> None:
    source = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    function = re.search(r"^_installer_exit\(\) \{\n.*?^\}", source, re.MULTILINE | re.DOTALL)
    assert function, "the actual installer must own one composed exit function"
    traps = re.findall(r"^trap .* EXIT$", source, re.MULTILINE)
    assert traps == ["trap '_installer_exit' EXIT"], "EXIT must not overwrite completion"
    release = tmp_path / "released"
    environment = native_environment(
        host="powershell.exe" if os.name == "nt" else None,
        HOME=tmp_path.as_posix(), USERPROFILE=str(tmp_path),
    )
    for key in ("ATTEMPT", "WRITER", "DIRECTORY", "MISSING"):
        environment.pop("COPILOT_EXTENSIONS_DIAGNOSTICS_" + key, None)
    program = """
set -euo pipefail
source "$1"
installer_diagnostics_init agent-worktrees
release_path="$2"
release_rc="$3"
_release_versioned_slot_lease() {
    printf 'released\\n' > "$release_path"
    return "$release_rc"
}
""" + function.group() + "\n" + "\n".join(traps)
    program += f"\nprintf '{{\"native\":{native_exit}}}'\nexit {native_exit}\n"
    result = subprocess.run(
        [cast(str, native_bash()), "-c", program, "bash",
         (SCRIPTS / "installer-diagnostics.sh").as_posix(),
         release.as_posix(), "23" if release_failure else "0"],
        env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == native_exit, result.stderr
    assert json.loads(result.stdout) == {"native": native_exit}
    assert ("WARNING: installer lease cleanup failed" in result.stderr) is release_failure
    assert release.read_text(encoding="utf-8") == "released\n"
    attempts = list((tmp_path / ".copilot-extensions" / "installer-diagnostics").iterdir())
    assert len(attempts) == 1
    writers = list(attempts[0].glob("*.completed.json"))
    assert len(writers) == 1
    assert json.loads(writers[0].read_text(encoding="utf-8"))["exit"] == native_exit
    completion = json.loads((attempts[0] / "completed.json").read_text(encoding="utf-8"))
    assert completion["exit"] == native_exit
    archive = "\n".join(path.read_text(encoding="utf-8") for path in attempts[0].glob("*.log"))
    assert ("installer lease cleanup failed" in archive) is release_failure
