"""Declared registered-provider scopes and source-attributed aggregate snapshots."""
from __future__ import annotations


def selects_machine(pivot):
    return any("{machine}" in argument for argument in getattr(pivot, "list_cmd", ()))


def read_scope(pivot, machine):
    return "" if getattr(pivot, "account_scoped", False) or not selects_machine(pivot) else machine


def request_scopes(engine, pivot):
    if read_scope(pivot, None) == "":
        return ("",)
    if not engine.is_all():
        tab = engine._current_tab()
        if tab.get("source_kind") not in (None, "machine-ssh"):
            return ()
        machine = tab.get("machine") or engine._pivot_machine_id()
        return () if machine is None else (machine,)
    mapping = None
    scopes = []
    seen = set()
    for index, (_label, machine, _environment, _ready) in enumerate(engine.machines):
        if not index or not machine:
            continue
        tab = engine.source_tabs[index] if engine.source_tabs else {}
        if tab.get("source_kind") not in (None, "machine-ssh"):
            continue
        canonical = tab.get("machine")
        if not canonical:
            if mapping is None:
                mapping = engine._machine_key_map()
            canonical = mapping.get(machine, machine)
        if canonical.casefold() not in seen:
            seen.add(canonical.casefold())
            scopes.append(canonical)
    return tuple(scopes)


def _read_state(states):
    states = tuple(states)
    if not states:
        return "error"
    if any(state in {"idle", "loading"} for state in states):
        return "loading"
    return "error" if "error" in states else "ready"


def aggregate(runtime, pivot, scopes, *, annotate=True):
    """Keep failed and pending scope evidence alongside any available rows."""
    snapshots, rows, failures, pending = [], [], [], []
    summaries = {}
    for scope in scopes:
        state, entries, error = runtime.get(scope)
        snapshots.append({"scope": scope, "state": state, "error": error})
        if state == "error":
            failures.append(f"{scope or 'provider'}: {error or 'read failed'}")
        elif state in {"idle", "loading"}:
            pending.append(scope or "provider")
        for entry in entries:
            if any(str(key).startswith("_manager_") for key in entry):
                entry = {key: value for key, value in entry.items()
                         if not str(key).startswith("_manager_")}
            rows.append({
                **entry, "_manager_scope": scope,
                "_manager_row_key": (scope, str(entry.get(pivot.id_field, ""))),
                "_manager_scope_state": state,
            })
        summaries[scope] = runtime.get_summary(scope) if hasattr(runtime, "get_summary") else {}
    summary = dict(summaries[scopes[0]]) if len(scopes) == 1 else {}
    if annotate or len(scopes) > 1:
        summary["_manager_scopes"] = snapshots
    if failures or pending:
        status = []
        if pending:
            status.append(f"{len(pending)} scope(s) loading")
        if failures:
            status.append("read failures: " + "; ".join(failures))
        if len(scopes) > 1 or rows:
            summary.update(
                banner_text=f"{len(rows)} available/cached row(s); " + " · ".join(status),
                banner_level="warn" if failures else "info",
            )
    if not scopes:
        return "error", [], "No declared machine scopes are available.", summary
    state = _read_state(snapshot["state"] for snapshot in snapshots)
    return state, rows, "; ".join(failures), summary


def snapshot(engine, pivot):
    scopes = request_scopes(engine, pivot)
    if not scopes:
        return "error", [], "No declared machine scopes are available.", {}
    runtime = engine._pivot_runtime(pivot)
    if hasattr(runtime, "ensure_many"):
        runtime.ensure_many(scopes)
    else:
        for scope in scopes:
            runtime.ensure(scope)
    return aggregate(
        runtime, pivot, scopes,
        annotate=engine.is_all() and selects_machine(pivot) and not pivot.account_scoped,
    )


def summary(engine, pivot):
    scopes = request_scopes(engine, pivot)
    runtime = engine._pivot_runtime(pivot)
    if len(scopes) == 1:
        getter = getattr(runtime, "get_summary", None)
        return getter(scopes[0]) if getter is not None else {}
    return aggregate(runtime, pivot, scopes)[3]


def refresh(engine, pivot):
    runtime = engine._pivot_runtime(pivot)
    reload_scope = getattr(runtime, "refresh_read", None) or runtime.repoll
    scopes = request_scopes(engine, pivot)
    engine.debug = f"{pivot.label}: refresh requested"
    engine._registered_refresh_note = (engine._pivot_descriptor_key(), scopes, engine.debug)
    for scope in scopes:
        reload_scope(scope)


def refresh_footer(engine, status):
    note = getattr(engine, "_registered_refresh_note", None)
    if note is None or status != note[2]:
        return status
    pivot = engine._reg_pivot()
    if (pivot is None or engine._pivot_descriptor_key() != note[0]
            or request_scopes(engine, pivot) != note[1]):
        return "ready"
    runtime = engine._pivot_runtime(pivot)
    pending = getattr(runtime, "read_pending", None)
    state = _read_state(
        "loading" if pending is not None and pending(scope) else runtime.get(scope)[0]
        for scope in note[1]
    )
    return f"{pivot.label}: " + {
        "loading": "reading declared scope(s)",
        "error": "read failed or incomplete; see source errors",
        "ready": "read settled",
    }.get(state, state)
