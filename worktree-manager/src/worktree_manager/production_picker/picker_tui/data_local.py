#!/usr/bin/env python3
"""Local provider-backed data source for the Worktree Picker TUI.

Exposes the same surface the engine's prototype sources did
(``LOCAL`` / ``LOCAL_LABEL`` / ``machines()`` / ``load()`` / ``bucket`` /
``for_machine`` / ``for_source``), but backed by the attributable
``agent-worktrees`` JSON CLI on *this* machine. Remote machines use the same
provider contract through the SSH source.
"""
from __future__ import annotations

import datetime as _dt
import socket

from machine_transport import (
    AmbiguousMachineError,
    is_local_machine,
    local_execution_platform,
    resolve_machine_selection,
    is_observed_local_name,
)

from worktree_manager import engine_client

from .. import context, engine_group_c
from .. import project_config as cfg
from . import derive, roster, source_identity

bucket = derive.bucket
for_machine = derive.for_machine
for_source = derive.for_source
host_cols = roster.host_cols
target_envs = roster.target_envs

_ENV_LABEL = {"windows": "Win", "wsl": "WSL", "linux": "Linux"}
_GROUP_C_FIELDS = (
    "pr",
    "prs",
    "pr_count",
    "session_bound_live",
    "session_lock_live",
    "session_lock_stale",
    "stale_lock_pids",
    "mux_session",
    "mux_clients",
    "mux_attached",
)
_GROUP_C_MUX_FIELDS = ("mux_session", "mux_clients", "mux_attached")


def _local_identity() -> tuple[str, str]:
    host = socket.gethostname().split(".")[0]
    plat = local_execution_platform()
    return host, _ENV_LABEL.get(plat, plat.title())


LOCAL = _local_identity()
LOCAL_LABEL = f"{LOCAL[0]} · {LOCAL[1].lower()}"


def is_local(machine: str, env: str) -> bool:
    """Canonicalized "is ``(machine, env)`` this picker's own host?" check.

    A plain ``(machine, env) == LOCAL`` tuple comparison only matches when
    *machine* is spelled exactly like this host's raw OS hostname (``LOCAL[0]``,
    from ``socket.gethostname()``). A worktree record, an embodied-task
    payload, or any other producer may instead carry the canonical
    ``machines.yaml`` key/alias for this very machine (set via the
    hostname-field decoupling) -- a spelling mismatch then wrongly concludes
    "remote" and dispatches an unnecessary (and self-looping, over
    SSH/devtunnel) network hop to reach a host that is actually this one.
    ``data_ssh._build_sources`` already canonicalizes through the same
    registry for exactly this reason; this is the equivalent check for a
    single ``(machine, env)`` pair rather than the whole roster.

    The ``env``-gate and hostname tuple fast path stay Picker-specific.
    Every readable roster uses the shared platform-aware lookup through
    ``roster``, including unscoped native/guest pairs. Registry outages retain
    the shared ``is_local_machine`` fallback with the actual guest context.
    An empty roster uses that same fallback for case-insensitive observed
    native hostnames, not just the exact-case hostname tuple fast path.
    An unregistered configured native name also retains its self fallback when
    the entire readable roster is legacy/unscoped; guest and scoped targets do not.
    """
    matches_raw_local = (machine, env) == LOCAL
    if not machine or env != LOCAL[1]:
        return False
    try:
        config = cfg.load_config()
    except (RuntimeError, ValueError) as exc:
        if matches_raw_local:
            try:
                cfg.project_name()
            except RuntimeError:
                return True
        raise RuntimeError("Locality is not ready; refresh configuration before launching") from exc
    except Exception:
        return matches_raw_local
    revision = cfg.ConfigCacheSession(ttl=60).revision
    registry_error = None
    try:
        entries = cfg.load_machines_yaml(config.default_repo.anchor)
    except AmbiguousMachineError:
        return False
    except RuntimeError as exc:
        raise RuntimeError("Locality is not ready; refresh the machine registry before launching") from exc
    except (FileNotFoundError, ValueError, KeyError) as exc:
        entries = {}
        registry_error = exc
    if revision is not None and cfg.ConfigCacheSession(ttl=60).revision is not revision:
        raise RuntimeError("Locality is not ready; configuration changed")
    if entries:
        actual = local_execution_platform()
        try:
            target = resolve_machine_selection(
                entries, machine, actual,
            ).entry
            local = roster.local_entry(
                entries, config.machine, LOCAL[0], actual,
            )
        except ValueError:
            return False
        if (
            target is None and actual in ("windows", "linux")
            and machine.casefold() == (config.machine or "").casefold()
            and not any(entry.execution_platform for entry in entries.values())
        ):
            return True
        if target is None and local is None:
            return matches_raw_local
        if target is None and local is not None:
            return is_observed_local_name(
                machine, config_machine=config.machine, entries=entries,
                environment=actual, real_hostname=LOCAL[0],
            )
        return target is not None and target is local
    if matches_raw_local:
        return True
    result = is_local_machine(
        machine,
        config_machine=config.machine,
        load_entries=lambda: entries,
        # Explicitly the SAME short (domain-suffix-stripped) hostname
        # ``LOCAL`` itself uses -- the shared helper's own default
        # (``socket.gethostname()``, unstripped) would otherwise disagree
        # with this picker's roster construction (``data_ssh._build_sources``
        # also keys off the short hostname) on a domain-qualified host,
        # e.g. ``host.example.test`` vs. a registry entry's short
        # ``hostname: host``.
        real_hostname=LOCAL[0],
        guest=local_execution_platform() == "wsl",
    )
    if registry_error is not None and not result:
        raise RuntimeError("Locality is not ready; the machine registry is unavailable") from registry_error
    return result


