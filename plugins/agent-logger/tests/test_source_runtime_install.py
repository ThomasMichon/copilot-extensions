"""Native Logger/Vault source adoption using the existing offline installers."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import runpy
import shlex
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

REPOSITORY = Path(__file__).resolve().parents[3]
PLUGINS = ("agent-logger", "agent-vault")
_spec = importlib.util.spec_from_file_location(
    "logger_vault_source_identity",
    REPOSITORY / "libs" / "versioned-runtime" / "versioned_runtime_source.py",
)
assert _spec and _spec.loader
source = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = source
_spec.loader.exec_module(source)


def _ps(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run(argv, *, env=None, cwd=None):
    return subprocess.run(
        argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def _stage(tmp_path: Path, plugin: str):
    test = "test_install_binstub.py" if plugin == "agent-logger" else "test_install_snapshot_ps1.py"
    fixtures = runpy.run_path(str(REPOSITORY / "plugins" / plugin / "tests" / test))
    stage = fixtures["_stage_release_payload"] if plugin == "agent-logger" else fixtures["_stage_payload"]
    payload = stage(tmp_path)
    if plugin == "agent-vault":
        previous = sys.path[:]
        try:
            materializer = runpy.run_path(str(REPOSITORY / "tools" / "materialize_main.py"))
            messages = materializer["mie"].materialize_ref_into(
                source_consumer_dir=REPOSITORY / "plugins" / plugin,
                dest_consumer_dir=payload, canonical_root=REPOSITORY,
            )
        finally:
            sys.path[:] = previous
        assert not any(message.startswith("SKIP ") for message in messages), messages
    return payload, fixtures


def _small_stage(tmp_path: Path, plugin: str):
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    original = REPOSITORY / "plugins" / plugin / "scripts"
    names = {
        "install.ps1", "install.sh", "source-runtime.ps1", "source-runtime.sh",
        "resolve-runtime.ps1", "resolve-runtime.sh",
        "installer_dependency_policy.py", "installer_dependency_config.py",
    }
    names.update(path.name for path in original.glob("versioned_runtime*.*"))
    if plugin == "agent-logger":
        names.update(("command_binding.py", "command_lifecycle.py"))
        shutil.copytree(original / "installation-context", scripts / "installation-context")
        shutil.copytree(original / "_lifecycle_human", scripts / "_lifecycle_human")
    for name in names:
        shutil.copy2(original / name, scripts / name)
    for path in (REPOSITORY / "libs" / "installer-engine").iterdir():
        if path.is_file() and path.suffix in (".sh", ".ps1", ".py"):
            shutil.copy2(path, scripts / path.name)
    for name, before, after in (
        ("install.sh", '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"',
         '. "$SCRIPT_DIR/installer-engine.sh"'),
        ("install.ps1", ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')",
         ". (Join-Path $PSScriptRoot 'installer-engine.ps1')"),
    ):
        path = scripts / name
        path.write_text(path.read_text(encoding="utf-8").replace(before, after), encoding="utf-8", newline="\n")
    (payload / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": "1.2.3"}), encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        f'[project]\nname = "{plugin}"\nversion = "1.2.3"\n', encoding="utf-8",
    )
    (payload / "README.md").write_text("Synthetic native source payload.\n", encoding="utf-8")
    _refresh_native_declaration(payload)
    test = "test_install_binstub.py" if plugin == "agent-logger" else "test_install_snapshot_ps1.py"
    fixtures = runpy.run_path(str(REPOSITORY / "plugins" / plugin / "tests" / test))
    return payload, fixtures


def _receipt(payload: Path, plugin: str, commit: str = "a" * 40):
    version = json.loads((payload / "plugin.json").read_text(encoding="utf-8"))["version"]
    digest = source.assembly_hash(payload)
    identity = source.SourceIdentity(
        plugin, version, commit, "b" * 40, digest,
        source.runtime_version(version, commit, digest),
    )
    (payload / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    return identity


def _refresh_native_declaration(payload: Path):
    previous = sys.path[:]
    try:
        sys.path.insert(0, str(REPOSITORY / "tools"))
        contract = runpy.run_path(str(REPOSITORY / "tools" / "installer_engine_ref.py"))
        installer = payload / "scripts" / "install.ps1"
        installer.with_suffix(".human-output.json").write_text(
            contract["human_output_json"](installer), encoding="utf-8",
        )
    finally:
        sys.path[:] = previous


def _native(payload: Path, action: str, plugin: str):
    if os.name == "nt":
        host = shutil.which("pwsh") or shutil.which("powershell")
        if not host:
            pytest.skip("PowerShell unavailable")
        argv = [host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(payload / "scripts" / "install.ps1"), action]
        if plugin == "agent-vault" and action == "provision":
            argv += ["-NoService"]
        return argv
    if not shutil.which("bash"):
        pytest.skip("Bash unavailable")
    argv = ["bash", str(payload / "scripts" / "install.sh"), action]
    if plugin == "agent-vault" and action == "provision":
        argv += ["--no-service"]
    return argv


def _inventory(root: Path):
    return {
        str(path.relative_to(root)): (path.stat().st_mtime_ns, path.read_bytes())
        for path in root.rglob("*") if path.is_file()
        and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".pyo")
    }


@pytest.fixture(scope="module", params=PLUGINS)
def installed_source_generation(tmp_path_factory, request):
    plugin = request.param
    tmp_path = tmp_path_factory.mktemp(f"source-install-{plugin}")
    publisher = tmp_path / "publisher"
    payload, fixtures = _stage(publisher, plugin)
    relative_payload = payload.relative_to(publisher)
    identity = _receipt(payload, plugin)
    home = tmp_path / "home"
    env = fixtures["_isolated_install_env"](home)
    fixtures["_offline_uv"](tmp_path, env)
    root = home / ("." + plugin)
    stamped = _run(_native(payload, "stamp", plugin), env=env, cwd=home)
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    snapshot = Path((root / "payload-dir").read_text().strip())
    assert snapshot == root / "snapshots" / identity.runtime_version
    assert source.read_source_identity(snapshot, identity.package_version, plugin=plugin) == identity
    assert not (root / "versions").exists()
    before = _inventory(snapshot)
    redirects = {name: (root / name).read_bytes() for name in ("payload-dir", "stamped-version")}
    unavailable = tmp_path / "publisher-unavailable"
    publisher.rename(unavailable)
    assert not publisher.exists() and not payload.exists()
    assert not root.resolve().is_relative_to(unavailable.resolve())
    env["FIXTURE_SNAPSHOT_ROOT"] = str(snapshot)
    provisioned = _run(_native(snapshot, "provision", plugin), env=env, cwd=home)
    assert provisioned.returncode == 0, provisioned.stdout + provisioned.stderr
    assert (root / "current-version").read_text().strip() == identity.runtime_version
    slot = root / "versions" / identity.runtime_version
    completion = slot / ".install-complete.json"
    completed = (completion.stat().st_mtime_ns, completion.read_bytes())
    assert json.loads(completed[1])["version"] == identity.runtime_version
    return SimpleNamespace(
        plugin=plugin, tmp_path=tmp_path, publisher=publisher,
        failed_payload=unavailable / relative_payload, identity=identity,
        home=home, env=env, root=root, snapshot=snapshot,
        before=before, redirects=redirects, completion=completion, completed=completed,
    )


@pytest.mark.portfolio_tier("T3")
@pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_SOURCE_INSTALL_FULL") != "1",
    reason="explicit full source installation lane",
)
@pytest.mark.timeout(240)
def test_source_stamp_provision_reuse_and_commands_survive_source_removal(installed_source_generation):
    state = installed_source_generation
    plugin, home, env = state.plugin, state.home, state.env
    root, snapshot, identity = state.root, state.snapshot, state.identity
    completion, completed = state.completion, state.completed
    before, redirects = state.before, state.redirects
    assert not state.publisher.exists()
    repeated = _run(_native(snapshot, "stamp", plugin), env=env, cwd=home)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert _inventory(snapshot) == before
    assert all((root / name).read_bytes() == value for name, value in redirects.items())
    reused = _run(_native(snapshot, "provision", plugin), env=env, cwd=home)
    assert reused.returncode == 0, reused.stdout + reused.stderr
    assert (completion.stat().st_mtime_ns, completion.read_bytes()) == completed
    assert _inventory(snapshot) == before
    assert all((root / name).read_bytes() == value for name, value in redirects.items())

    command = "agent-logger" if plugin == "agent-logger" else "agent-vault"
    args = ["version"] if plugin == "agent-logger" else ["--version"]
    if os.name == "nt":
        invoke = [_native(snapshot, "stamp", plugin)[0], "-NoProfile", "-NonInteractive",
                  "-ExecutionPolicy", "Bypass",
                  "-File", str(home / ".local" / "bin" / (command + ".ps1")), *args]
    else:
        invoke = [str(home / ".local" / "bin" / command), *args]
    version = _run(invoke, env=env, cwd=home)
    assert version.returncode == 0, version.stdout + version.stderr
    assert identity.runtime_version in version.stdout
    if plugin == "agent-logger":
        auxiliary = list(invoke)
        auxiliary[auxiliary.index(str(home / ".local" / "bin" / (command + (".ps1" if os.name == "nt" else ""))))] = str(
            home / ".local" / "bin" / ("session-sync.ps1" if os.name == "nt" else "session-sync")
        )
        auxiliary[-1] = "--help"
        result = _run(auxiliary, env=env, cwd=home)
        assert result.returncode == 0, result.stdout + result.stderr
    elif os.name != "nt":
        askpass = home / ".local" / "bin" / ("vault-askpass.cmd" if os.name == "nt" else "vault-askpass")
        assert askpass.is_file()


@pytest.mark.portfolio_tier("T3")
@pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_SOURCE_INSTALL_FULL") != "1",
    reason="explicit full source installation lane",
)
@pytest.mark.timeout(240)
def test_failed_source_candidate_preserves_healthy_runtime(installed_source_generation):
    state = installed_source_generation
    plugin, home, env = state.plugin, state.home, state.env
    root, snapshot, identity = state.root, state.snapshot, state.identity
    failed_payload, tmp_path = state.failed_payload, state.tmp_path
    completion = state.completion
    assert not state.publisher.exists()
    assert (root / "current-version").read_text().strip() == identity.runtime_version
    completed = (completion.stat().st_mtime_ns, completion.read_bytes())
    redirects = {name: (root / name).read_bytes() for name in ("payload-dir", "stamped-version")}
    selected_markers = {
        name: (root / name).read_bytes() for name in ("current-version", "last-known-good")
    }
    runtime_helpers = _inventory(root / "bin")
    command_wrappers = _inventory(home / ".local" / "bin")
    snapshot_before = _inventory(snapshot)
    failed_identity = _receipt(failed_payload, plugin, "c" * 40)
    driver = tmp_path / "offline-uv" / "driver.py"
    text = driver.read_text(encoding="utf-8")
    anchor = "args = sys.argv[1:]\n"
    assert text.count(anchor) == 1
    text = text.replace(anchor, anchor + (
        "if os.environ.get('FIXTURE_SOURCE_BUILD_FAIL') and "
        "args[:2] == ['pip', 'install'] and '--show-settings' not in args:\n"
        "    sys.stderr.write('injected candidate build failure\\n')\n"
        "    sys.exit(23)\n"
    ))
    driver.write_text(text, encoding="utf-8")
    if os.name != "nt" and plugin == "agent-logger":
        (tmp_path / "offline-uv" / "uv").write_text(text, encoding="utf-8")
    failed = _run(
        _native(failed_payload, "provision", plugin),
        env={**env, "FIXTURE_SOURCE_BUILD_FAIL": "1"}, cwd=home,
    )
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert "injected candidate build failure" in failed.stdout + failed.stderr
    assert (root / "current-version").read_text().strip() == identity.runtime_version
    assert (completion.stat().st_mtime_ns, completion.read_bytes()) == completed
    assert all((root / name).read_bytes() == value for name, value in redirects.items())
    assert all((root / name).read_bytes() == value for name, value in selected_markers.items())
    assert _inventory(root / "bin") == runtime_helpers
    assert _inventory(home / ".local" / "bin") == command_wrappers
    assert _inventory(snapshot) == snapshot_before
    assert not (root / "versions" / failed_identity.runtime_version / ".install-complete.json").exists()


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("mutation", ["receipt", "content"])
def test_actual_outer_lock_rejects_changed_source_without_touching_selection(tmp_path, plugin, mutation):
    payload, fixtures = _small_stage(tmp_path, plugin)
    suffix = "ps1" if os.name == "nt" else "sh"
    installer = payload / "scripts" / ("install." + suffix)
    text = installer.read_text(encoding="utf-8")
    if os.name == "nt":
        if mutation == "receipt":
            mutate = (
                "$p = Join-Path $PluginDir 'runtime-source.json'\n"
                f"[IO.File]::WriteAllText($p, ([IO.File]::ReadAllText($p)).Replace({'b' * 40!r}, {'c' * 40!r}))\n"
            )
        else:
            mutate = "[IO.File]::AppendAllText((Join-Path $PluginDir 'README.md'), 'changed')\n"
        anchor = "        Assert-SourceRuntime\n        if ($script:SourceRuntimeIdentity) { return }" if plugin == "agent-logger" else (
            "    if ($Action -in @('install', 'update', 'provision', 'stamp')) { Assert-SourceRuntime }"
        )
    else:
        if mutation == "receipt":
            mutate = (
                f"sed -i 's/{'b' * 40}/{'c' * 40}/' \"$PLUGIN_DIR/runtime-source.json\"\n"
            )
        else:
            mutate = "printf changed >> \"$PLUGIN_DIR/README.md\"\n"
        anchor = "        _assert_source_runtime || exit 1\n    fi\n    if [[" if plugin == "agent-logger" else (
            "    install|update|provision|stamp) _assert_source_runtime || exit 1 ;;"
        )
    assert text.count(anchor) == 1
    replacement = mutate + anchor
    if os.name != "nt" and plugin == "agent-vault":
        replacement = "    install|update|provision|stamp) " + mutate + "_assert_source_runtime || exit 1 ;;"
    installer.write_text(text.replace(anchor, replacement), encoding="utf-8")
    if suffix == "ps1":
        _refresh_native_declaration(payload)
    _receipt(payload, plugin)
    home = tmp_path / "home"
    env = fixtures["_isolated_install_env"](home)
    root = home / ("." + plugin)
    root.mkdir(exist_ok=True)
    previous = {
        "current-version": b"1.2.2", "last-known-good": b"1.2.2",
        "payload-dir": b"previous-payload", "stamped-version": b"1.2.2",
    }
    for name, value in previous.items():
        (root / name).write_bytes(value)
    result = _run(_native(payload, "stamp", plugin), env=env, cwd=home)
    assert result.returncode != 0
    assert "source" in (result.stdout + result.stderr).lower()
    assert all((root / name).read_bytes() == value for name, value in previous.items())
    assert not (root / "snapshots").exists()
    assert not (root / "versions").exists()


@pytest.mark.parametrize("plugin", PLUGINS)
def test_distinct_source_commits_keep_separate_immutable_snapshots(tmp_path, plugin):
    payload, fixtures = _small_stage(tmp_path, plugin)
    home = tmp_path / "home"
    env = fixtures["_isolated_install_env"](home)
    root = home / ("." + plugin)
    first = _receipt(payload, plugin)
    initial = _run(_native(payload, "stamp", plugin), env=env, cwd=home)
    assert initial.returncode == 0, initial.stdout + initial.stderr
    commands = ("agent-vault",) if plugin == "agent-vault" else (
        "agent-logger", "session-sync", "collate-session", "read-session-digest",
        "prepare-session-log", "ramp-up-session",
    )
    for command in commands:
        for suffix in (".ps1", ".cmd") if os.name == "nt" else ("",):
            assert (home / ".local" / "bin" / (command + suffix)).is_file()
    assert not (root / "versions").exists(), "required stamp smoke must defer runtime construction"
    first_snapshot = root / "snapshots" / first.runtime_version
    before = _inventory(first_snapshot)
    repeated = _run(_native(payload, "stamp", plugin), env=env, cwd=home)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert _inventory(first_snapshot) == before
    second = _receipt(payload, plugin, "c" * 40)
    changed = _run(_native(payload, "stamp", plugin), env=env, cwd=home)
    assert changed.returncode == 0, changed.stdout + changed.stderr
    assert first.runtime_version != second.runtime_version
    assert _inventory(first_snapshot) == before
    assert (root / "stamped-version").read_text().strip() == second.runtime_version
    assert source.read_source_identity(
        root / "snapshots" / second.runtime_version, second.package_version, plugin=plugin,
    ) == second


@pytest.mark.parametrize("plugin", PLUGINS)
def test_failed_redirect_cannot_advance_source_stamp(tmp_path, plugin):
    payload, fixtures = _small_stage(tmp_path, plugin)
    _receipt(payload, plugin)
    home = tmp_path / "home"
    env = fixtures["_isolated_install_env"](home)
    root = home / ("." + plugin)
    root.mkdir(exist_ok=True)
    (root / "payload-dir").mkdir()
    (root / "stamped-version").write_bytes(b"previous")
    command = _native(payload, "stamp", plugin)
    if os.name != "nt":
        # A conditional caller disables Bash errexit throughout the installer.
        # Explicit returns must still propagate a failed redirect publication.
        command = ["bash", "-c", f"if . {shlex.quote(command[1])} stamp; then exit 0; else exit 19; fi"]
    result = _run(command, env=env, cwd=home)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "ordinary file" in result.stdout + result.stderr
    assert (root / "stamped-version").read_bytes() == b"previous"
    assert not (root / "current-version").exists()


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("package", ["1.2.3", "1.2.3-dev4", "1.2.3+build.meta"])
def test_operational_version_preserves_source_identity_and_package_metadata(tmp_path, plugin, package):
    module = REPOSITORY / "plugins" / plugin / "src" / plugin.replace("-", "_") / "source_build.py"
    effective = runpy.run_path(str(module))["effective_runtime_version"]
    generation = source.runtime_version(package, "a" * 40, "0" * 64)
    slot = tmp_path / "versions" / generation
    installed = slot / "site-packages" / "source_build.py"
    normalized = package.replace("-dev", ".dev")
    assert effective(normalized, prefix=slot, module_path=installed) == generation
    assert effective(normalized, prefix=slot, module_path=module) == normalized
    assert effective("9.8.7", prefix=slot, module_path=installed) == "9.8.7"
    assert effective(normalized, prefix=tmp_path / "dev", module_path=installed) == normalized


@pytest.mark.skipif(os.name != "nt", reason="native Windows launcher")
@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("host", ["pwsh", "powershell"])
def test_source_reader_supports_a_native_launcher_without_path_python(tmp_path, plugin, host):
    launcher = shutil.which("py")
    executable = shutil.which(host)
    if not executable or not launcher or Path(launcher).suffix.lower() != ".exe":
        pytest.skip("native PowerShell or Python launcher unavailable")
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text(
        json.dumps({"name": plugin, "version": "1.2.3"}), encoding="utf-8",
    )
    identity = _receipt(payload, plugin)
    binary = tmp_path / "bin"
    binary.mkdir()
    shutil.copy2(launcher, binary / "py.exe")
    second_binary = tmp_path / "second-bin"
    second_binary.mkdir()
    shutil.copy2(launcher, second_binary / "py.exe")
    scripts = REPOSITORY / "plugins" / plugin / "scripts"
    harness = tmp_path / "launcher.ps1"
    harness.write_text(f"""
