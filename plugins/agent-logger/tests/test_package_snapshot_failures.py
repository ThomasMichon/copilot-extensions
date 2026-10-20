"""Native conditional callers must not publish failed package-only snapshots."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from .test_source_runtime_install import (
    REPOSITORY,
    _function,
    _refresh_native_declaration,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="native POSIX snapshot contract")


def _case(tmp_path, plugin="agent-logger"):
    payload = tmp_path / "publisher" / "plugins" / plugin
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    original = REPOSITORY / "plugins" / plugin / "scripts"
    for name in ("install.sh", "install.ps1", "source-runtime.sh", "resolve-runtime.sh", "resolve-runtime.ps1"):
        shutil.copy2(original / name, scripts / name)
    for path in original.glob("versioned_runtime*.*"):
        shutil.copy2(path, scripts / path.name)
    for path in (REPOSITORY / "libs" / "installer-engine").iterdir():
        if path.is_file():
            shutil.copy2(path, scripts / path.name)
    for name, before, after in (
        ("install.sh", '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"',
         '. "$SCRIPT_DIR/installer-engine.sh"'),
        ("install.ps1", ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')",
         ". (Join-Path $PSScriptRoot 'installer-engine.ps1')"),
    ):
        path = scripts / name
        text = path.read_text(encoding="utf-8").replace(before, after)
        if name == "install.sh":
            first, rest = text.split("\n", 1)
            text = first + "\n# snapshot-write-sentinel\n" + rest
        path.write_text(text, encoding="utf-8", newline="\n")
    _refresh_native_declaration(payload)
    (payload / "plugin.json").write_text(
        f'{{"name":"{plugin}","version":"9.8.7"}}\n', encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "{plugin}"\nversion = "9.8.7"\n', encoding="utf-8",
    )
    command = payload / "bin" / plugin
    command.parent.mkdir()
    command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    command.chmod(0o755)
    for lib in ("config-migrate", "agent-procutil", "dropin-registry", "plugin-resolve", "plugin-activation"):
        directory = payload / "libs" / lib
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "{lib}"\nversion = "1.0.0"\n', encoding="utf-8",
        )
        (directory / "input.txt").write_text("declared library input\n", encoding="utf-8")
    (payload / "tests").mkdir()
    (payload / "tests" / "input.txt").write_text("not published\n", encoding="utf-8")
    root = tmp_path / "runtime"
    blocker = tmp_path / "publication-blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    previous = root / "snapshots" / "1.2.2"
    previous.mkdir(parents=True)
    (previous / "healthy.txt").write_bytes(b"healthy previous snapshot")
    for name, value in {
        "payload-dir": str(previous), "stamped-version": "1.2.2",
        "current-version": "1.2.2", "last-known-good": "1.2.2",
    }.items():
        (root / name).write_text(value, encoding="utf-8")
    return SimpleNamespace(
        payload=payload, root=root, previous=previous,
        local_bin=tmp_path / "local-bin", plugin=plugin, blocker=blocker,
    )


def _run_conditional(state, failure, caller="snapshot"):
    functions = ["do_stamp", "_deploy_binstub"] if state.plugin == "agent-vault" else [
        "publish_payload_snapshot", "_materialize_snapshot_libs", "_materialize_snapshot_engine",
        "_resolve_snapshot_engine_source", "_rewrite_snapshot_engine_ref",
        "_snapshot_requires_materialized_engine",
        "do_stamp", "install_package", "deploy_binstub",
        "deploy_auxiliary_compatibility_binstubs", "deploy_runtime_helpers",
    ]
    body = "\n".join(_function(state.plugin, name, "sh") for name in functions)
    operation = "do_stamp" if state.plugin == "agent-vault" else "publish_payload_snapshot"
    operation = {
        "snapshot": operation, "stamp": "do_stamp", "package": "install_package",
        "primary": "deploy_binstub", "auxiliary": "deploy_auxiliary_compatibility_binstubs",
        "helpers": "deploy_runtime_helpers",
    }[caller]
    script = f"""
