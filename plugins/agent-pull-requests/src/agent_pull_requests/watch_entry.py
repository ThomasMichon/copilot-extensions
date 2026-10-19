"""Governed on-demand entry, private discovery and the reserved passive process."""

from __future__ import annotations

import os
import socket
import sys
import time
from uuid import uuid4

from agent_procutil import spawn_daemon
from work_coalescing_singleton.client import DaemonUnavailable, request
from zdd.cutover import CutoverResult
from zdd.cutover_lock import CutoverLock

from .watch_control import WatchControlClient, WatchControlOwner, WatchControlServer
from .watch_custody import endpoint_tuple
from .watch_driver import WatchDriver
from .watch_invocation import WatchInvocation
from .watch_journal import WatchJournal, _regular_bytes, _target_selection, read_known_owner
from .watch_runtime import WatchInstance


def _client(invocation: WatchInvocation, document: dict, role: str) -> WatchControlClient:
    installation = invocation.fresh_installation()
    if document[role] is None or document["endpoints"][role] is None:
        raise PermissionError("watch owner discovery has no private endpoint custody")
    instance = WatchInstance.from_evidence(document[role], installation, environment={})
    return WatchControlClient(
        endpoint=endpoint_tuple(document["endpoints"][role]), instance=instance,
        installation=invocation.fresh_installation,
        transaction=lambda: (document["transactionId"], document["authorization"]),
    )


def dial(invocation: WatchInvocation) -> tuple[str, int, str] | None:
    invocation.require_current()
    installation = invocation.fresh_installation()
    try:
        document = read_known_owner(installation, environment={})
    except FileNotFoundError:
        if _regular_bytes(installation.run_root / "active.json") is not None:
            raise PermissionError("watch native route has no attributable owner journal")
        return None
    if document["phase"] not in {"committed", "complete", "rolled-back"}:
        return None
    from .watch_withdrawal import _require_selection, read, require_withdrawn_route

    withdrawal = read(installation, document)
    if withdrawal is not None:
        if withdrawal["phase"] == "complete":
            _require_selection(installation, withdrawal, withdrawn=True)
            from zdd import routing

            require_withdrawn_route(
                installation, routing.read_table(installation.run_root, strict=True),
                probe_writer=True,
            )
        return None
    role = "predecessor" if document["phase"] == "rolled-back" else "successor"
    if document[role] is None:
        return None
    if role == "successor":
        _target_selection(installation, document)
    else:
        from .watch_rollback import require_rollback_selection

        require_rollback_selection(installation, document)
    client = _client(invocation, document, role)
    health = client.health()
    if not all(health.get(key) is True for key in (
        "writerHeld", "readReady", "effectsEnabled",
    )):
        return None
    return client._endpoint


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def call(invocation: WatchInvocation, kind: str, payload: dict, *, boot: bool) -> dict:
    endpoint = dial(invocation)
    if endpoint is None and boot:
        ensure(invocation)
        endpoint = dial(invocation)
    if endpoint is None:
        return {"error": "no committed installation-local watch owner"}
    try:
        return request(
            *endpoint, kind=kind, key=uuid4().hex, payload=payload,
            request_deadline_s=5.0, completion_timeout_s=5.0,
        )
    except DaemonUnavailable:
        result = {"error": "installation-local watch request completion is unconfirmed"}
        if kind == "register":
            result["ambiguous_registration"] = True
        elif kind == "unregister":
            result["ambiguous_cancellation"] = True
        return result


def incumbent(
    invocation: WatchInvocation, guard: CutoverLock, *, timeout: float,
) -> WatchControlClient | None:
    """Resolve terminal custody under the caller's whole-transaction guard."""
    guard.require_owned()
    installation = invocation.fresh_installation()
    if not os.path.lexists(installation.run_root / "watch-owner-transaction.json"):
        from .watch_custody import require_absent

        if _regular_bytes(installation.run_root / "active.json") is not None:
            raise PermissionError("watch native route has no attributable owner journal")
        require_absent(installation)
        return None
    document = read_known_owner(installation, environment={})
    from .watch_withdrawal import finish, read

    if read(installation, document) is not None:
        finish(invocation, guard, document, timeout=timeout)
        return None
    if document["installation"] == installation.evidence():
        previous = WatchJournal.recover(installation, guard, environment={})
        document = previous._current()
        if document["phase"] not in {"complete", "rolled-back"}:
            from .watch_recovery import reconcile

            reconcile(previous, timeout=timeout)
            document = previous._current()
        previous.archive()
    if document["phase"] == "complete":
        _target_selection(installation, document)
    else:
        from .watch_rollback import require_rollback_selection

        require_rollback_selection(installation, document)
    role = "successor" if document["phase"] == "complete" else "predecessor"
    if document[role] is None:
        return None
    client = _client(invocation, document, role)
    health = client.health()
    if not all(health.get(key) is True for key in (
        "writerHeld", "readReady", "effectsEnabled",
    )):
        raise PermissionError("watch incumbent is not a verified active writer")
    return client


