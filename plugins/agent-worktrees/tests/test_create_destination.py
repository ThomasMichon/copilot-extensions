"""Ordinary destination coordination retains one original receiving operation."""

from __future__ import annotations

import argparse
import copy
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from machine_transport.registry import MachineEntry, SSHEnvironment

from agent_worktrees import allocation_creation_result, allocation_link_demand, config as cfg
from agent_worktrees import create_destination as destination, finalize, output, tracking
from agent_worktrees import owned_worktree as allocation, owned_worktree_remote as remote
from agent_worktrees import worktree_allocation_cli, worktree_ops_cli
from test_owned_worktree_transactions import IDENTITY, call
from test_resource_producer import owner as owner


def args(**values) -> argparse.Namespace:
    return argparse.Namespace(**{
        "creation_key": "caller opaque key / includes spaces", "no_owner": False,
        "system": False, "no_pair": True, "seed": None, "bound_agent": None,
        "interface": None, "origin": None, "machine": "remote", "repo": "target",
        "json": True, **values,
    })


@pytest.fixture
def coordinator(owner, monkeypatch):
    path, _ = owner
    operations = []
    effects = {}
    monkeypatch.setattr(destination, "_creator", lambda ref: ("m/p/owner", path))
    monkeypatch.setattr(allocation_link_demand, "require_capability", lambda ref: None)
    monkeypatch.setattr(allocation_link_demand, "request", lambda ref: None)
    monkeypatch.setattr(remote, "capabilities", lambda *a: worktree_allocation_cli.capabilities(receiver=True))

    def transport(operation: str, metadata: dict) -> dict:
        operations.append(operation)
        claim = next(
            item for item in tracking.load_record(path).resources
            if isinstance(item.producer, dict) and item.producer.get("lifetime") == allocation.LIFETIME
        )
        assert claim.producer["token"] == metadata["token"]
        assert claim.is_unsettled
        proof = {
            "schema": allocation.SCHEMA, "version": allocation.VERSION,
            "origin": metadata["creator"], "request_id": metadata["request_id"],
            "token": metadata["token"], "target": copy.deepcopy(metadata["recovery"]),
            "identity": copy.deepcopy(IDENTITY), "status": "allocated", "allocation_finished": True,
        }
        if operation == "create":
            effects.setdefault(metadata["request_id"], copy.deepcopy(IDENTITY))
            return proof
        if operation == "inspect":
            return proof
        assert operation == "result", "startup must never wait for bind or acknowledgement"
        assert metadata["request_id"] in effects
        return {
            "schema": allocation_creation_result.SCHEMA, "version": 1, "allocation": proof,
            "result": {
                "creation": {"version": 1, "status": "complete", "replayed": True},
                "worktree": {
                    "id": "child", "repo": "target", "execution_machine": "remote", "machine": "remote",
                    "path": IDENTITY["path"], "branch": IDENTITY["branch"],
                    "creation_nonce": IDENTITY["creation_nonce"], "owner_ref": "m/p/owner",
                },
            },
        }

    monkeypatch.setattr(remote, "request", transport)
    return path, operations, effects, transport


def create_remote(selected: argparse.Namespace) -> dict:
    return destination._remote_create(
        selected, "m/p/owner#session", {"machine": "remote", "project": "target"}, None,
    )


def test_durable_pending_precedes_receiving_effect_and_does_not_wait_for_linkage(coordinator):
    path, operations, effects, _ = coordinator
    result = create_remote(args())
    assert operations == ["create", "result"] and len(effects) == 1
    assert result["allocation"]["linkage"] == "pending"
    assert result["allocation"]["receiving_status"] == "allocated"
    assert result["allocation"]["creation_key"] == args().creation_key
    assert "token" not in json.dumps(result)
    assert result["worktree"]["path"] == IDENTITY["path"] and "launch" not in result
    record = tracking.load_record(path)
    assert not finalize._assert_obligations_settled(record, "owner", abandon=False)
    assert record.resources[0].producer["phase"] == "pending"


