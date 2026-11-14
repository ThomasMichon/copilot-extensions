"""The vault service starts through the shared resident-daemon launch."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import venv
import sys
from pathlib import Path

import agent_procutil
import pytest

from agent_vault import cli, config, configuration, configuration_scope, launch, service


def _cache_args(argv, root):
    assert argv == ["-B"]
    assert not (Path(root) / "cache" / "python").exists()


@pytest.fixture
def recorded_spawns(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(launch, "spawn_service", lambda args: calls.append(list(args)))
    monkeypatch.setattr(cli, "send_command", lambda *_a, **_k: {"ok": True})
    monkeypatch.setattr(cli.time, "sleep", lambda _s: None)
    monkeypatch.setattr("agent_vault.configuration.runtime_enabled", lambda: True)
    return calls


@pytest.mark.parametrize("windows", [False, True])
def test_withdrawn_activation_refuses_every_runtime_start_path(monkeypatch, windows, capsys):
    monkeypatch.setattr(cli, "IS_WINDOWS", windows)
    monkeypatch.setattr("agent_vault.configuration.runtime_enabled", lambda: False)

    def forbidden(*args, **kwargs):
        pytest.fail("withdrawn activation must not touch service or database startup")

    monkeypatch.setattr(cli.config, "resolve_context", forbidden)
    monkeypatch.setattr(cli, "_start_service_systemd", forbidden)
    monkeypatch.setattr(launch, "spawn_service", forbidden)
    assert cli.start_service() is False
    assert "inactive" in capsys.readouterr().err


@pytest.mark.parametrize("windows", [True, False])
def test_start_service_launches_through_spawn_service(monkeypatch, recorded_spawns, windows):
    monkeypatch.setattr(cli, "IS_WINDOWS", windows)
    assert cli.start_service(4242) is True
    # Always in the foreground: spawn_daemon already detached it, and POSIX's
    # self-daemonize would move it to HOME with its output on /dev/null.
    assert recorded_spawns == [["--tcp-port", "4242", "--foreground"]]


@pytest.mark.parametrize("configured_port,source,explicit,expected", [
    (19999, "default", None, 0),
    (4242, "env", None, 4242),
    (4242, "repo", None, 4242),
    (19999, "default", 4343, 4343),
    (19999, "default", 0, 0),
])
def test_daemon_bind_default_does_not_change_client_context(
    monkeypatch, recorded_spawns, configured_port, source, explicit, expected,
):
    from types import SimpleNamespace

    monkeypatch.setenv(config.BIND_PORT_ENV, "0")
    monkeypatch.setattr(
        config, "resolve_context",
        lambda: SimpleNamespace(port=configured_port, sources={"port": source}),
    )
    assert cli.start_service(explicit)
    assert recorded_spawns == [["--tcp-port", str(expected), "--foreground"]]


@pytest.mark.parametrize("value", ["-1", "65536", "1.5", " 0", "０", ""])
def test_invalid_daemon_bind_default_refuses_before_launch(monkeypatch, recorded_spawns, value):
    from types import SimpleNamespace

    monkeypatch.setenv(config.BIND_PORT_ENV, value)
    monkeypatch.setattr(
        config, "resolve_context",
        lambda: SimpleNamespace(port=19999, sources={"port": "default"}),
    )
    with pytest.raises(ValueError, match="AGENT_VAULT_BIND_PORT"):
        cli.start_service()
    assert recorded_spawns == []


def test_start_service_waits_for_credential_work_admission(monkeypatch, recorded_spawns):
    replies = iter(({"ok": True, "accepting_work": False}, {"ok": True, "accepting_work": True}))
    seen = []

    def ping(*args, **kwargs):
        response = next(replies)
        seen.append(response)
        return response

    monkeypatch.setattr(cli, "send_command", ping)
    assert cli.start_service(4242)
    assert len(seen) == 2
    assert recorded_spawns == [["--tcp-port", "4242", "--foreground"]]


@pytest.mark.parametrize("eventually_ready", (True, False))
def test_ensure_waits_for_transition_without_spawning_a_duplicate(
    monkeypatch, recorded_spawns, eventually_ready,
):
    calls = []

    def ping(*args, **kwargs):
        calls.append(True)
        return {"ok": True, "accepting_work": eventually_ready and len(calls) >= 3}

    monkeypatch.setattr(cli, "send_command", ping)
    assert cli.ensure_service(4242) is eventually_ready
    assert len(calls) == (3 if eventually_ready else 21)
    assert recorded_spawns == []


def test_windows_self_daemonize_launches_through_spawn_service(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(launch, "spawn_service", lambda args: calls.append(list(args)))
    service.daemonize_windows(["-m", "agent_vault.service", "--tcp-port", "4242", "-v"])
    assert calls == [["--tcp-port", "4242", "-v", "--foreground"]]


def test_spawn_service_runs_from_the_runtime_root_with_a_log(monkeypatch, tmp_path):
    home = tmp_path / "vault-home"
    monkeypatch.setenv(config.HOME_ENV, str(home))
    seen: dict = {}

    def _fake_spawn_daemon(argv, **kwargs):
        seen.update(argv=list(argv), **kwargs)
        return "proc"

    monkeypatch.setattr(launch, "spawn_daemon", _fake_spawn_daemon)
    assert launch.spawn_service(["--foreground"]) == "proc"
    root = home.resolve()
    assert root.is_dir()
    _cache_args(seen["argv"][1:2], root)
    assert seen["argv"][2] == "-c"
    assert seen["argv"][4:] == [
        str(Path(launch.__file__).resolve().parent.parent), "--foreground",
        "--configuration-root", str(Path.cwd()),
    ]
    assert seen["cwd"] == root
    assert seen["log_path"] == root / launch.SERVICE_STDIO_LOG
    # The service always ran inside its caller's Job before: keep it running
    # there (logged) when that Job forbids breakaway.
    assert seen["contained_fallback"] is True
    assert seen.get("breakaway", True) is True


def test_spawn_service_is_the_shared_primitive():
    assert launch.spawn_daemon is agent_procutil.spawn_daemon


@pytest.mark.skipif(os.name != "nt", reason="Windows native start/status fallback")
@pytest.mark.parametrize("action", ["start", "status"])
def test_native_action_uses_active_profile_not_unbuilt_desire(tmp_path, action):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    assert shell, "the selected native control requires PowerShell"
    root = tmp_path / "vault"
    active = root / "versions" / "1.0.0+ssh"
    venv.EnvBuilder(with_pip=False).create(active)
    package = active / "Lib" / "site-packages" / "agent_vault"
    package.mkdir()
    (package / "__init__.py").write_text("")
    probe = (
        "import json,sys\n"
        "print(json.dumps({'arguments':sys.argv[1:],'runtime':sys.prefix}))\n"
    )
    (package / "__main__.py").write_text(probe)
    (package / "service.py").write_text(probe)
    (root / "current-version").write_text(active.name)
    desire = root / "runtime-profile.json"
    desire.write_text("unbuilt desired profile must not select execution")
    before = {path: path.read_bytes() for path in (desire, root / "current-version")}
    resolver = root / "bin" / "resolve-runtime.ps1"
    resolver.parent.mkdir()
    resolver.write_text(
        "param($TraceMode)\n"
        "if ($TraceMode -cne 'read-only') { throw 'unexpected resolver mutation' }\n"
        f"$AgentRtPy = '{str(active / 'Scripts' / 'python.exe')}'\n",
    )
    runner = tmp_path / "action.ps1"
    runner.write_text(r"""
