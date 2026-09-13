"""``schedule``/``emitter``/``webhook``/``reservations`` CLI command family,
extracted from ``__main__.py``.

These are the producer-facing CLI surfaces: ``schedule`` drives the recurring
task scheduler (serve/tick/register/lease management), ``emitter`` drives the
side-load/serve/tick change-detection loop, ``webhook`` serves the inbound
webhook listener, and ``reservations`` gives operators visibility and manual
control over spawn reservations (list/fail/defer/settle/rearm). See
visions/plugins/agent-dispatch for the producer model these commands expose.

Split out to keep ``__main__.py`` under its module-size ceiling (see
``tools/check-module-size.py``) rather than growing an already very large
file further -- this is purely a move, no behavior change.

This module still needs a handful of names that genuinely belong to
``__main__.py`` (``_client``, ``_emit``, ``_enrich``, ``_resolve_client_target``)
-- CLI-wide helpers other commands there use too, monkeypatched by tests via
their ``agent_dispatch.__main__`` attribute path.
``loop_commands._resolve_cli_module()`` already solves resolving the actually-
running ``__main__`` module (``python -m agent_dispatch`` loads it as
``sys.modules["__main__"]``, never as ``sys.modules["agent_dispatch.__main__"]``);
reuse it here via the same ``_proxy()`` pattern instead of duplicating the
resolution logic. ``_registration_scope`` and ``DispatchError`` are imported
directly since they already live outside ``__main__.py`` (in ``supervise_cli.py``
and ``client.py`` respectively).
"""

from __future__ import annotations

import argparse
import sys

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
