"""Tests for agent-ssh mesh-status (mesh.py).

Sample identifiers are neutral placeholders per the repo's identifier-neutrality
guidance for public artifacts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_ssh import mesh as mesh_mod

SAMPLE = """
control_plane:
  project: example-mesh

machines:
  host-a:
    display_name: host-a
    environment: Linux x64
    role: primary
    ssh:
      ready: true
      environments:
        - name: native
          alias: host-a
          shell: bash
        - name: wsl
          alias: host-a-wsl
          shell: bash
          user: dev
    dtssh:
      alias: host-a
      port: 2222
  host-b:
    display_name: host-b
    environment: Linux ARM64
    role: field
    hostname: raw-hostname
    ssh:
      ready: false
    dtssh:
      alias: host-b
      best_effort: true
"""


@pytest.fixture()
def machines_file(tmp_path: Path) -> Path:
    p = tmp_path / "machines.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def test_load_mesh_projects_machines(machines_file: Path) -> None:
    mesh = mesh_mod.load_mesh(machines_file)
    assert mesh.project == "example-mesh"
    assert [m.key for m in mesh.machines] == ["host-a", "host-b"]

    host_a = mesh.machines[0]
    assert host_a.role == "primary"
    assert host_a.ssh_ready is True
    assert [e.name for e in host_a.environments] == ["native", "wsl"]
    assert host_a.environments[1].user == "dev"
    assert host_a.dtssh_alias == "host-a"
    assert host_a.dtssh_best_effort is False

    host_b = mesh.machines[1]
    assert host_b.ssh_ready is False
    assert host_b.hostname == "raw-hostname"
    assert host_b.dtssh_best_effort is True


def test_summary_line(machines_file: Path) -> None:
    mesh = mesh_mod.load_mesh(machines_file)
    expected = "example-mesh: 2 machine(s) in machines.yaml, 1 SSH-ready."
    assert mesh_mod.summary_line(mesh) == expected


def test_format_report_mentions_hosts_and_dtssh(machines_file: Path) -> None:
    report = mesh_mod.format_report(mesh_mod.load_mesh(machines_file))
    assert "host-a" in report
    assert "role=primary" in report
    assert "ssh native: host-a (bash)" in report
    assert "best-effort" in report  # host-b dtssh note


def test_empty_when_not_a_mapping(tmp_path: Path) -> None:
    p = tmp_path / "machines.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    mesh = mesh_mod.load_mesh(p)
    assert mesh.machines == []


def test_empty_on_malformed_yaml(tmp_path: Path) -> None:
    p = tmp_path / "machines.yaml"
    p.write_text("control_plane: [unterminated\n  : :\n", encoding="utf-8")
    mesh = mesh_mod.load_mesh(p)  # must not raise
    assert mesh.machines == []


def test_find_machines_file_walks_parents(tmp_path: Path) -> None:
    (tmp_path / "machines.yaml").write_text(SAMPLE, encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    found = mesh_mod.find_machines_file(sub)
    assert found is not None
    assert found.name == "machines.yaml"
