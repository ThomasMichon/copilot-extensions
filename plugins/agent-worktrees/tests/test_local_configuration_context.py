"""Optional topology customizes local configuration without enrolling the OS."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_worktrees import config as cfg


@pytest.fixture
def local_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    path = state / "config.yaml"
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: state)
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "example")
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda _anchor: {})

    def prepare(environment: str, machines: dict | None, machine: str | None = None):
        monkeypatch.setattr(cfg, "detect_platform", lambda: environment)
        document = {
            "repo_name": "example",
            "repos": {"example": {
                "anchor": str(anchor), "worktree_root": str(tmp_path / "trees"),
            }},
        }
        if machine is not None:
            document["machine"] = machine
        path.write_text(yaml.safe_dump(document), encoding="utf-8")
        topology = anchor / "machines.yaml"
        if machines is not None:
            topology.write_text(yaml.safe_dump({"machines": machines}), encoding="utf-8")
        return anchor, path, {
            source: source.read_bytes() for source in (path, topology) if source.exists()
        }

    return prepare


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("map_kind", ["absent", "empty", "unrelated", "matching"])
@pytest.mark.parametrize("hostname", ["Observed-Host", "Observed-Host-wsl", "Observed-Host-win"])
def test_detect_and_load_optional_local_map(
    local_config, monkeypatch: pytest.MonkeyPatch,
    environment: str, map_kind: str, hostname: str,
) -> None:
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: hostname)
    machines = {
        "absent": None,
        "empty": {},
        "unrelated": {"peer-space": {
            "execution_platform": "linux", "physical_host": "peer-host",
        }},
        "matching": {hostname.lower(): {"hostname": hostname}},
    }[map_kind]
    anchor, path, before = local_config(environment, machines)
    expected = hostname.lower() + ("-wsl" if environment == "wsl" else "")
    assert cfg.detect_machine(anchor) == expected
    loaded = cfg.load_config(
        path, include_control_plane_related_pr=False, include_knowledge_preferences=False,
    )
    assert loaded.machine == expected
    assert loaded.platform == environment
    assert {source: source.read_bytes() for source in before} == before


@pytest.mark.parametrize("environment", ["windows", "linux"])
@pytest.mark.parametrize("binding", [None, "Local-Alias", "LOCAL-ALIAS"])
def test_native_legacy_customization_retains_binding(
    local_config, monkeypatch: pytest.MonkeyPatch, environment: str, binding: str | None,
) -> None:
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host")
    anchor, path, before = local_config(environment, {
        "preferred-host": {"hostname": "Observed-Host", "alias": "Local-Alias"},
        "peer-space": {"execution_platform": "wsl", "physical_host": "peer-host"},
    }, binding)
    assert cfg.detect_machine(anchor) == "Local-Alias"
    if binding:
        assert cfg.detect_machine(anchor, value=binding) == binding
    assert cfg.load_config(
        path, include_control_plane_related_pr=False, include_knowledge_preferences=False,
    ).machine == (binding or "observed-host")
    assert {source: source.read_bytes() for source in before} == before


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_matching_explicit_local_map_is_not_an_enrollment_gate(
    local_config, monkeypatch: pytest.MonkeyPatch, environment: str,
) -> None:
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host")
    anchor, path, before = local_config(environment, {
        "local-space": {
            "hostname": "Observed-Host", "execution_platform": environment,
            "physical_host": "physical-host",
        },
    })
    assert cfg.detect_machine(anchor) == "local-space"
    assert cfg.load_config(
        path, include_control_plane_related_pr=False, include_knowledge_preferences=False,
    ).machine == "local-space"
    assert {source: source.read_bytes() for source in before} == before


@pytest.mark.parametrize(("actual", "declared"), [
    ("windows", "wsl"), ("wsl", "windows"), ("linux", "windows"),
])
def test_configured_explicit_space_still_refuses_platform_conflict(
    local_config, monkeypatch: pytest.MonkeyPatch, actual: str, declared: str,
) -> None:
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host")
    anchor, path, before = local_config(actual, {
        "configured-space": {
            "execution_platform": declared, "physical_host": "physical-host",
        },
    }, "configured-space")
    with pytest.raises(ValueError, match="conflicts"):
        cfg.detect_machine(anchor, value="configured-space")
    with pytest.raises(ValueError, match="conflicts"):
        cfg.load_config(
            path, include_control_plane_related_pr=False, include_knowledge_preferences=False,
        )
    assert {source: source.read_bytes() for source in before} == before


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("map_kind", ["absent", "empty", "unrelated"])
@pytest.mark.parametrize("source", ["local", "global"])
def test_existing_configured_binding_does_not_require_map_enrollment(
    local_config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    environment: str, map_kind: str, source: str,
) -> None:
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host")
    machines = {
        "absent": None, "empty": {},
        "unrelated": {"peer-space": {
            "execution_platform": "linux", "physical_host": "peer-host",
        }},
    }[map_kind]
    anchor, path, before = local_config(
        environment, machines, "Preferred-Host" if source == "local" else None,
    )
    global_path = tmp_path / "global.yaml"
    global_path.write_text(
        yaml.safe_dump({"machine": "Other-Host" if source == "local" else "Preferred-Host"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "global_config_path", lambda: global_path)
    before[global_path] = global_path.read_bytes()
    expected = "Preferred-Host-wsl" if environment == "wsl" else "Preferred-Host"
    assert cfg.detect_machine(anchor, value="Preferred-Host") == expected
    assert cfg.load_config(
        path, include_control_plane_related_pr=False, include_knowledge_preferences=False,
    ).machine == expected
    assert {item: item.read_bytes() for item in before} == before


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_actual_detected_customization_never_becomes_configured_read_authority(
    local_config, monkeypatch: pytest.MonkeyPatch, environment: str,
) -> None:
    from agent_worktrees import execution_identity_read

    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host")
    _, path, before = local_config(environment, {
        "local-space": {
            "hostname": "Observed-Host", "execution_platform": environment,
            "physical_host": "physical-host",
        },
    })
    monkeypatch.setattr(cfg, "default_config_path", lambda: path)
    assert execution_identity_read.read_execution_identity()["reason"] == "machine-binding-not-configured"
    assert {item: item.read_bytes() for item in before} == before


@pytest.mark.parametrize("kind", ["ambiguous", "invalid"])
def test_optional_map_errors_are_not_concealed_as_absence(
    local_config, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host")
    machines = {
        "ambiguous": {
            key: {"hostname": "Observed-Host", "physical_host": key}
            for key in ("first-host", "second-host")
        },
        "invalid": {"peer-space": {"execution_platform": "not-a-platform"}},
    }[kind]
    anchor, path, before = local_config("windows", machines)
    for operation in (
        lambda: cfg.detect_machine(anchor),
        lambda: cfg.load_config(
            path, include_control_plane_related_pr=False, include_knowledge_preferences=False,
        ),
    ):
        with pytest.raises(ValueError, match="ambiguous|invalid"):
            operation()
    assert {item: item.read_bytes() for item in before} == before


def test_sole_repository_fallback_keeps_local_customization(
    local_config, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host")
    monkeypatch.setattr(cfg, "_project_name_safe", lambda: "")
    anchor, path, _ = local_config("wsl", {
        "preferred-host": {"hostname": "Observed-Host"},
    })
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document.pop("repo_name")
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    before = path.read_bytes()
    loaded = cfg.load_config(
        path, include_control_plane_related_pr=False, include_knowledge_preferences=False,
    )
    assert loaded.repo_name == ""
    assert loaded.default_repo.anchor == str(anchor)
    assert loaded.machine == cfg.detect_machine(anchor) == "preferred-host-wsl"
    assert path.read_bytes() == before


@pytest.mark.parametrize("environment", ["windows", "linux"])
@pytest.mark.parametrize("physical", ["Rack/Computer", "Rack Computer", "Rack#Computer"])
def test_missing_native_customization_returns_execution_token_not_physical_label(
    local_config, monkeypatch: pytest.MonkeyPatch, environment: str, physical: str,
) -> None:
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host")
    anchor, path, before = local_config(environment, {
        "guest-space": {
            "hostname": "Observed-Host", "execution_platform": "wsl",
            "physical_host": physical,
        },
    })
    assert cfg.detect_machine(anchor) == "observed-host"
    assert cfg.load_config(
        path, include_control_plane_related_pr=False, include_knowledge_preferences=False,
    ).machine == "observed-host"
    assert {item: item.read_bytes() for item in before} == before


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("binding", [None, "custom-space", "local-alias"])
def test_ordinary_project_uses_machine_local_map_without_repository_copy(
    local_config, monkeypatch: pytest.MonkeyPatch, environment: str, binding: str | None,
) -> None:
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host")
    anchor, path, before = local_config(environment, None, binding)
    topology = path.parent / cfg.INREPO_CONFIG_DIRNAME / "machines.yaml"
    topology.parent.mkdir(parents=True)
    topology.write_text(yaml.safe_dump({"machines": {
        "custom-space": {
            "hostname": "Observed-Host", "alias": "local-alias",
            "execution_platform": environment, "physical_host": "physical-host",
        },
    }}), encoding="utf-8")
    before[topology] = topology.read_bytes()
    loaded = cfg.load_config(
        path, include_control_plane_related_pr=False, include_knowledge_preferences=False,
    )
    assert not loaded.default_repo.stateless
    assert not loaded.default_repo.requires_external_state_root
    assert loaded.machine == cfg.detect_machine(
        anchor, value=binding, config=loaded,
    ) == "custom-space"
    assert {item: item.read_bytes() for item in before} == before