def run_target(
    journal: WatchJournal, invocation: WatchInvocation, *, poll_interval: float, timeout: float,
) -> CutoverResult:
    def spawn(port: int, instance_id: str):
        invocation.require_current()
        installation = invocation.fresh_installation()
        environment = dict(os.environ)
        environment.update(
            COPILOT_EXTENSIONS_CONTEXT=str(installation.receipt),
            AGENT_RT_ROOT=str(installation.plugin_root),
            COPILOT_PLUGIN_ROOT=str(installation.payload_root),
        )
        return spawn_daemon(
            [
                sys.executable, "-I", "-m", "agent_pull_requests", "serve",
                "--passive-instance", instance_id, "--port", str(port),
                "--poll-interval", str(poll_interval),
            ],
            cwd=invocation.runtime.runtime_root,
            log_path=installation.run_root / "watch-owner.log",
            env=environment, console_children=True,
        )

    return WatchDriver(
        journal, spawn=spawn, pick_free_port=_port, timeout=timeout,
    ).run()


def ensure(
    invocation: WatchInvocation, *, restart: bool = False,
    poll_interval: float = 30.0, timeout: float = 120.0,
) -> bool:
    """Ensure an owner is ready; report whether guarded discovery found one."""
    invocation.require_current()
    installation = invocation.fresh_installation()
    with installation.transaction(timeout=timeout, environment={}) as guard:
        invocation.require_current()
        owner = incumbent(invocation, guard, timeout=timeout)
        if owner is not None and not restart and owner.instance.runtime == invocation.runtime:
            return True
        manifest = _regular_bytes(installation.plugin_root / "deploy-manifest.json")
        if manifest is None:
            raise PermissionError("watch selected runtime has no completed deployment manifest")
        journal = WatchJournal.begin(
            installation, guard, target=invocation.runtime, target_manifest=manifest,
            predecessor=None if owner is None else owner.instance, environment={},
        )
        result = run_target(journal, invocation, poll_interval=poll_interval, timeout=timeout)
        if not result.ok or journal._document["phase"] != "complete":
            raise RuntimeError("watch startup rolled back; no new owner was committed")
        return owner is not None


def stop(invocation: WatchInvocation, *, timeout: float = 120.0) -> bool:
    """Stop only the attributable installation owner; never enter legacy shutdown."""
    from .watch_withdrawal import finish

    invocation.require_current()
    installation = invocation.fresh_installation()
    with installation.transaction(timeout=timeout, environment={}) as guard:
        owner = incumbent(invocation, guard, timeout=timeout)
        if owner is None:
            return False
        document = read_known_owner(installation, environment={})
        finish(invocation, guard, document, client=owner, timeout=timeout)
        return True


def serve_passive(invocation: WatchInvocation, args, *, fetch) -> int:
    if not args.passive_instance or not 0 < args.port <= 65535:
        raise PermissionError("watch passive entry requires its reserved instance and port")
    owner = WatchControlOwner(
        invocation, fetch=fetch, poll_interval=args.poll_interval,
        instance_id=args.passive_instance,
    )
    server = WatchControlServer(owner.compute, on_idle=None, bind_port=args.port)
    try:
        server.start()
        owner.publish_launch(server)
        owner.wait_for_shutdown()
    finally:
        server.close_admission(reason="shutdown")
        server.close()
        deadline = time.monotonic() + 5.0
        while server.active_handler_count():
            if time.monotonic() >= deadline:
                raise TimeoutError("watch passive handlers did not drain")
            time.sleep(0.02)
        owner.writer.close(timeout=5.0)
    return 0