def test_lost_receiving_response_returns_same_retry_identity_and_never_recarves(coordinator, monkeypatch):
    path, operations, effects, transport = coordinator

    def lose_response(operation, metadata):
        response = transport(operation, metadata)
        if operation == "create":
            raise RuntimeError("receiving response lost")
        return response

    monkeypatch.setattr(remote, "request", lose_response)
    with pytest.raises(destination.DestinationCreationFailure) as caught:
        create_remote(args())
    retained = tracking.load_record(path).resources[0].producer
    failure = caught.value.payload()
    assert failure["creation"]["repeat_new"] is False
    assert failure["allocation"]["creation_key"] == args().creation_key
    assert retained["request_id"] == failure["allocation"]["request_id"]
    monkeypatch.setattr(remote, "request", transport)
    result = create_remote(args(creation_key=failure["allocation"]["creation_key"]))
    assert len(effects) == 1 and len(tracking.load_record(path).resources) == 1
    assert result["allocation"]["request_id"] == retained["request_id"]
    assert operations == ["create", "create", "result"]


def test_generated_retry_key_survives_unknown_outcome(coordinator, monkeypatch):
    _, _, _, transport = coordinator

    def unavailable(operation, metadata):
        transport(operation, metadata)
        raise RuntimeError("unknown outcome")

    monkeypatch.setattr(remote, "request", unavailable)
    with pytest.raises(destination.DestinationCreationFailure) as caught:
        create_remote(args(creation_key=None))
    recovery = caught.value.payload()["allocation"]
    assert recovery["creation_key"] and recovery["request_id"].startswith("create_")
    assert recovery["creation_key"] in str(caught.value)
    monkeypatch.setattr(remote, "request", transport)
    result = create_remote(args(creation_key=recovery["creation_key"]))
    assert result["allocation"]["request_id"] == recovery["request_id"]


def test_generated_operation_recovers_by_retained_request_id_after_no_output(coordinator, monkeypatch):
    path, _, effects, transport = coordinator

    def interrupted(operation, metadata):
        response = transport(operation, metadata)
        if operation == "create":
            raise SystemExit("caller disappeared before output")
        return response

    monkeypatch.setattr(remote, "request", interrupted)
    with pytest.raises(SystemExit):
        create_remote(args(creation_key=None))
    retained = tracking.load_record(path).resources[0].producer
    monkeypatch.setattr(remote, "request", transport)
    result = create_remote(args(creation_key=None, creation_request_id=retained["request_id"]))
    assert result["allocation"]["request_id"] == retained["request_id"]
    assert result["allocation"]["creation_key"] is None
    assert len(effects) == 1 and len(tracking.load_record(path).resources) == 1


def test_request_id_recovery_never_reserves_a_missing_or_changed_operation(coordinator):
    path, operations, effects, _ = coordinator
    with pytest.raises(destination.DestinationCreationFailure, match="missing"):
        create_remote(args(creation_key=None, creation_request_id="unrecorded"))
    assert not tracking.load_record(path).resources and not operations and not effects
    result = create_remote(args())
    operations.clear()
    before = path.read_bytes()
    with pytest.raises(destination.DestinationCreationFailure, match="differ"):
        destination._remote_create(
            args(creation_key=None, creation_request_id=result["allocation"]["request_id"]),
            "m/p/owner", {"machine": "remote", "project": "other"}, None,
        )
    assert path.read_bytes() == before and not operations


def test_existing_bound_identity_only_reads_original_result(coordinator):
    path, operations, _, _ = coordinator
    first = create_remote(args())
    token = tracking.load_record(path).resources[0].producer["token"]
    assert call(path, "bind", {"token": token})["phase"] == "bound"
    operations.clear()
    replay = create_remote(args())
    assert operations == ["result"]
    assert replay["worktree"] == first["worktree"] and replay["allocation"]["linkage"] == "bound"


