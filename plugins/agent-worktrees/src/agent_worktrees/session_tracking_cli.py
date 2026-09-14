"""``agent-worktrees`` cross-project session-tracking CLI surface."""

from __future__ import annotations

import argparse
import dataclasses
import re
from pathlib import Path

from . import config as cfg
from . import installer as inst
from . import profile_assignment, sessions, terminal_conclusion, tracking


def _core():
    """Lazily resolve ``agent_worktrees.__main__`` -- see ``pr_cli._core``."""
    from . import __main__ as core

    return core


def add_parsers(sub) -> None:
    """Register parser wiring for cross-project session-tracking commands."""
    # list-sessions -- enumerate a worktree's Copilot sessions as JSON
    sp = sub.add_parser(
        "list-sessions",
        help="List a worktree's Copilot sessions with metadata (JSON)",
    )
    sp.add_argument(
        "--worktree",
        "--worktree-id",
        dest="worktree_id",
        default=None,
        help="Worktree ID to scope to (default: all worktrees)",
    )
    sp.add_argument(
        "--all-projects",
        action="store_true",
        help="Enumerate sessions across every adopted project",
    )
    sp.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (default; accepted for caller compatibility)",
    )

    # head-session -- a worktree's asserted head session + lifecycle state (JSON)
    sp = sub.add_parser(
        "head-session",
        help="Show a worktree's asserted head (current) session + state (JSON)",
    )
    sp.add_argument(
        "--worktree",
        "--worktree-id",
        dest="worktree_id",
        required=True,
        help="Worktree ID (full or 4-char suffix)",
    )
    sp.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (default; accepted for caller compatibility)",
    )

    sp = sub.add_parser(
        "worktree-lineage",
        help="Show one worktree's authoritative bounded lineage graph (JSON)",
    )
    sp.add_argument(
        "--worktree",
        "--worktree-id",
        dest="worktree_id",
        required=True,
        help="Worktree ID (full or unique suffix)",
    )
    sp.add_argument(
        "--json", action="store_true", help="Emit JSON (the default; accepted for consistency)"
    )

    # conclude-session -- assert a session's conclusion (handed-off | concluded)
    sp = sub.add_parser(
        "conclude-session",
        help="Assert a session concluded (handed-off|concluded); clears it as "
        "head (JSON) without guessing a successor",
    )
    sp.add_argument(
        "--worktree",
        "--worktree-id",
        dest="worktree_id",
        required=True,
        help="Worktree ID (full or 4-char suffix)",
    )
    sp.add_argument(
        "--session",
        "--session-id",
        dest="session_id",
        required=True,
        help="Copilot session ID to conclude",
    )
    sp.add_argument(
        "--state",
        choices=["handed-off", "concluded"],
        default="handed-off",
        help="Conclusion kind (default: handed-off)",
    )
    sp.add_argument("--handoff-token", default=None, help="Stable token for the pending handoff")
    sp.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (default; accepted for caller compatibility)",
    )

    # conclude-disposable -- safely conclude an exact disposable CLI worker
    sp = sub.add_parser(
        "conclude-disposable",
        help="Conclude an exact disposable CLI worker and optionally remove it",
    )
    sp.add_argument(
        "--worktree",
        "--worktree-id",
        dest="worktree_id",
        required=True,
        help="Exact recorded worktree id (suffix inference is not allowed)",
    )
    sp.add_argument(
        "--session",
        "--session-id",
        dest="session_id",
        default=None,
        help="Exact recorded Copilot session id, when available",
    )
    sp.add_argument(
        "--policy",
        choices=[
            terminal_conclusion.DISPOSABLE_CLI_POLICY,
            terminal_conclusion.DISPATCH_ATTEMPT_POLICY,
        ],
        required=True,
        help="Explicit discard policy authorizing generated checkout reconciliation",
    )
    sp.add_argument(
        "--reservation",
        dest="reservation_key",
        default=None,
        help="Exact dispatch reservation required by dispatch-attempt policy",
    )
    sp.add_argument(
        "--owner",
        required=True,
        help="Lifecycle owner recorded on the managed worktree",
    )
    sp.add_argument(
        "--remove",
        action="store_true",
        help="After a successful safety verdict, immediately run exact-ID "
        "managed teardown with fresh lifecycle and liveness checks",
    )
    sp.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (the default; accepted for command-family consistency)",
    )

    # link-succession -- write the two-way predecessor<->successor handoff link
    sp = sub.add_parser(
        "link-succession",
        help="Write the two-way predecessor<->successor link, conclude the "
        "predecessor, and move the head to the successor (JSON)",
    )
    sp.add_argument(
        "--worktree",
        "--worktree-id",
        dest="worktree_id",
        required=True,
        help="Worktree ID (full or 4-char suffix)",
    )
    sp.add_argument(
        "--predecessor", required=True, help="The outgoing session ID (marked handed-off)"
    )
    sp.add_argument("--successor", required=True, help="The incoming session ID (the new head)")
    sp.add_argument(
        "--predecessor-state",
        dest="predecessor_state",
        choices=["handed-off", "concluded"],
        default="handed-off",
        help="Predecessor conclusion kind (default: handed-off)",
    )
    sp.add_argument("--handoff-token", default=None, help="Stable token for this succession link")
    sp.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (default; accepted for caller compatibility)",
    )

    # session-transcript -- emit a session's renderable events as JSON
    sp = sub.add_parser(
        "session-transcript",
        help="Emit a Copilot session's renderable transcript events (JSON)",
    )
    sp.add_argument("session_id", help="Copilot session ID")
    sp.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (default; accepted for caller compatibility)",
    )

    # recent-messages -- a worktree's latest session's last N conversation turns
    sp = sub.add_parser(
        "recent-messages",
        help="Show a worktree's latest session's last N conversation messages "
        "(JSON) -- the read-side companion to the disposition summary",
    )
    sp.add_argument(
        "--worktree",
        "--worktree-id",
        dest="worktree_id",
        required=True,
        help="Worktree ID (full or 4-char suffix)",
    )
    sp.add_argument(
        "--limit",
        type=int,
        default=3,
        help="How many of the most recent messages to return (default: 3)",
    )
    sp.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (default; accepted for caller compatibility)",
    )


