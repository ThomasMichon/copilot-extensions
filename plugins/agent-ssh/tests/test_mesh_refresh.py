"""Tests for agent-ssh refresh-mesh (mesh_refresh.py).

Sample identifiers are neutral placeholders per the repo's identifier-neutrality
guidance for public artifacts.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_ssh import mesh_refresh

SAMPLE = """
control_plane:
  project: example-mesh

machines:
  host-a:
    display_name: host-a
    ssh:
      ready: true
    dtssh:
      alias: host-a
      port: 2222
  host-b:
    display_name: host-b
    ssh:
      ready: true
    dtssh:
      alias: host-b
      port: 2222
  host-c:
    display_name: host-c
    ssh:
      ready: true
"""

NO_DTSSH_SAMPLE = """
control_plane:
  project: example-mesh

machines:
  host-a:
    display_name: host-a
    ssh:
      ready: true
"""


@pytest.fixture()
def machines_file(tmp_path: Path) -> Path:
    p = tmp_path / "machines.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


@pytest.fixture()
def payload_root(tmp_path: Path) -> Path:
    root = tmp_path / "payload"
    deploy = root / "transports" / "dtssh" / "deploy"
    deploy.mkdir(parents=True)
    (deploy / "emit-registry.py").write_text("", encoding="utf-8")
    (root / "transports" / "dtssh" / "module.yaml").write_text("module: dtssh\n", encoding="utf-8")
    return root


def test_refresh_mesh_reports_missing_machines_yaml(tmp_path: Path) -> None:
    result = mesh_refresh.refresh_mesh(machines_yaml=tmp_path / "absent.yaml")
    assert result.ok is True
    assert result.machines_yaml is None
    assert "no machines.yaml" in result.detail


def test_refresh_mesh_reports_no_dtssh_machines(tmp_path: Path) -> None:
    path = tmp_path / "machines.yaml"
    path.write_text(NO_DTSSH_SAMPLE, encoding="utf-8")
    result = mesh_refresh.refresh_mesh(machines_yaml=path)
    assert result.ok is True
    assert result.machines_yaml == str(path)
    assert "no dtssh-transport machines" in result.detail


def test_refresh_mesh_reports_unresolvable_payload_root(machines_file: Path) -> None:
    def _raise() -> Path:
        raise RuntimeError("cannot resolve the active agent-ssh payload")

    result = mesh_refresh.refresh_mesh(
        machines_yaml=machines_file, resolve_payload_root=_raise
    )
    assert result.ok is False
    assert "cannot resolve the active agent-ssh payload" in result.detail


def test_refresh_mesh_reports_missing_transport_assets(
    tmp_path: Path, machines_file: Path
) -> None:
    empty_root = tmp_path / "empty-payload"
    empty_root.mkdir()
    result = mesh_refresh.refresh_mesh(
        machines_yaml=machines_file, resolve_payload_root=lambda: empty_root
    )
    assert result.ok is False
    assert "deploy assets are unavailable" in result.detail


def test_refresh_mesh_reports_emit_registry_failure(
    tmp_path, monkeypatch, machines_file: Path, payload_root: Path
) -> None:
    def fake_run(argv, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="devtunnel login required\n")

    monkeypatch.setattr(mesh_refresh.subprocess, "run", fake_run)
    result = mesh_refresh.refresh_mesh(
        machines_yaml=machines_file, resolve_payload_root=lambda: payload_root
    )
    assert result.ok is False
    assert "devtunnel login required" in result.detail


def test_refresh_mesh_success_probes_every_alias(
    tmp_path, monkeypatch, machines_file: Path, payload_root: Path
) -> None:
    def fake_run(argv, **kwargs):
        out_index = argv.index("--out") + 1
        Path(argv[out_index]).write_text("transport: dtssh\nmachines: []\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    written = {}

    def fake_write_fragment(cfg, module, *, config_d=None, registry_path=None, module_path=None):
        written["cfg"] = cfg
        return Path("50-agent-ssh-dtssh.conf")

    class _FakeReport:
        def refresh(self):
            return self

    monkeypatch.setattr(mesh_refresh.subprocess, "run", fake_run)
    monkeypatch.setattr(mesh_refresh.ssh_profile, "load_file", lambda path: {"transport": "dtssh"})
    monkeypatch.setattr(mesh_refresh.ssh_profile, "write_fragment", fake_write_fragment)
    monkeypatch.setattr(
        mesh_refresh.fragment_registry,
        "FragmentRegistry",
        lambda config_d: _FakeReport(),
    )
    monkeypatch.setattr(mesh_refresh, "probe_alias", lambda alias, timeout: True)

    result = mesh_refresh.refresh_mesh(
        machines_yaml=machines_file, resolve_payload_root=lambda: payload_root
    )
    assert result.ok is True
    assert {alias.alias for alias in result.aliases} == {"host-a", "host-b"}
    assert all(alias.reachable for alias in result.aliases)
    assert "cfg" in written


def test_refresh_mesh_flags_unreachable_alias(
    tmp_path, monkeypatch, machines_file: Path, payload_root: Path
) -> None:
    def fake_run(argv, **kwargs):
        out_index = argv.index("--out") + 1
        Path(argv[out_index]).write_text("transport: dtssh\nmachines: []\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    class _FakeReport:
        def refresh(self):
            return self

    monkeypatch.setattr(mesh_refresh.subprocess, "run", fake_run)
    monkeypatch.setattr(mesh_refresh.ssh_profile, "load_file", lambda path: {"transport": "dtssh"})
    monkeypatch.setattr(
        mesh_refresh.ssh_profile, "write_fragment", lambda *a, **k: Path("frag")
    )
    monkeypatch.setattr(
        mesh_refresh.fragment_registry,
        "FragmentRegistry",
        lambda config_d: _FakeReport(),
    )
    monkeypatch.setattr(
        mesh_refresh, "probe_alias", lambda alias, timeout: alias != "host-b"
    )

    result = mesh_refresh.refresh_mesh(
        machines_yaml=machines_file, resolve_payload_root=lambda: payload_root
    )
    assert result.ok is False
    assert "host-b" in result.detail
    reachable = {alias.alias: alias.reachable for alias in result.aliases}
    assert reachable == {"host-a": True, "host-b": False}
