"""Retained installation binding and native PowerShell lock contention."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_adopter_publication import _adopter, _command
from test_bridge_first_use_publication import _initialize_shell_profiles, _run, _selected_cell
from test_command_binding import (
    POWERSHELL, _snapshot, binding_fixture as binding_fixture,
    powershell_host as powershell_host,
)
from test_containers_first_use_publication import containers_release as containers_release
from test_installer_publication import _policy
from test_native_publication import _completed_slot

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.timeout(60)]


def _runtime_module(fixture: dict, root: Path, output: str) -> Path:
    python = _completed_slot({**fixture, "legacy": root})
    site = python.parent.parent / (
        "Lib/site-packages" if os.name == "nt" else
        f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    (site / "agent_containers.py").write_text(f"print({output!r})\n", encoding="utf-8")
    return site


EMITTERS = ["native", pytest.param(
    "powershell", marks=pytest.mark.skipif(
        os.name == "nt" or POWERSHELL is None, reason="POSIX compatibility emitter",
    ),
)]


def _environment(fixture: dict, release: Path) -> dict:
    environment = _adopter(fixture, "agent-containers", release)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    environment["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    environment["COMPUTERNAME"] = "fixture"
    if os.name == "nt":
        environment.pop("PSModulePath", None)
        environment["LOCALAPPDATA"] = str(fixture["home"] / "AppData/Local")
        environment["APPDATA"] = str(fixture["home"] / "AppData/Roaming")
    _initialize_shell_profiles(fixture, environment)
    return environment


def _global_command(fixture: dict, environment: dict, powershell_host):
    path = fixture["home"] / ".local/bin/agent-containers"
    if os.name == "nt":
        host = powershell_host(fixture, environment)
        try:
            return _run(fixture, environment, [
                POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(path.with_suffix(".ps1")), "--help",
            ], host)
        finally:
            host.close()
    return _run(fixture, environment, ["bash", str(path), "--help"], None)


@pytest.mark.parametrize("emitter", EMITTERS)
@pytest.mark.parametrize("spelling", ["trailing", "dot", pytest.param(
    "symlink", marks=pytest.mark.skipif(os.name == "nt", reason="unprivileged POSIX symlink"),
)])
def test_equivalent_root_keeps_known_legacy_global_binding(
    binding_fixture: dict, containers_release: Path, emitter: str, spelling: str,
    powershell_host,
) -> None:
    fixture = binding_fixture
    environment = _environment(fixture, containers_release)
    root = fixture["legacy"]
    _policy(fixture, root, fixture["arguments"], "standalone")
    supplied = str(root) + (os.sep if spelling == "trailing" else os.sep + ".")
    if spelling == "symlink":
        alias = root.with_name("runtime-alias")
        alias.symlink_to(root, target_is_directory=True)
        supplied = str(alias)
    shell = "powershell" if os.name == "nt" or emitter == "powershell" else "bash"
    result = _run(fixture, environment, [
        *_command(fixture, shell, "init"), "stamp",
        "-InstallDir" if shell == "powershell" else "--install-dir", supplied,
    ], None)
    assert result.returncode == 0, result.stdout + result.stderr
    site = _runtime_module(fixture, root, "")
    (site / "agent_containers.py").write_text(
        "import os\nprint(os.environ['AGENT_CONTAINERS_HOME'])\n", encoding="utf-8",
    )
    pointer = root / "payload-dir"
    pointer.rename(pointer.with_name("payload-dir.unavailable"))
    fixture["payload"].rename(fixture["payload"].with_name("publisher-unavailable"))
    result = _global_command(fixture, environment, powershell_host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == str(root.resolve())


@pytest.mark.parametrize("emitter", EMITTERS)
@pytest.mark.parametrize("scope", ["legacy", "selected-cell"])
def test_fresh_provision_publishes_binding_without_prior_stamp(
    binding_fixture: dict, containers_release: Path, emitter: str, scope: str,
    powershell_host,
) -> None:
    fixture = binding_fixture
    environment = _environment(fixture, containers_release)
    root, arguments = fixture["legacy"], fixture["arguments"]
    if scope == "selected-cell":
        root, arguments = _selected_cell(fixture, environment)
    _policy(fixture, root, arguments, "standalone")
    _runtime_module(fixture, root, "fresh-ready")
    python = root / "versions/1.0.0" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = python.parent.parent / (
        "Lib/site-packages" if os.name == "nt" else
        f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
    )
    (site / "credential_relay.py").write_text("", encoding="utf-8")
    scripts = fixture["payload"] / "scripts"
    profile = scripts / "runtime_profile.py"
    original = profile.with_name("recorded-runtime-profile.py")
    profile.rename(original)
    result = {
        "profile": "base", "runtimeVersion": "1.0.0",
        "venv": str(python.parent.parent), "python": str(python), "runtimeSource": None,
    }
    profile.write_text(
        "import json,runpy,sys\n"
        "if '--root' not in sys.argv:\n"
        f" runpy.run_path({str(original)!r},run_name='__main__')\n"
        "else:\n"
        f" result={result!r}\n"
        " print('\\t'.join(list(result.values())[:4]) if '--tsv' in sys.argv else json.dumps(result))\n",
        encoding="utf-8",
    )
    tool = fixture["home"] / "test-bin" / ("uv.cmd" if os.name == "nt" else "uv")
    tool.parent.mkdir(exist_ok=True)
    tool.write_text("@exit /b 97\n" if os.name == "nt" else "#!/bin/sh\nexit 97\n", encoding="utf-8")
    tool.chmod(0o755)
    environment["PATH"] = str(tool.parent) + os.pathsep + environment["PATH"]
    assert not (root / "payload-dir").exists()
    shell = "powershell" if os.name == "nt" or emitter == "powershell" else "bash"
    result = _run(fixture, environment, [
        *_command(fixture, shell, "init"), "provision",
        "-InstallDir" if shell == "powershell" else "--install-dir", str(root),
    ], None)
    assert result.returncode == 0, result.stdout + result.stderr
    pointer = root / "payload-dir"
    assert pointer.is_file(), "fresh provision did not publish its payload binding"
    assert Path(pointer.read_text(encoding="utf-8-sig").strip()) == fixture["payload"].resolve()
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    result = _global_command(fixture, environment, powershell_host)
    assert result.returncode == 0 and result.stdout.strip() == "fresh-ready", result.stdout + result.stderr


@pytest.mark.parametrize("stamp_shell", ["native", pytest.param(
    "powershell", marks=pytest.mark.skipif(
        os.name == "nt" or POWERSHELL is None, reason="POSIX compatibility emitter",
    ),
)])
@pytest.mark.parametrize("loss", ["active", "publisher", "pointer", "deactivated"])
def test_cell_bound_global_never_uses_legacy_resolver_fallback(
    binding_fixture: dict, containers_release: Path, stamp_shell: str, loss: str,
    powershell_host,
) -> None:
    fixture = binding_fixture
    environment = _adopter(fixture, "agent-containers", containers_release)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    if os.name == "nt":
        environment.pop("PSModulePath", None)
        environment["LOCALAPPDATA"] = str(fixture["home"] / "AppData/Local")
        environment["APPDATA"] = str(fixture["home"] / "AppData/Roaming")
    profiles = _initialize_shell_profiles(fixture, environment)
    root, arguments = _selected_cell(fixture, environment)
    _policy(fixture, root, arguments, "standalone")
    shell = "powershell" if os.name == "nt" or stamp_shell == "powershell" else "bash"
    stamp = [*_command(fixture, shell, "init"), "stamp",
             "-InstallDir" if shell == "powershell" else "--install-dir", str(root)]
    result = _run(fixture, environment, stamp, None)
    assert result.returncode == 0, result.stdout + result.stderr
    _runtime_module(fixture, root, "cell-ready")
    if loss == "publisher":
        fixture["payload"].rename(fixture["payload"].with_name("publisher-unavailable"))
    elif loss == "pointer":
        pointer = root / "payload-dir"
        pointer.rename(pointer.with_name("payload-dir.unavailable"))
    elif loss == "deactivated":
        receipt = json.loads((root / "installation-activation.json").read_text(encoding="utf-8"))
        changed = fixture["primitive"].compare_and_swap_activation(
            context=arguments["context"], expected_marketplace_id=receipt["marketplaceId"],
            expected_plugin_id="agent-containers",
            expected_namespace_generation=receipt["namespaceGeneration"],
            expected_install_generation=receipt["installGeneration"],
            expected_activation_generation=receipt["generation"],
            activation_mode="legacy", activation_state="deactivated", legacy_disposition="absent",
            legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
            legacy_root=fixture["legacy"], environment=fixture["environment"],
        )
        assert changed["activationChanged"], changed
        pointer = root / "payload-dir"
        pointer.rename(pointer.with_name("payload-dir.unavailable"))
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    path = fixture["home"] / ".local/bin/agent-containers"
    command = ["bash", str(path), "--help"]
    host = None
    if os.name == "nt":
        command = [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(path.with_suffix(".ps1")), "--help"]
        host = powershell_host(fixture, environment)
    before = {key: value for key, value in _snapshot(fixture["home"]).items() if key not in profiles}
    result = _run(fixture, environment, command, host)
    after = {key: value for key, value in _snapshot(fixture["home"]).items() if key not in profiles}
    if host:
        host.close()
    evidence = result.stdout + result.stderr
    if loss == "active":
        assert result.returncode == 0 and result.stdout.strip() == "cell-ready", evidence
    else:
        assert result.returncode == 126 and not result.stdout.strip(), evidence
        assert before == after


@pytest.mark.skipif(os.name == "nt" or POWERSHELL is None, reason="POSIX FileShare contention")
@pytest.mark.parametrize("retry_posix", [True, False], ids=["posix-retry", "windows-only"])
def test_powershell_posix_cold_caller_waits_for_owner_then_reuses_runtime(
    binding_fixture: dict, containers_release: Path, retry_posix: bool,
) -> None:
    import fcntl

    fixture = binding_fixture
    environment = _adopter(fixture, "agent-containers", containers_release)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    root = fixture["legacy"]
    environment["AGENT_CONTAINERS_HOME"] = str(root)
    _policy(fixture, root, fixture["arguments"], "standalone")
    scripts = fixture["payload"] / "scripts"
    helper = scripts / "installation-context/installation-context.ps1"
    decisions = {}
    for action in ("status", "probe-plugin-publication", "probe-legacy"):
        result = _run(fixture, environment, [
            POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(helper), action,
            "-PayloadRoot", str(fixture["payload"]), "-PluginId", "agent-containers",
            "-LegacyRoot", str(root),
        ], None)
        assert result.returncode == 0, result.stdout + result.stderr
        decisions[action] = json.loads(result.stdout)
    # Freeze real, unchanged policy answers outside the OS contention handshake.
    # The first-use matrix separately exercises live admission and its refresh.
    cases = []
    for action, decision in decisions.items():
        encoded = json.dumps(decision).replace("'", "''")
        cases.append(f" '{action}' {{ [Console]::Write('{encoded}'); exit 0 }}\n")
    helper.write_text(
        "param([string]$Action)\n"
        "switch($Action){\n" + "".join(cases)
        + " default { throw 'unexpected admission action' }\n}\n", encoding="utf-8",
    )
    if not retry_posix:
        gate = scripts / "runtime-gate.ps1"
        content = gate.read_text(encoding="utf-8")
        predicate = (
            "$posixContention = [Environment]::OSVersion.Platform -eq [PlatformID]::Unix "
            "-and $code -eq 11"
        )
        assert content.count(predicate) == 1
        gate.write_text(content.replace(predicate, "$posixContention = $false"), encoding="utf-8")
    installer_marker = root / "installer-ran"
    (scripts / "init.ps1").write_text(
        "param([string]$Action,[string]$InstallDir)\n"
        "[IO.File]::WriteAllText((Join-Path $InstallDir 'installer-ran'),'unexpected')\nexit 37\n",
        encoding="utf-8",
    )
    resolver = scripts / "resolve-runtime.ps1"
    real_resolver = scripts / "recorded-resolver.ps1"
    resolver.rename(real_resolver)
    quote = lambda path: "'" + str(path).replace("'", "''") + "'"
    ready = root / "contender-ready"
    proceed = root / "contender-proceed"
    phases = root / "contender-phases"
    resolver.write_text(
        "# Trace contract: normal/read-only/ready-v1\n"
        "param([string]$TraceMode='normal')\n"
        f"[IO.File]::AppendAllText({quote(phases)}, $TraceMode+[Environment]::NewLine)\n"
        f". {quote(real_resolver)} -TraceMode $TraceMode\n"
        "if($TraceMode -eq 'normal'){\n"
        f" [IO.File]::WriteAllText({quote(ready)},'ready')\n"
        " $wait=[Diagnostics.Stopwatch]::StartNew()\n"
        f" while(-not [IO.File]::Exists({quote(proceed)})){{\n"
        "  if($wait.Elapsed.TotalSeconds -gt 10){throw 'owner coordination timed out'}\n"
        "  Start-Sleep -Milliseconds 10\n }\n}\n",
        encoding="utf-8",
    )
    with (root / ".provision.lock").open("a+b") as lock:
        process = subprocess.Popen(
            [POWERSHELL, "-NoLogo", "-NoProfile", "-File",
             str(fixture["payload"] / "bin/agent-containers.ps1"), "--help"],
            cwd=fixture["home"], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists(), {
                "exit": process.poll(),
                "phases": phases.read_text() if phases.exists() else "resolver not reached",
            }
            fcntl.flock(lock, fcntl.LOCK_EX)
            proceed.write_bytes(b"continue")
            try:
                stdout, stderr = process.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                assert retry_posix, "negative control did not reach the contended file open"
            else:
                if not retry_posix:
                    assert process.returncode != 0 and "used by another process" in stderr, stdout + stderr
                    assert not installer_marker.exists()
                    return
                pytest.fail(f"contender did not wait for the owner: {stdout}{stderr}")
            _runtime_module(fixture, root, "shared-ready")
            fcntl.flock(lock, fcntl.LOCK_UN)
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0 and stdout.strip() == "shared-ready", stdout + stderr
            assert not installer_marker.exists()
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