def cmd_list_sessions(args: argparse.Namespace) -> int:
    """List a worktree's Copilot sessions with metadata as JSON.

    Scopes to a single worktree with ``--worktree ID``; without it,
    enumerates sessions across tracked worktrees in the current project.
    ``--all-projects`` instead reads every adopted project's tracking
    directory. Each session entry carries its ``worktree_id`` plus the
    worktree's resolved interface and origin. Always emits the versioned JSON
    envelope (machine-facing -- consumed by agent-bridge).
    """
    wt_id = getattr(args, "worktree_id", None)
    all_projects = bool(getattr(args, "all_projects", False))
    if all_projects:
        records = []
        for tracking_path in _all_tracking_dirs():
            try:
                records.extend(tracking.list_records(tracking_path))
            except Exception:
                continue
    else:
        records = tracking.list_records(cfg.tracking_dir())
    if wt_id:
        records = [r for r in records if r.worktree_id == wt_id]
        if not records:
            return _core()._json_error(f"No worktree found: {wt_id}")

    by_session: dict[str, dict] = {}
    head_session: str | None = None
    head_revision = 0
    handoffs: list[dict] = []
    controller_revision = 0
    controllers: list[dict[str, object]] = []
    controller_findings: list[dict[str, object]] = []
    for rec in records:
        for s in sessions.list_worktree_sessions(rec):
            row = dict(s)
            row["worktree_id"] = rec.worktree_id
            row["interface"] = rec.resolved_interface
            row["origin"] = rec.resolved_origin
            session_id = row.get("id")
            if not isinstance(session_id, str) or not session_id:
                continue
            assignment = profile_assignment.assignment_for_session(rec, session_id)
            if assignment is not None:
                row["profile_assignment"] = profile_assignment.metadata(assignment)
            existing = by_session.get(session_id)
            if existing is None:
                by_session[session_id] = row
                continue
            if (
                existing.get("interface") != row["interface"]
                or existing.get("origin") != row["origin"]
            ):
                existing["interface"] = "unknown"
                existing["origin"] = "unknown"
                existing["provenance_conflict"] = True
            if row.get("is_head") and not existing.get("is_head"):
                preserved = {
                    key: existing[key]
                    for key in ("interface", "origin", "provenance_conflict")
                    if key in existing
                }
                existing.clear()
                existing.update(row)
                existing.update(preserved)
    # session-lifecycle: when scoped to ONE worktree, surface its asserted head
    # on the envelope so a consumer (agent-bridge -> Neuron Forge) can resolve
    # the current session without re-deriving it. Per-session ``is_head`` (from
    # list_worktree_sessions) covers the all-worktrees case. Derived from the
    # ground-layer record; no rival pointer (agent-fabric derive-dont-duplicate).
    if wt_id and records:
        head_session = records[0].resolved_head_session
        transition = records[0].replayed_head_transition
        head_revision = transition.revision if transition is not None else 0
        handoffs = [dataclasses.asdict(handoff) for handoff in records[0].handoffs]
        controller_revision = records[0].controller_revision
        controllers = _core()._controller_metadata(records[0])
        controller_findings = _core()._controller_findings(records[0])

    _core()._json_output(
        {
            "sessions": list(by_session.values()),
            "head_session": head_session,
            "head_revision": head_revision,
            "handoffs": handoffs,
            "controller_revision": controller_revision,
            "controllers": controllers,
            "controller_findings": controller_findings,
        }
    )
    return 0


