"""Declarative tracked-effort driver loops.

The high-level declaration expands to an ordinary periodic emitter and one
headless supervised lane. The emitter discovers active effort READMEs from the
consumer's own state root and authors one goal-driven task per eligible effort.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .effort_driver_task_contracts import (
    build_task_contract,
    validate_task_contract,
)
from .registrar import (
    Filters,
    ProfileDeclaration,
    RegistrarError,
    _load_filters,
    load_declaration,
)
from .worker_identities import load_worker_identity

_TERMINAL = frozenset({"completed", "abandoned", "dead_letter"})
_ACTIVE_STATUSES = "proposed,queued,claimed,started,suspended,submitted"
_KNOWN_KEYS = frozenset(
    {
        "name",
        "kind",
        "repo",
        "source",
        "cadence_seconds",
        "tick_interval_seconds",
        "effort_slugs",
        "state_root",
        "task_label",
        "pool",
        "filters",
        "owner",
        "description",
        "worker_guidance",
        "worker_identity",
        "allow_self_config_changes",
        "require_verification",
        "evaluator_ref",
        "task_contract",
    }
)
_EFFORT_REF_RE = re.compile(r"(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#\d+")
_STATUS_RE = re.compile(r"(?m)^- \*\*Status:\*\*\s*(.+?)\s*$")


@dataclass(frozen=True)
class ActiveEffort:
    slug: str
    title: str
    readme_relative: str
    state_root: str
    status: str
    coordination_refs: tuple[str, ...]


def _string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise RegistrarError(f"effort-driver-loop {key}: expected a non-empty string")
    return value


def _number(
    data: Mapping[str, Any],
    key: str,
    *,
    default: int | float | None = None,
    minimum: float = 0,
) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RegistrarError(f"effort-driver-loop {key}: expected a number")
    result = float(value)
    if result < minimum:
        raise RegistrarError(
            f"effort-driver-loop {key}: must be >= {minimum:g}"
        )
    return result


def _strings(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key, ())
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise RegistrarError(
            f"effort-driver-loop {key}: expected a list of non-empty strings"
        )
    return tuple(dict.fromkeys(value))


def _resolve_state_root(
    state_root: str | None,
    *,
    cwd: str | Path | None,
) -> str:
    if state_root is None:
        if cwd is None:
            return str(Path.cwd().resolve())
        return str(Path(cwd).resolve())
    root = Path(state_root)
    if not root.is_absolute():
        if cwd is None:
            root = Path.cwd() / root
        else:
            root = Path(cwd) / root
    return str(root.resolve())


def validate_config(
    data: Mapping[str, Any],
    *,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    """Validate/normalize a declaration. ``cwd`` (declaring repo root, if
    known) threads a named ``worker_identity`` to its repo-local override and
    resolves the default/relative state root."""
    if not isinstance(data, Mapping):
        raise RegistrarError("effort-driver-loop: expected a mapping")
    extra = sorted(set(data) - _KNOWN_KEYS)
    if extra:
        raise RegistrarError(
            f"effort-driver-loop: unknown key(s) {extra}; known: {sorted(_KNOWN_KEYS)}"
        )
    if data.get("kind") != "effort-driver-loop":
        raise RegistrarError("effort-driver-loop kind must be 'effort-driver-loop'")
    name = _string(data, "name")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        raise RegistrarError("effort-driver-loop name: use letters, digits, '.', '_' or '-'")
    repo = _string(data, "repo")
    source = _string(data, "source")
    task_label = _string(data, "task_label")
    cadence = _number(data, "cadence_seconds", minimum=1)
    tick_interval = _number(
        data,
        "tick_interval_seconds",
        default=min(60.0, cadence),
        minimum=1,
    )
    require_verification = data.get("require_verification", False)
    if not isinstance(require_verification, bool):
        raise RegistrarError(
            "effort-driver-loop require_verification: expected true/false"
        )
    evaluator_ref = data.get("evaluator_ref")
    if evaluator_ref is not None and (not isinstance(evaluator_ref, str) or not evaluator_ref):
        raise RegistrarError("effort-driver-loop evaluator_ref: expected a non-empty string")
    effort_slugs = _strings(data, "effort_slugs")
    if not effort_slugs:
        raise RegistrarError(
            "effort-driver-loop effort_slugs: expected one or more active effort slugs"
        )
    raw_state_root = data.get("state_root")
    if raw_state_root is not None and (
        not isinstance(raw_state_root, str) or not raw_state_root
    ):
        raise RegistrarError("effort-driver-loop state_root: expected a non-empty string")
    state_root = _resolve_state_root(raw_state_root, cwd=cwd)

    pool = data.get("pool")
    if not isinstance(pool, Mapping):
        raise RegistrarError("effort-driver-loop pool: expected a mapping")
    pool = dict(pool)
    try:
        placement = _load_filters(data.get("filters"))
        pool_filters = _load_filters(pool.pop("filters", None))
    except RegistrarError as exc:
        raise RegistrarError(f"effort-driver-loop filters: {exc}") from exc
    unsupported = sorted(
        (set(placement.permit) | set(placement.reject)) - {"machine"}
    )
    if unsupported:
        raise RegistrarError(
            "effort-driver-loop top-level filters support only machine "
            f"placement: {unsupported}"
        )
    if pool.get("max_active_processes", pool.get("concurrency", 1)) != 1:
        raise RegistrarError("effort-driver-loop pool concurrency must be 1")
    body = pool.get("body") or {}
    if not isinstance(body, Mapping):
        raise RegistrarError("effort-driver-loop pool.body: expected a mapping")
    if body.get("type", "headless") != "headless":
        raise RegistrarError("effort-driver-loop pool.body.type must be 'headless'")
    fleet = pool.get("fleet") or {}
    if not isinstance(fleet, Mapping):
        raise RegistrarError("effort-driver-loop pool.fleet: expected a mapping")
    if fleet.get("pool") and fleet.get("headless") is not True:
        raise RegistrarError(
            "effort-driver-loop fleet workers must set headless: true"
        )
    reserved_pool = {
        "name",
        "labels",
        "repos",
        "kind",
        "spec",
        "owner",
        "description",
    }
    if present := sorted(reserved_pool & set(pool)):
        raise RegistrarError(
            f"effort-driver-loop pool fields {present} are derived"
        )

    owner = data.get("owner")
    description = data.get("description")
    guidance = data.get("worker_guidance")
    worker_identity = data.get("worker_identity")
    allow_self_config = data.get("allow_self_config_changes", False)
    for key, value in (
        ("owner", owner),
        ("description", description),
        ("worker_guidance", guidance),
        ("worker_identity", worker_identity),
    ):
        if value is not None and not isinstance(value, str):
            raise RegistrarError(f"effort-driver-loop {key}: expected a string")
    if worker_identity and guidance:
        raise RegistrarError(
            "effort-driver-loop: worker_identity and worker_guidance are "
            "mutually exclusive -- select a named identity or inline "
            "guidance, not both"
        )
    identity_name = ""
    if worker_identity:
        identity = load_worker_identity(worker_identity, cwd=Path(cwd) if cwd else None)
        guidance = identity.rules
        identity_name = identity.name
    if not isinstance(allow_self_config, bool):
        raise RegistrarError(
            "effort-driver-loop allow_self_config_changes: expected true/false"
        )
    task_contract = validate_task_contract(data)

    def filters_payload(filters: Filters) -> dict[str, dict[str, list[str]]]:
        return {
            side: {
                dimension: sorted(values)
                for dimension, values in sorted(getattr(filters, side).items())
            }
            for side in ("permit", "reject")
            if getattr(filters, side)
        }

    worker_permit = dict(placement.permit)
    for dimension, values in pool_filters.permit.items():
        worker_permit[dimension] = (
            worker_permit[dimension] & values
            if dimension in worker_permit
            else values
        )
    worker_reject = dict(placement.reject)
    for dimension, values in pool_filters.reject.items():
        worker_reject[dimension] = worker_reject.get(
            dimension, frozenset()
        ) | values
    worker_filters = Filters(worker_permit, worker_reject)
    for dimension, permitted in worker_permit.items():
        if not permitted or permitted <= worker_reject.get(
            dimension, frozenset()
        ):
            raise RegistrarError(
                "effort-driver-loop filters leave no eligible "
                f"{dimension!r} value"
            )

    return {
        "name": name,
        "kind": "effort-driver-loop",
        "repo": repo,
        "source": source,
        "cadence_seconds": cadence,
        "tick_interval_seconds": tick_interval,
        "effort_slugs": list(effort_slugs),
        "state_root": state_root,
        "task_label": task_label,
        "pool": pool,
        "filters": filters_payload(placement),
        "worker_filters": filters_payload(worker_filters),
        "owner": owner,
        "description": description,
        "worker_guidance": guidance or "",
        "worker_identity": identity_name,
        "allow_self_config_changes": allow_self_config,
        "require_verification": require_verification,
        "evaluator_ref": evaluator_ref,
        "task_contract": task_contract,
    }


def expand_effort_driver_loop(
    data: Mapping[str, Any],
    *,
    repo_root: str | Path | None = None,
) -> tuple[ProfileDeclaration, ...]:
    """Expand into one emitter + one worker lane."""
    config = validate_config(data, cwd=repo_root)
    spec: dict[str, Any] = {
        "id": f"{config['name']}-source",
        "interval_seconds": config["tick_interval_seconds"],
        "lease_scope": f"effort-driver-loop:{config['name']}",
        "effort_driver_loop": dict(data),
    }
    if repo_root is not None:
        spec["cwd"] = str(Path(repo_root).resolve())
    common = {
        "owner": config["owner"],
        "description": config["description"],
    }
    source = load_declaration(
        {
            "name": f"{config['name']}-source",
            "kind": "emitter",
            "spec": spec,
            "filters": config["filters"],
            **common,
        }
    )
    pool = load_declaration(
        {
            "name": f"{config['name']}-workers",
            "labels": [config["task_label"]],
            "repos": config["repo"],
            **config["pool"],
            "concurrency": 1,
            "filters": config["worker_filters"],
            **common,
        }
    )
    return source, pool


def occurrence_epoch(now: float, cadence_seconds: float) -> int:
    return int(now // cadence_seconds * cadence_seconds)


def _active_efforts_root(config: Mapping[str, Any]) -> Path:
    return Path(str(config["state_root"])) / "efforts" / "active"


def _title_from_readme(text: str, slug: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return slug


def _status_from_readme(text: str) -> str:
    match = _STATUS_RE.search(text)
    return match.group(1).strip() if match else "unknown"


def _coordination_refs_from_readme(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_EFFORT_REF_RE.findall(text)))


def _discover_active_efforts(config: Mapping[str, Any]) -> tuple[list[ActiveEffort], list[str]]:
    """Sweep ``<state_root>/efforts/active/`` for effort READMEs.

    The glob is recursive (``**/README.md``, any depth under ``active/``),
    not flat (``*/README.md``, exactly one level) -- a consumer's own
    organizing convention between ``active/`` and the effort's own folder
    (for example nesting one extra level by originating repo,
    ``active/<repo>/<slug>/README.md``, when one state root serves several
    repos) is this engine's business to tolerate, not something every
    adopter must flatten away just to be discoverable. The effort's own
    identity is always its immediate parent folder name (``slug``),
    regardless of how deep that folder sits -- never a path segment this
    engine would have to know the meaning of.

    A slug is ambiguous, not merely a coincidence, if it is found at more
    than one path: explicit ``effort_slugs`` selection and ``origin_ref``
    (``f"{name}/effort/{slug}/occurrence/{occurrence}"``) both key on the
    bare slug, so two distinct effort folders sharing one name could not
    be told apart by a selection list, and would generate colliding
    origin_refs for genuinely different artifacts (``exclusive_key``/
    ``dedup_key`` key on the full ``readme_relative`` path instead, so
    they stay distinct even then -- it is selection and origin_ref
    tracking that actually break). That is always a real authoring
    conflict needing a rename, so it is raised here rather than resolved
    by picking one match arbitrarily (`sorted()`'s own tie-break would be
    stable but still semantically wrong).
    """
    active_root = _active_efforts_root(config)
    if not active_root.is_dir():
        raise RuntimeError(
            f"effort-driver-loop active efforts directory does not exist: {active_root}"
        )
    selected = tuple(str(slug) for slug in config.get("effort_slugs", ()))
    selected_set = set(selected)
    by_slug: dict[str, list[Path]] = {}
    for readme in sorted(active_root.glob("**/README.md")):
        if readme.parent == active_root:
            # A loose README.md directly under active/, with no effort
            # folder of its own -- not an effort (every effort has its own
            # named folder; this guards the degenerate `**` zero-levels
            # match from being misread as an effort named "active").
            continue
        slug = readme.parent.name
        if selected_set and slug not in selected_set:
            continue
        by_slug.setdefault(slug, []).append(readme)
    ambiguous = {slug: paths for slug, paths in by_slug.items() if len(paths) > 1}
    if ambiguous:
        detail = "; ".join(
            f"{slug!r} at {[str(p) for p in paths]}" for slug, paths in sorted(ambiguous.items())
        )
        raise RuntimeError(
            f"effort-driver-loop found more than one active effort folder sharing the same "
            f"slug under {active_root} -- rename one so each slug is unique: {detail}"
        )
    efforts: list[ActiveEffort] = []
    found_slugs: set[str] = set()
    for slug, (readme,) in sorted(by_slug.items()):
        text = readme.read_text(encoding="utf-8")
        found_slugs.add(slug)
        efforts.append(
            ActiveEffort(
                slug=slug,
                title=_title_from_readme(text, slug),
                readme_relative=readme.relative_to(
                    Path(config["state_root"])
                ).as_posix(),
                state_root=str(config["state_root"]),
                status=_status_from_readme(text),
                coordination_refs=_coordination_refs_from_readme(text),
            )
        )
    missing = [slug for slug in selected if slug not in found_slugs]
    return efforts, missing


def _task_contract_fields(config: Mapping[str, Any], effort: ActiveEffort) -> dict[str, str]:
    self_config = (
        "You may update this loop's active declaration only when that change is "
        "necessary to complete the accepted effort."
        if config["allow_self_config_changes"]
        else "Do not change this loop's active declaration or configuration."
    )
    refs = ", ".join(effort.coordination_refs) if effort.coordination_refs else "none noted"
    return build_task_contract(
        config,
        effort_title=effort.title,
        effort_slug=effort.slug,
        effort_readme=effort.readme_relative,
        effort_status=effort.status,
        coordination_refs=refs,
        self_config_clause=self_config,
        worker_guidance=config["worker_guidance"].strip(),
    )


def _exclusive_key(config: Mapping[str, Any], effort: ActiveEffort) -> str:
    return f"effort-driver-loop:{config['name']}:{effort.readme_relative}"


def _origin_ref(config: Mapping[str, Any], effort: ActiveEffort, occurrence: int) -> str:
    return f"{config['name']}/effort/{effort.slug}/occurrence/{occurrence}"


def plan(client: Any, config: Mapping[str, Any], *, now: float) -> dict[str, Any]:
    occurrence = occurrence_epoch(now, config["cadence_seconds"])
    efforts, missing = _discover_active_efforts(config)
    eligible: list[ActiveEffort] = []
    suppressed: list[str] = []
    for effort in efforts:
        exclusive_key = _exclusive_key(config, effort)
        origin_ref = _origin_ref(config, effort, occurrence)
        active = client.list(
            repo=config["repo"],
            source=config["source"],
            exclusive_key=exclusive_key,
            status=_ACTIVE_STATUSES,
            limit=1,
        )
        same_occurrence = client.list(
            repo=config["repo"],
            source=config["source"],
            origin_ref=origin_ref,
            limit=1,
        )
        if active or same_occurrence:
            suppressed.append(effort.slug)
            continue
        eligible.append(effort)
    return {
        "occurrence": occurrence,
        "efforts": efforts,
        "eligible": eligible,
        "suppressed_efforts": suppressed,
        "missing_effort_slugs": missing,
    }


def run_tick(
    client: Any,
    config: Mapping[str, Any],
    *,
    clock: Callable[[], float] = time.time,
    dry_run: bool = False,
    cwd: str | Path | None = None,
) -> dict[str, Any]:
    config = validate_config(config, cwd=cwd)
    now = clock()
    discovered = plan(client, config, now=now)
    if dry_run or discovered["missing_effort_slugs"]:
        return {
            "occurrence": discovered["occurrence"],
            "eligible": [effort.slug for effort in discovered["eligible"]],
            "suppressed_efforts": list(discovered["suppressed_efforts"]),
            "missing_effort_slugs": list(discovered["missing_effort_slugs"]),
            "created": [],
        }
    created = []
    for effort in discovered["eligible"]:
        contract = _task_contract_fields(config, effort)
        payload = {
            "effort_driver_loop": {
                "effort_slug": effort.slug,
                "effort_title": effort.title,
                "effort_readme": effort.readme_relative,
                "effort_status": effort.status,
                "coordination_refs": list(effort.coordination_refs),
            }
        }
        created.append(
            client.create(
                contract["title"],
                repo=config["repo"],
                prompt=contract["prompt"],
                goal=contract["goal"],
                done_criteria=contract["done_criteria"],
                labels=[config["task_label"]],
                payload_inline=json.dumps(
                    payload,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                target_repo=config["repo"],
                source=config["source"],
                origin_ref=_origin_ref(config, effort, discovered["occurrence"]),
                dedup_key=_exclusive_key(config, effort),
                exclusive_key=_exclusive_key(config, effort),
                require_verification=bool(config.get("require_verification", False)),
                evaluator_ref=config.get("evaluator_ref"),
            )
        )
    return {
        "occurrence": discovered["occurrence"],
        "eligible": [effort.slug for effort in discovered["eligible"]],
        "suppressed_efforts": list(discovered["suppressed_efforts"]),
        "missing_effort_slugs": list(discovered["missing_effort_slugs"]),
        "created": created,
    }
