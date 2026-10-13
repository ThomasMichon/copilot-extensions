"""Manager consumer contract for independent logical Windows/WSL machines."""
from __future__ import annotations

import types

import pytest
import yaml

from machine_transport import AmbiguousMachineError
from worktree_manager.production_picker import project_config as cfg
from worktree_manager.production_picker.picker_tui import data_local, data_ssh, roster

pytestmark = [
    pytest.mark.portfolio_tier("T1"),
    pytest.mark.effect("filesystem"),
    pytest.mark.contract("worktree_manager.logical_machine_roster"),
]


def _legacy():
    return {
        "host": {
            "hostname": "raw-host",
            "alias": "friendly",
            "ssh": {
                "ready": True,
                "environments": [
                    {"name": "windows", "alias": "win-ssh", "shell": "pwsh"},
                    {"name": "wsl", "alias": "wsl-ssh", "shell": "bash"},
                ],
            },
        },
    }


def _install(monkeypatch, tmp_path, machines, *, platform="windows", machine="host"):
    path = tmp_path / "machines.yaml"
    path.write_text(yaml.safe_dump({"machines": machines}, sort_keys=False), encoding="utf-8")
    cfg.clear_caches()
    config = types.SimpleNamespace(
        machine=machine,
        default_repo=types.SimpleNamespace(anchor=str(tmp_path)),
    )
    monkeypatch.setattr(cfg, "load_config", lambda: config)
    monkeypatch.setattr(roster, "local_execution_platform", lambda: platform)
    monkeypatch.setattr(data_local, "local_execution_platform", lambda: platform)
    monkeypatch.setattr(data_ssh, "_local_identity", lambda: ("raw-host", platform))
    monkeypatch.setattr("socket.gethostname", lambda: "raw-host.example.test")
    monkeypatch.setattr(data_local, "LOCAL", ("raw-host", roster._ENV_LABEL[platform]))
    monkeypatch.setattr(data_ssh, "_project", lambda: "example")
    monkeypatch.setattr(data_ssh.provider_sources, "load", lambda _project: [])
    monkeypatch.setattr(
        data_ssh, "_argv_for",
        lambda shell, alias, project, **kwargs: ["ssh", alias, shell, project],
    )
    return path


@pytest.mark.parametrize("platform,local_key", [("windows", "host"), ("wsl", "host-wsl")])
@pytest.mark.parametrize("machine", ["host", "friendly", ""])
def test_legacy_projection_has_independent_sources_targets_and_local_space(
    monkeypatch, tmp_path, platform, local_key, machine,
):
    path = _install(monkeypatch, tmp_path, _legacy(), platform=platform, machine=machine)
    before = path.read_bytes()
    entries = cfg.load_machines_yaml(tmp_path)
    assert list(entries) == ["host", "host-wsl"]
    assert [m.execution_platform for m in entries.values()] == ["windows", "wsl"]
    assert [m.physical_host for m in entries.values()] == ["host", "host"]
    assert [m.legacy_key for m in entries.values()] == ["host", "host"]
    assert [env.alias for m in entries.values() for env in m.ssh_environments] == [
        "win-ssh", "wsl-ssh",
    ]
    assert roster.host_cols() == [
        ("host·Win", "host", "Win"), ("host·WSL", "host-wsl", "WSL"),
    ]
    assert roster.target_envs() == [("host", "Win"), ("host-wsl", "WSL")]
    assert roster.local_host() == (local_key, roster._ENV_LABEL[platform])
    sources = data_ssh._build_sources()
    assert [s.key for s in sources] == roster.target_envs()
    assert [s.machine for s in sources if s.local] == [local_key]
    assert [s.source_id for s in sources] == [
        "machine-ssh:host:win", "machine-ssh:host-wsl:wsl",
    ]
    assert [s.source_label for s in sources] == ["host / Win", "host / WSL"]
    assert data_ssh.machines(sources) == [
        ("host Win", "host", "Win", True),
        ("host WSL", "host-wsl", "WSL", True),
    ]
    assert [tab["label"] for tab in data_ssh.source_tabs(sources)] == ["host Win", "host WSL"]
    for source in sources:
        assert data_local.is_local(*source.key) is source.local
        if not source.local:
            assert source.alias == ("win-ssh" if source.env == "Win" else "wsl-ssh")
    assert path.read_bytes() == before


@pytest.mark.parametrize("platform,key", [("windows", "desk-win"), ("wsl", "ubuntu")])
def test_explicit_keys_aliases_and_physical_association_stay_separate(
    monkeypatch, tmp_path, platform, key,
):
    machines = {
        name: {
            "execution_platform": env,
            "physical_host": "desk",
            "hostname": "raw-host",
            "alias": f"{name}-select",
            "ssh": {"ready": True, "environments": [
                {"name": env, "alias": f"{name}-ssh"},
            ]},
        }
        for name, env in [("desk-win", "windows"), ("ubuntu", "wsl")]
    }
    _install(monkeypatch, tmp_path, machines, platform=platform, machine=f"{key}-select")
    entries = cfg.load_machines_yaml(tmp_path)
    assert list(entries) == ["desk-win", "ubuntu"]
    assert entries[key].alias == f"{key}-select"
    assert entries[key].legacy_key == ""
    assert roster.host_cols() == [
        ("desk·Win", "desk-win", "Win"), ("desk·WSL", "ubuntu", "WSL"),
    ]
    assert roster.target_envs() == [("desk-win", "Win"), ("ubuntu", "WSL")]
    assert roster.local_host() == (key, roster._ENV_LABEL[platform])
    sources = data_ssh._build_sources()
    assert [s.machine for s in sources if s.local] == [key]
    assert [s.source_label for s in sources] == ["desk / Win", "desk / WSL"]
    assert data_local.is_local(f"{key}-select", roster._ENV_LABEL[platform])
    other = "ubuntu" if key == "desk-win" else "desk-win"
    assert not data_local.is_local(other, roster._ENV_LABEL[platform])


