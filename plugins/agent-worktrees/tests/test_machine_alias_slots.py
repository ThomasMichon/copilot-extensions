"""Alias grid through actual config, record, reference and handoff adapters."""

from __future__ import annotations
import base64
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from machine_transport import legacy_alias_forms, split_machine_environment

from agent_worktrees import (
    claim_handoffs, claimant, config as cfg, execution_spaces,
    legacy_wsl_record_locality, resolve_cli, tracking,
    resolve_machine_cli,
)


CELLS = [
    (platform, alias)
    for platform in ("windows", "wsl")
    for base in ("Example-Host", "Generated-Host", "Legacy-Alias", "Example Workstation")
    for form in legacy_alias_forms(base, platform)
    for alias in (form, form.swapcase())
]


@pytest.fixture
def world(tmp_path, monkeypatch):
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    state = tmp_path / "state" / "example"
    directory = state / "worktrees"
    directory.mkdir(parents=True)
    namespace = tmp_path / "trees"
    namespace.mkdir()
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: state)
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(cfg, "_ACTIVE_PROJECT", "example")
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda _anchor: {})
    monkeypatch.setattr(claimant, "_owner_looks_dead", lambda _record: False)
    return SimpleNamespace(
        anchor=anchor, state=state, directory=directory, namespace=namespace,
        config_path=state / "config.yaml",
    )


@pytest.fixture
def parsed_topology_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse each cell's actual topology once; changed bytes remain fresh."""
    original = cfg._parse_machines_yaml_file
    parsed: dict[tuple[Path, bool, bytes], dict[str, cfg.MachineEntry]] = {}

    def read(path: str | Path, *, require_alias: bool = True) -> dict[str, cfg.MachineEntry]:
        source = Path(path)
        key = (source.resolve(), require_alias, source.read_bytes())
        if key not in parsed:
            parsed[key] = original(source, require_alias=require_alias)
        return deepcopy(parsed[key])

    monkeypatch.setattr(cfg, "_parse_machines_yaml_file", read)


def _topology(world, explicit):
    host = {
        "hostname": "Generated-Host", "display_name": "Example Workstation",
        "alias": "Legacy-Alias", "ssh": {"environments": [
            {"name": "windows", "alias": "native-profile"},
            {"name": "wsl", "alias": "guest-profile"},
        ]},
    }
    if explicit:
        host["execution_platform"] = "windows"
        host["physical_host"] = "Example-Host"
        host["ssh"]["environments"] = host["ssh"]["environments"][:1]
        machines = {"Example-Host": host, "Example-Host-wsl": {
            "execution_platform": "wsl", "physical_host": "Example-Host",
            "hostname": "Generated-Host", "display_name": "Example Workstation",
            "ssh": {"environments": [{"name": "wsl", "alias": "guest-profile"}]},
        }}
    else:
        machines = {"Example-Host": host}
    (world.anchor / "machines.yaml").write_text(
        yaml.safe_dump({"machines": machines}), encoding="utf-8",
    )


@pytest.mark.parametrize("actual", ["windows", "wsl"])
@pytest.mark.parametrize("name", ["example-host", "EXAMPLE-HOST"])
@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.guard
def test_overlapping_key_profile_config_load_and_automatic_detection(
    world, monkeypatch, actual, name, configured,
):
    (world.anchor / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        "example-host": {"ssh": {"environments": [
            {"name": "windows", "alias": name},
            {"name": "wsl", "alias": "guest-login"},
        ]}},
    }}), encoding="utf-8")
    monkeypatch.setattr(cfg, "detect_platform", lambda: actual)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: name)
    document = {
        "repo_name": "example", "platform": actual,
        "repos": {"example": {
            "anchor": str(world.anchor), "worktree_root": str(world.namespace),
        }},
    }
    if configured:
        document["machine"] = name
    world.config_path.write_text(yaml.safe_dump(document), encoding="utf-8")
    before = world.config_path.read_bytes()
    config = cfg.load_config(world.config_path, include_control_plane_related_pr=False)
    expected = "example-host-wsl" if actual == "wsl" else "example-host"
    assert config.machine == expected
    assert cfg.detect_machine(world.anchor) == expected
    assert cfg.detect_machine(world.anchor, value=name) == expected
    execution_spaces.require_current_execution_space(config)
    assert world.config_path.read_bytes() == before


