"""Portable transactions and wire controls; no native execution is implied."""

import base64
import errno
import io
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_procutil import _daemon_instance_shell as shell
from agent_procutil import _daemon_instance_store as store
from agent_procutil._daemon_instance_records import ProcessIdentity, reserve_for_process
from agent_procutil.daemon_instances import (
    INSTANCE_ENV, REGISTRY_ENV, DaemonInstanceReceipt,
)


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT", raising=False)
    owner = Mock(identity=ProcessIdentity(4200, "windows:123"), scopes=None)
    owner.contain.return_value = r"Local\ce-owned-job-" + "a" * 32
    result = shell.ShellInstanceSession(owner)
    result.parameters = {
        "service": "fixture", "runtime_root": str(tmp_path / "runtime"),
        "source": str(Path(__file__).resolve()), "registry_root": str(tmp_path / "registry"),
        "purpose": "production",
    }
    return result


def enter(session, **extra):
    return session.dispatch({"command": "enter", "parameters": session.parameters, **extra})


def exit_guard(session, guard):
    return session.dispatch({
        "command": "exit", "guard_token": guard["guard_token"], "receipt": guard,
    })


def test_nested_shell_guards_share_original_identity_and_close_once(session):
    first, second = enter(session), enter(session)
    assert first["instance_id"] == second["instance_id"]
    assert first["guard_token"] != second["guard_token"]
    receipt = shell._receipt(first)
    assert session.owner.identity.matches(store.inspect(receipt.registry_root)[0])
    exit_guard(session, second)
    assert store.inspect(receipt.registry_root)[0]["state"] == "running"
    exit_guard(session, first)
    assert store.inspect(receipt.registry_root)[0]["state"] == "finished"
    session.owner.contain.assert_not_called()
    session.owner.retire.assert_not_called()
    with pytest.raises(ValueError, match="guard custody"):
        exit_guard(session, first)


def test_closing_forged_receipt_does_not_release_original_guard(session, tmp_path):
    guard = enter(session)
    with pytest.raises(ValueError, match="receipt changed"):
        exit_guard(session, {**guard, "registry_root": str(tmp_path / "other")})
    assert session._tokens == {guard["guard_token"]}
    exit_guard(session, guard)


def test_failed_finish_retains_original_guard_for_exact_retry(session, monkeypatch):
    guard = enter(session)
    original = shell.finish_for_process
    monkeypatch.setattr(shell, "finish_for_process", Mock(side_effect=OSError("write refused")))
    with pytest.raises(OSError, match="write refused"):
        exit_guard(session, guard)
    assert session._tokens == {guard["guard_token"]}
    assert session._active[0] == shell._receipt(guard)
    monkeypatch.setattr(shell, "finish_for_process", original)
    exit_guard(session, guard)


def test_direct_shell_startup_still_requires_explicit_purpose(session):
    session.parameters.pop("purpose")
    with pytest.raises(ValueError, match="explicit purpose"):
        enter(session)
    assert not Path(session.parameters["registry_root"]).exists()
    session.owner.contain.assert_not_called()


@pytest.fixture
def disposable(session, monkeypatch):
    spec = shell._spec(session.parameters, {}, admission=False)
    receipt = reserve_for_process(spec, session.owner.identity)
    record = store._read(receipt.record_path)
    now = store._wall_clock()
    record.update(
        purpose="unattended-test", ttl="10", deadline=str(now + 10),
        hard_deadline=str(now + 30),
    )
    store._write(receipt.record_path, record)
    monkeypatch.setattr(store, "_expire", lambda row, wall: wall >= float(row["deadline"]))
    environment = {INSTANCE_ENV: receipt.instance_id, REGISTRY_ENV: str(receipt.registry_root)}
    return session, receipt, environment


def test_disposable_shell_is_contained_before_its_running_record_is_published(disposable):
    session, receipt, environment = disposable

    def contain(_environment):
        assert store._read(receipt.record_path)["state"] == "reserved"
        return "fixture-scopes"
    session.owner.contain.side_effect = contain
    guard = enter(session, environment=environment)
    assert guard["job_scopes"] == "fixture-scopes"
    assert store._read(receipt.record_path)["state"] == "running"
    exit_guard(session, guard)
    assert session.owner.scopes is None
    session.owner.close.assert_not_called()


def test_foreign_admission_is_refused_before_any_caller_job_assignment(disposable):
    session, receipt, environment = disposable
    record = store._read(receipt.record_path)
    record.update(pid="4300", birth="windows:124", state="bound")
    store._write(receipt.record_path, record)
    before = receipt.record_path.read_bytes()
    with pytest.raises(ValueError, match="process differs"):
        enter(session, environment=environment)
    session.owner.contain.assert_not_called()
    assert receipt.record_path.read_bytes() == before


