"""Required publication failures stay failures with conditional Bash callers."""
from __future__ import annotations

import os
import json
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from . import source_publication_fixture as fixture

INJECTION = r'''
if [[ -n "${FIXTURE_PEER_PYTHON:-}" ]]; then
    _bootstrap_python() { printf '%s' "$FIXTURE_PEER_PYTHON"; }
fi
_fixture_fail() {
    [[ ( "${FIXTURE_FAILURE:-}" == "$1" ||
         ( "${FIXTURE_FAILURE:-}" == success-peer && "$1" == cat ) ) &&
       ! -e "$FIXTURE_FAILURE_LOG" ]]
}
_fixture_record() { builtin printf '%s\n' "$1" > "$FIXTURE_FAILURE_LOG"; }
mkdir() {
    if _fixture_fail mkdir; then
        local arg
        for arg in "$@"; do
            if [[ "$arg" == "$LOCAL_BIN" ]]; then _fixture_record mkdir; return 73; fi
        done
    fi
    command mkdir "$@"
}
cp() {
    if _fixture_fail copy && [[ "${@: -1}" == "$INSTALL_DIR/bin/.resolve-runtime.ps1.publication-stage."*/resolve-runtime.ps1 ]]; then
        command head -c 12 "${@: -2:1}" > "${@: -1}"
        _fixture_record copy
        return 73
    fi
    command cp "$@"
}
cat() {
    if _fixture_fail cat && [[ /dev/fd/1 -ef "${STUB_STAGE:-$STUB}" ]]; then
        builtin printf broken
        _fixture_record cat
        return 73
    fi
    command cat "$@"
}
chmod() {
    if _fixture_fail chmod && [[ "${@: -1}" == "${BOARD_STAGE:-$BOARD_STUB}" ]]; then
        _fixture_record chmod
        return 73
    fi
    command chmod "$@"
}
printf() {
    if _fixture_fail machine && [[ /dev/fd/1 -ef "${MACHINE_STAGE:-$INSTALL_DIR/machine}" ]]; then
        builtin printf broken
        _fixture_record machine
        return 73
    fi
    builtin printf "$@"
}
mv() {
    if _fixture_fail board-directory && [[ "${@: -2:1}" == "${BOARD_STAGE:-}" ]]; then
        command rm -f "$BOARD_STUB"
        command mkdir "$BOARD_STUB"
        builtin printf peer-directory > "$BOARD_STUB/peer-sentinel"
        _fixture_record board-directory
    fi
    if _fixture_fail manifest &&
        [[ "${@: -2:1}" == "$INSTALL_DIR/.deploy-manifest.json.publication-stage."*/deploy-manifest.json ]]; then
        _fixture_record manifest
        return 73
    fi
    command mv "$@"
}
if type -P flock >/dev/null 2>&1; then
    flock() {
        if _fixture_fail lock && [[ "${1:-}" != -u ]]; then _fixture_record lock; return 73; fi
        command flock "$@"
    }
fi
'''

