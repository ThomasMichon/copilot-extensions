"""Create/run/sync worktree operation surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

from . import activity, finalize as fin, git_ops, obligations, output, sessions, tracking
from . import claims_cli
from . import config as cfg
from . import managed_worktree_guard as remove_guard
from . import reap_cli


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _apply_tracking_override(*args, **kwargs):
    return _core()._apply_tracking_override(*args, **kwargs)


def _build_active_paths(*args, **kwargs):
    return _core()._build_active_paths(*args, **kwargs)


def _create_worktree_core(*args, **kwargs):
    return _core()._create_worktree_core(*args, **kwargs)


def _infer_worktree_id_from_cwd(*args, **kwargs):
    return _core()._infer_worktree_id_from_cwd(*args, **kwargs)


def _json_error(*args, **kwargs):
    return _core()._json_error(*args, **kwargs)


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


def _remove_managed_worktree(*args, **kwargs):
    return reap_cli._remove_managed_worktree(*args, **kwargs)


def _resolve_worktree_id(*args, **kwargs):
    return _core()._resolve_worktree_id(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "create",
        help="Create a worktree programmatically (no launch, no mux) -- the "
        "path for agents/daemons; prints id + dir (add --json for a plan)",
    )
    p.add_argument(
        "--system",
        action="store_true",
        help="Create a daemon-owned worktree (hidden from Picker, "
        "cleanup-exempt; tear down with remove-system)",
    )
    p.add_argument(
        "--name",
        default=None,
        help="With --system: short slug for the worktree id (e.g. the service name)",
    )
    p.add_argument(
        "--owner",
        default=None,
        help="With --system: owning service name (recorded for the browse view)",
    )
    p.add_argument(
        "--interface",
        default=None,
        choices=["cli", "acp"],
        help="Stamp the worktree's interface mark (cli|acp). Default: "
        "derived from kind (bridge=acp, else cli). See #2668.",
    )
    p.add_argument(
        "--origin",
        default=None,
        choices=["user", "system", "delegate"],
        help="Stamp who kicked the work off (user|system|delegate). "
        "user = operator (NF/Picker), delegate = agent-spawned, "
        "system = background/daemon. Default: derived from kind + "
        "caller. Governs Picker/cockpit visibility. See #2668.",
    )
    p.add_argument(
        "--dispatch-task-id", default=None, help="Task id for a dispatch-created attempt worktree"
    )
    p.add_argument(
        "--dispatch-reservation-key",
        default=None,
        help="Exact spawn reservation that created this worktree",
    )
    p.add_argument(
        "--dispatch-attempt",
        type=int,
        default=None,
        help="Positive dispatch spawn-attempt ordinal",
    )
    p.add_argument(
        "--dispatch-driver",
        default=None,
        help="Dispatch lifecycle driver that owns this allocation",
    )
    p.add_argument(
        "--dispatch-supervisor",
        default=None,
        help="Exact supervisor process provenance for this allocation",
    )
    p.add_argument(
        "--owner-ref",
        default=None,
        dest="owner_ref",
        help="Qualified ref (machine/project/worktree_id[#session]) of "
        "the worktree that owns this one as an outbound resource. "
        "Usually injected by `run` via AGENT_WORKTREES_OWNER_REF; "
        "the flag is the low-level primitive for scripts/tools "
        "that already know both sides.",
    )
    p.add_argument(
        "--no-owner",
        action="store_true",
        dest="no_owner",
        help="Create a deliberately top-level worktree: do NOT inherit "
        "an owner from AGENT_WORKTREES_OWNER_REF or the CWD, or a "
        "parent session from COPILOT_AGENT_SESSION_ID, so no parent's "
        "finalize or terminal-cleanup gate is held on it (Ph6).",
    )
    p.add_argument(
        "--agent",
        default=None,
        dest="bound_agent",
        help="Bind a charter (agent-bridge spawn profile name, e.g. "
        "board-sweep-worker) to this worktree: agent-bridge reads the "
        "binding when spawning/resuming any session here instead of "
        "the venue's bare default. A charter is a spawn profile, never "
        "its own first-class agent-bridge target.",
    )
    p.add_argument(
        "--no-pair",
        action="store_true",
        dest="no_pair",
        help="Skip the paired-knowledge carve for THIS creation only, "
        "regardless of --origin -- for a registrar/pool declaration with "
        "no bound knowledge repo to give its workers. Narrower than the "
        "whole-host AGENT_WORKTREES_NO_PAIR env var, which still applies "
        "on top of this flag.",
    )
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")

    p = sub.add_parser(
        "run",
        help="Run an inner (possibly cross-repo) subcommand and journal the "
        "resource it produces as an outbound claim on THIS worktree "
        '(e.g. run "<other-project> create --json")',
    )
    p.add_argument(
        "--owner-ref",
        default=None,
        dest="owner_ref",
        help="Override the auto-resolved owner ref "
        "(machine/project/worktree_id[#session]) for the calling "
        "worktree. Default: resolved from the current directory.",
    )
    p.add_argument(
        "inner_command",
        nargs=argparse.REMAINDER,
        help="The inner subcommand to run, as a quoted string or "
        'trailing tokens (e.g. "copilot-extensions create --json")',
    )

    p = sub.add_parser("remove-system", help="Remove a system worktree by id")
    p.add_argument("worktree_id", help="Worktree id to remove")
    p.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")

    p = sub.add_parser("sync", help="Fast-forward worktrees to the default branch")
    p.add_argument(
        "--worktree-id",
        default=None,
        help="Sync a single worktree by ID (default: all active worktrees on this machine)",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Sync every active worktree (the default when no --worktree-id is given)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help='Emit JSON results (a single object with --worktree-id, else {"results": [...]})',
    )


def _slugify(text: str) -> str:
    """Lowercase, keep alnum/dash, collapse the rest to single dashes."""
    import re

    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s or "daemon"


def cmd_remove_system(args: argparse.Namespace) -> int:
    """Remove a system worktree by id (git worktree + tracking record)."""
    config = cfg.load_config()
    tracking_path = cfg.tracking_dir()
    wt_id = getattr(args, "worktree_id", None)
    force = getattr(args, "force", False)
    if not wt_id:
        output.err("remove-system requires a worktree id")
        return 2

    yaml_path = tracking_path / f"{wt_id}.yaml"
    if not yaml_path.exists():
        output.err(f"no such worktree: {wt_id}")
        return 1
    rec = tracking.load_record(yaml_path)
    if rec.kind not in tracking.MANAGED_KINDS:
        output.err(
            f"{wt_id} is not a managed (system/bridge) worktree (kind={rec.kind}); refusing"
        )
        return 1

    def _fail(message: str) -> int:
        return _json_error(message) if getattr(args, "json", False) else (output.err(message) or 1)

    outcome = remove_guard.perform(
        wt_id,
        yaml_path,
        config,
        force=force,
        remove_fn=_remove_managed_worktree,
        tracking_path=tracking_path,
    )
    if not outcome.ok:
        return _fail(outcome.message)
    if not outcome.removed:
        rec = outcome.rec
        loc = f"; worktree path: {rec.worktree_path}" if rec.worktree_path else ""
        return _fail(
            f"failed to fully remove system worktree {wt_id}: "
            f"{'; '.join(outcome.warnings) or 'removal failed'}{loc}; "
            "tracking record retained for retry"
        )
    activity.log_event("system_worktree_removed", worktree_id=wt_id)
    if getattr(args, "json", False):
        _json_output({"removed": wt_id})
    else:
        print(f"✅ Removed system worktree: {wt_id}")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    """Create a new worktree non-interactively."""
    is_system = getattr(args, "system", False)
    no_owner = getattr(args, "no_owner", False)
    owner_ref = (
        None
        if (is_system or no_owner)
        else (
            getattr(args, "owner_ref", None)
            or os.environ.get("AGENT_WORKTREES_OWNER_REF")
            or _resolve_owner_ref()
            or None
        )
    )
    dispatch_fields = {
        "task_id": getattr(args, "dispatch_task_id", None),
        "reservation_key": getattr(args, "dispatch_reservation_key", None),
        "attempt": getattr(args, "dispatch_attempt", None),
        "driver": getattr(args, "dispatch_driver", None),
        "supervisor": getattr(args, "dispatch_supervisor", None),
    }
    present_dispatch_fields = {key for key, value in dispatch_fields.items() if value is not None}
    if present_dispatch_fields and len(present_dispatch_fields) != len(dispatch_fields):
        missing = sorted(set(dispatch_fields) - present_dispatch_fields)
        message = "dispatch allocation provenance requires all fields; missing " + ", ".join(
            missing
        )
        if args.json:
            return _json_error(message)
        output.err(message)
        return 1
    if present_dispatch_fields:
        attempt = dispatch_fields["attempt"]
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt <= 0:
            message = "dispatch attempt must be a positive integer"
            if args.json:
                return _json_error(message)
            output.err(message)
            return 1
        invalid = [
            key
            for key in ("task_id", "reservation_key", "driver", "supervisor")
            if not isinstance(dispatch_fields[key], str)
            or not dispatch_fields[key].strip()
            or len(dispatch_fields[key].strip()) > tracking.DISPATCH_PROVENANCE_TEXT_MAX
        ]
        if invalid:
            message = (
                "dispatch allocation fields must be non-empty and at most "
                f"{tracking.DISPATCH_PROVENANCE_TEXT_MAX} characters: {', '.join(invalid)}"
            )
            if args.json:
                return _json_error(message)
            output.err(message)
            return 1
        for key in ("task_id", "reservation_key", "driver", "supervisor"):
            value = dispatch_fields[key]
            if isinstance(value, str):
                dispatch_fields[key] = value.strip()
    dispatch_attempt = dispatch_fields if present_dispatch_fields else None
    with output.stdout_to_stderr():
        try:
            config = cfg.load_config()
            result = _create_worktree_core(
                config,
                no_mux=True,
                kind="system" if is_system else "session",
                owner=(getattr(args, "owner", None) or getattr(args, "name", None))
                if is_system
                else None,
                name=getattr(args, "name", None) if is_system else None,
                interface=getattr(args, "interface", None),
                origin=getattr(args, "origin", None),
                owner_ref=owner_ref,
                inherit_parent_session=not (is_system or no_owner),
                dispatch_attempt=dispatch_attempt,
                bound_agent=getattr(args, "bound_agent", None),
                no_pair=getattr(args, "no_pair", False),
            )
        except claims_cli.CoordinationReadinessFailure as exc:
            return claims_cli._emit_coordination_rejection(exc.readiness, json_out=args.json)
        except Exception as e:
            if args.json:
                return _json_error(str(e))
            output.err(str(e))
            return 1

    if args.json:
        _json_output(result)
        return 0

    wt = result["worktree"]
    label = "system worktree" if is_system else "worktree"
    print(f"✅ Created {label}: {wt['id']}")
    print(f"   Path:   {wt['path']}")
    print(f"   Branch: {wt['branch']}")
    return 0


def _resolve_owner_ref() -> str | None:
    """Build this worktree's qualified owner ref from the current context."""
    try:
        config = cfg.load_config()
    except Exception:
        return None
    caller_id = _infer_worktree_id_from_cwd(config)
    if not caller_id:
        return _resolve_anchor_owner_ref(config)
    try:
        project = cfg.project_name()
    except Exception:
        project = config.repo_name
    session = os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    return tracking.format_claim_ref(config.machine, project, caller_id, session)


