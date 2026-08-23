#!/usr/bin/env python3
"""Portable, stdlib-only relay-collision probe for agent-bridge (concurrent-flip family).

Drives the REAL credential-relay server (`credential_relay.server`) under port
contention and asserts the binding guarantee behind the duplicate-daemon relay
bug: *when a second party contends for the relay's port, the relay must still come
up (reclaim the port or fall back to an OS-assigned ephemeral one) and publish the
port it actually bound -- it is never left silently dead.* A relay left unbound
breaks all credential forwarding over the SSH tunnel, so "came up on some port" is
the invariant that keeps auth working when concurrent plugin updates spawn a
racing daemon.

It is the reusable core of the clean-room ``agent-bridge-concurrent-relay``
scenario: the thin scenario.sh installs + provisions the plugin on a fresh box and
runs this probe under the built agent-bridge venv, so the contention orchestration
is verifiable independently of Docker (it runs on any OS with a built agent-bridge
venv that vendors ``credential_relay``).

FIDELITY NOTE (honest scope). This Tier-P probe proves the relay-bind RESILIENCE
mechanism the "auth survives a concurrent update" guarantee is built on: the
dynamic default bind, and the ephemeral fallback when a live occupant holds a
pinned port (the relay comes up regardless, on a discoverable port). A *fully live*
"two real daemons race the sessionStart-hook installer and CodeSpace auth keeps
working end-to-end" assertion needs two real installed daemons + an SSH `-R`
credential relay + a remote git operation -- a Tier-E, model/box-in-the-loop
concern driven by the wider chaos rig, not stdlib-simulatable here. A follow-up
check ``pinned-port-reclaim`` (evict a *stale, killable* holder and rebind the SAME
port) needs a spawned subprocess occupant and is intentionally out of this
deterministic in-process slice.

Checks (each prints ``PROBE: <name> PASS|FAIL <detail>``):
  dynamic-default-bind            port 0 -> the OS assigns an ephemeral port; the
                                  relay is running and publishes that real port.
  live-occupant-ephemeral-fallback  a live, unevictable occupant holds a pinned
                                  port; the relay falls back to an ephemeral port,
                                  comes up, and publishes a DIFFERENT real port --
                                  never left silently unbound.

Usage:
    python relay_collision_probe.py [--checks a,b]

Exit 0 iff every selected check PASSes. Requires ``credential_relay`` importable
(run under the built agent-bridge venv python).
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import sys

ALL_CHECKS = ["dynamic-default-bind", "live-occupant-ephemeral-fallback"]


def _emit(name: str, ok: bool, detail: str) -> bool:
    print(f"PROBE: {name} {'PASS' if ok else 'FAIL'} {detail}")
    return ok


def _import_server():
    """Import the real CredentialRelayServer, tolerating either export path."""
    try:
        from credential_relay.server import CredentialRelayServer  # type: ignore

        return CredentialRelayServer
    except Exception:
        from credential_relay import CredentialRelayServer  # type: ignore

        return CredentialRelayServer


async def _check_dynamic_default_bind(Server) -> bool:
    """port 0 -> OS-assigned ephemeral; running and publishes the real port."""
    server = Server(port=0, sources=[])
    try:
        await server.start()
        ok = bool(getattr(server, "running", False)) and int(server.port) > 0
        return _emit(
            "dynamic-default-bind",
            ok,
            f"(bound port={server.port} running={getattr(server, 'running', None)})",
        )
    finally:
        await server.stop()


async def _check_live_occupant_ephemeral_fallback(Server) -> bool:
    """A live, unevictable occupant holds a pinned port -> relay binds ephemeral.

    We hold the pinned port with a live in-process asyncio server the relay cannot
    evict (it is not a stale agent-bridge daemon), so the relay must take the
    ephemeral-fallback path and still come up on a DIFFERENT, discoverable port.
    """
    # Grab a currently-free port number to "pin", then keep it occupied.
    probe_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
    probe_sock.bind(("127.0.0.1", 0))
    pinned = probe_sock.getsockname()[1]
    probe_sock.close()  # release; a live asyncio occupant takes it next

    occupant = await asyncio.start_server(
        lambda r, w: None, host="127.0.0.1", port=pinned
    )
    server = Server(port=pinned, sources=[])
    try:
        await server.start()
        running = bool(getattr(server, "running", False))
        moved = int(server.port) != int(pinned)
        ok = running and moved and int(server.port) > 0
        return _emit(
            "live-occupant-ephemeral-fallback",
            ok,
            f"(pinned={pinned} bound={server.port} running={running} "
            f"moved_off_pin={moved})",
        )
    finally:
        await server.stop()
        occupant.close()
        await occupant.wait_closed()


async def _run(checks: list[str]) -> int:
    Server = _import_server()
    dispatch = {
        "dynamic-default-bind": _check_dynamic_default_bind,
        "live-occupant-ephemeral-fallback": _check_live_occupant_ephemeral_fallback,
    }
    all_ok = True
    for name in checks:
        fn = dispatch.get(name)
        if fn is None:
            all_ok = _emit(name, False, "(unknown check)") and all_ok
            continue
        try:
            all_ok = (await fn(Server)) and all_ok
        except Exception as exc:  # a probe crash is a FAIL, not a traceback
            all_ok = _emit(name, False, f"(probe error: {exc!r})") and all_ok
    return 0 if all_ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--checks",
        default=",".join(ALL_CHECKS),
        help="comma-separated subset of: " + ", ".join(ALL_CHECKS),
    )
    args = ap.parse_args()
    checks = [c.strip() for c in args.checks.split(",") if c.strip()]
    return asyncio.run(_run(checks))


if __name__ == "__main__":
    sys.exit(main())
