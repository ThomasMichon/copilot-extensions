"""Real Linux listener custody; no remote exec, shell, PTY or production endpoints."""

import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, TYPE_CHECKING, cast

import pytest

from agent_ssh_service.config import ServiceConfig, SSHServiceError
from agent_ssh_service.relay import AccountLookup, NativeRelay, PosixOperations, owns_listener
from agent_ssh_service.store import DirectoryStore

if TYPE_CHECKING:
    from services.ssh.tests.test_relay import join, relay_document
else:
    from test_relay import join, relay_document

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or getattr(os, "geteuid", lambda: 0)() == 0,
    reason="Native relay requires the real non-root Linux OpenSSH owner",
)


def prepare(config: ServiceConfig, tmp_path: Path) -> NativeRelay:
    import pwd
    import socket

    from agent_procutil.owned_process import run_caller_owned

    sshd = shutil.which("sshd")
    keygen = shutil.which("ssh-keygen")
    if not sshd or not keygen or not Path("/run/sshd").is_dir():
        pytest.skip("Explicit native OpenSSH prerequisites are unavailable")
    host = tmp_path / "host-key"
    result = run_caller_owned(
        [keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(host)], timeout=10,
        max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0
    value = relay_document(config)
    # Port zero is used only to select a disposable test endpoint; production is explicit.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    value["relay"].update(
        sshd=str(Path(sshd).resolve()), ssh_keygen=str(Path(keygen).resolve()),
        host_key_file=str(host), host_public_key=" ".join(host.with_suffix(".pub").read_text().split()[:2]),
        user=cast(AccountLookup, pwd).getpwuid(cast(PosixOperations, os).geteuid()).pw_name, port=port,
    )
    return NativeRelay(DirectoryStore(ServiceConfig.from_dict(value)))


async def wait_ready(owner: asyncio.Task[None], ready: asyncio.Event) -> None:
    waiter = asyncio.create_task(ready.wait())
    try:
        done, _ = await asyncio.wait({owner, waiter}, timeout=15,
                                     return_when=asyncio.FIRST_COMPLETED)
        if owner in done:
            await owner
        assert ready.is_set(), "Actual native relay did not retain its listening socket"
    finally:
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)


async def test_actual_sshd_is_owned_before_ready_and_settles_after_two_lifetimes(config: ServiceConfig, tmp_path: Path) -> None:
    relay = prepare(config, tmp_path)
    for _ in range(2):
        stop, ready = asyncio.Event(), asyncio.Event()
        owner = asyncio.create_task(relay.run(stop, ready))
        try:
            await wait_ready(owner, ready)
            assert cast(subprocess.Popen[bytes], relay.process).returncode is None
            assert owns_listener(cast(subprocess.Popen[bytes], relay.process).pid, "127.0.0.1", relay.selected["port"])
            relay.store.enroll("principal-worker-a", join())
            relay.reconcile()
            assert len((relay.root / "authorized_keys").read_text().splitlines()) == 1
        finally:
            stop.set()
            await asyncio.wait_for(owner, 10)
        assert cast(subprocess.Popen[bytes], relay.process).returncode is not None


async def test_native_listener_collision_is_not_accepted_as_our_readiness(config: ServiceConfig, tmp_path: Path) -> None:
    import socket

    relay = prepare(config, tmp_path)
    with socket.socket() as foreign:
        foreign.bind(("127.0.0.1", relay.selected["port"]))
        foreign.listen(1)
        stop, ready = asyncio.Event(), asyncio.Event()
        # Procfs can refuse observation before the exiting child publishes its status.
        with pytest.raises(SSHServiceError, match="native-relay-(exited|listener-unobservable)"):
            await asyncio.wait_for(relay.run(stop, ready), 15)
        assert not ready.is_set() and cast(subprocess.Popen[bytes], relay.process).returncode is not None
        assert foreign.getsockname()[1] == relay.selected["port"]


@pytest.mark.parametrize("failure", [PermissionError, FileNotFoundError])
async def test_unobservable_live_child_refuses_and_settles_without_readiness(
    config: ServiceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    failure: type[OSError],
) -> None:
    import agent_ssh_service.relay as implementation

    relay = prepare(config, tmp_path)

    def unavailable(*args: Any) -> None:
        raise failure("synthetic native observation refusal")

    monkeypatch.setattr(implementation, "owns_listener", unavailable)
    stop, ready = asyncio.Event(), asyncio.Event()
    with pytest.raises(SSHServiceError, match="native-relay-listener-unobservable"):
        await asyncio.wait_for(relay.run(stop, ready), 15)
    assert not ready.is_set() and cast(subprocess.Popen[bytes], relay.process).returncode is not None
