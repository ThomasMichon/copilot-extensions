"""Single-install, multi-tenant orchestration.

agent-logger is one shared runtime that serves *N* tenants. A repository
**adopts** agent-logger by carrying a repo-targeted ``tenant`` block -- in a
dedicated ``.agent-logger.tenant.yaml`` (recommended, so an older agent-logger
never trips over it) or under a ``tenant:`` key in the shared
``.agent-logger.yaml``. That block declares the repo's **role**
(``source`` -- its in-scope sessions get synced -- and/or ``sink`` -- logs land
back in it) and its **scope** (which sessions it wants, per machine). This
module discovers the machine's adopted repos via the agent-worktrees
adopted-projects registry (the same registry agent-machines reads), resolves
each adopted repo's tenant block for *this* machine, and drives the daemon each
tenant + scope needs -- one runtime, many tenant daemons.

Layering of a tenant's resolved sync/chronicle config (lowest precedence first):

1. built-in :data:`~agent_logger.config.DEFAULTS`;
2. the tenant block's base ``sync`` / ``chronicle`` (committed, all machines);
3. the tenant block's ``machines.<machine>`` override (committed, per machine);
4. a machine-local per-tenant supplement
   ``<home>/tenants/<tenant>.yaml`` (uncommitted -- transport + secrets such as
   the notify webhook URL that must never live in a repo).

The committed tenant block declares *what and for whom* (role + scope), always
safe to review; the machine-local supplement holds *how* (transport, secrets).

À la carte: with no registry (agent-worktrees absent) discovery yields no
tenants and the caller falls back to the classic single-home config path, so
existing single-tenant installs are unaffected.
"""

from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent_logger.config import (
    DEFAULTS,
    REPO_CONFIG_FILENAMES,
    REPO_CONFIG_SCHEMA_VERSION,
    Config,
    _deep_merge,
    home_dir,
)
from agent_logger.segmenter.platform import detect_machine

#: Roles a tenant may play.
VALID_ROLES = ("source", "sink")

#: Machine-local per-tenant supplement dir under the shared home.
TENANT_SUPPLEMENT_DIR = "tenants"

#: Dedicated repo-targeted tenant-config filenames, searched *before* the shared
#: ``.agent-logger.yaml``. A dedicated file is the recommended carrier: an
#: agent-logger old enough to predate tenant support never reads these names, so
#: a repo can adopt a tenant without a checkout breaking that older install's
#: ordinary log-config read of ``.agent-logger.yaml``.
TENANT_CONFIG_FILENAMES = (
    ".agent-logger.tenant.yaml",
    ".agent-logger.tenant.yml",
    ".config/agent-logger.tenant.yaml",
    ".config/agent-logger.tenant.yml",
)


class TenantConfigError(ValueError):
    """Raised when a repo-targeted ``tenant`` block is malformed."""


# --------------------------------------------------------------------------- #
# adopted-projects registry (agent-worktrees) -- dependency-free, graceful     #
# --------------------------------------------------------------------------- #


def _aw_home(aw_home: Path | None = None) -> Path:
    return aw_home or (Path.home() / ".agent-worktrees")


def current_platform() -> str:
    """Registry path-key for this platform: ``windows`` | ``wsl`` | ``linux``."""
    if os.name == "nt":
        return "windows"
    release = platform.release().lower()
    if "microsoft" in release or "wsl" in release:
        return "wsl"
    return "linux"


def _read_yaml(path: Path) -> dict:
    """Read a YAML mapping, gracefully returning ``{}`` when missing/unreadable."""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_repo_path(name: str, entry: dict, srcroot: dict, plat: str) -> Path | None:
    """Resolve a repo checkout path from its ``repos.yaml`` entry (see
    agent-machines' ``discover.resolve_repo_path``). ``~`` shorthands are
    expanded so an unexpanded home never silently drops a repo."""
    if isinstance(entry, dict) and entry.get(plat):
        return Path(str(entry[plat])).expanduser()
    root = srcroot.get(plat)
    if root:
        return (Path(str(root)) / name).expanduser()
    return None