$ErrorActionPreference = 'Stop'
$PluginDir = {_ps(payload)}
$ScriptDir = {_ps(scripts)}
$InstallDir = {_ps(tmp_path / 'runtime')}
$SrcVersion = '1.2.3'
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
. (Join-Path $ScriptDir 'source-runtime.ps1')
{_function(plugin, 'Get-BootstrapPython', 'ps1')}
if (Get-Command python, python3 -ErrorAction SilentlyContinue) {{ throw 'unexpected PATH Python' }}
Initialize-SourceRuntime
[Console]::Write($SrcVersion)
""", encoding="utf-8")
    result = _run(
        [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        env={**os.environ, "PATH": os.pathsep.join((str(binary), str(second_binary)))},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == identity.runtime_version


def _function(plugin: str, name: str, suffix: str):
    text = (REPOSITORY / "plugins" / plugin / "scripts" / ("install." + suffix)).read_text(encoding="utf-8")
    if suffix == "sh":
        start = re.search(rf"^{re.escape(name)}\(\) \{{", text, re.M)
        assert start, name
        lines = text[start.start():].splitlines(keepends=True)
        delimiter = None
        result = []
        for line in lines:
            result.append(line)
            if delimiter is not None:
                if line.rstrip("\r\n").lstrip("\t") == delimiter:
                    delimiter = None
                continue
            if line.rstrip("\r\n") == "}":
                return "".join(result)
            if not line.lstrip().startswith("#"):
                heredoc = re.search(r"<<-?\s*(?:['\"]([A-Za-z_][A-Za-z_0-9]*)['\"]|([A-Za-z_][A-Za-z_0-9]*))", line)
                if heredoc:
                    delimiter = heredoc.group(1) or heredoc.group(2)
        raise AssertionError(f"Unclosed shell function: {name}")
    pattern = rf"^function {re.escape(name)} \{{.*?^\}}"
    match = re.search(pattern, text, re.M | re.S)
    assert match, name
    return match.group()


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("boundary", ["lease", "completion", "activation", "snapshot"])
def test_source_boundary_guards_preserve_previous_selection(tmp_path, plugin, boundary):
    payload, fixtures = _small_stage(tmp_path, plugin)
    identity = _receipt(payload, plugin)
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "current-version").write_bytes(b"previous")
    scripts = payload / "scripts"
    if os.name == "nt":
        host = shutil.which("pwsh") or shutil.which("powershell")
        if not host:
            pytest.skip("PowerShell unavailable")
        action = {
            "lease": "Invoke-VersionedSlotClean", "completion": "Invoke-VersionedMarkComplete",
            "activation": "Invoke-VersionedActivate", "snapshot": "Publish-SourceSnapshot",
        }[boundary]
        mutate = f"""
