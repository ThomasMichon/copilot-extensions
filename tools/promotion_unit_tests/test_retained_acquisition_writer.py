"""Exercise the retained writer with synthetic journal and process boundaries."""

from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = "promotion_retained_writer"


def finish(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("synthetic owner unexpectedly suspended")


class Refused(RuntimeError):
    def __init__(self, code, message, *, effect=None):
        super().__init__(message)
        self.code = code
        self.effect = effect


@dataclass(frozen=True)
class Request:
    worktree_writer: object
    delivery_id: str = "delivery-original"
    worktree_id: str = "workspace-original"
    worktree_path: str = "/example/workspace"
    project: str = "example"
    task_id: str = "task-original"
    task_generation: int = 1
    reservation_key: str = "reservation-original"
    target: str = "local"
    mode: str = "acp"


@pytest.fixture
def boundary(monkeypatch):
    def module(name, **attributes):
        value = ModuleType(name)
        value.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, value)
        return value

    root = ROOT / "plugins/agent-bridge/src/agent_bridge"
    module(PACKAGE, __path__=[str(root)])
    module(f"{PACKAGE}.acquisition_contract", AcquisitionRefused=Refused, AcquisitionRequest=Request)
    module(f"{PACKAGE}.acquisition_journal", _digest=lambda request: request.delivery_id)
    module(f"{PACKAGE}._peer_launch", ContextRefused=Refused)
    calls = []
    prefix = ("/example/pinned-python", "/example/pinned-worktrees")
    identity = {
        "installation_root": "/example/installation",
        "runtime_generation": "a" * 32,
    }
    requirement = SimpleNamespace(
        identity=SimpleNamespace(
            installation_root=identity["installation_root"],
            model_dump=lambda: identity.copy(),
        ),
        creation_nonce="original-nonce",
        model_dump=lambda: {"version": 1, "identity": identity.copy(), "creation_nonce": "original-nonce"},
    )
    support = {
        "schema": "copilot-extensions.resource-relationship", "version": 1,
        "expected_creation_nonce": True, "expected_writer_identity": True,
        "writer_scope": "executing-process", "writer_identity": identity.copy(),
        "retained_session_writes": 1,
        "writer_contract": {
            "schema": "copilot-extensions.worktree-record-writer",
            "version": 1, "retained_relationships": 1, "creation_nonce": 1,
        },
    }

    def select():
        calls.append(("select", prefix))
        return prefix, Path(identity["installation_root"]), prefix[0]

    async def command(args, project, *, owner_prefix):
        calls.append(("qualify", tuple(owner_prefix), tuple(args), project))
        return support

    module(f"{PACKAGE}.session_lifecycle_cli", _agent_worktrees_pinned_selection=select)
    module(f"{PACKAGE}.acquisition_authority", worktree_command=command)
    name = f"{PACKAGE}.acquisition_worktree_writer"
    spec = importlib.util.spec_from_file_location(name, root / "acquisition_worktree_writer.py")
    assert spec and spec.loader
    writer = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, writer)
    spec.loader.exec_module(writer)
    rows = []
    db = SimpleNamespace(execute_read=lambda *args: rows)
    return SimpleNamespace(
        writer=writer, request=Request(requirement), db=db, rows=rows,
        prefix=prefix, support=support, calls=calls,
    )


def test_fresh_selection_and_recovery_reuse_original_prefix(boundary):
    b = boundary

    async def acquire():
        async with b.writer.acquisition_writer(b.db, b.request):
            assert b.writer.current().prefix == b.prefix
            document = b.writer.retained_document(b.request)
            b.writer.require_scope(
                ["register-session", "--worktree-id", b.request.worktree_id],
                project=b.request.project, cwd=b.request.worktree_path,
            )
            return document

    document = finish(acquire())
    assert b.writer.current() is None
    b.rows.append({
        "request_digest": b.request.delivery_id,
        "data_json": json.dumps({"_worktree_writer": document}),
    })
    assert finish(acquire()) == document
    assert [call[0] for call in b.calls] == ["select", "qualify", "qualify"]
    assert all(call[1] == b.prefix for call in b.calls)
    assert b.request.worktree_writer.creation_nonce == "original-nonce"


@pytest.mark.parametrize("field", [
    "writer_identity", "writer_scope", "expected_creation_nonce",
    "expected_writer_identity", "writer_contract", "retained_session_writes",
])
def test_changed_or_missing_writer_refuses_before_context_admission(boundary, field):
    b = boundary
    b.support[field] = None

    async def acquire():
        async with b.writer.acquisition_writer(b.db, b.request):
            raise AssertionError("incompatible writer admitted")

    with pytest.raises(Refused) as raised:
        finish(acquire())
    assert raised.value.code == "worktree_writer_incompatible"
    assert b.writer.current() is None