def adopted_repo_paths(aw_home: Path | None = None) -> list[tuple[str, Path]]:
    """Resolve ``(repo_name, checkout_path)`` for every adopted project on this
    machine's platform. Reads ``projects.yaml`` for the candidate set and
    ``repos.yaml`` only to resolve paths -- both upstream-owned facts. Absent
    registry -> empty list (à la carte independence)."""
    home = _aw_home(aw_home)
    projects = _read_yaml(home / "projects.yaml").get("projects") or {}
    registry = _read_yaml(home / "repos.yaml")
    repos = registry.get("repos") or {}
    srcroot = registry.get("srcroot") or {}
    if not isinstance(projects, dict) or not isinstance(repos, dict):
        return []
    if not isinstance(srcroot, dict):
        srcroot = {}
    plat = current_platform()
    out: dict[str, tuple[str, Path]] = {}
    for raw_name in projects:
        name = str(raw_name)
        # Case-insensitive match against repos.yaml for the canonical entry.
        canonical, entry = name, {}
        folded = name.casefold()
        for reg_name, reg_entry in repos.items():
            if str(reg_name).casefold() == folded and isinstance(reg_entry, dict):
                canonical, entry = str(reg_name), reg_entry
                break
        path = _resolve_repo_path(canonical, entry, srcroot, plat)
        if path is not None:
            out.setdefault(canonical.casefold(), (canonical, path))
    return list(out.values())


# --------------------------------------------------------------------------- #
# tenant block parsing + per-machine resolution                                #
# --------------------------------------------------------------------------- #


def find_tenant_config(repo_path: Path) -> Path | None:
    """Return the adopted repo's tenant-config file, if it declares a tenant.

    A dedicated ``.agent-logger.tenant.yaml`` is searched before the shared
    ``.agent-logger.yaml``. Only a file that actually carries a ``tenant``
    mapping is returned, so a log-only ``.agent-logger.yaml`` never shadows a
    real tenant declaration in a lower-priority file.
    """
    for name in (*TENANT_CONFIG_FILENAMES, *REPO_CONFIG_FILENAMES):
        candidate = repo_path / name
        if candidate.is_file() and isinstance(_read_yaml(candidate).get("tenant"), dict):
            return candidate
    return None


