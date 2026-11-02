"""The common bootstrap family must admit publication before dispatch."""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import time
from collections.abc import Callable
from functools import cache

import pytest

from test_adopter_publication import ROOT, SHELLS, _adopter, _command, _without_python
from test_bridge_first_use_publication import _initialize_shell_profiles
from test_command_binding import (
    POWERSHELL, _snapshot,
    _write,
    binding_fixture as binding_fixture,
    powershell_host as powershell_host,
)
from test_installer_publication import _policy
from test_native_publication import _run
from test_simple_first_use_publication import _release_payload

PLUGINS = (
    "agent-codespaces", "agent-logger", "agent-pull-requests", "agent-vault",
    "agent-bridge", "agent-dispatch", "agent-containers", "agent-mcp",
    "agent-machines", "budget-guidance",
)
pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.timeout(60)]
DRIVER_BOOTSTRAPS = {"agent-vault", "agent-bridge"}


@pytest.fixture(scope="session")
def bootstrap_payloads(tmp_path_factory: pytest.TempPathFactory) -> Callable[[str], Path]:
    root = tmp_path_factory.mktemp("bootstrap-releases")

    @cache
    def release(plugin: str) -> Path:
        return _release_payload(root, plugin)

    return release


def _bootstrap_fixture(
    fixture: dict, shell: str, plugin: str, release: Callable[[str], Path],
) -> dict:
    released = release(plugin)
    environment = _adopter(fixture, plugin, released)
    environment["BOOTSTRAP_WITNESS"] = str(fixture["legacy"] / "installer-dispatched")
    scripts = fixture["payload"] / "scripts"
    for name in ("init", "install"):
        if shell == "powershell":
            content = (
                "param([ValidateSet('stamp', 'install', 'stamp-binstub', 'cell-provision')]"
                "[string]$Action, [string]$Context, [string]$ExpectedMarketplaceId)\n"
                "[IO.File]::WriteAllText($env:BOOTSTRAP_WITNESS, $Action)\n"
            )
            suffix = "ps1"
        else:
            content = (
                "#!/usr/bin/env bash\n"
                'case "${1:-}" in\n'
                'stamp) printf "%s\\n" "$1" > "$BOOTSTRAP_WITNESS";;\n'
                "*) exit 2;;\n"
                "esac\n"
            )
            suffix = "sh"
        (scripts / f"{name}.{suffix}").write_text(content, encoding="utf-8")
    if plugin in DRIVER_BOOTSTRAPS:
        engines = None
        if os.name == "nt":
            stock = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
            engines = (stock, POWERSHELL, shutil.which("pwsh"))
        fixture["bootstrap_shell_profiles"] = _initialize_shell_profiles(
            fixture, environment, engines=engines,
        )
        driver = fixture["payload"] / "libs" / "update-driver" / "scripts"
        (driver / "foundation-produce.ps1").write_text(
            "param([string]$Root,[string]$SourceHash)\n"
            "[IO.File]::WriteAllText((Join-Path $Root 'bootstrap-foundation-entered'), $SourceHash)\n"
            "$deadline = [DateTime]::UtcNow.AddSeconds(25)\n"
            "while (-not (Test-Path (Join-Path $Root 'bootstrap-foundation-release')) -and "
            "[DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 20 }\n"
            "exit 37\n", encoding="utf-8",
        )
        (driver / "foundation-produce.sh").write_text(
            '#!/usr/bin/env bash\nprintf "%s" "$2" >"$1/bootstrap-foundation-entered"\n'
            'for ((i=0; i<1250; i++)); do\n'
            '  [[ ! -f "$1/bootstrap-foundation-release" ]] || break\n'
            '  sleep .02\n'
            'done\n'
            "printf 'fixture bootstrap foundation refusal\\n' >&2\nexit 37\n",
            encoding="utf-8",
        )
        if os.name == "nt":
            # This packet owns publication admission, not daemon qualification.
            # Adapt only the contained launch effect; native portfolios use the real broker.
            (fixture["payload"] / "libs/agent-procutil/src/agent_procutil/native_daemon.ps1").write_text(
                "function Start-NativeDaemon {\n"
                "param($Script, $Arguments, $WorkingDirectory, $LogPath)\n"
                "$root = Split-Path -Parent $LogPath\n"
                "[IO.File]::WriteAllText((Join-Path $root 'bootstrap-native-launch'), 'launched')\n"
                "[IO.File]::AppendAllText($LogPath, 'fixture native launch refusal')\n"
                "$effect = [pscustomobject]@{Id=424242;StartTime=[DateTime]::UtcNow}\n"
                "$effect | Add-Member -MemberType ScriptMethod -Name Dispose -Value {}\n"
                "return $effect\n"
                "}\n", encoding="utf-8",
            )
    return environment