def _resolve_anchor_owner_ref(config: cfg.Config) -> str | None:
    """Resolve an ANCHOR owner ref when the CWD is a **singleton** repo's anchor."""
    try:
        from . import repos as _repos

        project = cfg.project_name()
    except Exception:
        return None
    if not project:
        return None
    try:
        entry = _repos.find_repo(project)
    except Exception:
        return None
    if entry is None or entry.repo_class != "singleton":
        return None
    anchor = entry.local_path()
    if not anchor:
        return None
    try:
        cwd = Path.cwd().resolve()
        anchor_p = Path(anchor).resolve()
        if cwd != anchor_p and anchor_p not in cwd.parents:
            return None
    except Exception:
        return None
    session = os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    return tracking.format_anchor_ref(config.machine, project, session)


def _claim_from_run_output(stdout: str) -> tracking.ResourceClaim | None:
    """Recognize the resource an inner command produced from its JSON output."""
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    wt = data.get("worktree")
    if not isinstance(wt, dict):
        wt = data if data.get("id") and data.get("machine") else None
    if isinstance(wt, dict):
        wid = wt.get("id")
        machine = wt.get("machine")
        project = wt.get("repo")
        if wid and machine:
            ref = tracking.format_claim_ref(
                str(machine), str(project) if project else None, str(wid)
            )
            return tracking.ResourceClaim(
                kind="worktree",
                ref=ref,
                created_at=tracking._now_iso(),
                state="active",
            )
    pr = data.get("pr")
    if isinstance(pr, dict) and pr.get("ref"):
        return tracking.ResourceClaim(
            kind="pr",
            ref=str(pr["ref"]),
            created_at=tracking._now_iso(),
            state="active",
        )
    return None