@pytest.mark.parametrize("actual,configured", [
    ("wsl", "example-host"), ("windows", "guest-login"),
])
@pytest.mark.guard
def test_overlapping_key_profile_does_not_relax_explicit_configured_space(world, monkeypatch, actual, configured):
    (world.anchor / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        "example-host": {
            "execution_platform": "windows", "physical_host": "example-host",
            "ssh": {"environments": [{"name": "windows", "alias": "example-host"}]},
        },
        "example-host-wsl": {
            "execution_platform": "wsl", "physical_host": "example-host",
            "ssh": {"environments": [{"name": "wsl", "alias": "guest-login"}]},
        },
    }}), encoding="utf-8")
    monkeypatch.setattr(cfg, "detect_platform", lambda: actual)
    world.config_path.write_text(yaml.safe_dump({
        "repo_name": "example", "machine": configured, "platform": actual,
        "repos": {"example": {
            "anchor": str(world.anchor), "worktree_root": str(world.namespace),
        }},
    }), encoding="utf-8")
    before = world.config_path.read_bytes()
    with pytest.raises(ValueError, match="conflicts"):
        cfg.load_config(world.config_path, include_control_plane_related_pr=False)
    assert world.config_path.read_bytes() == before


@pytest.mark.parametrize("actual", ["windows", "wsl"])
@pytest.mark.parametrize("observed", ["Observed-Host", "oBSERVED-hOST"])
@pytest.mark.guard
def test_observed_hostname_without_metadata_keeps_record_and_launch_local(
    world, monkeypatch, actual, observed,
):
    (world.anchor / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        "Preferred-Host": {"ssh": {"environments": [
            {"name": "windows", "alias": "native-profile"},
            {"name": "wsl", "alias": "guest-profile"},
        ]}},
    }}), encoding="utf-8")
    monkeypatch.setattr(cfg, "detect_platform", lambda: actual)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host")
    world.config_path.write_text(yaml.safe_dump({
        "repo_name": "example", "machine": "Preferred-Host", "platform": actual,
        "repos": {"example": {
            "anchor": str(world.anchor), "worktree_root": str(world.namespace),
        }},
    }), encoding="utf-8")
    config = cfg.load_config(world.config_path, include_control_plane_related_pr=False)
    checkout = world.namespace / "observed"
    checkout.mkdir()
    record = tracking.create_new_record(
        "observed", "worktree/observed", str(checkout), "example",
        observed, actual, world.directory,
    )
    before = record.yaml_path.read_bytes()
    ref = tracking.parse_claim_ref(f"{observed}/example/observed")
    assert legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(ref, config) == record.yaml_path
    execution_spaces.require_record_mutation(record, config)
    assert resolve_cli._target_is_local(config, observed, actual)
    other = "wsl" if actual == "windows" else "windows"
    assert not resolve_cli._target_is_local(config, observed, other)
    assert record.yaml_path.read_bytes() == before
    registered = yaml.safe_load((world.anchor / "machines.yaml").read_text(encoding="utf-8"))
    registered["machines"]["Observed-Host"] = {
        "execution_platform": actual, "physical_host": "foreign-host",
    }
    (world.anchor / "machines.yaml").write_text(yaml.safe_dump(registered), encoding="utf-8")
    assert not resolve_cli._target_is_local(config, observed, actual)
    assert legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(ref, config) is None
    assert record.yaml_path.read_bytes() == before


