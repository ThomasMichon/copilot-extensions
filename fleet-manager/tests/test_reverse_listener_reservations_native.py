"""Real loopback resource contracts, not an OpenSSH or production-owner adapter."""

from dataclasses import replace
import asyncio
import errno
import hashlib
import shutil
import socket
import sys
import threading
import time

import pytest

from fleet_manager.reverse_listener_reservations import (
    Admission, Candidate, Listener, Rejected, reserve_reverse_listener as coordinate,
)
from test_reverse_listener_reservations import JournalOwner, request
from test_client_distribution import run, wheel as wheel


def reserve_reverse_listener(selected, *, owner):
    return asyncio.run(coordinate(selected, owner=owner))


def listener(port=0):
    owned = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            owned.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        owned.settimeout(2)
        owned.bind(("127.0.0.1", port))
        owned.listen()
        return owned
    except BaseException:
        owned.close()
        raise


def permitted_after(occupied, count=4):
    port = occupied.getsockname()[1]
    direction = 1 if port + count <= 65535 else -1
    return tuple(Listener("127.0.0.1", port + direction * index) for index in range(count + 1))


class SocketOwner(JournalOwner):
    """Isolated supplied-owner model with real sockets and synthetic identity.

    The model's finite bind loop stands in only for the test leaf. It does not
    implement or qualify the separately owned production SSH provider contract.
    """

    identity = b"synthetic-loopback-target"

    def __init__(self, permitted):
        super().__init__(permitted)
        self.target = hashlib.sha256(self.identity).hexdigest()
        self.lock = threading.Lock()
        self.sockets = {}
        self.closed = []
        self.ready = True
        self.barrier = None

    async def admit(self, requested, *, timeout):
        with self.lock:
            admitted = await super().admit(requested, timeout=timeout)
            if isinstance(admitted, Admission):
                admitted = replace(admitted, valid_until=int(time.time()) + 60)
                self.records[requested.correlation]["admission"] = admitted
            return admitted

    async def reserve(self, admitted, *, timeout):
        with self.lock:
            self.called("reserve", admitted, timeout)
            row = self.records[admitted.request.correlation]
            assert row["admission"] == admitted and "candidate" not in row
            refused = self.current_rejection(admitted.request)
            if refused is not None:
                row["terminal"] = refused
                return refused
            for selected in admitted.request.permitted:
                try:
                    owned = listener(selected.port)
                except OSError as error:
                    if error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) == 10048:
                        continue
                    raise
                resource = "socket-" + admitted.request.correlation
                candidate = Candidate(admitted, selected, resource)
                row["candidate"] = candidate
                self.held[resource] = candidate
                self.sockets[resource] = owned
                break
            else:
                rejected = Rejected(admitted.request, "unavailable", "candidates-exhausted")
                row["terminal"] = rejected
                return rejected
        self.intercept("bound", candidate)
        if self.barrier is not None:
            self.barrier.wait(timeout=5)
        return candidate

    async def publish(self, candidate, *, timeout):
        with self.lock:
            row = self.records[candidate.admission.request.correlation]
            if not self.ready:
                self.called("publish", candidate, timeout)
                row["rejected"] = Rejected(candidate.admission.request, "refused", "readiness-refused")
                return row["rejected"]
            owned = self.sockets[candidate.resource]
            assert owned.getsockname() == (candidate.listener.host, candidate.listener.port)
            with socket.create_connection(owned.getsockname(), timeout=min(timeout, 2)) as client:
                peer, _ = owned.accept()
                with peer:
                    peer.settimeout(min(timeout, 2))
                    peer.sendall(self.identity)
                observed = bytearray()
                while len(observed) < len(self.identity):
                    part = client.recv(len(self.identity) - len(observed))
                    assert part
                    observed.extend(part)
                assert hashlib.sha256(observed).hexdigest() == candidate.admission.request.target_identity
            return await super().publish(candidate, timeout=timeout)

    async def discard(self, candidate, *, timeout):
        with self.lock:
            row = self.records[candidate.admission.request.correlation]
            if (row.get("candidate") != candidate or "terminal" in row
                    or "rejected" not in row):
                return "unknown"
            result = await super().discard(candidate, timeout=timeout)
            owned = self.sockets.pop(candidate.resource)
            owned.close()
            self.closed.append(owned)
            return result

    def close_fixture(self):
        for owned in self.sockets.values():
            owned.close()
            self.closed.append(owned)
        self.sockets.clear()
        self.held.clear()
        assert all(owned.fileno() == -1 for owned in self.closed)


def selected_request(owner, permitted, **changes):
    return request(permitted=permitted, target_identity=owner.target, **changes)


def assert_original_listener_usable(occupied):
    with socket.create_connection(occupied.getsockname(), timeout=2):
        peer, _ = occupied.accept()
        peer.close()
    assert occupied.fileno() >= 0