def _ensure_anchor_ledger(
    parsed: tracking.ClaimRef,
    tracking_dir,
) -> tracking.WorktreeRecord | None:
    """Lazily materialize a singleton project's ``@anchor`` claim ledger."""
    try:
        from . import repos as _repos

        config = cfg.load_config()
    except Exception:
        return None
    project = parsed.project or getattr(config, "repo_name", None)
    if not project:
        return None
    anchor = None
    try:
        entry = _repos.find_repo(project)
        anchor = entry.local_path() if entry else None
    except Exception:
        anchor = None
    if not anchor:
        try:
            anchor = config.default_repo.anchor
        except Exception:
            return None
    try:
        return tracking.load_or_create_anchor_record(
            anchor, project, config.machine, config.platform, tracking_dir
        )
    except Exception:
        return None


def _journal_run_claim(owner_ref: str, stdout: str) -> tracking.ResourceClaim | None:
    """Append the produced-resource claim to the caller's tracking record."""
    claim = _claim_from_run_output(stdout)
    if claim is None:
        return None
    parsed = tracking.parse_claim_ref(owner_ref)
    if parsed is None:
        return None
    tdir = cfg.tracking_dir()
    rec_path = tdir / f"{parsed.worktree_id}.yaml"
    if not rec_path.exists():
        if parsed.is_anchor:
            record = _ensure_anchor_ledger(parsed, tdir)
            if record is None:
                return None
        else:
            return None
    with tracking._RecordLock(rec_path, require_sidecar=True):
        record = tracking.load_record(rec_path)
        tracking.add_resource_claim(record, claim, save=False)
        tracking.save_record(record, rec_path)
    return claim


