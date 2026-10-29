"""List-surface loading, error and last-good evidence, never host liveness."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReadFeedback:
    label: str
    identity: str | None
    state: str
    reason: str = ""
    failure_kind: str = "content"
    cached: bool = False
    loading_frame: str = ""


def worktree_reload_pending(engine):
    return getattr(engine, "_setup_epoch", 0) > max(
        getattr(engine, "_setup_applied_epoch", 0),
        getattr(engine, "_setup_failed_epoch", 0),
    )


def worktree_setup_failed(engine):
    return bool(getattr(engine, "_setup_error", "")) and (
        getattr(engine, "_setup_failed_epoch", 0) == getattr(engine, "_setup_epoch", 0)
    )


def worktree_row_identity(row):
    return (
        row.get("source_id") or (row.get("machine"), row.get("env")),
        (row.get("source") or {}).get("instance_id") or None,
    )


def worktree_source_instance(engine, tab):
    status = getattr(getattr(engine, "loader", None), "read_status_for_source", None)
    if tab.get("source_id") and callable(status):
        return status(tab["source_id"]).get("instance_id") or tab.get("instance_id") or None
    return tab.get("instance_id") or None


def retained_worktree_scope(engine, tabs):
    if not getattr(engine, "_roster_ready", False) or len(engine.machines) <= 1:
        return None
    previous_key = engine._current_tab_key()
    return next((
        index for index, tab in enumerate(tabs)
        if (tab.get("source_id") or (
            (tab.get("machine"), tab.get("env")) if index else None
        )) == previous_key
    ), None)


def has_worktree_snapshot(engine, index, state):
    if not getattr(engine, "live", False) or state == "disabled":
        return False
    loader = getattr(engine, "loader", None)
    tab = engine.source_tabs[index] if engine.source_tabs else {}
    _, machine, environment, _ = engine.machines[index]
    identity = tab.get("source_id") or (machine, environment)
    key = (identity, worktree_source_instance(engine, tab))
    remembered = getattr(engine, "_loaded_worktree_sources", None)
    if remembered is None:
        remembered = engine._loaded_worktree_sources = set()
    rows = getattr(engine, "data", ())
    row_cache = getattr(engine, "_worktree_source_rows", None)
    if row_cache is None or row_cache[0] is not rows:
        row_cache = engine._worktree_source_rows = (rows, {
            worktree_row_identity(row) for row in rows
        })
    read_status = getattr(loader, "read_status_for_source", None)
    detail = read_status(identity) if callable(read_status) else {}
    complete = (
        detail.get("has_snapshot", False)
        and (detail.get("instance_id") or None) == key[1]
        if callable(read_status) else state == "ready"
    )
    if complete or key in row_cache[1]:
        remembered.add(key)
    return key in remembered


def worktree_read_feedback(engine):
    loader = getattr(engine, "loader", None)
    if not getattr(engine, "live", False):
        return []
    notices = []
    setup_failed = worktree_setup_failed(engine)
    for index, (label, machine, environment, _ready) in enumerate(getattr(engine, "machines", ())):
        if not index:
            continue
        state = engine.machine_state(index)
        cached = has_worktree_snapshot(engine, index, state)
        if not engine.is_all() and index != engine.machine_idx:
            continue
        if state not in {"failed", "ready", "loading"}:
            continue
        tab = engine.source_tabs[index] if engine.source_tabs else {}
        identity = tab.get("source_id")
        detail = {}
        if identity and callable(getattr(loader, "read_status_for_source", None)):
            detail = loader.read_status_for_source(identity)
            reason = detail.get("error")
        elif identity and hasattr(loader, "error_for_source"):
            reason = loader.error_for_source(identity)
        elif hasattr(loader, "error"):
            reason = loader.error(machine, environment)
        else:
            reason = None
        if setup_failed:
            state = "failed"
            reason = getattr(engine, "_setup_error", "Worktree sources could not be refreshed.")
        if state == "ready" and not reason:
            continue
        read_identity = (
            f"local execution (no SSH) / {identity or 'resident current-roster'}"
            if tab.get("local") else identity
        )
        notices.append(ReadFeedback(
            tab.get("source_label") or label, read_identity, state,
            "" if state == "loading" else
            str(reason or "No current source snapshot was obtained.").strip()[:240],
            detail.get("failure_kind", "content") if not setup_failed else "content",
            cached,
            engine.spin() if state == "loading" and not cached else "",
        ))
    if setup_failed and not notices:
        notices.append(ReadFeedback(
            "Worktrees", None, "failed", engine._setup_error, cached=bool(
                getattr(engine, "data", ()) or getattr(engine, "_loaded_worktree_sources", ())
            ),
        ))
    return notices


def failed_worktree_reads(engine):
    return [
        (notice.label, notice.identity, notice.reason)
        for notice in worktree_read_feedback(engine) if notice.state != "loading"
    ]