def _bootstrap_snapshot(fixture: dict) -> dict:
    # Shell-only controls attest these exact host profiles, never product state.
    profiles = fixture.get("bootstrap_shell_profiles", set())
    return {name: value for name, value in _snapshot(fixture["home"]).items() if name not in profiles}


def _run_bootstrap(fixture: dict, environment: dict, command: list[str], host=None):
    if fixture["payload"].name not in DRIVER_BOOTSTRAPS:
        return _run(fixture, environment, command, host)
    assert host is None
    started = time.monotonic()
    output, errors = (fixture["home"] / name for name in ("bootstrap-stdout", "bootstrap-stderr"))
    timed_out = None
    with output.open("wb") as stdout, errors.open("wb") as stderr:
        caller = subprocess.Popen(
            command, cwd=fixture["home"], env=environment,
            stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            caller.wait(timeout=30)
        except subprocess.TimeoutExpired as error:
            timed_out = error
            caller.kill()
            caller.wait(timeout=10)
    result = subprocess.CompletedProcess(
        command, caller.returncode, output.read_text(encoding="utf-8-sig"),
        errors.read_text(encoding="utf-8-sig"),
    )
    result.bootstrap_elapsed = time.monotonic() - started
    plugin = fixture["payload"].name
    prefix = f"[{plugin}] driver launch acknowledgement: "
    frame = next(
        (line.removeprefix(prefix) for line in result.stderr.splitlines() if line.startswith(prefix)),
        None,
    )
    if frame is None:
        if timed_out is not None:
            root = fixture["home"] / ".copilot-extensions/update-driver"
            diagnostic = "\n".join(
                f"{path.name}:\n{path.read_text(errors='replace')}"
                for path in root.glob("*.log")
            )
            pytest.fail(f"{timed_out}\n{result.stdout}\n{result.stderr}\n{diagnostic}")
        return result
    if os.name == "nt":
        # Start-NativeDaemon is a no-process effect adapter in this packet.
        return result
    root = fixture["home"] / ".copilot-extensions" / "update-driver"
    release = root / "bootstrap-foundation-release"
    descriptor = os.pidfd_open(int(frame.split()[0].removeprefix("launched:")))
    try:
        release.touch()
        assert select.select([descriptor], [], [], 30)[0], result.stderr
    finally:
        os.close(descriptor)
    if timed_out is not None:
        raise timed_out
    return result


def _assert_driver_launch(fixture: dict, result, action: str) -> None:
    root = fixture["home"] / ".copilot-extensions" / "update-driver"
    assert f"bootstrap {action} supervision launched" in result.stderr, result.stdout + result.stderr
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        diagnostic = "\n".join(path.read_text(errors="replace") for path in root.glob("first-use-*.log"))
        if (root / "bootstrap-native-launch").is_file() and "fixture native launch refusal" in diagnostic:
            break
        if (root / "bootstrap-foundation-entered").is_file() and (
            "Foundation construction failed" in diagnostic or
            "fixture bootstrap foundation refusal" in diagnostic
        ):
            break
        time.sleep(.05)
    else:
        pytest.fail(result.stdout + result.stderr + diagnostic)
    assert not (fixture["legacy"] / "installer-dispatched").exists()
    assert not (fixture["legacy"] / "process-dispatched").exists()
    assert not (fixture["legacy"] / "reconcile.lock").exists()
    assert not (root / "queue.sqlite3").exists()


def _assert_hook_deadline(fixture: dict, result) -> None:
    hooks = json.loads((fixture["payload"] / "hooks.json").read_text(encoding="utf-8"))
    deadline = hooks["hooks"]["sessionStart"][0]["timeoutSec"]
    elapsed = result.bootstrap_elapsed
    assert elapsed <= deadline, f"bootstrap admission took {elapsed:.2f}s; hook deadline is {deadline}s"


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["managed", "invalid", "foreign", "future"])
def test_bootstrap_family_denies_stamp_before_dispatch(
    binding_fixture: dict, plugin: str, shell: str, state: str, powershell_host, bootstrap_payloads,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin, bootstrap_payloads)
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _bootstrap_snapshot(binding_fixture)

    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )

    assert not (binding_fixture["legacy"] / "installer-dispatched").exists()
    assert result.returncode == 3, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert "Runtime publication refused" in result.stderr
    assert _bootstrap_snapshot(binding_fixture) == before
    if host:
        host.close()
        assert _bootstrap_snapshot(binding_fixture) == before


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["missing", "standalone"])
def test_bootstrap_family_still_stamps_standalone(
    binding_fixture: dict, plugin: str, shell: str, state: str, powershell_host, bootstrap_payloads,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin, bootstrap_payloads)
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], state)
    command = (
        _recording_processes(binding_fixture, shell, environment)
        if plugin == "agent-machines" and shell == "powershell" else
        _command(binding_fixture, shell, "bootstrap-check")
    )
    host = (
        powershell_host(binding_fixture, environment)
        if shell == "powershell" and plugin not in DRIVER_BOOTSTRAPS else None
    )
    result = _run_bootstrap(
        binding_fixture, environment, command, host,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    if plugin in DRIVER_BOOTSTRAPS:
        _assert_hook_deadline(binding_fixture, result)
        _assert_driver_launch(binding_fixture, result, "stamp")
        return
    expected = "stamp-binstub" if plugin == "agent-machines" and shell == "powershell" else "stamp"
    assert (binding_fixture["legacy"] / "installer-dispatched").read_text().strip() == expected
    if plugin == "agent-machines" and shell == "powershell":
        assert Path(environment["BOOTSTRAP_PROCESS_WITNESS"]).read_text() == "spawn"


def _installed(fixture: dict, *, healthy: bool) -> None:
    (fixture["payload"] / "pyproject.toml").write_text('version = "2.0.0"\n', encoding="utf-8")
    declaration = fixture["payload"] / "plugin.json"
    document = json.loads(declaration.read_text(encoding="utf-8"))
    document["version"] = "2.0.0"
    _write(declaration, document)
    manifest = {
        "source": {
            "version": "2.0.0" if healthy else "1.0.0", "path": str(fixture["payload"]),
        },
    }
    (fixture["legacy"] / "deploy-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )
    (fixture["legacy"] / ".venv").mkdir()
    if healthy:
        (fixture["legacy"] / "current-version").write_text("2.0.0", encoding="utf-8")
        interpreter = fixture["legacy"] / "versions/2.0.0" / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("healthy interpreter fixture\n", encoding="utf-8")
        interpreter.chmod(0o755)
        (interpreter.parent.parent / ".install-complete.json").write_text(json.dumps({
            "version": "2.0.0", "completed_at": "2026-01-01T00:00:00Z", "pid": 1,
        }), encoding="utf-8")
        if fixture["payload"].name == "agent-machines":
            binstub = fixture["home"] / ".local/bin" / (
                "agent-machines.cmd" if os.name == "nt" else "agent-machines"
            )
            binstub.parent.mkdir(parents=True, exist_ok=True)
            binstub.write_text("healthy command fixture\n", encoding="utf-8")
            binstub.chmod(0o755)


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["managed", "invalid"])
def test_bootstrap_family_healthy_runtime_remains_read_only(
    binding_fixture: dict, plugin: str, shell: str, state: str, powershell_host, bootstrap_payloads,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin, bootstrap_payloads)
    _installed(binding_fixture, healthy=True)
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _bootstrap_snapshot(binding_fixture)
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert not result.stderr
    assert _bootstrap_snapshot(binding_fixture) == before
    if host:
        host.close()
        assert _bootstrap_snapshot(binding_fixture) == before


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["missing", "standalone", "managed"])
def test_bootstrap_family_helperless_compatibility(
    binding_fixture: dict, plugin: str, shell: str, state: str, powershell_host, bootstrap_payloads,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin, bootstrap_payloads)
    suffix = "ps1" if shell == "powershell" else "sh"
    if plugin in DRIVER_BOOTSTRAPS:
        native_suffix = "ps1" if os.name == "nt" else "sh"
        helper = binding_fixture["payload"] / f"scripts/installation-context/installation-context.{native_suffix}"
    else:
        helper = binding_fixture["payload"] / f"scripts/installation-context/plugin-publication-probe.{suffix}"
    helper.rename(helper.with_suffix(".unavailable"))
    command = (
        _recording_processes(binding_fixture, shell, environment)
        if plugin == "agent-machines" and shell == "powershell" else
        _command(binding_fixture, shell, "bootstrap-check")
    )
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _bootstrap_snapshot(binding_fixture)
    result = _run(
        binding_fixture, environment, command, host,
    )
    compatible_missing = state == "missing" and plugin not in DRIVER_BOOTSTRAPS
    assert result.returncode == (0 if compatible_missing else 3), result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    witness = binding_fixture["legacy"] / "installer-dispatched"
    if compatible_missing:
        expected = "stamp-binstub" if plugin == "agent-machines" and shell == "powershell" else "stamp"
        assert witness.read_text().strip() == expected
        if plugin == "agent-machines" and shell == "powershell":
            assert Path(environment["BOOTSTRAP_PROCESS_WITNESS"]).read_text() == "spawn"
    else:
        assert "Runtime publication refused" in result.stderr
        assert _bootstrap_snapshot(binding_fixture) == before