$p = Join-Path $PluginDir 'runtime-source.json'
[IO.File]::WriteAllText($p, ([IO.File]::ReadAllText($p)).Replace({'b' * 40!r}, {'c' * 40!r}))
"""
        body = f"""
$ErrorActionPreference = 'Stop'
$PluginDir = {_ps(payload)}
$ScriptDir = {_ps(scripts)}
$PSScriptRoot = $ScriptDir
$InstallDir = {_ps(root)}
$SrcVersion = {_ps(identity.package_version)}
$VenvDir = Join-Path $InstallDir '.venv'
$LinkDir = $VenvDir
$VersionedRuntime = $true
. (Join-Path $ScriptDir 'versioned_runtime_native.ps1')
. (Join-Path $ScriptDir 'source-runtime.ps1')
function Get-BootstrapPython {{ return {_ps(sys.executable)} }}
function Get-PayloadHash {{ return ('d' * 64) }}
function Start-RuntimeConstruction {{ {mutate}; return $false }}
Initialize-SourceRuntime
"""
        if boundary != "snapshot":
            body += _function(plugin, action, "ps1") + "\n"
        if boundary != "lease":
            body += mutate
        body += f"try {{ {action}; exit 0 }} catch {{ [Console]::Error.Write($_); exit 19 }}\n"
        harness = tmp_path / "boundary.ps1"
        harness.write_text(body, encoding="utf-8")
        result = _run([host, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)])
    else:
        if not shutil.which("bash"):
            pytest.skip("Bash unavailable")
        action = {
            "lease": "_versioned_slot_clean", "completion": "_versioned_mark_complete",
            "activation": "_versioned_activate", "snapshot": "_publish_source_snapshot",
        }[boundary]
        mutate = f"sed -i 's/{'b' * 40}/{'c' * 40}/' \"$PLUGIN_DIR/runtime-source.json\""
        body = f"""
