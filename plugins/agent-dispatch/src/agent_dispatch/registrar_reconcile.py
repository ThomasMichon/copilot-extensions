"""Bridge: registrar **declarations** -> supervisor **registrations** (Phase 3).

The singleton :class:`~agent_dispatch.supervisor_daemon.SupervisorDaemon` reconciles a
set of *registrations* (``{id, kind, spec, machine, env}``) into subprocesses. The
registrar's source of truth is a set of *declarations*
(:class:`~agent_dispatch.registrar.ProfileDeclaration`), discovered from pointers.

This module maps one to the other so the daemon can run the **declared** profile set
directly -- no second store, no per-profile OS unit. A declaration becomes a
``supervised-lane`` registration (or ``evaluator`` when it names one), with a **stable
id** (``declared:<owner>:<name>``) so re-discovering an unchanged declaration is a
no-op reconcile, a changed one restarts in place, and a vanished one winds down.

The mapping is **pure** and mirrors :meth:`ProfileDeclaration.to_supervise_args`; the
lane ``spec`` keys line up with
:func:`agent_dispatch.supervisor_daemon._lane_flags`.
"""

from __future__ import annotations

from collections.abc import Iterable

from .registrar import ProfileDeclaration
from .registrations import RegistrationKind

#: Prefix that namespaces a declared registration's id away from store-backed ones,
#: so the two desired-set sources never collide.
DECLARED_ID_PREFIX = "declared"


def declared_registration_id(decl: ProfileDeclaration) -> str:
    """The stable registration id a declaration maps to (``declared:<owner>:<name>``)."""
    owner = decl.owner or "local"
    return f"{DECLARED_ID_PREFIX}:{owner}:{decl.name}"


def declaration_to_spec(decl: ProfileDeclaration) -> dict:
    """Map a declaration to the daemon lane ``spec`` (mirrors ``to_supervise_args``)."""
    spec: dict = {
        "labels": list(decl.labels),
        "max_concurrent": decl.concurrency,
        "max_attempts": decl.max_attempts,
        "interval": decl.interval,
        "heartbeat": decl.heartbeat,
        "reactive": decl.reactive,
        "reactive_interval": decl.reactive_interval,
    }
    if decl.repos == "all":
        spec["all_repos"] = True
    else:
        spec["repo"] = decl.repos
    if decl.label_max_attempts:
        spec["label_max_attempts"] = dict(decl.label_max_attempts)
    # Headless is the default embody backend. An embody (CLI-default) profile pins
    # the backend and lists any headless subset; a headless profile lists any CLI
    # opt-out subset (cli_labels) and leaves the backend at its default.
    if decl.body.type == "embody":
        spec["embody_backend"] = "cli"
        if decl.body.headless_labels:
            spec["headless_labels"] = list(decl.body.headless_labels)
    elif decl.body.cli_labels:
        spec["cli_labels"] = list(decl.body.cli_labels)
    if decl.body.type == "headless" or decl.body.headless_labels:
        spec["headless_agent"] = decl.body.agent
    if decl.verify_timeout:
        spec["verify_timeout"] = decl.verify_timeout
    if decl.evaluator:
        spec["evaluator"] = decl.evaluator
    return spec


def declaration_to_registration(
    decl: ProfileDeclaration, *, machine: str | None, env: str = "default"
) -> dict:
    """Map a declaration to a daemon registration dict scoped to ``(machine, env)``."""
    kind = RegistrationKind.EVALUATOR if decl.evaluator else RegistrationKind.SUPERVISED_LANE
    return {
        "id": declared_registration_id(decl),
        "kind": kind,
        "spec": declaration_to_spec(decl),
        "machine": machine,
        "env": env,
        "status": "active",
        "source": DECLARED_ID_PREFIX,
        "owner": decl.owner,
    }


def runs_on_machine(decl: ProfileDeclaration, machine: str | None) -> bool:
    """Does this declaration's pool filter permit running on ``machine``?

    Reuses the Phase-2 pool filter: a declaration with a ``permit.machine`` that
    excludes this host is not desired here; one with no machine restriction (or a
    daemon with no machine identity) runs anywhere. Only the ``machine`` dimension is
    consulted -- every other dimension is left unconstrained (a wildcard).
    """
    if machine is None:
        return True
    return decl.permits({"machine": machine})


def declared_registrations(
    decls: Iterable[ProfileDeclaration], *, machine: str | None, env: str = "default"
) -> list[dict]:
    """Convert the declarations that run on ``machine`` into daemon registrations."""
    return [
        declaration_to_registration(decl, machine=machine, env=env)
        for decl in decls
        if runs_on_machine(decl, machine)
    ]