def test_real_fixed_tuple_failure_then_retained_candidate_recovery_preserves_unknown_holder():
    occupied = listener()
    permitted = permitted_after(occupied)
    owner = SocketOwner(permitted)
    try:
        control = reserve_reverse_listener(selected_request(owner, permitted[:1]), owner=owner)
        assert (control.state, control.reason) == ("unavailable", "candidates-exhausted")
        assert_original_listener_usable(occupied)
        recovered = reserve_reverse_listener(
            selected_request(owner, permitted, correlation="operation-b"), owner=owner,
        )
        assert recovered.state == "published"
        candidate = recovered.publication.candidate
        retained = owner.sockets[candidate.resource]
        assert candidate.listener in permitted[1:]
        assert retained.getsockname()[1] == candidate.listener.port
        assert retained.fileno() >= 0 and owner.closed == []
        with pytest.raises(OSError) as error:
            listener(candidate.listener.port)
        assert (error.value.errno == errno.EADDRINUSE
                or getattr(error.value, "winerror", None) == 10048)
        assert_original_listener_usable(occupied)
    finally:
        owner.close_fixture()
        occupied.close()
    assert occupied.fileno() == -1


def test_single_occupied_grant_is_exhausted_without_touching_another_listener():
    occupied, independent = listener(), listener()
    permitted = (Listener("127.0.0.1", occupied.getsockname()[1]),)
    owner = SocketOwner(permitted)
    try:
        selected = selected_request(owner, permitted)
        first = reserve_reverse_listener(selected, owner=owner)
        assert (first.state, first.reason) == ("unavailable", "candidates-exhausted")
        assert reserve_reverse_listener(selected, owner=owner) == first
        assert owner.calls.count("reserve") == 1
        assert owner.sockets == {} and owner.closed == []
        assert_original_listener_usable(occupied)
        assert_original_listener_usable(independent)
    finally:
        owner.close_fixture()
        occupied.close()
        independent.close()


def test_readiness_refusal_closes_only_unpublished_owned_candidate():
    occupied = listener()
    permitted = permitted_after(occupied)
    owner = SocketOwner(permitted)
    owner.ready = False
    try:
        result = reserve_reverse_listener(
            selected_request(owner, permitted), owner=owner,
        )
        assert (result.state, result.reason) == ("refused", "readiness-refused")
        assert len(owner.closed) == 1 and owner.sockets == {} and owner.generation == 0
        assert owner.closed[0].fileno() == -1
        assert_original_listener_usable(occupied)
    finally:
        owner.close_fixture()
        occupied.close()


@pytest.mark.parametrize("stage", ["bound", "published"])
def test_lost_actual_resource_acknowledgement_never_rebinds_or_closes_possibly_published_socket(stage):
    occupied = listener()
    permitted = permitted_after(occupied)
    owner = SocketOwner(permitted)
    try:
        selected = selected_request(owner, permitted)

        def interrupt(current, value):
            if current == stage:
                raise TimeoutError("lost synthetic acknowledgement")

        owner.intercept = interrupt
        first = reserve_reverse_listener(selected, owner=owner)
        assert first.state == "unknown" and first.reobserve_required
        assert len(owner.sockets) == 1 and owner.closed == []
        retained = next(iter(owner.sockets.values()))
        assert retained.fileno() >= 0
        owner.intercept = lambda current, value: None
        again = reserve_reverse_listener(selected, owner=owner)
        assert again.state == ("published" if stage == "published" else "unknown")
        assert owner.calls.count("reserve") == 1 and "discard" not in owner.calls
        assert next(iter(owner.sockets.values())) is retained
        assert_original_listener_usable(occupied)
    finally:
        owner.close_fixture()
        occupied.close()


@pytest.mark.parametrize("method", ["admit", "reserve", "publish", "discard"])
def test_actual_asyncio_owner_timeout_retains_correlation_and_never_replays(method):
    occupied = listener()
    permitted = permitted_after(occupied)
    owner = SocketOwner(permitted)
    if method == "discard":
        owner.ready = False
    original = getattr(owner, method)

    async def lose_acknowledgement(*args, **kwargs):
        await original(*args, **kwargs)
        await asyncio.wait_for(asyncio.Event().wait(), timeout=0.001)

    setattr(owner, method, lose_acknowledgement)
    try:
        selected = selected_request(owner, permitted)
        first = reserve_reverse_listener(selected, owner=owner)
        phase = {
            "admit": "admission", "reserve": "reservation",
            "publish": "publication", "discard": "discard",
        }[method]
        assert (first.state, first.reason) == ("unknown", phase + "-timeout")
        assert first.reobserve_required and first.correlation == selected.correlation
        assert len(owner.closed) == (1 if method == "discard" else 0)
        assert owner.calls.count("discard") == (1 if method == "discard" else 0)
        setattr(owner, method, original)
        again = reserve_reverse_listener(selected, owner=owner)
        expected = {"publish": "published", "discard": "refused"}.get(method, "unknown")
        assert again.state == expected
        assert owner.calls.count("reserve") == (0 if method == "admit" else 1)
        assert len(owner.sockets) == (0 if method in {"admit", "discard"} else 1)
        assert_original_listener_usable(occupied)
    finally:
        owner.close_fixture()
        occupied.close()