@pytest.mark.parametrize("change", [
    {"schema": "unsupported"}, {"version": True}, {"claim_linkage": "blocking"},
    {"receiving_receipt": False}, {"operations": ["create", "inspect"]},
    {"creation_result": {"schema": allocation_creation_result.SCHEMA, "version": True}},
])
def test_mixed_version_receiver_refuses_before_pending_or_receiving_effects(coordinator, monkeypatch, change):
    path, operations, effects, _ = coordinator
    capabilities = {**worktree_allocation_cli.capabilities(receiver=True), **change}
    monkeypatch.setattr(remote, "capabilities", lambda *a: capabilities)
    with pytest.raises(ValueError, match="receiver"):
        create_remote(args())
    assert not tracking.load_record(path).resources and operations == [] and effects == {}


@pytest.mark.parametrize("change", [{"seed": "prompt"}, {"no_owner": True}, {"system": True}])
def test_unsupported_remote_policy_refuses_before_negotiation_or_effects(coordinator, monkeypatch, change):
    path, operations, _, _ = coordinator
    monkeypatch.setattr(remote, "capabilities", lambda *a: pytest.fail("unsupported option reached peer"))
    with pytest.raises(ValueError):
        create_remote(args(**change))
    assert not tracking.load_record(path).resources and not operations


def test_changed_target_facts_conflict_with_same_original_key(coordinator):
    path, operations, _, _ = coordinator
    create_remote(args())
    before = path.read_bytes()
    operations.clear()
    with pytest.raises(destination.DestinationCreationFailure, match="conflicts"):
        destination._remote_create(
            args(), "m/p/owner", {"machine": "remote", "project": "different"}, None,
        )
    assert path.read_bytes() == before and not operations


def test_unfinished_result_remains_pending_without_usable_worktree(coordinator, monkeypatch):
    _, _, _, transport = coordinator

    def pending(operation, metadata):
        response = transport(operation, metadata)
        if operation == "result":
            response["allocation"].update(status="pending", allocation_finished=False)
            response["result"] = None
        return response

    monkeypatch.setattr(remote, "request", pending)
    with pytest.raises(destination.DestinationCreationFailure) as caught:
        create_remote(args())
    result = caught.value.payload()
    assert result["creation"]["status"] == "pending"
    assert result["allocation"]["receiving_status"] == "pending" and "worktree" not in result


def test_current_linkage_read_failure_does_not_hide_durable_receiving_result(coordinator, monkeypatch, caplog):
    original = destination._origin_operation

    def transaction(creator, path, operation, payload):
        if operation == "inspect":
            raise ValueError("origin inspection unavailable")
        return original(creator, path, operation, payload)

    monkeypatch.setattr(destination, "_origin_operation", transaction)
    result = create_remote(args())
    assert result["worktree"]["id"] == "child" and result["allocation"]["linkage"] == "unknown"
    assert "current origin linkage is unknown" in caplog.text


def test_missing_current_writer_refuses_before_reservation_or_receiving(coordinator, monkeypatch):
    path, operations, _, _ = coordinator
    monkeypatch.setattr(destination.tracking_write, "registered_verbs", lambda: frozenset())
    with pytest.raises(ValueError, match="origin writer"):
        create_remote(args())
    assert not tracking.load_record(path).resources and not operations