def _project_repo() -> tuple[str, str]:
    """``(repo name, default branch)`` for the active project's default repo."""
    try:
        config = cfg.load_config()
        return config.repo_name, config.default_repo.default_branch
    except Exception:
        return "", ""


_REPO_BRANCH: tuple[str, str] | None = None


def __getattr__(name: str):
    global _REPO_BRANCH
    if name in ("REPO", "BRANCH"):
        if _REPO_BRANCH is None:
            _REPO_BRANCH = _project_repo()
        return _REPO_BRANCH[0] if name == "REPO" else _REPO_BRANCH[1]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def machines():
    """Machine-tab descriptors. Slice 1: the local machine only."""
    m, e = LOCAL
    return [(f"{m} {e}", m, e, True)]


def load_profile_column(machine, env):
    """Read a host's terminal-profile column (local in-process / remote SSH)."""
    from . import profiles_io

    return profiles_io.load_column(machine, env)


def apply_profile_column(machine, env, sels, *, mirror=True):
    """Persist a host's terminal-profile column. Returns ``(ok, detail)``."""
    from . import profiles_io

    return profiles_io.apply_column(machine, env, sels, mirror=mirror)


def reconcile_local_batch(*, worktree_ids: list[str] | None = None, runner=None):
    """Best-effort Group C reconcile batch for the current project."""
    try:
        return engine_group_c.picker_reconcile_local(
            context.project(),
            worktree_ids=worktree_ids,
            runner=runner,
        )
    except (
        engine_client.EngineError,
        engine_client.EngineFeatureUnavailable,
        ValueError,
    ):
        return None


def reconcile_prs() -> int:
    """Compatibility wrapper over the Group C batch summary."""
    batch = reconcile_local_batch()
    if batch is None:
        return 0
    return int(batch.summary.get("pr_terminal_count") or 0)


def reconcile_bound_live() -> int:
    """Compatibility wrapper over the Group C batch summary."""
    batch = reconcile_local_batch()
    if batch is None:
        return 0
    return int(batch.summary.get("bound_visible_change_count") or 0)


def _merge_reconcile_row(
    raw: dict,
    reconcile_row: dict | None,
    *,
    preserve_mux: bool = False,
) -> None:
    if not isinstance(reconcile_row, dict):
        return
    for field in _GROUP_C_FIELDS:
        if field in reconcile_row:
            raw[field] = reconcile_row[field]
        elif preserve_mux and field in _GROUP_C_MUX_FIELDS:
            continue
        else:
            raw.pop(field, None)