def cmd_run(args: argparse.Namespace) -> int:
    """Run an inner subcommand and journal the resource it produces as an outbound claim."""
    raw = list(getattr(args, "inner_command", None) or [])
    if not raw:
        output.err('run: no subcommand given. Usage: run "<subcommand ...>"')
        return 2
    cmd_str = raw[0] if len(raw) == 1 else " ".join(raw)

    owner_ref = getattr(args, "owner_ref", None) or _core_helper("_resolve_owner_ref", _resolve_owner_ref)()
    if not owner_ref:
        output.err(
            "run: could not resolve the calling worktree from the current "
            "directory. Refusing to create a resource without creator "
            "ownership; run from a managed worktree or pass --owner-ref."
        )
        return 1

    try:
        run_config = cfg.load_config()
        parsed_owner = tracking.parse_claim_ref(owner_ref)
    except (OSError, RuntimeError, ValueError) as exc:
        output.err(f"run: cannot resolve owner: {exc}")
        return 1
    if parsed_owner is None or not parsed_owner.is_qualified:
        output.err(
            "run: --owner-ref must be a qualified "
            f"machine/project/worktree_id ref (got {owner_ref!r})"
        )
        return 1
    if parsed_owner.machine != run_config.machine:
        output.err(
            f"run: cross-machine owner {owner_ref} cannot synchronously reserve "
            "this resource; use a dispatch/lease flow that persists remote "
            "ownership before creation"
        )
        return 1
    readiness = claims_cli._coordination_readiness_for_owner_ref(owner_ref, run_config)
    if not readiness.ready:
        output.err(f"run: {readiness.code}: {readiness.error}")
        return 3

    child_env = dict(os.environ)
    child_env["AGENT_WORKTREES_OWNER_REF"] = owner_ref

    owner_path = None
    pending_ref = ""
    if owner_ref:
        try:
            owner_path, _owner_id, owner_err = claims_cli._resolve_owner_ref_record_path(
                owner_ref, run_config
            )
            if owner_err:
                raise RuntimeError(owner_err)
            if owner_path is not None and not owner_path.exists():
                if parsed_owner and parsed_owner.is_anchor:
                    _ensure_anchor_ledger(parsed_owner, owner_path.parent)
            if owner_path is not None and not owner_path.exists():
                raise RuntimeError(f"same-machine owner ledger is missing: {owner_ref}")
            if owner_path is not None:
                pending_ref = f"pending-run:{secrets.token_hex(12)}"
                with tracking._RecordLock(owner_path, require_sidecar=True):
                    owner_record = tracking.load_record(owner_path)
                    tracking.add_resource_claim(
                        owner_record,
                        tracking.ResourceClaim(
                            kind="workdir",
                            ref=pending_ref,
                            created_at=tracking._now_iso(),
                            state=obligations.ACTIVE,
                            note=f"pending resource creation: {cmd_str[:160]}",
                        ),
                        save=False,
                    )
                    tracking.save_record(owner_record, owner_path)
        except Exception as exc:
            output.err(f"run: cannot freeze owner ledger before creation: {exc}")
            return 1

    def _finish_pending(claim: tracking.ResourceClaim | None) -> bool:
        if owner_path is None or not pending_ref:
            return claim is not None
        if claim is None:
            return False
        try:
            with tracking._RecordLock(owner_path, require_sidecar=True):
                owner_record = tracking.load_record(owner_path)
                owner_record.resources = [
                    item for item in owner_record.resources if item.ref != pending_ref
                ]
                tracking.add_resource_claim(owner_record, claim, save=False)
                tracking.save_record(owner_record, owner_path)
            return True
        except Exception as exc:
            output.err(f"run: could not settle pending ownership: {exc}")
            return False

    try:
        proc = subprocess.run(
            cmd_str,
            shell=True,
            env=child_env,
            stdout=subprocess.PIPE,
            text=True,
        )
    except Exception as e:
        output.err(
            f"run: failed to execute inner command: {e}; pending ownership "
            f"{pending_ref or '(cross-machine)'} is retained"
        )
        return 1
    child_stdout = proc.stdout or ""
    sys.stdout.write(child_stdout)
    sys.stdout.flush()

    claim = _claim_from_run_output(child_stdout) if child_stdout.strip() else None
    if claim is None:
        output.err(
            f"run: resource command exited {proc.returncode} but no claim could "
            f"be parsed; pending ownership {pending_ref or '(cross-machine)'} is retained"
        )
        return proc.returncode or 1
    if not _finish_pending(claim):
        return 1
    output.err(f"run: journaled outbound claim {claim.ref} ({claim.kind}) on {owner_ref}")
    return proc.returncode