def _as_str_list(value: Any, location: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [s.strip() for s in value.split(",")]
    elif isinstance(value, (list, tuple)):
        items = [str(s).strip() for s in value]
    else:
        raise TenantConfigError(f"{location} must be a string or list of strings")
    return [s for s in items if s]


def parse_tenant_block(data: dict, *, source: str, default_id: str) -> dict | None:
    """Validate and normalize a repo's ``tenant`` block.

    Returns the normalized block, or ``None`` when the file carries no tenant
    block (a log-only repo config). Raises :class:`TenantConfigError` on a
    malformed block.

    **Forward compatibility (rolling updates).** The block is versioned by the
    file's ``schema_version`` (the repo-config schema; ``tenant`` is a v2
    feature). A block from a *newer* schema than this build supports is read
    **tolerantly** -- unknown tenant fields, unknown per-machine override keys,
    and roles this build cannot serve are **ignored/dropped** rather than fatal,
    so an older reader mid-upgrade degrades to the subset it understands instead
    of hard-failing. At or below this build's schema the block is validated
    **strictly** (unknown field / unknown role raise -- typo protection).
    """
    if not isinstance(data, dict):
        raise TenantConfigError(f"{source}: repo config must be a mapping")
    block = data.get("tenant")
    if block is None:
        return None
    if not isinstance(block, dict):
        raise TenantConfigError(f"{source}: tenant must be a mapping")

    schema_version = data.get("schema_version", REPO_CONFIG_SCHEMA_VERSION)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise TenantConfigError(
            f"{source}: schema_version must be an integer, got {schema_version!r}"
        )
    if schema_version < 1:
        raise TenantConfigError(
            f"{source}: schema_version must be >= 1, got {schema_version!r}"
        )
    future = schema_version > REPO_CONFIG_SCHEMA_VERSION

    allowed = {"id", "roles", "enabled", "sync", "chronicle", "machines"}
    unknown = set(block) - allowed
    if unknown and not future:
        raise TenantConfigError(
            f"{source}: unknown tenant field(s): {', '.join(sorted(unknown))}"
        )

    tenant_id = block.get("id", default_id)
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise TenantConfigError(f"{source}: tenant.id must be a non-empty string")
    tenant_id = tenant_id.strip()
    # tenant_id is interpolated into <home>/tenants/<id>.yaml and the per-tenant
    # chronicle db/manifest paths, so a repo-controlled value must be a bare
    # name -- never a path separator, dot-segment, or absolute path that could
    # escape the runtime home.
    if (
        tenant_id in {".", ".."}
        or any(sep in tenant_id for sep in ("/", "\\"))
        or os.path.isabs(tenant_id)
    ):
        raise TenantConfigError(
            f"{source}: tenant.id {tenant_id!r} must be a bare name "
            "(no '/', '\\', '.', '..', or absolute path)"
        )

    roles = block.get("roles", ["source"])
    roles_list = _as_str_list(roles, f"{source}: tenant.roles")
    if future:
        # Keep only roles this build can serve; a newer role is inert here, not
        # fatal (a newer machine's daemon serves it). May legitimately be empty.
        roles_list = [r for r in roles_list if r in VALID_ROLES]
    else:
        if not roles_list:
            raise TenantConfigError(
                f"{source}: tenant.roles must name at least one role"
            )
        for role in roles_list:
            if role not in VALID_ROLES:
                raise TenantConfigError(
                    f"{source}: tenant.roles has unknown role {role!r} "
                    f"(valid: {', '.join(VALID_ROLES)})"
                )

    enabled = block.get("enabled", True)
    if not isinstance(enabled, bool):
        raise TenantConfigError(f"{source}: tenant.enabled must be a boolean")

    for key in ("sync", "chronicle"):
        if key in block and not isinstance(block[key], dict):
            raise TenantConfigError(f"{source}: tenant.{key} must be a mapping")

    machines = block.get("machines", {})
    if not isinstance(machines, dict):
        raise TenantConfigError(f"{source}: tenant.machines must be a mapping")
    for mkey, mval in machines.items():
        if not isinstance(mval, dict):
            raise TenantConfigError(
                f"{source}: tenant.machines.{mkey} must be a mapping"
            )
        munknown = set(mval) - {"sync", "chronicle", "enabled", "roles"}
        if munknown and not future:
            raise TenantConfigError(
                f"{source}: tenant.machines.{mkey} has unknown field(s): "
                f"{', '.join(sorted(munknown))}"
            )
        # Validate override types at parse time so resolve_tenant cannot coerce
        # a truthy string (YAML ``enabled: "false"`` -> True) or raise on a
        # malformed ``roles`` outside discovery's TenantConfigError handling.
        if "enabled" in mval and not isinstance(mval["enabled"], bool):
            raise TenantConfigError(
                f"{source}: tenant.machines.{mkey}.enabled must be a boolean"
            )
        if "roles" in mval:
            mroles = _as_str_list(mval["roles"], f"{source}: tenant.machines.{mkey}.roles")
            if not future:
                for role in mroles:
                    if role not in VALID_ROLES:
                        raise TenantConfigError(
                            f"{source}: tenant.machines.{mkey}.roles has unknown "
                            f"role {role!r} (valid: {', '.join(VALID_ROLES)})"
                        )

    return {
        "id": tenant_id,
        "roles": roles_list,
        "enabled": enabled,
        "sync": dict(block.get("sync") or {}),
        "chronicle": dict(block.get("chronicle") or {}),
        "machines": machines,
        "future_schema": future,
    }


def machine_matches(machine: str, key: str) -> bool:
    """Whether a ``machines`` override key applies to *machine*.

    Exact match, or a variant suffix match so a ``book2`` key covers
    both ``book2`` and its ``book2-wsl`` WSL sibling.
    """
    m = (machine or "").casefold()
    k = (key or "").casefold()
    return bool(k) and (m == k or m.startswith(k + "-"))


@dataclass
class ResolvedTenant:
    """A tenant resolved for one machine: identity, roles, and a live Config."""

    tenant_id: str
    repo_name: str
    repo_path: Path
    roles: tuple[str, ...]
    enabled: bool
    config: Config
    config_path: Path
    advisories: tuple[str, ...] = ()

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def scope_summary(self) -> dict[str, Any]:
        cfg = self.config
        return {
            "repo_allowlist": cfg.sync_repo_allowlist,
            "repo_denylist": cfg.sync_repo_denylist,
            "repo_allowlist_fail_closed": cfg.sync_repo_allowlist_fail_closed,
            "harness_repos": cfg.sync_harness_repos,
            "require_repo_opt_in": cfg.sync_require_repo_opt_in,
            "target": cfg.sync_target,
        }


def _machine_supplement(home: Path, tenant_id: str) -> dict:
    """Read the uncommitted machine-local per-tenant supplement (transport +
    secrets). Absent -> ``{}``."""
    path = home / TENANT_SUPPLEMENT_DIR / f"{tenant_id}.yaml"
    return _read_yaml(path)


def resolve_tenant(
    block: dict,
    *,
    repo_name: str,
    repo_path: Path,
    config_path: Path,
    machine: str,
    home: Path,
) -> ResolvedTenant:
    """Resolve a parsed tenant block into a :class:`ResolvedTenant` for *machine*."""
    tenant_id = block["id"]
    roles = list(block["roles"])
    enabled = block["enabled"]

    sync: dict = dict(block.get("sync") or {})
    chronicle: dict = dict(block.get("chronicle") or {})

    advisories: list[str] = []
    if block.get("future_schema"):
        advisories.append(
            "config declares a newer schema_version than this build supports; "
            "unrecognized fields/roles were ignored (upgrade agent-logger)"
        )

    # (3) per-machine committed override (first matching key wins).
    for key, override in block.get("machines", {}).items():
        if not machine_matches(machine, key):
            continue
        if "enabled" in override:
            enabled = bool(override["enabled"])
        if "roles" in override:
            roles = _as_str_list(override["roles"], f"tenant.machines.{key}.roles")
        if isinstance(override.get("sync"), dict):
            sync = _deep_merge(sync, override["sync"])
        if isinstance(override.get("chronicle"), dict):
            chronicle = _deep_merge(chronicle, override["chronicle"])
        break

    # A newer-schema config may carry roles this build cannot serve; keep only
    # the serviceable ones (post-override, so an override's roles are filtered
    # too). May legitimately become empty -> the tenant is inert on this build.
    if block.get("future_schema"):
        roles = [r for r in roles if r in VALID_ROLES]

    # (4) uncommitted machine-local supplement (transport + secrets).
    supplement = _machine_supplement(home, tenant_id)
    if isinstance(supplement.get("sync"), dict):
        sync = _deep_merge(sync, supplement["sync"])
    if isinstance(supplement.get("chronicle"), dict):
        chronicle = _deep_merge(chronicle, supplement["chronicle"])

    # Lock scope follows the SOURCE, not the tenant: every tenant syncing the
    # same ~/.copilot shares the default ``session-sync.lock`` so their
    # source-mutating steps (origin marking, cold-session compaction) and the
    # legacy ``session-sync`` never race. A tenant that overrides its source to
    # a distinct tree sets its own ``sync.lock_name`` in the supplement. (The
    # orchestrator also runs tenants sequentially -- see run_all.)

    # Namespace sink state per tenant so multiple sink tenants never collide.
    if "sink" in roles:
        chronicle.setdefault("db_path", str(home / f"chronicle-{tenant_id}.db"))
        chronicle.setdefault(
            "manifests_dir", str(home / f"chronicle-manifests-{tenant_id}")
        )

    data = _deep_merge(
        DEFAULTS,
        {"sync": sync, "chronicle": chronicle, "machine": {"name": machine}},
    )
    config = Config(data, home)

    if "source" in roles and not config.sync_repo_allowlist:
        if config.sync_repo_denylist:
            advisories.append("catch-all source scope (denylist, no allowlist)")
        else:
            advisories.append("unfiltered source scope (syncs every session)")

    return ResolvedTenant(
        tenant_id=tenant_id,
        repo_name=repo_name,
        repo_path=repo_path,
        roles=tuple(roles),
        enabled=enabled,
        config=config,
        config_path=config_path,
        advisories=tuple(advisories),
    )


def discover_tenants(
    *,
    machine: str | None = None,
    home: Path | None = None,
    aw_home: Path | None = None,
) -> list[ResolvedTenant]:
    """Discover and resolve every adopted-repo tenant for this machine.

    Only adopted repos that carry a ``tenant`` block become tenants; a facility
    repo without one (e.g. copilot-extensions) is merely a session *origin* that
    a tenant's scope may include, not a tenant itself.
    """
    resolved_home = home or home_dir()
    resolved_machine = machine or detect_machine()
    tenants: list[ResolvedTenant] = []
    seen_ids: set[str] = set()
    for repo_name, repo_path in adopted_repo_paths(aw_home):
        config_path = find_tenant_config(repo_path)
        if config_path is None:
            continue
        try:
            data = _read_yaml(config_path)
            block = parse_tenant_block(
                data, source=str(config_path), default_id=repo_name
            )
            if block is None:
                continue
            tenant = resolve_tenant(
                block,
                repo_name=repo_name,
                repo_path=repo_path,
                config_path=config_path,
                machine=resolved_machine,
                home=resolved_home,
            )
        except TenantConfigError:
            # A malformed tenant block (parse OR resolve) must not take down the
            # whole fleet's orchestration; skip it. doctor/list surfaces the
            # error separately.
            continue
        if tenant.tenant_id in seen_ids:
            # Two adopted repos declaring the same id would share one supplement,
            # lock, and sink-state namespace -- not independent tenants. Keep the
            # first and drop the collision (fail closed on ambiguous identity).
            continue
        seen_ids.add(tenant.tenant_id)
        tenants.append(tenant)
    return tenants


# --------------------------------------------------------------------------- #
# orchestration                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class TenantOutcome:
    tenant_id: str
    role: str
    status: str  # "ok" | "failed" | "skipped"
    detail: str = ""


@dataclass
class OrchestrationResult:
    machine: str
    outcomes: list[TenantOutcome] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "machine": self.machine,
            "tenants": sorted({o.tenant_id for o in self.outcomes}),
            "outcomes": [
                {
                    "tenant": o.tenant_id,
                    "role": o.role,
                    "status": o.status,
                    "detail": o.detail,
                }
                for o in self.outcomes
            ],
        }