def _overlay_reconcile_rows(
    rows: list[dict],
    *,
    batch=None,
    worktree_ids: list[str] | None = None,
    runner=None,
) -> list[dict]:
    if batch is None:
        batch = reconcile_local_batch(worktree_ids=worktree_ids, runner=runner)
    if batch is None:
        return rows
    preserve_mux = not bool(batch.summary.get("mux_scan_ok", True))
    by_id = {
        str(row.get("id") or ""): row
        for row in batch.rows
        if isinstance(row, dict) and row.get("id")
    }
    for raw in rows:
        _merge_reconcile_row(
            raw,
            by_id.get(str(raw.get("id") or "")),
            preserve_mux=preserve_mux,
        )
    return rows


def _overlay_cached_state(raw: dict, rec) -> None:
    """Overlay cached session-render state onto a cache-only row.

    Cache-first first paint still comes from the provider's cache-only list
    payload. Once the Group C batch lands, its ``session_*`` / ``mux_*`` fields
    layer onto later authoritative loads via :func:`_merge_reconcile_row`;
    cache-only paint keeps trusting whatever the cached payload already had and
    otherwise degrades to the same "unknown until refined" shape as before.
    """
    lock_live = raw.get("session_lock_live") is True
    stale_pids = (
        list(raw.get("stale_lock_pids") or [])
        if raw.get("session_lock_stale")
        else []
    )
    live = lock_live or (raw.get("session_bound_live") is True)

    if rec.session_turns is not None:
        raw["turn_count"] = rec.session_turns
    if rec.git_state:
        raw["state"] = rec.git_state
    if rec.session_summary and not (raw.get("title") and raw["title"] != "null"):
        raw["title"] = rec.session_summary

    if live:
        if lock_live:
            raw["session_lock_live"] = True
        raw["state"] = "active"
    elif stale_pids:
        raw["session_lock_stale"] = True
        raw["stale_lock_pids"] = stale_pids
    elif rec.session_turns is None and not rec.git_state:
        raw["state"] = "unknown"


def load(
    machine: str | None = None,
    env: str | None = None,
    *,
    classify: bool = True,
    source_kind=source_identity.MACHINE_SSH_KIND,
    source_id=None,
    source_label=None,
    runner=None,
):
    """Normalized records for this machine's worktrees (tracking + classify).

    *machine*/*env* default to this host's identity (``LOCAL``). The SSH source
    overrides them so the local machine's rows carry its ``machines.yaml``
    display name and env label, matching the multi-machine tab descriptors.

    Both refresh modes read the same resident publication through the selected
    engine on a background loader thread. Neither reconciles or overlays a
    competing caller-side answer.
    """
    derive.NOW = _dt.datetime.now()
    machine = machine if machine is not None else LOCAL[0]
    env = env if env is not None else LOCAL[1]
    norm_source = {
        "source_kind": source_kind,
        "source_id": source_id,
        "source_label": source_label,
    }
    rows = list(
        engine_client.list_worktree_rows(
            context.project(),
            classify=classify,
            mux_details=True,
            cache_only=not classify,
            runner=runner,
        )
    )
    return [derive.norm(row, machine, env, **norm_source) for row in rows]


def orphans(*, runner=None) -> list[dict]:
    """This machine's durable claims-orphanage (worktree-claims-transitive-
    finalization Phase 4 item 2) -- see ``engine_client.orphaned_obligations``
    for why this is deliberately local-only, never fleet-aggregated."""
    return engine_client.orphaned_obligations(context.project(), runner=runner)


def _stamp_from_raw(rec, raw: dict, reconcile_row: dict | None) -> None:
    """Compatibility helper: merge Group C reconcile fields onto ``raw``."""
    _merge_reconcile_row(raw, reconcile_row)


def refresh_one(worktree_id: str, machine: str | None = None,
                env: str | None = None, *, runner=None):
    """Read ONE current slot -- the picker's per-row Refresh."""
    derive.NOW = _dt.datetime.now()
    machine = machine if machine is not None else LOCAL[0]
    env = env if env is not None else LOCAL[1]
    rows = list(
        engine_client.list_worktree_rows(
            context.project(),
            classify=True,
            mux_details=True,
            fresh=True,
            worktree_id=worktree_id,
            refresh=True,
            runner=runner,
        )
    )
    if not rows:
        return None
    return derive.norm(rows[0], machine, env)
