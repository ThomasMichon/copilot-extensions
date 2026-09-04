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
    """Map a declaration to its daemon ``spec``."""
    if decl.kind != RegistrationKind.SUPERVISED_LANE:
        return dict(decl.spec)

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
    # Fleet dispatch (pool/origin/headless) is mutually exclusive with the
    # non-fleet body-backend branches -- mirror ``to_supervise_args`` exactly so the
    # daemon-built argv matches the lossless-superset render. Without carrying the
    # fleet block here the serve daemon silently drops pool/origin and every fleet
    # supervisor runs LOCAL: it spawns a body on the pool host that queries that
    # host's *own* coordinator (not the origin), so the origin task 404s and the
    # body stands down without doing the work.
    if decl.fleet.enabled:
        spec["fleet"] = {
            "pool": list(decl.fleet.pool),
            "origin": decl.fleet.origin,
            "headless": decl.fleet.headless,
        }
        # A fleet embody body pins CLI explicitly; a headless fleet emits --headless
        # (from spec["fleet"]) in _lane_flags.
        if decl.body.type == "embody" and not decl.fleet.headless:
            spec["embody_backend"] = "cli"
    # Headless is the default embody backend. An embody (CLI-default) profile pins
    # the backend and lists any headless subset; a headless profile lists any CLI
    # opt-out subset (cli_labels) and leaves the backend at its default.
    elif decl.body.type == "embody":
        spec["embody_backend"] = "cli"
        if decl.body.headless_labels:
            spec["headless_labels"] = list(decl.body.headless_labels)
    elif decl.body.cli_labels:
        spec["cli_labels"] = list(decl.body.cli_labels)
    if decl.body.disposable_cli_labels:
        spec["disposable_cli_labels"] = list(decl.body.disposable_cli_labels)
    if decl.body.type == "headless" or decl.body.headless_labels or decl.fleet.headless:
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
    kind = (
        decl.kind
        if decl.kind != RegistrationKind.SUPERVISED_LANE
        else RegistrationKind.EVALUATOR if decl.evaluator
        else RegistrationKind.SUPERVISED_LANE
    )
    registration = {
        "id": declared_registration_id(decl),
        "logical_id": decl.name,
        "kind": kind,
        "spec": declaration_to_spec(decl),
        "machine": machine,
        "env": env,
        "status": "active",
        "source": DECLARED_ID_PREFIX,
        "owner": decl.owner,
    }
    if decl.kind == RegistrationKind.PLUGIN_COMPANION:
        registration["plugin"] = {
            "root": decl.plugin_root,
            "source_path": decl.source_path,
            "version": decl.plugin_version,
            "activation_scopes": list(decl.activation_scopes),
        }
        registration["runtime_revision"] = {
            "plugin_root": decl.plugin_root,
            "plugin_version": decl.plugin_version,
        }
    return registration


def _constrains_machine(decl: ProfileDeclaration) -> bool:
    """True if this declaration restricts *which machine* may run it.

    A declaration constrains the machine dimension when its filters name it on
    either side -- a ``permit.machine`` (only these hosts) or a ``reject.machine``
    (never these hosts). A declaration that names neither is **machine-agnostic**
    and runs on any host.
    """
    return "machine" in decl.filters.permit or "machine" in decl.filters.reject


def runs_on_machine(decl: ProfileDeclaration, machine: str | None) -> bool:
    """Does this declaration's pool filter permit running on ``machine``?

    Reuses the Phase-2 pool filter: a declaration with a ``permit.machine`` that
    excludes this host is not desired here; one with no machine restriction runs
    anywhere.

    **Fail closed when the host cannot identify itself.** If ``machine`` is
    ``None`` (the daemon could not resolve its own machine -- e.g. a bare
    service/scheduled-task context where CWD-based identity resolution fails), a
    declaration that **restricts to specific machines** is *excluded*: an
    unidentified host must not run a machine-pinned pool it cannot confirm it is a
    permitted member of. (The prior behavior ran *everything* on an unidentified
    host, so a host with a registrar pointer would run cross-machine declarations
    it should skip -- see aperture-labs #5001.) A **machine-agnostic** declaration
    (no ``machine`` permit/reject) still runs anywhere, including on an
    unidentified host.
    """
    if machine is None:
        return not _constrains_machine(decl)
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
