"""Stock-PowerShell owner interoperates with unchanged Python participant v1."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from contextlib import ExitStack, contextmanager
from multiprocessing.connection import Client
from pathlib import Path

import pytest

import agent_procutil as pu
from single_instance_lease.operation import OperationLock, OperationLockedError
from update_driver import ParticipantError, borrowed_custody, participant, participant_environment

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows participant witness")
SOURCE = Path(__file__).resolve().parents[2]
OPERATION = SOURCE / "single-instance-lease" / "src" / "single_instance_lease" / "operation.ps1"
PARTICIPANT = SOURCE / "update-driver" / "src" / "update_driver" / "participant.ps1"
KEY = "UPDATE_DRIVER_WITNESS"


def line(child):
    receipt = queue.Queue()
    reader = threading.Thread(target=lambda: receipt.put(child.stdout.readline()), daemon=True)
    reader.start()
    value = receipt.get(timeout=25)
    reader.join(timeout=1)
    assert value, "native witness fixture ended before its control receipt"
    return value.strip()


@contextmanager
def owner(area, mode="owned", *, expected_death=False):
    root = area / "execution space"
    root.mkdir()
    shell = Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    fixture = Path(__file__).with_name("fixture_native_participant.ps1")
    child, job = pu.spawn_sync_in_kill_on_close_job(
        [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(fixture),
         str(OPERATION), str(PARTICIPANT), str(root), mode],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **pu.no_window_kwargs(),
    )
    try:
        yield root, child
        if child.poll() is None:
            child.stdin.write("stop\n")
            child.stdin.flush()
        _, error = child.communicate(timeout=10)
        if expected_death:
            assert child.returncode != 0
        else:
            assert child.returncode == 0, error
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)
        if job is not None:
            job.close()


def control(child, command):
    child.stdin.write(command + "\n")
    child.stdin.flush()
    assert line(child) == {
        "release": "released", "close": "closed", "replace-owner": "replaced",
    }[command]


def test_native_witness_joins_consumes_transport_and_excludes_python(area, monkeypatch):
    with owner(area) as (root, child):
        transport = line(child)
        with pytest.raises(OperationLockedError):
            OperationLock(root / "execution.lock").acquire()
        monkeypatch.setenv(KEY, transport)
        with borrowed_custody(root=root) as custody:
            assert custody is not None
            assert KEY not in os.environ
            custody.assert_owned()
            assert participant_environment() == {KEY: transport}
            # A second simultaneous, explicit participant must use the same protocol.
            with participant(root=root, witness=transport) as nested:
                nested.assert_owned()
            custody.assert_owned()
        assert participant_environment() == {}
        control(child, "close")
        # Closing the witness is not permission to release its native owner.
        with pytest.raises(OperationLockedError):
            OperationLock(root / "execution.lock").acquire()
        control(child, "release")
        with OperationLock(root / "execution.lock") as lock:
            lock.assert_owned()


def test_forged_and_mismatched_witnesses_refuse_without_retiring_owner(area):
    with owner(area) as (root, child):
        transport = line(child)
        forged = json.loads(transport)
        forged["key"] = "00" * 32
        with pytest.raises(ParticipantError):
            participant(root=root, witness=json.dumps(forged))
        with pytest.raises(ParticipantError, match="mismatch"):
            participant(root=root, witness=transport, request_id="b" * 32)
        with pytest.raises(ParticipantError, match="mismatch"):
            participant(root=root.parent, witness=transport)
        with participant(root=root, witness=transport) as custody:
            custody.assert_owned()


@pytest.mark.parametrize("loss", ["release", "close", "death", "replace-owner"])
def test_actual_owner_or_witness_loss_revokes_joined_and_new_participants(area, loss):
    with owner(area, expected_death=loss == "death") as (root, child):
        transport = line(child)
        with participant(root=root, witness=transport) as custody:
            custody.assert_owned()
            if loss == "death":
                child.kill()
                child.wait(timeout=5)
            else:
                control(child, loss)
            with pytest.raises(ParticipantError):
                custody.assert_owned()
        with pytest.raises(ParticipantError):
            participant(root=root, witness=transport)
        if loss in {"release", "death"}:
            with OperationLock(root / "execution.lock") as lock:
                lock.assert_owned()


@pytest.mark.parametrize("mode", ["wrong-lock", "disposed"])
def test_native_witness_requires_matching_actual_handle(area, mode):
    with owner(area, mode) as (_, child):
        assert line(child) == "refused"


@pytest.mark.parametrize(
    "payload", [
        b"null", b"[]", b"{", b"x" * 16385,
        b'{"nonce":1e99999}', b'{"nonce":922337203685477580812345678901234567890}',
        b'{"nonce":{"value":[[[[[[[[[0]]]]]]]]]}}',
        b'{"__type":[]}', b'{"__type":null}', b'{"__type":{}}', b'{"__type":0}',
        b'{"nonce":{"__type":{}}}',
    ],
    ids=[
        "null", "array", "malformed", "oversized", "numeric-overflow", "large-integer", "recursion",
        "type-array", "type-null", "type-object", "type-number", "nested-type-object",
    ],
)
def test_malformed_native_handshake_is_bounded_without_retiring_real_witness(area, payload):
    with owner(area) as (root, child):
        transport = line(child)
        address = json.loads(transport)["address"]
        connection = Client(address, family="AF_PIPE", authkey=None)
        try:
            assert connection.poll(3)
            connection.recv_bytes(16384)
            connection.send_bytes(payload)
            assert connection.poll(3)
            with pytest.raises((EOFError, OSError)):
                connection.recv_bytes(16384)
        finally:
            connection.close()
        with participant(root=root, witness=transport) as custody:
            custody.assert_owned()


def test_saturated_witness_refuses_excess_without_revoking_existing_participants(area):
    with owner(area) as (root, child):
        transport = line(child)
        with ExitStack() as participants:
            joined = [
                participants.enter_context(participant(root=root, witness=transport))
                for _ in range(16)
            ]
            with pytest.raises(ParticipantError):
                participant(root=root, witness=transport)
            assert child.poll() is None
            for custody in joined:
                custody.assert_owned()
        # Python close is asynchronous from the server's disconnect observation.
        deadline = time.monotonic() + 3
        while True:
            try:
                replacement = participant(root=root, witness=transport)
                break
            except ParticipantError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.01)
        with replacement as custody:
            custody.assert_owned()


def test_stalled_handshake_timeout_preserves_owner_and_genuine_participant(area):
    with owner(area) as (root, child):
        transport = line(child)
        with participant(root=root, witness=transport) as custody:
            connection = Client(json.loads(transport)["address"], family="AF_PIPE", authkey=None)
            try:
                assert connection.poll(3)
                connection.recv_bytes(16384)
                # Send no proof: the real pending native ReadAsync must time out safely.
                assert connection.poll(4)
                with pytest.raises((EOFError, OSError)):
                    connection.recv_bytes(16384)
            finally:
                connection.close()
            assert child.poll() is None
            custody.assert_owned()
        with participant(root=root, witness=transport) as custody:
            custody.assert_owned()


def test_native_directory_root_trailing_separator_matches_python_path(area):
    with owner(area, "trailing-root") as (root, child):
        transport = line(child)
        assert json.loads(transport)["root"] == str(root)
        with participant(root=root, witness=transport) as custody:
            custody.assert_owned()
