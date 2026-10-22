"""Loopback, installed and opt-in OpenSSH contracts, not production adapters."""

from dataclasses import replace
from contextlib import AsyncExitStack
import asyncio
import errno
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
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


async def openssh_consumer_scenario(directory, mode):
    import pwd

    from agent_procutil import no_window_kwargs
    from ssh_manager.config_sources import SSHConfig
    from ssh_manager.process import terminate_ssh_process_tree
    from ssh_manager.proxy import create_ssh_subprocess
    from ssh_manager.reverse_candidates import (
        FiniteReverseForward, ReverseEndpoint, ReverseEvidence, ReverseForwardError,
    )

    repository = Path(__file__).resolve().parents[2]
    import ssh_manager
    assert Path(ssh_manager.__file__).resolve().is_relative_to(repository / "libs/ssh-manager/src")
    sshd = shutil.which("sshd")
    keygen = shutil.which("ssh-keygen")
    assert sshd and keygen and shutil.which("ssh"), "explicit OpenSSH lane requires existing binaries"
    secret = secrets.token_bytes(32)
    ports = []
    occupied = alternate = independent = control = target = 0
    owned = []
    observed_pids = set()
    writers = set()
    handlers = set()
    server = ssh_process = log_stream = None
    log = directory / "sshd.log"
    cleanup = AsyncExitStack()

    def listening():
        return {
            int(row.split()[1].split(":")[1], 16)
            for row in Path("/proc/net/tcp").read_text().splitlines()[1:]
            if row.split()[3] == "0A"
        }

    async def serve(reader, writer):
        handlers.add(asyncio.current_task())
        writers.add(writer)
        try:
            value = await asyncio.wait_for(reader.readline(), 2)
            writer.write(hmac.new(secret, value, hashlib.sha256).hexdigest().encode() + b"\n")
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            finally:
                writers.discard(writer)

    async def verify(port):
        reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2)
        try:
            value = secrets.token_hex(24).encode() + b"\n"
            writer.write(value)
            await writer.drain()
            response = await asyncio.wait_for(reader.readline(), 2)
            assert hmac.compare_digest(
                response.strip(), hmac.new(secret, value, hashlib.sha256).hexdigest().encode(),
            )
        finally:
            writer.close()
            await writer.wait_closed()

    async def close_application():
        server.close()
        await server.wait_closed()
        pending = tuple(writers)
        for writer in pending:
            writer.close()
        await asyncio.wait_for(asyncio.gather(
            *(writer.wait_closed() for writer in pending), *handlers,
        ), 5)

    try:
        for _ in range(4):
            sock = listener()
            ports.append(sock)
            cleanup.callback(sock.close)
        occupied, alternate, independent, control = [sock.getsockname()[1] for sock in ports]
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        cleanup.push_async_callback(close_application)
        target = server.sockets[0].getsockname()[1]
        for name in ("host", "client"):
            subprocess.run(
                [keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(directory / name)],
                stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=10,
                **no_window_kwargs(),
            )
        grants = [
            "restrict", "port-forwarding",
            *(f'permitlisten="127.0.0.1:{port}"' for port in (occupied, alternate, independent)),
            f'permitopen="127.0.0.1:{target}"',
        ]
        authorized = directory / "authorized_keys"
        authorized.write_text(",".join(grants) + " " + (directory / "client.pub").read_text())
        authorized.chmod(0o600)
        alias = "fixture-" + secrets.token_hex(6)
        public = (directory / "host.pub").read_text().split()
        (directory / "known_hosts").write_text(f"{alias} {public[0]} {public[1]}\n")
        username = pwd.getpwuid(os.getuid()).pw_name
        server_config = directory / "sshd_config"
        server_config.write_text("\n".join([
            f"Port {control}", "ListenAddress 127.0.0.1",
            f"HostKey {directory / 'host'}", f"PidFile {directory / 'sshd.pid'}",
            f"AuthorizedKeysFile {authorized}", f"AllowUsers {username}",
            "AuthenticationMethods publickey", "PubkeyAuthentication yes",
            "PasswordAuthentication no", "KbdInteractiveAuthentication no", "UsePAM no",
            "StrictModes yes", "PermitRootLogin no", "UseDNS no", "MaxSessions 0",
            "AllowTcpForwarding yes", "AllowAgentForwarding no", "X11Forwarding no",
            "PermitTTY no", "GatewayPorts no", "LogLevel DEBUG3",
            "PermitListen " + " ".join(
                f"127.0.0.1:{port}" for port in (occupied, alternate, independent)
            ),
            f"PermitOpen 127.0.0.1:{target}", "",
        ]))
        client_config = directory / "ssh_config"
        client_config.write_text("\n".join([
            f"Host {alias}", " HostName 127.0.0.1", f" Port {control}", f" User {username}",
            f" IdentityFile {directory / 'client'}", " IdentitiesOnly yes", " IdentityAgent none",
            " StrictHostKeyChecking yes", f" HostKeyAlias {alias}",
            f" UserKnownHostsFile {directory / 'known_hosts'}",
            " GlobalKnownHostsFile /dev/null", " ProxyCommand none",
            " ControlMaster no", " ControlPath none", "",
        ]))
        client_config.chmod(0o600)
        config = SSHConfig(host_alias=alias, config_file=str(client_config))
        subprocess.run(
            [sshd, "-t", "-f", str(server_config)],
            stdin=subprocess.DEVNULL, capture_output=True, check=True, timeout=10,
            **no_window_kwargs(),
        )
        for sock in ports[1:]:
            sock.close()
        log_stream = log.open("wb")
        cleanup.callback(log_stream.close)
        ssh_process = await create_ssh_subprocess(
            sshd, "-D", "-e", "-f", str(server_config), config=config,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=log_stream,
        )
        cleanup.push_async_callback(terminate_ssh_process_tree, ssh_process)
        for _ in range(100):
            assert ssh_process.returncode is None, "isolated rootless sshd exited"
            if control in listening():
                break
            await asyncio.sleep(0.01)
        assert control in listening()

        def transport_for(permitted, correlation, timeout):
            attempts = []

            async def observe(attempt):
                attempts.append(attempt)
                observed_pids.add(attempt.process_pid)
                assert attempt.request_id == correlation
                if not attempt.process_settled:
                    assert attempt.endpoint.port == occupied
                    assert ports[0].fileno() >= 0
                    diagnostic = log.read_text(errors="replace")
                    assert "Address already in use" in diagnostic and str(occupied) in diagnostic
                    return ReverseEvidence(attempt.attempt_id, "occupied", "collision")
                await verify(attempt.endpoint.port)
                return ReverseEvidence(attempt.attempt_id, "ready", "readiness")

            transport = FiniteReverseForward(
                config, tuple(ReverseEndpoint(item.host, item.port) for item in permitted),
                request_id=correlation, observe=observe, ready_timeout=min(timeout, 10),
                host_port_resolver=lambda: target,
            )
            owned.append(transport)
            cleanup.push_async_callback(transport.stop)
            return transport, attempts

        peer, _ = transport_for((Listener("127.0.0.1", independent),), "independent", 10)
        await peer.establish()
        await verify(independent)

        class SSHJournalFixture(JournalOwner):
            """The canonical synthetic journal composed with the actual SSH leaf."""

            async def admit(self, selected, *, timeout):
                admitted = await super().admit(selected, timeout=timeout)
                if isinstance(admitted, Admission):
                    admitted = replace(admitted, valid_until=int(time.time()) + 60)
                    self.records[selected.correlation]["admission"] = admitted
                return admitted

            async def reserve(self, admitted, *, timeout):
                from fleet_manager.reverse_listener_reservations import Pending

                self.called("reserve", admitted, timeout)
                row = self.records[admitted.request.correlation]
                assert row["admission"] == admitted and "dispatch" not in row
                refused = self.current_rejection(admitted.request)
                if refused is not None:
                    row["terminal"] = refused
                    return refused
                row["dispatch"] = True
                transport, attempts = transport_for(
                    admitted.request.permitted, admitted.request.correlation, timeout,
                )
                row["transport"], row["attempts"] = transport, attempts
                try:
                    await asyncio.wait_for(transport.establish(), timeout)
                except ReverseForwardError as error:
                    if (error.status.state == "unavailable"
                            and error.status.reason == "candidates-exhausted"):
                        refused = Rejected(
                            admitted.request, "unavailable", "candidates-exhausted",
                        )
                        row["terminal"] = refused
                        return refused
                    return Pending(admitted.request)
                status = transport.reverse_status
                assert status.state == "ready"
                attempt = status.attempt
                candidate = Candidate(
                    admitted, Listener(attempt.endpoint.host, attempt.endpoint.port), attempt.attempt_id,
                )
                row["candidate"] = candidate
                self.held[candidate.resource] = candidate
                return candidate

            async def publish(self, candidate, *, timeout):
                await asyncio.wait_for(verify(candidate.listener.port), timeout)
                if mode == "stale-generation":
                    self.generation += 1
                return await super().publish(candidate, timeout=timeout)

            async def discard(self, candidate, *, timeout):
                row = self.records[candidate.admission.request.correlation]
                assert row["candidate"] == candidate and "rejected" in row
                status = await asyncio.wait_for(
                    row["transport"].stop_attempt(candidate.resource), timeout,
                )
                if status is None or status.state != "stopped":
                    return "unknown"
                return await super().discard(candidate, timeout=timeout)

        permitted = (Listener("127.0.0.1", occupied), Listener("127.0.0.1", alternate))
        journal = SSHJournalFixture(permitted)
        journal.target = hashlib.sha256(secret).hexdigest()
        journal.policy = hashlib.sha256(authorized.read_bytes()).hexdigest()
        selected = request(
            correlation=mode, permitted=permitted,
            target_identity=journal.target, policy_digest=journal.policy,
        )
        if mode == "lost-acknowledgement":
            def interrupt(stage, value):
                if stage == "published":
                    raise TimeoutError("lost fixture publication acknowledgement")
            journal.intercept = interrupt
        first = await coordinate(selected, owner=journal)
        row = journal.records[selected.correlation]
        transport = row["transport"]
        assert len(row["attempts"]) == 2 and journal.calls.count("reserve") == 1
        assert row["candidate"].resource == row["attempts"][-1].attempt_id
        if mode == "stale-generation":
            assert first.state == "conflict" and not transport.is_alive
            assert journal.held == {} and journal.calls.count("discard") == 1
        else:
            assert first.state == ("unknown" if mode == "lost-acknowledgement" else "published")
            assert transport.is_alive and journal.calls.count("discard") == 0
            await verify(alternate)
            again = await coordinate(selected, owner=journal)
            assert again.state == "published" and again.publication.generation == 1
            assert len(row["attempts"]) == 2 and journal.calls.count("reserve") == 1
        await verify(independent)
        assert_original_listener_usable(ports[0])
        assert "Accepted publickey" in log.read_text()
    finally:
        await cleanup.aclose()
        assert all(not transport.is_alive for transport in owned)
        if ssh_process is not None:
            assert ssh_process.returncode is not None
        for _ in range(100):
            if not {occupied, alternate, independent, control, target}.intersection(listening()):
                break
            await asyncio.sleep(0.01)
        assert not {occupied, alternate, independent, control, target}.intersection(listening())
        assert not any(Path(f"/proc/{pid}").exists() for pid in observed_pids)


@pytest.mark.skipif(sys.platform != "linux", reason="the isolated OpenSSH fixture is Linux-only")
@pytest.mark.skipif(
    os.environ.get("FLEET_MANAGER_TEST_OPENSSH") != "1",
    reason="temporary OpenSSH test keys require explicit FLEET_MANAGER_TEST_OPENSSH=1",
)
@pytest.mark.parametrize("mode", ["published", "lost-acknowledgement", "stale-generation"])
def test_real_openssh_coordinator_consumer_retains_exact_resource_and_original_custody(mode, monkeypatch):
    import pwd

    assert os.geteuid() != 0, "the isolated OpenSSH lane requires an existing unprivileged user"
    repository = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(repository / "libs/ssh-manager/src"))
    monkeypatch.syspath_prepend(str(repository / "libs/agent-procutil/src"))
    home = pwd.getpwuid(os.getuid()).pw_dir
    # StrictModes rejects an authorized-key path beneath a world-writable /tmp.
    with tempfile.TemporaryDirectory(prefix="fleet-reverse-ssh-", dir=home) as temporary:
        directory = Path(temporary)
        asyncio.run(asyncio.wait_for(openssh_consumer_scenario(directory, mode), 20))
    assert not directory.exists()