set -euo pipefail
PLUGIN_DIR={shlex.quote(str(state.payload))}
SCRIPT_DIR="$PLUGIN_DIR/scripts"
INSTALL_DIR={shlex.quote(str(state.root))}
LOCAL_BIN={shlex.quote(str(state.local_bin))}
SRC_VERSION=9.8.7
PACKAGE_VERSION="$SRC_VERSION"
SOURCE_RUNTIME_IDENTITY=""
PUBLISH_GLOBAL_BINSTUBS=1
VENV="$INSTALL_DIR/artifact"
LINK_DIR="$INSTALL_DIR/.venv"
BLOCKER={shlex.quote(str(state.blocker))}
_failure={shlex.quote(failure)}
_stub_target="${{_failure%%:*}}"
_publication_fault="${{_failure#*:}}"
_stage="$INSTALL_DIR/snapshots/$SRC_VERSION.tmp.$$"
_injected() {{ command printf 'injected snapshot failure: %s\\n' "$_failure" >&2; }}
_fail() {{ command printf 'ERROR: %s\\n' "$1" >&2; }}
_ok() {{ command printf 'OK: %s\\n' "$1"; }}
ok() {{ _ok "$1"; }}
cp() {{
    if [[ "$_failure" == payload-copy && "${{1:-}}" == -a && "${{2:-}}" == "$PLUGIN_DIR/." ]]; then
        command cp "$PLUGIN_DIR/plugin.json" "$_stage/plugin.json"
        _injected; return 73
    fi
    if [[ "$_failure" == lib-copy && "${{1:-}}" == -a && "${{2:-}}" == "$PLUGIN_DIR/libs/config-migrate" ]]; then
        _injected; return 73
    fi
    if [[ "$_failure" == engine-sh-copy && "${{1:-}}" == -f && "${{2:-}}" == "$SCRIPT_DIR/installer-engine.sh" ]]; then
        command mv "$_stage/scripts/installer-engine.sh" "$_stage/scripts/installer-engine.sh.unavailable"
        _injected; return 73
    fi
    if [[ "$_failure" == engine-ps-copy && "${{1:-}}" == -f && "${{2:-}}" == "$SCRIPT_DIR/installer-engine.ps1" ]]; then
        command mv "$_stage/scripts/installer-engine.ps1" "$_stage/scripts/installer-engine.ps1.unavailable"
        _injected; return 73
    fi
    command cp "$@" || return "$?"
    if [[ "$_failure" == rewrite-read && "${{1:-}}" == -f && "${{2:-}}" == "$SCRIPT_DIR/installer-engine.sh" ]]; then
        command mv "$_stage/scripts/install.sh" "$_stage/scripts/install.sh.unavailable"
        _injected
    fi
    if [[ "$_failure" == rewrite-prepare && "${{1:-}}" == -f && "${{2:-}}" == "$SCRIPT_DIR/installer-engine.sh" ]]; then
        command mkdir "$_stage/scripts/install.sh.tmp.$$"
        _injected
    fi
    if [[ "$_failure" == rewrite-prepare-link && "${{1:-}}" == -f && "${{2:-}}" == "$SCRIPT_DIR/installer-engine.sh" ]]; then
        command mkdir "$_stage/rewrite-collision"
        command printf preserved > "$_stage/rewrite-collision/keep"
        command ln -s "$_stage/rewrite-collision" "$_stage/scripts/install.sh.tmp.$$"
        _injected
    fi
}}
mkdir() {{
    if [[ "$_failure" == stage-mkdir && "${{2:-}}" == "$_stage" ]]; then _injected; return 73; fi
    if [[ "$_failure" == libraries-mkdir && "${{2:-}}" == "$_stage/libs" ]]; then _injected; return 73; fi
    if [[ "$_failure" == scripts-mkdir && "${{2:-}}" == "$_stage/scripts" ]]; then _injected; return 73; fi
    if [[ "$_failure" == stamp-mkdir && "${{2:-}}" == "$INSTALL_DIR" && "${{3:-}}" == "$LOCAL_BIN" ]]; then _injected; return 73; fi
    command mkdir "$@"
}}
mktemp() {{
    if [[ "$_publication_fault" == staging && "${{1:-}}" == "$LOCAL_BIN/.$_stub_target.XXXXXX" ]]; then
        command mktemp "$BLOCKER/child.XXXXXX"; return "$?"
    fi
    command mktemp "$@"
}}
cat() {{
    local output
    output="$(readlink "/proc/$$/fd/1")"
    if [[ "$_publication_fault" == write && "$output" == "$LOCAL_BIN/.$_stub_target."* ]]; then
        command cat "$@" > "$BLOCKER/child"; return "$?"
    fi
    command cat "$@"
}}
chmod() {{
    if [[ "$_publication_fault" == chmod && "${{2:-}}" == "$LOCAL_BIN/.$_stub_target."* ]]; then
        command chmod +x "$BLOCKER/child"; return "$?"
    fi
    command chmod "$@"
}}
rm() {{
    if [[ "$_failure" == lib-remove && "${{2:-}}" == "$_stage/libs/config-migrate" ]]; then _injected; return 73; fi
    if [[ "$_failure" == prune && "${{2:-}}" == "$_stage/.git" ]]; then _injected; return 73; fi
    command rm "$@"
}}
mv() {{
    if [[ "$_failure" == rewrite-rename && "${{2:-}}" == "$_stage/scripts/install.sh.tmp.$$" ]]; then _injected; return 73; fi
    if [[ "$_failure" == snapshot-rename && "${{1:-}}" == "$_stage" ]]; then _injected; return 73; fi
    if [[ "$_publication_fault" == rename && "${{3:-}}" == "$LOCAL_BIN/$_stub_target" ]]; then
        command mv -f "$2" "$BLOCKER/child"; return "$?"
    fi
    command mv "$@"
}}
printf() {{
    local value
    for value in "$@"; do
        if [[ "$_failure" == rewrite-write && "$value" == '# snapshot-write-sentinel' ]]; then _injected; return 73; fi
    done
    command printf "$@"
}}
. "$SCRIPT_DIR/installer-engine.sh"
. "$SCRIPT_DIR/source-runtime.sh"
{body}
# Build/acquisition is fixed here; the conditional callers and publication bodies are real.
_ensure_uv_index() {{ :; }}
ensure_uv() {{ printf '%s\\n' fixture-provider; }}
_versioned_slot_clean() {{ _RUNTIME_CONSTRUCTION_REUSE=0; }}
_versioned_activate() {{ :; }}
invoke_uv_pip_install_resilient() {{ :; }}
new_signed_venv() {{
    command mkdir -p "$2/bin"
    command printf '#!/bin/sh\\nexit 0\\n' > "$2/bin/python"
    command chmod +x "$2/bin/python"
    command printf 'home = fixture\\n' > "$2/pyvenv.cfg"
}}
chg() {{ :; }}
warn() {{ :; }}
if [[ "$_failure" == engine-source-missing ]]; then
    command mv "$SCRIPT_DIR/installer-engine.sh" "$SCRIPT_DIR/installer-engine.sh.unavailable"
    _injected
