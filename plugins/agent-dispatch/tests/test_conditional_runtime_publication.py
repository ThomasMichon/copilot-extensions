"""Required publication failures stay failures with conditional Bash callers."""
from __future__ import annotations

import os
import json
import importlib.util
import shutil
import shlex
import stat
import subprocess
import sys
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


@pytest.mark.parametrize("home", ["fixture", "relative", "missing"])
def test_fixture_posix_home_binding_preserves_canonical_input_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, home: str,
) -> None:
    scripts = tmp_path / "scripts"
    copied = scripts / "installation-context"
    shutil.copytree(fixture.SCRIPTS / "installation-context", copied)
    adapter = (copied / "installation-context.ps1").read_bytes()
    canonical = (fixture.SCRIPTS / "installation-context" / "_installation_context_resolution.py").read_bytes()
    fixture.bind_posix_account_home(scripts)
    assert (copied / "installation-context.ps1").read_bytes() == adapter
    assert (fixture.SCRIPTS / "installation-context" / "_installation_context_resolution.py").read_bytes() == canonical
    spec = importlib.util.spec_from_file_location(
        "_dispatch_fixture_posix_home_sdk", copied / "installation_context.py",
    )
    assert spec and spec.loader
    sdk = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, sdk)
    spec.loader.exec_module(sdk)
    env = fixture.environment(tmp_path)
    expected = Path(env["HOME"]).resolve()
    if home != "fixture":
        env["HOME"] = "relative-home" if home == "relative" else str(tmp_path / "missing-home")
        message = "must be absolute" if home == "relative" else "Cannot resolve path|Path does not exist"
        with pytest.raises(sdk.InstallationContextError, match=message):
            sdk._current_environment(environment=env, os_profile=None, platform="posix", wsl_distro=None)
    else:
        record, profile = sdk._current_environment(
            environment=env, os_profile=None, platform="posix", wsl_distro=None,
        )
        assert profile == expected
        assert record == {"platform": "posix", "homeRealPath": str(expected), "wslDistro": None}


def test_fixture_posix_home_binding_rejects_modified_canonical_input(tmp_path: Path) -> None:
    scripts = tmp_path / "scripts"
    copied = scripts / "installation-context"
    shutil.copytree(fixture.SCRIPTS / "installation-context", copied)
    helper = copied / "_installation_context_resolution.py"
    foreign = helper.read_bytes() + b"\n# foreign fixture modification\n"
    helper.write_bytes(foreign)
    with pytest.raises(AssertionError, match="unchanged canonical SDK fragment"):
        fixture.bind_posix_account_home(scripts)
    assert helper.read_bytes() == foreign


def _admission_source(tmp_path: Path, missing: str | None = None) -> Path:
    scripts = tmp_path / "payload" / "scripts"
    scripts.mkdir(parents=True)
    if missing != "directory":
        bundle = scripts / "installation-context"
        bundle.mkdir()
        for name in fixture.mandatory_admission_files():
            if name != missing:
                (bundle / name).write_text(f"new:{name}\n", encoding="utf-8")
        (bundle / "optional-extra.txt").write_text("optional\n", encoding="utf-8")
    for name in (
        "resolve-runtime.sh", "resolve-runtime.ps1", "runtime-gate.sh", "runtime-gate.ps1",
        "delegated-waiter-callback.sh", "delegated-waiter-callback.ps1",
    ):
        (scripts / name).write_text(f"new:{name}\n", encoding="utf-8")
    return scripts