@pytest.mark.parametrize("outcome", ["complete", "unknown", "changed"])
def test_run_reuses_original_responsibility_instead_of_competing_plain_child_claim(
    coordinator, monkeypatch, outcome,
):
    path, _, _, transport = coordinator
    original_run = worktree_ops_cli.subprocess.run

    def inner(command, **kwargs):
        if not isinstance(command, str) or not kwargs.get("shell"):
            return original_run(command, **kwargs)
        if outcome == "unknown":
            def lost(operation, metadata):
                transport(operation, metadata)
                raise RuntimeError("original receiving response lost")
            monkeypatch.setattr(remote, "request", lost)
        try:
            response = create_remote(args())
            code = 0
        except destination.DestinationCreationFailure as exc:
            response, code = exc.payload(), 3
        if outcome == "changed":
            response["allocation"]["request_id"] = "different-operation"
        return SimpleNamespace(stdout=json.dumps(response), returncode=code)

    monkeypatch.setattr(worktree_ops_cli.subprocess, "run", inner)
    code = worktree_ops_cli.cmd_run(argparse.Namespace(
        inner_command=["owned-command create --machine remote --repo target --json"],
        owner_ref="m/p/owner#session",
    ))
    record = tracking.load_record(path)
    protected = [item for item in record.resources if item.has_producer]
    assert len(protected) == 1 and protected[0].producer["phase"] == "pending"
    assert not any(item.ref == IDENTITY["ref"] for item in record.resources)
    pending_wrappers = [item for item in record.resources if item.ref.startswith("pending-run:")]
    assert len(pending_wrappers) == (1 if outcome == "changed" else 0)
    assert code == {"complete": 0, "unknown": 3, "changed": 1}[outcome]
    assert not finalize._assert_obligations_settled(record, "owner", abandon=False)


def test_journal_helper_observes_original_protected_claim_without_adding_plain_ref(coordinator):
    path, _, _, _ = coordinator
    response = create_remote(args())
    protected = worktree_ops_cli._journal_run_claim("m/p/owner", json.dumps(response))
    assert protected.producer["request_id"] == response["allocation"]["request_id"]
    assert len(tracking.load_record(path).resources) == 1


def test_origin_capability_owner_probe_reads_actual_ledger_without_reservation(owner):
    path, _ = owner
    before = path.read_bytes()
    with output.capture_json_output() as captured:
        assert worktree_allocation_cli.cmd_worktree_allocation(
            args(operation="capabilities", owner_ref="m/p/owner#session"),
        ) == 0
    capability = json.loads(captured.getvalue())
    assert capability["creator"] == "m/p/owner"
    assert capability["ordinary_create"]["creator_identity"] is True
    assert path.read_bytes() == before


def test_origin_uses_original_cwd_repository_before_explicit_destination(owner, monkeypatch):
    path, config = owner
    monkeypatch.setattr(destination, "_cwd_project", lambda cwd: "p")
    seen = []
    monkeypatch.setattr(destination.worktree_identity, "tracked_invocation_record", lambda cwd, **k: (
        seen.append((cwd, k)) or tracking.load_record(path)
    ))
    monkeypatch.setenv("COPILOT_AGENT_SESSION_ID", "current-session")
    project, selected, owner_ref = destination._origin(
        Path("/original/cwd"), None, standalone=False,
    )
    assert project == "p" and selected is config and owner_ref == "m/p/owner#current-session"
    assert seen == [(Path("/original/cwd"), {"project": "p", "anchor": Path(config.default_repo.anchor)})]


def test_explicit_owner_controls_authority_but_not_default_destination_repository(owner, monkeypatch):
    _, config = owner
    selected = []
    monkeypatch.setattr(destination, "_cwd_project", lambda cwd: "cwd-project")
    monkeypatch.setattr(cfg, "load_project_config", lambda p: selected.append(p) or config)
    project, _, owner_ref = destination._origin(
        Path("/original/cwd"), "m/p/owner#session", standalone=False,
    )
    assert selected == ["p"] and project == "cwd-project" and owner_ref == "m/p/owner#session"