def _recording_processes(fixture: dict, shell: str, environment: dict) -> list[str]:
    witness = fixture["legacy"] / "process-dispatched"
    environment["BOOTSTRAP_PROCESS_WITNESS"] = str(witness)
    scripts = fixture["payload"] / "scripts"
    if shell == "powershell":
        body = (
            "function Get-Process { param($Id) "
            "if ($Id -eq $PID) { Microsoft.PowerShell.Management\\Get-Process -Id $Id } "
            "else { [pscustomobject]@{ Id = $Id } } }\n"
            "function Stop-Process { param($Id, [switch]$Force) "
            "[IO.File]::WriteAllText($env:BOOTSTRAP_PROCESS_WITNESS, 'kill') }\n"
            "function Start-Process { param($FilePath, $WorkingDirectory, [switch]$PassThru, "
            "$WindowStyle, $ArgumentList) "
            "[IO.File]::WriteAllText($env:BOOTSTRAP_PROCESS_WITNESS, 'spawn'); "
            "[pscustomobject]@{ Id = 424242; StartTime = [DateTime]::Now } }\n"
            "& (Join-Path $PSScriptRoot 'bootstrap-check.ps1')\nexit $LASTEXITCODE\n"
        )
        suffix = "ps1"
    else:
        body = (
            "#!/usr/bin/env bash\n"
            'kill() { printf "%s" kill > "$BOOTSTRAP_PROCESS_WITNESS"; return 0; }\n'
            'nohup() { printf "%s" spawn > "$BOOTSTRAP_PROCESS_WITNESS"; }\n'
            'source "$(dirname "${BASH_SOURCE[0]}")/bootstrap-check.sh"\n'
        )
        suffix = "sh"
    (scripts / f"bootstrap-recording.{suffix}").write_text(body, encoding="utf-8")
    return _command(fixture, shell, "bootstrap-recording")


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["managed", "invalid", "foreign", "future"])
def test_bootstrap_family_denies_drift_before_reap_lock_or_spawn(
    binding_fixture: dict, plugin: str, shell: str, state: str, powershell_host, bootstrap_payloads,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin, bootstrap_payloads)
    _installed(binding_fixture, healthy=False)
    command = _recording_processes(binding_fixture, shell, environment)
    lock = binding_fixture["legacy"] / "reconcile.lock"
    lock.write_text("424242", encoding="utf-8")
    os.utime(lock, (1_000_000_000, 1_000_000_000))
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _bootstrap_snapshot(binding_fixture)
    result = _run(binding_fixture, environment, command, host)
    assert not Path(environment["BOOTSTRAP_PROCESS_WITNESS"]).exists()
    assert result.returncode == 3, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert "Runtime publication refused" in result.stderr
    assert _bootstrap_snapshot(binding_fixture) == before
    if host:
        host.close()
        assert _bootstrap_snapshot(binding_fixture) == before


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("failure", ["mode-invalid", "resolver-missing", "adapter-broken"])
def test_bootstrap_family_other_admission_failures_are_read_only(
    binding_fixture: dict, shell: str, failure: str, powershell_host, bootstrap_payloads,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, "agent-logger", bootstrap_payloads)
    suffix = "ps1" if shell == "powershell" else "sh"
    helpers = binding_fixture["payload"] / "scripts/installation-context"
    if failure == "mode-invalid":
        _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "invalid", "version": 1,
            "installationMode": {"enabled": True, "marketplaces": {}},
        })
    elif failure == "resolver-missing":
        helper = helpers / f"installation-context.{suffix}"
        helper.rename(helper.with_suffix(".unavailable"))
    else:
        (helpers / f"plugin-publication-probe.{suffix}").write_text(
            "throw 'broken admission fixture'\n" if shell == "powershell" else "exit 7\n",
            encoding="utf-8",
        )
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert "Runtime publication refused" in result.stderr
    assert _snapshot(binding_fixture["home"]) == before
    if host:
        host.close()
        assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