def test_recovery_missing_binding_never_recaptures_selection(boundary):
    b = boundary
    b.rows.append({"request_digest": b.request.delivery_id, "data_json": "{}"})

    async def acquire():
        async with b.writer.acquisition_writer(b.db, b.request):
            raise AssertionError("missing original binding admitted")

    with pytest.raises(Refused) as raised:
        finish(acquire())
    assert raised.value.code == "worktree_writer_unconfirmed"
    assert b.calls == []


def test_required_session_commands_carry_original_executing_scope(boundary):
    b = boundary
    legacy = ["register-session", "--session-id", "native-one"]
    assert b.writer.session_write_args(legacy) is legacy

    async def acquire():
        async with b.writer.acquisition_writer(b.db, b.request):
            for command in ("register-session", "bind-session", "link-succession"):
                arguments = [command, "--worktree-id", b.request.worktree_id]
                result = b.writer.session_write_args(arguments)
                assert result[:-2] == arguments
                assert result[-2] == "--retained-record-writer"
                assert json.loads(result[-1]) == {
                    **b.request.worktree_writer.model_dump(), "worktree_id": b.request.worktree_id,
                    "project": b.request.project, "worktree_path": b.request.worktree_path,
                }
            read = ["head-session", "--worktree-id", b.request.worktree_id]
            assert b.writer.session_write_args(read) is read

    finish(acquire())
    assert b.writer.current() is None


@pytest.mark.parametrize("changed", [
    None, "nonce", "journal", "instance", "missing-launcher", "unreadable-record",
])
def test_original_writer_inspection_rechecks_owner_without_selection(boundary, monkeypatch, changed):
    b = boundary
    contract = sys.modules[f"{PACKAGE}.acquisition_contract"]
    monkeypatch.setattr(contract, "AcquisitionRequest", SimpleNamespace(model_validate=lambda data: b.request))
    slot = {"role": "active", "pid": 17, "active": {"pid": 17, "generation": 1}}
    for name, attributes in (
        ("config", {"config_dir": lambda: Path("/example/bridge")}),
        ("self_retire", {"slot_descriptor": lambda root: slot}),
    ):
        module = ModuleType(f"{PACKAGE}.{name}")
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    original = {
        "worktree_id": b.request.worktree_id, "state": "pending",
        "request_digest": b.request.delivery_id, "stage": "prepared",
        "data_json": json.dumps({
            "_request": b.request.delivery_id,
            "_worktree_writer": {"version": 1, "prefix": list(b.prefix)},
        }),
    }
    b.rows.append(original.copy())
    authority = sys.modules[f"{PACKAGE}.acquisition_authority"]
    if changed == "missing-launcher":
        async def missing(*args, **kwargs):
            raise FileNotFoundError("original launcher unavailable")
        monkeypatch.setattr(authority, "worktree_command", missing)

    async def record(worktree_id, project, *, command):
        assert worktree_id == b.request.worktree_id and project == b.request.project
        assert command.keywords == {"owner_prefix": b.prefix}
        if changed == "journal":
            b.rows[0]["stage"] = "launching"
        elif changed == "instance":
            slot["active"]["generation"] = 2
        elif changed == "unreadable-record":
            raise PermissionError("original owner record unavailable")
        return {
            "repo": project, "path": b.request.worktree_path,
            "creation_nonce": "replaced" if changed == "nonce" else "original-nonce",
        }

    monkeypatch.setattr(authority, "read_worktree_record", record, raising=False)
    name = f"{PACKAGE}.acquisition_writer_inspection"
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "plugins/agent-bridge/src/agent_bridge/acquisition_writer_inspection.py",
    )
    assert spec and spec.loader
    inspection = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, inspection)
    spec.loader.exec_module(inspection)
    pending = inspection.inspect_writer(b.db, b.request.delivery_id, original)
    if changed:
        with pytest.raises(Refused):
            finish(pending)
    else:
        observed = finish(pending)
        assert observed["worktree_writer"] == b.request.worktree_writer.model_dump()
        assert observed["bridge_instance"]["generation"] == 1
        assert observed["reservation_key"] == b.request.reservation_key
        assert b.rows == [original]
    assert [call[0] for call in b.calls] == ([] if changed == "missing-launcher" else ["qualify"])
    assert b.writer.current() is None


