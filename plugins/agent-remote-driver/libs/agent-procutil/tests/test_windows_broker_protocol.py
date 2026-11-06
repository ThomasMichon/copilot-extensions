"""Finite brokerage retains real control before resume and ambiguous failure."""

from __future__ import annotations

from collections import deque
from unittest.mock import Mock

import pytest
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError, Refusal

from agent_procutil import DaemonSpawnError
from agent_procutil import windows_broker_native as native
from agent_procutil import windows_broker_protocol as protocol


@pytest.fixture(autouse=True)
def isolated_pending_custody(monkeypatch):
    monkeypatch.setattr(protocol, "_pending", [])
    monkeypatch.setattr(protocol, "_pending_closes", [])


def spec():
    return {
        "argv": [r"C:\runtime\python.exe", "argument with spaces", "", "\u65e5\n&"],
        "cwd": r"C:\runtime\state",
        "env": {"VALUE": "live-only-fixture\n&", "EMPTY": ""},
        "creationflags": 0x08000000,
        "log_path": r"C:\runtime\logs\out.log",
        "stderr_log_path": None,
    }


def request():
    return protocol.LaunchRequest.parse(spec())


def wire(monkeypatch, incoming, *, fail_write=None):
    events = []
    messages = deque(incoming)
    channel = Mock()
    channel.close.side_effect = lambda: events.append("close")
    channel.read_exact.side_effect = PrivateIPCError(Refusal.CLOSED)

    def read(channel, deadline):
        deadline.remaining()
        message = messages.popleft()
        if isinstance(message, BaseException):
            raise message
        events.append(("read", message))
        return message

    def write(channel, message, deadline):
        deadline.remaining()
        events.append(("write", message))
        if fail_write is not None and fail_write in message:
            raise PrivateIPCError(Refusal.CLOSED)

    monkeypatch.setattr(protocol, "read_frame", read)
    monkeypatch.setattr(protocol, "write_frame", write)
    return channel, events


def child():
    result = Mock(pid=92001)
    result.offer.return_value = {"source_handle": 321}
    return result


def test_request_is_exact_and_has_no_secret_repr():
    value = request()
    assert value.frame() == spec()
    assert "live-only-fixture" not in repr(value)
    assert "argument with spaces" not in repr(value)


@pytest.mark.parametrize(
    "key,value",
    [
        ("argv", []), ("argv", "python"), ("argv", ["py", "\0"]),
        ("argv", [False]), ("argv", ["py"] * 4097),
        ("env", {"A": "1", "a": "2"}), ("env", {"A=B": "x"}),
        ("env", {"": "x"}), ("env", {"A": False}), ("env", []),
        ("env", {"A": "\0"}), ("cwd", "C:relative"), ("cwd", r"\relative"),
        ("cwd", "relative"), ("log_path", ""), ("stderr_log_path", "relative"),
        ("creationflags", True), ("creationflags", 0),
        ("creationflags", 0x08000000 | 0x01000000),
        ("creationflags", 0x08000000 | 4),
    ],
)
def test_invalid_request_is_redacted(key, value):
    data = {**spec(), key: value}
    with pytest.raises(DaemonSpawnError, match="invalid request") as caught:
        protocol.LaunchRequest.parse(data)
    assert caught.value.cleanup_confirmed
    assert "live-only-fixture" not in str(caught.value)


def test_unknown_fields_are_not_extension_authority():
    with pytest.raises(DaemonSpawnError, match="invalid request"):
        protocol.LaunchRequest.parse({**spec(), "shell": True})


def test_server_only_resumes_after_native_adoption(monkeypatch):
    resident = child()
    channel, events = wire(
        monkeypatch, [spec(), {"adopted": resident.pid}, {"committed": resident.pid}],
    )
    resident.resume.side_effect = lambda: events.append("resume")
    stage = Mock(return_value=resident)
    protocol.serve(channel, stage, Deadline(2))
    stage.assert_called_once()
    assert stage.call_args.args[0].frame() == spec()
    assert events.index(("read", {"adopted": resident.pid})) < events.index("resume")
    assert events.index("resume") < events.index(("write", {"started": resident.pid}))
    resident.retire.assert_not_called()
    resident.close.assert_called_once()