def _all_tracking_dirs() -> list[Path]:
    """Every project's worktree-tracking dir on this machine (dedup, ordered).

    The active project (resolved from CWD, when there is one) comes first, then
    every project in the projects registry. This lets a **project-agnostic
    caller** -- notably the agent-bridge daemon, whose CWD is unrelated to the
    worktree it is guarding -- resolve a worktree by id without first knowing
    which project owns it. Never raises: a project that cannot resolve a dir is
    skipped.
    """
    dirs: list[Path] = []
    seen: set[Path] = set()

    def _add(d: Path | None) -> None:
        if d is not None and d not in seen:
            seen.add(d)
            dirs.append(d)

    try:
        _add(cfg.tracking_dir())
    except Exception:
        pass
    try:
        projects = inst.read_projects_registry().get("projects", {})
    except Exception:
        projects = {}
    for name in projects:
        try:
            _add(cfg.project_dir(name) / "worktrees")
        except Exception:
            continue
    return dirs


def _find_tracking_file(raw_id: str) -> Path | None:
    """Locate a worktree's tracking YAML across **all** projects, or None.

    Exact stem match wins globally; a unique 4-char (or longer) suffix match is
    the fallback. An ambiguous suffix (or no match) returns None -- the caller
    then treats the worktree as untracked (fail-open), never guessing.
    """
    import re

    if re.search(r"[/\\]|\.\.", raw_id):
        return None
    tdirs = _all_tracking_dirs()
    for tdir in tdirs:
        exact = tdir / f"{raw_id}.yaml"
        if exact.exists():
            return exact
    matches: list[Path] = []
    for tdir in tdirs:
        if not tdir.exists():
            continue
        matches += [p for p in tdir.glob("*.yaml") if p.stem.endswith(raw_id)]
    return matches[0] if len(matches) == 1 else None


def _find_tracking_file_by_session(session_id: str) -> Path | None:
    """Locate the first exact registered association in deterministic order."""
    for tracking_dir in _all_tracking_dirs():
        if not tracking_dir.exists():
            continue
        for path in sorted(tracking_dir.glob("*.yaml"), key=lambda item: item.name):
            try:
                if session_id not in path.read_text(encoding="utf-8"):
                    continue
                if tracking.load_record(path).session_entry(session_id):
                    return path
            except Exception:
                continue
    return None