def _run_source(tenant: ResolvedTenant, *, dry_run: bool, prune: bool) -> TenantOutcome:
    from agent_logger.sync.engine import run_sync

    try:
        code = run_sync(tenant.config, dry_run=dry_run, prune=prune)
    except Exception as exc:  # pragma: no cover - defensive isolation
        return TenantOutcome(tenant.tenant_id, "source", "failed", repr(exc))
    status = "ok" if code == 0 else "failed"
    return TenantOutcome(tenant.tenant_id, "source", status, f"exit={code}")


def _run_sink(tenant: ResolvedTenant, *, dry_run: bool) -> TenantOutcome:
    if dry_run:
        return TenantOutcome(tenant.tenant_id, "sink", "skipped", "dry-run")
    from agent_logger.chronicle.factory import build_chronicler

    try:
        chronicler = build_chronicler(tenant.config)
        result = chronicler.run_once()
    except Exception as exc:  # pragma: no cover - defensive isolation
        return TenantOutcome(tenant.tenant_id, "sink", "failed", repr(exc))
    return TenantOutcome(
        tenant.tenant_id, "sink", "ok", f"landed={result.landed}/{result.digests}"
    )


def _source_include(tenant: ResolvedTenant) -> tuple[str, set[str] | None]:
    """Return ``(source_path, included_session_ids)`` for a source tenant.

    ``None`` for the id set means "every session" (no allow/deny filter).
    """
    from agent_logger.sync.engine import _included_sessions
    from agent_logger.sync.origin import effective_harness

    cfg = tenant.config
    machine = cfg.machine_name or detect_machine()
    allowlist = cfg.sync_repo_allowlist
    denylist = cfg.sync_repo_denylist
    require_repo_opt_in = cfg.sync_require_repo_opt_in
    effective = effective_harness(allowlist, cfg.sync_harness_repos, denylist)
    include = _included_sessions(
        cfg.sync_source,
        allowlist,
        cfg.sync_repo_allowlist_fail_closed,
        effective,
        machine,
        denylist,
        require_repo_opt_in,
    )
    return str(cfg.sync_source), include