@pytest.mark.parametrize("point", ["stage", "offer", "adopt", "resume", "started", "commit"])
def test_server_reaps_uncommitted_child_at_every_failure(monkeypatch, point):
    resident = child()
    failure = PrivateIPCError(Refusal.CLOSED)
    incoming = [
        spec(), failure if point == "adopt" else {"adopted": resident.pid},
        failure if point == "commit" else {"committed": resident.pid},
    ]
    channel, _ = wire(
        monkeypatch, incoming, fail_write="started" if point == "started" else None,
    )
    stage = Mock(side_effect=failure) if point == "stage" else Mock(return_value=resident)
    if point == "offer":
        resident.offer.side_effect = failure
    if point == "resume":
        resident.resume.side_effect = failure
    with pytest.raises(PrivateIPCError):
        protocol.serve(channel, stage, Deadline(2))
    if point == "stage":
        resident.retire.assert_not_called()
    else:
        resident.retire.assert_called_once()
        resident.close.assert_called_once()
    channel.close.assert_called()


def test_server_rejects_wrong_adoption_before_resume(monkeypatch):
    resident = child()
    channel, _ = wire(monkeypatch, [spec(), {"adopted": resident.pid + 1}])
    with pytest.raises(DaemonSpawnError, match="transaction refused"):
        protocol.serve(channel, Mock(return_value=resident), Deadline(2))
    resident.resume.assert_not_called()
    resident.retire.assert_called_once()


def test_client_joins_finite_broker_not_resident(monkeypatch):
    resident = child()
    channel, events = wire(
        monkeypatch, [{"staged": resident.offer()}, {"started": resident.pid}],
    )
    peer = Mock()
    peer.adopt.side_effect = lambda *_: events.append("native-adopt") or resident
    peer.wait_exit.return_value = 0
    assert protocol.launch(channel, peer, request(), Deadline(2)) is resident
    assert events.index("native-adopt") < events.index(("write", {"adopted": resident.pid}))
    peer.wait_exit.assert_called_once()
    resident.wait.assert_not_called()
    resident.resume.assert_not_called()
    resident.retire.assert_not_called()
    resident.close.assert_not_called()
    channel.close.assert_called_once()


@pytest.mark.parametrize("retirement_fails", [False, True])
def test_channel_close_failure_still_retires_pinned_child(monkeypatch, retirement_fails):
    resident = child()
    if retirement_fails:
        resident.retire.side_effect = OSError("private retirement detail")
    channel, _ = wire(monkeypatch, [
        {"staged": resident.offer()}, {"started": resident.pid},
    ])
    channel.close.side_effect = OSError("private handle detail")
    peer = Mock()
    peer.adopt.return_value = resident
    peer.wait_exit.return_value = 0
    with pytest.raises(DaemonSpawnError, match="transaction failed") as caught:
        protocol.launch(channel, peer, request(), Deadline(2))
    assert caught.value.process_created is True
    assert caught.value.admission_sent is True
    assert not caught.value.cleanup_confirmed
    assert "private" not in str(caught.value)
    resident.retire.assert_called_once()
    assert protocol._pending_closes == [channel]
    assert protocol._pending == ([resident] if retirement_fails else [])
    with pytest.raises(DaemonSpawnError, match="cleanup unconfirmed"):
        protocol.retry_cleanup(Deadline(1))
    channel.close.side_effect = None
    resident.retire.side_effect = None
    protocol.retry_cleanup(Deadline(1))
    assert protocol._pending_closes == []
    assert protocol._pending == []


