"""Actual project registration keeps optional topology out of local admission."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent_worktrees import config as cfg, installation_cli, installer, repos


@pytest.fixture
def registration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    originals = {
        "project_dir": cfg.project_dir, "global_config_path": cfg.global_config_path,
        "find_repo": repos.find_repo, "add_repo": repos.add_repo,
        "subprocess_run": installation_cli.subprocess.run,
    }
    from agent_worktrees import registry_paths

    originals["installation_context"] = registry_paths.installation_context
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
    return SimpleNamespace(
        repo=repo, state=state, effects=effects, args=args, originals=originals, overrides=overrides,
    )


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
        "friendly-host" if platform == "wsl" and map_kind in ("unscoped", "sibling-only")
        else "raw-host"
    ) + ("-wsl" if platform == "wsl" else "")
    assert config.machine == expected
    assert config.configured_machine is None
    assert config.platform == platform
    assert Path(config.default_repo.anchor) == fixture.repo
    assert "machine" not in yaml.safe_load(config_path.read_text())
    assert "machine" not in yaml.safe_load(cfg.global_config_path().read_text())
    assert {"prepare", "repository", "project", "publication", "commit", "binstubs"} <= set(
        fixture.effects,
    )
    customized = map_kind == "matching" or (map_kind == "unscoped" and platform != "wsl")
    assert ("instructions" in fixture.effects) is customized


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize("fault", [
    "collision", "platform", "ambiguous", "malformed", "declaration", "directory",
])
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
    elif fault == "directory":
        (fixture.repo / "machines.yaml").mkdir()
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


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize("tier", ["global", "project", "dropin"])
@pytest.mark.parametrize("binding", ["foreign-space", ""])
def test_registration_checks_retained_effective_binding_before_writes(
    registration, monkeypatch: pytest.MonkeyPatch, platform: str, command: str,
    tier: str, binding: str,
) -> None:
    fixture = registration
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    other = "windows" if platform == "wsl" else "wsl"
    (fixture.repo / "machines.yaml").write_text(yaml.safe_dump({"machines": {"foreign-space": {
        "hostname": "raw-host", "physical_host": "friendly-host", "execution_platform": other,
    }}}))
    path = {
        "global": cfg.global_config_path(),
        "project": cfg.project_dir("registered") / "config.yaml",
        "dropin": cfg.project_dir("registered") / "config.d" / "local.yaml",
    }[tier]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({
        "machine": binding, "repos": {"registered": {"anchor": str(fixture.repo)}},
    }))
    before = {str(p.relative_to(fixture.state)): p.read_bytes()
              for p in fixture.state.rglob("*") if p.is_file()}
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == 1
    assert fixture.effects == []
    assert {str(p.relative_to(fixture.state)): p.read_bytes()
            for p in fixture.state.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize("physical", ["friendly-win", "friendly-wsl"])
def test_detected_custom_physical_suffix_survives_scaffold_round_trip(
    registration, monkeypatch: pytest.MonkeyPatch, platform: str, command: str, physical: str,
) -> None:
    fixture = registration
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    other = "windows" if platform == "wsl" else "wsl"
    (fixture.repo / "machines.yaml").write_text(yaml.safe_dump({"machines": {"sibling-space": {
        "hostname": "raw-host", "physical_host": physical, "execution_platform": other,
    }}}))
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == 0
    config = cfg.load_config(
        cfg.project_dir("registered") / "config.yaml", project="registered",
        include_control_plane_related_pr=False, include_knowledge_preferences=False,
    )
    assert config.configured_machine is None
    assert config.machine == (physical + "-wsl" if platform == "wsl" else "raw-host")


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize(("case", "allowed"), [
    ("global", True), ("project", True), ("dropin", True),
    ("project-over-dropin", True), ("forced-project", True),
    ("forced-dropin", False), ("explicit-vs-retained", False), ("forced-global", False),
])
def test_registration_matches_retained_binding_precedence(
    registration, monkeypatch: pytest.MonkeyPatch, platform: str, command: str,
    case: str, allowed: bool,
) -> None:
    fixture = registration
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    other = "windows" if platform == "wsl" else "wsl"
    entries = {key: {
        "hostname": "raw-host", "physical_host": "friendly-host", "execution_platform": platform,
    } for key in ("first", "second")}
    entries["foreign"] = {
        "hostname": "other-host", "physical_host": "other-host", "execution_platform": other,
    }
    (fixture.repo / "machines.yaml").write_text(yaml.safe_dump({"machines": entries}))
    global_path = cfg.global_config_path()
    global_path.parent.mkdir(parents=True)
    global_path.write_text(yaml.safe_dump({
        "machine": "first" if case in ("global", "forced-project") else "foreign",
        "platform": platform,
    }))
    project_path = cfg.project_dir("registered") / "config.yaml"
    if case in ("project", "project-over-dropin", "forced-project", "forced-dropin"):
        project_path.parent.mkdir(parents=True)
        project_path.write_text(yaml.safe_dump({
            "machine": "foreign" if case == "forced-project" else "first",
            "repos": {"registered": {"anchor": str(fixture.repo)}},
        }))
    if case in ("dropin", "project-over-dropin", "forced-dropin"):
        path = project_path.parent / "config.d" / "local.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump({
            "machine": "first" if case == "dropin" else "foreign",
        }))
    fixture.args.force = case in ("forced-project", "forced-dropin", "forced-global")
    if case in ("explicit-vs-retained", "forced-global"):
        fixture.args.machine = "first"
    before = {str(p.relative_to(fixture.state)): p.read_bytes()
              for p in fixture.state.rglob("*") if p.is_file()}
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == (0 if allowed else 1)
    if allowed:
        config = cfg.load_config(
            project_path, project="registered",
            include_control_plane_related_pr=False, include_knowledge_preferences=False,
        )
        assert config.configured_machine == "first"
        assert config.machine == "first"
        assert global_path.read_bytes() == before[str(global_path.relative_to(fixture.state))]
    else:
        assert fixture.effects == []
        assert {str(p.relative_to(fixture.state)): p.read_bytes()
                for p in fixture.state.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize("case", [
    "fresh", "retained", "aliased-foreign", "aliased-map", "aliased-map-valid",
    "aliased-map-shadowed", "aliased-checkout", "aliased-checkout-valid",
    "aliased-checkout-forced", "retarget-absent-map", "missing-receipt", "foreign-receipt",
])
def test_namespaced_registration_checks_prospective_owned_state_before_writes(
    registration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, command: str, case: str,
) -> None:
    from agent_worktrees import project_adoption, project_state, registry_paths
    from test_registry_paths import _payload, _register_repo, _select, _stamp

    fixture = registration
    fixture.args.force = case == "aliased-checkout-forced"
    payload = _payload(tmp_path, "payload")
    with monkeypatch.context() as native:
        native.setattr(installation_cli.subprocess, "run", fixture.originals["subprocess_run"])
        receipt, _ = _stamp(
            tmp_path / "durable", payload,
            marketplace="registration-local", repository="Example/Registration-Local",
        )
    _select(monkeypatch, receipt, payload)
    monkeypatch.setattr(registry_paths, "installation_context", fixture.originals["installation_context"])
    monkeypatch.setattr(cfg, "project_dir", fixture.originals["project_dir"])
    monkeypatch.setattr(cfg, "global_config_path", fixture.originals["global_config_path"])
    monkeypatch.setattr(repos, "find_repo", fixture.originals["find_repo"])
    monkeypatch.setattr(repos, "add_repo", fixture.originals["add_repo"])
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    remote = "https://github.com/example/project.git"
    monkeypatch.setattr(
        installation_cli.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=remote, stderr=""),
    )

    def prepare(*args):
        fixture.effects.append("prepare")
        return project_adoption.prepare_namespaced_project_state(*args)

    fixture.overrides["_prepare_namespaced_project_state"] = prepare
    if case != "fresh":
        previous = "previous-label" if case.startswith("aliased") else "registered"
        _register_repo(
            previous, "https://github.com/example/previous.git" if case == "retarget-absent-map" else remote,
        )
        state = project_state.ensure_project_state(previous)
        (state / "config.yaml").write_text(yaml.safe_dump({
            "machine": "foreign-space" if case in ("aliased-foreign", "aliased-map") else "local-space",
            "repos": {"registered": {"anchor": str(fixture.repo)}},
        }))
        if case.startswith("aliased-checkout"):
            previous_checkout = tmp_path / "previous-checkout"
            previous_checkout.mkdir()
            (state / "config.yaml").write_text(yaml.safe_dump({
                "repo_name": previous, "machine": "local-space",
                "repos": {previous: {"anchor": str(previous_checkout)}},
            }))
            (previous_checkout / "machines.yaml").write_text(yaml.safe_dump({"machines": {
                "local-space": {
                    "physical_host": "friendly-host",
                    "execution_platform": platform if case == "aliased-checkout-valid" else (
                        "windows" if platform == "wsl" else "wsl"
                    ),
                },
            }}))
        identity_path = state.parent / "identity.json"
        if case == "missing-receipt":
            identity_path.unlink()
        elif case == "foreign-receipt":
            value = json.loads(identity_path.read_text())
            value["repositoryId"] = "repo-foreign"
            identity_path.write_text(json.dumps(value))
    other = "windows" if platform == "wsl" else "wsl"
    map_root = (
        state / cfg.INREPO_CONFIG_DIRNAME
        if case.startswith("aliased-map") or case == "retarget-absent-map" else fixture.repo
    )
    map_root.mkdir(parents=True, exist_ok=True)
    (map_root / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        "local-space": {
            "hostname": "raw-host", "physical_host": "friendly-host",
            "execution_platform": other if case == "aliased-map-shadowed" else platform,
        },
        "foreign-space": {
            "hostname": "other-host", "physical_host": "other-host", "execution_platform": other,
        },
    }}))
    if case == "aliased-map-shadowed":
        (fixture.repo / "machines.yaml").write_text(yaml.safe_dump({"machines": {"local-space": {
            "hostname": "raw-host", "physical_host": "friendly-host", "execution_platform": platform,
        }}}))
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    refused = case in (
        "aliased-foreign", "aliased-map", "aliased-checkout", "missing-receipt", "foreign-receipt",
    )
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == (1 if refused else 0)
    if refused:
        assert fixture.effects == []
        assert {str(p.relative_to(tmp_path)): p.read_bytes()
                for p in tmp_path.rglob("*") if p.is_file()} == before
        if case in ("aliased-foreign", "aliased-map"):
            assert "registered" not in repos.read_registry(strict=True).repos
    else:
        config = cfg.load_config(
            cfg.project_dir("registered") / "config.yaml", project="registered",
            include_control_plane_related_pr=False, include_knowledge_preferences=False,
        )
        expected = ("raw-host-wsl" if platform == "wsl" else "raw-host") if case == "retarget-absent-map" else "local-space"
        assert config.machine == expected
        if case == "retarget-absent-map":
            assert "instructions" not in fixture.effects
        assert "prepare" in fixture.effects


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize("tier", ["global", "project", "dropin"])
@pytest.mark.parametrize("foreign", [False, True])
@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("canonical", [False, True])
def test_registration_uses_target_bound_knowledge_map(
    registration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, command: str, tier: str, foreign: bool, force: bool, canonical: bool,
) -> None:
    from agent_worktrees import state_root

    fixture = registration
    fixture.args.force = force
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "unrelated")
    monkeypatch.setattr(cfg, "project_name", lambda: "unrelated")
    # install already has an explicit active project; register does not.
    if command == "install":
        monkeypatch.setattr(cfg, "project_name", lambda: "registered")
    monkeypatch.setattr(
        cfg, "project_dir", lambda name=None: fixture.state / (name or cfg.project_name()),
    )
    inrepo = fixture.repo / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    inrepo.parent.mkdir(parents=True)
    inrepo.write_text("stateless: true\n")
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    other = "windows" if platform == "wsl" else "wsl"
    (knowledge / "machines.yaml").write_text(yaml.safe_dump({"machines": {"opaque-space": {
        "hostname": "raw-host", "physical_host": "friendly-host",
        "execution_platform": other if foreign else platform,
    }}}))
    if canonical:
        path = knowledge / cfg.INREPO_CONFIG_DIRNAME / "machines.yaml"
        path.parent.mkdir(parents=True)
        path.write_text("machines: {}\n")
    monkeypatch.setattr(state_root, "_checkout_path", lambda name: str(knowledge))
    project_path = cfg.project_dir("registered") / "config.yaml"
    project_path.parent.mkdir(parents=True)
    target = {"repos": {"registered": {"anchor": str(fixture.repo)}}}
    if tier == "project":
        target.update(machine="opaque-space", knowledge_repo="knowledge")
    elif tier == "global":
        cfg.global_config_path().write_text(
            "machine: opaque-space\nknowledge_repo: knowledge\n",
        )
    else:
        dropin = project_path.parent / "config.d" / "local.yaml"
        dropin.parent.mkdir()
        dropin.write_text("machine: opaque-space\nknowledge_repo: knowledge\n")
    project_path.write_text(yaml.safe_dump(target))
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    dropped = force and tier == "project"
    refused = foreign and not dropped
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == (1 if refused else 0)
    if refused:
        assert fixture.effects == []
        assert {str(p.relative_to(tmp_path)): p.read_bytes()
                for p in tmp_path.rglob("*") if p.is_file()} == before
    else:
        config = cfg.load_config(
            project_path, project="registered",
            include_control_plane_related_pr=False, include_knowledge_preferences=False,
        )
        expected = ("raw-host-wsl" if platform == "wsl" else "raw-host") if dropped else "opaque-space"
        assert config.machine == expected
        assert config.configured_machine == (None if dropped else "opaque-space")
        assert ("instructions" in fixture.effects) is not dropped


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("command", ["register", "install"])
@pytest.mark.parametrize("map_kind", ["absent", "empty", "unrelated", "ambiguous", "malformed"])
def test_bound_knowledge_map_is_optional_but_invalid_declarations_refuse(
    registration, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, command: str, map_kind: str,
) -> None:
    from agent_worktrees import state_root

    fixture = registration
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    inrepo = fixture.repo / ".copilot-extensions" / "agent-worktrees" / "config.yaml"
    inrepo.parent.mkdir(parents=True)
    inrepo.write_text("stateless: true\n")
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    if map_kind in ("empty", "unrelated"):
        _map(knowledge, map_kind, platform)
    elif map_kind == "ambiguous":
        (knowledge / "machines.yaml").write_text(yaml.safe_dump({"machines": {key: {
            "hostname": "raw-host", "physical_host": "friendly-host",
            "execution_platform": platform,
        } for key in ("first", "second")}}))
    elif map_kind == "malformed":
        (knowledge / "machines.yaml").write_text("machines: [\n")
    monkeypatch.setattr(state_root, "_checkout_path", lambda name: str(knowledge))
    fixture.state.mkdir()
    cfg.global_config_path().write_text("knowledge_repo: knowledge\n")
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    refused = map_kind in ("ambiguous", "malformed")
    assert getattr(installation_cli, f"cmd_{command}")(fixture.args) == (1 if refused else 0)
    if refused:
        assert fixture.effects == []
        assert {str(p.relative_to(tmp_path)): p.read_bytes()
                for p in tmp_path.rglob("*") if p.is_file()} == before
    else:
        config = cfg.load_config(
            cfg.project_dir("registered") / "config.yaml", project="registered",
            include_control_plane_related_pr=False, include_knowledge_preferences=False,
        )
        assert config.configured_machine is None
        assert config.machine == ("raw-host-wsl" if platform == "wsl" else "raw-host")
