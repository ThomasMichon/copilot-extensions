"""``agent-worktrees claims transitive`` -- Plan Phase 3 (discovery
ergonomics) of the ``worktree-claims-transitive-finalization`` tracking
effort: a cheap, read-only query answering "what does my whole subtree still
owe?" without manually walking each child worktree's own ``claims`` output.

Pure diagnostic convenience -- the real finalize-safety guarantee (the
bottom-up per-hop settlement proven end-to-end by
``test_transitive_finalize_integration.py``) does not depend on this query
existing; it only reports the SAME local ``worktree``-kind edges the
existing obligation gate already trusts, in one place instead of requiring
a manual child-by-child ``claims`` read.
"""

from __future__ import annotations

import argparse

from . import config as cfg, output, tracking


def _is_safe_path_component(value: str) -> bool:
    """True when ``value`` is safe to join as a single filesystem path
    segment -- no path separators, no ``..``/``.`` traversal components, no
    NUL. A parsed :class:`~agent_worktrees.tracking_claims.ClaimRef`'s
    ``project``/``worktree_id`` fields are **not** validated by
    ``parse_claim_ref`` itself (a three-or-more-component ref is simply
    accepted as qualified), so a corrupted or malicious claim ref must be
    rejected here before it is ever joined into a real path.
    """
    if not value or "\x00" in value:
        return False
    if "/" in value or "\\" in value:
        return False
    return value not in (".", "..")


def transitive_obligations(
    worktree_id: str,
    project: str,
    config: cfg.Config,
    *,
    _visited: set[tuple[str, str, str]] | None = None,
    _path: tuple[str, ...] = (),
) -> tuple[list[dict], list[dict]]:
    """Recursively collect every unsettled, non-``worktree``-kind resource
    claim anywhere in ``worktree_id``'s subtree (itself + every worktree it
    created, transitively via its ``worktree``-kind claims).

    Returns ``(obligations, unresolved)``. Each ``obligations`` entry carries
    ``path`` -- the worktree-id chain from the root (inclusive) to the
    record that actually holds the claim -- so a caller never has to
    re-derive "who holds this" by hand. ``unresolved`` entries are subtree
    edges this machine could not actually check: a cross-machine child (the
    same case ``finalize._settle_parent_obligation`` defers to the lease
    mirror / ``claims sweep``), a vanished/unreadable record, or a cyclic
    edge (never expected from normal creation, but the walk must still
    terminate rather than recurse forever if the ledger is ever hand-edited
    into one).
    """
    if _visited is None:
        _visited = set()
    key = (config.machine, project, worktree_id)
    here = _path + (worktree_id,)
    if key in _visited:
        return [], [{"path": here, "ref": "", "reason": "cycle detected"}]
    _visited.add(key)

    try:
        rec_path = cfg.project_dir(project) / "worktrees" / f"{worktree_id}.yaml"
        if not rec_path.exists():
            return [], [{"path": here, "ref": "", "reason": "record not found"}]
        rec = tracking.load_record(rec_path)
    except Exception as exc:
        # Covers both an unreadable/corrupt record AND `project_dir` itself
        # raising -- e.g. a same-machine child naming an unavailable/
        # unresolvable project state root under a namespaced install. Either
        # way this is a degraded, reported edge, never a crash.
        return [], [{"path": here, "ref": "", "reason": f"unreadable: {exc}"}]

    found: list[dict] = []
    unresolved: list[dict] = []
    for c in rec.resources:
        # A `session` claim is advisory-only -- `finalize`'s own obligation
        # gate explicitly never counts it as unsettled
        # (`_assert_obligations_settled`'s `kind != "session"` filter),
        # since a normally-running worktree always carries one for its own
        # live session. Mirroring that exclusion here keeps a
        # resource-clean subtree reporting as clean in real use instead of
        # always "owing" its own invoking session.
        if c.kind == "session":
            continue
        if c.is_unsettled:
            # An active `worktree`-kind claim IS itself a real, reportable
            # obligation -- the child it names has not finalized yet, which
            # is exactly what still blocks this worktree's own finalize
            # (proven end-to-end by `test_transitive_finalize_integration.py`).
            # Reporting only its DESCENDANTS' leaf claims (the old behavior)
            # could read as "settled" even while a clean-but-unfinalized
            # child still blocks the parent -- so it is surfaced here too,
            # in addition to descending for whatever it itself still owes.
            found.append({
                "path": here, "kind": c.kind, "ref": c.ref,
                "state": c.state, "note": c.note,
            })
        if c.kind != "worktree" or not c.is_unsettled:
            # Only an ACTIVE `worktree`-kind claim is a structural edge
            # worth descending; an at-rest/released one means that child
            # already settled (or never needs checking again), so there is
            # nothing further down that branch for THIS query to surface.
            continue
        parsed = tracking.parse_claim_ref(c.ref)
        if parsed is None or not parsed.is_qualified:
            unresolved.append({
                "path": here, "ref": c.ref, "reason": "unqualified child ref",
            })
            continue
        if not _is_safe_path_component(parsed.worktree_id) or not _is_safe_path_component(
            parsed.project or project
        ):
            # A corrupted/malicious ref (e.g. containing "..", a path
            # separator, or a NUL) could otherwise escape the project's
            # `worktrees` directory once joined into a filesystem path below
            # -- refuse to descend rather than read/report an unrelated file.
            unresolved.append({
                "path": here, "ref": c.ref,
                "reason": "unsafe child ref (rejected, not resolved)",
            })
            continue
        if parsed.machine != config.machine:
            unresolved.append({
                "path": here, "ref": c.ref,
                "reason": "cross-machine child (see lease mirror / claims sweep)",
            })
            continue
        child_found, child_unresolved = transitive_obligations(
            parsed.worktree_id, parsed.project or project, config,
            _visited=_visited, _path=here,
        )
        found.extend(child_found)
        unresolved.extend(child_unresolved)
    return found, unresolved