def test_receiving_scope_checks_locked_incarnation_and_resets_after_failure(monkeypatch):
    package = "promotion_receiving_writer"
    root = ROOT / "plugins/agent-worktrees/src/agent_worktrees"
    identity = {"installation_root": "/example/owner", "runtime_generation": "a" * 32}
    for name, attributes in (
        (package, {"__path__": [str(root)]}),
        (package + ".resource_relationship", {
            "valid_writer_identity": lambda value: value == identity,
            "writer_identity": lambda: identity.copy(),
        }),
        (package + ".execution_spaces", {"ExecutionSpaceError": RuntimeError}),
        (package + ".output", {"_json_error": lambda message: {"error": message}}),
    ):
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    name = package + ".retained_record_writer"
    spec = importlib.util.spec_from_file_location(name, root / "retained_record_writer.py")
    assert spec and spec.loader
    receiver = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, receiver)
    spec.loader.exec_module(receiver)
    scope = {
        "version": 1, "identity": identity, "creation_nonce": "original-nonce",
        "worktree_id": "original", "project": "example", "worktree_path": "/example/workspace",
    }
    record = SimpleNamespace(
        worktree_id="original", repo="example", worktree_path="/example/workspace",
        creation_nonce="original-nonce",
    )
    args = SimpleNamespace(
        retained_record_writer=json.dumps(scope), worktree_id="original", session_id="native-one",
    )

    @receiver.command
    def command(_args):
        assert receiver.active()
        receiver.require_record(record)
        return "written"

    assert command(args) == "written"
    assert not receiver.active()
    record.creation_nonce = "replaced"
    assert "incarnation changed" in command(args)["error"]
    assert not receiver.active()


def test_required_scope_refuses_another_workspace_and_propagates_lineage_error(boundary):
    b = boundary

    async def acquire():
        async with b.writer.acquisition_writer(b.db, b.request):
            target = SimpleNamespace(
                type="local", worktree_id=b.request.worktree_id,
                cwd=b.request.worktree_path, project=b.request.project,
                venue={b.writer.DELIVERY_KEY: b.request.delivery_id},
                spawn_command=None, container=None, codespace=None, remote_acp=None,
            )
            b.writer.require_target(target)
            with pytest.raises(Refused):
                b.writer.require_scope(["bind-session", "--worktree-dir", "/example/other"])
            with pytest.raises(Refused):
                b.writer.require_target(SimpleNamespace(**{
                    **vars(target), "worktree_id": "workspace-other",
                }))
            with b.writer.lineage_errors():
                raise ValueError("original writer failed")

    with pytest.raises(ValueError, match="original writer failed"):
        finish(acquire())
    assert b.writer.current() is None
    with b.writer.lineage_errors():
        raise ValueError("legacy best effort remains unchanged")


def test_required_cli_refuses_without_writer_or_native_effect(boundary):
    b = boundary

    async def acquire():
        async with b.writer.acquisition_writer(b.db, replace(b.request, mode="cli")):
            raise AssertionError("unsupported CLI writer admitted")

    with pytest.raises(Refused) as raised:
        finish(acquire())
    assert raised.value.code == "worktree_writer_mode_unsupported"
    assert raised.value.effect == "not_started"
    assert b.calls == []


def test_handoff_journal_keeps_original_successor_and_native_binding(boundary):
    b = boundary
    stored = {
        "request_digest": b.request.delivery_id,
        "data_json": json.dumps({"_worktree_writer": {"version": 1, "prefix": list(b.prefix)}}),
    }
    writes = []

    def execute(sql, params):
        if sql.startswith("UPDATE"):
            stored["data_json"] = params[0]
            writes.append(params)
        return SimpleNamespace(fetchone=lambda: stored)

    def transaction(*, write, full_sync):
        assert full_sync is True
        return nullcontext(SimpleNamespace(execute=execute))

    b.db._runtime_transaction = transaction

    async def acquire():
        async with b.writer.acquisition_writer(b.db, b.request):
            created = b.writer.handoff_state(b.db, "predecessor", create={
                "successor_id": "original-successor", "seed_prompt": "original seed",
            })
            assert created["stage"] == "prepared"
            started = b.writer.handoff_state(
                b.db, "predecessor", stage="started", native_session_id="original-native",
            )
            assert started["successor_id"] == "original-successor"
            with pytest.raises(Refused, match="original successor must be reconciled"):
                b.writer.handoff_state(b.db, "predecessor", create={"successor_id": "replacement"})
            with pytest.raises(Refused, match="native identity changed"):
                b.writer.handoff_state(
                    b.db, "predecessor", stage="started", native_session_id="replacement",
                )
            recovered = b.writer.handoff_state(b.db, "predecessor")
            assert recovered["native_session_id"] == "original-native"
            assert recovered["seed_prompt"] == "original seed"

    finish(acquire())
    assert len(writes) == 2
    assert json.loads(stored["data_json"])["_worktree_writer"]["prefix"] == list(b.prefix)