@pytest.mark.parametrize("bind_fails", [False, True])
def test_client_records_suspended_child_before_admission(monkeypatch, bind_fails):
    from agent_procutil import _daemon_instance_identity

    resident = child()
    identity = resident.pid, "windows:400"
    monkeypatch.setattr(_daemon_instance_identity, "created_process_identity", lambda _: identity)
    channel, events = wire(
        monkeypatch, [{"staged": resident.offer()}, {"started": resident.pid}],
    )
    peer = Mock()
    peer.adopt.return_value = resident
    peer.wait_exit.return_value = 0

    def bind(value):
        assert value is resident
        events.append("receipt-bound")
        if bind_fails:
            raise OSError("private receipt failure")

    if bind_fails:
        with pytest.raises(DaemonSpawnError) as caught:
            protocol.launch(channel, peer, request(), Deadline(2), before_admit=bind)
        assert caught.value.process_created is True
        assert caught.value.process_identity == identity
        assert caught.value.admission_sent is False
        assert caught.value.cleanup_confirmed
        assert "private" not in str(caught.value)
        assert not any(event == ("write", {"adopted": resident.pid}) for event in events)
        resident.retire.assert_called_once()
    else:
        assert protocol.launch(
            channel, peer, request(), Deadline(2), before_admit=bind,
        ) is resident
        assert events.index("receipt-bound") < events.index(("write", {"adopted": resident.pid}))


@pytest.mark.parametrize("point", ["request", "offer", "adopt", "started", "commit", "exit"])
def test_client_failure_is_owned_and_never_retried(monkeypatch, point):
    resident = child()
    failure = PrivateIPCError(Refusal.CLOSED)
    channel, _ = wire(
        monkeypatch,
        [
            failure if point == "offer" else {"staged": resident.offer()},
            failure if point == "started" else {"started": resident.pid},
        ],
        fail_write={"request": "argv", "commit": "committed"}.get(point),
    )
    peer = Mock()
    peer.adopt.side_effect = failure if point == "adopt" else None
    peer.adopt.return_value = resident
    peer.wait_exit.return_value = 1 if point == "exit" else 0
    with pytest.raises(DaemonSpawnError, match="transaction failed") as caught:
        protocol.launch(channel, peer, request(), Deadline(2))
    adopted = point in {"started", "commit", "exit"}
    assert caught.value.cleanup_confirmed is adopted
    assert caught.value.process_created is (True if adopted else None)
    assert caught.value.admission_sent is adopted
    assert resident.retire.call_count == int(adopted)
    assert resident.close.call_count == int(adopted)


def test_unknown_child_requires_broker_confirmed_cleanup(monkeypatch):
    channel, _ = wire(monkeypatch, [PrivateIPCError(Refusal.CLOSED)])
    peer = Mock()
    peer.wait_exit.return_value = 1
    with pytest.raises(DaemonSpawnError) as caught:
        protocol.launch(channel, peer, request(), Deadline(2))
    assert caught.value.cleanup_confirmed


def test_cleanup_failure_prohibits_retry_even_after_known_child(monkeypatch):
    resident = child()
    resident.retire.side_effect = OSError("sensitive native error")
    channel, _ = wire(
        monkeypatch, [{"staged": resident.offer()}, PrivateIPCError(Refusal.CLOSED)],
    )
    peer = Mock()
    peer.adopt.return_value = resident
    with pytest.raises(DaemonSpawnError) as caught:
        protocol.launch(channel, peer, request(), Deadline(2))
    assert not caught.value.cleanup_confirmed
    assert "sensitive" not in str(caught.value)
    resident.close.assert_not_called()
    assert protocol._pending == [resident]
    with pytest.raises(DaemonSpawnError, match="cleanup unconfirmed"):
        protocol.retry_cleanup(Deadline(1))
    assert protocol._pending == [resident]
    resident.retire.side_effect = None
    protocol.retry_cleanup(Deadline(1))
    assert protocol._pending == []
    resident.close.assert_called_once()


