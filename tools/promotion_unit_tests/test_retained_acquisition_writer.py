"""Exercise the retained writer with synthetic journal and process boundaries."""

from __future__ import annotations

import importlib.util
import json
import sys
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
    )
    support = {
        "schema": "copilot-extensions.resource-relationship", "version": 1,
        "expected_creation_nonce": True, "expected_writer_identity": True,
        "writer_scope": "executing-process", "writer_identity": identity.copy(),
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
    "expected_writer_identity", "writer_contract",
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


def test_required_scope_refuses_another_workspace_and_propagates_lineage_error(boundary):
    b = boundary

    async def acquire():
        async with b.writer.acquisition_writer(b.db, b.request):
            target = SimpleNamespace(
                type="local", worktree_id=b.request.worktree_id,
                cwd=b.request.worktree_path, project=b.request.project,
                venue={b.writer.DELIVERY_KEY: b.request.delivery_id},
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