@pytest.mark.parametrize("project", ["p", None])
@pytest.mark.parametrize("machine", [None, "remote"])
def test_untracked_linked_origin_is_refused_before_child_unless_explicit_standalone(
    owner, monkeypatch, project, machine,
):
    _, config = owner
    monkeypatch.setattr(destination, "_cwd_project", lambda cwd: project)
    monkeypatch.setattr(cfg, "project_name", lambda: "p")
    monkeypatch.setattr(destination.worktree_identity, "tracked_invocation_record", lambda *a, **k: None)
    monkeypatch.setattr(destination.worktree_identity, "_worktree_id_from_git", lambda *a, **k: "external")
    with pytest.raises(ValueError, match="linked worktree owner is untracked"):
        destination.create(
            args(machine=machine, repo="p"), owner_ref=None, dispatch_attempt=None,
            invocation_cwd=Path("/external/linked"), create_local=lambda *a, **k: pytest.fail("child created"),
        )
    origin_project, selected, owner_ref = destination._origin(
        Path("/external/linked"), None, standalone=True,
    )
    assert origin_project == project and selected is config and owner_ref is None


@pytest.mark.parametrize("local", [False, True])
def test_legacy_label_cannot_discard_requested_windows_environment(owner, monkeypatch, local):
    _, config = owner
    monkeypatch.setattr(destination.execution_spaces, "current_execution_context", lambda c: SimpleNamespace(
        execution_machine="box" if local else "m", environment="linux",
    ))
    entry = MachineEntry(
        key="box", display_name="Box", environment="Linux", ssh_ready=True,
        ssh_environments=[SSHEnvironment(name="Linux", alias="box-linux", shell="bash")],
    )
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **k: {"box": entry})
    with pytest.raises(ValueError, match="environment"):
        destination._target(config, "box (Windows)", "target")


def test_exact_execution_key_precedes_suffix_spelling_and_requires_matching_route(owner, monkeypatch):
    _, config = owner
    monkeypatch.setattr(destination.execution_spaces, "current_execution_context", lambda c: SimpleNamespace(
        execution_machine="m", environment="linux",
    ))
    entry = MachineEntry(
        key="box-win", display_name="Linux logical space", execution_platform="linux", ssh_ready=True,
        ssh_environments=[SSHEnvironment(name="Linux", alias="box-linux", shell="bash")],
    )
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **k: {entry.key: entry})
    assert destination._target(config, "box-win", "target") == {"machine": "box-win", "project": "target"}
    entry = replace(entry, ssh_environments=[
        SSHEnvironment(name="Windows", alias="box-win", shell="powershell"),
    ])
    with pytest.raises(ValueError):
        destination._target(config, "box-win", "target")


def test_destination_parser_keeps_unknown_argument_refusal():
    parser = argparse.ArgumentParser()
    worktree_ops_cli.add_parsers(parser.add_subparsers(dest="command"))
    selected = parser.parse_args(["create", "--machine=remote", "--repo=target", "--json"])
    assert selected.machine == "remote" and selected.repo == "target"
    selected = parser.parse_args(["create", "--bridge", "--caller-worktree=caller"])
    assert destination.creation_kind(selected) == "bridge"
    assert selected.caller_worktree == "caller"
    with pytest.raises(SystemExit):
        parser.parse_args(["create", "--bridge", "--system"])
    with pytest.raises(ValueError, match="requires --bridge"):
        destination.creation_kind(args(caller_worktree="caller"))
    with pytest.raises(SystemExit) as caught:
        parser.parse_args(["create", "--machine=remote", "--invented-option"])
    assert caught.value.code == 2


