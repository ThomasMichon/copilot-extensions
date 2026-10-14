"""The Python lifecycle publishes ownership without replacing a foreign owner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from agent_worktrees import _command_binding, _installation_context, installation_cli, installer

pytestmark = pytest.mark.guard


@pytest.fixture
def owned_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    payload = tmp_path / ".copilot/installed-plugins/example/agent-worktrees"
    package = payload / "src/agent_worktrees"
    package.mkdir(parents=True)
    (payload / "plugin.json").write_text(
        '{"name":"agent-worktrees","version":"1.0.0"}', encoding="utf-8"
    )
    runtime = tmp_path / ".agent-worktrees"
    runtime.mkdir()
    (tmp_path / ".copilot/settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}}
        },
    }), encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(payload))
    monkeypatch.setenv("AGENT_WORKTREES_PAYLOAD_ROOT", str(payload))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(installer, "install_dir", lambda: runtime)
    monkeypatch.setattr(installer, "find_package_source", lambda *args: package)
    monkeypatch.setattr(installer.registry_paths, "installation_context", lambda: None)
    original = _installation_context._current_environment

    def current_environment(**arguments: Any) -> Any:
        return original(**{**arguments, "os_profile": tmp_path})

    monkeypatch.setattr(_installation_context, "_current_environment", current_environment)
    return tmp_path, runtime


def test_command_binding_python_publisher_is_catalog_selectable(owned_install: tuple[Path, Path]) -> None:
    home, runtime = owned_install
    installer.write_deploy_manifest(home, "example-host")
    manifest = json.loads((runtime / "deploy-manifest.json").read_text(encoding="utf-8"))
    assert manifest["commandBinding"]["source"]["canonical"] == "github:example/plugins"
    result = _command_binding.resolve_command_binding(
        _installation_context,
        payload_root=home / ".copilot/installed-plugins/example/agent-worktrees",
        plugin_id="agent-worktrees", legacy_root=runtime,
    )
    assert result["status"] == "selected"
    assert result["installation"]["mode"] == "legacy"
    assert not (home / ".copilot-extensions/marketplaces").exists()


def test_command_binding_python_publisher_preserves_foreign_owner(owned_install: tuple[Path, Path]) -> None:
    home, runtime = owned_install
    installer.write_deploy_manifest(home, "example-host")
    manifest = runtime / "deploy-manifest.json"
    before = manifest.read_bytes()
    (home / ".copilot/settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Other/Plugins"}}
        },
    }), encoding="utf-8")
    with pytest.raises(_command_binding.CommandOwnershipConflict, match="transfer"):
        installer.write_deploy_manifest(home, "example-host")
    assert manifest.read_bytes() == before
    assert not manifest.with_suffix(".json.tmp").exists()


def test_command_binding_python_publisher_reports_unavailable_source(
    owned_install: tuple[Path, Path], capsys: pytest.CaptureFixture,
) -> None:
    home, runtime = owned_install
    (home / ".copilot/settings.json").write_text("{}", encoding="utf-8")
    installer.write_deploy_manifest(home, "example-host")
    manifest = json.loads((runtime / "deploy-manifest.json").read_text(encoding="utf-8"))
    assert "commandBinding" not in manifest
    output = capsys.readouterr()
    assert "Command ownership was not published" in output.out + output.err


def test_command_binding_python_cli_reports_refusal_without_traceback(
    owned_install: tuple[Path, Path], capsys: pytest.CaptureFixture,
) -> None:
    home, runtime = owned_install
    installer.write_deploy_manifest(home, "example-host")
    manifest = runtime / "deploy-manifest.json"
    before = manifest.read_bytes()
    (home / ".copilot/settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Other/Plugins"}}
        },
    }), encoding="utf-8")
    assert not installation_cli._write_install_manifest(home, "example-host")
    output = capsys.readouterr()
    assert "Deploy manifest publication refused" in output.out + output.err
    assert "Traceback" not in output.out + output.err
    assert manifest.read_bytes() == before


def test_command_binding_python_install_admission_precedes_runtime_mutation(
    owned_install: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    home, runtime = owned_install
    monkeypatch.setattr(installer, "check_prereqs", lambda: [])
    monkeypatch.setattr(installation_cli.worktree_identity, "_find_repo_dir", lambda: home)
    monkeypatch.setattr(installation_cli.cfg, "project_name", lambda: "example")
    monkeypatch.setattr(installation_cli.cfg, "machines_yaml_path", lambda root: home / "missing-machines.yaml")
    mutations = []

    def deploy(*args: Any) -> int:
        mutations.append("deployed")
        state = json.loads((runtime / "deploy-manifest.json").read_text())
        assert state["commandLifecycle"]["state"] == "pending"
        (runtime / "runtime-slot").write_text("example", encoding="utf-8")
        installer.write_deploy_manifest(home, "example-host")
        return 0

    monkeypatch.setattr(installation_cli, "_install_project", deploy)
    assert installation_cli.cmd_install(argparse.Namespace(machine="example-host")) == 0
    assert mutations == ["deployed"]
    before = {path.name: path.read_bytes() for path in runtime.iterdir() if path.is_file()}
    (home / ".copilot/settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Other/Plugins"}}
        },
    }), encoding="utf-8")
    assert installation_cli.cmd_install(argparse.Namespace(machine="example-host")) == 1
    assert mutations == ["deployed"]
    assert {path.name: path.read_bytes() for path in runtime.iterdir() if path.is_file()} == before
    captured = capsys.readouterr()
    assert "Runtime installation refused" in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err


def test_command_binding_python_install_rejects_invalid_context_at_error_boundary(
    owned_install: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    home, runtime = owned_install
    monkeypatch.setattr(installer, "check_prereqs", lambda: [])
    monkeypatch.setattr(installation_cli.worktree_identity, "_find_repo_dir", lambda: home)
    monkeypatch.setattr(installation_cli.cfg, "project_name", lambda: "example")
    monkeypatch.setattr(installation_cli.cfg, "machines_yaml_path", lambda root: home / "missing-machines.yaml")

    def invalid_context():
        raise _installation_context.InstallationContextError("invalid fixture context")

    monkeypatch.setattr(installer.registry_paths, "installation_context", invalid_context)
    monkeypatch.setattr(installation_cli, "_install_project", lambda *args: pytest.fail("Mutation admitted"))
    before = list(runtime.iterdir())
    assert installation_cli.cmd_install(argparse.Namespace(machine="example-host")) == 1
    captured = capsys.readouterr()
    assert "Runtime installation refused" in captured.out + captured.err
    assert "Traceback" not in captured.out + captured.err
    assert list(runtime.iterdir()) == before