def test_server_retains_failed_retirement_and_blocks_new_staging(monkeypatch):
    resident = child()
    resident.retire.side_effect = OSError("sensitive native error")
    channel, _ = wire(
        monkeypatch, [spec(), PrivateIPCError(Refusal.CLOSED)],
    )
    stage = Mock(return_value=resident)
    with pytest.raises(DaemonSpawnError, match="cleanup unconfirmed"):
        protocol.serve(channel, stage, Deadline(2))
    resident.close.assert_not_called()
    assert protocol._pending == [resident]
    with pytest.raises(DaemonSpawnError, match="cleanup unconfirmed"):
        protocol.serve(channel, stage, Deadline(2))
    assert stage.call_count == 1


def test_expired_request_never_stages(monkeypatch):
    channel, _ = wire(monkeypatch, [spec()])
    deadline = Deadline(2)
    monkeypatch.setattr(
        deadline, "remaining", Mock(side_effect=PrivateIPCError(Refusal.DEADLINE)),
    )
    stage = Mock()
    with pytest.raises(PrivateIPCError):
        protocol.serve(channel, stage, deadline)
    stage.assert_not_called()


def test_native_adoption_duplicates_through_original_broker_custody(monkeypatch):
    api = Mock()
    identity = PeerIdentity(92001, "400", "S-1-5-21-123")
    api.k.GetProcessId.return_value = identity.pid
    api.identity.return_value = identity
    monkeypatch.setattr(native, "native", lambda: api)
    duplicate = Mock(return_value=6001)
    monkeypatch.setattr(native, "_duplicate", duplicate)
    monkeypatch.setattr(native, "_outside_job", lambda child: True)
    monkeypatch.setattr(native.NativeChild, "poll", lambda child: None)
    monkeypatch.setattr(native.NativeChild, "close", Mock())
    peer = native.NativeBrokerPeer.__new__(native.NativeBrokerPeer)
    peer.process = Mock(_handle=5001)
    peer.process.poll.return_value = None
    peer.identity = PeerIdentity(91001, "300", identity.principal)
    offer = {
        "pid": identity.pid, "birth": identity.start_time,
        "principal": identity.principal, "source_handle": 99,
    }
    adopted = peer.adopt(offer, request())
    assert adopted._handle == 6001
    duplicate.assert_called_once_with(5001, 99)
    api.k.OpenProcess.assert_not_called()
    adopted.close()


@pytest.mark.parametrize("refusal", ["pid", "birth", "principal", "job"])
def test_native_adoption_rejects_unverified_child_without_signalling(monkeypatch, refusal):
    api = Mock()
    api.k.GetProcessId.return_value = 92001 if refusal != "pid" else 92002
    api.identity.return_value = PeerIdentity(92001, "400", "S-1-5-21-123")
    monkeypatch.setattr(native, "native", lambda: api)
    monkeypatch.setattr(native, "_duplicate", lambda *_: 6001)
    monkeypatch.setattr(native, "_outside_job", lambda child: refusal != "job")
    monkeypatch.setattr(native.NativeChild, "poll", lambda child: None)
    close, terminate = Mock(), Mock()
    monkeypatch.setattr(native.NativeChild, "close", close)
    monkeypatch.setattr(native.NativeChild, "terminate", terminate)
    peer = native.NativeBrokerPeer.__new__(native.NativeBrokerPeer)
    peer.process = Mock(_handle=5001)
    peer.process.poll.return_value = None
    peer.identity = PeerIdentity(91001, "300", "S-1-5-21-123")
    offer = {
        "pid": 92001, "birth": "401" if refusal == "birth" else "400",
        "principal": "different" if refusal == "principal" else peer.identity.principal,
        "source_handle": 99,
    }
    with pytest.raises(DaemonSpawnError):
        peer.adopt(offer, request())
    close.assert_called_once()
    terminate.assert_not_called()