def test_explicit_wrong_platform_selector_refuses_sibling_fallback(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {
        "desk-win": {"execution_platform": "windows", "physical_host": "desk"},
        "ubuntu": {"execution_platform": "wsl", "physical_host": "desk"},
    }, platform="wsl", machine="desk-win")
    for read in (roster.local_host, data_ssh._build_sources):
        with pytest.raises(ValueError, match="not the local wsl space"):
            read()
    assert not data_local.is_local("ubuntu", "WSL")


def test_raw_hostname_cannot_bypass_an_explicit_logical_platform(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {
        "raw-host": {"execution_platform": "windows", "physical_host": "desk"},
        "ubuntu": {"execution_platform": "wsl", "physical_host": "desk"},
    }, platform="wsl", machine="ubuntu")
    assert not data_local.is_local("raw-host", "WSL")
    assert data_local.is_local("ubuntu", "WSL")


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("machine", ["", "unregistered-host"])
def test_remote_only_registry_preserves_raw_local_source(
    monkeypatch, tmp_path, platform, machine,
):
    _install(monkeypatch, tmp_path, {
        "remote": {
            "hostname": "remote-host",
            "ssh": {"environments": [
                {"name": "windows", "alias": "remote-win"},
                {"name": "wsl", "alias": "remote-wsl"},
            ]},
        },
    }, platform=platform, machine=machine)
    sources = data_ssh._build_sources()
    local = [source for source in sources if source.local]
    assert len(local) == 1
    assert local[0].key == data_local.LOCAL
    assert local[0].ready
    assert data_local.is_local(*local[0].key)
    assert not data_local.is_local("unregistered-other", data_local.LOCAL[1])
    for source in sources:
        if not source.local:
            assert not data_local.is_local(*source.key)


@pytest.mark.parametrize("platform", ["windows", "linux"])
@pytest.mark.parametrize("explicit", [False, True])
def test_standalone_native_machine_has_no_generated_sibling(
    monkeypatch, tmp_path, platform, explicit,
):
    entry = {
        "hostname": "raw-host",
        "ssh": {"environments": [{"name": platform, "alias": "native-ssh"}]},
    }
    if explicit:
        entry["execution_platform"] = platform
    _install(monkeypatch, tmp_path, {"native": entry}, platform=platform, machine="native")
    assert list(cfg.load_machines_yaml(tmp_path)) == ["native"]
    env = roster._ENV_LABEL[platform]
    assert roster.target_envs() == [("native", env)]
    assert roster.local_host() == ("native", env)
    assert [s.key for s in data_ssh._build_sources()] == [("native", env)]


def test_explicit_space_without_ssh_profile_still_has_axes_and_local_source(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path, {
        "ubuntu": {"execution_platform": "wsl", "physical_host": "host"},
        "other": {"execution_platform": "linux"},
    }, platform="wsl", machine="ubuntu")
    assert roster.host_cols() == [
        ("host·WSL", "ubuntu", "WSL"), ("other·Lx", "other", "Linux"),
    ]
    sources = data_ssh._build_sources()
    assert [(s.key, s.local, s.ready) for s in sources] == [
        (("ubuntu", "WSL"), True, True), (("other", "Linux"), False, False),
    ]
    assert roster.local_host() == ("ubuntu", "WSL")


@pytest.mark.parametrize("collision", ["key", "alias", "profile"])
def test_topology_collisions_refuse_without_rewriting(monkeypatch, tmp_path, collision):
    machines = _legacy()
    if collision == "key":
        machines["host-wsl"] = {"execution_platform": "linux"}
    elif collision == "alias":
        machines["other"] = {"alias": "wsl-ssh"}
    else:
        machines["host"]["ssh"]["environments"].append({"name": "wsl", "alias": "second"})
    path = _install(monkeypatch, tmp_path, machines)
    before = path.read_bytes()
    for read in (
        lambda: cfg.load_machines_yaml(tmp_path), roster.host_cols, data_ssh._build_sources,
    ):
        with pytest.raises(AmbiguousMachineError):
            read()
    assert path.read_bytes() == before


def test_overlay_fallback_uses_same_projection(monkeypatch, tmp_path):
    path = _install(monkeypatch, tmp_path, _legacy())
    absent = tmp_path / "absent"
    monkeypatch.setattr(cfg, "machines_yaml_path", lambda _root: path)
    assert list(cfg.load_machines_yaml(absent)) == ["host", "host-wsl"]