@pytest.mark.parametrize("explicit", [False, True], ids=["legacy-record", "new-record"])
@pytest.mark.parametrize("platform,alias", CELLS)
@pytest.mark.usefixtures("parsed_topology_cache")
@cfg.cached_load_config_scope()
def test_alias_slot_grid_preserves_strings_and_scopes(world, monkeypatch, explicit, platform, alias):
    """Share one immutable config snapshot; record and handoff writes remain live."""
    _topology(world, explicit)
    before_topology = (world.anchor / "machines.yaml").read_bytes()
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    world.config_path.write_text(yaml.safe_dump({
        "repo_name": "example", "machine": alias, "platform": platform,
        "repos": {"example": {
            "anchor": str(world.anchor), "worktree_root": str(world.namespace),
        }},
    }), encoding="utf-8")
    before_config = world.config_path.read_bytes()
    config = cfg.load_config(world.config_path, include_control_plane_related_pr=False)
    expected = "Example-Host-wsl" if platform == "wsl" else "Example-Host"
    assert config.machine == expected
    assert cfg.detect_machine(world.anchor, value=alias) == expected
    assert world.config_path.read_bytes() == before_config
    assert resolve_cli._target_is_local(config, alias, platform)
    parsed = split_machine_environment(alias)
    if not parsed.environment and parsed.machine.casefold() in ("generated-host", "example workstation"):
        with pytest.raises(ValueError, match="does not select an execution space"):
            resolve_cli._target_is_local(config, alias)
    else:
        assert resolve_cli._target_is_local(config, alias)
    # Allocation is the external side effect; the real JSON selector must pass
    # only this receiving namespace to it.
    allocated = []
    monkeypatch.setattr(resolve_cli, "_validate_profile_assignment_config", lambda *_args: None)
    monkeypatch.setattr(resolve_cli, "_resolve_profile", lambda *_args: None)
    monkeypatch.setattr(
        resolve_cli, "_create_worktree_core",
        lambda receiving, **_kwargs: allocated.append(receiving) or {"action": "exec"},
    )
    state = resolve_cli.ResolveCommandState.from_args(SimpleNamespace(
        json=True, new_worktree=True, machine=alias, environment=platform, worktree_id=None,
    ))
    state.config = config
    assert resolve_cli._resolve_json_mode(state) == 0
    assert allocated == [config] and allocated[0].machine == expected

    refs = {name: tracking.format_claim_ref(alias, "example", name)
            for name in ("parent", "consumer", "child", "settled-child")}
    paths = {}
    for name in refs:
        checkout = world.namespace / name
        checkout.mkdir()
        record = tracking.create_new_record(
            name, f"worktree/{name}", str(checkout), "example", alias, platform, world.directory,
            owner_ref=refs["parent"] if name in ("child", "settled-child") else None,
        )
        paths[name] = record.yaml_path
        execution_spaces.require_record_mutation(record, config)
        path, worktree_id, error = execution_spaces.resolve_owner_record_path(refs[name], config)
        assert (path, worktree_id, error) == (paths[name], name, None)
        assert claimant.local_claimant_alive(refs[name]) is True
        assert claimant.resolve_claimant_alive(refs[name], allow_remote=False) is True
        assert legacy_wsl_record_locality.claim_refs_equivalent(
            tracking.parse_claim_ref(refs[name]),
            tracking.parse_claim_ref(tracking.format_claim_ref(expected, "example", name)),
        )

    parent = tracking.load_record(paths["parent"])
    parent.resources = [
        tracking.ResourceClaim(kind="worktree", ref=refs["child"]),
        tracking.ResourceClaim(kind="worktree", ref=refs["settled-child"]),
        tracking.ResourceClaim(kind="pr", ref="example-org/transition#1"),
    ]
    tracking.save_record(parent, paths["parent"])
    messages = []
    execution_spaces.settle_parent_obligation(
        tracking.load_record(paths["settled-child"]), config, "settled-child",
        output=SimpleNamespace(ok=messages.append, warn=pytest.fail),
    )
    assert len(messages) == 1
    assert tracking.load_record(paths["parent"]).resources[1].state == "at-rest"
    declined, _ = claim_handoffs.offer(
        refs["parent"], refs["consumer"], ["example-org/transition#1"], machine=expected,
    )
    declined = claim_handoffs.transition(
        declined.bundle_id, actor=refs["consumer"], action="declined", reason="matrix refusal",
    )
    assert declined.state == "declined"
    assert (declined.source, declined.consumer) == (refs["parent"], refs["consumer"])
    bundle, _ = claim_handoffs.offer(
        refs["parent"], refs["consumer"], [refs["child"]], machine=expected,
    )
    accepted = claim_handoffs.accept(
        bundle.bundle_id, actor=f"{expected}/example/consumer", machine=expected,
    )
    assert accepted.state == "accepted"
    assert accepted.source == refs["parent"] and accepted.consumer == refs["consumer"]
    assert tracking.load_record(paths["child"]).owner_ref == refs["consumer"]
    for path in paths.values():
        record = tracking.load_record(path)
        assert (record.machine, record.platform) == (alias, platform)
    assert world.config_path.read_bytes() == before_config
    assert (world.anchor / "machines.yaml").read_bytes() == before_topology


def test_topology_fixture_returns_independent_results_and_reloads_changed_bytes(
    world, parsed_topology_cache,
):
    _topology(world, False)
    source = world.anchor / "machines.yaml"
    first = cfg._parse_machines_yaml_file(source, require_alias=False)
    first.clear()
    assert cfg._parse_machines_yaml_file(source, require_alias=False)
    document = yaml.safe_load(source.read_text(encoding="utf-8"))
    document["machines"]["Example-Host"]["hostname"] = "Alternate-Host"
    source.write_text(yaml.safe_dump(document), encoding="utf-8")
    updated = cfg._parse_machines_yaml_file(source, require_alias=False)
    assert updated["Example-Host"].hostname == "Alternate-Host"