def _sync_one_record(
    rec: tracking.WorktreeRecord,
    repo: cfg.RepoConfig,
    active_paths: set[str],
) -> dict:
    """Fast-forward one worktree (FF-only, never an active session)."""
    if not (rec.worktree_path and Path(rec.worktree_path).exists()):
        return {"worktree_id": rec.worktree_id, "updated": False, "reason": "gone", "behind": 0}
    info = git_ops.classify_worktree(
        rec.worktree_path,
        rec.branch,
        fetch=False,
        remote=repo.remote,
        default_branch=repo.default_branch,
        active_paths=active_paths,
    )
    info = _apply_tracking_override(rec, info)
    if info.state == git_ops.WorktreeState.ACTIVE:
        return {
            "worktree_id": rec.worktree_id,
            "updated": False,
            "reason": "active",
            "behind": info.behind,
        }
    ff = git_ops.fast_forward_worktree(
        rec.worktree_path,
        remote=repo.remote,
        default_branch=repo.default_branch,
        do_fetch=False,
    )
    return {
        "worktree_id": rec.worktree_id,
        "updated": ff.updated,
        "reason": ff.reason,
        "behind": ff.behind,
    }


def sync_one(wt_id: str) -> dict:
    """Fast-forward a single worktree by ID; return a JSON-ready result dict."""
    config = cfg.load_config()
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    wt_id = _resolve_worktree_id(wt_id)
    yaml_path = tracking_path / f"{wt_id}.yaml"
    if not yaml_path.exists():
        return {"worktree_id": wt_id, "updated": False, "reason": "not-found", "behind": 0}
    rec = tracking.load_record(yaml_path)
    if git_ops.has_remote(repo.remote, cwd=repo.anchor):
        try:
            git_ops.fetch(repo.remote, cwd=repo.anchor)
        except Exception:
            pass
    session_ctx = sessions.scan_sessions_fast([rec])
    active_paths = _build_active_paths([rec], session_ctx)
    return _core_helper("_sync_one_record", _sync_one_record)(rec, repo, active_paths)


