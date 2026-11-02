"""Actual guidance readers share local identity without manifest enrollment."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import __main__ as cli
from agent_worktrees import config as cfg, repos, session_context


def _configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, shape: str,
    *, binding: str | None = None,
) -> cfg.Config:
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    entries = {}
    if shape == "unrelated":
        entries = {"peer": {"hostname": "Other-Host", "execution_platform": platform}}
    elif shape == "matching":
        entries = {"custom-space": {
            "hostname": "Observed-Host-wsl", "physical_host": "physical-host",
            "execution_platform": platform, "display_name": "Friendly host",
            "role": "worker",
        }}
    elif shape == "unscoped":
        entries = {"physical-host": {"hostname": "Observed-Host-wsl"}}
    elif shape == "sibling":
        entries = {"sibling-space": {
            "hostname": "Observed-Host-wsl", "physical_host": "physical-host",
            "execution_platform": "windows" if platform == "wsl" else "wsl",
            "role": "foreign-role",
        }}
    if shape != "absent":
        (anchor / "machines.yaml").write_text(
            yaml.safe_dump({"machines": entries}), encoding="utf-8",
        )
    global_path = tmp_path / "global.yaml"
    global_path.write_text(yaml.safe_dump(
        {"machine": binding} if binding is not None else {},
    ), encoding="utf-8")
    project_path = tmp_path / "project.yaml"
    project_path.write_text(yaml.safe_dump({
        "repo_name": "example",
        "repos": {"example": {
            "anchor": str(anchor), "worktree_root": str(tmp_path / "trees"),
        }},
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "global_config_path", lambda: global_path)
    monkeypatch.setattr(repos, "read_registry", lambda: repos.ReposRegistry())
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host-wsl")
    config = cfg.load_config(
        project_path, project="example", include_control_plane_related_pr=False,
    )
    monkeypatch.setattr(cli, "_resolve_active_project", lambda _: ("example", None))
    monkeypatch.setattr(cfg, "set_active_project", lambda _: None)
    monkeypatch.setattr(cfg, "load_config", lambda: config)
    return config


def _facts(config: cfg.Config | None) -> dict:
    rendered = session_context.render_machine_context(config)
    return json.loads(rendered.split("```json\n", 1)[1].split("\n```", 1)[0])


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("shape", ["absent", "empty", "unrelated", "matching", "unscoped", "sibling"])
def test_actual_guidance_uses_manifest_independent_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    platform: str, shape: str,
) -> None:
    config = _configure(tmp_path, monkeypatch, platform, shape)
    setup = capsys.readouterr()
    if shape == "unscoped" and platform == "wsl":
        assert "no guest entry" in setup.err
    else:
        assert not setup.err
    physical = "observed-host-wsl" if shape in ("absent", "empty", "unrelated") else "physical-host"
    execution = "custom-space" if shape == "matching" else (
        f"{physical}-wsl" if platform == "wsl" else physical
    )
    before = (Path(config.default_repo.anchor) / "machines.yaml")
    snapshot = before.read_bytes() if before.exists() else None
    facts = _facts(config)
    assert facts["machine"] == physical
    assert facts["execution_machine"] == execution
    assert facts["hostname"] == "Observed-Host-wsl"
    assert facts["environment"] == facts["platform"] == platform
    assert facts["status"] == "resolved"
    assert "reason" not in facts
    alias = {"matching": "Friendly host", "unscoped": "physical-host"}.get(shape)
    if shape == "unscoped" and platform == "wsl":
        alias = None
    assert facts["alias"] == alias
    assert cli.cmd_machine_context(SimpleNamespace()) == 0
    captured = capsys.readouterr()
    text = json.loads(captured.out)["additionalContext"]
    assert f"Machine: {physical}\n" in text
    assert f"Execution identity: {execution}\n" in text
    assert "Hostname: Observed-Host-wsl\n" in text
    assert f"Environment: {platform}\n" in text
    assert "Project: example" in text
    assert "foreign-role" not in text
    assert not captured.err
    assert (before.read_bytes() if before.exists() else None) == snapshot


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_guidance_retains_actual_configured_binding_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    platform: str,
) -> None:
    binding = "configured-host-wsl" if platform == "wsl" else "configured-host"
    config = _configure(tmp_path, monkeypatch, platform, "empty", binding=binding)
    facts = _facts(config)
    assert facts["machine"] == "configured-host"
    assert facts["execution_machine"] == binding
    assert facts["hostname"] == "Observed-Host-wsl"
    assert cli.cmd_machine_context(SimpleNamespace()) == 0
    text = json.loads(capsys.readouterr().out)["additionalContext"]
    assert "Machine: configured-host\n" in text
    assert f"Execution identity: {binding}\n" in text


@pytest.mark.parametrize("failure", ["malformed", "ambiguous", "platform"])
def test_guidance_refuses_invalid_customization_without_claiming_local_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    failure: str,
) -> None:
    config = _configure(tmp_path, monkeypatch, "wsl", "matching", binding="custom-space")
    source = Path(config.default_repo.anchor) / "machines.yaml"
    if failure == "malformed":
        source.write_text("machines: [\n", encoding="utf-8")
    else:
        entry = {
            "hostname": "Observed-Host-wsl", "physical_host": "physical-host",
            "execution_platform": "windows" if failure == "platform" else "wsl",
        }
        entries = {"custom-space": entry}
        if failure == "ambiguous":
            entries["CUSTOM-SPACE"] = dict(entry)
        source.write_text(yaml.safe_dump({"machines": entries}), encoding="utf-8")
    before = source.read_bytes()
    facts = _facts(config)
    assert facts["status"] == "unresolved"
    assert facts["machine"] is None and facts["execution_machine"] is None
    assert facts["reason"]
    assert cli.cmd_machine_context(SimpleNamespace()) == 0
    captured = capsys.readouterr()
    text = json.loads(captured.out)["additionalContext"]
    assert "Machine: unresolved" in text
    assert "warning:" in captured.err
    assert source.read_bytes() == before