def test_concurrent_real_bind_arbitration_and_generation_cas_preserve_the_winner():
    occupied = listener()
    permitted = permitted_after(occupied, 8)
    owner = SocketOwner(permitted)
    owner.barrier = threading.Barrier(2)
    results, errors = [], []
    workers = []
    try:
        def run(correlation):
            try:
                results.append(reserve_reverse_listener(
                    selected_request(owner, permitted, correlation=correlation), owner=owner,
                ))
            except BaseException as error:
                errors.append(error)

        workers = [
            threading.Thread(target=run, args=("operation-" + str(number),))
            for number in range(2)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=10)
        assert not any(worker.is_alive() for worker in workers)
        assert errors == [] and sorted(result.state for result in results) == ["conflict", "published"]
        winner = next(result.publication for result in results if result.state == "published")
        assert owner.generation == winner.generation == 1
        assert len(owner.sockets) == len(owner.closed) == 1
        assert owner.closed[0].fileno() == -1
        retained = owner.sockets[winner.candidate.resource]
        assert retained.fileno() >= 0
        assert asyncio.run(owner.discard(winner.candidate, timeout=2)) == "unknown"
        assert owner.sockets[winner.candidate.resource] is retained and retained.fileno() >= 0
        assert_original_listener_usable(occupied)
    finally:
        for worker in workers:
            worker.join(timeout=10)
        assert not any(worker.is_alive() for worker in workers)
        owner.close_fixture()
        occupied.close()


def test_late_old_generation_cleanup_cannot_close_a_published_successor():
    occupied = listener()
    permitted = permitted_after(occupied, 8)
    owner = SocketOwner(permitted)
    try:
        first = reserve_reverse_listener(selected_request(owner, permitted), owner=owner)
        second = reserve_reverse_listener(selected_request(
            owner, permitted, correlation="operation-b", expected_generation=1,
        ), owner=owner)
        assert first.state == second.state == "published"
        assert second.publication.generation == 2
        assert first.publication.candidate.listener != second.publication.candidate.listener
        successor = owner.sockets[second.publication.candidate.resource]
        assert asyncio.run(owner.discard(first.publication.candidate, timeout=2)) == "unknown"
        assert owner.sockets[second.publication.candidate.resource] is successor
        assert successor.fileno() >= 0 and owner.closed == []
        assert_original_listener_usable(occupied)
    finally:
        owner.close_fixture()
        occupied.close()


def test_installed_wheel_calls_the_coordinator_without_source_or_editable_imports(wheel, tmp_path):
    artifact, _ = wheel
    installed = tmp_path / "installed"
    outside = tmp_path / "without-source"
    outside.mkdir()
    unrelated = tmp_path / "adopter-state"
    unrelated.write_bytes(b"preserved")
    result = run([
        sys.executable, "-I", "-m", "pip", "install", "--no-index", "--no-deps",
        "--target", str(installed), str(artifact),
    ], cwd=outside)
    assert result.returncode == 0, result.stderr
    code = """
from pathlib import Path
import asyncio
import sys
import time
target = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(target))
import fleet_manager.reverse_listener_reservations as api
assert Path(api.__file__).resolve().is_relative_to(target)
assert not list(target.glob('*.egg-link'))
request = api.ReservationRequest(
    'operation-a', 'participant-a', 'session-a', 'epoch-a', 'a' * 64, 'b' * 64,
    0, (api.Listener('127.0.0.1', 24001),),
)
class Owner:
    def __init__(self):
        self.calls = []
        self.terminal = None
    async def admit(self, selected, *, timeout):
        self.calls.append('admit')
        assert selected == request and 0 < timeout <= 30
        return self.terminal or api.Admission(request, int(time.time()) + 60, b'fixture-only')
    async def reserve(self, admission, *, timeout):
        self.calls.append('reserve')
        assert admission.request == request and 0 < timeout <= 30
        return api.Candidate(admission, request.permitted[0], 'resource-a')
    async def publish(self, candidate, *, timeout):
        self.calls.append('publish')
        assert 0 < timeout <= 30
        self.terminal = api.Publication(candidate, 1)
        return self.terminal
    async def discard(self, candidate, *, timeout):
        raise AssertionError('published fixture must never be discarded')
owner = Owner()
first = asyncio.run(api.reserve_reverse_listener(request, owner=owner))
assert first.state == 'published' and first.publication.generation == 1
assert asyncio.run(api.reserve_reverse_listener(request, owner=owner)) == first
assert owner.calls == ['admit', 'reserve', 'publish', 'admit']
assert 'fixture-only' not in repr(first.to_dict())
print('installed-coordinator:published,idempotent,source-independent')
"""
    try:
        result = run([sys.executable, "-I", "-c", code, str(installed)], cwd=outside)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "installed-coordinator:published,idempotent,source-independent"
        assert unrelated.read_bytes() == b"preserved"
    finally:
        shutil.rmtree(installed)
    assert not installed.exists() and unrelated.read_bytes() == b"preserved"
