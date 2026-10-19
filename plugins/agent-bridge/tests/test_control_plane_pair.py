from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_bridge import agent_registry
from agent_bridge.models import TopologyProfile
from agent_bridge.topology import TopologyLoadError, load_control_plane_project


def _topology(path: Path, *, project: str = "example-harness") -> None:
    path.write_text(
        "control_plane:\n"
        f"  project: {project}\n"
        "  harness: example-harness\n"
        "  knowledge: example-knowledge\n"
        "machines:\n"
        "  host-a:\n"
        "    ssh:\n"
        "      ready: true\n"
        "      environments:\n"
        "        - name: linux\n"
        "          alias: host-a\n"
        "          shell: bash\n",
        encoding="utf-8",
    )


def test_real_derived_machine_target_is_harness_not_knowledge(tmp_path, monkeypatch):
    topology = tmp_path / "machines.yaml"
    _topology(topology)
    monkeypatch.setattr(agent_registry, "load_local_repos", lambda: [])
    monkeypatch.setattr(agent_registry, "discover_local_agents", lambda *a, **kw: {})
    monkeypatch.setattr(agent_registry, "load_elevated_projects", lambda: set())
    resolver = agent_registry.build_resolver(
        SimpleNamespace(topologies={"example": TopologyProfile(machines_yaml=str(topology))})
    )
    assert resolver is not None
    assert resolver.agents["host-a"].project == "example-harness"
    assert all(agent.project != "example-knowledge" for agent in resolver.agents.values())


def test_pair_keeps_the_pre_pair_daemon_reader_compatible(tmp_path):
    topology = tmp_path / "machines.yaml"
    _topology(topology)
    # Frozen pre-pair reader contract: older daemons use only this scalar.
    declaration = yaml.safe_load(topology.read_text(encoding="utf-8"))["control_plane"]
    legacy_target = str(declaration["project"]) if declaration.get("project") else None
    assert legacy_target == load_control_plane_project(topology) == "example-harness"
    assert declaration["knowledge"] == "example-knowledge"


def test_conflicting_projection_cannot_fall_back_to_a_different_target(tmp_path):
    topology = tmp_path / "machines.yaml"
    _topology(topology, project="example-knowledge")
    with pytest.raises(TopologyLoadError, match="must equal"):
        load_control_plane_project(topology)
