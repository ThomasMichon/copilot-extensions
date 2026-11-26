"""Exercise the payload's pre-runtime helper with an unusable PATH Python."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import importlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs

import test_payload_invocation as payload_tests

PLUGIN = Path(__file__).resolve().parents[1]
windows_payload_runtime = payload_tests.windows_payload_runtime
pytestmark = pytest.mark.portfolio_tier("T3")


def _payload_command(host: str, plugin: Path, shell: str) -> list[str]:
    entrypoint = plugin / "bin" / "agent-bridge.ps1"
    if shell == "powershell":
        quoted = str(entrypoint).replace("'", "''")
        command = f"& '{quoted}' send fixture --prompt-file -; exit $LASTEXITCODE"
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        return [host, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]
    return [
        host, "-NoProfile", "-NonInteractive", "-File", str(entrypoint),
        "send", "fixture", "--prompt-file", "-",
    ]


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("path_python", ["poison", "old"])
def test_prepared_payload_ignores_path_python(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path: Path,
    shell: str,
    path_python: str,
) -> None:
    """A prepared Bridge must reach its runtime without invoking PATH Python."""
    plugin, _, base_env = windows_payload_runtime
    managed_root = tmp_path / "empty-managed-root"
    managed_root.mkdir()
    poison = tmp_path / "poison"
    poison.mkdir()
    marker = tmp_path / "path-python-called"
    (poison / "python.cmd").write_text(
        '@echo off\r\n> "%PROBE_PATH_PYTHON%" echo called\r\nexit /b 39\r\n',
        encoding="ascii",
    )
    env = dict(base_env)
    env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    path_root = str(poison)
    if path_python == "old":
        old_python = os.environ.get("BRIDGE_TEST_OLD_PYTHON")
        if not old_python:
            pytest.skip("requires a read-only Python 3.9 negative control")
        path_root = str(Path(old_python).parent)
    env["PATH"] = path_root + os.pathsep + env["PATH"]
    env["PROBE_PATH_PYTHON"] = str(marker)
    env["UV_PYTHON_INSTALL_DIR"] = str(managed_root)
    env["UV_PYTHON_DOWNLOADS"] = "never"
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    result = subprocess.run(
        _payload_command(host, plugin, shell),
        input="managed-ingress-probe",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        **no_window_kwargs(),
    )
    assert result.returncode == 17, result.stdout + result.stderr
    assert result.stdout == "managed-ingress-probe"
    assert not marker.exists()


@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("action", ["install", "update"])
def test_install_prepares_only_after_native_admission(
    tmp_path: Path, shell: str, admitted: bool, action: str,
) -> None:
    """Execute the actual native entry with private admission/preparation adapters."""
    if os.name != "nt":
        pytest.skip("native Windows bootstrap")
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    scripts = tmp_path / "payload" / "scripts"
    scripts.mkdir(parents=True)
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    entry = source[:source.index("# === install-contract:test-persistent-environment")]
    initializer = ""
    initializer_start = source.find("function Initialize-BridgeBootstrapPython {")
    if initializer_start >= 0:
        initializer_end = source.index("\nfunction ", initializer_start + 1)
        initializer = source[initializer_start:initializer_end]
    action_start = source.index(f"function Invoke-{action.title()} {{")
    boundary = (
        "\n    # Check for migration" if action == "install"
        else "\n    # Serialize concurrent installers"
    )
    action_stage = source[action_start:source.index(boundary, action_start)] + "\n}\n"
    installer = scripts / "install.ps1"
    installer.write_text(
        entry + initializer + "\n$script:PathsReady=$true\n" + action_stage
        + f"\nInvoke-{action.title()}\n",
        encoding="utf-8",
    )
    calls = tmp_path / "calls"
    (scripts / "versioned_runtime_native.ps1").write_text(
        """
$script:UvReady=$false
$script:PathsReady=$false
function Write-Step { param($Message) }
function Ensure-Uv {
    if (-not $script:PathsReady) { throw 'uv owner used before path initialization' }
    [IO.File]::AppendAllText($env:PROBE_CALLS, "uv-owner`n")
    $script:UvReady=$true
    return $true
}
function Enter-NativeInstallerDriver {
    param($Payload,$Root,$Plugin,$Action,$Options,$WheelProfile,$WheelDirectory,
          $WheelPython,$WheelUv,$BootstrapPython)
    if ($env:PROBE_ADMIT -ne '1') {
        [IO.File]::AppendAllText($env:PROBE_CALLS, "refused`n")
        throw 'fixture hosting policy refusal'
    }
    [IO.File]::AppendAllText($env:PROBE_CALLS, "admitted`n")
    [pscustomobject]@{
        Participant=$true; ExitCode=0; Python=$BootstrapPython; Arguments=@()
    }
}
""",
        encoding="utf-8",
    )
    (scripts / "resolve-bootstrap-python.ps1").write_text(
        """
