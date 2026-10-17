"""Legacy WSL record locality must not alias an explicit native machine target."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import claim_handoffs, claimant, claims_cli, config as cfg, tracking
from agent_worktrees.machine_identity import is_local_machine


@pytest.fixture(params=["unscoped", "projected"])
def legacy_records(tmp_path, monkeypatch, request):
    anchor = tmp_path / "source"
    anchor.mkdir()
    projected = request.param == "projected"
    (anchor / "machines.yaml").write_text(
        "machines:\n  example-host:\n    ssh:\n      environments:\n"
        "      - {name: windows, alias: native-profile}\n"
        "      - {name: wsl, alias: guest-profile}\n" if projected else
        "machines:\n  example-host: {}\n  example-host-wsl: {}\n",
        encoding="utf-8",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="example-host-wsl", platform="wsl",
        repo_name="example",
        repos={"example": cfg.RepoConfig(anchor=str(anchor), worktree_root=str(tmp_path / "trees"))},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda _project, **_kwargs: config)
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: tmp_path / (project or "example"))
    monkeypatch.setattr(cfg, "tracking_dir", lambda *_args, **_kwargs: tmp_path / "example" / "worktrees")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(claimant, "_owner_looks_dead", lambda _record: False)
    directory = tmp_path / "example" / "worktrees"
    directory.mkdir(parents=True)
    paths = {}
    for platform in ("wsl", "windows", "linux", ""):
        worktree_id = f"legacy-{platform or 'unknown'}"
        checkout = tmp_path / "trees" / worktree_id
        checkout.mkdir(parents=True)
        tracking.create_new_record(
            worktree_id, f"worktree/{worktree_id}", str(checkout), "example",
            "example-host", platform, directory,
        )
        paths[platform] = directory / f"{worktree_id}.yaml"
    child = tmp_path / "trees" / "legacy-child"
    child.mkdir()
    owner_ref = "example-host/example/legacy-wsl"
    tracking.create_new_record(
        "legacy-child", "worktree/legacy-child", str(child), "example",
        "example-host", "wsl", directory, owner_ref=owner_ref,
    )
    paths["child"] = directory / "legacy-child.yaml"
    snapshots = {path: path.read_bytes() for path in paths.values()}
    yield SimpleNamespace(config=config, paths=paths, owner_ref=owner_ref, projected=projected)
    assert all(path.read_bytes() == content for path, content in snapshots.items())


def test_legacy_wsl_record_is_still_readable_by_id_and_qualified_guest_ref(legacy_records):
    record = tracking.load_record_by_id("legacy-wsl")
    assert record.machine == "example-host"
    assert record.platform == "wsl"
    assert claimant.local_claimant_alive("example-host-wsl/example/legacy-wsl") is True
    path, _, error = claims_cli._resolve_owner_ref_record_path(
        "example-host-wsl/example/legacy-wsl", legacy_records.config,
    )
    assert error is None
    assert path == legacy_records.paths["wsl"]


@pytest.mark.parametrize("platform", ["windows", "linux", ""])
def test_native_target_and_non_wsl_legacy_records_remain_foreign(legacy_records, platform):
    assert not is_local_machine("example-host", legacy_records.config)
    worktree_id = f"legacy-{platform or 'unknown'}"
    ref = f"example-host/example/{worktree_id}"
    assert claimant.local_claimant_alive(ref) is None
    path, _, error = claims_cli._resolve_owner_ref_record_path(ref, legacy_records.config)
    assert path is None
    assert bool(error) is legacy_records.projected


def test_legacy_wsl_owner_ref_remains_locally_addressable(legacy_records):
    path, _, error = claims_cli._resolve_owner_ref_record_path(
        legacy_records.owner_ref, legacy_records.config,
    )
    assert error is None
    assert path == legacy_records.paths["wsl"]


def test_legacy_wsl_owner_ref_has_local_claimant_liveness(legacy_records):
    assert claimant.local_claimant_alive(legacy_records.owner_ref) is True


def test_historical_alias_uses_receiving_project_not_callers_topology(
    legacy_records, tmp_path,
):
    from agent_worktrees.legacy_wsl_record_locality import resolve_legacy_wsl_owner_ref

    receiving = Path(legacy_records.config.default_repo.anchor) / "machines.yaml"
    text = receiving.read_text()
    receiving.write_text(text.replace(
        "  example-host: {}", "  example-host: {alias: receiving-alias}",
    ).replace(
        "  example-host:\n", "  example-host:\n    alias: receiving-alias\n",
    ))
    caller_anchor = tmp_path / "caller-source"
    caller_anchor.mkdir()
    (caller_anchor / "machines.yaml").write_text(
        "machines:\n  example-host: {alias: caller-alias}\n  example-host-wsl: {}\n",
    )
    caller = replace(
        legacy_records.config, repo_name="caller",
        repos={"caller": cfg.RepoConfig(anchor=str(caller_anchor), worktree_root=str(tmp_path / "caller-trees"))},
    )
    ref = tracking.parse_claim_ref("receiving-alias/example/legacy-wsl")
    assert resolve_legacy_wsl_owner_ref(ref, caller) == legacy_records.paths["wsl"]
    assert resolve_legacy_wsl_owner_ref(ref, replace(caller, machine="foreign-host-wsl")) is None


@pytest.mark.parametrize("checkout_state", ["missing", "file", "foreign-symlink"])
def test_post_cleanup_legacy_locality_keeps_exact_namespace_and_redirect_fences(
    legacy_records, tmp_path, checkout_state,
):
    checkout = Path(tracking.load_record(legacy_records.paths["wsl"]).worktree_path)
    checkout.rmdir()
    if checkout_state == "file":
        checkout.write_text("not a checkout", encoding="utf-8")
    elif checkout_state == "foreign-symlink":
        foreign = tmp_path / "foreign"
        foreign.mkdir()
        try:
            checkout.symlink_to(foreign, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"host cannot create the contained symlink fixture: {exc}")
    resolved, _, _ = claims_cli._resolve_owner_ref_record_path(
        legacy_records.owner_ref, legacy_records.config,
    )
    assert (resolved == legacy_records.paths["wsl"]) is (checkout_state == "missing")
    assert claimant.local_claimant_alive(legacy_records.owner_ref) is (
        False if checkout_state == "missing" else None
    )


def test_legacy_wsl_owner_ref_never_uses_native_remote_death_probe(legacy_records, monkeypatch):
    probes = []

    def remote(machine, project, owner_ref, **_kwargs):
        probes.append((machine, project, owner_ref))
        return False

    monkeypatch.setattr(claimant, "_remote_claimant_alive", remote)
    result = claimant.resolve_claimant_alive(legacy_records.owner_ref)
    assert probes == [], "a live local WSL owner must not be probed as the native host"
    assert result is True


def test_legacy_wsl_record_remains_eligible_for_local_claim_handoff(legacy_records):
    ref = tracking.parse_claim_ref(legacy_records.owner_ref)
    path, record = claim_handoffs._load_actor_record(
        ref, role="consumer", machine=legacy_records.config.machine,
    )
    assert path == legacy_records.paths["wsl"]
    assert record.machine == "example-host"
    assert record.platform == "wsl"


def test_legacy_wsl_offer_accept_actor_equivalence(legacy_records):
    """A bundle actor recorded under the pre-split native-host spelling and
    the current newly-qualified actor must be recognized as the SAME
    worktree for offer-to-accept (``_same_worktree``/``_load_actor_record``),
    never just the exact-string consumer the bundle happened to carry."""
    legacy_actor = legacy_records.owner_ref  # "example-host/example/legacy-wsl"
    qualified_actor = tracking.format_claim_ref(
        legacy_records.config.machine, "example", "legacy-wsl",
    )
    assert claim_handoffs._same_worktree(qualified_actor, legacy_actor) is True
    assert claim_handoffs._same_worktree(legacy_actor, qualified_actor) is True
    # And the consumer-side finish check accepts the legacy spelling as local.
    path, record = claim_handoffs._load_actor_record(
        tracking.parse_claim_ref(legacy_actor), role="consumer",
        machine=legacy_records.config.machine,
    )
    assert path == legacy_records.paths["wsl"]
    assert record.platform == "wsl"


def test_legacy_wsl_locality_uses_actual_platform_not_configured_override(
    legacy_records, monkeypatch,
):
    """``config.platform`` is an overridable path-selection setting, not the
    real execution platform -- a WSL guest configured with ``platform:
    windows``/``linux`` for path resolution is still physically WSL, and the
    legacy exception must track reality (``detect_platform()``), not the
    override."""
    overridden = replace(legacy_records.config, platform="windows")
    path, _, error = claims_cli._resolve_owner_ref_record_path(
        legacy_records.owner_ref, overridden,
    )
    assert error is None
    assert path == legacy_records.paths["wsl"]
    assert claimant.local_claimant_alive(legacy_records.owner_ref) is True


def test_legacy_wsl_owner_ref_resolves_via_hostname_and_alias(tmp_path, monkeypatch):
    """A historical record can name the raw OS hostname or a configured
    alias, not just the registry's topology key -- resolution must go
    through the same topology sources used for identity qualification."""
    anchor = tmp_path / "source"
    anchor.mkdir()
    (anchor / "machines.yaml").write_text(
        "machines:\n"
        "  example-host:\n"
        "    hostname: generated-host\n"
        "    alias: example-transport\n"
        "  example-host-wsl: {}\n",
        encoding="utf-8",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="example-host-wsl", platform="wsl",
        repo_name="example",
        repos={"example": cfg.RepoConfig(anchor=str(anchor), worktree_root=str(tmp_path / "trees"))},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda _project, **_kwargs: config)
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: tmp_path / (project or "example"))
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(claimant, "_owner_looks_dead", lambda _record: False)
    directory = tmp_path / "example" / "worktrees"
    directory.mkdir(parents=True)
    checkout = tmp_path / "trees" / "legacy-hostname"
    checkout.mkdir(parents=True)
    # The record itself was written under the raw OS hostname, not the
    # topology key -- explicit trusted platform=wsl evidence still applies.
    tracking.create_new_record(
        "legacy-hostname", "worktree/legacy-hostname", str(checkout), "example",
        "generated-host", "wsl", directory,
    )
    path = directory / "legacy-hostname.yaml"
    for owner_ref in (
        "generated-host/example/legacy-hostname",
        "example-transport/example/legacy-hostname",
    ):
        resolved, _, error = claims_cli._resolve_owner_ref_record_path(owner_ref, config)
        assert error is None
        assert resolved == path
        assert claimant.local_claimant_alive(owner_ref) is True


@pytest.mark.parametrize("mismatch", ["worktree", "project", "namespace", "relative-path"])
def test_legacy_record_evidence_must_match_exact_reference_and_namespace(
    legacy_records: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, mismatch: str,
) -> None:
    path = legacy_records.paths["wsl"]
    original_load = tracking.load_record
    record = original_load(path)
    changes = {
        "worktree": {"worktree_id": "other-worktree"},
        "project": {"repo": "other-project"},
        "namespace": {"worktree_path": str(Path(record.worktree_path).parent)},
        "relative-path": {"worktree_path": "legacy-wsl"},
    }
    record = replace(record, **changes[mismatch])
    monkeypatch.setattr(
        tracking, "load_record",
        lambda candidate: record if candidate == path else original_load(candidate),
    )
    resolved, _, error = claims_cli._resolve_owner_ref_record_path(
        legacy_records.owner_ref, legacy_records.config,
    )
    assert resolved is None
    assert bool(error) is legacy_records.projected
    assert claimant.local_claimant_alive(legacy_records.owner_ref) is None
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="cross-machine"):
        claim_handoffs._load_actor_record(
            tracking.parse_claim_ref(legacy_records.owner_ref),
            role="consumer", machine=legacy_records.config.machine,
        )


def test_registered_execution_space_never_uses_legacy_record_exception(
    legacy_records: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = {
        key: cfg.MachineEntry(key=key, display_name=key, execution_platform=platform)
        for key, platform in (("example-host", "windows"), ("example-host-wsl", "wsl"))
    }
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda _anchor, **_kwargs: entries)
    resolved, _, error = claims_cli._resolve_owner_ref_record_path(
        legacy_records.owner_ref, legacy_records.config,
    )
    assert resolved is None and error
    assert claimant.local_claimant_alive(legacy_records.owner_ref) is None
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="cross-machine"):
        claim_handoffs._load_actor_record(
            tracking.parse_claim_ref(legacy_records.owner_ref),
            role="consumer", machine=legacy_records.config.machine,
        )


def test_legacy_record_cannot_infer_namespace_from_current_directory(
    legacy_records: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(legacy_records.config.srcroot)
    receiving_config = replace(
        legacy_records.config,
        repos={"example": replace(legacy_records.config.default_repo, worktree_root="trees")},
    )
    monkeypatch.setattr(cfg, "load_project_config", lambda _project, **_kwargs: receiving_config)
    resolved, _, error = claims_cli._resolve_owner_ref_record_path(
        legacy_records.owner_ref, legacy_records.config,
    )
    assert resolved is None
    assert bool(error) is legacy_records.projected
    assert claimant.local_claimant_alive(legacy_records.owner_ref) is None


def test_legacy_child_cannot_authorize_registered_parent_ledger(
    legacy_records: SimpleNamespace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import execution_spaces, finalize

    anchor = tmp_path / "registered-parent"
    anchor.mkdir()
    (anchor / "machines.yaml").write_text(
        "machines:\n"
        "  example-host:\n    execution_platform: windows\n"
        "  example-host-wsl:\n    execution_platform: wsl\n",
        encoding="utf-8",
    )
    receiving_config = replace(
        legacy_records.config, repo_name="parent",
        repos={"parent": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(tmp_path / "trees"),
        )},
    )
    directory = tmp_path / "parent" / "worktrees"
    directory.mkdir(parents=True)
    checkout = tmp_path / "trees" / "legacy-parent"
    checkout.mkdir()
    parent = tracking.create_new_record(
        "legacy-parent", "worktree/legacy-parent", str(checkout), "parent",
        "example-host", "wsl", directory,
    )
    child_ref = "example-host/example/legacy-child"
    parent.resources = [tracking.ResourceClaim(kind="worktree", ref=child_ref)]
    path = directory / "legacy-parent.yaml"
    tracking.save_record(parent, path)
    before = path.read_bytes()
    monkeypatch.setattr(
        cfg, "load_project_config",
        lambda project, **_kwargs: receiving_config
        if project == "parent" else legacy_records.config,
    )
    old_ref = "example-host/parent/legacy-parent"
    resolved, _, error = claims_cli._resolve_owner_ref_record_path(old_ref, legacy_records.config)
    assert resolved is None
    assert bool(error) is legacy_records.projected
    assert claimant.local_claimant_alive(old_ref) is None
    with pytest.raises(claim_handoffs.ClaimHandoffError, match="cross-machine"):
        claim_handoffs._load_actor_record(
            tracking.parse_claim_ref(old_ref), role="consumer",
            machine=legacy_records.config.machine,
        )
    child = replace(tracking.load_record(legacy_records.paths["child"]), owner_ref=old_ref)
    if legacy_records.projected:
        with pytest.raises(execution_spaces.ExecutionSpaceError, match="parent ownership"):
            finalize._settle_parent_obligation(child, legacy_records.config, "legacy-child")
    else:
        finalize._settle_parent_obligation(child, legacy_records.config, "legacy-child")
    assert path.read_bytes() == before
    child.owner_ref = "example-host-wsl/parent/legacy-parent"
    with pytest.raises(execution_spaces.ExecutionSpaceError):
        finalize._settle_parent_obligation(child, legacy_records.config, "legacy-child")
    assert path.read_bytes() == before
