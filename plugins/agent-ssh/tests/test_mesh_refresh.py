"""Tests for agent-ssh refresh-mesh (mesh_refresh.py).

Sample identifiers are neutral placeholders per the repo's identifier-neutrality
guidance for public artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_ssh import fragment_registry, mesh_refresh, ssh_profile

SAMPLE = """
control_plane:
  project: example-mesh

machines:
  host-a:
    display_name: host-a
    hostname: host-a-raw
    ssh:
      ready: true
    dtssh:
      alias: host-a
      port: 2222
  host-b:
    display_name: host-b
    hostname: host-b-raw
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

KEY_ONLY_SAMPLE = """
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
"""


@pytest.fixture(autouse=True)
def isolated_ssh_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ssh_profile, "_ssh_dir", lambda: tmp_path / ".ssh")
    monkeypatch.setenv("AGENT_SSH_HOME", str(tmp_path / "runtime"))


@pytest.fixture()
def machines_file(tmp_path: Path) -> Path:
    p = tmp_path / "machines.yaml"
    p.write_text(SAMPLE, encoding="utf-8")
    return p


@pytest.fixture()
def key_only_machines_file(tmp_path: Path) -> Path:
    p = tmp_path / "machines.yaml"
    p.write_text(KEY_ONLY_SAMPLE, encoding="utf-8")
    return p


@pytest.fixture()
def payload_root(tmp_path: Path) -> Path:
    root = tmp_path / "payload"
    deploy = root / "transports" / "dtssh" / "deploy"
    deploy.mkdir(parents=True)
    (deploy / "emit-registry.py").write_text("", encoding="utf-8")
    (root / "transports" / "dtssh" / "module.yaml").write_text("module: dtssh\n", encoding="utf-8")
    return root


@pytest.fixture()
def registry_run():
    original_run = mesh_refresh.subprocess.run

    def run(argv, **kwargs):
        if "--out" not in argv:
            return original_run(argv, **kwargs)
        output = Path(argv[argv.index("--out") + 1])
        output.write_text("transport: dtssh\nmachines: []\n", encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return run


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
    tmp_path, monkeypatch, machines_file: Path, payload_root: Path, registry_run
) -> None:
    written = {}

    def fake_write_fragment(cfg, module, *, config_d=None, registry_path=None, module_path=None):
        written["cfg"] = cfg
        return Path("50-agent-ssh-dtssh.conf")

    class _FakeReport:
        def refresh(self):
            return self

    monkeypatch.setattr(mesh_refresh.subprocess, "run", registry_run)
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


def _write_registry_producer(payload_root: Path, hostname: str) -> None:
    registry = json.dumps({
        "transport": "dtssh",
        "machines": [
            {"name": alias, "hostname": hostname, "user": "test-user"}
            for alias in ("host-a", "host-b")
        ],
    })
    script = payload_root / "transports" / "dtssh" / "deploy" / "emit-registry.py"
    script.write_text(
        "import sys\nfrom pathlib import Path\n"
        "out = Path(sys.argv[sys.argv.index('--out') + 1])\n"
        f"out.write_text({registry!r}, encoding='utf-8')\n",
        encoding="utf-8",
    )


def _fragment_source(config_d: Path) -> Path:
    lines = (config_d / "50-agent-ssh-dtssh.conf").read_text(encoding="utf-8").splitlines()
    metadata = next(line for line in lines if line.startswith(ssh_profile.METADATA_PREFIX))
    return Path(json.loads(metadata[len(ssh_profile.METADATA_PREFIX):])["registry"])


def test_refresh_mesh_retains_real_post_refresh_provenance(
    tmp_path, monkeypatch, machines_file: Path, payload_root: Path
) -> None:
    _write_registry_producer(payload_root, "tunnel-a.example.invalid")
    config_d = tmp_path / ".ssh" / "config.d"
    monkeypatch.setattr(mesh_refresh, "probe_alias", lambda alias, timeout: True)

    result = mesh_refresh.refresh_mesh(
        machines_yaml=machines_file, config_d=config_d,
        resolve_payload_root=lambda: payload_root,
    )

    assert result.ok
    source = _fragment_source(config_d)
    assert source.is_file()
    assert source.parent.parent == config_d.parent
    assert not any("agent-ssh-mesh-refresh-" in part for part in source.parts)
    report = fragment_registry.FragmentRegistry(config_d).refresh(emit_warnings=False)
    assert not report.findings
    assert report.active_aliases == frozenset({"host-a", "host-b"})
    assert all(report.permits_probe(alias) for alias in ("host-a", "host-b"))