set -eu
PLUGIN_DIR={shlex.quote(str(payload))}
SCRIPT_DIR={shlex.quote(str(scripts))}
INSTALL_DIR={shlex.quote(str(root))}
SRC_VERSION={shlex.quote(identity.package_version)}
LINK_DIR="$INSTALL_DIR/.venv"
VERSIONED_RUNTIME=1
. "$SCRIPT_DIR/source-runtime.sh"
_bootstrap_python() {{ printf '%s\\n' {shlex.quote(sys.executable)}; }}
_fail() {{ printf '%s\\n' "$1" >&2; }}
_payload_hash() {{ printf '%s\\n' {'d' * 64}; }}
_runtime_construction_begin() {{ {mutate}; }}
_initialize_source_runtime
"""
        if boundary != "snapshot":
            body += _function(plugin, action, "sh") + "\n"
        if boundary != "lease":
            body += mutate + "\n"
        body += f"if {action}; then exit 0; else exit 19; fi\n"
        harness = tmp_path / "boundary.sh"
        harness.write_text(body, encoding="utf-8")
        result = _run(["bash", str(harness)])
    assert result.returncode == 19, result.stdout + result.stderr
    assert "identity changed" in result.stdout + result.stderr
    assert (root / "current-version").read_bytes() == b"previous"
    assert not (root / "versions").exists()
    assert not (root / "snapshots").exists()