def cmd_head_session(args: argparse.Namespace) -> int:
    """Emit a worktree's **asserted head session** and its lifecycle state (JSON).

    The ground-layer read that higher layers (agent-bridge's create guard,
    context-handoff) **derive** the current session from -- the source of truth
    for "which session is current in this worktree," so no other layer keeps a
    rival pointer (agent-fabric ``derive-dont-duplicate``).

    Output envelope::

        {"version": 1, "worktree_id": "<id>", "tracked": bool,
         "head_session": "<session-id>" | null, "active": bool,
         "state": "active" | "handed-off" | "concluded" | null}

    - ``head_session`` is ``WorktreeRecord.resolved_head_session`` -- the stored
      head when it is still un-concluded, else the newest non-concluded session
      (today's "latest is current" fallback), else null.
    - ``active`` is ``head_session is not None`` -- i.e. the worktree has a
      current, un-concluded session that a fresh create would collide with.
    - ``tracked`` is False when no tracking record exists for the worktree (an
      unknown / untracked worktree): a fail-open signal so a consumer treats it
      as "no head to guard."

    Resolves the worktree across **all** projects (see :func:`_find_tracking_file`)
    so the agent-bridge daemon can call it from any CWD. An unknown worktree is
    **not** an error (exit 0, ``tracked: false``): a guard that cannot find a
    record must fail *open*, not refuse the create.
    """
    raw = args.worktree_id
    yaml_path = _find_tracking_file(raw)
    if yaml_path is None:
        _core()._json_output(
            {
                "worktree_id": raw,
                "tracked": False,
                "head_session": None,
                "active": False,
                "occupied": False,
                "state": None,
                "head_revision": 0,
                "pending_handoffs": [],
                "controller_revision": 0,
                "controllers": [],
                "controller_findings": [],
            }
        )
        return 0
    record = tracking.load_record(yaml_path)
    head = record.resolved_head_session
    entry = record.session_entry(head) if head else None
    transition = record.replayed_head_transition
    pending = [
        {
            "ordinal": handoff.ordinal,
            "token": handoff.token,
            "predecessor": handoff.predecessor,
            "opened_at": handoff.opened_at,
            "candidate": handoff.candidate,
            "candidate_at": handoff.candidate_at,
        }
        for handoff in record.pending_handoffs
    ]
    _core()._json_output(
        {
            "worktree_id": record.worktree_id or raw,
            "tracked": True,
            "head_session": head,
            "active": head is not None,
            "occupied": head is not None or bool(pending),
            "state": (entry.state if entry is not None else None),
            "head_revision": transition.revision if transition is not None else 0,
            "pending_handoffs": pending,
            "controller_revision": record.controller_revision,
            "controllers": _core()._controller_metadata(record),
            "controller_findings": _core()._controller_findings(record),
        }
    )
    return 0


def cmd_worktree_lineage(args: argparse.Namespace) -> int:
    """Emit one authoritative worktree's bounded session/controller graph."""
    from . import lineage_surfaces

    yaml_path = _find_tracking_file(args.worktree_id)
    if yaml_path is None:
        return _core()._json_error(f"No worktree found: {args.worktree_id}")
    record = tracking.load_record(yaml_path)
    _core()._json_output(lineage_surfaces.worktree_lineage(record))
    return 0


def cmd_conclude_session(args: argparse.Namespace) -> int:
    """Assert a session's conclusion (``handed-off`` | ``concluded``) -- JSON out.

    The ground-layer WRITE that context-handoff's live cutover shells to so the
    retired session leaves a durable, asserted lifecycle record -- not merely a
    killed pane. Concluding the outgoing session clears it as head without
    guessing a replacement from list order. An exact-token successor bind
    performs the normal atomic link; this command remains the compatibility and
    explicit-sunset primitive.

    Resolves the worktree across all projects (a higher-layer caller's CWD is
    unrelated to the worktree). Unlike the read-only ``head-session``, an unknown
    worktree or session is a real error here -- a mutation must not silently
    no-op.
    """
    raw = args.worktree_id
    yaml_path = _find_tracking_file(raw)
    if yaml_path is None:
        return _core()._json_error(f"Worktree not found: {raw}")
    state = getattr(args, "state", "handed-off")
    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        try:
            tracking.conclude_session(
                record,
                args.session_id,
                state=state,
                handoff_token=getattr(args, "handoff_token", None),
                save=False,
            )
        except tracking.SessionLifecycleError as e:
            return _core()._json_error(str(e))
        # Persist to the RESOLVED path, not ``record.yaml_path`` -- this verb is
        # project-agnostic (``_find_tracking_file`` searches every project), and
        # runs with no active project, so a bare ``save_record`` would recompute
        # the wrong (or an unresolvable) path.
        tracking.save_record(record, yaml_path)
    record = tracking.load_record(yaml_path)
    entry = record.session_entry(args.session_id)
    _core()._json_output(
        {
            "worktree_id": record.worktree_id or raw,
            "session": args.session_id,
            "state": (entry.state if entry is not None else None),
            "head_session": record.resolved_head_session,
            "head_revision": record.head_revision,
            "pending_handoffs": [
                dataclasses.asdict(handoff) for handoff in record.pending_handoffs
            ],
        }
    )
    return 0