def snapshot(paths: list[Path]) -> dict[Path, tuple[bytes, int] | None]:
    return {
        path: (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.exists() else None
        for path in paths
    }


@pytest.mark.parametrize(("failure", "action"), [
    ("mkdir", "install"), ("copy", "install"), ("cat", "install"),
    ("chmod", "install"), ("machine", "install"), ("manifest", "install"),
    ("lock", "install"), ("copy", "update"), ("mkdir", "provision"),
    ("copy", "stamp"), ("cat", "stamp"), ("peer", "stamp"),
    ("success-peer", "stamp"), ("board-directory", "stamp"),
])
def test_conditional_publication_failure_keeps_healthy_selection(tmp_path, monkeypatch, failure, action):
    if os.name == "nt":
        pytest.skip("POSIX failure injection")
    if failure == "lock" and not shutil.which("flock", path=fixture.environment(tmp_path)["PATH"]):
        pytest.skip("lock injection requires an external flock in the fixture PATH")
    original = fixture.script

    def render(ext: str) -> str:
        text = original(ext)
        if ext == "sh":
            text = text.replace(
                "_initialize_source_runtime\ncase",
                INJECTION + "\n_initialize_source_runtime\ncase",
            ).replace(
                "stamp) do_stamp ;;",
                "stamp) if do_stamp; then exit 0; else exit $?; fi ;;",
            ).replace(
                "install|provision|update) _ensure_runtime ;;",
                "install|provision|update) if _ensure_runtime; then exit 0; else exit $?; fi ;;",
            )
        return text

    monkeypatch.setattr(fixture, "script", render)
    env, root, sources = fixture.stage(tmp_path)
    (a, identity_a), (b, _) = sources
    result = fixture.run("sh", a / "scripts/install.sh", ["install"], env)
    assert result.returncode == 0, result.stdout + result.stderr
    bin_dir = Path(env["HOME"]) / ".local" / "bin"
    paths = [root / name for name in (
        "current-version", "last-known-good", "deploy-manifest.json", "payload-dir",
        "stamped-version", "machine", "bin/resolve-runtime.sh", "bin/resolve-runtime.ps1",
    )] + [bin_dir / "agent-dispatch", bin_dir / "agent-dispatch-board"]
    before = snapshot(paths)
    completions = {
        identity.runtime_version: (root / "versions" / identity.runtime_version / ".install-complete.json").read_bytes()
        for _, identity in sources
    }
    env["FIXTURE_FAILURE"] = failure
    env["FIXTURE_FAILURE_LOG"] = str(tmp_path / "injected")
    if failure == "peer":
        env["FIXTURE_PEER_PYTHON"] = str(fixture.peer_failure_python(tmp_path))
    if failure == "success-peer":
        env["FIXTURE_PEER_PYTHON"] = str(fixture.successful_peer_python(tmp_path))
        env["FIXTURE_PEER_RECORD"] = str(tmp_path / "peer-record.json")
    result = fixture.run("sh", b / "scripts/install.sh", [action], env)
    log = Path(env["FIXTURE_FAILURE_LOG"])
    if failure != "success-peer" or log.exists():
        assert log.read_text().strip() == ("cat" if failure == "success-peer" else failure)
    if failure == "board-directory":
        board = bin_dir / "agent-dispatch-board"
        assert board.is_dir() and (board / "peer-sentinel").read_bytes() == b"peer-directory"
        assert result.returncode != 0, (
            f"Reported success after real mv into peer directory: {[path.name for path in board.iterdir()]}; "
            f"journals: {list(root.glob('.publication.????????/journal'))}"
        )
    assert result.returncode != 0, result.stdout + result.stderr
    if failure == "success-peer":
        peer = json.loads(Path(env["FIXTURE_PEER_RECORD"]).read_text())
        assert peer["producer_inode"] != peer["inode"]
        assert (root / "current-version").read_bytes() == before[root / "current-version"][0]
        actual = (root / "payload-dir").stat()
        assert (actual.st_dev, actual.st_ino) == (peer["device"], peer["inode"])
        assert (root / "payload-dir").read_bytes().hex() == peer["data"]
        assert (root / "current-version").read_bytes() == before[root / "current-version"][0]
        assert list(root.glob(".publication.????????/journal")), result.stdout + result.stderr
        return
    if failure == "board-directory":
        board = bin_dir / "agent-dispatch-board"
        assert board.is_dir()
        assert (board / "peer-sentinel").read_bytes() == b"peer-directory"
        assert not list(board.glob("*.publication.*"))
        assert list(root.glob(".publication.????????/journal")), result.stdout + result.stderr
        return
    if failure == "peer":
        assert (root / "payload-dir").read_bytes() == b"peer-owned-redirect\n"
        assert (root / "current-version").read_bytes() == before[root / "current-version"][0]
        journals = list(root.glob(".publication.????????/journal"))
        assert journals and all(path.stat().st_size for path in journals), result.stdout + result.stderr
        assert any(
            path.read_bytes() == before[root / "payload-dir"][0]
            for journal in journals for path in journal.parent.glob("before.*")
        )
        assert {path: value for path, value in snapshot(paths).items() if path != root / "payload-dir"} == {
            path: value for path, value in before.items() if path != root / "payload-dir"
        }
        for _, identity in sources:
            assert (root / "versions" / identity.runtime_version / ".install-complete.json").read_bytes() == completions[identity.runtime_version]
        assert "retained" in result.stderr.lower()
        return
    assert snapshot(paths) == before
    assert not list(root.rglob("*.publication.*"))
    assert not list(bin_dir.glob("*.publication.*"))
    for _, identity in sources:
        assert (root / "versions" / identity.runtime_version / ".install-complete.json").read_bytes() == completions[identity.runtime_version]
    result = fixture.run("sh", bin_dir / "agent-dispatch", ["--version"], env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == f"agent-dispatch {identity_a.runtime_version}"


@pytest.mark.parametrize("failure", ["cat", "machine"])
def test_descriptor_injection_is_portable_without_procfs(tmp_path: Path, failure: str) -> None:
    from .test_install_sh_activation_guard import _resolve_bash

    bash = _resolve_bash()
    if not bash:
        pytest.skip("native Bash unavailable")
    assert "/proc/" not in INJECTION
    assert "BASHPID" not in INJECTION
    script = f"""
set -eu
INSTALL_DIR='{tmp_path.as_posix()}'
STUB="$INSTALL_DIR/stub"
BOARD_STUB="$INSTALL_DIR/board"
FIXTURE_FAILURE='{failure}'
FIXTURE_FAILURE_LOG="$INSTALL_DIR/injected"
type() {{
    if [[ "$*" == '-P flock' ]]; then return 1; fi
    builtin type "$@"
}}
{INJECTION}
if declare -F flock >/dev/null; then exit 90; fi
if [[ '{failure}' == cat ]]; then
    if cat /dev/null > "$STUB"; then exit 91; else [[ "$?" == 73 ]] || exit 92; fi
else
    if printf '%s' value > "$INSTALL_DIR/machine"; then exit 93; else [[ "$?" == 73 ]] || exit 94; fi
fi
"""
    result = subprocess.run(
        [bash, "-c", script], cwd=tmp_path, env=fixture.environment(tmp_path),
        capture_output=True, text=True, timeout=10, creationflags=fixture.HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "injected").read_text().strip() == failure
    target = tmp_path / ("stub" if failure == "cat" else "machine")
    assert target.read_text() == "broken"


@pytest.mark.parametrize("case", [
    "success", "foreign-payload", "foreign-manifest", "foreign-new", "foreign-created", "foreign-current",
    "foreign-same-bytes", "foreign-inplace", "foreign-mode", "foreign-delete",
    "foreign-directory", "foreign-symlink", "backup-tamper",
    "restore-io", "remove-io",
])
@pytest.mark.timeout(60 if os.name == "nt" else 30)
def test_outer_recovery_requires_per_file_ownership(tmp_path: Path, case: str) -> None:
    from .test_install_sh_activation_guard import _resolve_bash

    bash = _resolve_bash()
    if not bash:
        pytest.skip("native Bash unavailable")
    if case == "foreign-symlink" and os.name == "nt":
        pytest.skip("POSIX symlink creation without elevation")
    root = tmp_path / "runtime"
    root.mkdir()
    engine = Path(__file__).parents[3] / "libs/installer-engine/installer-engine.sh"
    result = fixture.run_publication_bash(
        [bash, "-c", f"""
set -euo pipefail
. '{engine.as_posix()}'
INSTALL_DIR='{root.as_posix()}'; SRC_VERSION=2.0.0
_fail() {{ printf '%s\\n' "$*" >&2; }}
_warn() {{ printf '%s\\n' "$*" >&2; }}
python() {{ return 97; }}; python3() {{ return 97; }}; _bootstrap_python() {{ return 97; }}
payload="$INSTALL_DIR/payload-dir"; manifest="$INSTALL_DIR/deploy-manifest.json"
fresh="$INSTALL_DIR/new-reader"; current="$INSTALL_DIR/current-version"
printf old-payload > "$payload"; chmod 640 "$payload"
printf old-manifest > "$manifest"; chmod 600 "$manifest"
printf 1.0.0 > "$current"
_runtime_publication_begin "$payload" "$manifest" "$fresh" "$current"
_runtime_publication_write_value "$payload" new-payload
_runtime_publication_write_value "$manifest" new-manifest
if [[ '{case}' != foreign-created ]]; then _runtime_publication_write_value "$fresh" new-reader; fi
evidence="$_RUNTIME_PUBLICATION_EVIDENCE"
case '{case}' in
    foreign-payload|foreign-same-bytes)
        if [[ '{case}' == foreign-same-bytes ]]; then printf new-payload > "$INSTALL_DIR/peer";
        else printf peer > "$INSTALL_DIR/peer"; fi
        command mv "$INSTALL_DIR/peer" "$payload"; foreign="$payload" ;;
    foreign-manifest)
        printf peer > "$INSTALL_DIR/peer"; command mv "$INSTALL_DIR/peer" "$manifest"; foreign="$manifest" ;;
    foreign-new|foreign-created)
        printf peer > "$INSTALL_DIR/peer"; command mv "$INSTALL_DIR/peer" "$fresh"; foreign="$fresh" ;;
    foreign-current)
        printf 1.0.0 > "$INSTALL_DIR/peer"; command mv "$INSTALL_DIR/peer" "$current"; foreign="$current" ;;
    foreign-inplace) printf peer > "$manifest"; foreign="$manifest" ;;
    foreign-mode) chmod 400 "$payload"; foreign="$payload" ;;
    foreign-delete) command rm "$payload"; foreign="$payload" ;;
    foreign-directory) command rm "$payload"; mkdir "$payload"; foreign="$payload" ;;
    foreign-symlink)
        printf peer > "$INSTALL_DIR/peer-target"
        command rm "$payload"; ln -s "$INSTALL_DIR/peer-target" "$payload"; foreign="$payload" ;;
    backup-tamper) printf corrupt > "${{_RUNTIME_PUBLICATION_BACKUPS[0]}}" ;;
esac
if [[ '{case}' == foreign-* ]]; then
    peer_before="$(_runtime_publication_state "$foreign")" || peer_before=unsafe
    if _runtime_publication_restore; then exit 90; fi
    peer_after="$(_runtime_publication_state "$foreign")" || peer_after=unsafe
    [[ "$peer_after" == "$peer_before" ]]
    [[ -d "$evidence" && -s "$evidence/journal" ]]
    [[ "$(cat "$manifest")" == new-manifest || "$manifest" == "$foreign" ]]
    if [[ "$foreign" != "$payload" ]]; then [[ "$(cat "$payload")" == new-payload ]]; fi
    if [[ '{case}' == foreign-directory ]]; then [[ -d "$payload" ]]; fi
    if [[ '{case}' == foreign-symlink ]]; then [[ -L "$payload" && "$(cat "$INSTALL_DIR/peer-target")" == peer ]]; fi
elif [[ '{case}' == backup-tamper ]]; then
    if _runtime_publication_restore; then exit 91; fi
    [[ "$(cat "$payload")" == new-payload && -s "$evidence/journal" ]]
else
    mv() {{
        if [[ '{case}' == restore-io && "${{@: -2:1}}" == */payload-dir ]]; then return 73; fi
        command mv "$@" || return $?
    }}
    rm() {{
        if [[ '{case}' == remove-io && "${{@: -1}}" == "$fresh" ]]; then return 74; fi
        command rm "$@"
    }}
    if [[ '{case}' == restore-io || '{case}' == remove-io ]]; then
        if _runtime_publication_restore; then exit 92; fi
        [[ -d "$evidence" && -s "$evidence/journal" ]]
        if [[ '{case}' == restore-io ]]; then
            [[ "$(cat "$payload")" == new-payload && "$(cat "$manifest")" == new-manifest ]]
        else
            [[ "$(cat "$payload")" == old-payload && "$(cat "$manifest")" == old-manifest ]]
        fi
        [[ "$(cat "$fresh")" == new-reader && "$(cat "$current")" == 1.0.0 ]]
        exit 0
    fi
    _runtime_publication_restore
    [[ "$(cat "$payload")" == old-payload && "$(cat "$manifest")" == old-manifest ]]
    [[ ! -e "$fresh" && ! -e "$evidence" ]]
fi
[[ "$(cat "$current")" == 1.0.0 ]]
"""],
        tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("rename_error", [73], ids=["rename-then-error"])
@pytest.mark.timeout(60 if os.name == "nt" else 30)
def test_outer_recovery_accepts_rename_then_error(tmp_path: Path, rename_error: int) -> None:
    from .test_install_sh_activation_guard import _resolve_bash

    bash = _resolve_bash()
    if not bash:
        pytest.skip("native Bash unavailable")
    root = tmp_path / "runtime"
    root.mkdir()
    engine = Path(__file__).parents[3] / "libs/installer-engine/installer-engine.sh"
    result = fixture.run_publication_bash(
        [bash, "-c", f"""
set -euo pipefail
. '{engine.as_posix()}'
INSTALL_DIR='{root.as_posix()}'; SRC_VERSION=2.0.0
_fail() {{ printf '%s\\n' "$*" >&2; }}; _warn() {{ printf '%s\\n' "$*" >&2; }}
python() {{ return 97; }}; python3() {{ return 97; }}; _bootstrap_python() {{ return 97; }}
payload="$INSTALL_DIR/payload-dir"; current="$INSTALL_DIR/current-version"
printf old-payload > "$payload"; chmod 640 "$payload"
printf 1.0.0 > "$current"
selector="$(_runtime_publication_state "$current")"
_runtime_publication_begin "$payload" "$current"
_runtime_publication_write_value "$payload" new-payload
evidence="$_RUNTIME_PUBLICATION_EVIDENCE"; renamed=0
mv() {{
    command mv "$@" || return $?
    if [[ "${{@: -2:1}}" == */payload-dir ]]; then
        renamed=1
        return {rename_error}
    fi
}}
_runtime_publication_restore
[[ "$renamed" == 1 && "$(cat "$payload")" == old-payload ]]
[[ "$(_runtime_publication_state "$current")" == "$selector" && ! -e "$evidence" ]]
for directory in "$INSTALL_DIR"/.*.publication-stage.*; do
    [[ ! -e "$directory" && ! -L "$directory" ]]
done
"""],
        tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("operation", ["replace", "remove"])
@pytest.mark.timeout(60 if os.name == "nt" else 30)
def test_owned_recovery_can_retry_after_io_failure(tmp_path: Path, operation: str) -> None:
    from .test_install_sh_activation_guard import _resolve_bash

    bash = _resolve_bash()
    if not bash:
        pytest.skip("native Bash unavailable")
    root = tmp_path / "runtime"
    root.mkdir()
    engine = Path(__file__).parents[3] / "libs/installer-engine/installer-engine.sh"
    result = fixture.run_publication_bash(
        [bash, "-c", f"""
set -euo pipefail
. '{engine.as_posix()}'
INSTALL_DIR='{root.as_posix()}'; SRC_VERSION=2.0.0
_fail() {{ printf '%s\\n' "$*" >&2; }}; _warn() {{ printf '%s\\n' "$*" >&2; }}
target="$INSTALL_DIR/reader"; current="$INSTALL_DIR/current-version"
printf 1.0.0 > "$current"
if [[ '{operation}' == replace ]]; then printf original > "$target"; fi
_runtime_publication_begin "$target" "$current"
_runtime_publication_write_value "$target" candidate
evidence="$_RUNTIME_PUBLICATION_EVIDENCE"
mv() {{ if [[ '{operation}' == replace && "${{@: -2:1}}" == */reader ]]; then return 73; fi; command mv "$@"; }}
rm() {{ if [[ '{operation}' == remove && "${{@: -1}}" == "$target" ]]; then return 74; fi; command rm "$@"; }}
if _runtime_publication_restore; then exit 90; fi
[[ "$(cat "$target")" == candidate && -s "$evidence/journal" ]]
unset -f mv rm
_runtime_publication_restore
if [[ '{operation}' == replace ]]; then [[ "$(cat "$target")" == original ]]; else [[ ! -e "$target" ]]; fi
[[ "$(cat "$current")" == 1.0.0 && ! -e "$evidence" ]]
"""],
        tmp_path=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_actual_legacy_stamp_keeps_python_free_publication(tmp_path: Path) -> None:
    from .test_install_sh_activation_guard import _resolve_bash

    bash = _resolve_bash()
    if not bash:
        pytest.skip("native Bash unavailable")
    scripts = tmp_path / "payload/scripts"
    scripts.mkdir(parents=True)
    engine = Path(__file__).parents[3] / "libs/installer-engine/installer-engine.sh"
    shutil.copy2(engine, scripts / "installer-engine.sh")
    source_helper = Path(__file__).parents[1] / "scripts/source-runtime.sh"
    shutil.copy2(source_helper, scripts / source_helper.name)
    for name in ("resolve-runtime.sh", "resolve-runtime.ps1", "runtime-gate.sh", "runtime-gate.ps1"):
        shutil.copy2(fixture.SCRIPTS / name, scripts / name)
    shutil.copytree(fixture.SCRIPTS / "installation-context", scripts / "installation-context")
    entry = scripts / "install.sh"
    text = fixture.script("sh").replace(
        "_initialize_source_runtime\ncase",
        """
_bootstrap_python() { printf forbidden-python >&2; return 97; }
_find_python() { printf forbidden-python >&2; return 97; }
_initialize_source_runtime
before_mask="$(umask)"
do_stamp || exit $?
[[ "$(umask)" == "$before_mask" ]]
[[ "$(cat "$INSTALL_DIR/payload-dir")" == "$PLUGIN_DIR" ]]
exit 0
case""",
    )
    entry.write_bytes(text.encode("utf-8"))
    env = fixture.environment(tmp_path)
    lock_support = subprocess.run(
        [bash, "-c", """
if command -v flock >/dev/null 2>&1; then printf flock; exit 0; fi
if ln -s 1 "$1/probe-lock" && [[ -L "$1/probe-lock" ]]; then printf pid-link; exit 0; fi
exit 77
""", "probe", tmp_path.as_posix()],
        env=env, cwd=tmp_path, capture_output=True, text=True,
        timeout=10, creationflags=fixture.HEADLESS,
    )
    if lock_support.returncode == 77:
        pytest.skip("stamp requires flock or supported POSIX PID symlinks")
    assert lock_support.returncode == 0, lock_support.stdout + lock_support.stderr
    result = subprocess.run(
        [bash, entry.as_posix(), "stamp"], env=env, cwd=tmp_path,
        capture_output=True, text=True, timeout=20, creationflags=fixture.HEADLESS,
    )
    assert result.returncode == 0, lock_support.stdout + result.stdout + result.stderr
    assert "forbidden-python" not in result.stderr
    root = Path(env["HOME"]) / ".agent-dispatch"
    assert (root / "stamped-version").read_text() == fixture.PACKAGE
    assert (root / "bin/runtime-gate.sh").is_file()
    assert (root / "bin/installation-context/installation-context.sh").is_file()
    assert not list(root.rglob("*.publication.*"))
