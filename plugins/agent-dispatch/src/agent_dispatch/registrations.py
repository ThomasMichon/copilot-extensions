"""Supervisor **registration** model -- the durable unit a caller hands the
host's singleton supervisor.

A registration is *what to run*, not *the running of it*: a lane to spawn for, a
schedule, an emitter, or an evaluator, captured as a ``kind`` plus a ``spec``
dict (the config the unit's runtime consumes) and scoped to a
``machine``-and-``env``. Registering **adds the registration and returns its
handle**; the singleton supervisor (the daemon, a later increment) is what turns
each registration into a live subprocess. This module is deliberately
transport- and store-free: it defines the record, the kind/status vocabularies,
validation, and stable-id derivation, so both the queue store and the CLI share
one definition.

See ``visions/plugins/agent-dispatch`` -- Concept *the supervisor*, Feature
*registered-supervision*, Behavior *supervise-registers-and-returns*.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath


class RegistrationKind:
    """The kinds of work a caller can register with the singleton supervisor.

    Each names a runtime the supervisor drives in its own subprocess: a
    ``supervised-lane`` spawns claimable tasks in a lane; a ``schedule`` fires a
    recurring producer; an ``emitter`` runs an event source (e.g. a webhook
    listener); an ``evaluator`` advances a domain loop across terminal tasks.
    """

    SUPERVISED_LANE = "supervised-lane"
    SCHEDULE = "schedule"
    EMITTER = "emitter"
    EVALUATOR = "evaluator"
    PLUGIN_COMPANION = "plugin-companion"

    ALL = frozenset(
        {SUPERVISED_LANE, SCHEDULE, EMITTER, EVALUATOR, PLUGIN_COMPANION}
    )
    DIRECT = frozenset({SUPERVISED_LANE, SCHEDULE, EMITTER, EVALUATOR})


class RegistrationStatus:
    """Lifecycle status of a registration (the caller-owned token's state).

    ``active`` registrations are the ones the singleton reconciles into running
    subprocesses; ``paused`` retains the definition but is skipped by the
    reconcile (the analogue of a paused schedule). Removal deletes the row
    entirely (and, once the daemon lands, winds down its running work).
    """

    ACTIVE = "active"
    PAUSED = "paused"

    ALL = frozenset({ACTIVE, PAUSED})


class RegistrationError(ValueError):
    """Raised when a registration's kind or spec is malformed."""


_COMPANION_KEYS = frozenset(
    {
        "command",
        "stop_command",
        "config_provider",
        "health_probe",
        "config_timeout_seconds",
        "health_timeout_seconds",
        "startup_timeout_seconds",
        "stop_timeout_seconds",
        "managed_runtime",
    }
)
_COMPANION_REQUIRED_KEYS = frozenset({"command"})
_COMPANION_TIMEOUT_KEYS = frozenset(
    {
        "config_timeout_seconds",
        "health_timeout_seconds",
        "startup_timeout_seconds",
        "stop_timeout_seconds",
    }
)
COMPANION_CONFIG_RESULT_VERSION = 1
COMPANION_HEALTH_RESULT_VERSION = 1
MANAGED_RUNTIME_SCHEMA_VERSION = 1
_PORTABLE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_IMPORT_NAME = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
_MAX_PROJECT_PATH_LENGTH = 1024
_MAX_PROJECT_COMPONENTS = 32
_MAX_PROJECT_COMPONENT_LENGTH = 128
_MAX_IMPORT_LENGTH = 512
_MAX_IMPORT_COMPONENTS = 16
_MAX_IMPORT_COMPONENT_LENGTH = 128
_WINDOWS_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
    | {f"{prefix}{index}" for prefix in ("com", "lpt") for index in "¹²³"}
)


def _validate_plugin_relative_argv(value: object, *, field: str) -> None:
    if not isinstance(value, list) or not value or not all(
        isinstance(part, str) and part for part in value
    ):
        raise RegistrationError(
            f"plugin-companion '{field}' must be a non-empty argv list"
        )
    executable = value[0].replace("\\", "/")
    path = PurePosixPath(executable)
    if path.is_absolute() or re.match(r"^[A-Za-z]:", executable):
        raise RegistrationError(
            f"plugin-companion '{field}' executable must be plugin-relative"
        )
    if any(part in ("", ".", "..") for part in path.parts):
        raise RegistrationError(
            f"plugin-companion '{field}' executable must be a contained relative path"
        )


def _validate_portable_component(value: object, *, field: str) -> None:
    if (
        not isinstance(value, str)
        or value in {".", ".."}
        or not _PORTABLE_COMPONENT.fullmatch(value)
        or value.endswith(".")
        or value.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_STEMS
    ):
        raise RegistrationError(
            f"plugin-companion managed runtime '{field}' must be a portable "
            "filesystem component"
        )


