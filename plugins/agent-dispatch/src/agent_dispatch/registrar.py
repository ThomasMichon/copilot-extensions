"""The task registrar -- declarative supervised-work profiles (Phase 1: schema).

A **profile declaration** is the durable, declarative description of one unit of
supervised work -- a label-gated pool, a scheduled lane, a fleet dispatch.
It is the source of truth the singleton supervisor reconciles (vision:
*declarative-discovered-registrar* / *discover-and-live-reconcile*). This module is
**pure**: it parses an already-decoded mapping (from YAML or JSON -- reading files
and watching directories is the discovery layer, not here), validates it, and
renders the equivalent ``agent-dispatch supervise`` invocation, so a declaration is
a **lossless superset** of the legacy ``AGENT_DISPATCH_SUPERVISE_*`` env profile it
replaces.

Nothing here does I/O, holds a process, or knows where declarations live. That keeps
the schema trivially testable and lets the discovery + service layers (later phases)
own the file-watching and the running.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace


class RegistrarError(ValueError):
    """A profile declaration is malformed (missing name, bad type, bad value)."""


_VALID_BODY_TYPES = frozenset({"embody", "headless"})

#: Recognized top-level keys -- an unknown key is a typo we refuse rather than
#: silently ignore (declarations are authored by hand and by other systems).
_KNOWN_KEYS = frozenset(
    {
        "name",
        "labels",
        "repos",
        "concurrency",
        "max_active_processes",
        "interval",
        "max_attempts",
        "label_max_attempts",
        "heartbeat",
        "reactive",
        "reactive_interval",
        "verify_timeout",
        "body",
        "fleet",
        "filters",
        "evaluator",
        "owner",
        "description",
        "runtime_generation",
        "transition_group",
        "kind",
        "spec",
    }
)
_KNOWN_BODY_KEYS = frozenset(
    {
        "type",
        "agent",
        "headless_labels",
        "cli_labels",
        "disposable_cli_labels",
    }
)
_KNOWN_FLEET_KEYS = frozenset({"pool", "origin", "headless"})
_KNOWN_FILTER_KEYS = frozenset({"permit", "reject"})

#: The one filter vocabulary, spoken by both sides (a task *declares* these
#: attributes; a pool *filters* over them). Scalar dimensions match by
#: membership (the task's single value must be among the permitted set);
#: ``capabilities`` is a *set* dimension matched by subset (a task's required
#: capabilities must all be provided by the pool). See the registrar effort's
#: Model section + worked examples.
_FILTER_SCALAR_DIMS = frozenset({"repo", "machine", "env", "role", "worktree", "task-type"})
_FILTER_SET_DIMS = frozenset({"capabilities"})
_FILTER_DIMS = _FILTER_SCALAR_DIMS | _FILTER_SET_DIMS


def _canon_dim(key: object) -> str:
    """Normalize a filter dimension name (accept ``task_type`` for ``task-type``)."""
    if not isinstance(key, str) or not key:
        raise RegistrarError(f"filters: dimension names must be non-empty strings, got {key!r}")
    return key.replace("_", "-")


@dataclass(frozen=True)
class Body:
    """How a claimed task is embodied for this profile.

    ``type=headless`` (default) routes the profile's labels to a headless
    agent-bridge ACP session named by ``agent`` -- the right body for a
    self-contained, autonomous dispatched task (no mux, no CLI-start-prompt).
    ``type=embody`` is a CLI-backed autopilot worktree session (mux, attachable).
    Per-label overrides refine either default: ``cli_labels`` forces specific
    labels to a CLI body when the profile is headless-by-default; ``headless_labels``
    forces specific labels headless when the profile is ``embody`` (a mixed profile).
    """

    type: str = "headless"
    agent: str = "task-worker"
    headless_labels: tuple[str, ...] = ()
    cli_labels: tuple[str, ...] = ()
    disposable_cli_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Fleet:
    """Optional fleet dispatch -- fan bodies across a pool of remote hosts."""

    pool: tuple[str, ...] = ()
    origin: str | None = None
    headless: bool = False

    @property
    def enabled(self) -> bool:
        return bool(self.pool)


@dataclass(frozen=True)
class Filters:
    """A pool's permit/reject predicate over the shared filter vocabulary.

    A **pool is a filter with a cap** (vision: *pools-as-filters*): it accepts a
    task when the task's declared attributes clear the ``permit`` gate and miss the
    ``reject`` gate. Both sides are keyed by the same vocabulary
    (:data:`_FILTER_DIMS`). Semantics:

    * ``permit`` -- for each constrained dimension the task must match. A scalar
      dimension matches when the task's value is among the permitted set **or the
      task does not declare it** (an *untargeted* attribute is a wildcard, so an
      unpinned task can bind to any pool). ``capabilities`` matches when the task's
      required capabilities are a *subset* of those the pool provides. Dimensions
      not listed in ``permit`` are unconstrained.
    * ``reject`` -- a task is rejected when it *explicitly* carries a rejected value
      (scalar: equal; ``capabilities``: intersects). Reject wins over permit.

    Pure data + a predicate; nothing here binds a task -- the supervisor consults
    :meth:`ProfileDeclaration.permits`.
    """

    permit: Mapping[str, frozenset[str]] = field(default_factory=dict)
    reject: Mapping[str, frozenset[str]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.permit and not self.reject

    def permits(self, attrs: Mapping[str, object]) -> bool:
        """Does a task with these declared ``attrs`` clear this filter?"""
        norm = _normalize_task_attrs(attrs)
        for dim, rejected in self.reject.items():
            tv = norm.get(dim)
            if dim in _FILTER_SET_DIMS:
                if tv and (set(tv) & rejected):
                    return False
            elif tv is not None and tv in rejected:
                return False
        for dim, permitted in self.permit.items():
            tv = norm.get(dim)
            if dim in _FILTER_SET_DIMS:
                if not set(tv or ()) <= permitted:
                    return False
            elif tv is not None and tv not in permitted:
                return False
        return True


@dataclass(frozen=True)
class ProfileDeclaration:
    """One declarative unit of supervised work -- the registrar's atom.

    A lossless superset of a legacy ``AGENT_DISPATCH_SUPERVISE_*`` env profile:
    :meth:`to_supervise_args` renders the exact ``agent-dispatch supervise ...``
    invocation this declaration stands for.
    """

    name: str
    kind: str = "supervised-lane"
    spec: Mapping[str, object] = field(default_factory=dict)
    labels: tuple[str, ...] = ()
    repos: str = "all"  # "all" -> --all-repos, else a lane (--repo <lane>)
    concurrency: int = 1
    interval: float = 30.0
    max_attempts: int = 3
    label_max_attempts: Mapping[str, int] = field(default_factory=dict)
    heartbeat: bool = True
    reactive: bool = False
    reactive_interval: float = 2.0
    verify_timeout: int = 0
    body: Body = field(default_factory=Body)
    fleet: Fleet = field(default_factory=Fleet)
    filters: Filters = field(default_factory=Filters)
    evaluator: str | None = None
    owner: str | None = None  # provenance: which system declared this
    description: str | None = None
    runtime_generation: str | None = None
    transition_group: str | None = None
    plugin_root: str | None = None
    source_path: str | None = None
    plugin_version: str | None = None
    activation_scopes: tuple[str, ...] = ()

    def to_supervise_args(self) -> list[str]:
        """Render the equivalent ``agent-dispatch supervise`` args (lossless).

        The exact argv the singleton would run to service this declaration. This is
        the proof of *lossless superset*: every legacy env profile maps to a
        declaration whose rendered args reproduce its old invocation.
        """
        if self.kind != "supervised-lane":
            return [
                "supervise",
                "register",
                "--kind",
                self.kind,
                "--spec",
                json.dumps(dict(self.spec), sort_keys=True),
            ]

        args: list[str] = ["supervise"]
        if self.repos == "all":
            args.append("--all-repos")
        else:
            args += ["--repo", self.repos]
        for label in self.labels:
            args += ["--label", label]
        args += ["--max-concurrent", str(self.concurrency)]
        args += ["--max-attempts", str(self.max_attempts)]
        for label, n in self.label_max_attempts.items():
            args += ["--label-max-attempts", f"{label}={n}"]
        args += ["--interval", _num(self.interval)]
        if not self.heartbeat:
            args.append("--no-heartbeat")
        if not self.reactive:
            args.append("--no-reactive")
        args += ["--reactive-interval", _num(self.reactive_interval)]
        if self.verify_timeout:
            args += ["--verify-timeout", str(self.verify_timeout)]
        if self.fleet.enabled:
            args += ["--pool", ",".join(self.fleet.pool)]
            if self.fleet.origin:
                args += ["--origin", self.fleet.origin]
            # Headless is the default body; a fleet pins CLI explicitly.
            if self.body.type == "embody" and not self.fleet.headless:
                args += ["--embody-backend", "cli"]
            elif self.fleet.headless:
                args.append("--headless")
        elif self.body.type == "embody":
            # CLI-default lane: --headless-label opts a subset into a headless body.
            args += ["--embody-backend", "cli"]
            for label in self.body.headless_labels:
                args += ["--headless-label", label]
        else:
            # Headless-default lane (the default): --cli-label opts a subset out to CLI.
            for label in self.body.cli_labels:
                args += ["--cli-label", label]
        for label in self.body.disposable_cli_labels:
            args += ["--disposable-cli-label", label]
        if self.body.type == "headless" or self.body.headless_labels or self.fleet.headless:
            args += ["--headless-agent", self.body.agent]
        if self.evaluator:
            args += ["--evaluator", self.evaluator]
        return args

    def _effective_headless_labels(self) -> tuple[str, ...]:
        """Which labels this (local) profile routes to a headless body.

        A ``headless`` body (the default) routes *every* watched label headless
        (minus any ``cli_labels`` opt-outs). An ``embody`` body routes only its
        explicit ``headless_labels`` (a mixed profile), none by default.
        """
        if self.body.type == "headless":
            cli = set(self.body.cli_labels)
            return tuple(label for label in self.labels if label not in cli)
        return self.body.headless_labels

    def effective_headless_labels(self) -> tuple[str, ...]:
        """Public accessor for the labels this (local) profile routes headless.

        The supervisor's declaration->registration bridge needs this to build the
        lane spec, so it is exposed rather than kept private."""
        return self._effective_headless_labels()

    def with_owner(self, owner: str) -> ProfileDeclaration:
        """Return a copy stamped with discovery-time provenance (the pointer's owner),
        used when a declaration is discovered without an explicit ``owner``."""
        return replace(self, owner=self.owner or owner)

    def with_plugin_provenance(
        self,
        *,
        plugin_root: str,
        source_path: str,
        plugin_version: str,
        activation_scopes: tuple[str, ...],
    ) -> ProfileDeclaration:
        """Attach authoritative discovery metadata to a plugin-owned declaration."""
        return replace(
            self,
            plugin_root=plugin_root,
            source_path=source_path,
            plugin_version=plugin_version,
            activation_scopes=activation_scopes,
        )

    def effective_filters(self) -> Filters:
        """The pool filter with the ``name``/``labels``/``repos`` shorthand folded in.

        The legacy fields *are* a filter shorthand (vision: *pools-as-filters*, the
        registrar effort's Model section): a pool is *named for its task-type*, so an
        unspecified ``permit.task-type`` defaults to this profile's ``name`` plus its
        watched ``labels`` (the legacy label gate generalizes to task-type); an
        unspecified ``permit.repo`` defaults to the profile's lane (``repos``) unless
        it watches all repos. Explicit ``filters`` always win over the shorthand.
        """
        permit = dict(self.filters.permit)
        reject = dict(self.filters.reject)
        if "task-type" not in permit:
            permit["task-type"] = frozenset({self.name, *self.labels})
        if "repo" not in permit and self.repos != "all":
            permit["repo"] = frozenset({self.repos})
        return Filters(permit=permit, reject=reject)

    def permits(self, task_attrs: Mapping[str, object]) -> bool:
        """Does this pool (filter + shorthand) accept a task with these attributes?"""
        return self.effective_filters().permits(task_attrs)


def _num(x: float) -> str:
    """Render a float without a trailing ``.0`` so ``30.0`` -> ``30`` (arg parity)."""
    return str(int(x)) if float(x).is_integer() else str(x)


def _as_str_tuple(value: object, *, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        # A comma/space list is accepted (parity with the env form) as a convenience.
        return tuple(p for p in value.replace(",", " ").split() if p)
    if isinstance(value, Sequence):
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise RegistrarError(f"{key}: every entry must be a string, got {item!r}")
            out.append(item)
        return tuple(out)
    raise RegistrarError(f"{key}: expected a string or list, got {type(value).__name__}")


def _as_int(value: object, *, key: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RegistrarError(f"{key}: expected an integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise RegistrarError(f"{key}: must be >= {minimum}, got {value}")
    return value


def _as_float(value: object, *, key: str, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RegistrarError(f"{key}: expected a number, got {value!r}")
    v = float(value)
    if minimum is not None and v < minimum:
        raise RegistrarError(f"{key}: must be >= {minimum}, got {v}")
    return v


def _as_bool(value: object, *, key: str) -> bool:
    if not isinstance(value, bool):
        raise RegistrarError(f"{key}: expected true/false, got {value!r}")
    return value


def _reject_unknown(data: Mapping, known: frozenset[str], *, where: str) -> None:
    extra = set(data) - known
    if extra:
        raise RegistrarError(
            f"{where}: unknown key(s) {sorted(extra)}; known: {sorted(known)}"
        )


def _load_body(data: object) -> Body:
    if data is None:
        return Body()
    if not isinstance(data, Mapping):
        raise RegistrarError(f"body: expected a mapping, got {type(data).__name__}")
    _reject_unknown(data, _KNOWN_BODY_KEYS, where="body")
    btype = data.get("type", "headless")
    if btype not in _VALID_BODY_TYPES:
        raise RegistrarError(
            f"body.type: must be one of {sorted(_VALID_BODY_TYPES)}, got {btype!r}"
        )
    agent = data.get("agent", "task-worker")
    if not isinstance(agent, str) or not agent:
        raise RegistrarError(f"body.agent: expected a non-empty string, got {agent!r}")
    return Body(
        type=btype,
        agent=agent,
        headless_labels=_as_str_tuple(data.get("headless_labels"), key="body.headless_labels"),
        cli_labels=_as_str_tuple(data.get("cli_labels"), key="body.cli_labels"),
        disposable_cli_labels=_as_str_tuple(
            data.get("disposable_cli_labels"),
            key="body.disposable_cli_labels",
        ),
    )


def _load_fleet(data: object) -> Fleet:
    if data is None:
        return Fleet()
    if not isinstance(data, Mapping):
        raise RegistrarError(f"fleet: expected a mapping, got {type(data).__name__}")
    _reject_unknown(data, _KNOWN_FLEET_KEYS, where="fleet")
    origin = data.get("origin")
    if origin is not None and (not isinstance(origin, str) or not origin):
        raise RegistrarError(f"fleet.origin: expected a non-empty string, got {origin!r}")
    return Fleet(
        pool=_as_str_tuple(data.get("pool"), key="fleet.pool"),
        origin=origin,
        headless=_as_bool(data.get("headless", False), key="fleet.headless"),
    )


def _load_filter_side(data: object, *, where: str) -> dict[str, frozenset[str]]:
    """Parse one side (``permit`` or ``reject``) of a filter into dim -> value set."""
    if data is None:
        return {}
    if not isinstance(data, Mapping):
        raise RegistrarError(f"{where}: expected a mapping of dimension->values, "
                             f"got {type(data).__name__}")
    out: dict[str, frozenset[str]] = {}
    for raw_key, value in data.items():
        dim = _canon_dim(raw_key)
        if dim not in _FILTER_DIMS:
            raise RegistrarError(
                f"{where}: unknown dimension {dim!r}; known: {sorted(_FILTER_DIMS)}"
            )
        out[dim] = frozenset(_as_str_tuple(value, key=f"{where}.{dim}"))
    return out


def _load_filters(data: object) -> Filters:
    if data is None:
        return Filters()
    if not isinstance(data, Mapping):
        raise RegistrarError(f"filters: expected a mapping, got {type(data).__name__}")
    _reject_unknown(data, _KNOWN_FILTER_KEYS, where="filters")
    return Filters(
        permit=_load_filter_side(data.get("permit"), where="filters.permit"),
        reject=_load_filter_side(data.get("reject"), where="filters.reject"),
    )


def _normalize_task_attrs(attrs: Mapping[str, object]) -> dict[str, object]:
    """Normalize a task's declared attributes for filter matching.

    Canonicalizes dimension names (``task_type`` -> ``task-type``), coerces the
    ``capabilities`` set dimension to a tuple, and leaves scalar dimensions as their
    string value. Unknown dimensions are ignored (a task may carry attributes no
    pool filters on).
    """
    if not isinstance(attrs, Mapping):
        raise RegistrarError(f"task attributes: expected a mapping, got {type(attrs).__name__}")
    out: dict[str, object] = {}
    for raw_key, value in attrs.items():
        dim = _canon_dim(raw_key)
        if dim not in _FILTER_DIMS:
            continue
        if dim in _FILTER_SET_DIMS:
            out[dim] = _as_str_tuple(value, key=dim)
        elif value is not None:
            if not isinstance(value, str):
                raise RegistrarError(f"task attribute {dim!r}: expected a string, got {value!r}")
            out[dim] = value
    return out


def _load_label_max_attempts(value: object) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RegistrarError(
            f"label_max_attempts: expected a mapping label->int, got {type(value).__name__}"
        )
    out: dict[str, int] = {}
    for label, n in value.items():
        if not isinstance(label, str) or not label:
            raise RegistrarError(f"label_max_attempts: bad label {label!r}")
        out[label] = _as_int(n, key=f"label_max_attempts[{label}]", minimum=0)
    return out


def load_declaration(
    data: Mapping, *, allow_plugin_companion: bool = False
) -> ProfileDeclaration:
    """Validate + normalize a decoded mapping into a :class:`ProfileDeclaration`.

    Raises :class:`RegistrarError` with a specific message on any problem. Pure:
    the caller supplies the already-parsed mapping (YAML/JSON decoding + file I/O
    live in the discovery layer).
    """
    if not isinstance(data, Mapping):
        raise RegistrarError(f"declaration: expected a mapping, got {type(data).__name__}")
    _reject_unknown(data, _KNOWN_KEYS, where="declaration")

    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise RegistrarError("declaration: 'name' is required and must be a non-empty string")
    if not all(c.isalnum() or c in "-_" for c in name):
        raise RegistrarError(
            f"name: {name!r} -- use only letters, digits, '-' and '_' (it names a unit)"
        )

    repos = data.get("repos", "all")
    if not isinstance(repos, str) or not repos:
        raise RegistrarError(f"repos: expected 'all' or a lane string, got {repos!r}")

    evaluator = data.get("evaluator")
    if evaluator is not None and (not isinstance(evaluator, str) or not evaluator):
        raise RegistrarError(f"evaluator: expected a path string, got {evaluator!r}")
    for opt_key in ("owner", "description"):
        v = data.get(opt_key)
        if v is not None and not isinstance(v, str):
            raise RegistrarError(f"{opt_key}: expected a string, got {v!r}")

    kind = data.get("kind", "supervised-lane")
    if not isinstance(kind, str):
        raise RegistrarError(f"kind: expected a string, got {kind!r}")
    if kind != "supervised-lane":
        from .registrations import RegistrationError, RegistrationKind, validate_registration

        if kind == RegistrationKind.PLUGIN_COMPANION and not allow_plugin_companion:
            raise RegistrarError(
                "plugin-companion declarations require attributed plugin discovery"
            )

        allowed = {
            "name",
            "kind",
            "spec",
            "filters",
            "owner",
            "description",
            "runtime_generation",
            "transition_group",
        }
        extras = sorted(set(data) - allowed)
        if extras:
            raise RegistrarError(
                f"declaration kind {kind!r} does not accept lane fields: {extras}"
            )
        runtime_generation = data.get("runtime_generation")
        if runtime_generation is not None and kind != RegistrationKind.PLUGIN_COMPANION:
            raise RegistrarError(
                "runtime_generation: only plugin-companion declarations may set it"
            )
        if runtime_generation is not None and (
            not isinstance(runtime_generation, str)
            or not runtime_generation
        ):
            raise RegistrarError(
                "runtime_generation: plugin-companion declarations require a non-empty string"
            )
        transition_group = data.get("transition_group")
        if transition_group is not None and kind != RegistrationKind.PLUGIN_COMPANION:
            raise RegistrarError(
                "transition_group: only plugin-companion declarations may set it"
            )
        if transition_group is not None and (
            not isinstance(transition_group, str)
            or not transition_group.strip()
        ):
            raise RegistrarError(
                "transition_group: plugin-companion declarations require a non-empty string"
            )
        spec = data.get("spec")
        if not isinstance(spec, Mapping):
            raise RegistrarError(
                f"declaration kind {kind!r} needs a 'spec' mapping"
            )
        try:
            validate_registration(kind, dict(spec))
        except RegistrationError as exc:
            raise RegistrarError(str(exc)) from exc
        return ProfileDeclaration(
            name=name,
            kind=kind,
            spec=dict(spec),
            filters=_load_filters(data.get("filters")),
            owner=data.get("owner"),
            description=data.get("description"),
            runtime_generation=runtime_generation,
            transition_group=transition_group,
        )

    for field in ("runtime_generation", "transition_group"):
        if data.get(field) is not None:
            raise RegistrarError(
                f"{field}: only plugin-companion declarations may set it"
            )

    legacy_concurrency = data.get("concurrency")
    process_cap = data.get(
        "max_active_processes",
        legacy_concurrency if legacy_concurrency is not None else 1,
    )
    if (
        legacy_concurrency is not None
        and data.get("max_active_processes") is not None
        and legacy_concurrency != data.get("max_active_processes")
    ):
        raise RegistrarError(
            "concurrency and max_active_processes must agree when both are set"
        )
    _as_bool(data.get("reactive", False), key="reactive")
    decl = ProfileDeclaration(
        name=name,
        kind=kind,
        labels=_as_str_tuple(data.get("labels"), key="labels"),
        repos=repos,
        concurrency=_as_int(
            process_cap,
            key=(
                "max_active_processes"
                if data.get("max_active_processes") is not None
                else "concurrency"
            ),
            minimum=1,
        ),
        interval=_as_float(data.get("interval", 30.0), key="interval", minimum=1.0),
        max_attempts=_as_int(data.get("max_attempts", 3), key="max_attempts", minimum=0),
        label_max_attempts=_load_label_max_attempts(data.get("label_max_attempts")),
        heartbeat=_as_bool(data.get("heartbeat", True), key="heartbeat"),
        reactive=False,
        reactive_interval=_as_float(
            data.get("reactive_interval", 2.0), key="reactive_interval", minimum=0.1
        ),
        verify_timeout=_as_int(data.get("verify_timeout", 0), key="verify_timeout", minimum=0),
        body=_load_body(data.get("body")),
        fleet=_load_fleet(data.get("fleet")),
        filters=_load_filters(data.get("filters")),
        evaluator=evaluator,
        owner=data.get("owner"),
        description=data.get("description"),
    )

    # A headless local profile whose headless labels are a subset that doesn't
    # intersect the watched labels supervises nothing headless -- catch the typo.
    if decl.body.headless_labels and not decl.fleet.enabled:
        stray = set(decl.body.headless_labels) - set(decl.labels)
        if stray:
            raise RegistrarError(
                f"body.headless_labels {sorted(stray)} are not in labels "
                f"{sorted(decl.labels)} -- a headless label must also be watched"
            )
    # Symmetric guard for the headless-default opt-out: a cli_labels entry that is
    # not watched forces nothing to CLI -- catch the typo.
    if decl.body.cli_labels and not decl.fleet.enabled:
        stray = set(decl.body.cli_labels) - set(decl.labels)
        if stray:
            raise RegistrarError(
                f"body.cli_labels {sorted(stray)} are not in labels "
                f"{sorted(decl.labels)} -- a cli label must also be watched"
            )
    if decl.body.disposable_cli_labels:
        watched = set(decl.labels)
        disposable = set(decl.body.disposable_cli_labels)
        if stray := disposable - watched:
            raise RegistrarError(
                f"body.disposable_cli_labels {sorted(stray)} are not in labels "
                f"{sorted(decl.labels)} -- a disposable CLI label must be watched"
            )
        if decl.fleet.enabled:
            raise RegistrarError(
                "body.disposable_cli_labels are supported only for local "
                "worker bodies"
            )
    return decl


# --- Migration: legacy AGENT_DISPATCH_SUPERVISE_* env profile -> declaration -----

_ENV_PREFIX = "AGENT_DISPATCH_SUPERVISE_"

#: EXTRA_ARGS value flags (take an argument) parsed on migration so a legacy
#: profile converts losslessly. The bare flags (--all-repos, --no-heartbeat,
#: --no-reactive, --headless) are matched directly in :func:`_parse_extra_args`.
_EXTRA_VALUE_KEYS = ("--repo", "--pool", "--origin", "--headless-agent", "--evaluator")


def _env_int(raw: str | None, *, key: str, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise RegistrarError(f"{key}: expected an integer, got {raw!r}") from exc


def _env_float(raw: str | None, *, key: str, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise RegistrarError(f"{key}: expected a number, got {raw!r}") from exc


def declaration_from_env(name: str, env: Mapping[str, str]) -> ProfileDeclaration:
    """Convert a legacy ``AGENT_DISPATCH_SUPERVISE_*`` env profile into a declaration.

    The migration path (registrar effort Phase 4): existing ``supervisors/<name>.env``
    profiles become declarations without behavior change. ``name`` is the profile
    name (the env file's stem). Unrecognized ``EXTRA_ARGS`` are ignored here (they
    are surfaced by the migration tool, not silently dropped in production).
    """
    def g(key: str) -> str | None:
        v = env.get(_ENV_PREFIX + key)
        return v.strip() if isinstance(v, str) and v.strip() else None

    labels = _as_str_tuple(g("LABELS"), key="LABELS")
    headless_labels = _as_str_tuple(g("HEADLESS_LABELS"), key="HEADLESS_LABELS")
    cli_labels = _as_str_tuple(g("CLI_LABELS"), key="CLI_LABELS")
    headless_agent = g("HEADLESS_AGENT")
    backend = g("EMBODY_BACKEND")
    if backend is not None and backend not in ("headless", "cli"):
        raise RegistrarError(
            f"EMBODY_BACKEND: must be 'headless' or 'cli', got {backend!r}"
        )

    label_max: dict[str, int] = {}
    if (raw := g("LABEL_MAX_ATTEMPTS")) is not None:
        for pair in raw.replace(",", " ").split():
            if "=" not in pair:
                raise RegistrarError(f"LABEL_MAX_ATTEMPTS: expected LABEL=N, got {pair!r}")
            lbl, _, num = pair.partition("=")
            try:
                label_max[lbl] = int(num)
            except ValueError as exc:
                raise RegistrarError(f"LABEL_MAX_ATTEMPTS: N must be int, got {num!r}") from exc

    extra = _parse_extra_args((g("EXTRA_ARGS") or "").split())
    # The dedicated HEADLESS_AGENT var wins; an EXTRA_ARGS --headless-agent is a fallback.
    agent = headless_agent or extra["headless_agent"]
    # Embody backend defaults to HEADLESS. Only an explicit EMBODY_BACKEND=cli makes
    # the lane CLI-default; HEADLESS_LABELS alone no longer forces CLI (it is the
    # headless subset for a cli lane, redundant on a headless one). So a plain file
    # is headless, and an existing HEADLESS_LABELS=<all watched> file stays headless.
    body_type = "embody" if backend == "cli" else "headless"

    body: dict[str, object] = {"type": body_type}
    if agent:
        body["agent"] = agent
    if body_type == "embody" and headless_labels:
        body["headless_labels"] = list(headless_labels)
    if body_type == "headless" and cli_labels:
        body["cli_labels"] = list(cli_labels)

    data: dict[str, object] = {
        "name": name,
        "labels": list(labels),
        "repos": extra["repo"] or "all",
        "concurrency": _env_int(g("MAX_CONCURRENT"), key="MAX_CONCURRENT", default=1),
        "interval": _env_float(g("INTERVAL"), key="INTERVAL", default=30.0),
        "max_attempts": _env_int(g("MAX_ATTEMPTS"), key="MAX_ATTEMPTS", default=3),
        "label_max_attempts": label_max,
        "heartbeat": not extra["no_heartbeat"],
        "reactive": False,
        "body": body,
    }
    if extra["pool"]:
        data["fleet"] = {
            "pool": extra["pool"],
            **({"origin": extra["origin"]} if extra["origin"] else {}),
            "headless": extra["headless"],
        }
    if extra["evaluator"]:
        data["evaluator"] = extra["evaluator"]
    return load_declaration(data)


def _parse_extra_args(tokens: list[str]) -> dict[str, object]:
    """Light parse of the legacy EXTRA_ARGS flags relevant to a declaration."""
    out: dict[str, object] = {
        "all_repos": False,
        "no_heartbeat": False,
        "no_reactive": False,
        "headless": False,
        "repo": None,
        "pool": [],
        "origin": None,
        "headless_agent": None,
        "evaluator": None,
    }
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--all-repos":
            out["all_repos"] = True
        elif tok == "--no-heartbeat":
            out["no_heartbeat"] = True
        elif tok == "--no-reactive":
            out["no_reactive"] = True
        elif tok == "--headless":
            out["headless"] = True
        elif tok in _EXTRA_VALUE_KEYS and i + 1 < len(tokens):
            val = tokens[i + 1]
            if tok == "--repo":
                out["repo"] = val
            elif tok == "--pool":
                out["pool"] = [p for p in val.replace(",", " ").split() if p]
            elif tok == "--origin":
                out["origin"] = val
            elif tok == "--headless-agent":
                out["headless_agent"] = val
            elif tok == "--evaluator":
                out["evaluator"] = val
            i += 1
        i += 1
    return out
