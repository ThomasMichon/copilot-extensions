"""Read-only source failure evidence, distinct from resource or host liveness."""
from __future__ import annotations


def failed_worktree_reads(engine):
    loader = getattr(engine, "loader", None)
    if not getattr(engine, "live", False) or loader is None:
        return []
    notices = []
    for index, (label, machine, environment, _ready) in enumerate(engine.machines):
        if not index or (not engine.is_all() and index != engine.machine_idx):
            continue
        state = engine.machine_state(index)
        if state not in {"failed", "ready"}:
            continue
        tab = engine.source_tabs[index] if engine.source_tabs else {}
        identity = tab.get("source_id")
        if identity and hasattr(loader, "error_for_source"):
            reason = loader.error_for_source(identity)
        elif hasattr(loader, "error"):
            reason = loader.error(machine, environment)
        else:
            reason = None
        if state == "ready" and not reason:
            continue
        notices.append((
            tab.get("source_label") or label, identity,
            str(reason or "No current source snapshot was obtained.").strip()[:240],
        ))
    return notices
