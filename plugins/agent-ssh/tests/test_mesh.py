"""Tests for agent-ssh mesh-status (mesh.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_ssh import mesh as mesh_mod

SAMPLE = """
control_plane:
  project: dotfiles

machines:
  tmichon-dev6:
    display_name: dev6
    environment: Windows 11 x64
    role: primary-dev
    ssh:
      ready: true
      environments:
        - name: windows
          alias: tmichon-dev6
          shell: pwsh
        - name: wsl
          alias: tmichon-dev6-wsl
          shell: bash
          user: tmichon
    dtssh:
      alias: tmichon-dev6
      port: 2222
  tmichon-book2:
    display_name: book2
    environment: Windows 11 ARM64
    role: field-terminal
    hostname: cpc-raw-name
    ssh:
      ready: false
    dtssh:
      alias: tmichon-book2
      best_effort: true
"""


@pytest.fixture()
def machines_file(tmp_path: Path) -> Path:
    p = tmp_path / "machines.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


def test_load_mesh_projects_machines(machines_file: Path) -> None:
    mesh = mesh_mod.load_mesh(machines_file)
    assert mesh.project == "dotfiles"
    assert [m.key for m in mesh.machines] == ["tmichon-dev6", "tmichon-book2"]

    dev6 = mesh.machines[0]
    assert dev6.role == "primary-dev"
    assert dev6.ssh_ready is True
    assert [e.name for e in dev6.environments] == ["windows", "wsl"]
    assert dev6.environments[1].user == "tmichon"
    assert dev6.dtssh_alias == "tmichon-dev6"
    assert dev6.dtssh_best_effort is False

    book2 = mesh.machines[1]
    assert book2.ssh_ready is False
    assert book2.hostname == "cpc-raw-name"
    assert book2.dtssh_best_effort is True


def test_summary_line(machines_file: Path) -> None:
    mesh = mesh_mod.load_mesh(machines_file)
    assert mesh_mod.summary_line(mesh) == "dotfiles: 2 machine(s) in machines.yaml, 1 SSH-ready."


def test_format_report_mentions_hosts_and_dtssh(machines_file: Path) -> None:
    report = mesh_mod.format_report(mesh_mod.load_mesh(machines_file))
    assert "dev6" in report
    assert "role=primary-dev" in report
    assert "ssh windows: tmichon-dev6 (pwsh)" in report
    assert "best-effort" in report  # book2 dtssh note


def test_empty_when_not_a_mapping(tmp_path: Path) -> None:
    p = tmp_path / "machines.yaml"
    p.write_text("- just\n- a\n- list\n", encoding="utf-8")
    mesh = mesh_mod.load_mesh(p)
    assert mesh.machines == []


def test_find_machines_file_walks_parents(tmp_path: Path) -> None:
    (tmp_path / "machines.yaml").write_text(SAMPLE, encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    found = mesh_mod.find_machines_file(sub)
    assert found is not None
    assert found.name == "machines.yaml"
