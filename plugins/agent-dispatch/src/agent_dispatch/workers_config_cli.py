"""Thin CLI glue for a Worktree Manager Picker "Workers" ``config_sections``
entry (``agent-dispatch-workers-config-section``, Phase 1).

Bridges two already-shipped primitives -- ``registrar discover``'s declared
``supervised-lane`` pools and ``supervise override``'s local enable/disable
kill-switch -- into the one request shape a ``config_sections[].run``
invocation needs: given a pool name, print a short, human-legible status
line (and, when invoked with a toggle flag, apply the local override
first). No new mutation primitive is introduced here -- this is glue, not a
new engine; see ``efforts/active/agent-dispatch-workers-config-section``.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, Any

from .loop_commands import _resolve_cli_module
from .registrations import RegistrationKind

if TYPE_CHECKING:
    from .registrar import ProfileDeclaration

#: A ``ConfigSection``'s status-line budget (``agent-worktrees``'s own
#: ≤200-char contract for a ``config_sections[].run`` command's stdout).
STATUS_LINE_MAX_CHARS = 200


def _core():
    return _resolve_cli_module()


def find_supervised_lane(
    name: str, *, owner: str | None = None
) -> "ProfileDeclaration | None":
    """Return the matching ``supervised-lane`` declaration, or ``None``.

    ``registrar discover`` already rejects duplicate profile names across
    sources (``agent-dispatch-recipe-library``'s own discovery contract), so
    ``name`` alone identifies one pool; ``owner`` narrows further only when
    a caller already knows it -- never required for the common case.
    """
    from . import registrar_discovery as rd

    for decl in rd.discover():
        if decl.kind != RegistrationKind.SUPERVISED_LANE:
            continue
        if decl.name != name:
            continue
        if owner is not None and decl.owner != owner:
            continue
        return decl
    return None


def pool_status_line(
    name: str,
    *,
    declaration: "ProfileDeclaration | None" = None,
    overridden: bool = False,
    override_reason: str | None = None,
) -> str:
    """Render a ≤200-char status line for one pool.

    ``declaration=None`` means "no matching declaration found" -- reported
    plainly (never crashed on, never mistaken for a zero-lane active pool).
    """
    if declaration is None:
        line = f"{name}: no declaration found"
    else:
        lanes = declaration.concurrency
        unit = "lane" if lanes == 1 else "lanes"
        state = "overridden off" if overridden else "active"
        if overridden and override_reason:
            state = f"{state} ({override_reason})"
        line = f"{name}: {lanes} {unit} declared -- {state}"
    if len(line) > STATUS_LINE_MAX_CHARS:
        line = line[: STATUS_LINE_MAX_CHARS - 1] + "\u2026"
    return line


def _cmd_workers_config_section(args: argparse.Namespace) -> int:
    """``workers config-section <name>`` -- status, optionally toggling first.

    Resolves ``name`` against ``registrar discover``'s declared
    ``supervised-lane`` pools, optionally applies ``--toggle
    enable|disable`` via the existing ``supervise override`` primitive
    (addressed by the pool's **logical** override id,
    ``logical:<owner>:<name>`` -- no concrete registration id needed, and no
    live registration either: the override store is independent of whether
    the daemon has reconciled this declaration yet), then prints the
    resulting status: a single ≤200-char line by default (the
    ``config_sections[].run`` contract), or full JSON detail with
    ``--json``. Exit code is 0 when the pool is found, 1 when no matching
    declaration exists (status/JSON is still emitted either way -- a Picker
    invocation should show the "not found" state, not merely fail silently).
    """
    from .config import overrides_path
    from .overrides import (
        clear_override,
        load_overrides,
        logical_override_id,
        overridden_off_ids,
        set_override,
    )

    declaration = find_supervised_lane(args.name, owner=getattr(args, "owner", None))
    owner = declaration.owner if declaration is not None else (args.owner or "local")
    token = logical_override_id(owner or "local", args.name)
    path = overrides_path()

    toggle = getattr(args, "toggle", None)
    if toggle == "disable":
        set_override(path, token, disabled=True, reason=getattr(args, "reason", None))
    elif toggle == "enable":
        clear_override(path, token)

    overrides = load_overrides(path)
    overridden = token in overridden_off_ids(overrides)
    reason = (overrides.get(token) or {}).get("reason") if overridden else None

    if getattr(args, "json", False):
        payload: dict[str, Any] = {
            "name": args.name,
            "owner": owner,
            "found": declaration is not None,
            "concurrency": declaration.concurrency if declaration is not None else None,
            "overridden_off": overridden,
            "override_reason": reason,
            "override_id": token,
        }
        # `_emit` always returns 0 (it signals success-of-output, not the
        # pool's own found/not-found state) -- compute this command's own
        # exit code independently so `--json` and the default status-line
        # path agree on when a Picker invocation should see a failure.
        _core()._emit(payload)
        return 0 if declaration is not None else 1
    print(
        pool_status_line(
            args.name,
            declaration=declaration,
            overridden=overridden,
            override_reason=reason,
        )
    )
    return 0 if declaration is not None else 1


def register_workers_commands(sub) -> None:
    p = sub.add_parser(
        "workers",
        help=(
            "Worktree Manager Picker glue for declared supervised-lane "
            "worker pools (status + local enable/disable)"
        ),
    )
    workers_sub = p.add_subparsers(dest="workers_command", required=True)
    cs = workers_sub.add_parser(
        "config-section",
        help=(
            "print a pool's <=200-char status line, optionally toggling "
            "its local override first -- the config_sections[].run entry point"
        ),
    )
    cs.add_argument("name", help="the declared supervised-lane pool's name")
    cs.add_argument(
        "--owner",
        help=(
            "narrow to a specific declaration owner when names collide "
            "across an unusual multi-source setup (rare -- discover() "
            "already rejects true duplicate names)"
        ),
    )
    cs.add_argument(
        "--toggle",
        choices=["enable", "disable"],
        help="apply a local override before reporting status",
    )
    cs.add_argument("--reason", help="optional reason recorded with --toggle disable")
    cs.add_argument(
        "--json", action="store_true", help="emit full JSON instead of the compact status line"
    )
    cs.set_defaults(func=_core()._cmd_workers_config_section)
