"""Execution identity is distinct from host metadata and SSH transport names."""

import argparse
import json
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import config as cfg
from agent_worktrees.machine_identity import is_local_machine


@pytest.fixture
def topology(tmp_path):
    (tmp_path / "machines.yaml").write_text(
        yaml.safe_dump({
            "machines": {
                "example-host": {
                    "hostname": "generated-host",
                    "alias": "example-transport",
                    "ssh": {"environments": [
                        {"name": "windows", "alias": "arbitrary-windows-ssh"},
                        {"name": "wsl", "alias": "arbitrary-guest-ssh"},
                    ]},
                },
                "example-host-wsl": {"hostname": "generated-host"},
            },
        }),
        encoding="utf-8",
    )
    return tmp_path


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_detection_preserves_native_and_qualifies_guest(topology, monkeypatch, environment):
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "GENERATED-HOST")
    monkeypatch.setattr(cfg, "detect_platform", lambda: environment)
    expected = "example-host-wsl" if environment == "wsl" else "example-transport"
    assert cfg.detect_machine(topology) == expected


@pytest.mark.parametrize("hostname", ["example-host", "example-host-wsl"])
def test_standalone_guest_is_qualified_once(monkeypatch, hostname):
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: hostname)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    assert cfg.detect_machine() == "example-host-wsl"


@pytest.mark.parametrize("source", ["local", "global", "default"])
def test_config_precedence_and_topology_mapping(topology, tmp_path, monkeypatch, source):
    global_path = tmp_path / "global.yaml"
    global_path.write_text(
        "machine: generated-host\n" if source == "global" else "machine: wrong-host\n"
        if source == "local" else "{}\n",
        encoding="utf-8",
    )
    local_path = tmp_path / "local.yaml"
    local = {
        "repo_name": "example",
        "repos": {"example": {"anchor": str(topology)}},
    }
    if source == "local":
        local["machine"] = "generated-host"
    local_path.write_text(yaml.safe_dump(local), encoding="utf-8")
    before = local_path.read_bytes(), global_path.read_bytes()
    monkeypatch.setattr(cfg, "global_config_path", lambda: global_path)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "GENERATED-HOST")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    config = cfg.load_config(local_path, include_control_plane_related_pr=False)
    assert config.machine == "example-host-wsl"
    assert config.platform == "wsl"
    assert (local_path.read_bytes(), global_path.read_bytes()) == before