function Resolve-PayloadBootstrapPython {
    param($Operation='Inspect',[switch]$AllowUnprepared,$TimeoutMilliseconds)
    if ($Operation -eq 'Prepare' -and -not $script:UvReady) {
        throw 'managed Python preparation ran before the uv prerequisite owner'
    }
    [IO.File]::AppendAllText($env:PROBE_CALLS, $Operation + "`n")
    return $env:PROBE_PYTHON
}
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.pop("COPILOT_EXTENSIONS_HUMAN_UTF8", None)
    env.update(
        COPILOT_EXTENSIONS_TEST_CONTAINED="0",
        PROBE_ADMIT="1" if admitted else "0",
        PROBE_CALLS=str(calls),
        PROBE_PYTHON=sys.executable,
    )
    result = subprocess.run(
        [
            host, "-NoProfile", "-NonInteractive", "-File", str(installer),
            action, "-InstallDir", str(tmp_path / "runtime"),
        ],
        env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert (result.returncode == 0) is admitted, result.stdout + result.stderr
    expected = (
        ["Inspect", "admitted", "uv-owner", "Prepare"]
        if admitted else ["Inspect", "refused"]
    )
    assert calls.read_text(encoding="utf-8").splitlines() == expected


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_native_driver_keeps_selected_bootstrap(
    tmp_path: Path, shell: str,
) -> None:
    """A selected managed executable reaches the existing driver unchanged."""
    if os.name != "nt":
        pytest.skip("native Windows bootstrap")
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    script = tmp_path / "driver.ps1"
    script.write_text(
        """
. $env:PROBE_NATIVE
function Resolve-NativeBootstrapPython { throw 'PATH discovery is forbidden' }
function Invoke-Hidden {
    param($FilePath,$ArgList,[switch]$StreamOutput)
    if ($FilePath -cne $env:PROBE_PYTHON) { throw 'selected interpreter changed' }
    $global:LASTEXITCODE=0
}
$entry=Enter-NativeInstallerDriver -Payload $env:PROBE_PAYLOAD -Root $env:PROBE_ROOT `
    -Plugin agent-bridge -Action install -BootstrapPython $env:PROBE_PYTHON
$entry | ConvertTo-Json -Compress -Depth 4
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        PROBE_NATIVE=str(PLUGIN / "scripts" / "versioned_runtime_native.ps1"),
        PROBE_PAYLOAD=str(PLUGIN),
        PROBE_ROOT=str(tmp_path / "runtime"),
        PROBE_PYTHON=sys.executable,
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["Python"] == sys.executable


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_missing_managed_python_does_not_fall_back_or_prepare(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path: Path,
    shell: str,
) -> None:
    """Ordinary communication cannot acquire its missing bootstrap prerequisite."""
    plugin, _, base_env = windows_payload_runtime
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    env = dict(base_env)
    env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    env["AGENT_BRIDGE_INSTALL_DIR"] = str(tmp_path / "absent-runtime")
    env["AGENT_BRIDGE_CONFIG_DIR"] = env["AGENT_BRIDGE_INSTALL_DIR"]
    managed_root = tmp_path / "empty-managed-root"
    managed_root.mkdir()
    env["UV_PYTHON_INSTALL_DIR"] = str(managed_root)
    env["UV_PYTHON_DOWNLOADS"] = "never"
    result = subprocess.run(
        _payload_command(host, plugin, shell),
        input="must-not-reach-runtime",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        **no_window_kwargs(),
    )
    assert result.returncode == 126, result.stdout + result.stderr
    assert "managed-python-not-prepared" in result.stderr
    assert "must-not-reach-runtime" not in result.stdout
    assert not list(managed_root.iterdir())


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize(
    ("poison_python_home", "ambient_context"),
    [(False, None), (True, None), (False, "foreign"), (False, "stale")],
)
def test_session_writer_uses_managed_python(
    tmp_path: Path, shell: str, poison_python_home: bool, ambient_context: str | None,
) -> None:
    """The real hook selects the same managed helper without using PATH Python."""
    if os.name != "nt":
        pytest.skip("native Windows bootstrap")
    managed_root = os.environ.get("BRIDGE_TEST_MANAGED_PYTHON_ROOT")
    if not managed_root:
        pytest.skip("requires a read-only prepared uv Python root")
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    for name in (
        "write-session-guidance.ps1", "resolve-bootstrap-python.ps1",
        "bootstrap-uv.ps1", "bootstrap-python-version.txt", "versioned_runtime_native.ps1",
        "resolve-runtime.ps1", "bootstrap-capture.ps1", "bootstrap-capture.exe",
    ):
        shutil.copyfile(PLUGIN / "scripts" / name, scripts / name)
    (scripts / "write_session_guidance.py").write_text(
        "import json,sys\nprint(json.dumps({'python':sys.executable}))\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    if ambient_context:
        plugin_id = "agent-bridge" if ambient_context == "stale" else "another-plugin"
        context = (
            tmp_path / "marketplaces" / ("fixture--" + "0" * 16)
            / "plugins" / plugin_id / "install.json"
        )
        if ambient_context == "foreign":
            context.parent.mkdir(parents=True)
            context.write_text("{}", encoding="utf-8")
        env["COPILOT_EXTENSIONS_CONTEXT"] = str(context)
    poison = tmp_path / "poison"
    poison.mkdir()
    marker = tmp_path / "path-python-called"
    for name in ("python.cmd", "python3.cmd", "py.cmd"):
        (poison / name).write_text(
            '@echo off\r\n> "%PROBE_PATH_PYTHON%" echo called\r\nexit /b 39\r\n',
            encoding="ascii",
        )
    env.update(
        COPILOT_PLUGIN_ROOT=str(payload),
        AGENT_BRIDGE_INSTALL_DIR=str(tmp_path / "absent-runtime"),
        AGENT_BRIDGE_CONFIG_DIR=str(tmp_path / "absent-runtime"),
        UV_PYTHON_INSTALL_DIR=managed_root,
        UV_PYTHON_DOWNLOADS="never",
        PATH=str(poison) + os.pathsep + env["PATH"],
        PROBE_PATH_PYTHON=str(marker),
    )
    if poison_python_home:
        env["PYTHONHOME"] = str(tmp_path / "invalid-python-home")
    result = subprocess.run(
        [
            host, "-NoProfile", "-NonInteractive", "-File",
            str(scripts / "write-session-guidance.ps1"),
        ],
        env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    selected = Path(json.loads(result.stdout)["python"])
    assert selected.is_relative_to(Path(managed_root))
    assert not result.stderr
    assert not marker.exists()


@pytest.fixture(scope="module")
def materialized_budget_sdk(tmp_path_factory: pytest.TempPathFactory) -> Path:
    source = PLUGIN.parent / "budget-guidance"
    payload = tmp_path_factory.mktemp("budget-sdk") / "payload"
    payload.mkdir()
    for name in ("plugin.json", "pyproject.toml", "payload-invocation.json"):
        shutil.copyfile(source / name, payload / name)
    shutil.copytree(
        source / "scripts", payload / "scripts", ignore=shutil.ignore_patterns("__pycache__"),
    )
    materializer = importlib.import_module("materialize_main")
    changes = getattr(materializer, "materialize_payload_invocation_into")(
        source_consumer_dir=source, dest_consumer_dir=payload, canonical_root=PLUGIN.parents[1],
    )
    assert changes and not any(change.startswith("SKIP ") for change in changes), changes
    assert (payload / "scripts" / "bootstrap-python-version.txt").is_file()
    assert not (payload / "scripts" / "resolve-bootstrap-python.ps1").exists()
    return payload


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_unhinted_materialized_budget_sdk_does_not_adopt_bridge_resolver(
    materialized_budget_sdk: Path, tmp_path: Path, shell: str,
) -> None:
    if os.name != "nt":
        pytest.skip("native Windows bootstrap")
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("COPILOT_EXTENSIONS_")
    }
    home = tmp_path / "home"
    home.mkdir()
    env.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_HOME=str(home / ".copilot"),
        PATH=str(Path(sys.executable).parent) + os.pathsep + env["PATH"],
    )
    result = subprocess.run(
        [
            host, "-NoProfile", "-NonInteractive", "-File",
            str(materialized_budget_sdk / "scripts" / "installation-context" / "installation-context.ps1"),
            "-Action", "source-id", "-SourceJson",
            json.dumps({"kind": "github", "repo": "example/extensions"}),
            "-MarketplaceKey", "fixture",
        ],
        env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["marketplaceId"].startswith("fixture--")


@dataclass(frozen=True)
class NamespacedRuntime:
    plugin: Path
    environment: dict[str, str]
    receipt: Path
    runtime: Path
    durable: Path
    legacy: Path


def _create_namespaced_runtime(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path_factory: pytest.TempPathFactory,
    locator: str,
    *,
    home_name: str = "home",
) -> NamespacedRuntime:
    from agent_bridge import _installation_context as installation_context

    plugin, _, base_env = windows_payload_runtime
    tmp_path = tmp_path_factory.mktemp(f"namespaced-{locator}")
    home = tmp_path / home_name
    home.mkdir()
    if locator == "installed":
        source = plugin
        plugin = home / ".copilot" / "installed-plugins" / "fixture" / "agent-bridge"
        (plugin / "bin").mkdir(parents=True)
        shutil.copyfile(source / "bin" / "agent-bridge.ps1", plugin / "bin" / "agent-bridge.ps1")
        for name in ("plugin.json", "payload-invocation.json"):
            shutil.copyfile(source / name, plugin / name)
        scripts = plugin / "scripts"
        shutil.copytree(source / "scripts" / "installation-context", scripts / "installation-context")
        for name in (
            "runtime-gate.ps1", "resolve-runtime.ps1", "install.ps1",
            "versioned_runtime_native.ps1", "bootstrap-python-version.txt",
            "bootstrap-uv.ps1", "resolve-bootstrap-python.ps1", "versioned_runtime_probe.py",
            "bootstrap-capture.ps1", "bootstrap-capture.exe",
        ):
            shutil.copyfile(source / "scripts" / name, scripts / name)
        shutil.copytree(source / "scripts/_bootstrap_python", scripts / "_bootstrap_python")
        (home / ".copilot" / "settings.json").write_text(
            json.dumps({"extraKnownMarketplaces": {
                "fixture": {"source": {"kind": "github", "repo": "example/extensions"}},
            }}), encoding="utf-8",
        )
    durable = home / ".copilot-extensions"
    legacy = home / ".agent-bridge"
    context = getattr(installation_context, "stamp_context")(
        payload_root=plugin, plugin_id="agent-bridge",
        source_descriptor=(
            None if locator == "installed"
            else {"kind": "github", "repo": "example/extensions"}
        ),
        durable_home=durable, copilot_home=home / ".copilot",
        payload_version=json.loads((plugin / "plugin.json").read_text())["version"],
        payload_origin="installed" if locator == "installed" else "explicit",
        expected_namespace_generation=0, expected_install_generation=0,
        environment={"HOME": str(home), "USERPROFILE": str(home)},
    )
    (durable / "installation-mode.json").write_text(
        json.dumps({
            "schema": getattr(installation_context, "POLICY_SCHEMA"), "version": 1,
            "installationMode": {"enabled": True},
        }), encoding="utf-8",
    )
    activation = getattr(installation_context, "compare_and_swap_activation")(
        context=context["installReceipt"], durable_home=durable,
        expected_marketplace_id=context["marketplaceId"], expected_plugin_id="agent-bridge",
        expected_namespace_generation=context["namespaceGeneration"],
        expected_install_generation=context["generation"], expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active",
        legacy_disposition="absent", legacy_root=legacy,
        legacy_probe={"declared": True, "result": "absent", "checkedAt": None},
        environment={"HOME": str(home), "USERPROFILE": str(home)},
    )
    assert activation["status"] == "ready"
    runtime = Path(context["pluginRoot"])
    prepared = Path(base_env["AGENT_BRIDGE_INSTALL_DIR"])
    slot = runtime / "versions" / "1.2.3"
    original_slot = prepared / "versions" / "1.2.3"
    module = slot / "Lib" / "site-packages" / "agent_bridge"
    module.mkdir(parents=True)
    (slot / "Scripts").mkdir()
    for name in ("pyvenv.cfg", ".install-complete.json"):
        shutil.copyfile(original_slot / name, slot / name)
    shutil.copyfile(original_slot / "Scripts" / "python.exe", slot / "Scripts" / "python.exe")
    for name in ("__init__.py", "__main__.py"):
        shutil.copyfile(original_slot / "Lib" / "site-packages" / "agent_bridge" / name, module / name)
    shutil.copyfile(prepared / "current-version", runtime / "current-version")
    env = dict(base_env)
    for key in (
        "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", "AGENT_BRIDGE_INSTALL_DIR",
        "AGENT_BRIDGE_CONFIG_DIR", "AGENT_RT_ROOT",
    ):
        env.pop(key, None)
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    env.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_HOME=str(home / ".copilot"),
        COPILOT_EXTENSIONS_CONTEXT=context["installReceipt"],
        COPILOT_PLUGIN_ROOT=str(plugin),
        EXPECTED_RUNTIME_ROOT=str(runtime), EXPECTED_RUNTIME_SLOT=str(slot),
        EXPECTED_RUNTIME_PYTHON=str(slot / "Scripts" / "python.exe"),
        PATH=os.pathsep.join((base_env["BRIDGE_TEST_UV_TOOLS"], str(system32))),
        UV_PYTHON_INSTALL_DIR=str(tmp_path / "empty-managed-root"),
        UV_PYTHON_DOWNLOADS="never",
    )
    if locator == "installed":
        env.pop("COPILOT_EXTENSIONS_CONTEXT")
    return NamespacedRuntime(plugin, env, Path(context["installReceipt"]), runtime, durable, legacy)


@pytest.fixture(scope="module", params=["context", "installed"])
def namespaced_runtime(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path_factory: pytest.TempPathFactory,
    request: pytest.FixtureRequest,
) -> NamespacedRuntime:
    return _create_namespaced_runtime(windows_payload_runtime, tmp_path_factory, str(request.param))


@pytest.fixture(scope="module")
def tokenized_context_runtime(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path_factory: pytest.TempPathFactory,
) -> NamespacedRuntime:
    return _create_namespaced_runtime(
        windows_payload_runtime, tmp_path_factory, "context",
        home_name="h%BRIDGE_CTX_2%",
    )


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("action", ["resolve", "validate", "status"])
def test_context_environment_tokens_expand_exactly_once(
    tokenized_context_runtime: NamespacedRuntime, shell: str, action: str,
) -> None:
    fixture = tokenized_context_runtime
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    literal = "h%BRIDGE_CTX_2%"

    def raw(path: Path) -> str:
        return str(path).replace(literal, "%BRIDGE_CTX_1%")

    env = dict(fixture.environment)
    env.update(
        BRIDGE_CTX_1=literal,
        BRIDGE_CTX_2="must-not-be-expanded",
    )
    arguments = [
        host, "-NoProfile", "-NonInteractive", "-File",
        str(fixture.plugin / "scripts" / "installation-context" / "installation-context.ps1"),
        "-Action", action, "-Context", raw(fixture.receipt),
        "-PayloadRoot", str(fixture.plugin),
        "-ExpectedPluginId", "agent-bridge", "-ExpectedPayloadRoot", str(fixture.plugin),
        "-CopilotHome", raw(Path(env["COPILOT_HOME"])),
        "-DurableHome", raw(fixture.durable),
    ]
    if action == "status":
        arguments += ["-LegacyRoot", raw(fixture.legacy)]
    result = subprocess.run(
        arguments, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)
    assert "must-not-be-expanded" not in result.stdout + result.stderr


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_namespaced_prepared_runtime_is_offline_bootstrap_handoff(
    namespaced_runtime: NamespacedRuntime, shell: str,
) -> None:
    """Only the addressed cell is prepared; the real SDK still admits the app."""
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    fixture = namespaced_runtime
    command = _payload_command(host, fixture.plugin, shell)
    result = subprocess.run(
        command, input="offline-namespaced-handoff",
        env=fixture.environment, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 17, result.stdout + result.stderr
    assert result.stdout == "offline-namespaced-handoff"
    assert not fixture.legacy.exists()


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_namespaced_explicit_context_overrides_ambient_hint(
    namespaced_runtime: NamespacedRuntime, shell: str,
) -> None:
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    fixture = namespaced_runtime
    explicit_env = dict(fixture.environment)
    explicit_env["COPILOT_EXTENSIONS_CONTEXT"] = str(fixture.legacy / "stale" / "install.json")
    explicit = subprocess.run(
        [
            host, "-NoProfile", "-NonInteractive", "-File",
            str(fixture.plugin / "scripts" / "installation-context" / "installation-context.ps1"),
            "-Action", "status", "-Context", str(fixture.receipt).upper(),
            "-DurableHome", str(fixture.durable), "-PayloadRoot", str(fixture.plugin),
            "-PluginId", "agent-bridge", "-LegacyRoot", str(fixture.legacy),
        ],
        env=explicit_env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert explicit.returncode == 0, explicit.stdout + explicit.stderr
    assert json.loads(explicit.stdout)["runtimeRoot"] == str(fixture.runtime)


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_namespaced_corrupt_identity_is_refused_after_bootstrap(
    namespaced_runtime: NamespacedRuntime, shell: str,
) -> None:
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    fixture = namespaced_runtime
    original = fixture.receipt.read_bytes()
    invalid = json.loads(original)
    invalid["pluginId"] = "other-plugin"
    refused_env = dict(fixture.environment)
    refused_env["COPILOT_EXTENSIONS_CONTEXT"] = str(fixture.receipt)
    try:
        fixture.receipt.write_text(json.dumps(invalid), encoding="utf-8")
        refused = subprocess.run(
            _payload_command(host, fixture.plugin, shell), input="must-not-reach-runtime",
            env=refused_env, capture_output=True, text=True, encoding="utf-8",
            timeout=30, **no_window_kwargs(),
        )
    finally:
        fixture.receipt.write_bytes(original)
    assert refused.returncode == 126, refused.stdout + refused.stderr
    assert "must-not-reach-runtime" not in refused.stdout
    assert "bootstrap" not in refused.stderr.lower()


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize(
    "action,repair",
    [("start", False), ("stop", False), ("status", False), ("schedule", False),
     ("provision", True)],
)
def test_runtime_only_dispatch_never_discovers_ambient_seed(
    tmp_path: Path, shell: str, action: str, repair: bool,
) -> None:
    if os.name != "nt":
        pytest.skip("native Windows bootstrap")
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    start = source.index("function Initialize-SourceRuntime {")
    end = source.index("\nfunction ", start + 1)
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "resolve-runtime.ps1").write_text(
        "$AgentRtPy=$env:PROBE_PYTHON\n", encoding="utf-8",
    )
    script = tmp_path / "dispatch.ps1"
    script.write_text(
        """
$ErrorActionPreference='Stop'
$Action=$env:PROBE_ACTION
$ForceTaskRepair=($env:PROBE_REPAIR -eq '1')
$WheelProfile=''
$driverPython=$null
$PluginDir=$env:PROBE_PAYLOAD
$InstallDir=$env:PROBE_RUNTIME
function Write-InstallerLifecycle { param($Outcome) }
function Resolve-NativeBootstrapPython { throw 'ambient seed discovery is forbidden' }
function Invoke-Start { 'start' }
function Invoke-Stop { 'stop' }
function Invoke-Status { 'status' }
function Invoke-Install { 'install' }
function Invoke-Update { 'update' }
function Invoke-Provision { 'provision' }
function Ensure-ScheduledTask { 'schedule' }
function Enter-InstallLock { $true }
function Exit-InstallLock {}
"""
        + source[start:end] + "\n"
        + source[source.index("$script:InstallerLifecycleAction ="):],
        encoding="utf-8",
    )
    env = dict(os.environ)
    for key in ("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", "COPILOT_EXTENSIONS_RUNTIME_SOURCE_PIN"):
        env.pop(key, None)
    env.update(
        PROBE_ACTION=action, PROBE_REPAIR=str(int(repair)), PROBE_PAYLOAD=str(tmp_path / "payload"),
        PROBE_RUNTIME=str(runtime), PROBE_PYTHON=sys.executable,
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(script)],
        env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == action


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("action", ["source-id", "stamp"])
def test_context_free_sdk_ignores_unrelated_ambient_context(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path: Path,
    shell: str,
    action: str,
) -> None:
    plugin, _, base_env = windows_payload_runtime
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    env = dict(base_env)
    env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    env["COPILOT_EXTENSIONS_CONTEXT"] = str(tmp_path / "another-plugin" / "install.json")
    args = [
        host, "-NoProfile", "-NonInteractive", "-File",
        str(plugin / "scripts" / "installation-context" / "installation-context.ps1"),
        "-Action", action, "-SourceJson",
        json.dumps({"kind": "github", "repo": "example/extensions"}),
        "-MarketplaceKey", "fixture",
    ]
    if action == "stamp":
        args += [
            "-PayloadRoot", str(plugin), "-PluginId", "agent-bridge",
            "-PayloadVersion", json.loads((plugin / "plugin.json").read_text())["version"],
            "-PayloadOrigin", "explicit", "-DurableHome", str(tmp_path / "durable"),
            "-ExpectedNamespaceGeneration", "0", "-ExpectedInstallGeneration", "0",
        ]
    result = subprocess.run(
        args, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["marketplaceId"].startswith("fixture--")


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_explicit_legacy_root_selects_prepared_offline_helper(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path: Path,
    shell: str,
) -> None:
    plugin, _, base_env = windows_payload_runtime
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    runtime = base_env["AGENT_BRIDGE_INSTALL_DIR"]
    home = tmp_path / "home"
    home.mkdir()
    env = dict(base_env)
    for key in (
        "COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", "COPILOT_EXTENSIONS_CONTEXT",
        "AGENT_BRIDGE_INSTALL_DIR", "AGENT_BRIDGE_CONFIG_DIR", "AGENT_RT_ROOT",
    ):
        env.pop(key, None)
    env.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_HOME=str(home / ".copilot"),
        PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
        UV_PYTHON_INSTALL_DIR=str(tmp_path / "empty-managed-root"),
        UV_PYTHON_DOWNLOADS="never",
    )
    result = subprocess.run(
        [
            host, "-NoProfile", "-NonInteractive", "-File",
            str(plugin / "scripts" / "installation-context" / "installation-context.ps1"),
            "-Action", "status", "-LegacyRoot", runtime, "-PayloadRoot", str(plugin),
            "-PluginId", "agent-bridge",
        ],
        env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["legacy"]["root"] == runtime


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_prepared_runtime_is_offline_bootstrap_handoff(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path: Path,
    shell: str,
) -> None:
    """A completed installation uses prepared UV without global bootstrap Python."""
    plugin, _, base_env = windows_payload_runtime
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    env = dict(base_env)
    env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    env["PATH"] = os.pathsep.join(
        (base_env["BRIDGE_TEST_UV_TOOLS"], str(system32),
         str(system32 / "WindowsPowerShell" / "v1.0")),
    )
    managed_root = tmp_path / "empty-managed-root"
    managed_root.mkdir()
    env["UV_PYTHON_INSTALL_DIR"] = str(managed_root)
    env["UV_PYTHON_DOWNLOADS"] = "never"
    result = subprocess.run(
        _payload_command(host, plugin, shell),
        input="offline-prepared-handoff",
        env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 17, result.stdout + result.stderr
    assert result.stdout == "offline-prepared-handoff"
    assert not list(managed_root.iterdir())


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
@pytest.mark.parametrize("inherited_selection", [False, True])
def test_cold_context_helper_uses_declared_python(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path: Path,
    shell: str,
    inherited_selection: bool,
) -> None:
    """Cold SDK helpers use the declaration, not inherited Python preferences."""
    plugin, _, base_env = windows_payload_runtime
    managed_root = os.environ.get("BRIDGE_TEST_MANAGED_PYTHON_ROOT")
    if not managed_root:
        pytest.skip("requires a read-only prepared uv Python root")
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    runtime = tmp_path / "absent-runtime"
    env = dict(base_env)
    env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    env.update(
        AGENT_BRIDGE_INSTALL_DIR=str(runtime),
        AGENT_BRIDGE_CONFIG_DIR=str(runtime),
        UV_PYTHON_INSTALL_DIR=managed_root,
        UV_PYTHON_DOWNLOADS="never",
    )
    if inherited_selection:
        env["UV_PYTHON"] = "3.9"
        env["UV_PYTHON_PREFERENCE"] = "only-system"
    wrapper = tmp_path / "cold-sdk.ps1"
    wrapper.write_text(
        """
$keys=@('UV_PYTHON','UV_PYTHON_PREFERENCE','UV_MANAGED_PYTHON','UV_NO_MANAGED_PYTHON',
        'UV_PYTHON_INSTALL_DIR','UV_PYTHON_DOWNLOADS')
$before=@{}
foreach($key in $keys) { $before[$key]=[Environment]::GetEnvironmentVariable($key,'Process') }
& $env:PROBE_SDK -Action status -PayloadRoot $env:PROBE_PAYLOAD `
    -PluginId agent-bridge -LegacyRoot $env:AGENT_BRIDGE_INSTALL_DIR
$code=$LASTEXITCODE
foreach($key in $keys) {
    if ([Environment]::GetEnvironmentVariable($key,'Process') -cne $before[$key]) {
        throw 'bootstrap changed caller Python selection or storage/download policy'
    }
}
exit $code
""",
        encoding="utf-8",
    )
    env["PROBE_SDK"] = str(
        plugin / "scripts" / "installation-context" / "installation-context.ps1",
    )
    env["PROBE_PAYLOAD"] = str(plugin)
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(wrapper)],
        env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["legacy"]["root"] == str(runtime)
    assert not runtime.exists()


@pytest.mark.parametrize("prepared", [False, True])
def test_closed_wheel_publication_validates_offline_handoff(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path: Path,
    prepared: bool,
) -> None:
    """Closed publication requires a persistent, selected runtime before success."""
    plugin, _, base_env = windows_payload_runtime
    host = shutil.which("pwsh")
    if host is None:
        pytest.skip("pwsh is unavailable")
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    start = source.index("function Write-DeployManifest {")
    end = source.index("\nfunction ", start + 1)
    wrapper = plugin / "scripts" / "closed-publication-test.ps1"
    wrapper.write_text(
        source[start:end] + """
$ErrorActionPreference='Stop'
$WheelProfile='fixture-closed-profile'
$NativeWheelSelection=@{}
$InstallDir=$env:AGENT_BRIDGE_INSTALL_DIR
$PluginDir=$env:PROBE_PAYLOAD
$LinkDir=$env:EXPECTED_RUNTIME_SLOT
$SrcVersion=$PackageVersion='1.2.3'
function Get-SourceKind { 'standalone' }
function Get-GitInfo {}
function Write-DeployManifestShared {
    param($Service,$Plugin,$InstallPath,$PluginPath,$VenvPath,$GetSourceKind,$GetGitInfo,
          $SourcePathOverride,$VersionOverride,$AdditionalFields,$PayloadHash)
    [IO.File]::WriteAllText($env:PROBE_PUBLISHED,'published')
}
Write-DeployManifest -PayloadHash ('a' * 64)
""",
        encoding="utf-8",
    )
    env = dict(base_env)
    env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    env["PATH"] = str(system32)
    if not prepared:
        env["AGENT_BRIDGE_INSTALL_DIR"] = str(tmp_path / "absent-runtime")
        env["AGENT_BRIDGE_CONFIG_DIR"] = env["AGENT_BRIDGE_INSTALL_DIR"]
    published = tmp_path / "published"
    env.update(
        PROBE_PAYLOAD=str(plugin), PROBE_PUBLISHED=str(published),
        UV_PYTHON_INSTALL_DIR=str(tmp_path / "absent-managed-python"),
        UV_PYTHON_DOWNLOADS="never",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(wrapper)],
        env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert (result.returncode == 0) is prepared, result.stdout + result.stderr
    assert published.exists() is prepared
    if prepared:
        command = subprocess.run(
            _payload_command(host, plugin, "pwsh"),
            input="closed-wheel-handoff",
            env=env, capture_output=True, text=True, encoding="utf-8",
            timeout=30, **no_window_kwargs(),
        )
        assert command.returncode == 17, command.stdout + command.stderr
        assert command.stdout == "closed-wheel-handoff"


@pytest.mark.parametrize("fail_action,prior_selection", [(False, None), (True, "caller-selection")])
def test_closed_setup_hint_is_scoped_to_the_installation(
    tmp_path: Path, fail_action: bool, prior_selection: str | None,
) -> None:
    """The actual dispatch carries closed inputs into helpers and restores callers."""
    if os.name != "nt":
        pytest.skip("native Windows bootstrap")
    host = shutil.which("pwsh")
    if host is None:
        pytest.skip("pwsh is unavailable")
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    dispatch = tmp_path / "dispatch.ps1"
    dispatch.write_text(
        """
$Action='install'
$ForceTaskRepair=$false
$WheelProfile='fixture-profile'
$WheelPython=$env:PROBE_PYTHON
$WheelDirectory=$PluginDir=$InstallDir=$env:PROBE_ROOT
$driverPython=$null
function Write-InstallerLifecycle { param($Outcome) }
function Assert-SetupHint {
    if ($env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON -cne $env:PROBE_PYTHON) {
        throw 'closed setup lost its admitted Python'
    }
}
function Initialize-SourceRuntime { Assert-SetupHint }
function Initialize-NativeWheelRuntime {
    param($Python,$Profile,$Directory,$Payload,$Plugin,$Root)
    Assert-SetupHint
}
function Invoke-Install {
    Assert-SetupHint
    if ($env:PROBE_FAIL -eq '1') { throw 'fixture action failure' }
}
function Exit-InstallLock {}
"""
        + source[source.index("$script:InstallerLifecycleAction ="):],
        encoding="utf-8",
    )
    (tmp_path / "installer-instance-reconcile.ps1").write_text(
        "function Invoke-InstallerInstanceReconcile { param($HelperRoot,$RuntimeRoot) }\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / "check-dispatch.ps1"
    wrapper.write_text(
        """
$ErrorActionPreference='Stop'
$before=$env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON
$failed=$false
try { & $env:PROBE_DISPATCH } catch { $failed=$true }
if ($failed -ne ($env:PROBE_FAIL -eq '1')) { throw 'unexpected dispatch outcome' }
if ($env:COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON -cne $before) {
    throw 'installer leaked its bootstrap interpreter into the caller'
}
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    if prior_selection is None:
        env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    else:
        env["COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"] = prior_selection
    env.update(
        PROBE_DISPATCH=str(dispatch), PROBE_PYTHON=sys.executable,
        PROBE_ROOT=str(tmp_path), PROBE_FAIL="1" if fail_action else "0",
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(wrapper)],
        env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("shell", ["pwsh", "powershell"])
def test_declaration_transport_honors_short_remaining_deadline(
    windows_payload_runtime: tuple[Path, Path, dict[str, str]],
    tmp_path: Path,
    shell: str,
) -> None:
    """A three-second caller budget reaches execution, never a four-second floor."""
    plugin, _, base_env = windows_payload_runtime
    host = shutil.which(shell)
    if host is None:
        pytest.skip(f"{shell} is unavailable")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    sdk = scripts / "installation-context"
    shutil.copytree(plugin / "scripts" / "installation-context", sdk)
    shutil.copyfile(plugin / "plugin.json", payload / "plugin.json")
    for name in (
        "bootstrap-python-version.txt", "bootstrap-uv.ps1",
        "resolve-bootstrap-python.ps1", "resolve-runtime.ps1", "versioned_runtime_native.ps1",
        "bootstrap-capture.ps1", "bootstrap-capture.exe",
    ):
        shutil.copyfile(plugin / "scripts" / name, scripts / name)
    transport = sdk / "shell-caller-owned.ps1"
    transport.rename(sdk / "original-caller.ps1")
    transport.write_text(
        """
[CmdletBinding()]
param([string]$Worker,[hashtable]$Request,[string]$DiagnosticPrefix,[string]$Python)
& (Join-Path $PSScriptRoot 'original-caller.ps1') @PSBoundParameters -TimeoutSeconds 3
exit $LASTEXITCODE
""",
        encoding="utf-8",
    )
    env = dict(base_env)
    env.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON", None)
    env["COPILOT_PLUGIN_ROOT"] = str(payload)
    result = subprocess.run(
        [
            host, "-NoProfile", "-NonInteractive", "-File",
            str(sdk / "installation-context.ps1"), "-Action", "status",
            "-PayloadRoot", str(payload), "-PluginId", "agent-bridge",
            "-LegacyRoot", env["AGENT_BRIDGE_INSTALL_DIR"],
        ],
        env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=15, **no_window_kwargs(),
    )
    if result.returncode:
        assert (
            "WATCHDOG:" in result.stderr
            or "Bounded native capture exceeded its execution deadline." in result.stderr
        ), result.stdout + result.stderr
    else:
        assert json.loads(result.stdout)["pluginId"] == "agent-bridge"
    assert "Cannot validate argument" not in result.stderr
