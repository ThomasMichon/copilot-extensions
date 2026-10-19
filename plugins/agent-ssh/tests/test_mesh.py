"""Tests for agent-ssh mesh-status (mesh.py).

Sample identifiers are neutral placeholders per the repo's identifier-neutrality
guidance for public artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_ssh import __main__ as cli
from agent_ssh import mesh as mesh_mod

SAMPLE = """
control_plane:
  project: example-mesh

machines:
  host-a:
    display_name: host-a
    environment: Linux x64
    role: primary
    description: "  General-purpose development host.  "
    capabilities: [" builds ", "", builds, tests, " tests "]
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
    assert host_a.description == "General-purpose development host."
    assert host_a.capabilities == ["builds", "tests"]
    assert host_a.ssh_ready is True
    assert [e.name for e in host_a.environments] == ["native", "wsl"]
    assert host_a.environments[1].user == "dev"
    assert host_a.dtssh_alias == "host-a"
    assert host_a.dtssh_best_effort is False

    host_b = mesh.machines[1]
    assert host_b.ssh_ready is False
    assert host_b.hostname == "raw-hostname"
    assert host_b.dtssh_best_effort is True
    assert host_b.description == ""
    assert host_b.capabilities == []


def test_json_projection_includes_machine_metadata(machines_file: Path) -> None:
    data = mesh_mod.load_mesh(machines_file).to_dict()
    host_a = data["machines"][0]
    host_b = data["machines"][1]
    assert host_a["description"] == "General-purpose development host."
    assert host_a["capabilities"] == ["builds", "tests"]
    assert host_b["description"] == ""
    assert host_b["capabilities"] == []


def test_mesh_status_json_includes_machine_metadata(
    machines_file: Path, tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(
        cli.fragment_registry.FragmentRegistry,
        "refresh",
        lambda _self: None,
    )
    args = argparse.Namespace(
        config_d=tmp_path,
        path=machines_file,
        json=True,
        summary=False,
    )

    assert cli._cmd_mesh_status(args) == 0

    machine = json.loads(capsys.readouterr().out)["machines"][0]
    assert machine["description"] == "General-purpose development host."
    assert machine["capabilities"] == ["builds", "tests"]


def test_summary_line(machines_file: Path) -> None:
    mesh = mesh_mod.load_mesh(machines_file)
    expected = "example-mesh: 2 machine(s) in machines.yaml, 1 SSH-ready."
    assert mesh_mod.summary_line(mesh) == expected


def test_format_report_mentions_hosts_and_dtssh(machines_file: Path) -> None:
    report = mesh_mod.format_report(mesh_mod.load_mesh(machines_file))
    assert "host-a" in report
    assert "role=primary" in report
    assert "description: General-purpose development host." in report
    assert "capabilities: builds, tests" in report
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


def test_invalid_yaml_timestamp_preserves_tolerant_loading(tmp_path: Path) -> None:
    path = tmp_path / "machines.yaml"
    path.write_text(
        "machines: {host-a: {environment: 2026-13-01}}\n", encoding="utf-8",
    )
    assert mesh_mod.load_mesh(path).machines == []
    with pytest.raises(ValueError, match="cannot parse machines.yaml"):
        mesh_mod.load_mesh(path, strict=True)


def test_malformed_metadata_fails_open(tmp_path: Path) -> None:
    p = tmp_path / "machines.yaml"
    p.write_text(
        """machines:
  host-a:
    description: [not, a, string]
    capabilities: builds
""",
        encoding="utf-8",
    )
    machine = mesh_mod.load_mesh(p).machines[0]
    assert machine.description == ""
    assert machine.capabilities == []


def test_find_machines_file_walks_parents(tmp_path: Path) -> None:
    (tmp_path / "machines.yaml").write_text(SAMPLE, encoding="utf-8")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    found = mesh_mod.find_machines_file(sub)
    assert found is not None
    assert found.name == "machines.yaml"


@pytest.mark.parametrize(
    "relative",
    ["machines.yaml", ".agent-worktrees/machines.yaml", "config/machines.yaml", ".github/machines.yaml"],
)
def test_find_conventional_source_in_git_repo(tmp_path: Path, relative: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, **no_window_kwargs())
    source = repo / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(SAMPLE, encoding="utf-8")
    sub = repo / "src" / "nested"
    sub.mkdir(parents=True)

    assert mesh_mod.find_machines_file(sub) == source


def test_conventional_source_priority_matches_bridge(tmp_path: Path) -> None:
    for relative in (
        "machines.yaml", ".agent-worktrees/machines.yaml",
        "config/machines.yaml", ".github/machines.yaml",
    ):
        source = tmp_path / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(SAMPLE, encoding="utf-8")

    assert mesh_mod.find_machines_file(tmp_path) == tmp_path / "machines.yaml"


def test_discovery_stops_at_git_boundary(tmp_path: Path) -> None:
    (tmp_path / "machines.yaml").write_text(SAMPLE, encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, **no_window_kwargs())
    sub = repo / "nested"
    sub.mkdir()
    # A nearer subdirectory declaration is not the repository's topology either.
    (sub / "machines.yaml").write_text(SAMPLE, encoding="utf-8")

    assert mesh_mod.find_machines_file(sub) is None


def test_inherited_git_directory_cannot_select_another_repo(tmp_path: Path, monkeypatch) -> None:
    repos = [tmp_path / name for name in ("caller", "other")]
    for repo in repos:
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, **no_window_kwargs())
        (repo / "machines.yaml").write_text(SAMPLE, encoding="utf-8")
    caller, other = repos
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))

    assert mesh_mod.find_machines_file(caller) == caller / "machines.yaml"