def test_knowledge_topology_uses_loaded_config_without_recursion(
    topology, tmp_path, monkeypatch,
):
    from agent_worktrees import state_root

    harness = tmp_path / "harness"
    harness.mkdir()
    local = tmp_path / "local.yaml"
    local.write_text(yaml.safe_dump({
        "repo_name": "example",
        "knowledge_repo": "knowledge",
        "repos": {"example": {"anchor": str(harness), "stateless": True}},
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "generated-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")

    def sources(config, *, base_anchor):
        assert config.knowledge_repo == "knowledge"
        assert base_anchor == str(harness)
        return [SimpleNamespace(anchor=str(harness)), SimpleNamespace(anchor=str(topology))]

    monkeypatch.setattr(state_root, "config_source_anchors", sources)
    config = cfg.load_config(local, include_control_plane_related_pr=False)
    assert config.machine == "example-host-wsl"


def test_guest_does_not_treat_host_as_local(topology, monkeypatch):
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "generated-host")
    config = cfg.Config(
        srcroot="", machine="example-host-wsl", platform="wsl",
        repo_name="example",
        repos={"example": cfg.RepoConfig(anchor=str(topology), worktree_root="")},
    )
    assert not is_local_machine("example-host", config)
    assert not is_local_machine("generated-host", config)
    assert is_local_machine("example-host-wsl", config)


def test_missing_guest_keeps_identity_and_host_metadata(tmp_path, monkeypatch, capsys):
    (tmp_path / "machines.yaml").write_text(
        "machines:\n  example-host:\n    hostname: generated-host\n"
        "    description: Shared host metadata\n"
        "    ssh:\n      environments:\n"
        "      - name: wsl\n        alias: independent-transport\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "generated-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    assert cfg.detect_machine(tmp_path) == "example-host-wsl"
    assert "no guest entry" in capsys.readouterr().err
    entries = cfg.load_machines_yaml(tmp_path)
    assert cfg.find_machine_entry(entries, "example-host-wsl") is None
    metadata = cfg.find_machine_metadata(entries, "example-host-wsl")
    assert metadata.description == "Shared host metadata"
    assert metadata.ssh_environments[0].alias == "independent-transport"
    rendered = cfg.render_copilot_instructions(metadata, machine="example-host-wsl")
    assert "Machine: example-host-wsl" in rendered
    assert "Deployment environment: independent-transport" in rendered


def test_get_and_context_share_execution_identity(topology, monkeypatch, capsys):
    from agent_worktrees import __main__ as cli

    config = cfg.Config(
        srcroot="", machine="example-host-wsl", platform="wsl",
        repo_name="example",
        repos={"example": cfg.RepoConfig(anchor=str(topology), worktree_root="")},
    )
    monkeypatch.setattr(cfg, "load_config", lambda **_kwargs: config)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cli, "_infer_worktree_id_from_cwd", lambda _config: None)
    monkeypatch.setattr(cli, "_worktree_path_for_id", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(cli, "_cwd_is_inside_project", lambda _path: False)
    assert cli.cmd_get(argparse.Namespace(key="machine")) == 0
    assert capsys.readouterr().out.strip() == config.machine
    monkeypatch.setattr(cli, "_resolve_active_project", lambda _project: ("example", None))
    assert cli.cmd_machine_context(argparse.Namespace()) == 0
    context = json.loads(capsys.readouterr().out)["additionalContext"]
    assert f"Machine: {config.machine}" in context


def test_ambiguous_topology_fails_closed(tmp_path, monkeypatch):
    (tmp_path / "machines.yaml").write_text(
        "machines:\n  example-a:\n    hostname: shared\n"
        "  example-b:\n    hostname: shared\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "shared")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    with pytest.raises(ValueError, match="ambiguous"):
        cfg.detect_machine(tmp_path)


def test_related_discovery_preserves_configured_identity(topology, monkeypatch):
    from agent_worktrees import related_cli

    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "unrelated-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "load_config", lambda **_kwargs: SimpleNamespace(
        machine="example-host-wsl",
    ))
    assert related_cli._related_current_machine([str(topology)], str(topology)) == (
        "example-host-wsl"
    )


@pytest.mark.parametrize("environment", ["windows", "linux"])
@pytest.mark.parametrize("source", ["local", "global"])
@pytest.mark.parametrize("selected", ["example-transport", "generated-host", "example-host"])
def test_native_configured_identity_is_not_migrated(
    topology, tmp_path, monkeypatch, environment, source, selected,
):
    from agent_worktrees import related_cli, tracking

    local_path = tmp_path / "local.yaml"
    global_path = tmp_path / "global.yaml"
    local = {"repo_name": "example", "repos": {"example": {"anchor": str(topology)}}}
    if source == "local":
        local["machine"] = selected
    local_path.write_text(yaml.safe_dump(local), encoding="utf-8")
    global_path.write_text(
        yaml.safe_dump({"machine": selected if source == "global" else "wrong-host"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "global_config_path", lambda: global_path)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "unrelated-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: environment)
    config = cfg.load_config(local_path, include_control_plane_related_pr=False)
    assert config.machine == selected
    legacy_owner = tracking.parse_claim_ref(
        tracking.format_claim_ref(selected, "example", "legacy-worktree"),
    )
    assert legacy_owner.machine == config.machine
    monkeypatch.setattr(cfg, "load_config", lambda **_kwargs: config)
    assert related_cli._related_current_machine([str(topology)], str(topology)) == selected


@pytest.mark.parametrize("environment", ["windows", "linux"])
def test_native_auto_detection_retains_alias_or_key(topology, monkeypatch, environment):
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "generated-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: environment)
    assert cfg.detect_machine(topology) == "example-transport"


@pytest.mark.parametrize("environment", ["windows", "linux"])
def test_native_unconfigured_identity_retains_hostname(topology, tmp_path, monkeypatch, environment):
    local = tmp_path / "local.yaml"
    local.write_text(yaml.safe_dump({
        "repo_name": "example", "repos": {"example": {"anchor": str(topology)}},
    }), encoding="utf-8")
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "generated-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: environment)
    config = cfg.load_config(local, include_control_plane_related_pr=False)
    assert config.machine == "generated-host"


def test_shared_display_names_are_metadata(topology, monkeypatch):
    document = yaml.safe_load((topology / "machines.yaml").read_text(encoding="utf-8"))
    for entry in document["machines"].values():
        entry["display_name"] = "Example workstation"
    (topology / "machines.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "generated-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    assert cfg.detect_machine(topology) == "example-host-wsl"
    entries = cfg.load_machines_yaml(topology)
    assert cfg.find_machine_entry(entries, "example-host").key == "example-host"
    assert cfg.find_machine_entry(entries, "example-host-wsl").key == "example-host-wsl"
    with pytest.raises(ValueError, match="display label.*multiple machines"):
        cfg.find_machine_entry(entries, "Example workstation")