def _find_tracking_file_exact(raw_id: str) -> Path | None:
    """Locate an exact worktree id across projects without suffix inference."""
    if re.search(r"[/\\]|\.\.", raw_id):
        return None
    matches: list[Path] = []
    for tracking_dir in _all_tracking_dirs():
        path = tracking_dir / f"{raw_id}.yaml"
        if path.is_file():
            matches.append(path)
    unique = list(dict.fromkeys(path.resolve() for path in matches))
    if len(unique) > 1:
        raise RuntimeError(f"Worktree id is ambiguous across projects: {raw_id}")
    return unique[0] if unique else None


def _project_for_tracking_file(path: Path) -> str | None:
    """Resolve the project whose machine-local tracking dir contains ``path``."""
    names: list[str] = []
    active = cfg.active_project()
    if active:
        names.append(active)
    try:
        names.extend(inst.read_projects_registry().get("projects", {}))
    except Exception:
        pass
    for name in dict.fromkeys(names):
        try:
            if (cfg.project_dir(name) / "worktrees").resolve() == path.parent.resolve():
                return name
        except OSError:
            continue
    return None


def _relocate_active_project_for_worktree(wt_id: str) -> bool:
    """Switch the ambient active project if ``wt_id`` lives in a different one.

    A worktree id is globally unique, but a resume-by-id call only knows the
    *ambient* project (CWD/``--project`` resolved once by ``main()``) -- which
    is wrong whenever the Picker's cross-project daemon hands back an id for a
    worktree that belongs to some other registered project (#2338 follow-up:
    the launcher scoping fix covers plan construction, but a resume-by-id
    lookup that runs before any plan exists still trusts the ambient project).
    Rather than fail with a false "Worktree not found" for a worktree that
    genuinely exists elsewhere, look it up by exact id across every
    registered project's tracking dir and, if it is uniquely found under a
    different project, switch the in-process active project to match before
    the caller re-checks. Best-effort: any lookup error leaves the ambient
    project untouched, so the caller's existing "not found" handling still
    applies. Returns ``True`` iff the active project was switched.
    """
    if (cfg.tracking_dir() / f"{wt_id}.yaml").exists():
        return False
    try:
        found = _find_tracking_file_exact(wt_id)
    except RuntimeError:
        return False
    if found is None:
        return False
    project = _project_for_tracking_file(found)
    if not project or project == cfg.active_project():
        return False
    cfg.set_active_project(project)
    return True


def cmd_conclude_disposable(args: argparse.Namespace) -> int:
    """Conclude one exact disposable CLI worker and optionally remove it."""
    raw = args.worktree_id
    if not raw or re.search(r"[/\\]|\.\.", raw):
        return _core()._json_error(f"Invalid exact worktree id: {raw!r}")
    try:
        yaml_path = _find_tracking_file_exact(raw)
    except RuntimeError as exc:
        return _core()._json_error(str(exc))
    if yaml_path is None:
        if getattr(args, "remove", False):
            _core()._json_output(
                {
                    "worktree_id": raw,
                    "action": "already-removed",
                    "managed_gc_eligible": False,
                }
            )
            return 0
        return _core()._json_error(f"Worktree not found by exact id: {raw}")
    project = _project_for_tracking_file(yaml_path)
    if not project:
        return _core()._json_error(f"Could not resolve project for worktree: {raw}")
    try:
        config = cfg.load_project_config(project)
        try:
            record = tracking.load_record(yaml_path)
        except FileNotFoundError:
            if getattr(args, "remove", False):
                _core()._json_output(
                    {
                        "worktree_id": raw,
                        "action": "already-removed",
                        "managed_gc_eligible": False,
                    }
                )
                return 0
            raise
        if record.worktree_id != raw or record.worktree_id != yaml_path.stem:
            return _core()._json_error(f"Tracking record identity mismatch for exact id: {raw}")
        repo = _core()._repo_for_record(config, record)
        if repo is None:
            _core()._json_output(
                {
                    "worktree_id": record.worktree_id,
                    "action": "skipped",
                    "reason": "repo-unresolved",
                    "managed_gc_eligible": False,
                }
            )
            return 0
        try:
            result = terminal_conclusion.conclude_disposable_worktree(
                yaml_path,
                repo,
                session_id=getattr(args, "session_id", None),
                owner=args.owner,
                policy=args.policy,
                reservation_key=getattr(args, "reservation_key", None),
            )
        except FileNotFoundError:
            if getattr(args, "remove", False) and not yaml_path.exists():
                _core()._json_output(
                    {
                        "worktree_id": raw,
                        "action": "already-removed",
                        "managed_gc_eligible": False,
                    }
                )
                return 0
            raise
        if getattr(args, "remove", False) and result.get("managed_gc_eligible"):
            report = _core().sweep_managed_worktrees(
                min_idle_secs=0,
                config=config,
                tracking_path=yaml_path.parent,
                worktree_ids={record.worktree_id},
            )
            removed = next(
                (entry for entry in report["removed"] if entry.get("id") == record.worktree_id),
                None,
            )
            if removed is None:
                if not yaml_path.exists():
                    result.update(
                        action="already-removed",
                        reason="managed-gc-already-removed",
                        managed_gc_eligible=False,
                    )
                    _core()._json_output(result)
                    return 0
                skipped = next(
                    (
                        entry
                        for entry in report["skipped"]
                        if entry.get("id") == record.worktree_id
                    ),
                    None,
                )
                reason = (
                    skipped.get("reason")
                    if isinstance(skipped, dict)
                    else "worktree was not selected"
                )
                raise RuntimeError(f"managed teardown skipped: {reason}")
            result.update(
                action="removed",
                reason="managed-gc-removed",
                managed_gc_eligible=False,
                removal=removed,
            )
    except Exception as exc:
        return _core()._json_error(str(exc))
    _core()._json_output(result)
    return 0


