"""Tests for canonical payload-local command generation."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "generate.py"
REPO = SCRIPT.parents[2]
_spec = importlib.util.spec_from_file_location("payload_invocation_generate", SCRIPT)
generator = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(generator)


def _manifest(tmp_path: Path) -> Path:
    path = tmp_path / "plugin" / "payload-invocation.json"
    path.parent.mkdir()
    scripts = path.parent / "scripts"
    scripts.mkdir()
    (scripts / "install.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (scripts / "install.ps1").write_text("# generated fixture\n", encoding="utf-8")
    (scripts / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY=""\n', encoding="utf-8"
    )
    (scripts / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $null\n", encoding="utf-8"
    )
    path.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.payload-invocation",
                "version": 1,
                "command": "agent-example",
                "module": "agent_example",
                "runtimeRoot": ".agent-example",
                "noSelfProvisionEnv": "AGENT_EXAMPLE_NO_SELFPROVISION",
                "purpose": "Exercise an example runtime",
            }
        ),
        encoding="utf-8",
    )
    return path


def _multi_manifest(tmp_path: Path) -> Path:
    path = _manifest(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    primary = {
        field: data.pop(field) for field in ("command", "module", "purpose")
    }
    data["plugin"] = "agent-example"
    data["commands"] = [
        primary,
        {
            "command": "example-helper",
            "module": "agent_example.helper",
            "purpose": "Exercise an example helper",
        },
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _required_context_manifest(tmp_path: Path) -> Path:
    path = _manifest(tmp_path)
    scripts = path.parent / "scripts"
    (scripts / "invoke-payload-runtime.sh").write_text(
        "#!/usr/bin/env bash\n", encoding="utf-8"
    )
    (scripts / "invoke-payload-runtime.ps1").write_text(
        "# generated fixture\n", encoding="utf-8"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(
        {
            "version": 2,
            "command": "agent-machines",
            "module": "agent_machines",
            "legacyRuntimeRoot": data.pop("runtimeRoot"),
            "installationContext": "required",
            "payloadRootEnv": "AGENT_MACHINES_PAYLOAD_ROOT",
            "payloadDispatcher": {
                "posix": "scripts/invoke-payload-runtime.sh",
                "windows": "scripts/invoke-payload-runtime.ps1",
            },
        }
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_generates_three_payload_local_shims(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    assert generator.process_manifest(manifest, check=False) == []
    generated = generator.expected_files(manifest)
    assert {path.name for path in generated} == {
        "agent-example",
        "agent-example.cmd",
        "agent-example.ps1",
        "emit-command-catalog.ps1",
        "emit-command-catalog.sh",
    }
    for path, expected in generated.items():
        assert path.read_text(encoding="utf-8") == expected
        assert ".local/bin" not in expected
        assert "installed-plugins/*" not in expected
    if os.name != "nt":
        assert (manifest.parent / "bin" / "agent-example").stat().st_mode & 0o100
        assert (
            manifest.parent / "scripts" / "emit-command-catalog.sh"
        ).stat().st_mode & 0o100


def test_payload_root_env_is_opt_in_and_preserves_defaults(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    baseline = generator.expected_files(manifest)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["payloadRootEnv"] = "AGENT_EXAMPLE_PAYLOAD_ROOT"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generated = generator.expected_files(manifest)

    posix_path = manifest.parent / "bin" / "agent-example"
    powershell_path = manifest.parent / "bin" / "agent-example.ps1"
    assert "AGENT_EXAMPLE_PAYLOAD_ROOT" not in baseline[posix_path]
    assert "AGENT_EXAMPLE_PAYLOAD_ROOT" not in baseline[powershell_path]
    assert (
        'export AGENT_EXAMPLE_PAYLOAD_ROOT="$_payload_root"'
        in generated[posix_path]
    )
    assert (
        "$env:AGENT_EXAMPLE_PAYLOAD_ROOT = $_payloadRoot"
        in generated[powershell_path]
    )
    assert (
        "$env:AGENT_EXAMPLE_PAYLOAD_ROOT = $_payloadRoot\n    try {"
        in generated[powershell_path]
    )
    assert "} finally {" in generated[powershell_path]


def test_session_start_bootstrap_defaults_true_and_requires_boolean(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    assert generator.load_manifest(manifest)["sessionStartBootstrap"] is True
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["sessionStartBootstrap"] = False
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert generator.load_manifest(manifest)["sessionStartBootstrap"] is False
    data["sessionStartBootstrap"] = "false"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="sessionStartBootstrap"):
        generator.load_manifest(manifest)


def test_payload_dispatcher_delegates_both_platform_shims(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    scripts = manifest.parent / "scripts"
    (scripts / "runtime-gate.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (scripts / "runtime-gate.ps1").write_text("# gate\n", encoding="utf-8")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["payloadDispatcher"] = {
        "posix": "scripts/runtime-gate.sh",
        "windows": "scripts/runtime-gate.ps1",
    }
    data["payloadRootEnv"] = "AGENT_EXAMPLE_PAYLOAD_ROOT"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    generated = generator.expected_files(manifest)

    posix = generated[manifest.parent / "bin" / "agent-example"]
    powershell = generated[manifest.parent / "bin" / "agent-example.ps1"]
    assert 'exec "$_payload_root/scripts/runtime-gate.sh" "$@"' in posix
    assert 'export AGENT_EXAMPLE_PAYLOAD_ROOT="$_payload_root"' in posix
    assert "$_payloadDispatcher = Join-Path $_payloadRoot 'scripts\\runtime-gate.ps1'" in powershell
    assert "$env:AGENT_EXAMPLE_PAYLOAD_ROOT = $_payloadRoot" in powershell
    assert "& $_payloadDispatcher @args" in powershell
    assert "_resolve_runtime" not in posix
    assert "Resolve-PayloadRuntime" not in powershell


def test_payload_dispatcher_requires_platform_parity(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    gate = manifest.parent / "scripts" / "runtime-gate.sh"
    gate.write_text("#!/bin/sh\n", encoding="utf-8")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["payloadDispatcher"] = {"posix": "scripts/runtime-gate.sh"}
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="must declare both"):
        generator.load_manifest(manifest)


def test_catalog_gate_is_root_relative_and_rendered_on_both_platforms(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    gate = manifest.parent / "scripts" / "catalog_gate.py"
    gate.write_text("raise SystemExit(0)\n", encoding="utf-8")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["catalogGate"] = "scripts/catalog_gate.py"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    generated = generator.expected_files(manifest)

    posix = generated[manifest.parent / "scripts" / "emit-command-catalog.sh"]
    powershell = generated[
        manifest.parent / "scripts" / "emit-command-catalog.ps1"
    ]
    assert 'catalog_gate="$self_root/scripts/catalog_gate.py"' in posix
    assert '"$py" -E -X utf8 "$catalog_gate" --check --cwd "$PWD"' in posix
    assert "$catalogGate = Join-Path $selfRoot 'scripts\\catalog_gate.py'" in powershell
    assert "-E -X utf8 $catalogGate --check" in powershell

    data["catalogGate"] = "../escape.py"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="catalogGate"):
        generator.load_manifest(manifest)


def test_required_installation_context_uses_fixed_dispatchers(tmp_path: Path) -> None:
    manifest = _required_context_manifest(tmp_path)

    data = generator.load_manifest(manifest)
    generated = generator.expected_files(manifest)

    assert data["installationContext"] == "required"
    assert data["runtimeRoot"] == ".agent-example"
    posix = generated[manifest.parent / "bin" / "agent-machines"]
    powershell = generated[manifest.parent / "bin" / "agent-machines.ps1"]
    assert "if ! command -v bash >/dev/null 2>&1; then" in posix
    assert "[agent-machines] required payload dispatcher needs bash." in posix
    assert 'exec bash "$_payload_root/scripts/invoke-payload-runtime.sh" "$@"' in posix
    assert "$env:AGENT_MACHINES_PAYLOAD_ROOT = $_payloadRoot" in powershell
    assert "Resolve-PayloadRuntime" not in powershell


@pytest.mark.parametrize("version", [True, False])
def test_manifest_version_rejects_json_booleans(
    tmp_path: Path,
    version: bool,
) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["version"] = version
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="version 1 or 2"):
        generator.load_manifest(manifest)


@pytest.mark.parametrize("plugin", ["agent-unrelated", "context-handoff"])
def test_required_installation_context_rejects_ineligible_plugin(
    tmp_path: Path,
    plugin: str,
) -> None:
    manifest = _required_context_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["command"] = plugin
    data["plugin"] = plugin
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="runtime-bearing core suite plugin identities|invalid plugin",
    ):
        generator.load_manifest(manifest)


@pytest.mark.parametrize("catalog", [None, "{\n"])
def test_required_installation_context_requires_valid_canonical_roster(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog: str | None,
) -> None:
    manifest = _required_context_manifest(tmp_path)
    repo = tmp_path / "isolated-repo"
    if catalog is not None:
        marketplace = repo / ".github" / "plugin" / "marketplace.json"
        marketplace.parent.mkdir(parents=True)
        marketplace.write_text(catalog, encoding="utf-8")
    monkeypatch.setattr(generator, "REPO", repo)

    with pytest.raises(ValueError, match="cannot load canonical marketplace roster"):
        generator.load_manifest(manifest)


def test_v1_rejects_installation_context_fields_without_changing_defaults(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    baseline = generator.expected_files(manifest)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["installationContext"] = "required"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="version 1 cannot declare"):
        generator.load_manifest(manifest)

    data.pop("installationContext")
    manifest.write_text(json.dumps(data), encoding="utf-8")
    assert generator.expected_files(manifest) == baseline


def _copied_agent_machines_payload(tmp_path: Path) -> Path:
    payload = tmp_path / "agent-machines"
    shutil.copytree(
        REPO / "plugins" / "agent-machines",
        payload,
        ignore=shutil.ignore_patterns(
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
            "*.egg-info",
        ),
    )
    return payload


def _directory_marketplace_agent_machines_payload(tmp_path: Path) -> Path:
    marketplace = tmp_path / "marketplace"
    payload = marketplace / "plugins" / "agent-machines"
    shutil.copytree(
        REPO / "plugins" / "agent-machines",
        payload,
        ignore=shutil.ignore_patterns(
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
            "*.egg-info",
        ),
    )
    write_json = {
        "name": "example",
        "owner": {"name": "Example"},
        "metadata": {"version": "1.0.0"},
        "plugins": [
            {
                "name": "agent-machines",
                "description": "Synthetic directory marketplace fixture",
                "version": json.loads(
                    (payload / "plugin.json").read_text(encoding="utf-8")
                )["version"],
                "source": "plugins/agent-machines",
            }
        ],
    }
    catalog = marketplace / ".github" / "plugin" / "marketplace.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps(write_json), encoding="utf-8")
    return payload


def _stamp_agent_machines_context(
    tmp_path: Path,
    payload: Path,
    pwsh: str,
    *,
    explicit_source: bool = True,
    durable_home: Path | None = None,
) -> Path:
    version = json.loads(
        (payload / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    arguments = [
        pwsh,
        "-NoProfile",
        "-File",
        str(REPO / "libs" / "installation-context" / "installation-context.ps1"),
        "stamp",
    ]
    if explicit_source:
        arguments.extend(
            [
                "-SourceJson",
                '{"source":"github","repo":"example-org/example-marketplace"}',
                "-MarketplaceKey",
                "example",
            ]
        )
    arguments.extend(
        [
            "-PluginId",
            "agent-machines",
            "-PayloadRoot",
            str(payload),
            "-PayloadVersion",
            version,
            "-PayloadOrigin",
            "explicit",
            "-ExpectedNamespaceGeneration",
            "0",
            "-ExpectedInstallGeneration",
            "0",
            "-DurableHome",
            str(durable_home or tmp_path / "durable"),
        ]
    )
    result = subprocess.run(
        arguments,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(json.loads(result.stdout)["installReceipt"])


def _activate_agent_machines_context(
    tmp_path: Path,
    home: Path,
    context: Path,
    pwsh: str,
    *,
    durable_home: Path | None = None,
) -> Path:
    install = json.loads(context.read_text(encoding="utf-8"))
    namespace = json.loads(
        Path(install["namespaceReceipt"]).read_text(encoding="utf-8")
    )
    policy = home / ".copilot-extensions" / "installation-mode.json"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.installation-mode",
                "version": 1,
                "installationMode": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update({"HOME": str(home), "USERPROFILE": str(home)})
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(REPO / "libs" / "installation-context" / "installation-context.ps1"),
            "activation-cas",
            "-Context",
            str(context),
            "-ExpectedMarketplaceId",
            install["marketplaceId"],
            "-ExpectedPluginId",
            "agent-machines",
            "-ExpectedNamespaceGeneration",
            str(namespace["generation"]),
            "-ExpectedInstallGeneration",
            str(install["generation"]),
            "-ExpectedActivationGeneration",
            "0",
            "-ActivationMode",
            "namespaced",
            "-ActivationState",
            "active",
            "-LegacyDisposition",
            "absent",
            "-LegacyProbeJson",
            '{"declared":true,"result":"absent","checkedAt":"2026-01-01T00:00:00Z"}',
            "-LegacyRoot",
            str(home / ".agent-machines"),
            "-DurableHome",
            str(durable_home or tmp_path / "durable"),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    json.loads(result.stdout)
    return context.parent


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="canonical Windows profile semantics require native Windows",
)
@pytest.mark.parametrize("policy_state", ["absent", "explicit-false"])
def test_agent_machines_required_context_preserves_absent_policy_legacy_use(
    tmp_path: Path,
    policy_state: str,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_machines_payload(tmp_path)
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "$AgentRtPy = $env:TEST_PYTHON\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    if policy_state == "explicit-false":
        policy = home / ".copilot-extensions" / "installation-mode.json"
        policy.parent.mkdir(parents=True)
        policy.write_text(
            json.dumps(
                {
                    "schema": "copilot-extensions.installation-mode",
                    "version": 1,
                    "installationMode": {"enabled": False},
                }
            ),
            encoding="utf-8",
        )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "PYTHONPATH": os.pathsep.join(
                [
                    str(payload / "src"),
                    str(payload / "libs" / "plugin-resolve" / "src"),
                    str(payload / "libs" / "agent-procutil" / "src"),
                ]
            ),
            "TEST_PYTHON": sys.executable,
        }
    )
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "agent-machines.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads((payload / "plugin.json").read_text())["version"] in result.stdout
    assert not (home / ".agent-machines").exists()
    if policy_state == "absent":
        assert not (home / ".copilot-extensions").exists()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_agent_machines_requested_context_never_falls_back_to_legacy(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_machines_payload(tmp_path)
    context = _stamp_agent_machines_context(tmp_path, payload, pwsh)
    home = tmp_path / "home"
    home.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "AGENT_MACHINES_NO_SELFPROVISION": "1",
        }
    )

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "agent-machines.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 126
    assert "requested installation context is not active" in result.stderr
    assert not (home / ".agent-machines").exists()


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_agent_machines_active_context_selects_only_its_cell_root(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_machines_payload(tmp_path)
    context = _stamp_agent_machines_context(tmp_path, payload, pwsh)
    home = tmp_path / "home"
    home.mkdir()
    plugin_root = _activate_agent_machines_context(tmp_path, home, context, pwsh)
    root_record = tmp_path / "selected-root.txt"
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "[IO.File]::WriteAllText($env:TEST_ROOT_RECORD, $env:AGENT_RT_ROOT)\n"
        "$AgentRtPy = $env:TEST_PYTHON\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "PYTHONPATH": os.pathsep.join(
                [
                    str(payload / "src"),
                    str(payload / "libs" / "plugin-resolve" / "src"),
                    str(payload / "libs" / "agent-procutil" / "src"),
                ]
            ),
            "TEST_PYTHON": sys.executable,
            "TEST_ROOT_RECORD": str(root_record),
        }
    )

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "agent-machines.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert root_record.read_text(encoding="utf-8") == str(plugin_root)
    assert not (home / ".agent-machines").exists()


@pytest.mark.skipif(
    os.name != "nt" or shutil.which("pwsh") is None,
    reason="canonical Windows profile semantics require native Windows",
)
def test_agent_machines_blocked_context_states_never_run_legacy(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _copied_agent_machines_payload(tmp_path)
    context = _stamp_agent_machines_context(tmp_path, payload, pwsh)
    home = tmp_path / "home"
    home.mkdir()
    plugin_root = _activate_agent_machines_context(tmp_path, home, context, pwsh)
    root_record = tmp_path / "selected-root.txt"
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "[IO.File]::WriteAllText($env:TEST_ROOT_RECORD, $env:AGENT_RT_ROOT)\n"
        "$AgentRtPy = $env:TEST_PYTHON\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "COPILOT_EXTENSIONS_CONTEXT": str(context),
            "PYTHONPATH": os.pathsep.join(
                [
                    str(payload / "src"),
                    str(payload / "libs" / "plugin-resolve" / "src"),
                    str(payload / "libs" / "agent-procutil" / "src"),
                ]
            ),
            "TEST_PYTHON": sys.executable,
            "TEST_ROOT_RECORD": str(root_record),
        }
    )

    def invoke_blocked(label: str) -> None:
        root_record.unlink(missing_ok=True)
        result = subprocess.run(
            [
                pwsh,
                "-NoProfile",
                "-File",
                str(payload / "bin" / "agent-machines.ps1"),
                "--version",
            ],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 126, f"{label}: {result.stderr}"
        assert "blocks invocation" in result.stderr
        assert not root_record.exists(), label

    policy = home / ".copilot-extensions" / "installation-mode.json"
    original_policy = policy.read_bytes()
    policy.write_text("{\n", encoding="utf-8")
    invoke_blocked("malformed policy")
    policy.write_bytes(original_policy)

    maintenance = plugin_root / "maintenance"
    maintenance.write_text("maintenance\n", encoding="utf-8")
    invoke_blocked("maintenance")
    maintenance.unlink()

    activation = plugin_root / "installation-activation.json"
    original_activation = json.loads(activation.read_text(encoding="utf-8"))
    foreign_activation = dict(original_activation)
    foreign_activation["environment"] = dict(original_activation["environment"])
    foreign_activation["environment"]["platform"] = "posix"
    activation.write_text(json.dumps(foreign_activation), encoding="utf-8")
    invoke_blocked("foreign activation")
    activation.unlink()

    legacy = home / ".agent-machines"
    legacy.mkdir()
    (legacy / ".installation-ownership.json").write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.legacy-installation-ownership",
                "version": 1,
                "marketplaceId": original_activation["marketplaceId"],
                "pluginId": "agent-machines",
                "activation": {
                    "path": str(plugin_root / "missing-activation.json"),
                    "generation": 1,
                },
                "environment": original_activation["environment"],
                "transferredAt": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    invoke_blocked("orphaned transfer")


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_agent_machines_removed_policy_keeps_active_cell_authoritative(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    payload = _directory_marketplace_agent_machines_payload(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    durable_home = home / ".copilot-extensions"
    context = _stamp_agent_machines_context(
        tmp_path,
        payload,
        pwsh,
        explicit_source=False,
        durable_home=durable_home,
    )
    plugin_root = _activate_agent_machines_context(
        tmp_path,
        home,
        context,
        pwsh,
        durable_home=durable_home,
    )
    (home / ".copilot-extensions" / "installation-mode.json").unlink()
    root_record = tmp_path / "selected-root.txt"
    (payload / "scripts" / "resolve-runtime.ps1").write_text(
        "[IO.File]::WriteAllText($env:TEST_ROOT_RECORD, $env:AGENT_RT_ROOT)\n"
        "$AgentRtPy = $env:TEST_PYTHON\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(payload),
            "PYTHONPATH": os.pathsep.join(
                [
                    str(payload / "src"),
                    str(payload / "libs" / "plugin-resolve" / "src"),
                    str(payload / "libs" / "agent-procutil" / "src"),
                ]
            ),
            "TEST_PYTHON": sys.executable,
            "TEST_ROOT_RECORD": str(root_record),
        }
    )
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)

    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(payload / "bin" / "agent-machines.ps1"),
            "--version",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert root_record.read_text(encoding="utf-8") == str(plugin_root)
    assert not (home / ".agent-machines").exists()


def test_generates_multiple_commands_and_one_catalog(tmp_path: Path) -> None:
    manifest = _multi_manifest(tmp_path)
    assert generator.process_manifest(manifest, check=False) == []
    generated = generator.expected_files(manifest)
    assert {path.name for path in generated} == {
        "agent-example",
        "agent-example.cmd",
        "agent-example.ps1",
        "example-helper",
        "example-helper.cmd",
        "example-helper.ps1",
        "emit-command-catalog.ps1",
        "emit-command-catalog.sh",
    }
    helper = generated[manifest.parent / "bin" / "example-helper"]
    assert '_command="example-helper"' in helper
    assert '_module="agent_example.helper"' in helper
    catalog = generated[manifest.parent / "scripts" / "emit-command-catalog.sh"]
    assert '"id":"agent-example"' in catalog
    assert '"id":"example-helper"' in catalog
    assert '"plugin": "agent-example"' in catalog


def test_commands_schema_preserves_plugin_identity_with_one_command(
    tmp_path: Path,
) -> None:
    manifest = _multi_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["commands"] = [
        {
            "command": "example-helper",
            "module": "agent_example.helper",
            "purpose": "Exercise an example helper",
        }
    ]
    manifest.write_text(json.dumps(data), encoding="utf-8")

    generated = generator.expected_files(manifest)
    catalog = generated[manifest.parent / "scripts" / "emit-command-catalog.sh"]
    assert '"plugin": "agent-example"' in catalog
    assert '"id":"example-helper"' in catalog


def test_manifest_can_select_cmd_for_windows_catalog(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["windowsCatalogShim"] = "cmd"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    generated = generator.expected_files(manifest)
    catalog = generated[manifest.parent / "scripts" / "emit-command-catalog.ps1"]
    cmd = generated[manifest.parent / "bin" / "agent-example.cmd"]
    assert r"bin\agent-example.cmd" in catalog
    assert "shell = 'cmd'" in catalog
    assert r'where.exe" pwsh 2^>nul' in cmd
    assert 'set "_PSHOST=%%I"' in cmd
    assert r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" in cmd
    assert '"%_PSHOST%" -NoProfile' in cmd


def test_manifest_rejects_unknown_windows_catalog_shim(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["windowsCatalogShim"] = "exe"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid windowsCatalogShim"):
        generator.load_manifest(manifest)


def test_manifest_can_provision_directly_from_self_staging_installer(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["provisionMode"] = "direct"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    generated = generator.expected_files(manifest)
    posix = generated[manifest.parent / "bin" / "agent-example"]
    powershell = generated[manifest.parent / "bin" / "agent-example.ps1"]
    assert 'bash "$_installer" provision' in posix
    assert "payload-dir" not in posix
    assert "$_installer provision" in powershell
    assert "payload-dir" not in powershell


def test_manifest_rejects_unknown_provision_mode(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["provisionMode"] = "ambient"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid provisionMode"):
        generator.load_manifest(manifest)


@pytest.mark.skipif(os.name == "nt", reason="POSIX catalog execution test")
def test_posix_catalog_emits_every_command_id(tmp_path: Path) -> None:
    manifest = _multi_manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    (manifest.parent / "plugin.json").write_text(
        '{"name":"agent-example"}\n', encoding="utf-8"
    )
    env = os.environ.copy()
    env["COPILOT_PLUGIN_ROOT"] = str(manifest.parent)
    result = subprocess.run(
        [str(manifest.parent / "scripts" / "emit-command-catalog.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    outer = json.loads(result.stdout)
    match = re.search(r"```json\n(.*?)\n```", outer["additionalContext"], re.S)
    assert match
    catalog = json.loads(match.group(1))
    assert catalog["plugin"] == "agent-example"
    assert [command["id"] for command in catalog["commands"]] == [
        "agent-example",
        "example-helper",
    ]
    assert all(command["availability"] == "ready" for command in catalog["commands"])


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_powershell_catalog_emits_every_command_id(tmp_path: Path) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    manifest = _multi_manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    (manifest.parent / "plugin.json").write_text(
        '{"name":"agent-example"}\n', encoding="utf-8"
    )
    env = os.environ.copy()
    env.update(
        {
            "COPILOT_PLUGIN_ROOT": str(manifest.parent),
            "USERPROFILE": str(tmp_path / "home"),
        }
    )
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-File",
            str(manifest.parent / "scripts" / "emit-command-catalog.ps1"),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    outer = json.loads(result.stdout)
    match = re.search(r"```json\n(.*?)\n```", outer["additionalContext"], re.S)
    assert match
    catalog = json.loads(match.group(1))
    assert catalog["plugin"] == "agent-example"
    assert [command["id"] for command in catalog["commands"]] == [
        "agent-example",
        "example-helper",
    ]
    assert all(command["argv"][0].endswith(".ps1") for command in catalog["commands"])
    assert all(command["availability"] == "ready" for command in catalog["commands"])


def test_check_detects_drift(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    assert generator.process_manifest(manifest, check=True) == []
    (manifest.parent / "bin" / "agent-example.ps1").write_text(
        "stale\n", encoding="utf-8"
    )
    assert generator.process_manifest(manifest, check=True)


def test_manifest_validation_fails_closed(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["command"] = "../agent-example"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    try:
        generator.load_manifest(manifest)
    except ValueError as error:
        assert "invalid command" in str(error)
    else:
        raise AssertionError("invalid command was accepted")

    legacy_root = tmp_path / "legacy-plugin"
    legacy_root.mkdir()
    manifest = _manifest(legacy_root)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["plugin"] = "agent-different"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="legacy plugin must equal command"):
        generator.load_manifest(manifest)


def test_multi_command_manifest_rejects_ambiguous_or_duplicate_commands(
    tmp_path: Path,
) -> None:
    manifest = _multi_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["command"] = "agent-example"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be combined"):
        generator.load_manifest(manifest)

    data.pop("command")
    data["commands"].append(dict(data["commands"][0]))
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate command"):
        generator.load_manifest(manifest)


@pytest.mark.parametrize(
    "command",
    ["install", "resolve-runtime", "emit-command-catalog"],
)
def test_manifest_rejects_generated_script_collisions(
    tmp_path: Path,
    command: str,
) -> None:
    manifest = _multi_manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputDir"] = "scripts"
    data["commands"] = [
        {
            "command": command,
            "module": "agent_example.helper",
            "purpose": "Exercise a colliding command",
        }
    ]
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="generated path collision"):
        generator.expected_files(manifest)


def test_manifest_selects_and_requires_installer_entrypoint(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["installer"] = "init"
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError, match="installer not found"):
        generator.expected_files(manifest)

    scripts = manifest.parent / "scripts"
    (scripts / "init.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (scripts / "init.ps1").write_text("# generated fixture\n", encoding="utf-8")
    generated = generator.expected_files(manifest)
    posix = generated[manifest.parent / "bin" / "agent-example"]
    powershell = generated[manifest.parent / "bin" / "agent-example.ps1"]
    assert 'scripts/init.sh"' in posix
    assert "'scripts\\init.ps1'" in powershell


@pytest.mark.parametrize("suffix", [".sh", ".ps1"])
def test_manifest_requires_runtime_resolver_pair(tmp_path: Path, suffix: str) -> None:
    manifest = _manifest(tmp_path)
    (manifest.parent / "scripts" / f"resolve-runtime{suffix}").unlink()

    with pytest.raises(ValueError, match="runtime resolver not found"):
        generator.expected_files(manifest)


def test_manifest_supports_nested_payload_output(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputDir"] = "bin/payload"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generated = generator.expected_files(manifest)
    assert manifest.parent / "bin" / "payload" / "agent-example" in generated
    catalog = generated[manifest.parent / "scripts" / "emit-command-catalog.sh"]
    assert 'command_path="$self_root/bin/payload/agent-example"' in catalog


@pytest.mark.parametrize(
    "plugin",
    [
        "agent-bridge",
        "agent-codespaces",
        "agent-containers",
        "agent-dispatch",
        "agent-logger",
        "agent-machines",
        "agent-mcp",
        "agent-ssh",
        "agent-vault",
        "agent-worktrees",
    ],
)
def test_payload_catalog_adopters_publish_payload_catalogs(plugin: str) -> None:
    plugin_root = REPO / "plugins" / plugin
    manifest = plugin_root / "payload-invocation.json"
    data = generator.load_manifest(manifest)
    assert data["plugin"] == plugin
    command_ids = {
        command["command"] for command in data["commands"]
    }
    assert plugin in command_ids

    generated = generator.expected_files(manifest)
    assert generator.process_manifest(manifest, check=True) == []
    output_dir = plugin_root / str(data["outputDir"])
    for command_id in command_ids:
        assert output_dir / command_id in generated

    hooks = json.loads((plugin_root / "hooks.json").read_text(encoding="utf-8"))
    session_hooks = hooks["hooks"]["sessionStart"]
    for shell in ("bash", "powershell"):
        catalog_hooks = [
            hook for hook in session_hooks if "emit-command-catalog" in hook[shell]
        ]
        assert len(catalog_hooks) == 1
        assert "COPILOT_PLUGIN_ROOT" in catalog_hooks[0][shell]


def test_skill_catalog_references_name_payload_adopters() -> None:
    reference = re.compile(
        r'<(agent-[a-z0-9-]+) catalog(?: "([a-z][a-z0-9-]*)")? argv\[0\]>'
    )
    references: dict[str, dict[str, list[Path]]] = {}
    capability_paths = [
        *(REPO / "plugins").glob("*/skills/**/*.md"),
        *(REPO / "plugins").glob("*/agents/**/*.md"),
    ]
    for skill in capability_paths:
        for plugin, command in reference.findall(skill.read_text(encoding="utf-8")):
            command_id = command or plugin
            references.setdefault(plugin, {}).setdefault(command_id, []).append(
                skill.relative_to(REPO)
            )

    missing = {
        plugin: paths
        for plugin, paths in references.items()
        if not (REPO / "plugins" / plugin / "payload-invocation.json").is_file()
    }
    assert missing == {}
    missing_commands = {}
    for plugin, command_paths in references.items():
        manifest = generator.load_manifest(
            REPO / "plugins" / plugin / "payload-invocation.json"
        )
        command_ids = {
            command["command"] for command in manifest["commands"]
        }
        unknown = {
            command: paths
            for command, paths in command_paths.items()
            if command not in command_ids
        }
        if unknown:
            missing_commands[plugin] = unknown
    assert missing_commands == {}
    missing_hooks = {}
    for plugin, paths in references.items():
        hooks_path = REPO / "plugins" / plugin / "hooks.json"
        if not hooks_path.is_file():
            missing_hooks[plugin] = paths
            continue
        hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
        session_hooks = hooks.get("hooks", {}).get("sessionStart", [])
        if not any(
            "emit-command-catalog" in hook.get("bash", "")
            and "emit-command-catalog" in hook.get("powershell", "")
            for hook in session_hooks
        ):
            missing_hooks[plugin] = paths
    assert missing_hooks == {}


@pytest.mark.skipif(os.name == "nt", reason="POSIX catalog test")
def test_posix_catalog_fails_open_when_python_fails(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    python = fake_bin / "python3"
    python.write_text("#!/bin/sh\nexit 42\n", encoding="utf-8")
    python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["COPILOT_PLUGIN_ROOT"] = str(manifest.parent)
    result = subprocess.run(
        [str(manifest.parent / "scripts" / "emit-command-catalog.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "{}"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_posix_shim_preserves_args_exit_and_project_cwd(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputDir"] = "bin/payload"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY="$AGENT_RT_ROOT/versions/test/bin/python"\n',
        encoding="utf-8",
    )

    home = tmp_path / "home"
    fake_python = home / ".agent-example" / "versions" / "test" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$PWD|$*"\nexit 23\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    project = tmp_path / "project"
    project.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "COPILOT_PROJECT_DIR": str(project),
        }
    )
    result = subprocess.run(
        [str(plugin / "bin" / "payload" / "agent-example"), "search", "two words"],
        cwd=plugin,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 23
    assert result.stdout.strip() == f"{project}|-m agent_example search two words"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_multi_command_shim_dispatches_its_own_module(tmp_path: Path) -> None:
    manifest = _multi_manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    (scripts / "resolve-runtime.sh").write_text(
        'AGENT_RT_PY="$AGENT_RT_ROOT/versions/test/bin/python"\n',
        encoding="utf-8",
    )
    home = tmp_path / "home"
    fake_python = home / ".agent-example" / "versions" / "test" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*"\nexit 0\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
        }
    )
    result = subprocess.run(
        [str(plugin / "bin" / "example-helper"), "two words"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "-m agent_example.helper two words"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_posix_shim_rejects_conflicting_payload_context(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    env = os.environ.copy()
    env.update({"HOME": str(tmp_path / "home"), "COPILOT_PLUGIN_ROOT": str(other)})
    result = subprocess.run(
        [str(plugin / "bin" / "agent-example"), "status"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 126
    assert "payload context mismatch" in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX shim test")
def test_first_use_provision_is_serialized(tmp_path: Path) -> None:
    manifest = _manifest(tmp_path)
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "resolve-runtime.sh").write_text(
        'AW_PY=""\n'
        'p="$AGENT_RT_ROOT/versions/test/bin/python"\n'
        '[ -x "$p" ] && AW_PY="$p"\n'
        'AGENT_RT_PY="$AW_PY"\n'
        "true\n",
        encoding="utf-8",
    )
    installer = scripts / "install.sh"
    installer.write_text(
        '#!/bin/bash\nset -eu\nroot="$HOME/.agent-example"\n'
        'plugin="$(cd "$(dirname "$0")/.." && pwd)"\n'
        'case "$1" in\n'
        '  stamp) mkdir -p "$root"; printf "%s\\n" "$plugin" > "$root/payload-dir" ;;\n'
        '  provision)\n'
        '    printf "provision\\n" >> "$root/provision-count"\n'
        '    sleep 0.5\n'
        '    mkdir -p "$root/versions/test/bin"\n'
        '    printf "%s\\n" "#!/bin/sh" "exit 0" > "$root/versions/test/bin/python"\n'
        '    chmod +x "$root/versions/test/bin/python" ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    installer.chmod(0o755)

    home = tmp_path / "home"
    home.mkdir()
    shadow_bin = tmp_path / "shadow-bin"
    shadow_bin.mkdir()
    shadow_marker = tmp_path / "shadow-called"
    shadow = shadow_bin / "agent-example"
    shadow.write_text(
        f'#!/bin/sh\nprintf called > "{shadow_marker}"\nexit 99\n',
        encoding="utf-8",
    )
    shadow.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "COPILOT_EXT_NO_FLOCK": "1",
            "PATH": f"{shadow_bin}{os.pathsep}{env['PATH']}",
        }
    )
    lock = home / ".agent-example" / ".provision.lock.pid"
    lock.parent.mkdir(parents=True)
    lock.symlink_to("999999999")
    command = [str(plugin / "bin" / "agent-example"), "status"]
    first = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    second = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    _first_out, first_err = first.communicate(timeout=10)
    _second_out, second_err = second.communicate(timeout=10)
    assert first.returncode == 0, first_err
    assert second.returncode == 0, second_err
    count = (home / ".agent-example" / "provision-count").read_text(
        encoding="utf-8"
    )
    assert count.splitlines() == ["provision"]
    assert not shadow_marker.exists()


def test_agent_machines_dispatchers_share_the_legacy_installer_lock() -> None:
    scripts = REPO / "plugins" / "agent-machines" / "scripts"
    posix = (scripts / "invoke-payload-runtime.sh").read_text(encoding="utf-8")
    powershell = (scripts / "invoke-payload-runtime.ps1").read_text(encoding="utf-8")

    assert '$RUNTIME_ROOT/.provision.lock"' in posix
    assert '$RUNTIME_ROOT/.provision.lock.pid"' in posix
    assert "Join-Path $runtimeRoot '.provision.lock'" in powershell
    assert ".payload-provision.lock" not in posix
    assert ".payload-provision.lock" not in powershell


def test_windows_templates_preserve_context_and_release_payload_cwd(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)
    generated = generator.expected_files(manifest)
    powershell = next(
        content for path, content in generated.items() if path.suffix == ".ps1"
    )
    cmd = next(
        content for path, content in generated.items() if path.suffix == ".cmd"
    )
    assert "[IO.Directory]::SetCurrentDirectory($_outside)" in powershell
    assert "StartsWith($_payloadPrefix" in powershell
    assert "[IO.FileShare]::None" in powershell
    assert "if not defined COPILOT_PLUGIN_ROOT" in cmd


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh is not installed")
def test_powershell_shim_preserves_sibling_cwd_and_leaves_payload(
    tmp_path: Path,
) -> None:
    pwsh = shutil.which("pwsh")
    assert pwsh
    manifest = _manifest(tmp_path)
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["outputDir"] = "bin/payload"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    generator.process_manifest(manifest, check=False)
    plugin = manifest.parent
    (plugin / "plugin.json").write_text('{"name":"agent-example"}\n', encoding="utf-8")
    scripts = plugin / "scripts"
    scripts.mkdir(exist_ok=True)
    python_literal = str(Path(sys.executable)).replace("'", "''")
    (scripts / "resolve-runtime.ps1").write_text(
        f"$AgentRtPy = '{python_literal}'\n",
        encoding="utf-8",
    )
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "agent_example.py").write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "print(f\"{Path.cwd()}|{' '.join(sys.argv[1:])}\")\n",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    project = tmp_path / "project"
    sibling = tmp_path / "plugin-backup"
    project.mkdir()
    sibling.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(plugin),
            "COPILOT_PROJECT_DIR": str(project),
            "PYTHONPATH": str(module_dir),
        }
    )
    command = [
        pwsh,
        "-NoProfile",
        "-File",
        str(plugin / "bin" / "payload" / "agent-example.ps1"),
        "status",
    ]
    sibling_result = subprocess.run(
        command, cwd=sibling, env=env, capture_output=True, text=True, check=True
    )
    assert sibling_result.stdout.strip() == (
        f"{sibling}|status"
    )
    payload_result = subprocess.run(
        command, cwd=plugin, env=env, capture_output=True, text=True, check=True
    )
    assert payload_result.stdout.strip() == (
        f"{project}|status"
    )
