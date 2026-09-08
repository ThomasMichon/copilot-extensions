"""Durable, uniform service lifecycle event log (shared across zdd consumers).

One structured, append-only record per lifecycle transition -- install, update,
start, stop, drain, and each cutover step (begin -> new-bound -> verify -> flip
-> retire -> rollback) -- written to a **durable** log, so a failed transition
(the canonical one: "the new daemon never bound") is diagnosable *after the
fact* from a single file instead of being reconstructed from scattered stdout
and a crash traceback.

Why this exists alongside the other two lifecycle surfaces:

* :mod:`zdd.breadcrumb` is a single-file **snapshot** of the *in-flight* cutover
  (``cutover.json``), overwritten at each step and cleared on success -- it
  exists to *recover* a stranded survivor, not to *record history*. After a
  clean cutover it is gone.
* Installer shell flows echo ``[OK]``/``[FAIL]`` to **stdout** -- ephemeral;
  once the deploy process exits, the trace is lost.
* The runtime telemetry seam (:mod:`agent_bridge.telemetry`) surfaces *session*
  lifecycle/health, not *service* install/update/cutover transitions, and is a
  no-op unless a consumer wires a sink.

This module is the durable, always-on middle: an **append-only JSON-Lines**
history of every service-lifecycle transition, co-located with the routing
table and the breadcrumb (``<config_dir>/lifecycle.log``) so a diagnostician
looks in exactly one place. It is dependency-free (stdlib only) and **fail-open**
in every path -- logging a lifecycle event must never perturb the lifecycle it
observes.

Record schema (one JSON object per line)::

    {
      "ts": "2026-08-21T20:00:00.000000+00:00",  # ISO-8601 UTC, always present
      "service": "agent-bridge",                 # which service
      "node": "some-host",                       # hostname (source machine)
      "action": "cutover-new-bound",             # one of ACTIONS
      "outcome": "fail",                          # begin | ok | fail
      "version": "0.4.0-dev316",                 # service version, when known
      "port": 50000,                              # relevant port, when known
      "pid": 12345,                               # emitting process pid
      "detail": {"old_port": 49000}               # optional, structured, no secrets
    }
"""

from __future__ import annotations

import json
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_FILENAME = "lifecycle.log"

#: Roll the log over to ``lifecycle.log.1`` once it exceeds this many bytes, so
#: an always-on history cannot grow without bound. One generation is retained.
_MAX_BYTES = 1_000_000

# -- Vocabulary --------------------------------------------------------------

# Service lifecycle actions. The cutover steps mirror the reversible sequence in
# zdd.cutover; install/update/start/stop/drain cover the surrounding lifecycle.
INSTALL = "install"
UPDATE = "update"
START = "start"
STOP = "stop"
DRAIN = "drain"
CUTOVER_BEGIN = "cutover-begin"
CUTOVER_NEW_BOUND = "cutover-new-bound"
CUTOVER_VERIFY = "cutover-verify"
CUTOVER_FLIP = "cutover-flip"
CUTOVER_RETIRE = "cutover-retire"
ROLLBACK = "rollback"
#: A dead-port watchdog retired an advertised-but-dead endpoint (see
#: ``zdd.routing.reap_stale_active``).
WATCHDOG_REAP = "watchdog-reap"

#: Every recognized action. An unrecognized action is still logged (fail-open),
#: but callers should prefer a constant so the vocabulary stays uniform.
ACTIONS = frozenset(
    {
        INSTALL,
        UPDATE,
        START,
        STOP,
        DRAIN,
        CUTOVER_BEGIN,
        CUTOVER_NEW_BOUND,
        CUTOVER_VERIFY,
        CUTOVER_FLIP,
        CUTOVER_RETIRE,
        ROLLBACK,
        WATCHDOG_REAP,
    }
)

# Outcomes. ``begin`` marks the start of a (possibly long) action; ``ok``/``fail``
# mark its result.
BEGIN = "begin"
OK = "ok"
FAIL = "fail"
OUTCOMES = frozenset({BEGIN, OK, FAIL})


def log_path(config_dir: str | os.PathLike[str]) -> Path:
    """Absolute path of the lifecycle log inside ``config_dir``."""
    return Path(config_dir) / _LOG_FILENAME


def service_from_config_dir(config_dir: str | os.PathLike[str]) -> str:
    """Infer a service name from a config-dir path.

    The conventional layout is ``~/.<service>`` (e.g. ``~/.agent-bridge``), so the
    directory basename with a leading dot stripped is the service name. Falls
    back to the raw basename.
    """
    name = Path(config_dir).name
    return name[1:] if name.startswith(".") else name


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rotate_if_needed(path: Path) -> None:
    """Roll the log to ``.1`` once it exceeds :data:`_MAX_BYTES` (best-effort)."""
    try:
        if path.stat().st_size < _MAX_BYTES:
            return
    except OSError:
        return
    try:
        os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        # A rotation failure must never block emission; keep appending.
        pass