def test_bootstrap_family_standalone_drift_still_dispatches(
    binding_fixture: dict, plugin: str, shell: str, powershell_host, bootstrap_payloads,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin, bootstrap_payloads)
    _installed(binding_fixture, healthy=False)
    command = _recording_processes(binding_fixture, shell, environment)
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], "standalone")
    host = (
        powershell_host(binding_fixture, environment)
        if shell == "powershell" and plugin not in DRIVER_BOOTSTRAPS else None
    )
    result = _run_bootstrap(binding_fixture, environment, command, host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    if plugin in DRIVER_BOOTSTRAPS:
        _assert_hook_deadline(binding_fixture, result)
        _assert_driver_launch(binding_fixture, result, "install" if plugin == "agent-bridge" else "provision")
        return
    witness = Path(environment["BOOTSTRAP_PROCESS_WITNESS"])
    deadline = time.monotonic() + 2
    while not witness.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert witness.read_text() == "spawn"
    if shell == "powershell" and plugin == "agent-dispatch":
        status = json.loads((binding_fixture["legacy"] / "reconcile-status.json").read_text())
        assert status["launched_pid"] == 424242
        assert status["wrapper_started_at"]
    elif plugin not in {"agent-machines", "agent-bridge"}:
        assert (binding_fixture["legacy"] / "reconcile.lock").read_text().strip().isdigit()


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["managed", "standalone", "healthy"])
def test_budget_bootstrap_retains_pythonless_admission(
    binding_fixture: dict, shell: str, state: str, powershell_host, bootstrap_payloads,
) -> None:
    environment = _bootstrap_fixture(
        binding_fixture, shell, "budget-guidance", bootstrap_payloads,
    )
    if state == "healthy":
        _installed(binding_fixture, healthy=True)
    _policy(
        binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"],
        "standalone" if state == "standalone" else "managed",
    )
    _without_python(binding_fixture, environment)
    command = _command(binding_fixture, shell, "bootstrap-check")
    if shell == "powershell":
        environment["PATH"] = os.pathsep.join((
            str(Path(command[0]).parent),
            str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"),
        ))
    assert shutil.which("python", path=environment["PATH"]) is None
    assert shutil.which("python3", path=environment["PATH"]) is None
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(binding_fixture, environment, command, host)
    assert json.loads(result.stdout) == {}
    witness = binding_fixture["legacy"] / "installer-dispatched"
    if state == "standalone":
        assert result.returncode == 0, result.stdout + result.stderr
        assert witness.read_text().strip() == "stamp"
    else:
        assert result.returncode == (0 if state == "healthy" else 3), result.stdout + result.stderr
        if state == "healthy":
            assert not result.stderr
        else:
            assert "Runtime publication refused" in result.stderr
        assert not witness.exists()
        assert _snapshot(binding_fixture["home"]) == before
    if host:
        host.close()
        if state != "standalone":
            assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.skipif(os.name == "nt", reason="stock macOS Bash is a POSIX interpreter")