def _validate_project_path(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RegistrationError(
            f"plugin-companion managed runtime '{field}' must be a plugin-relative path"
        )
    normalized = value
    if normalized == ".":
        return normalized
    parts = normalized.split("/")
    path = PurePosixPath(normalized)
    if (
        len(normalized) > _MAX_PROJECT_PATH_LENGTH
        or len(parts) > _MAX_PROJECT_COMPONENTS
        or any(len(part) > _MAX_PROJECT_COMPONENT_LENGTH for part in parts)
        or path.is_absolute()
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in ("", ".", "..") for part in parts)
        or any(
            part.endswith((".", " "))
            or any(ord(character) < 32 or character in '<>:"|?*' for character in part)
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_STEMS
            for part in parts
        )
    ):
        raise RegistrationError(
            f"plugin-companion managed runtime '{field}' must be a contained "
            "plugin-relative path"
        )
    return path.as_posix()


def _validate_managed_runtime(value: object) -> None:
    if not isinstance(value, dict):
        raise RegistrationError(
            "plugin-companion 'managed_runtime' must be a JSON object"
        )
    allowed = {"schema_version", "runtimes"}
    if unknown := sorted(set(value) - allowed):
        raise RegistrationError(
            "plugin-companion managed runtime has unknown fields: "
            + ", ".join(unknown)
        )
    schema_version = value.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != MANAGED_RUNTIME_SCHEMA_VERSION
    ):
        raise RegistrationError(
            "plugin-companion managed runtime needs schema_version "
            f"{MANAGED_RUNTIME_SCHEMA_VERSION}"
        )
    runtimes = value.get("runtimes")
    if not isinstance(runtimes, list) or not runtimes or len(runtimes) > 16:
        raise RegistrationError(
            "plugin-companion managed runtime 'runtimes' must contain 1 to 16 objects"
        )

    runtime_names: set[str] = set()
    python_environments: set[str] = set()
    runtime_keys = {
        "name",
        "version",
        "profile",
        "python_env",
        "projects",
        "identity_paths",
        "imports",
    }
    required_runtime_keys = {
        "name",
        "version",
        "profile",
        "python_env",
        "projects",
        "imports",
    }
    for index, runtime in enumerate(runtimes):
        prefix = f"runtimes[{index}]"
        if not isinstance(runtime, dict):
            raise RegistrationError(
                f"plugin-companion managed runtime '{prefix}' must be a JSON object"
            )
        if unknown := sorted(set(runtime) - runtime_keys):
            raise RegistrationError(
                f"plugin-companion managed runtime '{prefix}' has unknown fields: "
                + ", ".join(unknown)
            )
        missing = sorted(required_runtime_keys - set(runtime))
        if missing:
            raise RegistrationError(
                f"plugin-companion managed runtime '{prefix}' is missing required "
                f"fields: {', '.join(missing)}"
            )
        for field in ("name", "version", "profile"):
            _validate_portable_component(
                runtime[field], field=f"{prefix}.{field}"
            )
        name = runtime["name"]
        canonical_name = name.casefold()
        if canonical_name in runtime_names:
            raise RegistrationError(
                f"plugin-companion managed runtime has duplicate runtime name: {name}"
            )
        runtime_names.add(canonical_name)

        python_env = runtime["python_env"]
        if (
            not isinstance(python_env, str)
            or not _ENVIRONMENT_NAME.fullmatch(python_env)
        ):
            raise RegistrationError(
                f"plugin-companion managed runtime '{prefix}.python_env' must be "
                "an environment variable name"
            )
        canonical_python_env = python_env.casefold()
        if canonical_python_env in python_environments:
            raise RegistrationError(
                "plugin-companion managed runtime has duplicate python environment: "
                f"{python_env}"
            )
        python_environments.add(canonical_python_env)

        projects = runtime["projects"]
        if not isinstance(projects, list) or not projects or len(projects) > 32:
            raise RegistrationError(
                f"plugin-companion managed runtime '{prefix}.projects' must contain "
                "1 to 32 objects"
            )
        project_paths: set[str] = set()
        for project_index, project in enumerate(projects):
            project_prefix = f"{prefix}.projects[{project_index}]"
            if not isinstance(project, dict):
                raise RegistrationError(
                    f"plugin-companion managed runtime '{project_prefix}' must be "
                    "a JSON object"
                )
            if unknown := sorted(set(project) - {"path", "extras"}):
                raise RegistrationError(
                    f"plugin-companion managed runtime '{project_prefix}' has "
                    f"unknown fields: {', '.join(unknown)}"
                )
            if "path" not in project:
                raise RegistrationError(
                    f"plugin-companion managed runtime '{project_prefix}' is "
                    "missing required field: path"
                )
            project_path = _validate_project_path(
                project["path"], field=f"{project_prefix}.path"
            )
            canonical_project_path = project_path.casefold()
            if canonical_project_path in project_paths:
                raise RegistrationError(
                    f"plugin-companion managed runtime '{prefix}' has duplicate "
                    f"project path: {project_path}"
                )
            project_paths.add(canonical_project_path)
            extras = project.get("extras")
            if extras is not None and (
                not isinstance(extras, list)
                or not extras
                or len(extras) > 32
                or not all(
                    isinstance(extra, str)
                    and _PORTABLE_COMPONENT.fullmatch(extra)
                    and not extra.endswith(".")
                    and extra.split(".", 1)[0].casefold()
                    not in _WINDOWS_RESERVED_STEMS
                    for extra in extras
                )
                or len({extra.casefold() for extra in extras}) != len(extras)
            ):
                raise RegistrationError(
                    f"plugin-companion managed runtime '{project_prefix}.extras' "
                    "must contain 1 to 32 unique portable names"
                )
        identity_paths = runtime.get("identity_paths")
        if identity_paths is not None:
            if (
                not isinstance(identity_paths, list)
                or not identity_paths
                or len(identity_paths) > 64
            ):
                raise RegistrationError(
                    f"plugin-companion managed runtime '{prefix}.identity_paths' "
                    "must contain 1 to 64 paths"
                )
            seen_identity_paths: set[str] = set()
            for identity_index, identity_path in enumerate(identity_paths):
                normalized_identity_path = _validate_project_path(
                    identity_path,
                    field=f"{prefix}.identity_paths[{identity_index}]",
                )
                canonical_identity_path = normalized_identity_path.casefold()
                if canonical_identity_path in seen_identity_paths:
                    raise RegistrationError(
                        f"plugin-companion managed runtime '{prefix}' has duplicate "
                        f"identity path: {normalized_identity_path}"
                    )
                seen_identity_paths.add(canonical_identity_path)

        imports = runtime["imports"]
        if (
            not isinstance(imports, list)
            or not imports
            or len(imports) > 32
            or not all(
                isinstance(import_name, str)
                and len(import_name) <= _MAX_IMPORT_LENGTH
                and len(import_name.split(".")) <= _MAX_IMPORT_COMPONENTS
                and all(
                    len(component) <= _MAX_IMPORT_COMPONENT_LENGTH
                    for component in import_name.split(".")
                )
                and _IMPORT_NAME.fullmatch(import_name)
                for import_name in imports
            )
            or len({import_name.casefold() for import_name in imports}) != len(imports)
        ):
            raise RegistrationError(
                f"plugin-companion managed runtime '{prefix}.imports' must contain "
                "1 to 32 unique Python import names"
            )