def test_git_lookup_timeout_does_not_fall_back(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "machines.yaml").write_text(SAMPLE, encoding="utf-8")

    def timeout(argv, **kwargs):
        assert kwargs["timeout"] == 10
        raise subprocess.TimeoutExpired(argv, 10)

    monkeypatch.setattr(mesh_mod.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="timed out"):
        mesh_mod.find_machines_file(tmp_path)


def test_discovery_without_git_stops_at_worktree_gitfile(
    tmp_path: Path, monkeypatch,
) -> None:
    (tmp_path / "machines.yaml").write_text(SAMPLE, encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    sub = repo / "nested"
    sub.mkdir()

    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("git unavailable")

    monkeypatch.setattr(mesh_mod.subprocess, "run", unavailable)
    assert mesh_mod.find_machines_file(sub) is None


def test_invalid_preferred_source_does_not_select_a_fallback(tmp_path: Path) -> None:
    (tmp_path / "machines.yaml").mkdir()
    fallback = tmp_path / ".agent-worktrees" / "machines.yaml"
    fallback.parent.mkdir()
    fallback.write_text(SAMPLE, encoding="utf-8")

    with pytest.raises(ValueError, match="not a regular file"):
        mesh_mod.find_machines_file(tmp_path)


@pytest.mark.parametrize("with_topology", [False, True])
@pytest.mark.parametrize("command", ["mesh-status", "refresh-mesh"])
def test_regular_config_file_does_not_block_conventional_discovery(
    tmp_path: Path, with_topology: bool, command: str,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, **no_window_kwargs())
    (tmp_path / "config").write_text("unrelated repository config\n", encoding="utf-8")
    source = tmp_path / ".github" / "machines.yaml"
    if with_topology:
        source.parent.mkdir()
        source.write_text("machines: {}\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "agent_ssh", command, "--json"],
        cwd=tmp_path, capture_output=True, text=True, timeout=5, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    field = "source" if command == "mesh-status" else "machines_yaml"
    assert data[field] == (str(source) if with_topology else "" if command == "mesh-status" else None)


@pytest.mark.parametrize("body", ["", "machines: []", "- not-a-mapping", "machines: [broken"])
def test_strict_source_load_rejects_invalid_document(tmp_path: Path, body: str) -> None:
    source = tmp_path / "machines.yaml"
    source.write_text(body, encoding="utf-8")

    with pytest.raises(ValueError, match="machines.yaml"):
        mesh_mod.load_mesh(source, strict=True)


def test_strict_source_keeps_optional_null_transport_blocks(tmp_path: Path) -> None:
    source = tmp_path / "machines.yaml"
    source.write_text("machines: {host-a: {ssh: null, dtssh: null}}\n", encoding="utf-8")
    mesh = mesh_mod.load_mesh(source, strict=True)
    assert [machine.key for machine in mesh.machines] == ["host-a"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO regression")
@pytest.mark.parametrize("via_symlink", [False, True])
@pytest.mark.parametrize("command", ["mesh-status", "refresh-mesh"])
def test_explicit_fifo_source_fails_without_blocking(
    tmp_path: Path, via_symlink: bool, command: str,
) -> None:
    source = tmp_path / "machines.yaml"
    os.mkfifo(source)
    if via_symlink:
        link = tmp_path / "linked.yaml"
        link.symlink_to(source)
        source = link

    result = subprocess.run(
        [sys.executable, "-m", "agent_ssh", command, "--path", str(source), "--json"],
        capture_output=True, text=True, timeout=5, **no_window_kwargs(),
    )
    assert result.returncode == 1
    assert "not a regular file" in result.stdout


@pytest.mark.parametrize("json_output", [False, True])
def test_mesh_status_explicit_missing_source_is_failure(
    tmp_path: Path, monkeypatch, capsys, json_output: bool,
) -> None:
    monkeypatch.setattr(cli.fragment_registry.FragmentRegistry, "refresh", lambda _self: None)
    args = argparse.Namespace(
        config_d=tmp_path, path=tmp_path / "absent.yaml",
        json=json_output, summary=False,
    )

    assert cli._cmd_mesh_status(args) == 1
    output = capsys.readouterr()
    if json_output:
        assert "cannot load mesh source" in json.loads(output.out)["error"]
    else:
        assert "cannot load mesh source" in output.err
