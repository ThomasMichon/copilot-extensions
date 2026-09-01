"""Bootstrap checks inspect explicit contexts without activating them."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CONTEXT_TOOL = REPO / "libs" / "installation-context" / "installation_context.py"
PLUGINS = ("agent-machines", "agent-index")
BEHAVIOR_PLUGINS = (
    PLUGINS
    if os.environ.get("INSTALLATION_CONTEXT_EXHAUSTIVE_ADAPTERS") == "1"
    else ("agent-index",)
)
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


def _stamp_context(
    tmp_path: Path,
    plugin: str,
    *,
    receipt_plugin_id: str | None = None,
) -> tuple[Path, Path, str]:
    payload = REPO / "plugins" / plugin
    selected_plugin_id = receipt_plugin_id or plugin
    version = json.loads(
        (payload / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    home = tmp_path / "home"
    home.mkdir()
    arguments = [
        "stamp",
        "--source-json",
        json.dumps({
            "source": "github",
            "repo": "Example-Org/Example-Marketplace.git",
        }),
        "--marketplace-key",
        "example",
        "--plugin-id",
        selected_plugin_id,
        "--payload-root",
        str(payload),
        "--payload-version",
        version,
        "--payload-origin",
        "explicit",
        "--expected-namespace-generation",
        "0",
        "--expected-install-generation",
        "0",
        "--durable-home",
        str(tmp_path / "durable"),
    ]
    if os.name == "nt" and POWERSHELL is not None:
        converted = [arguments[0]]
        for value in arguments[1:]:
            if value.startswith("--"):
                converted.append(
                    "-" + "".join(part.capitalize() for part in value[2:].split("-"))
                )
            else:
                converted.append(value)
        command = [
            str(POWERSHELL),
            "-NoProfile",
            "-File",
            str(CONTEXT_TOOL.with_name("installation-context.ps1")),
            *converted,
        ]
    else:
        command = [sys.executable, str(CONTEXT_TOOL), *arguments]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )
    context = json.loads(result.stdout)
    return home, Path(context["installReceipt"]), version


def _environment(home: Path, context: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "COPILOT_EXTENSIONS_CONTEXT": str(context),
    })
    environment.pop("COPILOT_PLUGIN_ROOT", None)
    return environment


def _write_agent_machines_cell_manifest(
    plugin_root: Path,
    context: Path,
    payload: Path,
    *,
    marketplace_id: str,
    source_version: str,
    runtime_version: str,
    source_path: Path | None = None,
    runtime_path: Path | None = None,
) -> None:
    selected_runtime = runtime_path or plugin_root / "versions" / runtime_version
    interpreter = selected_runtime / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.write_text("runtime\n", encoding="utf-8")
    interpreter.chmod(0o755)
    (plugin_root / "current-version").write_text(
        runtime_version + "\n",
        encoding="utf-8",
    )
    (plugin_root / "deploy-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 4,
                "service": "agent-machines",
                "source": {
                    "kind": "local",
                    "path": str(source_path or payload).replace("\\", "/"),
                    "repo": "copilot-extensions",
                    "plugin": "agent-machines",
                    "version": source_version,
                    "commit": None,
                    "branch": None,
                    "dirty": False,
                },
                "runtime": {
                    "kind": "python",
                    "version": runtime_version,
                    "path": str(selected_runtime).replace("\\", "/"),
                    "interpreter": str(interpreter).replace("\\", "/"),
                    "selectedBy": {
                        "kind": "local",
                        "path": str(payload).replace("\\", "/"),
                        "version": runtime_version,
                    },
                },
                "installation": {
                    "marketplaceId": marketplace_id,
                    "pluginId": "agent-machines",
                    "context": str(context).replace("\\", "/"),
                },
            }
        ),
        encoding="utf-8",
    )


def _activate_context(home: Path, context: Path) -> None:
    assert POWERSHELL is not None
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
            str(POWERSHELL),
            "-NoProfile",
            "-File",
            str(CONTEXT_TOOL.with_name("installation-context.ps1")),
            "activation-cas",
            "-Context",
            str(context),
            "-ExpectedMarketplaceId",
            install["marketplaceId"],
            "-ExpectedPluginId",
            install["pluginId"],
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
            str(home / f".{install['pluginId']}"),
            "-DurableHome",
            str(context.parents[4]),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def _prepare_legacy_current(home: Path, plugin: str) -> None:
    payload = REPO / "plugins" / plugin
    version = json.loads(
        (payload / "plugin.json").read_text(encoding="utf-8")
    )["version"]
    runtime = home / f".{plugin}"
    runtime.mkdir()
    source = {"version": version}
    if plugin == "agent-machines":
        source["path"] = str(payload)
        bin_dir = home / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        for name in ("agent-machines", "agent-machines.cmd"):
            binstub = bin_dir / name
            binstub.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            binstub.chmod(0o755)
    else:
        (runtime / ".venv").mkdir()
    (runtime / "deploy-manifest.json").write_text(
        json.dumps({"schema_version": 3, "source": source}),
        encoding="utf-8",
    )


def _run_shell(plugin: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    if os.name == "nt":
        pytest.skip("Bash runner is unavailable on native Windows")
    return subprocess.run(
        ["bash", str(REPO / "plugins" / plugin / "scripts" / "bootstrap-check.sh")],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def _run_powershell(
    plugin: str, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("PowerShell is unavailable")
    command = [POWERSHELL, "-NoProfile"]
    if os.name == "nt":
        command.extend(["-ExecutionPolicy", "Bypass"])
    command.extend([
        "-File",
        str(REPO / "plugins" / plugin / "scripts" / "bootstrap-check.ps1"),
    ])
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("runner", (_run_shell, _run_powershell))
def test_selected_current_manifest_is_a_read_only_noop(
    tmp_path: Path,
    plugin: str,
    runner,
) -> None:
    home, context, version = _stamp_context(tmp_path, plugin)
    plugin_root = context.parent
    (plugin_root / "deploy-manifest.json").write_text(
        json.dumps({"schema_version": 3, "source": {"version": version}}),
        encoding="utf-8",
    )

    result = runner(plugin, _environment(home, context))

    assert result.returncode == 0
    if plugin == "agent-machines":
        assert "requested installation context is not active" in (
            result.stdout + result.stderr
        )
        assert "without legacy fallback" in result.stdout + result.stderr
    else:
        assert result.stdout == ""
        assert result.stderr == ""


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is unavailable")
def test_agent_machines_active_current_cell_is_a_read_only_noop(
    tmp_path: Path,
) -> None:
    home, context, version = _stamp_context(tmp_path, "agent-machines")
    _activate_context(home, context)
    plugin_root = context.parent
    install = json.loads(context.read_text(encoding="utf-8"))
    _write_agent_machines_cell_manifest(
        plugin_root,
        context,
        REPO / "plugins" / "agent-machines",
        marketplace_id=install["marketplaceId"],
        source_version=version,
        runtime_version="0.1.0-dev1",
    )

    result = _run_powershell(
        "agent-machines",
        _environment(home, context),
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert not (home / ".agent-machines").exists()


def _run_stubbed_active_bootstrap(
    tmp_path: Path,
    runner_name: str,
    *,
    source_version: str,
    runtime_version: str = "0.1.0-dev1",
    source_path: Path | None = None,
    runtime_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    payload = tmp_path / f"payload-{runner_name}"
    shutil.copytree(REPO / "plugins" / "agent-machines", payload)
    home = tmp_path / f"home-{runner_name}"
    home.mkdir()
    plugin_root = tmp_path / f"cell-{runner_name}" / "plugins" / "agent-machines"
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    marketplace_id = "example--0123456789abcdef"
    _write_agent_machines_cell_manifest(
        plugin_root,
        context,
        payload,
        marketplace_id=marketplace_id,
        source_version=source_version,
        runtime_version=runtime_version,
        source_path=source_path,
        runtime_path=runtime_path,
    )
    status = json.dumps(
        {
            "status": "ready",
            "reason": "namespaced-active",
            "actualMode": "namespaced",
            "desiredMode": "namespaced",
            "runtimeRoot": str(plugin_root),
            "context": str(context).replace("\\", "/"),
            "marketplaceId": marketplace_id,
        }
    )
    environment = _environment(home, context)
    if runner_name == "shell":
        if os.name == "nt":
            pytest.skip("Bash runner is unavailable on native Windows")
        resolver = payload / "scripts" / "installation-context" / "installation-context.sh"
        resolver.write_text(
            "#!/usr/bin/env bash\n"
            f"printf '%s\\n' {json.dumps(status)}\n",
            encoding="utf-8",
        )
        resolver.chmod(0o755)
        init = payload / "scripts" / "init.sh"
        init.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        init.chmod(0o755)
        return subprocess.run(
            ["bash", str(payload / "scripts" / "bootstrap-check.sh")],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
    if POWERSHELL is None:
        pytest.skip("PowerShell is unavailable")
    resolver = payload / "scripts" / "installation-context" / "installation-context.ps1"
    resolver.write_text(
        "Write-Output '" + status.replace("'", "''") + "'\n",
        encoding="utf-8",
    )
    (payload / "scripts" / "init.ps1").write_text("exit 0\n", encoding="utf-8")
    return subprocess.run(
        [
            str(POWERSHELL),
            "-NoProfile",
            "-File",
            str(payload / "scripts" / "bootstrap-check.ps1"),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


@pytest.mark.parametrize("runner_name", ("shell", "powershell"))
def test_agent_machines_bootstrap_preserves_explicit_runtime_rollback(
    tmp_path: Path,
    runner_name: str,
) -> None:
    version = json.loads(
        (REPO / "plugins" / "agent-machines" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )["version"]

    result = _run_stubbed_active_bootstrap(
        tmp_path,
        runner_name,
        source_version=version,
    )

    assert result.returncode == 0
    assert "reconciling in background" not in result.stdout + result.stderr


@pytest.mark.parametrize("runner_name", ("shell", "powershell"))
@pytest.mark.parametrize("drift", ("version", "path"))
def test_agent_machines_bootstrap_reconciles_payload_provenance_drift(
    tmp_path: Path,
    runner_name: str,
    drift: str,
) -> None:
    version = json.loads(
        (REPO / "plugins" / "agent-machines" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )["version"]
    source_version = "0.0.0" if drift == "version" else version
    source_path = tmp_path / "prior-payload" if drift == "path" else None

    result = _run_stubbed_active_bootstrap(
        tmp_path,
        runner_name,
        source_version=source_version,
        source_path=source_path,
    )

    assert result.returncode == 0
    assert "reconciling in background" in result.stdout + result.stderr


@pytest.mark.parametrize("runner_name", ("shell", "powershell"))
def test_agent_machines_bootstrap_rejects_invalid_runtime_selection(
    tmp_path: Path,
    runner_name: str,
) -> None:
    version = json.loads(
        (REPO / "plugins" / "agent-machines" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )["version"]

    result = _run_stubbed_active_bootstrap(
        tmp_path,
        runner_name,
        source_version=version,
        runtime_path=tmp_path / "foreign-runtime",
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0
    assert "deploy manifest is invalid" in combined
    assert "without legacy fallback" in combined
    assert "reconciling in background" not in combined


@pytest.mark.parametrize("plugin", BEHAVIOR_PLUGINS)
@pytest.mark.parametrize("runner", (_run_shell, _run_powershell))
def test_selected_missing_manifest_does_not_stamp_legacy_runtime(
    tmp_path: Path,
    plugin: str,
    runner,
) -> None:
    home, context, _version = _stamp_context(tmp_path, plugin)

    result = runner(plugin, _environment(home, context))

    assert result.returncode == 0
    combined = result.stdout + result.stderr
    if plugin == "agent-machines":
        assert "requested installation context is not active" in combined
        assert "without legacy fallback" in combined
    else:
        assert "selected context has no deploy manifest" in combined
        assert "namespaced install remains non-operative" in combined
    assert not (home / f".{plugin}").exists()


@pytest.mark.parametrize("plugin", BEHAVIOR_PLUGINS)
@pytest.mark.parametrize("runner", (_run_shell, _run_powershell))
def test_selected_drift_does_not_run_legacy_installer(
    tmp_path: Path,
    plugin: str,
    runner,
) -> None:
    home, context, _version = _stamp_context(tmp_path, plugin)
    plugin_root = context.parent
    (plugin_root / "deploy-manifest.json").write_text(
        json.dumps({"schema_version": 3, "source": {"version": "0.0.0"}}),
        encoding="utf-8",
    )

    result = runner(plugin, _environment(home, context))

    assert result.returncode == 0
    combined = result.stdout + result.stderr
    if plugin == "agent-machines":
        assert "requested installation context is not active" in combined
        assert "without legacy fallback" in combined
    else:
        assert "context-aware install is not active yet" in combined
    assert not (home / f".{plugin}").exists()


@pytest.mark.parametrize("plugin", BEHAVIOR_PLUGINS)
@pytest.mark.parametrize("runner", (_run_shell, _run_powershell))
def test_invalid_context_does_not_fall_back_to_legacy_runtime(
    tmp_path: Path,
    plugin: str,
    runner,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    invalid = home / ".copilot-extensions" / "invalid.json"
    invalid.parent.mkdir()
    invalid.write_text("{not-json", encoding="utf-8")

    result = runner(plugin, _environment(home, invalid))

    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert (
        "installation context is invalid" in combined
        or "installation status is invalid" in combined
        or "installation governance blocks reconcile" in combined
    )
    assert "without legacy fallback" in combined
    assert not (home / f".{plugin}").exists()


@pytest.mark.parametrize("plugin", BEHAVIOR_PLUGINS)
@pytest.mark.parametrize("runner", (_run_shell, _run_powershell))
@pytest.mark.parametrize("conflicting_key", ("pluginId", "PluginId"))
def test_conflicting_context_plugin_id_fails_closed(
    tmp_path: Path,
    plugin: str,
    runner,
    conflicting_key: str,
) -> None:
    home, context, _version = _stamp_context(tmp_path, plugin)
    receipt = context.read_text(encoding="utf-8")
    receipt = receipt.replace(
        f'"pluginId": "{plugin}"',
        f'"pluginId": "{plugin}", "{conflicting_key}": "other"',
        1,
    )
    assert f'"{conflicting_key}": "other"' in receipt
    context.write_text(receipt, encoding="utf-8")

    result = runner(plugin, _environment(home, context))

    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert (
        "installation context is invalid" in combined
        or "installation governance blocks reconcile" in combined
    )
    assert "without legacy fallback" in combined
    assert not (home / f".{plugin}").exists()


@pytest.mark.parametrize(
    ("context_plugin", "runner_plugin"),
    (("agent-machines", "agent-index"), ("agent-index", "agent-machines")),
)
@pytest.mark.parametrize("runner", (_run_shell, _run_powershell))
def test_context_for_other_plugin_preserves_legacy_reconcile(
    tmp_path: Path,
    context_plugin: str,
    runner_plugin: str,
    runner,
) -> None:
    home, context, _version = _stamp_context(tmp_path, context_plugin)
    _prepare_legacy_current(home, runner_plugin)

    result = runner(runner_plugin, _environment(home, context))

    assert result.returncode == 0
    if runner_plugin == "agent-machines":
        assert "without legacy fallback" in result.stdout + result.stderr
    else:
        assert result.stdout == ""
        assert result.stderr == ""


@pytest.mark.parametrize("plugin", BEHAVIOR_PLUGINS)
@pytest.mark.parametrize("runner", (_run_shell, _run_powershell))
def test_context_plugin_identity_is_case_sensitive(
    tmp_path: Path,
    plugin: str,
    runner,
) -> None:
    receipt_plugin_id = "-".join(
        part.capitalize() for part in plugin.split("-")
    )
    home, context, _version = _stamp_context(
        tmp_path,
        plugin,
        receipt_plugin_id=receipt_plugin_id,
    )
    _prepare_legacy_current(home, plugin)

    result = runner(plugin, _environment(home, context))

    assert result.returncode == 0
    if plugin == "agent-machines":
        assert "without legacy fallback" in result.stdout + result.stderr
    else:
        assert result.stdout == ""
        assert result.stderr == ""


@pytest.mark.parametrize("plugin", BEHAVIOR_PLUGINS)
@pytest.mark.parametrize("runner", (_run_shell, _run_powershell))
def test_context_payload_origin_is_case_sensitive(
    tmp_path: Path,
    plugin: str,
    runner,
) -> None:
    home, context, _version = _stamp_context(tmp_path, plugin)
    receipt = context.read_text(encoding="utf-8")
    receipt = receipt.replace(
        '"origin": "explicit"',
        '"origin": "Explicit"',
        1,
    )
    context.write_text(receipt, encoding="utf-8")

    result = runner(plugin, _environment(home, context))

    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert (
        "installation context is invalid" in combined
        or "installation governance blocks reconcile" in combined
    )
    assert "without legacy fallback" in combined
    assert not (home / f".{plugin}").exists()