param($Source, $Root, $Action)
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)
foreach($name in @('Get-ActiveVaultPython','Invoke-Start','Invoke-Status')) {
    $node=$ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$true) |
        Where-Object Name -eq $name | Select-Object -First 1
    if (-not $node) { throw "missing native function $name" }
    Invoke-Expression $node.Extent.Text
}
function Get-ScheduledTask {}
function Test-KeePassXCCli { return $false }
function Write-Ok($message) { Write-Output $message }
function Write-Fail($message) { throw $message }
function Write-Skip($message) {}
function Write-Warn($message) {}
$InstallDir=$Root
$LinkPython=Join-Path $Root 'versions\unbuilt\Scripts\python.exe'
$Binstub=$BinstubPs1=$BinstubCmd=Join-Path $Root 'absent-binstub'
$TaskName='fixture'
$env:AGENT_RT_ROOT='retained-root'
[void](Get-ActiveVaultPython)
if ($env:AGENT_RT_ROOT -cne 'retained-root') { throw 'resolver changed caller context' }
if ($Action -eq 'start') { Invoke-Start } else { Invoke-Status }
""", encoding="utf-8")
    result = subprocess.run(
        [shell, "-NoLogo", "-NoProfile", "-File", str(runner),
         str(Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"),
         str(root), action],
        capture_output=True, text=True, timeout=20, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    observed = json.loads(next(line for line in result.stdout.splitlines() if line.startswith("{")))
    assert observed == {
        "arguments": ["start"] if action == "start" else ["--ping"], "runtime": str(active),
    }
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("linked", ["home", "parent"])
def test_linked_home_refuses_before_any_launch_artifact(tmp_path, monkeypatch, linked):
    from agent_procutil import _daemon_instance_store as store

    target = tmp_path / "selected-target"
    target.mkdir()
    alias = tmp_path / "vault-home"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"native directory symlink unavailable: {error}")
    selected = alias if linked == "home" else alias / "runtime"
    monkeypatch.setenv(config.HOME_ENV, str(selected))
    monkeypatch.setenv(store.REGISTRY_ENV, str(tmp_path / "registry"))
    monkeypatch.delenv(store.INSTANCE_ENV, raising=False)
    monkeypatch.delenv(store.TEST_CONTEXT_ENV, raising=False)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *_a, **_k: pytest.fail("redirected home reached process creation"),
    )
    with pytest.raises(OSError, match="redirected"):
        launch.spawn_service(["--foreground"])
    assert config.home_dir() == selected
    assert not list(target.iterdir())


@pytest.mark.parametrize("shadowed", [False, True])
def test_vendored_service_imports_its_own_package_from_runtime_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shadowed: bool,
) -> None:
    packages = tmp_path / "vendored packages"
    package = packages / "agent_vault"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "service.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), "
        "'module': __file__, 'cached': __cached__, 'cache': sys.pycache_prefix}))\n",
        encoding="utf-8",
    )
    home = tmp_path / "runtime home"
    home.mkdir()
    if shadowed:
        shadow = home / "agent_vault"
        shadow.mkdir()
        (shadow / "__init__.py").write_text(
            "raise RuntimeError('runtime cwd must not select the service package')\n",
            encoding="utf-8",
        )
    caller = tmp_path / "foreign caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv(config.HOME_ENV, str(home))
    monkeypatch.setattr(launch, "__file__", str(package / "launch.py"))
    command: list[str] = []
    overrides: dict[str, str] = {}

    def capture(
        argv: list[str], *, env_overrides: dict[str, str], **_kwargs: object,
    ) -> None:
        command.extend(argv)
        overrides.update(env_overrides)

    monkeypatch.setattr(launch, "spawn_daemon", capture)
    args = ["--foreground", "--probe-value", "value with spaces"]
    launch.spawn_service(args)
    _cache_args(command[1:2], home)
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith("PYTHON")
    }
    env.update(overrides)
    before = {path: path.read_bytes() for path in package.rglob("*") if path.is_file()}
    for _ in range(2):
        result = subprocess.run(
            [command[0], "-S", *command[1:]], cwd=home, env=env,
            capture_output=True, text=True, timeout=10,
            **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        assert observed["argv"] == [*args, "--configuration-root", str(caller)]
        assert Path(observed["cwd"]) == home.resolve()
        assert Path(observed["module"]).resolve() == (package / "service.py").resolve()
        assert {path: path.read_bytes() for path in package.rglob("*") if path.is_file()} == before
        assert observed["cache"] is None
        cached = Path(observed["cached"])
        assert not cached.exists()
        assert not (home / "cache").exists()


@pytest.mark.skipif(os.name != "nt", reason="Actual Windows service launcher template")
@pytest.mark.parametrize("supports_context", [False, True])
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_generated_task_launcher_routes_late_imports_outside_the_slot(tmp_path, supports_context, shell):
    root = tmp_path / "vault home-\u03bb"
    slot = root / "versions/1.0.0"
    venv.EnvBuilder(with_pip=False).create(slot)
    site = slot / "Lib/site-packages"
    for name in ("agent_vault", "zdd"):
        package = site / name
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
    (site / "zdd/private_peer.py").write_text("VALUE = 42\n", encoding="utf-8")
    if supports_context:
        (site / "agent_vault/service_arguments.py").write_text("", encoding="utf-8")
    (site / "agent_vault/service.py").write_text(
        "import json,os,sys\nfrom zdd import private_peer\n"
        "print(json.dumps({'args':sys.argv[1:],'cache':sys.pycache_prefix,"
        "'cached':private_peer.__cached__,'value':private_peer.VALUE,"
        "'config':os.environ.get('AGENT_VAULT_CONFIG')}))\n",
        encoding="utf-8",
    )
    source = (Path(__file__).resolve().parents[1] / "scripts/install.ps1").read_text(encoding="utf-8")
    start = source.index(
        '[System.IO.File]::WriteAllText($TaskLauncher, @"',
        source.index("function Register-AgentVaultTask"),
    )
    ending = '"@, [Text.UTF8Encoding]::new($true))'
    emission = source[start:source.index(ending, start) + len(ending)]
    values = {
        "InstallDir": root, "RunDir": root / "run", "SocketPath": root / "run/socket",
        "PipePath": "fixture-pipe", "PidFile": root / "run/pid", "LogFile": root / "logs/service.log",
        "TaskName": "FixtureVault", "installationId": "", "portDirective": "",
        "TaskLauncher": root / "service.ps1", "LinkPython": slot / "Scripts/python.exe",
        "serviceConfigurationRoot": tmp_path / "configuration-\u03bb",
        "serviceConfigurationPath": tmp_path / "selected-\u03bb.json",
    }
    harness = tmp_path / "publish-service.ps1"
    harness.write_text(
        "$ErrorActionPreference='Stop'\n$utf8NoBom=[Text.UTF8Encoding]::new($false)\n"
        + "\n".join(
            "$" + name + "='" + str(value).replace("'", "''") + "'"
            for name, value in values.items()
        )
        + "\n" + emission + "\n& $TaskLauncher --cache-probe\nexit $LASTEXITCODE\n",
        encoding="utf-8-sig",
    )
    host = shutil.which(shell)
    assert host, "native PowerShell is required"
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
    before = {path: path.read_bytes() for path in slot.rglob("*") if path.is_file()}
    for _ in range(2):
        result = subprocess.run(
            [host, "-NoProfile", "-File", str(harness)], env=env,
            capture_output=True, text=True, timeout=15, **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        assert observed["args"] == [
            "--foreground", "--persistent",
            *(["--configuration-root", str(tmp_path / "configuration-\u03bb")] if supports_context else []),
            "--cache-probe",
        ]
        assert observed["value"] == 42
        assert observed["config"] == str(tmp_path / "selected-\u03bb.json")
        assert {path: path.read_bytes() for path in slot.rglob("*") if path.is_file()} == before
        assert observed["cache"] is None
        cached = Path(observed["cached"])
        assert not cached.exists()
        assert not (root / "cache").exists()
    module = site / "zdd/private_peer.py"
    module_stat = module.stat()
    module.write_text("VALUE = 84\n", encoding="utf-8")
    os.utime(module, ns=(module_stat.st_atime_ns, module_stat.st_mtime_ns))
    result = subprocess.run(
        [host, "-NoProfile", "-File", str(harness)], env=env,
        capture_output=True, text=True, timeout=15, **agent_procutil.no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["value"] == 84 and not Path(observed["cached"]).exists()

    helper = site / "agent_procutil"
    shutil.copytree(
        Path(agent_procutil.__file__).parent, helper, ignore=shutil.ignore_patterns("__pycache__"),
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": slot.name, "completed_at": "fixture", "pid": 0, "payload_hash": "a" * 64,
    }), encoding="utf-8")
    record = site / "fixture.dist-info" / "RECORD"
    record.parent.mkdir()
    record.write_text("fixture.py,sha256=" + "a" * 43 + ",1\n", encoding="utf-8")
    cache_base = tmp_path / "user-cache"
    env["LOCALAPPDATA"] = str(cache_base)
    caches = []
    for _ in range(2):
        result = subprocess.run(
            [host, "-NoProfile", "-File", str(harness)], env=env,
            capture_output=True, text=True, timeout=15, **agent_procutil.no_window_kwargs(),
        )
        assert result.returncode == 0, result.stderr
        observed = json.loads(result.stdout)
        cached = Path(observed["cached"])
        assert cached.is_relative_to(Path("\\\\?\\" + str(cache_base)))
        assert cached.is_file()
        caches.append(cached)
    assert caches[0] == caches[1]
    assert not (root / "cache").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX systemd service entrypoint")
@pytest.mark.parametrize("provider", ["absent", "legacy", "managed"])
def test_posix_service_entrypoint_consumes_complete_cache_policy(tmp_path, provider):
    root = tmp_path / "vault root"
    slot = root / "versions" / "1.0.0"
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / "bin" / "python"
    site = slot / f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    package = site / "agent_vault"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")
    (package / "service.py").write_text(
        "import agent_vault, json, sys\n"
        "print(json.dumps([agent_vault.VALUE, agent_vault.__cached__, "
        "sys.dont_write_bytecode, sys.argv[1:]]))\n"
        "raise SystemExit(19)\n", encoding="utf-8",
    )
    if provider != "absent":
        helper = site / "agent_procutil"
        shutil.copytree(
            Path(agent_procutil.__file__).parent, helper,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        if provider == "legacy":
            (helper / "python_cache.py").write_text(
                "def cache_arguments(*args, **kwargs):\n"
                "    raise AssertionError('legacy allocation must not run')\n", encoding="utf-8",
            )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": slot.name, "completed_at": "fixture", "pid": 0, "payload_hash": "a" * 64,
    }), encoding="utf-8")
    record = site / "fixture.dist-info" / "RECORD"
    record.parent.mkdir()
    record.write_text("fixture.py,sha256=" + "a" * 43 + ",1\n", encoding="utf-8")
    cache_base = tmp_path / "user cache"
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
    env["XDG_CACHE_HOME"] = str(cache_base)
    launcher = Path(__file__).resolve().parents[1] / "scripts" / "vault-service.sh"
    caches = []
    for _ in range(2):
        result = subprocess.run(
            ["/bin/sh", str(launcher), str(python), str(root), "argument with spaces"],
            cwd=root, env=env, capture_output=True, text=True, timeout=15,
        )
        assert result.returncode == 19, result.stderr
        value, cached, readonly, arguments = json.loads(result.stdout)
        assert value == 42 and arguments == ["argument with spaces"]
        assert readonly is (provider != "managed")
        cache = Path(cached)
        assert not (root / "cache").exists()
        if provider == "managed":
            assert cache.is_file() and cache.is_relative_to(cache_base)
            caches.append(cache)
        else:
            assert not cache.exists() and not cache_base.exists()
    if caches:
        assert caches[0] == caches[1]
    assert not list(package.rglob("*.pyc"))


def test_relative_home_override_is_passed_resolved(monkeypatch, tmp_path):
    # The service runs from its root: a relative override inherited as-is
    # would resolve to <home>/<home> there, apart from the clients' copy.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(config.HOME_ENV, "state")
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    root = (tmp_path / "state").resolve()
    assert seen["cwd"] == root
    assert seen["env_overrides"][config.HOME_ENV] == str(root)


def test_service_launch_captures_qualified_discovery_before_changing_cwd(tmp_path, monkeypatch):
    base = tmp_path / "base"
    caller = base / "worktree"
    caller.mkdir(parents=True)
    home = tmp_path / "state"
    monkeypatch.chdir(caller)
    monkeypatch.setenv(config.HOME_ENV, str(home))
    monkeypatch.setattr(configuration, "owner_report", lambda root: {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "fixture", "execution_machine": "fixture",
            "installation_cell": "fixture", "binding": "unbound", "base_root": str(base),
        },
    })
    seen = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kwargs: seen.update(argv=argv, **kwargs))
    launch.spawn_service(["--foreground"])
    assert seen["cwd"] == home
    assert seen["argv"][-2:] == ["--configuration-root", str(caller)]


def test_service_snapshot_preserves_relative_selected_config_and_unicode_caller(tmp_path, monkeypatch):
    root = tmp_path / "configuration-\u03bb"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(configuration, "owner_report", lambda root: None)
    monkeypatch.setenv(config.CONFIG_ENV, "selected.json")
    assert configuration.service_configuration_snapshot(str(root)) == {
        "root": str(root), "config": str(root / "selected.json"),
    }


def test_bound_service_uses_persisted_nondefault_activation_file(tmp_path, monkeypatch):
    caller, discovery, state = (tmp_path / name for name in ("caller", "discovery", "state"))
    caller.mkdir()
    discovery.mkdir()
    state.mkdir()
    selected = caller / "selected.json"
    selected.write_text('{"enabled":true}', encoding="utf-8")
    monkeypatch.setattr(configuration, "owner_report", lambda root: None)
    monkeypatch.setattr(configuration_scope, "_SERVICE_CONFIGURATION_ROOT", None)
    monkeypatch.setenv(config.CONFIG_ENV, "selected.json")
    monkeypatch.chdir(caller)
    snapshot = configuration.service_configuration_snapshot(str(discovery))
    assert snapshot == {"root": str(discovery), "config": str(selected)}
    monkeypatch.setenv(config.CONFIG_ENV, snapshot["config"])
    config.bind_service_configuration(snapshot["root"])
    monkeypatch.chdir(state)
    assert configuration.runtime_enabled() is True
    selected.write_text('{"enabled":false}', encoding="utf-8")
    assert configuration.runtime_enabled() is False


@pytest.mark.parametrize("root", ["relative", "absent"])
def test_service_configuration_binding_rejects_invalid_roots(tmp_path, monkeypatch, root):
    monkeypatch.setattr(configuration_scope, "_SERVICE_CONFIGURATION_ROOT", None)
    selected = root if root == "relative" else str(tmp_path / "absent")
    with pytest.raises(ValueError, match="existing absolute directory"):
        config.bind_service_configuration(selected)


def test_bound_service_configuration_survives_state_cwd(tmp_path, monkeypatch):
    base, state = tmp_path / "base", tmp_path / "state"
    base.mkdir()
    state.mkdir()
    (base / ".agent-vault.json").write_text('{"enabled":true,"group":"configured"}')
    monkeypatch.setattr(configuration_scope, "_SERVICE_CONFIGURATION_ROOT", None)
    monkeypatch.setattr(configuration, "owner_report", lambda root: None)
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "absent.json"))
    config.bind_service_configuration(str(base))
    monkeypatch.chdir(state)
    assert configuration.runtime_enabled() is True
    assert config.resolve_context(strict=True).group == "configured"
    (base / ".agent-vault.json").write_text('{"enabled":false}')
    assert configuration.runtime_enabled() is False
    (base / ".agent-vault.json").write_text('{"enabled":"invalid"}')
    with pytest.raises(RuntimeError, match="enabled must be a boolean"):
        configuration.runtime_enabled()


@pytest.mark.parametrize("current", ["rebound", "refused", "foreign"])
def test_resident_binding_requeries_same_cell_inherited_configuration(tmp_path, monkeypatch, current):
    base, old, new, state = (tmp_path / name for name in ("base", "old", "new", "state"))
    for root in (base, old, new, state):
        root.mkdir()
    (old / ".agent-vault.json").write_text('{"enabled":true}', encoding="utf-8")
    (new / ".agent-vault.json").write_text('{"enabled":false}', encoding="utf-8")
    cell = tmp_path / "marketplaces" / "fixture"
    own = cell / "plugins" / "agent-vault"
    (cell / "plugins" / "agent-worktrees").mkdir(parents=True)
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "fixture", "execution_machine": "fixture",
            "installation_cell": str(cell), "binding": "bound",
            "base_root": str(base), "knowledge_root": str(old),
        },
    }
    fresh = json.loads(json.dumps(report))
    fresh["context"]["knowledge_root"] = str(new)
    if current == "foreign":
        fresh["context"]["installation_cell"] = str(tmp_path / "foreign")
    monkeypatch.setenv("AGENT_RT_ROOT", str(own))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own / "install.json"))
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    monkeypatch.setenv(config.CONFIG_ENV, str(tmp_path / "absent.json"))
    monkeypatch.setattr(configuration_scope, "_SERVICE_CONFIGURATION_ROOT", None)
    monkeypatch.setattr(configuration._peer_launch, "validate_owner", lambda *args: {
        "cellRoot": str(cell), "pluginRoot": str(own),
    })
    monkeypatch.setattr(configuration._peer_launch, "launch_prefix", lambda *args: ["qualified-peer"])
    calls = []

    def run(argv, **kwargs):
        assert argv == ["qualified-peer", "state-root", "--configuration", "--json"]
        assert kwargs["cwd"] == base
        assert "AGENT_CONFIGURATION_CONTEXT" not in kwargs["env"]
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 126 if current == "refused" else 0,
            json.dumps(fresh).encode("utf-8"), b"",
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert configuration.runtime_enabled(str(base)) is True
    assert calls == []
    config.bind_service_configuration(str(base))
    monkeypatch.chdir(state)
    if current == "rebound":
        assert configuration.runtime_enabled() is False
    else:
        with pytest.raises(ValueError):
            configuration.runtime_enabled()
    assert len(calls) == 1


def test_extension_source_keeps_unbound_default_and_uses_bound_service_root(tmp_path, monkeypatch):
    from agent_vault import extensions

    calls = []

    class Registry:
        def collect_config(self, cwd):
            calls.append(cwd)
            return {}

    monkeypatch.setattr(extensions, "get_registry", lambda: Registry())
    monkeypatch.setattr(configuration_scope, "_SERVICE_CONFIGURATION_ROOT", None)
    config._ext_config(None)
    config.bind_service_configuration(str(tmp_path))
    config._ext_config(None)
    config._ext_config("explicit")
    assert calls == [None, str(tmp_path), "explicit"]


def test_unset_home_override_stays_unset(monkeypatch, tmp_path):
    # Its presence selects the cache location, so legacy mode must not gain it.
    monkeypatch.delenv(config.HOME_ENV, raising=False)
    monkeypatch.setattr(launch, "service_root", lambda: tmp_path)
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    assert config.HOME_ENV not in seen["env_overrides"]


def test_relative_path_overrides_are_passed_resolved(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(launch, "service_root", lambda: tmp_path / "root")
    relative = {
        config.CONFIG_ENV: "settings.json", config.RUN_DIR_ENV: "run",
        config.PID_ENV: "vault.pid", config.LOG_ENV: "vault.log", config.SOCKET_ENV: "vault.sock",
        launch.CACHE_DIR_ENV: "cache", "KPDB": "vault.kdbx", "AGENT_VAULT_KEK_DIR": "keys",
        "AGENT_VAULT_WINDOWS_RUN_DIR": "winrun", "AGENT_VAULT_CORE_RUN_DIR": "corerun",
    }
    for name, value in relative.items():
        monkeypatch.setenv(name, value)
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    for name, value in relative.items():
        assert seen["env_overrides"][name] == str(tmp_path / value)


def test_unset_path_overrides_stay_unset(monkeypatch, tmp_path):
    for name in launch._PATH_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(launch, "service_root", lambda: tmp_path)
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    assert not set(launch._PATH_OVERRIDES) & set(seen["env_overrides"])


def test_relative_database_path_is_absolute_before_any_request(monkeypatch, tmp_path):
    # Requests carry the client's kpdb; the service resolves it from its own
    # runtime root, so a relative one must already be absolute.
    monkeypatch.chdir(tmp_path)
    assert config._expand_kpdb("vault.kdbx") == str(tmp_path / "vault.kdbx")
    assert config._expand_kpdb("db/vault.kdbx", base_dir=tmp_path / "repo") == str(
        tmp_path / "repo" / "db" / "vault.kdbx"
    )


@pytest.mark.parametrize("value", ["~/vault.kdbx", "$VAULT_TEST_ROOT/vault.kdbx"])
def test_path_overrides_are_expanded_before_they_are_resolved(monkeypatch, tmp_path, value):
    # `abspath("~/x")` would name `<caller>/~/x`: expand as the readers do first.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("VAULT_TEST_ROOT", str(tmp_path / "home"))
    monkeypatch.setenv("KPDB", value)
    monkeypatch.setattr(launch, "service_root", lambda: tmp_path / "root")
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    assert seen["env_overrides"]["KPDB"] == str(tmp_path / "home" / "vault.kdbx")


@pytest.mark.parametrize("value", ["~/cache", "$VAULT_TEST_ROOT/cache"])
def test_non_database_overrides_are_resolved_raw_like_their_readers(monkeypatch, tmp_path, value):
    # The cache reader takes the raw path (`<caller>/~/cache`); expanding it for
    # the service alone would split the client and service credential caches.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("VAULT_TEST_ROOT", str(tmp_path / "home"))
    monkeypatch.setenv(launch.CACHE_DIR_ENV, value)
    monkeypatch.setattr(launch, "service_root", lambda: tmp_path / "root")
    seen: dict = {}
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    launch.spawn_service(["--foreground"])
    client_view = str(Path(os.environ[launch.CACHE_DIR_ENV]).absolute())
    assert seen["env_overrides"][launch.CACHE_DIR_ENV] == client_view
    assert client_view == str(tmp_path / value)


@pytest.mark.parametrize("layout", ["flat", "registry"])
def test_global_config_relative_database_survives_the_service_cwd(monkeypatch, tmp_path, layout):
    # A request that omits kpdb makes the service resolve the configured
    # default from its own runtime root; it must name the client's database.
    settings = tmp_path / "settings" / "config.json"
    settings.parent.mkdir()
    data = (
        {"kpdb": "vault.kdbx"} if layout == "flat"
        else {"vaults": {"main": {"kpdb": "vault.kdbx"}}, "default_vault": "main"}
    )
    settings.write_text(json.dumps(data), encoding="utf-8")
    for name in ("KPDB", "AGENT_VAULT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(config.CONFIG_ENV, str(settings))
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})
    expected = str(tmp_path / "settings" / "vault.kdbx")
    for cwd in ("client", "service-root"):
        (tmp_path / cwd).mkdir()
        monkeypatch.chdir(tmp_path / cwd)
        assert config.resolve_kpdb(required=False) == expected


@pytest.mark.parametrize("given", ["vault.kdbx", "~/vault.kdbx"])
def test_vault_add_keeps_a_relative_database_at_the_callers_directory(
    monkeypatch, tmp_path, given
):
    # Settings resolve relative paths against their own directory, so `vault
    # add` anchors a relative path at the caller's cwd before saving it; a
    # home-relative path is stored as given and expanded on read.
    settings = tmp_path / "settings" / "config.json"
    settings.parent.mkdir()
    (tmp_path / "caller").mkdir()
    (tmp_path / "elsewhere").mkdir()
    for name in ("KPDB", "AGENT_VAULT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(config.CONFIG_ENV, str(settings))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})
    monkeypatch.chdir(tmp_path / "caller")
    args = type("Args", (), {"name": "example", "kpdb": given, "group": None, "port": None})()
    assert cli.cmd_vault_add(args) == 0
    monkeypatch.chdir(tmp_path / "elsewhere")
    expected = tmp_path / ("caller" if given == "vault.kdbx" else "home") / "vault.kdbx"
    assert config.resolve_kpdb(required=False) == str(expected)