@pytest.mark.parametrize("platform,foreign", [("windows", "wsl"), ("wsl", "windows")])
def test_record_context_conflict_never_licenses_foreign_space(world, monkeypatch, platform, foreign):
    _topology(world, True)
    current = "Example-Host-wsl" if platform == "wsl" else "Example-Host"
    other = "Example-Host" if platform == "wsl" else "Example-Host-wsl"
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    config = cfg.Config(
        machine=current, platform=platform, repo_name="example", srcroot="",
        repos={"example": cfg.RepoConfig(
            anchor=str(world.anchor), worktree_root=str(world.namespace),
        )},
    )
    monkeypatch.setattr(cfg, "load_project_config", lambda *_args, **_kwargs: config)
    checkout = world.namespace / "foreign"
    checkout.mkdir()
    record = tracking.create_new_record(
        "foreign", "worktree/foreign", str(checkout), "example", other, foreign, world.directory,
    )
    before = record.yaml_path.read_bytes()
    with pytest.raises(execution_spaces.ExecutionSpaceError):
        execution_spaces.require_record_mutation(record, config)
    ref = tracking.parse_claim_ref(f"{other}/example/foreign")
    assert legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(ref, config) is None
    assert claimant.local_claimant_alive(f"{other}/example/foreign") is None
    assert record.yaml_path.read_bytes() == before
    corrupted = tracking.create_new_record(
        "corrupted", "worktree/corrupted", str(checkout), "example", current, foreign, world.directory,
    )
    assert claimant.local_claimant_alive(f"{current}/example/corrupted") is None
    with pytest.raises(execution_spaces.ExecutionSpaceError):
        execution_spaces.require_record_mutation(corrupted, config)


@pytest.mark.parametrize("platform", ["windows", "wsl"])
def test_same_physical_platform_does_not_alias_an_independent_registered_space(world, monkeypatch, platform):
    _topology(world, True)
    topology = world.anchor / "machines.yaml"
    data = yaml.safe_load(topology.read_text(encoding="utf-8"))
    data["machines"]["other-space"] = {"execution_platform": platform, "physical_host": "Example-Host"}
    topology.write_text(yaml.safe_dump(data), encoding="utf-8")
    current = "Example-Host-wsl" if platform == "wsl" else "Example-Host"
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    config = cfg.Config(
        machine=current, platform=platform, repo_name="example", srcroot="",
        repos={"example": cfg.RepoConfig(
            anchor=str(world.anchor), worktree_root=str(world.namespace),
        )},
    )
    monkeypatch.setattr(cfg, "load_project_config", lambda *_args, **_kwargs: config)
    checkout = world.namespace / "other"
    checkout.mkdir()
    record = tracking.create_new_record(
        "other", "worktree/other", str(checkout), "example", "other-space", platform, world.directory,
    )
    before = record.yaml_path.read_bytes()
    with pytest.raises(execution_spaces.ExecutionSpaceError):
        execution_spaces.require_record_mutation(record, config)
    assert legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(
        tracking.parse_claim_ref("other-space/example/other"), config,
    ) is None
    assert record.yaml_path.read_bytes() == before


@pytest.mark.parametrize("target_platform,alias", CELLS)
@pytest.mark.parametrize("json_mode", [False, True], ids=["non-json", "json"])
def test_contextual_launch_alias_grid_uses_exact_foreign_profile(
    world, monkeypatch, target_platform, alias, json_mode,
):
    _topology(world, True)
    actual = "wsl" if target_platform == "windows" else "windows"
    current = "Example-Host-wsl" if actual == "wsl" else "Example-Host"
    config = cfg.Config(
        machine=current, platform=actual, repo_name="example", srcroot="",
        repos={"example": cfg.RepoConfig(
            anchor=str(world.anchor), worktree_root=str(world.namespace),
        )},
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: actual)
    monkeypatch.setattr(cfg, "load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(resolve_cli.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(resolve_machine_cli, "_in_ssh_session", lambda: False)
    plans = []
    monkeypatch.setattr(resolve_machine_cli, "_emit_plan", plans.append)
    monkeypatch.setattr(
        resolve_cli, "_resolve_noninteractive_worktree",
        lambda *_args: pytest.fail("foreign tuple must not read the local tracking record"),
    )
    args = SimpleNamespace(
        json=json_mode, worktree_id="target", machine=alias,
        environment=target_platform, new_worktree=False,
    )
    assert resolve_cli.cmd_resolve(args) == 0
    plan, = plans
    expected = "Example-Host-wsl" if target_platform == "wsl" else "Example-Host"
    assert plan["machine"] == expected
    assert plan["ssh_alias"] == ("guest-profile" if target_platform == "wsl" else "native-profile")
    command = plan["remote_command"]
    if "-EncodedCommand " in command:
        script = base64.b64decode(command.rsplit(" ", 1)[1]).decode("utf-16le")
        assert "'--worktree-id' 'target'" in script
    else:
        assert "--worktree-id target" in command
