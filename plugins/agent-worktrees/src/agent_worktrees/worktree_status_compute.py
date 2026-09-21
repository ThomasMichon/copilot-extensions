"""Per-worktree status bundle fact-assembly (agent-worktrees-external-status-
accelerator effort, Phase 2).

Split out of ``__main__.py`` to keep that module under its shrink-only size
baseline (``tools/check-module-size.py``) -- purely a location move, no
behavior change. :mod:`worktree_status_daemon` wraps :func:`compute` with the
cache layer; :mod:`session_tracking_cli` (via ``__main__``'s ``core`` alias)
calls it directly as the uncoalesced fallback.
"""

from __future__ import annotations

import dataclasses
import time

from . import config as cfg
from . import disposition_history, git_ops, sessions, tracking


def _worktree_status_fact(value, *, confirmed: bool, observed_at: float) -> dict:
    """Wrap one fact per the effort's response shape: never a single
    all-or-nothing freshness flag for the whole bundle (see the
    ``agent-worktrees-external-status-accelerator`` effort README's Phase 1
    design and the vision's ``marked-not-multiplied-uncertainty``)."""
    return {"value": value, "confirmed": confirmed, "observed_at": observed_at}


def compute(project: str, worktree_id: str) -> dict:
    """Assemble the full per-worktree status bundle
    (agent-worktrees-external-status-accelerator effort, Phase 2).

    This is the raw fact-assembly function `worktree_status_daemon
    .build_cached_compute` wraps with the cache layer -- it always computes
    live (no caching of its own); staleness/force semantics live entirely in
    :mod:`worktree_status_cache`. Resolves the record itself from
    ``project``/``worktree_id`` -- never trusts a caller-serialized record,
    mirroring ``_classify_daemon_compute``'s own contract.

    Every fact is independently wrapped via :func:`_worktree_status_fact`: a
    fact this pass could not confirm (an exception, a timeout) renders
    ``confirmed: false`` with the best available last-known value (``None``
    when there is none) rather than raising past this call or fabricating a
    value -- a bundle-wide failure here would otherwise surface as a bare
    exception through the coalescing server to every joined caller, which is
    reserved for a genuinely unresolvable identity (no such project/worktree),
    not a single fact's transient failure.

    Raises only when ``project``/``worktree_id`` do not resolve to a real
    tracked record at all -- there is no bundle to return in that case.
    """
    started_at = time.time()
    tracking_path = cfg.project_dir(project) / "worktrees"
    record = tracking.load_record_by_id(worktree_id, tracking_path=tracking_path)
    if record is None:
        raise ValueError(f"no tracked worktree {worktree_id!r} in project {project!r}")

    facts: dict[str, dict] = {}

    try:
        config = cfg.load_config(path=cfg.project_dir(project) / "config.yaml", project=project)
        repo = config.default_repo
        info = git_ops.classify_worktree(
            record.worktree_path,
            record.branch,
            fetch=True,
            remote=repo.remote,
            default_branch=repo.default_branch,
        )
        git_confirmed = not (info.fetch_requested and info.fetch_failed)
        facts["git_state"] = _worktree_status_fact(
            dataclasses.asdict(info), confirmed=git_confirmed, observed_at=time.time()
        )
    except Exception:
        # A transient classification failure must not present as "no
        # information" when the durable record already carries a last-known
        # state (`WorktreeRecord.git_state`, a plain state string) -- retain
        # that rather than fabricating detail we don't have, per the
        # "confirmed: false" contract's own "best available last-known
        # value" requirement.
        last_known = {"state": record.git_state} if record.git_state else None
        facts["git_state"] = _worktree_status_fact(
            last_known, confirmed=False, observed_at=time.time()
        )

    try:
        from . import lineage_surfaces

        facts["lineage"] = _worktree_status_fact(
            lineage_surfaces.worktree_lineage(record), confirmed=True, observed_at=time.time()
        )
    except Exception:
        facts["lineage"] = _worktree_status_fact(None, confirmed=False, observed_at=time.time())

    try:
        verdict = sessions.verify_worktree_active(record)
        facts["liveness"] = _worktree_status_fact(
            dataclasses.asdict(verdict), confirmed=verdict.probes_ok, observed_at=time.time()
        )
    except Exception:
        # Same "retain the last-known value" contract as git_state above:
        # `WorktreeRecord` persists cached `mux_live`/`bound_live` hints
        # (see `tracking.stamp_mux_live`/`stamp_bound_live`) even though the
        # authoritative `verify_worktree_active` probe failed this pass.
        last_known = None
        if record.mux_live is not None or record.bound_live is not None:
            last_known = {"mux_live": record.mux_live, "bound_live": record.bound_live}
        facts["liveness"] = _worktree_status_fact(
            last_known, confirmed=False, observed_at=time.time()
        )

    try:
        # `resources` is the OUTWARD claim ledger (what this worktree holds);
        # `owner_ref` is the INWARD link (whose claim this worktree itself
        # answers to, e.g. a knowledge worktree paired to a harness worktree).
        # A consumer needs both to render the complete claims graph.
        claims = {
            "resources": [dataclasses.asdict(claim) for claim in record.resources],
            "owner_ref": record.owner_ref,
        }
        facts["claims"] = _worktree_status_fact(claims, confirmed=True, observed_at=time.time())
    except Exception:
        facts["claims"] = _worktree_status_fact(None, confirmed=False, observed_at=time.time())

    try:
        history = disposition_history.read(worktree_id, limit=20, tracking_path=tracking_path)
        disposition = {
            "title": record.title,
            "summary": record.summary,
            "follow_up": record.follow_up,
            "resume_count": record.resume_count,
            "status": str(record.status),
            "history": history,
        }
        facts["disposition"] = _worktree_status_fact(
            disposition, confirmed=True, observed_at=time.time()
        )
    except Exception:
        facts["disposition"] = _worktree_status_fact(None, confirmed=False, observed_at=time.time())

    return {
        "worktree_id": worktree_id,
        "project": project,
        "machine": record.machine,
        "started_at": started_at,
        "as_of": time.time(),
        "facts": facts,
    }