def _validate_plugin_companion(spec: dict) -> None:
    unknown = sorted(set(spec) - _COMPANION_KEYS)
    if unknown:
        raise RegistrationError(
            f"plugin-companion spec has unknown fields: {', '.join(unknown)}"
        )
    missing = sorted(_COMPANION_REQUIRED_KEYS - set(spec))
    if missing:
        raise RegistrationError(
            f"plugin-companion spec is missing required fields: {', '.join(missing)}"
        )
    for field in ("command", "stop_command", "health_probe", "config_provider"):
        if field in spec:
            _validate_plugin_relative_argv(spec[field], field=field)
    for field in _COMPANION_TIMEOUT_KEYS:
        value = spec.get(field)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
            or value > 3600
        ):
            raise RegistrationError(
                f"plugin-companion '{field}' must be > 0 and <= 3600"
            )
    if "managed_runtime" in spec:
        _validate_managed_runtime(spec["managed_runtime"])


def validate_companion_config_result(result: dict) -> None:
    """Validate the versioned JSON contract emitted by a companion config provider."""
    if not isinstance(result, dict):
        raise RegistrationError("companion config result must be a JSON object")
    allowed = {"schema_version", "active", "arguments", "environment"}
    if unknown := sorted(set(result) - allowed):
        raise RegistrationError(
            f"companion config result has unknown fields: {', '.join(unknown)}"
        )
    schema_version = result.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != COMPANION_CONFIG_RESULT_VERSION
    ):
        raise RegistrationError(
            "companion config result needs schema_version "
            f"{COMPANION_CONFIG_RESULT_VERSION}"
        )
    if not isinstance(result.get("active"), bool):
        raise RegistrationError("companion config result 'active' must be true/false")
    arguments = result.get("arguments")
    if arguments is not None and (
        not isinstance(arguments, list)
        or not all(isinstance(value, str) and value for value in arguments)
    ):
        raise RegistrationError(
            "companion config result 'arguments' must be a list of non-empty strings"
        )
    environment = result.get("environment")
    if environment is not None and (
        not isinstance(environment, dict)
        or not all(
            isinstance(key, str)
            and key
            and isinstance(value, str)
            for key, value in environment.items()
        )
    ):
        raise RegistrationError(
            "companion config result 'environment' must map strings to strings"
        )