@pytest.mark.parametrize("failure", [False, True])
def test_local_different_project_uses_original_owner_and_restores_project(owner, monkeypatch, failure):
    _, config = owner
    original = cfg.active_project()
    cfg.set_active_project("origin")
    monkeypatch.setattr(destination, "_origin", lambda *a, **k: ("p", config, "m/p/owner#session"))
    monkeypatch.setattr(destination, "_target", lambda *a: {"machine": "m", "project": "target"})
    monkeypatch.setattr(destination.execution_spaces, "current_execution_context", lambda c: SimpleNamespace(execution_machine="m"))
    seen = []

    def local(selected, **options):
        assert cfg.active_project() == "target"
        seen.append(options)
        if failure:
            raise ValueError("local failure")
        return {"worktree": {"id": "actual-local"}}

    try:
        if failure:
            with pytest.raises(ValueError, match="local failure"):
                destination.create(
                    args(machine=None, seed="preserved"), owner_ref=None, dispatch_attempt=None,
                    invocation_cwd=Path("/original/cwd"), create_local=local,
                )
        else:
            result = destination.create(
                args(machine=None, seed="preserved"), owner_ref=None, dispatch_attempt=None,
                invocation_cwd=Path("/original/cwd"), create_local=local,
            )
            assert result["worktree"]["id"] == "actual-local"
        assert cfg.active_project() == "origin"
        assert seen[0]["owner_ref"] == "m/p/owner#session"
        assert seen[0]["pending_seed"] == "preserved"
        assert seen[0]["no_mux"] and not seen[0]["build_launch_plan"]
    finally:
        cfg.set_active_project(original)


def test_actual_origin_ledger_normalizes_session_alias_without_inventing_anchor(owner):
    path, _ = owner
    assert destination._creator("m/p/owner#session") == ("m/p/owner", path)
    with pytest.raises(ValueError, match="anchors"):
        destination._creator(tracking.format_anchor_ref("m", "p", "session"))


def test_selected_cli_json_remains_on_stdout_outside_creation_redirect(owner, monkeypatch):
    value = {"worktree": {"id": "actual", "path": "/actual", "branch": "worktree/actual"}}
    seen = []
    monkeypatch.setattr(destination, "create", lambda *a, **k: seen.append(k) or value)
    import agent_worktrees.__main__ as main

    monkeypatch.setattr(main, "_INVOCATION_CWD", Path("/original/cwd"))
    with output.capture_json_output() as captured:
        assert worktree_ops_cli.cmd_create(args()) == 0
    assert json.loads(captured.getvalue())["worktree"] == value["worktree"]
    assert seen[0]["invocation_cwd"] == Path("/original/cwd")


@pytest.mark.parametrize("health", [
    [],
    {}, {"status": "ready", "published": True, "capabilities": []},
    {"status": "draining", "published": True, "capabilities": ["worktree-allocation-linker-v1"],
     "allocationLinker": {"admitting": True}},
    {"status": "ready", "published": False, "capabilities": ["worktree-allocation-linker-v1"],
     "allocationLinker": {"admitting": True}},
    {"status": "ready", "published": True, "capabilities": ["worktree-allocation-linker-v1"],
     "allocationLinker": {"admitting": False}},
])
def test_actual_resident_must_advertise_live_admission_before_new_effects(monkeypatch, health):
    monkeypatch.setattr(allocation_link_demand.status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(allocation_link_demand.worktrees_client, "read_current", lambda r: {})
    monkeypatch.setattr(allocation_link_demand.worktrees_client, "read_health", lambda: health)
    with pytest.raises(ValueError, match="actual resident"):
        allocation_link_demand.require_capability("m/p/owner")


def test_live_linker_negotiation_uses_authenticated_health_not_advisory_version(monkeypatch):
    seen = []
    monkeypatch.setattr(allocation_link_demand.status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(allocation_link_demand.worktrees_client, "read_current", lambda r: (
        seen.append(r) or {"availability": {"status": "advisory"}}
    ))
    monkeypatch.setattr(allocation_link_demand.worktrees_client, "read_health", lambda: {
        "status": "ready", "published": True, "capabilities": ["worktree-allocation-linker-v1"],
        "allocationLinker": {"admitting": True},
    })
    allocation_link_demand.require_capability("m/p/owner")
    assert seen == [{"contract_version": 1, "project": "p", "worktree_id": "owner", "include_missing": True}]