@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("phase", ["cold", "drift"])
@pytest.mark.parametrize("state", ["missing", "standalone", "managed", "invalid", "foreign", "future"])
def test_bootstrap_publication_with_stock_bash(
    binding_fixture: dict, plugin: str, phase: str, state: str, bootstrap_payloads,
) -> None:
    requested = os.environ.get("INSTALLATION_CONTEXT_BASH32")
    interpreter = Path(requested or "/bin/bash")
    if not interpreter.is_file():
        if requested:
            pytest.fail("INSTALLATION_CONTEXT_BASH32 does not name an interpreter")
        pytest.skip("Bash 3.2 is unavailable")
    environment = _bootstrap_fixture(binding_fixture, "bash", plugin, bootstrap_payloads)
    version = _run(
        binding_fixture, environment, [str(interpreter), "-c", 'printf "%s" "$BASH_VERSION"'],
    )
    if not version.stdout.startswith("3.2."):
        if requested:
            pytest.fail("INSTALLATION_CONTEXT_BASH32 must select an actual Bash 3.2 interpreter")
        pytest.skip("Bash 3.2 is unavailable")
    if phase == "drift":
        _installed(binding_fixture, healthy=False)
        command = _recording_processes(binding_fixture, "bash", environment)
    else:
        command = _command(binding_fixture, "bash", "bootstrap-check")
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], state)
    if plugin == "budget-guidance":
        _without_python(binding_fixture, environment)
        native_bash = Path(environment["PATH"]) / "bash"
        native_bash.unlink()
        native_bash.symlink_to(interpreter)
        assert shutil.which("python", path=environment["PATH"]) is None
        assert shutil.which("python3", path=environment["PATH"]) is None
    else:
        tools = binding_fixture["home"] / "stock-shell"
        tools.mkdir()
        (tools / "bash").symlink_to(interpreter)
        environment["PATH"] = str(tools) + os.pathsep + environment["PATH"]
    command[0] = str(interpreter)
    before = _bootstrap_snapshot(binding_fixture)

    result = _run(binding_fixture, environment, command)

    assert json.loads(result.stdout) == {}, result.stdout + result.stderr
    if plugin in DRIVER_BOOTSTRAPS:
        assert result.returncode == 3, result.stdout + result.stderr
        assert "Runtime publication refused" in result.stderr
        assert _bootstrap_snapshot(binding_fixture) == before
        return
    if state in {"missing", "standalone"}:
        assert result.returncode == 0, result.stdout + result.stderr
        witness = binding_fixture["legacy"] / (
            "installer-dispatched" if phase == "cold" else "process-dispatched"
        )
        deadline = time.monotonic() + 2
        while not witness.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert witness.read_text().strip() == ("stamp" if phase == "cold" else "spawn")
    else:
        assert result.returncode == 3, result.stdout + result.stderr
        assert "Runtime publication refused" in result.stderr
        assert _bootstrap_snapshot(binding_fixture) == before


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("phase", ["cold", "drift", "healthy"])
@pytest.mark.parametrize("state", ["managed", "invalid", "foreign", "future", "standalone"])
def test_machines_bootstrap_uses_selected_cell_publication_policy(
    binding_fixture: dict, shell: str, phase: str, state: str,
    powershell_host, bootstrap_payloads,
) -> None:
    environment = _bootstrap_fixture(
        binding_fixture, shell, "agent-machines", bootstrap_payloads,
    )
    primitive = binding_fixture["primitive"]
    cell = primitive.stamp_context(
        payload_version="1.0.0", payload_origin="installed",
        expected_namespace_generation=0, expected_install_generation=0,
        **{key: binding_fixture["arguments"][key]
           for key in ("payload_root", "plugin_id", "environment")},
    )
    primitive.compare_and_swap_activation(
        context=cell["installReceipt"],
        expected_marketplace_id=cell["marketplaceId"],
        expected_plugin_id="agent-machines",
        expected_namespace_generation=cell["namespaceGeneration"],
        expected_install_generation=cell["generation"],
        expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active",
        legacy_disposition="absent",
        legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
        legacy_root=binding_fixture["legacy"], environment=binding_fixture["environment"],
    )
    _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True, "marketplaces": {}},
    })
    root = Path(cell["pluginRoot"])
    arguments = {**binding_fixture["arguments"], "context": cell["installReceipt"]}
    _policy(binding_fixture, root, arguments, state)
    _write(binding_fixture["legacy"] / "runtime-deployment.json", {"managed": state == "standalone"})
    environment["COPILOT_EXTENSIONS_CONTEXT"] = cell["installReceipt"]
    if phase != "cold":
        version = "2.0.0" if phase == "healthy" else "1.0.0"
        (binding_fixture["payload"] / "pyproject.toml").write_text(
            'version = "2.0.0"\n', encoding="utf-8",
        )
        slot = root / "versions" / version
        interpreter = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("selected interpreter fixture\n", encoding="utf-8")
        interpreter.chmod(0o755)
        marker = root / "current-version"
        marker.write_text(version, encoding="utf-8")
        _write(root / "deploy-manifest.json", {
            "schema_version": 4, "service": "agent-machines",
            "source": {
                "repo": "copilot-extensions", "plugin": "agent-machines",
                "kind": "installed", "path": str(binding_fixture["payload"]),
                "version": version, "dirty": False,
            },
            "runtime": {
                "kind": "python", "version": version, "path": str(slot),
                "interpreter": str(interpreter),
                "selectedBy": {"kind": "marker", "path": str(marker), "version": version},
            },
            "installation": {
                "marketplaceId": cell["marketplaceId"], "pluginId": "agent-machines",
                "context": Path(cell["installReceipt"]).as_posix(),
            },
        })
    command = _recording_processes(binding_fixture, shell, environment)
    witness = root / "process-dispatched"
    environment["BOOTSTRAP_PROCESS_WITNESS"] = str(witness)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    legacy_before = _snapshot(binding_fixture["legacy"])
    result = _run(binding_fixture, environment, command, host)
    assert json.loads(result.stdout) == {}
    assert _snapshot(binding_fixture["legacy"]) == legacy_before
    if phase == "healthy":
        assert result.returncode == 0, result.stdout + result.stderr
        assert not result.stderr
        assert _snapshot(binding_fixture["home"]) == before
    elif state == "standalone":
        assert result.returncode == 0, result.stdout + result.stderr
        deadline = time.monotonic() + 2
        while not witness.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert witness.read_text() == "spawn"
    else:
        assert result.returncode == 3, result.stdout + result.stderr
        assert "Runtime publication refused" in result.stderr
        assert not witness.exists()
        assert _snapshot(binding_fixture["home"]) == before
    if host:
        host.close()
        if phase == "healthy" or state != "standalone":
            assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