def validate_companion_health_result(result: dict) -> None:
    """Validate the versioned JSON contract emitted by a companion health probe."""
    if not isinstance(result, dict):
        raise RegistrationError("companion health result must be a JSON object")
    allowed = {"schema_version", "healthy", "detail"}
    if unknown := sorted(set(result) - allowed):
        raise RegistrationError(
            f"companion health result has unknown fields: {', '.join(unknown)}"
        )
    schema_version = result.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != COMPANION_HEALTH_RESULT_VERSION
    ):
        raise RegistrationError(
            "companion health result needs schema_version "
            f"{COMPANION_HEALTH_RESULT_VERSION}"
        )
    if not isinstance(result.get("healthy"), bool):
        raise RegistrationError("companion health result 'healthy' must be true/false")
    detail = result.get("detail")
    if detail is not None and (not isinstance(detail, str) or not detail):
        raise RegistrationError(
            "companion health result 'detail' must be a non-empty string"
        )


def _validate_disposable_cli_labels(spec: dict) -> None:
    values = spec.get("disposable_cli_labels")
    if values is None:
        return
    if not isinstance(values, list) or not values or not all(
        isinstance(value, str) and value for value in values
    ):
        raise RegistrationError(
            "'disposable_cli_labels' must be a non-empty list of labels"
        )
    labels = spec.get("labels") or []
    if not isinstance(labels, list) or not all(
        isinstance(value, str) and value for value in labels
    ):
        raise RegistrationError(
            "a disposable CLI policy requires a valid 'labels' list"
        )
    watched = set(labels)
    disposable = set(values)
    if stray := disposable - watched:
        raise RegistrationError(
            f"disposable CLI labels {sorted(stray)} are not watched by this lane"
        )

    fleet = spec.get("fleet") or {}
    if not isinstance(fleet, dict):
        raise RegistrationError("'fleet' must be a JSON object")
    if fleet.get("pool"):
        raise RegistrationError(
            "disposable worker conclusion is supported only for local bodies"
        )
    for key, routed in (
        ("headless_labels", spec.get("headless_labels") or []),
        ("cli_labels", spec.get("cli_labels") or []),
    ):
        if not isinstance(routed, list) or not all(
            isinstance(value, str) and value for value in routed
        ):
            raise RegistrationError(f"'{key}' must be a list of labels")


def _schedule_entry(spec: dict, *, strict: bool) -> dict:
    """The single schedule entry a schedule registration carries.

    A schedule spec is either a bare entry, or ``{"schedules": [entry]}``. When a
    ``schedules`` field is present it must be a non-empty list of objects; a
    malformed one raises :class:`RegistrationError` under ``strict`` (validation),
    and falls back to the spec itself otherwise (best-effort id derivation).
    """
    scheds = spec.get("schedules")
    if scheds is None:
        return spec
    if isinstance(scheds, list) and scheds and isinstance(scheds[0], dict):
        return scheds[0]
    if strict:
        raise RegistrationError(
            "schedule 'schedules' must be a non-empty list of objects"
        )
    return spec


@dataclass
class RegistrationRecord:
    """A read-only snapshot of a registered supervision unit.

    ``spec`` is the JSON-round-tripped config the unit's runtime consumes
    verbatim (its shape depends on ``kind``). ``machine``/``env`` scope the
    registration to exactly one host's singleton supervisor -- the *one
    supervisor per machine-and-environment* that will run it.
    """

    id: str
    kind: str
    spec: dict
    machine: str | None = None
    env: str = "default"
    status: str = RegistrationStatus.ACTIVE
    created_at: float = 0.0
    updated_at: float = 0.0