def _admission_bash(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    from .test_install_sh_activation_guard import _resolve_bash

    bash = _resolve_bash()
    if not bash:
        pytest.skip("native Bash unavailable")
    definitions = "\n".join(fixture.function("sh", name) for name in (
        "_runtime_admission_preflight", "deploy_binstub", "do_stamp", "do_install",
        "do_update", "_ensure_runtime",
    ))
    harness = tmp_path / "admission-bundle.sh"
    harness.write_bytes(("set -euo pipefail\n"
                        '_fail() { printf "%s\\n" "$*" >&2; }\n'
                        '_ok() { :; }; _warn() { printf "%s\\n" "$*" >&2; }\n'
                        + definitions + "\n" + body).encode("utf-8"))
    return subprocess.run(
        [bash, str(harness)],
        cwd=tmp_path, env=fixture.environment(tmp_path), capture_output=True,
        text=True, timeout=90 if os.name == "nt" else 20, creationflags=fixture.HEADLESS,
    )


@pytest.mark.parametrize("missing", ["directory", *fixture.mandatory_admission_files()])
def test_admission_bundle_missing_input_has_no_publication_effects(tmp_path: Path, missing: str) -> None:
    scripts = _admission_source(tmp_path, missing)
    root = tmp_path / "runtime"
    root.mkdir()
    for name in ("current-version", "payload-dir", "stamped-version", "machine"):
        (root / name).write_text(f"healthy:{name}", encoding="utf-8")
    before = snapshot(list(root.iterdir()))
    result = _admission_bash(tmp_path, f"""
SCRIPT_DIR={shlex.quote(scripts.as_posix())}
INSTALL_DIR={shlex.quote(root.as_posix())}
LOCAL_BIN={shlex.quote((tmp_path / "localbin").as_posix())}
VERSIONED_RUNTIME=1
for action in do_stamp do_install do_update _ensure_runtime deploy_binstub; do
    if "$action"; then exit 91; else [[ "$?" == 1 ]] || exit 92; fi
done
INSTALL_DIR={shlex.quote((tmp_path / "fresh-runtime").as_posix())}
for action in do_stamp do_install do_update _ensure_runtime deploy_binstub; do
    if "$action"; then exit 93; else [[ "$?" == 1 ]] || exit 94; fi
done
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.count("Required runtime admission") == 10
    assert ("installation-context" if missing == "directory" else missing) in result.stderr
    assert snapshot(list(root.iterdir())) == before
    assert not (tmp_path / "fresh-runtime").exists()
    assert not (tmp_path / "localbin").exists()


def test_admission_bundle_preflight_precedes_lifecycle_effects() -> None:
    text = (fixture.SCRIPTS / "install.sh").read_text(encoding="utf-8")
    dispatch = text[text.index('_LIFECYCLE_ACTION=""'):]
    assert dispatch.index("_runtime_admission_preflight") < dispatch.index("_lifecycle_emit begin")
    assert dispatch.index("_runtime_admission_preflight") < dispatch.index("reconcile_installer_instances")


@pytest.mark.parametrize("case", ["complete", "scoped", "copy-failure", "copy-failure-peer"])
@pytest.mark.timeout(120 if os.name == "nt" else 30)
def test_admission_bundle_complete_copy_and_owned_recovery(tmp_path: Path, case: str) -> None:
    scripts = _admission_source(tmp_path)
    root = tmp_path / "runtime"
    bin_dir = tmp_path / "localbin"
    support = root / "bin" / "installation-context"
    support.mkdir(parents=True)
    bin_dir.mkdir()
    paths = [root / name for name in (
        "machine", "payload-dir", "stamped-version", "current-version", "last-known-good",
    )] + [bin_dir / "agent-dispatch", bin_dir / "agent-dispatch-board"]
    paths.extend(support / name for name in fixture.mandatory_admission_files())
    paths.append(support / "optional-extra.txt")
    paths.extend(root / "bin" / name for name in (
        "resolve-runtime.sh", "resolve-runtime.ps1", "runtime-gate.sh", "runtime-gate.ps1",
    ))
    for path in paths:
        path.write_text(f"healthy:{path.name}\n", encoding="utf-8")
    before = snapshot(paths)
    legacy = tmp_path / "legacy" if case == "scoped" else root
    legacy.mkdir(exist_ok=True)
    body = f"""
SCRIPT_DIR={shlex.quote(scripts.as_posix())}
INSTALL_DIR={shlex.quote(root.as_posix())}
LEGACY_INSTALL_DIR={shlex.quote(legacy.as_posix())}
LOCAL_BIN={shlex.quote(bin_dir.as_posix())}
STUB="$LOCAL_BIN/agent-dispatch"; BOARD_STUB="$LOCAL_BIN/agent-dispatch-board"
PLUGIN_DIR=""; SOURCE_RUNTIME_IDENTITY=""
AGENT_DISPATCH_SUPERVISE_MACHINE=fixture
. {shlex.quote((fixture.REPO / "libs" / "installer-engine" / "installer-engine.sh").as_posix())}
. {shlex.quote((fixture.SCRIPTS / "source-runtime.sh").as_posix())}
# Full-set journaling is covered by the contained installer cases below.
# Keep this portable copy/refusal test bounded to its actual failure target.
_runtime_publication_begin "$INSTALL_DIR/machine" \
    "$INSTALL_DIR/bin/installation-context/json-query.awk" "$STUB" "$BOARD_STUB"
if [[ '{case}' == copy-failure* ]]; then
    real_copy="$(declare -f _runtime_publication_copy)"
    eval "${{real_copy/_runtime_publication_copy/_admission_real_copy}}"
    _runtime_publication_copy() {{
        [[ "$1" == "$SCRIPT_DIR/installation-context/json-query.awk" ]] || return 0
        _admission_real_copy "$@" || return 1
    }}
fi
copy_failed=0
cp() {{
    if [[ "$copy_failed" == 0 && '{case}' == copy-failure* &&
          "${{@: -1}}" == "$INSTALL_DIR/bin/installation-context/.json-query.awk.publication-stage."*/json-query.awk ]]; then
        copy_failed=1
        command head -c 4 "${{@: -2:1}}" > "${{@: -1}}" || return 1
        if [[ '{case}' == copy-failure-peer ]]; then
            printf peer-machine > "$INSTALL_DIR/peer-machine"
            command mv "$INSTALL_DIR/peer-machine" "$INSTALL_DIR/machine" || return 1
        fi
        return 73
    fi
    command cp "$@"
}}
if deploy_binstub; then
    [[ '{case}' == complete || '{case}' == scoped ]] || exit 91
    _runtime_publication_discard || exit 92
else
    [[ '{case}' == copy-failure* ]] || exit 93
    if [[ '{case}' == copy-failure-peer ]]; then
        peer_before="$(_runtime_publication_state "$INSTALL_DIR/machine")"
        if _runtime_publication_restore; then exit 94; fi
        [[ "$(_runtime_publication_state "$INSTALL_DIR/machine")" == "$peer_before" ]] || exit 95
    else
        _runtime_publication_restore || exit 96
    fi
fi
"""
    result = _admission_bash(tmp_path, body)
    assert result.returncode == 0, result.stdout + result.stderr
    if case == "copy-failure-peer":
        assert (root / "machine").read_bytes() == b"peer-machine"
        assert list(root.glob(".publication.????????/journal"))
        assert "Unowned publication state" in result.stderr
    elif case == "copy-failure":
        assert snapshot(paths) == before
        assert not list(root.rglob("*.publication.*"))
        assert not list(bin_dir.glob("*.publication.*"))
    else:
        for path in support.iterdir():
            assert path.read_bytes() == (scripts / "installation-context" / path.name).read_bytes()
        assert {path.name for path in support.iterdir()} == {
            *fixture.mandatory_admission_files(), "optional-extra.txt",
        }
        if case == "scoped":
            assert snapshot([bin_dir / "agent-dispatch", bin_dir / "agent-dispatch-board"]) == {
                path: value for path, value in before.items() if path.parent == bin_dir
            }
        else:
            assert "runtime-gate.sh" in (bin_dir / "agent-dispatch").read_text(encoding="utf-8")


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
    paths.extend(
        root / "bin/installation-context" / support.name
        for support in (fixture.SCRIPTS / "installation-context").iterdir()
        if support.is_file()
    )
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
    for support in (fixture.SCRIPTS / "installation-context").iterdir():
        if support.is_file():
            assert (root / "bin/installation-context" / support.name).read_bytes() == support.read_bytes()
    assert not list(root.rglob("*.publication.*"))
