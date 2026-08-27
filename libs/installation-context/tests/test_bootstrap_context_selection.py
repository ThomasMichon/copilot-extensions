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
    result = subprocess.run(
        [
            sys.executable,
            str(CONTEXT_TOOL),
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
        ],
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
    assert result.stdout == ""
    assert result.stderr == ""
    assert not (home / f".{plugin}").exists()


@pytest.mark.parametrize("plugin", PLUGINS)
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
    assert "selected context has no deploy manifest" in combined
    assert "namespaced install remains non-operative" in combined
    assert not (home / f".{plugin}").exists()


@pytest.mark.parametrize("plugin", PLUGINS)
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
    assert "context-aware install is not active yet" in combined
    assert not (home / f".{plugin}").exists()


@pytest.mark.parametrize("plugin", PLUGINS)
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
    assert "installation context is invalid" in combined
    assert "without legacy fallback" in combined
    assert not (home / f".{plugin}").exists()


@pytest.mark.parametrize("plugin", PLUGINS)
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
    assert "installation context is invalid" in combined
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
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize("plugin", PLUGINS)
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
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize("plugin", PLUGINS)
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
    assert "installation context is invalid" in combined
    assert "without legacy fallback" in combined
    assert not (home / f".{plugin}").exists()