def validate_registration(kind: str, spec: dict) -> None:
    """Validate a registration's ``kind`` and ``spec`` eagerly.

    Rejects an unknown kind or a non-dict spec, and applies a light per-kind
    shape check so a malformed registration is refused at register time rather
    than failing every reconcile. Intentionally permissive about the *contents*
    of a valid-shaped spec -- the unit's own runtime is the authority on the
    fine-grained schema.
    """
    if kind not in RegistrationKind.ALL:
        raise RegistrationError(
            f"unknown registration kind {kind!r}; expected one of "
            f"{', '.join(sorted(RegistrationKind.ALL))}"
        )
    if not isinstance(spec, dict):
        raise RegistrationError("registration 'spec' must be a JSON object")

    if kind == RegistrationKind.SUPERVISED_LANE:
        # A lane registration must name a scope: a specific repo, or an explicit
        # all-repos opt-in. This is what the supervisor spawns for.
        if not spec.get("repo") and not spec.get("all_repos"):
            raise RegistrationError(
                "supervised-lane registration needs a 'repo' (the lane) or "
                "'all_repos': true"
            )
        _validate_disposable_cli_labels(spec)
    elif kind == RegistrationKind.SCHEDULE:
        # A schedule is a self-run emitter: it needs an id (its dedup namespace,
        # 'sched:<id>:<epoch>') and a lane to emit into.
        if not spec:
            raise RegistrationError("schedule registration needs a non-empty spec")
        entry = _schedule_entry(spec, strict=True)
        if not entry.get("id"):
            raise RegistrationError("schedule registration needs an 'id'")
        if not entry.get("repo"):
            raise RegistrationError("schedule registration needs a 'repo' (the lane)")
    elif kind == RegistrationKind.EMITTER:
        if not spec:
            raise RegistrationError("emitter registration needs a non-empty spec")
        if "command" in spec or "repository_issue_loop" in spec:
            from .producers.emitter import EmitterError, validate_spec

            try:
                validate_spec(spec)
            except EmitterError as exc:
                raise RegistrationError(str(exc)) from exc
    elif kind == RegistrationKind.EVALUATOR:
        if not spec:
            raise RegistrationError("evaluator registration needs a non-empty spec")
        eval_spec = spec.get("evaluator_spec")
        if eval_spec is None and not spec.get("evaluator"):
            raise RegistrationError(
                "evaluator registration needs 'evaluator_spec' (inline) or "
                "'evaluator' (a path)"
            )
        if eval_spec is not None and not isinstance(eval_spec, dict):
            raise RegistrationError(
                "evaluator 'evaluator_spec' must be a JSON object"
            )
        if not spec.get("repo") and not spec.get("all_repos"):
            raise RegistrationError(
                "evaluator registration needs a 'repo' (the lane) or "
                "'all_repos': true"
            )
        evaluator_ref = spec.get("evaluator_ref")
        if evaluator_ref is not None and (
            not isinstance(evaluator_ref, str) or not evaluator_ref
        ):
            raise RegistrationError(
                "evaluator 'evaluator_ref' must be a non-empty string"
            )
        _validate_disposable_cli_labels(spec)
    elif kind == RegistrationKind.PLUGIN_COMPANION:
        _validate_plugin_companion(spec)


def _scope_key(kind: str, spec: dict) -> str:
    """A short, human-readable natural key for a registration's scope.

    Used only as the readable prefix of a derived id; the trailing digest is what
    actually guarantees idempotency, so this need only be stable, not unique.
    """
    if kind == RegistrationKind.SUPERVISED_LANE:
        lane = "all-repos" if spec.get("all_repos") else str(spec.get("repo") or "lane")
        labels = spec.get("labels") or spec.get("label") or []
        if isinstance(labels, str):
            labels = [labels]
        base = lane if not labels else f"{lane}-{'-'.join(sorted(str(x) for x in labels))}"
    elif kind == RegistrationKind.EVALUATOR:
        base = "all-repos" if spec.get("all_repos") else str(spec.get("repo") or "eval")
    elif kind == RegistrationKind.SCHEDULE:
        entry = _schedule_entry(spec, strict=False)
        base = str(entry.get("id") or kind)
    else:
        base = str(spec.get("id") or spec.get("name") or kind)
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    return slug or kind


def derive_registration_id(
    kind: str, spec: dict, machine: str | None, env: str
) -> str:
    """Derive a stable registration id from its identity.

    The id is deterministic in ``(kind, machine, env, spec)`` so **re-registering
    the same unit upserts** (the idempotency the vision requires) rather than
    creating a duplicate. It is a readable ``<kind>-<scope>-<digest>`` slug; the
    8-char digest disambiguates units that share a scope slug. A caller may
    always supply an explicit id instead.
    """
    payload = json.dumps(
        {"kind": kind, "machine": machine, "env": env, "spec": spec},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]
    return f"{kind}-{_scope_key(kind, spec)}-{digest}"