def test_bootstrap_refusal_does_not_gate_independent_hook_commands(
    binding_fixture: dict, plugin: str, shell: str, bootstrap_payloads,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin, bootstrap_payloads)
    environment["COPILOT_PLUGIN_ROOT"] = str(binding_fixture["payload"])
    shutil.copyfile(
        ROOT / "plugins" / plugin / "payload-invocation.json",
        binding_fixture["payload"] / "payload-invocation.json",
    )
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], "managed")
    hooks = json.loads((ROOT / "plugins" / plugin / "hooks.json").read_text())["hooks"]["sessionStart"]
    suffix = "ps1" if shell == "powershell" else "sh"
    scripts = binding_fixture["payload"] / "scripts"
    for name in ("write-session-guidance", "register-cold-store-provider", "register-bridge-provider"):
        target = scripts / f"{name}.{suffix}"
        if target.exists():
            target.write_text(
                f"[IO.File]::WriteAllText((Join-Path $env:USERPROFILE '{name}'), 'ran')\n"
                "[Console]::Out.Write('{}')\n"
                if shell == "powershell" else
                f'printf ran > "$HOME/{name}"\nprintf "{{}}"\n',
                encoding="utf-8",
            )
    for index, hook in enumerate(hooks):
        (scripts / f"hook-{index}.{suffix}").write_text(hook[shell], encoding="utf-8")
    for index in range(len(hooks)):
        result = _run(
            binding_fixture, environment, _command(binding_fixture, shell, f"hook-{index}"),
        )
        # The existing PowerShell hook expression does not forward the nested
        # script's exit status; the bootstrap diagnostic and no-write gate do.
        expected = 3 if index == 0 and shell == "bash" else 0
        assert result.returncode == expected, result.stdout + result.stderr
        if index == 0:
            assert "Runtime publication refused" in result.stderr
        assert json.loads(result.stdout) == {}
    assert not (binding_fixture["legacy"] / "installer-dispatched").exists()
    assert (binding_fixture["home"] / "write-session-guidance").read_text() == "ran"
    if plugin in {"agent-codespaces", "agent-logger"}:
        name = "register-bridge-provider" if plugin == "agent-codespaces" else "register-cold-store-provider"
        assert (binding_fixture["home"] / name).read_text() == "ran"