def record(
    config_dir: str | os.PathLike[str],
    action: str,
    *,
    service: str | None = None,
    outcome: str = OK,
    version: str | None = None,
    port: int | None = None,
    node: str | None = None,
    pid: int | None = None,
    detail: dict[str, Any] | None = None,
    ts: str | None = None,
) -> dict[str, Any] | None:
    """Append one lifecycle record to ``<config_dir>/lifecycle.log`` (fail-open).

    Returns the record written, or ``None`` if anything went wrong (a missing
    directory that cannot be created, an unwritable path, a serialization error).
    Emission is best-effort by contract: **it never raises**, so a lifecycle
    event can be recorded from inside the very transition it describes without
    risk of perturbing it.

    ``service`` defaults to ``None`` and is then inferred from ``config_dir``.
    Only **state and structure** belong in ``detail`` -- never conversation
    content, tokens, or other secrets. An explicitly-passed empty ``detail`` dict
    is preserved; ``None`` means "not provided".
    """
    try:
        svc = service or service_from_config_dir(config_dir)
        rec: dict[str, Any] = {
            "ts": ts or _now_iso(),
            "service": svc,
            "node": node or socket.gethostname(),
            "action": action,
            "outcome": outcome,
        }
        if version is not None:
            rec["version"] = version
        if port is not None:
            rec["port"] = port
        rec["pid"] = pid if pid is not None else os.getpid()
        if detail is not None:
            rec["detail"] = detail

        path = log_path(config_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path)
        line = json.dumps(rec, separators=(",", ":"), default=str)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return rec
    except Exception:  # noqa: BLE001 -- lifecycle logging is best-effort, never fatal
        return None


def _parse_events(raw: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def read_events(
    config_dir: str | os.PathLike[str], *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Read parsed lifecycle records (oldest first). Malformed lines are skipped.

    Both the current log and the one retained rotation generation
    (``lifecycle.log.1``, older) are read and concatenated so recent history is
    not lost -- and ``limit`` is not silently short-changed -- immediately after
    a rotation. ``limit`` returns only the most recent ``limit`` records. A
    missing log is an empty list.
    """
    path = log_path(config_dir)
    rotated = path.with_name(path.name + ".1")
    events: list[dict[str, Any]] = []
    for p in (rotated, path):  # older generation first, then current
        try:
            raw = p.read_text(encoding="utf-8")
        except OSError:
            continue
        events.extend(_parse_events(raw))
    if limit is not None and limit >= 0:
        # ``events[-0:]`` is the whole list, so guard limit==0 explicitly.
        return events[-limit:] if limit > 0 else []
    return events


# -- CLI ---------------------------------------------------------------------
# A tiny entry point so shell installers (which run before, or without, the
# service venv) can append a uniform record with the system interpreter:
#
#   PYTHONPATH=<payload>/libs/zdd/src python3 -m zdd.lifecycle record \
#       --config-dir ~/.agent-bridge --service agent-bridge \
#       --action update --outcome ok --version 0.4.0-dev316
#
# and an operator can read the recent history back:
#
#   python3 -m zdd.lifecycle show --config-dir ~/.agent-bridge -n 20


def _build_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m zdd.lifecycle",
        description="Durable, uniform service lifecycle event log.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="append one lifecycle record")
    rec.add_argument("--config-dir", required=True)
    rec.add_argument("--service", default=None)
    rec.add_argument("--action", required=True)
    rec.add_argument("--outcome", default=OK)
    rec.add_argument("--version", default=None)
    rec.add_argument("--port", type=int, default=None)
    rec.add_argument("--node", default=None)
    rec.add_argument("--pid", type=int, default=None)
    rec.add_argument(
        "--detail", default=None, help="optional JSON object of structured detail"
    )

    show = sub.add_parser("show", help="print recent lifecycle records")
    show.add_argument("--config-dir", required=True)
    show.add_argument("-n", "--limit", type=int, default=20)
    show.add_argument("--json", action="store_true", help="emit raw JSON lines")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "record":
        detail = None
        if args.detail:
            try:
                parsed = json.loads(args.detail)
                if isinstance(parsed, dict):
                    detail = parsed
            except ValueError:
                pass
        record(
            args.config_dir,
            args.action,
            service=args.service,
            outcome=args.outcome,
            version=args.version,
            port=args.port,
            node=args.node,
            pid=args.pid,
            detail=detail,
        )
        # Fail-open: a dropped record is not a hard error for the caller (the
        # installer must proceed regardless), so always exit 0.
        return 0

    if args.cmd == "show":
        events = read_events(args.config_dir, limit=args.limit)
        for ev in events:
            if args.json:
                print(json.dumps(ev, separators=(",", ":"), default=str))
            else:
                ts = ev.get("ts", "?")
                svc = ev.get("service", "?")
                act = ev.get("action", "?")
                out = ev.get("outcome", "?")
                port = ev.get("port")
                ver = ev.get("version")
                extra = " ".join(
                    part
                    for part in (
                        f"port={port}" if port is not None else "",
                        f"version={ver}" if ver else "",
                    )
                    if part
                )
                print(f"{ts}  {svc:<16} {act:<18} {out:<6} {extra}".rstrip())
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