def finalize_one(wt_id: str) -> dict:
    """Finalize a single worktree by ID; return a JSON-ready result dict."""
    import contextlib
    import io

    try:
        config = cfg.load_config()
    except Exception as e:
        return {
            "worktree_id": wt_id,
            "success": False,
            "ok": False,
            "reason": str(e) or "config load failed",
        }
    wt_id = _resolve_worktree_id(wt_id)
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            success = fin.validate_and_finalize(wt_id, config)
    except Exception as e:
        return {
            "worktree_id": wt_id,
            "success": False,
            "ok": False,
            "reason": (str(e) or type(e).__name__),
        }
    status = "finalized"
    try:
        yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        if yaml_path.exists():
            status = tracking.load_record(yaml_path).status
    except Exception:
        pass
    return {"worktree_id": wt_id, "success": bool(success), "ok": bool(success), "status": status}


def cmd_sync(args: argparse.Namespace) -> int:
    """Fast-forward worktrees to their upstream default branch (FF-only)."""
    config = cfg.load_config()
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    as_json = getattr(args, "json", False)
    single = getattr(args, "worktree_id", None)

    if single:
        wt_id = _resolve_worktree_id(single)
        yaml_path = tracking_path / f"{wt_id}.yaml"
        if not yaml_path.exists():
            res = {"worktree_id": wt_id, "updated": False, "reason": "not-found", "behind": 0}
            if as_json:
                _json_output(res)
            else:
                print(f"{wt_id}: not-found")
            return 1
        records = [tracking.load_record(yaml_path)]
    else:
        records = tracking.list_records(
            tracking_path,
            status_filter="active",
            platform_filter=cfg.detect_platform(),
        )
        records = [
            r
            for r in records
            if r.kind not in tracking.MANAGED_KINDS
            and r.worktree_path
            and Path(r.worktree_path).exists()
        ]

    if git_ops.has_remote(repo.remote, cwd=repo.anchor):
        try:
            git_ops.fetch(repo.remote, cwd=repo.anchor)
        except Exception:
            pass
        else:
            for _synced_repo in {r.repo for r in records if r.repo}:
                tracking.record_repo_fetch_confirmed(_synced_repo)

    session_ctx = sessions.scan_sessions_fast(records)
    active_paths = _build_active_paths(records, session_ctx)

    results = [
        _core_helper("_sync_one_record", _sync_one_record)(rec, repo, active_paths)
        for rec in records
    ]

    if as_json:
        _json_output(results[0] if single else {"results": results})
    elif not results:
        print("No worktrees to sync.")
    else:
        for r in results:
            tag = f"updated ↑{r.get('behind', 0)}" if r.get("updated") else r.get("reason", "?")
            print(f"{r['worktree_id']}: {tag}")
    return 0