def cmd_claims_transitive(
    args: argparse.Namespace,
    worktree_id: str | None,
    *,
    infer_worktree_id,
    json_error,
    json_output,
) -> int:
    """``claims transitive [worktree_id]``: everything a worktree's whole
    subtree still owes, in one read. Never mutates, and never itself a
    finalize precondition (the existing one-hop obligation gate is still
    what actually enforces safety at each worktree's own finalize call).
    """
    config = cfg.load_config()
    wt_id = infer_worktree_id(worktree_id, config)
    if not wt_id or not _is_safe_path_component(wt_id):
        # `_infer_worktree_id` returns an explicit CLI value verbatim -- an
        # empty, path-separator-bearing, or ".."/"."-containing value must
        # never reach the filesystem join below.
        msg = f"invalid worktree id: {wt_id!r}"
        if args.json:
            return json_error(msg)
        output.err(msg)
        return 1
    rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1

    found, unresolved = transitive_obligations(wt_id, config.repo_name, config)

    if args.json:
        json_output({
            "worktree_id": wt_id,
            "obligations": [{**o, "path": list(o["path"])} for o in found],
            "unresolved": [{**u, "path": list(u["path"])} for u in unresolved],
        })
        return 0

    print(f"Transitive obligations for {wt_id} (whole subtree, recursive):")
    if not found:
        # Invariant: every `unresolved` entry is only ever produced for an
        # ACTIVE `worktree`-kind claim that was already appended to `found`
        # at the same level, so `found` empty implies `unresolved` empty too
        # -- there is no path left to a false "settled" claim while
        # something is still genuinely unresolved underneath.
        print("  (none -- the whole subtree is settled)")
    else:
        for o in found:
            path = " -> ".join(o["path"])
            note = f"  -- {o['note']}" if o.get("note") else ""
            print(f"  - [{path}] {o['kind']}: {o['ref']} [{o['state']}]{note}")
    if unresolved:
        print("  Subtree edges this machine could not check:")
        for u in unresolved:
            path = " -> ".join(u["path"])
            ref = f" {u['ref']}" if u.get("ref") else ""
            print(f"    - [{path}]{ref} {u['reason']}")
    return 0