fi
if {operation}; then exit 0; else exit 19; fi
"""
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("native Bash unavailable")
    return subprocess.run(
        [bash, "-c", script], capture_output=True, text=True, timeout=15,
        env={**os.environ, "HOME": str(state.root.parent / "home")},
    )


@pytest.mark.parametrize("failure", [
    "payload-copy", "lib-copy", "engine-sh-copy", "engine-ps-copy",
    "stage-mkdir", "lib-remove", "prune", "rewrite-read",
    "rewrite-write", "rewrite-rename", "snapshot-rename", "libraries-mkdir",
    "scripts-mkdir", "engine-source-missing", "rewrite-prepare", "rewrite-prepare-link",
])
def test_conditional_package_snapshot_failure_preserves_previous_publication(tmp_path, failure):
    state = _case(tmp_path)
    before = {
        path.name: (path.stat().st_mtime_ns, path.read_bytes())
        for path in state.root.iterdir() if path.is_file()
    }
    previous = {
        str(path.relative_to(state.previous)): (path.stat().st_mtime_ns, path.read_bytes())
        for path in state.previous.rglob("*") if path.is_file()
    }
    result = _run_conditional(state, failure)
    assert result.returncode == 19, result.stdout + result.stderr
    assert f"injected snapshot failure: {failure}" in result.stderr
    assert "ERROR:" in result.stderr
    assert not (state.root / "snapshots" / "9.8.7").exists()
    assert all(
        ((state.root / name).stat().st_mtime_ns, (state.root / name).read_bytes()) == value
        for name, value in before.items()
    )
    assert {
        str(path.relative_to(state.previous)): (path.stat().st_mtime_ns, path.read_bytes())
        for path in state.previous.rglob("*") if path.is_file()
    } == previous
    assert "OK: snapshot:" not in result.stdout


def test_conditional_package_snapshot_success_materializes_before_publication(tmp_path):
    state = _case(tmp_path)
    result = _run_conditional(state, "none")
    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = state.root / "snapshots" / "9.8.7"
    assert Path((state.root / "payload-dir").read_text().strip()) == snapshot
    assert (state.root / "stamped-version").read_text().strip() == "9.8.7"
    assert (snapshot / "bin" / "agent-logger").is_file()
    assert not (snapshot / "tests").exists()
    assert (snapshot / "scripts" / "installer-engine.sh").is_file()
    assert (snapshot / "scripts" / "installer-engine.ps1").is_file()
    assert (snapshot / "libs" / "config-migrate" / "input.txt").is_file()
    assert not (snapshot / "libs" / "config-migrate" / "config-migrate").exists()


def test_conditional_vault_stamp_preparation_failure_preserves_previous_pointer(tmp_path):
    state = _case(tmp_path, "agent-vault")
    before = {path.name: path.read_bytes() for path in state.root.iterdir() if path.is_file()}
    result = _run_conditional(state, "stamp-mkdir")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "injected snapshot failure: stamp-mkdir" in result.stderr
    assert all((state.root / name).read_bytes() == value for name, value in before.items())


@pytest.mark.parametrize("caller", ["stamp", "package"])
@pytest.mark.parametrize("target", ["agent-logger", "session-sync"])
@pytest.mark.parametrize("fault", ["staging", "write", "chmod", "rename", "directory"])
def test_logger_conditional_callers_reject_real_custom_writer_failures(tmp_path, caller, target, fault):
    state = _case(tmp_path)
    state.local_bin.mkdir()
    destination = state.local_bin / target
    if fault == "directory":
        destination.mkdir()
        (destination / "previous.txt").write_bytes(b"previous")
    else:
        destination.write_bytes(b"previous executable")
        destination.chmod(0o755)
    result = _run_conditional(state, target + ":" + fault, caller)
    assert result.returncode == 19, result.stdout + result.stderr
    assert "stamped:" not in result.stdout
    assert "published compatibility binstubs" not in result.stdout
    if fault == "directory":
        assert (destination / "previous.txt").read_bytes() == b"previous"
    else:
        assert destination.read_bytes() == b"previous executable"


@pytest.mark.parametrize("caller", ["stamp", "package", "primary", "auxiliary"])
def test_logger_conditional_callers_reject_real_publication_directory_failure(tmp_path, caller):
    state = _case(tmp_path)
    state.local_bin.write_text("not a directory", encoding="utf-8")
    result = _run_conditional(state, "directory-preparation", caller)
    assert result.returncode == 19, result.stdout + result.stderr
    assert "stamped:" not in result.stdout
    assert "published compatibility binstubs" not in result.stdout


def _run_units(tmp_path, action, fault):
    root = tmp_path / "runtime"
    root.mkdir()
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    units = blocker / "units" if fault == "mkdir" else tmp_path / "units"
    if fault != "mkdir":
        units.mkdir()
    if fault.startswith(("directory-", "symlink-")):
        member = units / ("logger-fixture." + fault.split("-", 1)[1])
        if fault.startswith("symlink-"):
            collision = tmp_path / "unit-collision"
            collision.mkdir()
            member.symlink_to(collision, target_is_directory=True)
        else:
            member.mkdir()
        (member / "keep").write_text("preserved", encoding="utf-8")
    trace = tmp_path / "systemctl-calls"
    text = (REPOSITORY / "plugins" / "agent-logger" / "scripts" / "install.sh").read_text()
    dispatch = text[text.rindex('case "${ACTION}" in'):]
    script = f"""