def cmd_link_succession(args: argparse.Namespace) -> int:
    """Write the durable two-way handoff link and move the head -- JSON out.

    The explicit ground-layer form of ``tracking.link_succession``: chains
    ``predecessor -> successor`` in both directions, concludes the predecessor
    (default ``handed-off``), and moves the head to the successor. Both sessions
    must already be tracked -- so this is for callers that know BOTH ids (e.g. an
    explicit, non-cutover handoff or a manual repair). The live cutover instead
    concludes the predecessor via ``conclude-session`` and lets
    ``register_session`` stamp the successor half once its id exists.
    """
    raw = args.worktree_id
    yaml_path = _find_tracking_file(raw)
    if yaml_path is None:
        return _core()._json_error(f"Worktree not found: {raw}")
    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        try:
            tracking.link_succession(
                record,
                args.predecessor,
                args.successor,
                predecessor_state=getattr(args, "predecessor_state", "handed-off"),
                handoff_token=getattr(args, "handoff_token", None),
                save=False,
            )
        except tracking.SessionLifecycleError as e:
            return _core()._json_error(str(e))
        # Persist to the RESOLVED path (see cmd_conclude_session): this verb is
        # project-agnostic and runs with no active project.
        tracking.save_record(record, yaml_path)
    record = tracking.load_record(yaml_path)
    pred = record.session_entry(args.predecessor)
    _core()._json_output(
        {
            "worktree_id": record.worktree_id or raw,
            "predecessor": args.predecessor,
            "successor": args.successor,
            "predecessor_state": (pred.state if pred is not None else None),
            "head_session": record.resolved_head_session,
            "head_revision": record.head_revision,
        }
    )
    return 0


def cmd_session_transcript(args: argparse.Namespace) -> int:
    """Emit a single session's renderable transcript events as JSON.

    Reads the session's ``events.jsonl`` from local session-state and
    returns the renderable event subset.  An absent/empty session yields
    an empty ``events`` list (not an error) so callers can treat "no
    transcript" uniformly.
    """
    session_id = args.session_id
    events = sessions.read_session_transcript(session_id)
    _core()._json_output({"session_id": session_id, "events": events})
    return 0


def cmd_recent_messages(args: argparse.Namespace) -> int:
    """Emit a worktree's latest session's last N conversation messages as JSON.

    The read-side companion to the disposition ``summary`` overlay: when the
    agent-asserted summary never accumulated, this derives recent context
    straight from the worktree's newest session ``events.jsonl``. Accepts a full
    worktree id or its 4-char suffix. An unknown worktree is a JSON error; a
    known worktree with no session yields an empty ``messages`` list.
    """
    wt_id = _core()._resolve_worktree_id(args.worktree_id)
    records = tracking.list_records(cfg.tracking_dir())
    rec = next((r for r in records if r.worktree_id == wt_id), None)
    if rec is None:
        return _core()._json_error(f"No worktree found: {args.worktree_id}")
    payload = sessions.recent_worktree_messages(rec, limit=getattr(args, "limit", 3))
    payload["worktree_id"] = rec.worktree_id
    _core()._json_output(payload)
    return 0