def test_failed_record_publication_after_containment_retires_and_refuses_replay(disposable, monkeypatch):
    session, receipt, environment = disposable
    before = receipt.record_path.read_bytes()
    monkeypatch.setattr(store, "_write", Mock(side_effect=OSError("publication refused")))
    with pytest.raises(OSError, match="publication refused"):
        enter(session, environment=environment)
    session.owner.contain.assert_called_once_with(environment)
    session.owner.retire.assert_called_once_with()
    assert receipt.record_path.read_bytes() == before
    assert session._tokens == set()
    with pytest.raises(RuntimeError, match="replay is refused"):
        enter(session, environment=environment)


def test_expiry_retires_original_scope_without_cleaning_any_files(disposable, monkeypatch):
    session, receipt, environment = disposable
    enter(session, environment=environment)
    active = session._active[1]
    monkeypatch.setattr(store, "_monotonic_clock", lambda: active.hard_monotonic + 1)
    session.observe()
    session.owner.retire.assert_called_once_with()
    assert receipt.record_path.exists()


def test_busy_record_cannot_suspend_original_hard_bound(disposable, monkeypatch):
    session, _, environment = disposable
    enter(session, environment=environment)
    observed = session._active[1]

    @contextmanager
    def busy(*_args, **_kwargs):
        raise BlockingIOError(errno.EACCES, "busy")
        yield

    monkeypatch.setattr(store, "_locked", busy)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: observed.hard_monotonic - 1)
    session.observe()
    session.owner.retire.assert_not_called()
    monkeypatch.setattr(store, "_monotonic_clock", lambda: observed.hard_monotonic)
    session.observe()
    session.owner.retire.assert_called_once_with()


def test_child_completion_requires_the_duplicated_original_exit(session):
    child = Mock()
    child.poll.return_value = None

    @contextmanager
    def observed(identity, handle):
        assert identity == ProcessIdentity(4300, "windows:124")
        assert handle == 77
        yield child

    session.owner.child.side_effect = observed
    reservation = session.dispatch({"command": "reserve", "parameters": session.parameters})
    process = {"pid": 4300, "birth": "windows:124", "handle": 77}
    session.dispatch({"command": "bind", "receipt": reservation, "process": process})
    with pytest.raises(ValueError, match="observed exit"):
        session.dispatch({"command": "finish-child", "receipt": reservation, "process": process})
    receipt = shell._receipt(reservation)
    assert store._read(receipt.record_path)["state"] == "bound"
    child.poll.return_value = 0
    session.dispatch({"command": "finish-child", "receipt": reservation, "process": process})
    assert store._read(receipt.record_path)["state"] == "finished"


def test_reconcile_carries_original_shell_self_cleanup_boundary(session, monkeypatch):
    reconcile = Mock(return_value=[])
    monkeypatch.setattr(store, "reconcile", reconcile)
    assert session.dispatch({"command": "reconcile"}) == []
    reconcile.assert_called_once_with(None, 128, 5, _caller_pid=4200)


def test_wire_sequences_requests_and_preserves_explicit_errors(session, monkeypatch):
    nonce = "a" * 32
    requests = [
        {"command": "reserve", "parameters": session.parameters},
        {"command": "unknown"},
    ]
    payload = b"".join(
        base64.b64encode(json.dumps({**request, "nonce": nonce, "sequence": index}).encode())
        + b"\n"
        for index, request in enumerate(requests, 1)
    )
    monkeypatch.setattr(shell.sys, "stdin", SimpleNamespace(buffer=SimpleNamespace(raw=io.BytesIO(payload))))
    output = io.StringIO()
    monkeypatch.setattr(shell.sys, "stdout", output)
    assert shell._serve_requests(session, nonce) == 0
    responses = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [row["sequence"] for row in responses] == [1, 2]
    assert all(row["nonce"] == nonce for row in responses)
    assert responses[0]["ok"] is True
    assert DaemonInstanceReceipt(
        responses[0]["result"]["instance_id"], Path(session.parameters["registry_root"]),
    ).record_path.exists()
    assert responses[1] == {
        "ok": False, "error": "unknown instance shell operation", "sequence": 2, "nonce": nonce,
    }


@pytest.mark.parametrize("sequence,nonce", [(0, "a" * 32), (True, "a" * 32), (1, "b" * 32)])
def test_uncorrelated_wire_never_dispatches(sequence, nonce, session, monkeypatch):
    request = {"sequence": sequence, "nonce": nonce, "command": "reserve"}
    raw = base64.b64encode(json.dumps(request).encode()) + b"\n"
    monkeypatch.setattr(shell.sys, "stdin", SimpleNamespace(buffer=SimpleNamespace(raw=io.BytesIO(raw))))
    session.dispatch = Mock(side_effect=AssertionError("uncorrelated mutation"))
    with pytest.raises(ValueError, match="uncorrelated"):
        shell._serve_requests(session, "a" * 32)
    session.dispatch.assert_not_called()