def test_refresh_mesh_preserves_prior_evidence_until_new_fragment_publication(
    tmp_path, monkeypatch, machines_file: Path, payload_root: Path
) -> None:
    config_d = tmp_path / ".ssh" / "config.d"
    monkeypatch.setattr(mesh_refresh, "probe_alias", lambda alias, timeout: True)
    arguments = {
        "machines_yaml": machines_file,
        "config_d": config_d,
        "resolve_payload_root": lambda: payload_root,
    }
    _write_registry_producer(payload_root, "tunnel-a.example.invalid")
    assert mesh_refresh.refresh_mesh(**arguments).ok
    original_source = _fragment_source(config_d)
    original_bytes = original_source.read_bytes()
    write_fragment = ssh_profile.write_fragment

    def publish(cfg, module, **kwargs):
        report = fragment_registry.FragmentRegistry(config_d).refresh(emit_warnings=False)
        assert not report.findings
        assert report.active_aliases == frozenset({"host-a", "host-b"})
        assert original_source.read_bytes() == original_bytes
        return write_fragment(cfg, module, **kwargs)

    monkeypatch.setattr(ssh_profile, "write_fragment", publish)
    _write_registry_producer(payload_root, "tunnel-b.example.invalid")
    assert mesh_refresh.refresh_mesh(**arguments).ok
    changed_source = _fragment_source(config_d)
    assert changed_source != original_source
    assert original_source.read_bytes() == original_bytes
    assert fragment_registry.FragmentRegistry(config_d).refresh(
        emit_warnings=False
    ).active_aliases == frozenset({"host-a", "host-b"})
    assert mesh_refresh.refresh_mesh(**arguments).ok
    assert _fragment_source(config_d) == changed_source


def test_refresh_mesh_reports_source_retention_failure_without_replacing_profile(
    tmp_path, monkeypatch, machines_file: Path, payload_root: Path
) -> None:
    config_d = tmp_path / ".ssh" / "config.d"
    monkeypatch.setattr(mesh_refresh, "probe_alias", lambda alias, timeout: True)
    arguments = {
        "machines_yaml": machines_file,
        "config_d": config_d,
        "resolve_payload_root": lambda: payload_root,
    }
    _write_registry_producer(payload_root, "tunnel-a.example.invalid")
    assert mesh_refresh.refresh_mesh(**arguments).ok
    fragment = config_d / "50-agent-ssh-dtssh.conf"
    original_fragment = fragment.read_bytes()
    original_source = _fragment_source(config_d)
    _write_registry_producer(payload_root, "tunnel-b.example.invalid")

    def denied(*args, **kwargs):
        raise PermissionError("registry storage denied")

    monkeypatch.setattr(mesh_refresh, "atomic_write_text", denied)
    result = mesh_refresh.refresh_mesh(**arguments)
    assert not result.ok
    assert "registry storage denied" in result.detail
    assert not result.aliases
    assert fragment.read_bytes() == original_fragment
    assert original_source.is_file()
    report = fragment_registry.FragmentRegistry(config_d).refresh(emit_warnings=False)
    assert not report.findings
    assert report.active_aliases == frozenset({"host-a", "host-b"})


def test_refresh_mesh_flags_unreachable_alias(
    tmp_path, monkeypatch, machines_file: Path, payload_root: Path, registry_run
) -> None:
    class _FakeReport:
        def refresh(self):
            return self

    monkeypatch.setattr(mesh_refresh.subprocess, "run", registry_run)
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


@pytest.mark.parametrize(
    ("declared", "local", "expected"),
    [
        ("host-a-raw", "host-a-raw", True),
        ("HOST-A-RAW", "host-a-raw", True),
        ("host-a-raw.corp.example.com", "host-a-raw", True),
        ("host-a-raw", "host-a-raw.corp.example.com", True),
        ("host-a-raw", "host-b-raw", False),
        ("", "host-a-raw", False),
        ("host-a-raw", "", False),
        ("", "", False),
        (" \t", " \t", False),
        (" ", "host-a-raw", False),
        ("host-a-raw", "\t", False),
        (" host-a-raw ", "\thost-a-raw\t", True),
        # Two distinct FQDNs sharing a leading label must NEVER be equated by
        # truncating both sides to their short name -- only an exact match,
        # or a bare short name on exactly one side, counts.
        ("host-a.corp.example", "host-a.lab.example", False),
        ("host-a.corp.example", "host-a.corp.example", True),
        ("HOST-A.CORP.EXAMPLE", "host-a.corp.example", True),
    ],
)
def test_is_local_machine_matches_raw_hostname_case_and_domain_insensitively(
    declared: str, local: str, expected: bool
) -> None:
    assert mesh_refresh._is_local_machine(declared, local) is expected


