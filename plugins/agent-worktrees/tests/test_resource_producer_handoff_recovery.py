"""Disposable producer handoff collision and accepted-retry recovery contracts."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
import yaml
from test_resource_producer import absence, bind, call, reserve
from test_resource_producer import owner as owner

from agent_worktrees import claim_handoffs as handoffs
from agent_worktrees import claim_providers, config as cfg, tracking
from agent_worktrees import resource_producer as producer
from agent_worktrees.producer_handoff import _immutable_metadata


@pytest.fixture
def handoff_owner(owner, tmp_path, monkeypatch):
    monkeypatch.setattr(handoffs, "registry_path", lambda: tmp_path / "handoffs.yaml")
    monkeypatch.setattr(handoffs.claim_history, "record_bundle_transfer", lambda *a, **k: None)
    return owner[0]


def _offer(path):
    bound = bind(path, reserve(path))
    bundle, _ = handoffs.offer(
        "m/p/owner", "m/p/consumer", [bound["ref"]], machine="m",
    )
    return bundle, bound


def _accept(bundle):
    return handoffs.accept(bundle.bundle_id, actor="m/p/consumer", machine="m")


def _rewrite(path, mutate):
    raw = yaml.safe_load(path.read_text())
    mutate(raw)
    path.write_text(yaml.safe_dump(raw))


def _assert_rejected_unchanged(path, bundle, match):
    paths = (path, path.with_name("consumer.yaml"), handoffs.registry_path())
    before = {target: target.read_bytes() for target in paths}
    with pytest.raises(handoffs.ClaimHandoffError, match=match):
        _accept(bundle)
    assert {target: target.read_bytes() for target in paths} == before
    assert handoffs.show(bundle.bundle_id).state == "offered"


def _different_ref(snapshot):
    changed = copy.deepcopy(snapshot)
    changed["producer"]["identity"] = {"immutable_id": "different"}
    changed["ref"] = producer.claim_ref(changed["producer"])
    assert changed["ref"] != snapshot["ref"]
    return changed


def _crash_after_accepted(path, monkeypatch):
    bundle, bound = _offer(path)
    consumer_path = path.with_name("consumer.yaml")
    save = tracking.save_record

    def crash(record, target=None, **kwargs):
        if target == consumer_path and not record.resources[0].handoff_bundle:
            assert handoffs.show(bundle.bundle_id).state == "accepted"
            raise OSError("injected accepted marker cleanup failure")
        return save(record, target, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(tracking, "save_record", crash)
        with pytest.raises(handoffs.ClaimHandoffError, match="marker cleanup failure"):
            _accept(bundle)
    assert handoffs.show(bundle.bundle_id).state == "accepted"
    assert tracking.load_record(consumer_path).resources[0].handoff_bundle == bundle.bundle_id
    assert tracking.load_record(path).resources[0].producer["phase"] == "transferred"
    return bundle, bound


def test_resource_producer_recipient_distinct_ref_token_collision(handoff_owner):
    path = handoff_owner
    bundle, _ = _offer(path)
    collision = _different_ref(bundle.claims[0])
    _rewrite(
        path.with_name("consumer.yaml"),
        lambda raw: raw.update(resources=[collision]),
    )
    _assert_rejected_unchanged(path, bundle, "token collision")


def test_resource_producer_incoming_duplicate_tokens_rejected_before_accepting(handoff_owner):
    path = handoff_owner
    bundle, _ = _offer(path)
    duplicate = _different_ref(bundle.claims[0])
    bundle = replace(bundle, claims=(*bundle.claims, duplicate))
    handoffs._save_registry(handoffs.registry_path(), [bundle])
    _assert_rejected_unchanged(path, bundle, "duplicate producer token")


def test_resource_producer_duplicate_tokens_rejected_before_offer(handoff_owner):
    path = handoff_owner
    bound = bind(path, reserve(path))
    snapshot = handoffs._claim_snapshot(tracking.load_record(path).resources[0])
    duplicate = _different_ref(snapshot)
    _rewrite(path, lambda raw: raw["resources"].append(duplicate))
    consumer_path = path.with_name("consumer.yaml")
    before = (path.read_bytes(), consumer_path.read_bytes())
    with pytest.raises(handoffs.ClaimHandoffError, match="duplicate producer token"):
        handoffs.offer(
            "m/p/owner", "m/p/consumer", [bound["ref"], duplicate["ref"]], machine="m",
        )
    assert (path.read_bytes(), consumer_path.read_bytes()) == before
    assert not handoffs.registry_path().exists()


@pytest.mark.parametrize("distinct_ref", [False, True])
@pytest.mark.parametrize("ledger", ["owner.yaml", "consumer.yaml"])
def test_resource_producer_duplicate_ledger_preserved(handoff_owner, distinct_ref, ledger):
    path = handoff_owner
    bundle, _ = _offer(path)
    original = copy.deepcopy(bundle.claims[0])
    if ledger == "consumer.yaml":
        original["note"] = handoffs._note_with_provenance(original["note"], bundle.source)
    duplicate = _different_ref(original) if distinct_ref else copy.deepcopy(original)

    def corrupt(raw):
        if ledger == "consumer.yaml":
            raw["resources"] = [original]
        raw["resources"].append(duplicate)

    _rewrite(path.with_name(ledger), corrupt)
    _assert_rejected_unchanged(path, bundle, "duplicate producer")


@pytest.mark.parametrize("source_status", ["gone", "claim-gone", "archived", "finalized"])
def test_resource_producer_accepted_retry_does_not_require_source(
    handoff_owner, monkeypatch, source_status,
):
    path = handoff_owner
    bundle, bound = _crash_after_accepted(path, monkeypatch)
    if source_status == "gone":
        source_path = path.with_name("reaped-source.yaml")
        path.rename(source_path)
    else:
        source_path = path
        if source_status == "claim-gone":
            _rewrite(path, lambda raw: raw.update(resources=[]))
        else:
            _rewrite(path, lambda raw: raw.update(status=source_status))
    before_source = source_path.read_bytes()
    before_registry = handoffs.registry_path().read_bytes()
    record_path = handoffs._record_path

    def consumer_only(ref):
        assert ref.worktree_id == "consumer", "accepted retry accessed its source"
        return record_path(ref)

    monkeypatch.setattr(handoffs, "_record_path", consumer_only)
    assert _accept(bundle).state == "accepted"
    consumer_path = path.with_name("consumer.yaml")
    received = tracking.load_record(consumer_path).resources[0]
    assert received.producer == bound["producer"]
    assert received.handoff_bundle == ""
    assert source_path.read_bytes() == before_source
    assert handoffs.registry_path().read_bytes() == before_registry
    after = consumer_path.read_bytes()
    assert _accept(bundle).state == "accepted"
    assert consumer_path.read_bytes() == after
    monkeypatch.setattr(claim_providers, "resolve_claim_status", absence)
    closed = call(consumer_path, "close", {"token": bound["token"]}, ref="m/p/consumer")
    assert closed.get("ok") and closed["phase"] == "closed"


@pytest.mark.parametrize("phase", ["closed", "transferred"])
@pytest.mark.parametrize("marker", ["", "original", "onward"])
def test_resource_producer_accepted_retry_preserves_terminal_consumer(
    handoff_owner, monkeypatch, phase, marker,
):
    path = handoff_owner
    bundle, bound = _crash_after_accepted(path, monkeypatch)
    consumer_path = path.with_name("consumer.yaml")
    metadata = copy.deepcopy(bound["producer"])
    metadata["phase"] = phase
    if phase == "closed":
        metadata["closeout"] = absence(bound["ref"])
    else:
        metadata["transfer"] = {
            "accepted": True, "bundle": "onward",
            "source": "m/p/consumer", "consumer": "m/p/next",
        }
    assert producer.valid_metadata(metadata)
    handoff_marker = bundle.bundle_id if marker == "original" else marker

    def terminal(raw):
        raw["status"] = "finalized"
        raw["resources"][0].update(
            producer=metadata, state="released", handoff_bundle=handoff_marker,
        )

    _rewrite(consumer_path, terminal)
    before_source = path.read_bytes()
    before_registry = handoffs.registry_path().read_bytes()
    before_consumer = consumer_path.read_bytes()
    assert _accept(bundle).state == "accepted"
    received = tracking.load_record(consumer_path).resources[0]
    assert received.producer == metadata
    assert received.state == "released" and not received.is_unsettled
    assert received.handoff_bundle == ("" if marker == "original" else marker)
    assert tracking.load_record(consumer_path).status == "finalized"
    assert path.read_bytes() == before_source
    assert handoffs.registry_path().read_bytes() == before_registry
    if marker != "original":
        assert consumer_path.read_bytes() == before_consumer


@pytest.mark.parametrize("field,value", [
    ("creator", "m/p/other"),
    ("identity", {"immutable_id": "replacement"}),
    ("recovery", {"endpoint": "replacement"}),
    ("allocation_epoch", "replacement"),
])
def test_resource_producer_accepted_retry_rejects_immutable_changes(
    handoff_owner, monkeypatch, field, value,
):
    path = handoff_owner
    bundle, _ = _crash_after_accepted(path, monkeypatch)
    consumer_path = path.with_name("consumer.yaml")
    _rewrite(
        consumer_path,
        lambda raw: raw["resources"][0]["producer"].update({field: value}),
    )
    paths = (path, consumer_path, handoffs.registry_path())
    before = {target: target.read_bytes() for target in paths}
    with pytest.raises(handoffs.ClaimHandoffError, match="metadata is invalid"):
        _accept(bundle)
    assert {target: target.read_bytes() for target in paths} == before


def test_resource_producer_immutable_comparison_retains_optional_fields():
    metadata = {
        "creator": "m/p/owner", "identity": {"immutable_id": "id"},
        "allocation_epoch": "epoch", "phase": "bound",
    }
    terminal = {**metadata, "phase": "closed", "closeout": {"receipt": True}}
    assert _immutable_metadata(terminal) == _immutable_metadata(metadata)
    assert _immutable_metadata(terminal)["allocation_epoch"] == "epoch"
    assert _immutable_metadata({**metadata, "allocation_epoch": "other"}) != (
        _immutable_metadata(metadata)
    )


@pytest.fixture(params=[False, True], ids=["historical-consumer", "qualified-consumer"])
def historical_producer(tmp_path, monkeypatch, request):
    anchor, namespace = tmp_path / "anchor", tmp_path / "trees"
    anchor.mkdir()
    (anchor / "machines.yaml").write_text("machines: {}\n", encoding="utf-8")
    records = tmp_path / "state" / "worktrees"
    records.mkdir(parents=True)
    config = cfg.Config(
        srcroot=str(tmp_path), machine="example-host", platform="wsl", repo_name="p",
        repos={"p": cfg.RepoConfig(anchor=str(anchor), worktree_root=str(namespace))},
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: records.parent)
    monkeypatch.setattr(cfg, "tracking_dir", lambda: records)
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *a, **k: config)
    monkeypatch.setattr(handoffs, "registry_path", lambda: tmp_path / "handoffs.yaml")
    monkeypatch.setattr(handoffs.claim_history, "record_bundle_transfer", lambda *a, **k: None)
    for name in ("owner", "consumer"):
        checkout = namespace / name
        checkout.mkdir(parents=True)
        tracking.create_new_record(
            name, f"worktree/{name}", str(checkout), "p", "example-host", "wsl", records,
        )
    path = records / "owner.yaml"
    old_ref = "example-host/p/owner"
    pending = call(path, "reserve", {
        "kind": "container", "namespace": "raw-resource", "recovery": {"endpoint": "opaque"},
    }, ref=old_ref)
    assert pending.get("ok"), pending
    permit = call(path, "begin", {"token": pending["token"]}, ref=old_ref)
    assert permit.get("ok"), permit
    bound = call(path, "bind", {
        "token": pending["token"], "allocation_fence": permit["allocation_fence"],
        "identity": {"immutable_id": "full-id", "daemon": "opaque"},
    }, ref=old_ref)
    assert bound.get("ok"), bound
    (anchor / "machines.yaml").write_text(
        "machines:\n  example-host: {}\n  example-host-wsl: {}\n", encoding="utf-8",
    )
    config = replace(config, machine="example-host-wsl")
    bundle, _ = handoffs.offer(
        old_ref, f"{config.machine if request.param else 'example-host'}/p/consumer",
        [bound["ref"]], machine=config.machine,
    )
    return path, bundle, bound


@pytest.mark.guard
def test_historical_producer_real_offer_accept_and_accepted_retry(historical_producer):
    path, bundle, bound = historical_producer
    result = handoffs.accept(
        bundle.bundle_id, actor="example-host-wsl/p/consumer", machine="example-host-wsl",
    )
    assert result.state == "accepted"
    consumer_path = path.with_name("consumer.yaml")
    source = tracking.load_record(path)
    consumer = tracking.load_record(consumer_path)
    assert source.machine == consumer.machine == "example-host"
    assert source.resources[0].producer["phase"] == "transferred"
    assert source.resources[0].state == "released"
    assert consumer.resources[0].producer == bound["producer"]
    before = (path.read_bytes(), consumer_path.read_bytes(), handoffs.registry_path().read_bytes())
    assert handoffs.accept(
        bundle.bundle_id, actor="example-host-wsl/p/consumer", machine="example-host-wsl",
    ).state == "accepted"
    assert before == (path.read_bytes(), consumer_path.read_bytes(), handoffs.registry_path().read_bytes())


@pytest.mark.parametrize("role", ["owner", "consumer"])
@pytest.mark.parametrize("violation", ["platform", "machine", "namespace"])
@pytest.mark.guard
def test_historical_producer_foreign_context_retains_exact_ledgers(
    historical_producer, role, violation,
):
    path, bundle, _ = historical_producer
    target = path.with_name(role + ".yaml")
    record = tracking.load_record(target)
    changes = (
        {"platform": "windows"} if violation == "platform" else
        {"machine": "foreign-host-wsl"} if violation == "machine" else
        {"worktree_path": str(path.parent / "foreign" / record.worktree_id)}
    )
    record = replace(record, **changes)
    tracking.save_record(record, target)
    paths = (path, path.with_name("consumer.yaml"), handoffs.registry_path())
    before = {item: item.read_bytes() for item in paths}
    with pytest.raises(handoffs.ClaimHandoffError):
        handoffs.offer(
            bundle.source, bundle.consumer, [snapshot["ref"] for snapshot in bundle.claims],
            machine="example-host-wsl",
        )
    with pytest.raises(handoffs.ClaimHandoffError):
        handoffs.accept(
            bundle.bundle_id, actor="example-host-wsl/p/consumer", machine="example-host-wsl",
        )
    assert before == {item: item.read_bytes() for item in paths}


def test_resource_producer_accepted_retry_never_recreates_missing_consumer_claim(
    handoff_owner, monkeypatch,
):
    path = handoff_owner
    bundle, _ = _crash_after_accepted(path, monkeypatch)
    consumer_path = path.with_name("consumer.yaml")
    _rewrite(consumer_path, lambda raw: raw.update(resources=[]))
    before = consumer_path.read_bytes()
    registry = handoffs.registry_path().read_bytes()
    with pytest.raises(handoffs.ClaimHandoffError, match="obligation is missing"):
        _accept(bundle)
    assert consumer_path.read_bytes() == before
    assert handoffs.registry_path().read_bytes() == registry


def test_resource_producer_accepting_retry_preserves_exact_ref(handoff_owner, monkeypatch):
    path = handoff_owner
    bundle, bound = _offer(path)
    save_registry = handoffs._save_registry

    def crash(target, bundles):
        if bundles[0].state == "accepted":
            raise OSError("injected accepted registry failure")
        return save_registry(target, bundles)

    with monkeypatch.context() as patch:
        patch.setattr(handoffs, "_save_registry", crash)
        with pytest.raises(handoffs.ClaimHandoffError, match="accepted registry failure"):
            _accept(bundle)
    assert handoffs.show(bundle.bundle_id).state == "accepting"
    assert tracking.load_record(path).resources[0].producer["phase"] == "transferred"
    assert _accept(bundle).state == "accepted"
    received = tracking.load_record(path.with_name("consumer.yaml")).resources
    assert len(received) == 1
    assert received[0].ref == bound["ref"]
    assert received[0].producer == bound["producer"]
    assert received[0].handoff_bundle == ""