def source_conflicts(tenants: list[ResolvedTenant]) -> list[str]:
    """Report source tenants that would claim the same session.

    The agent-logger vision requires source claims to be **provably disjoint**
    (or resolution to fail closed): a session pushed by two tenants lands in two
    destinations -- exactly the cross-tenant leak the scope model exists to
    prevent. Only tenants sharing a source can overlap; a ``None`` (unfiltered)
    claim covers every session in its source.
    """
    by_source: dict[str, list[tuple[str, set[str] | None]]] = {}
    for t in tenants:
        if not (t.enabled and t.has_role("source")):
            continue
        src, include = _source_include(t)
        by_source.setdefault(src, []).append((t.tenant_id, include))

    conflicts: list[str] = []
    for src, claims in by_source.items():
        if len(claims) < 2:
            continue
        catchall = sorted(tid for tid, inc in claims if inc is None)
        if catchall:
            conflicts.append(
                f"{', '.join(catchall)} claim every session in {src}, "
                f"overlapping the other tenant(s) on that source"
            )
        explicit = [(tid, inc) for tid, inc in claims if inc is not None]
        for i in range(len(explicit)):
            for j in range(i + 1, len(explicit)):
                a_id, a = explicit[i]
                b_id, b = explicit[j]
                overlap = a & b
                if overlap:
                    sample = ", ".join(sorted(overlap)[:3])
                    conflicts.append(
                        f"{a_id} and {b_id} both claim {len(overlap)} session(s) "
                        f"in {src} (e.g. {sample})"
                    )
    return conflicts