set -euo pipefail
INSTALL_DIR={shlex.quote(str(root))}
UNIT_DIR={shlex.quote(str(units))}
VENV="$INSTALL_DIR/absent-runtime"
TIMER_NAME=logger-fixture
BLOCKER={shlex.quote(str(blocker))}
TRACE={shlex.quote(str(trace))}
FAULT={shlex.quote(fault)}
ACTION={shlex.quote(action)}
SKIP_PACKAGE_INSTALL=1
systemctl() {{
    printf '%s\\n' "$*" >> "$TRACE"
    [[ "$FAULT" != reload || "${{2:-}}" != daemon-reload ]]
}}
_fail() {{ printf 'FAIL: %s\\n' "$1" >&2; }}
ok() {{ printf 'OK: %s\\n' "$1"; }}
chg() {{ :; }}
cat() {{
    local output
    output="$(readlink "/proc/$$/fd/1")"
    if [[ "$FAULT" == write-service && "$output" == *'.service.'* ||
          "$FAULT" == write-timer && "$output" == *'.timer.'* ]]; then
        command cat "$@" > "$BLOCKER/child"; return "$?"
    fi
    command cat "$@"
}}
mv() {{
    if [[ "$FAULT" == rename-service && "${{3:-}}" == *'.service' ||
          "$FAULT" == rename-timer && "${{3:-}}" == *'.timer' ]]; then
        command mv -f "$2" "$BLOCKER/child"; return "$?"
    fi
    command mv "$@"
}}
{_function('agent-logger', 'write_units', 'sh')}
run_action() {{
{dispatch}
}}
if run_action; then exit 0; else exit 19; fi
"""
    result = subprocess.run([shutil.which("bash"), "-c", script],
                            capture_output=True, text=True, timeout=10)
    calls = trace.read_text().splitlines() if trace.exists() else []
    if fault.startswith(("directory-", "symlink-")):
        assert {p.name for p in member.iterdir()} == {"keep"}
        assert (member / "keep").read_text() == "preserved"
        assert member.is_symlink() == fault.startswith("symlink-")
    return result, calls


@pytest.mark.parametrize("action", ["install", "update"])
@pytest.mark.parametrize("fault", [
    "mkdir", "write-service", "write-timer", "rename-service", "rename-timer",
    "directory-service", "directory-timer", "symlink-service", "symlink-timer",
])
def test_logger_unit_publication_failure_stops_conditional_dispatch_before_systemctl(tmp_path, action, fault):
    result, calls = _run_units(tmp_path, action, fault)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "FAIL: Logger" in result.stderr
    assert not calls
    assert "timer enabled" not in result.stdout
    assert "package + units updated" not in result.stdout


@pytest.mark.parametrize("action", ["install", "update"])
def test_logger_unit_reload_failure_preserves_existing_strict_or_best_effort_policy(tmp_path, action):
    result, calls = _run_units(tmp_path, action, "reload")
    assert "--user daemon-reload" in calls
    if action == "install":
        assert result.returncode != 0
        assert "timer enabled" not in result.stdout
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "package + units updated" in result.stdout


@pytest.mark.parametrize("linked", [False, True], ids=["directory", "symlink-directory"])
@pytest.mark.parametrize(("caller", "member"), [
    ("primary", "agent-logger"), ("auxiliary", "ramp-up-session"),
    ("stamp", "ramp-up-session"), ("package", "ramp-up-session"),
])
def test_custom_command_directory_collision_preserves_peers_and_callers_fail(tmp_path, linked, caller, member):
    state = _case(tmp_path)
    state.local_bin.mkdir()
    names = ("agent-logger", "session-sync", "collate-session", "read-session-digest",
             "prepare-session-log", "ramp-up-session")
    for name in names:
        (state.local_bin / name).write_bytes(("old-" + name).encode())
    destination = state.local_bin / member
    destination.unlink()
    directory = tmp_path / "collision"
    directory.mkdir()
    (directory / "keep").write_bytes(b"preserved")
    if linked:
        destination.symlink_to(directory, target_is_directory=True)
    else:
        destination.mkdir()
        (destination / "keep").write_bytes(b"preserved")
        directory = destination
    before = {name: (state.local_bin / name).read_bytes() for name in names if name != member}
    result = _run_conditional(state, "none", caller)
    assert result.returncode == 19, result.stdout + result.stderr
    assert "directory" in result.stderr
    assert {path.name for path in directory.iterdir()} == {"keep"}
    assert (directory / "keep").read_bytes() == b"preserved"
    assert all((state.local_bin / name).read_bytes() == value for name, value in before.items())
    assert destination.is_symlink() == linked


@pytest.mark.parametrize("linked", [False, True], ids=["directory", "symlink-directory"])
@pytest.mark.parametrize("member", [
    "resolve-runtime.ps1", "resolve-runtime.sh", "versioned_runtime.py",
    "versioned_runtime_source.py", "versioned_runtime_leases.py", "versioned_runtime_construction.py",
    "versioned_runtime_publication.py",
])
def test_custom_resolver_directory_collision_preflights_all_peers(tmp_path, linked, member):
    state = _case(tmp_path)
    target = state.root / "bin"
    target.mkdir()
    names = [p.name for p in (state.payload / "scripts").iterdir()
             if p.name in {"resolve-runtime.ps1", "resolve-runtime.sh", "versioned_runtime.py",
                           "versioned_runtime_source.py", "versioned_runtime_leases.py",
                           "versioned_runtime_publication.py",
                           "versioned_runtime_construction.py"}]
    for name in names:
        (target / name).write_bytes(("old-" + name).encode())
    destination = target / member
    destination.unlink()
    collision = tmp_path / "collision"
    collision.mkdir()
    (collision / "keep").write_bytes(b"preserved")
    if linked:
        destination.symlink_to(collision, target_is_directory=True)
    else:
        destination.mkdir()
        (destination / "keep").write_bytes(b"preserved")
        collision = destination
    before = {name: (target / name).read_bytes() for name in names if name != member}
    result = _run_conditional(state, "none", "helpers")
    assert result.returncode == 19, result.stdout + result.stderr
    assert {p.name for p in collision.iterdir()} == {"keep"}
    assert all((target / name).read_bytes() == data for name, data in before.items())
    assert destination.is_symlink() == linked
