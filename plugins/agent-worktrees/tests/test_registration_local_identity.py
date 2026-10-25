"""Actual project registration keeps optional topology out of local admission."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import config as cfg, installation_cli, installer, repos


@pytest.fixture
def registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    state = tmp_path / "state"
    effects = []
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", None)
    monkeypatch.setattr(cfg, "_home", lambda: state)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: state / (name or "registered"))
    monkeypatch.setattr(cfg, "global_config_path", lambda: state / "global.yaml")
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "raw-host")
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    overrides = {
        "_prepare_namespaced_project_state": lambda *a: effects.append("prepare") or True,
        "_cleanup_stale_instructions": lambda *a: effects.append("cleanup"),
        "_deploy_copilot_instructions": lambda *a, **k: effects.append("instructions"),
        "_ensure_ado_pr_cli": lambda *a: None,
    }
    monkeypatch.setattr(
        installation_cli, "_core_helper", lambda name, local: overrides.get(name, local),
    )
    monkeypatch.setattr(repos, "find_repo", lambda *a, **k: None)
    monkeypatch.setattr(repos, "add_repo", lambda *a, **k: effects.append("repository"))
    monkeypatch.setattr(installer, "register_project", lambda *a, **k: effects.append("project"))
    monkeypatch.setattr(installer, "deploy_binstubs", lambda *a, **k: effects.append("binstubs") or True)
    monkeypatch.setattr(
        installation_cli.picker_profiles_cli, "_refresh_terminal_profiles",
        lambda *a, **k: effects.append("profiles") or True,
    )
    from agent_worktrees import related_machine_presence

    monkeypatch.setattr(related_machine_presence, "record_local_presence", lambda *a, **k: None)

    @contextmanager
    def publication(*args, **kwargs):
        effects.append("publication")
        yield SimpleNamespace(commit=lambda: effects.append("commit"))

    monkeypatch.setattr(installer, "project_binstub_registration", publication)
    from agent_worktrees import _command_lifecycle, registry_paths

    monkeypatch.setattr(cfg, "project_name", lambda: "registered")
    monkeypatch.setattr(registry_paths, "installation_context", lambda: None)
    monkeypatch.setattr(_command_lifecycle, "legacy_lifecycle", lambda *a, **k: nullcontext())
    monkeypatch.setattr(installer, "check_prereqs", lambda: [])
    monkeypatch.setattr(installer, "_payload_root", lambda *a: repo)
    monkeypatch.setattr(installer, "local_bin", lambda: state / "bin")
    monkeypatch.setattr(installer, "create_venv", lambda: effects.append("venv") or True)
    monkeypatch.setattr(installer, "deploy_package", lambda *a: effects.append("package") or True)
    monkeypatch.setattr(installer, "deploy_wrappers", lambda *a: effects.append("wrappers") or True)
    monkeypatch.setattr(installer, "reconcile_binstubs", lambda: effects.append("reconcile"))
    monkeypatch.setattr(
        installation_cli, "_write_install_manifest",
        lambda *a: effects.append("manifest") or True,
    )
    monkeypatch.setattr(installation_cli.worktree_identity, "_find_repo_dir", lambda: repo)
    monkeypatch.setattr(
        installation_cli.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )
    args = SimpleNamespace(
        project_name="registered", repo_dir=str(repo), machine=None,
        default_branch="main", force=False, agent=True,
    )
    return SimpleNamespace(repo=repo, state=state, effects=effects, args=args)


def _map(repo: Path, kind: str, platform: str) -> None:
    if kind == "absent":
        return
    sibling = "windows" if platform == "wsl" else "wsl"
    entries = {
        "empty": {},
        "unrelated": {"peer-space": {
            "hostname": "other-host", "physical_host": "peer-host",
            "execution_platform": platform,
        }},
        "matching": {"local-space": {
            "hostname": "raw-host", "physical_host": "friendly-host",
            "execution_platform": platform,
        }},
        "unscoped": {"friendly-host": {"hostname": "raw-host"}},
        "sibling-only": {"sibling-space": {
            "hostname": "raw-host", "physical_host": "friendly-host",
            "execution_platform": sibling,
        }},
    }
    (repo / "machines.yaml").write_text(yaml.safe_dump({"machines": entries[kind]}))


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize("map_kind", [
    "absent", "empty", "unrelated", "matching", "unscoped", "sibling-only",
])
def test_actual_register_without_machine_enrollment(
    registration, monkeypatch: pytest.MonkeyPatch, platform: str, command: str, map_kind: str,
) -> None:
    fixture = registration
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    _map(fixture.repo, map_kind, platform)
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == 0
    assert cfg.active_project() is None
    config_path = cfg.project_dir("registered") / "config.yaml"
    config = cfg.load_config(
        config_path, project="registered", include_control_plane_related_pr=False,
        include_knowledge_preferences=False,
    )
    expected = "local-space" if map_kind == "matching" else (
        "friendly-host" if map_kind in ("unscoped", "sibling-only") else "raw-host"
    ) + ("-wsl" if platform == "wsl" else "")
    assert config.machine == expected
    assert config.platform == platform
    assert Path(config.default_repo.anchor) == fixture.repo
    assert "machine" not in yaml.safe_load(config_path.read_text())
    assert yaml.safe_load(cfg.global_config_path().read_text())["machine"] == expected
    assert {"prepare", "repository", "project", "publication", "commit", "binstubs"} <= set(
        fixture.effects,
    )
    customized = map_kind == "matching" or (map_kind == "unscoped" and platform != "wsl")
    assert ("instructions" in fixture.effects) is customized


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize("fault", ["collision", "platform", "ambiguous", "malformed", "declaration"])
def test_registration_refuses_invalid_identity_before_effects(
    registration, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    platform: str, command: str, fault: str,
) -> None:
    fixture = registration
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    other = "windows" if platform == "wsl" else "wsl"
    entries = {
        "collision": {
            "local-space": {
                "hostname": "raw-host", "physical_host": "friendly-host",
                "execution_platform": platform,
            },
            "foreign": {
                "hostname": "other-host", "physical_host": "other-host",
                "execution_platform": other, "alias": "local-space",
            },
        },
        "platform": {"foreign": {
            "hostname": "other-host", "physical_host": "other-host",
            "execution_platform": other,
        }},
        "ambiguous": {key: {
            "hostname": "raw-host", "physical_host": "friendly-host",
            "execution_platform": platform,
        } for key in ("first", "second")},
        "declaration": {"invalid": {
            "execution_platform": platform,
            "ssh": {"environments": [{"name": other, "alias": "invalid-profile"}]},
        }},
    }
    if fault == "malformed":
        (fixture.repo / "machines.yaml").write_text("machines: [\n")
    else:
        (fixture.repo / "machines.yaml").write_text(yaml.safe_dump({"machines": entries[fault]}))
    if fault == "platform":
        fixture.args.machine = "foreign"
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == 1
    assert fixture.effects == []
    assert not fixture.state.exists()
    diagnostic = capsys.readouterr()
    assert "refused" in diagnostic.out + diagnostic.err


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize("binding", ["local-space", "route-alias", "profile-only", "raw-host"])
def test_explicit_registration_binding_retains_its_provenance(
    registration, monkeypatch: pytest.MonkeyPatch, platform: str, command: str, binding: str,
) -> None:
    fixture = registration
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    (fixture.repo / "machines.yaml").write_text(yaml.safe_dump({"machines": {"local-space": {
        "hostname": "raw-host", "physical_host": "friendly-host",
        "execution_platform": platform, "alias": "route-alias",
        "ssh": {"environments": [{"name": platform, "alias": "profile-only"}]},
    }}}))
    fixture.args.machine = binding
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == 0
    assert yaml.safe_load(cfg.global_config_path().read_text())["machine"] == binding
    config = cfg.load_config(
        cfg.project_dir("registered") / "config.yaml", project="registered",
        include_control_plane_related_pr=False, include_knowledge_preferences=False,
    )
    assert config.configured_machine == binding
    assert config.machine == "local-space"
    assert "instructions" in fixture.effects


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
def test_registration_preserves_existing_user_configuration(
    registration, monkeypatch: pytest.MonkeyPatch, platform: str, command: str,
) -> None:
    fixture = registration
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    global_path = cfg.global_config_path()
    global_path.parent.mkdir(parents=True)
    global_path.write_text(f"# user settings\nmachine: retained-label\nplatform: {platform}\n")
    project_path = cfg.project_dir("registered") / "config.yaml"
    project_path.parent.mkdir()
    project_path.write_text(yaml.safe_dump({
        "repo_name": "registered", "repos": {"registered": {"anchor": str(fixture.repo)}},
    }) + "# retained project settings\n")
    before = (global_path.read_bytes(), project_path.read_bytes())
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == 0
    assert (global_path.read_bytes(), project_path.read_bytes()) == before


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize("hostname", ["literal-win", "literal-wsl"])
def test_registration_keeps_observed_hostname_suffix_literal(
    registration, monkeypatch: pytest.MonkeyPatch, platform: str, command: str, hostname: str,
) -> None:
    fixture = registration
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: hostname)
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == 0
    expected = hostname + ("-wsl" if platform == "wsl" else "")
    config = cfg.load_config(
        cfg.project_dir("registered") / "config.yaml", project="registered",
        include_control_plane_related_pr=False, include_knowledge_preferences=False,
    )
    assert config.machine == expected


@pytest.mark.parametrize("command", ["register", "install"])
def test_registration_refuses_unknown_observed_environment(
    registration, monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    fixture = registration
    monkeypatch.setattr(cfg, "detect_platform", lambda: "unknown")
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == 1
    assert fixture.effects == []
    assert not fixture.state.exists()