def run_all(
    tenants: list[ResolvedTenant],
    *,
    roles: tuple[str, ...] = ("source",),
    dry_run: bool = False,
    prune: bool = False,
    machine: str | None = None,
) -> OrchestrationResult:
    """Drive each enabled tenant's daemon(s) for the requested *roles*.

    Tenants run **sequentially**; tenants sharing a source serialize on that
    source's lock (default ``session-sync.lock``), so their source-mutating
    steps never race each other or the legacy ``session-sync``. One tenant's
    failure never blocks another's.

    **Source preflight (fail closed).** Before any source push, the source plan
    is checked for overlapping claims. If two tenants would claim the same
    session, *no* source tenant is synced (a failed conflict outcome is
    reported) -- never double-push a session to two destinations. The sink role
    is unaffected by a source conflict.
    """
    result = OrchestrationResult(machine=machine or detect_machine())

    run_source = "source" in roles
    if run_source:
        conflicts = source_conflicts(tenants)
        if conflicts:
            for detail in conflicts:
                result.outcomes.append(
                    TenantOutcome("*", "source", "failed", f"source conflict: {detail}")
                )
            run_source = False  # fail closed: sync no source tenant

    for tenant in tenants:
        if not tenant.enabled:
            result.outcomes.append(
                TenantOutcome(tenant.tenant_id, "-", "skipped", "disabled")
            )
            continue
        if run_source and tenant.has_role("source"):
            result.outcomes.append(_run_source(tenant, dry_run=dry_run, prune=prune))
        if "sink" in roles and tenant.has_role("sink"):
            result.outcomes.append(_run_sink(tenant, dry_run=dry_run))
    return result
