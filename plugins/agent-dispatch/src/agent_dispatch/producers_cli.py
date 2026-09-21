"""``schedule`` / ``emitter`` / ``webhook`` / ``reservations`` CLI commands
extracted from ``__main__.py``.

These are the producer-facing command families: the schedule registry/timer
producer, the command-emitter loop, the reactive webhook ingress, and
operator visibility/control over spawn reservations. Split out to keep
``__main__.py`` under its module-size ceiling (see
``tools/check-module-size.py``) rather than growing an already very large
file further -- this is purely a move, no behavior change.

This module still needs a handful of names that genuinely belong to
``__main__.py`` (``_client``, ``_emit``, ``_enrich``,
``_resolve_client_target``). ``python -m agent_dispatch`` loads
``__main__.py`` as ``sys.modules["__main__"]``, never as
``sys.modules["agent_dispatch.__main__"]`` -- a naive ``from .__main__
import X`` would therefore import and execute an independent second copy of
that module rather than the one actually running, silently diverging any
monkeypatched state. ``loop_commands._resolve_cli_module()`` already solves
that resolution; reuse it here via the same ``_proxy()`` pattern other
extracted CLI modules use.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from .client import DispatchError
from .loop_commands import _resolve_cli_module
from .supervise_cli import _registration_scope


def _proxy(name: str):
    """Delegate to ``agent_dispatch.__main__.<name>`` via ``_resolve_cli_module``."""

    def _fn(*args, **kwargs):
        return getattr(_resolve_cli_module(), name)(*args, **kwargs)

    return _fn


_client = _proxy("_client")
_emit = _proxy("_emit")
_enrich = _proxy("_enrich")
_resolve_client_target = _proxy("_resolve_client_target")


def _cmd_schedule(args: argparse.Namespace) -> int:
    from .producers import schedule

    cmd = args.schedule_command

    if cmd == "serve":
        _url, _token = _resolve_client_target(args)
        if getattr(args, "registry", False):
            if not args.lease_scope or not args.holder:
                raise SystemExit(
                    "schedule serve --registry: --lease-scope and --holder are required"
                )
            schedule.serve_registry(
                url=_url,
                token=_token,
                interval=args.interval,
                lease_scope=args.lease_scope,
                holder=args.holder,
                holder_session=getattr(args, "holder_session", None),
                lease_ttl=getattr(args, "lease_ttl", None),
            )
        else:
            if not args.spec:
                raise SystemExit("schedule serve: pass a SPEC path or --registry")
            schedule.serve(args.spec, url=_url, token=_token, interval=args.interval)
        return 0

    if cmd == "tick":
        with _client(args) as c:
            if getattr(args, "registry", False):
                result = schedule.run_registry_tick(c)
            else:
                if not args.spec:
                    raise SystemExit("schedule tick: pass a SPEC path or --registry")
                result = schedule.run_tick(c, schedule.load_spec(args.spec))
        return _emit(
            {
                "created": [_enrich(t) for t in result["created"]],
                "errors": result["errors"],
            }
        )

    if cmd == "register":
        with _client(args) as c:
            result = schedule.register_from_spec(c, schedule.load_spec(args.spec))
        return _emit(result)

    if cmd == "list":
        with _client(args) as c:
            return _emit(c.list_schedules(include_paused=not args.active))

    if cmd == "inspect":
        import time as _time

        with _client(args) as c:
            rec = c.get_schedule(args.id)
            try:
                occ = schedule.due_occurrences(rec["entry"], now=_time.time())
            except schedule.ScheduleError:
                occ = []
            lease = c.get_schedule_lease(args.id)
        return _emit({"schedule": rec, "next_occurrences": occ, "lease": lease})

    if cmd == "remove":
        with _client(args) as c:
            return _emit(c.remove_schedule(args.id))

    if cmd in ("pause", "resume"):
        with _client(args) as c:
            return _emit(c.set_schedule_paused(args.id, cmd == "pause"))

    if cmd == "lease-list":
        with _client(args) as c:
            return _emit(c.list_schedule_leases())

    if cmd == "lease-show":
        with _client(args) as c:
            return _emit(c.get_schedule_lease(args.scope))

    if cmd == "lease-acquire":
        with _client(args) as c:
            return _emit(
                c.acquire_schedule_lease(
                    args.scope,
                    args.holder,
                    holder_session=args.holder_session,
                    ttl=args.ttl,
                )
            )

    if cmd == "lease-release":
        with _client(args) as c:
            return _emit(c.release_schedule_lease(args.scope, args.holder, force=args.force))

    raise SystemExit(f"unknown schedule command: {cmd!r}")


def _cmd_emitter(args: argparse.Namespace) -> int:
    from .producers import emitter

    if args.emitter_command == "side-load":
        try:
            with _client(args) as client:
                registration = client.get_registration(args.registration)
                machine, env = _registration_scope(args)
                return _emit(
                    emitter.run_side_load(
                        client,
                        registration,
                        args.change_ref,
                        current_machine=machine,
                        current_env=env,
                    )
                )
        except (emitter.EmitterError, DispatchError) as exc:
            print(f"agent-dispatch emitter side-load: {exc}", file=sys.stderr)
            return 2
    spec = emitter.load_spec(args.spec)
    if args.emitter_command == "serve":
        url, token = _resolve_client_target(args)
        emitter.serve(
            args.spec,
            url=url,
            token=token,
            holder=args.holder,
        )
        return 0
    if args.emitter_command == "tick":
        with _client(args) as client:
            return _emit(emitter.run_tick(client, spec, holder=args.holder))
    raise SystemExit(f"unknown emitter command: {args.emitter_command!r}")


def _cmd_webhook(args: argparse.Namespace) -> int:
    from .producers import webhook

    config = webhook.load_config(args.config) if args.config else {}
    if args.url:
        config["url"] = args.url
    if args.token:
        config["coordinator_token"] = args.token
    webhook.serve(config, host=args.host, port=args.port)
    return 0


def _parse_label_max_attempts(items: list[str] | None) -> dict[str, int]:
    """Parse repeated ``LABEL=N`` flags into a ``{label: max_attempts}`` map.

    Raises ``SystemExit`` on a malformed entry (bad shape or non-int N) so the
    supervisor fails loudly at startup rather than silently ignoring a policy.
    """
    out: dict[str, int] = {}
    for raw in items or []:
        label, sep, num = str(raw).partition("=")
        label = label.strip()
        if not sep or not label:
            raise SystemExit(f"--label-max-attempts expects LABEL=N, got {raw!r}")
        try:
            out[label] = max(0, int(num.strip()))
        except ValueError:
            raise SystemExit(f"--label-max-attempts: N must be an integer, got {num!r}")
    return out


def _cmd_reservations(args: argparse.Namespace) -> int:
    """Operator visibility + manual control over spawn reservations."""
    with _client(args) as c:
        if args.reservations_command == "list":
            rows = c.list_reservations(task_id=args.task, state=args.state, limit=args.limit)
            return _emit(rows)
        if args.reservations_command == "fail":
            return _emit(c.fail_spawn(args.key, detail=args.detail))
        if args.reservations_command == "defer":
            return _emit(c.defer_spawn(args.key, detail=args.detail))
        if args.reservations_command == "settle":
            return _emit(c.settle_spawn(args.key, detail=args.detail))
        if args.reservations_command == "rearm":
            return _emit(
                c.rearm_spawn(
                    args.task,
                    permitted=args.permit,
                    reason=args.reason,
                    min_failures=args.min_failures,
                )
            )
    return 2


def register_producer_commands(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "schedule",
        help="scheduler/timer producer: turn a JSON schedule spec into deferred "
        "tasks (idempotent per occurrence via not_before + dedup_key), and "
        "manage a persisted registry of recurring jobs + single-producer leases",
    )
    sched_sub = p.add_subparsers(dest="schedule_command", required=True)
    sp = sched_sub.add_parser(
        "tick",
        help="create every currently-due occurrence once, then exit (drive from "
        "cron / a systemd timer / manage_schedule)",
    )
    sp.add_argument(
        "spec",
        nargs="?",
        help="path to the JSON schedule spec (omit with --registry to tick the "
        "coordinator's registered schedules)",
    )
    sp.add_argument(
        "--registry",
        action="store_true",
        help="tick the coordinator's registered schedules instead of a spec file",
    )
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser(
        "serve", help="built-in timer: reload the spec and tick every --interval seconds"
    )
    sp.add_argument(
        "spec",
        nargs="?",
        help="path to the JSON schedule spec (omit with --registry)",
    )
    sp.add_argument(
        "--interval", type=float, default=60.0, help="seconds between ticks (default: 60)"
    )
    sp.add_argument(
        "--registry",
        action="store_true",
        help="lease-gated registry mode: tick the coordinator's registered "
        "schedules only while this host holds the job-lease",
    )
    sp.add_argument("--lease-scope", help="job-lease scope to hold in --registry mode (required)")
    sp.add_argument("--holder", help="this producer's identity (the machine) in --registry mode")
    sp.add_argument("--holder-session", help="optional live-session handle of the holder")
    sp.add_argument(
        "--lease-ttl",
        type=float,
        help="observability-only lease expiry seconds (never auto-steals)",
    )
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser(
        "register",
        help="register (upsert) every schedule in a spec file into the "
        "coordinator's persisted registry",
    )
    sp.add_argument("spec", help="path to the JSON schedule spec to register")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("list", help="list registered schedules")
    sp.add_argument("--active", action="store_true", help="only non-paused schedules")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser(
        "inspect", help="show one registered schedule + its next occurrences + lease"
    )
    sp.add_argument("id", help="the schedule id")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("remove", help="delete a registered schedule")
    sp.add_argument("id", help="the schedule id")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("pause", help="pause a registered schedule (keep its definition)")
    sp.add_argument("id", help="the schedule id")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("resume", help="resume a paused schedule")
    sp.add_argument("id", help="the schedule id")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("lease-list", help="list held schedule job-leases")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser("lease-show", help="show the job-lease for a scope")
    sp.add_argument("scope", help="the lease scope")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser(
        "lease-acquire",
        help="acquire/renew a job-lease (pin-not-failover: never steals a lease "
        "held by another holder)",
    )
    sp.add_argument("scope", help="the lease scope")
    sp.add_argument("--holder", required=True, help="this holder's identity (the machine)")
    sp.add_argument("--holder-session", help="optional live-session handle")
    sp.add_argument("--ttl", type=float, help="observability-only expiry seconds")
    sp.set_defaults(func=_cmd_schedule)
    sp = sched_sub.add_parser(
        "lease-release", help="release a job-lease (use --force to reassign a stuck one)"
    )
    sp.add_argument("scope", help="the lease scope")
    sp.add_argument("--holder", required=True, help="the releasing holder's identity")
    sp.add_argument("--force", action="store_true", help="reassign a lease held by another holder")
    sp.set_defaults(func=_cmd_schedule)

    p = subparsers.add_parser(
        "emitter",
        help="lease-gated periodic command emitter managed by the singleton supervisor",
    )
    emitter_sub = p.add_subparsers(dest="emitter_command", required=True)
    ep = emitter_sub.add_parser("tick", help="run one lease-gated emitter tick")
    ep.add_argument("spec", help="path to the JSON command-emitter spec")
    ep.add_argument("--holder", required=True, help="this producer's machine identity")
    ep.set_defaults(func=_cmd_emitter)
    ep = emitter_sub.add_parser("serve", help="run a command emitter on its declared interval")
    ep.add_argument("spec", help="path to the JSON command-emitter spec")
    ep.add_argument("--holder", required=True, help="this producer's machine identity")
    ep.set_defaults(func=_cmd_emitter)
    ep = emitter_sub.add_parser(
        "side-load",
        help="send one change reference through a registered emitter's on-demand path",
    )
    ep.add_argument("registration", help="emitter registration id")
    ep.add_argument("change_ref", help="target change reference for the emitter")
    ep.add_argument(
        "--env",
        help="registration environment (default: AGENT_DISPATCH_ENV or 'default')",
    )
    ep.set_defaults(func=_cmd_emitter)


def register_webhook_command(subparsers: Any) -> None:
    p = subparsers.add_parser(
        "webhook",
        help="reactive producer: serve an HTTP app mapping git-forge PR-merge "
        "and telemetry events onto tasks",
    )
    p.add_argument("--config", help="path to the JSON webhook config (optional)")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=9331, help="bind port (default: 9331)")
    p.set_defaults(func=_cmd_webhook)


def register_reservations_command(subparsers: Any) -> None:
    p = subparsers.add_parser("reservations", help="inspect / manually control spawn reservations")
    res_sub = p.add_subparsers(dest="reservations_command", required=True)
    rp = res_sub.add_parser("list", help="list spawn reservations")
    rp.add_argument("--task", help="filter by task id")
    rp.add_argument("--state", help="filter by state (comma-list ok)")
    rp.add_argument("--limit", type=int, default=200)
    rp.set_defaults(func=_cmd_reservations)
    rp = res_sub.add_parser(
        "fail", help="mark a reservation failed (releases the task for a fresh attempt)"
    )
    rp.add_argument("key")
    rp.add_argument("--detail")
    rp.set_defaults(func=_cmd_reservations)
    rp = res_sub.add_parser(
        "defer",
        help=(
            "mark a reservation deferred: a carried session was confirmed "
            "still live/busy, not a failure (releases the task for a fresh "
            "attempt without counting toward dead-lettering)"
        ),
    )
    rp.add_argument("key")
    rp.add_argument("--detail")
    rp.set_defaults(func=_cmd_reservations)
    rp = res_sub.add_parser("settle", help="mark a reservation settled (attempt over)")
    rp.add_argument("key")
    rp.add_argument("--detail")
    rp.set_defaults(func=_cmd_reservations)
    rp = res_sub.add_parser(
        "rearm",
        help="atomically retire a dead-lettered task's failed spawn attempts",
    )
    rp.add_argument("task", help="queued, unowned task id")
    rp.add_argument(
        "--permit",
        action="store_true",
        help="explicitly authorize the reservation-history mutation",
    )
    rp.add_argument("--reason", required=True, help="auditable operator reason")
    rp.add_argument(
        "--min-failures",
        type=int,
        default=3,
        help="required failed-attempt count (minimum/default: 3)",
    )
    rp.set_defaults(func=_cmd_reservations)
