#!/usr/bin/env python3
"""agent-vault-cutover probe -- stdlib-only, run by the built vault slot python.

Proves agent-vault's CLIENT-SIDE cutover resilience: the rendezvous **cutover
fallback ladder** (``override`` -> live ``rendezvous file`` -> ``legacy`` fixed
constant) that lets a client keep resolving the vault service across an endpoint
move without a hard failure. This is the discovery half of graceful cutover --
the piece agent-vault has today (``agent_vault.rendezvous.resolve``).

Emits ``PROBE: <name> PASS|FAIL <detail>`` lines and a ``PROBE-SUMMARY:`` line,
mirroring the agent-bridge-cutover probe so scenario.sh maps them uniformly.

NOTE: the DAEMON-SIDE active/passive zdd cutover (the connection-owner drain +
routing flip) is a SEPARATE mechanism agent-vault has NOT yet adopted; the
scenario reports that as an INFO gap, not a probe failure.
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path


def emit(name: str, ok: bool, detail: str = "") -> None:
    print(f"PROBE: {name} {'PASS' if ok else 'FAIL'} {detail}".rstrip(), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--checks",
        default="ladder-override,ladder-file,ladder-legacy,ladder-precedence,ladder-empty-raises",
    )
    args = ap.parse_args()
    checks = [c.strip() for c in args.checks.split(",") if c.strip()]

    try:
        from agent_vault import rendezvous as rz
    except Exception as e:  # noqa: BLE001
        emit("import", False, f"cannot import agent_vault.rendezvous: {e!r}")
        print("PROBE-SUMMARY: 0 passed, 1 failed", flush=True)
        return 1

    passed = failed = 0

    def record(ok: bool) -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
        else:
            failed += 1

    def _live(_ep: object) -> bool:  # force the file rung "live" regardless of pid/socket
        return True

    with tempfile.TemporaryDirectory() as td:
        rt = Path(td)

        if "ladder-override" in checks:
            try:
                ep = rz.resolve(rt, override="tcp:127.0.0.1:59991", legacy="tcp:127.0.0.1:8888")
                ok = getattr(ep, "source", None) == "env"
                emit("ladder-override", ok, f"source={getattr(ep, 'source', None)} addr={getattr(ep, 'address', ep)}")
            except Exception as e:  # noqa: BLE001
                ok = False
                emit("ladder-override", ok, f"raised {e!r}")
            record(ok)

        if "ladder-file" in checks:
            try:
                rz.write_endpoint(rt, transport="tcp", address="127.0.0.1:59992")
                ep = rz.resolve(rt, legacy="tcp:127.0.0.1:8888", probe=_live)
                ok = getattr(ep, "source", None) == "file"
                emit("ladder-file", ok, f"source={getattr(ep, 'source', None)} addr={getattr(ep, 'address', ep)}")
                rz.clear_endpoint(rt)
            except Exception as e:  # noqa: BLE001
                ok = False
                emit("ladder-file", ok, f"raised {e!r}")
            record(ok)

        if "ladder-legacy" in checks:
            try:
                ep = rz.resolve(rt, legacy="tcp:127.0.0.1:8888")
                ok = getattr(ep, "source", None) == "legacy"
                emit("ladder-legacy", ok, f"source={getattr(ep, 'source', None)} addr={getattr(ep, 'address', ep)}")
            except Exception as e:  # noqa: BLE001
                ok = False
                emit("ladder-legacy", ok, f"raised {e!r}")
            record(ok)

        if "ladder-precedence" in checks:
            # override must beat BOTH a live file and a legacy default.
            try:
                rz.write_endpoint(rt, transport="tcp", address="127.0.0.1:59993")
                ep = rz.resolve(rt, override="tcp:127.0.0.1:59991", legacy="tcp:127.0.0.1:8888", probe=_live)
                ok = getattr(ep, "source", None) == "env"
                emit("ladder-precedence", ok, f"override wins over file+legacy (source={getattr(ep, 'source', None)})")
                rz.clear_endpoint(rt)
            except Exception as e:  # noqa: BLE001
                ok = False
                emit("ladder-precedence", ok, f"raised {e!r}")
            record(ok)

        if "ladder-empty-raises" in checks:
            # No override, no file, no legacy -> a CLEAN, explicit failure (not a
            # random crash): the documented "nothing to resolve" contract.
            try:
                ep = rz.resolve(rt)
                ok = False
                emit("ladder-empty-raises", ok, f"expected a raise, got {getattr(ep, 'source', ep)!r}")
            except Exception as e:  # noqa: BLE001
                ok = True
                emit("ladder-empty-raises", ok, f"raised as designed: {type(e).__name__}")
            record(ok)

    print(f"PROBE-SUMMARY: {passed} passed, {failed} failed", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