def test_refresh_mesh_skips_ssh_probe_for_this_machines_own_alias(
    tmp_path, monkeypatch, machines_file: Path, payload_root: Path, registry_run
) -> None:
    """The alias whose declared hostname matches this machine must never be
    dialed over SSH -- it would otherwise route this machine's own
    reachability check out through its own dtssh/Dev Tunnel relay
    (copilot-extensions#5375)."""

    class _FakeReport:
        def refresh(self):
            return self

    probed: list[str] = []

    def fake_probe(alias, timeout):
        probed.append(alias)
        return True

    monkeypatch.setattr(mesh_refresh.subprocess, "run", registry_run)
    monkeypatch.setattr(mesh_refresh.ssh_profile, "load_file", lambda path: {"transport": "dtssh"})
    monkeypatch.setattr(
        mesh_refresh.ssh_profile, "write_fragment", lambda *a, **k: Path("frag")
    )
    monkeypatch.setattr(
        mesh_refresh.fragment_registry,
        "FragmentRegistry",
        lambda config_d: _FakeReport(),
    )
    monkeypatch.setattr(mesh_refresh, "probe_alias", fake_probe)

    result = mesh_refresh.refresh_mesh(
        machines_yaml=machines_file,
        resolve_payload_root=lambda: payload_root,
        local_hostname="host-a-raw",
    )

    # host-a is this machine -- never shelled out to ssh for it.
    assert probed == ["host-b"]
    assert result.ok is True
    by_alias = {alias.alias: alias for alias in result.aliases}
    assert by_alias["host-a"].local is True
    assert by_alias["host-a"].reachable is True
    assert "skipped outbound SSH probe" in by_alias["host-a"].detail
    assert by_alias["host-b"].local is False


def test_refresh_mesh_unresolvable_local_hostname_never_matches_everything(
    tmp_path, monkeypatch, machines_file: Path, payload_root: Path, registry_run
) -> None:
    """An empty/unresolvable local hostname must not accidentally mark every
    declared alias as local (which would silently skip every real probe)."""

    class _FakeReport:
        def refresh(self):
            return self

    probed: list[str] = []

    def fake_probe(alias, timeout):
        probed.append(alias)
        return True

    monkeypatch.setattr(mesh_refresh.subprocess, "run", registry_run)
    monkeypatch.setattr(mesh_refresh.ssh_profile, "load_file", lambda path: {"transport": "dtssh"})
    monkeypatch.setattr(
        mesh_refresh.ssh_profile, "write_fragment", lambda *a, **k: Path("frag")
    )
    monkeypatch.setattr(
        mesh_refresh.fragment_registry,
        "FragmentRegistry",
        lambda config_d: _FakeReport(),
    )
    monkeypatch.setattr(mesh_refresh, "probe_alias", fake_probe)

    result = mesh_refresh.refresh_mesh(
        machines_yaml=machines_file,
        resolve_payload_root=lambda: payload_root,
        local_hostname="",
    )

    assert sorted(probed) == ["host-a", "host-b"]
    assert all(not alias.local for alias in result.aliases)


def test_refresh_mesh_skips_local_alias_using_key_fallback_when_hostname_omitted(
    tmp_path, monkeypatch, key_only_machines_file: Path, payload_root: Path, registry_run
) -> None:
    """``hostname`` is optional in the shared machines.yaml contract -- an
    empty value means the machine's own key IS its hostname
    (agent_bridge.topology.MachineConfig.hostname). Self-detection must fall
    back to the key, not silently stay blind to the common key-only
    declaration."""

    class _FakeReport:
        def refresh(self):
            return self

    probed: list[str] = []

    def fake_probe(alias, timeout):
        probed.append(alias)
        return True

    monkeypatch.setattr(mesh_refresh.subprocess, "run", registry_run)
    monkeypatch.setattr(mesh_refresh.ssh_profile, "load_file", lambda path: {"transport": "dtssh"})
    monkeypatch.setattr(
        mesh_refresh.ssh_profile, "write_fragment", lambda *a, **k: Path("frag")
    )
    monkeypatch.setattr(
        mesh_refresh.fragment_registry,
        "FragmentRegistry",
        lambda config_d: _FakeReport(),
    )
    monkeypatch.setattr(mesh_refresh, "probe_alias", fake_probe)

    result = mesh_refresh.refresh_mesh(
        machines_yaml=key_only_machines_file,
        resolve_payload_root=lambda: payload_root,
        # No declared `hostname` for either machine in KEY_ONLY_SAMPLE -- the
        # local hostname matches the "host-a" machines.yaml key directly.
        local_hostname="host-a",
    )

    assert probed == ["host-b"]
    by_alias = {alias.alias: alias for alias in result.aliases}
    assert by_alias["host-a"].local is True
    assert by_alias["host-a"].reachable is True
    assert by_alias["host-b"].local is False
