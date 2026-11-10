"""Explicit paired repository sync, followed by worktree-local preparation."""

from __future__ import annotations

import argparse
import io
import sys
from collections.abc import Callable
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path

from . import config as cfg, git_ops, knowledge_plugins, launch_admission, output, sessions, state_root, tracking
from . import worktree_identity


def sync_pair(
    record: tracking.WorktreeRecord, *, on_progress: Callable[[str], None] | None = None,
) -> dict:
    results: list[dict] = []
    admission = ExitStack()
    plugin_state = "skipped"

    def report(message: str) -> None:
        if on_progress is not None:
            on_progress(message)

    def failed(reason: str, detail: str = "") -> dict:
        return {
            "worktree_id": record.worktree_id, "updated": False, "ok": False,
            "reason": reason, "behind": 0, "detail": detail,
            "pair_results": results, "plugins": plugin_state,
        }

    try:
        report("Resolving paired worktrees")
        config = cfg.load_project_config(record.repo)
        pair = state_root.resolve_pair(config, cwd=record.worktree_path)
        if pair.error or not pair.paired or not pair.current or not pair.sibling:
            return failed("pair-unavailable", pair.error or "paired checkout is unavailable")
        if pair.pair_kind != "worktree":
            return failed("pair-unavailable", "Sync requires two writable worktrees, not an anchor")
        entries = [pair.current, pair.sibling]
        harness = next(entry for entry in entries if entry.role == "harness")
        harness_config = config if harness.repo == record.repo else cfg.load_project_config(harness.repo)
        knowledge_plugins.resolve_pair(cwd=record.worktree_path, config=harness_config)
        records = [
            tracking.load_record(cfg.tracking_dir(entry.repo) / f"{entry.worktree_id}.yaml")
            for entry in entries
        ]
        configs = [
            config if entry.repo == record.repo else cfg.load_project_config(entry.repo)
            for entry in entries
        ]
        from .execution_spaces import require_record_mutation
        from .worktree_ops_cli import _apply_tracking_override, _build_active_paths

        report("Checking paired mutation authority")
        for member in sorted(records, key=lambda member: str(member.yaml_path)):
            admission.enter_context(launch_admission.guard(member.yaml_path))
        for member, owner in zip(records, configs, strict=True):
            require_record_mutation(member, owner)
        report("Observing paired worktree sessions")
        active = _build_active_paths(records, sessions.scan_sessions_fast(records))
        # Fetch and inspect both members before advancing either branch.
        for member, owner in zip(records, configs, strict=True):
            repo = owner.default_repo
            report(f"Fetching {member.pair_role} repository")
            if not repo.remote or not git_ops.has_remote(repo.remote, cwd=repo.anchor):
                return failed("remote-unavailable", f"{member.repo}: configured remote is unavailable")
            try:
                git_ops.fetch(repo.remote, cwd=repo.anchor, branch=repo.default_branch)
            except (OSError, RuntimeError, git_ops.GitError) as exc:
                return failed("fetch-failed", str(exc))
            tracking.record_repo_fetch_confirmed(member.repo)
            report(f"Inspecting {member.pair_role} worktree")
            raw = git_ops.classify_worktree(
                member.worktree_path, member.branch, fetch=False,
                remote=repo.remote, default_branch=repo.default_branch, active_paths=active,
            )
            if raw.state in {
                git_ops.WorktreeState.ACTIVE, git_ops.WorktreeState.GONE,
                git_ops.WorktreeState.UNKNOWN, git_ops.WorktreeState.ORPHAN,
                git_ops.WorktreeState.DIRTY,
            }:
                return failed("pair-sync-blocked", f"{member.repo}: {raw.state.value}")
            info = _apply_tracking_override(member, raw)
            reason = ""
            if info.state in {
                git_ops.WorktreeState.ACTIVE, git_ops.WorktreeState.GONE,
                git_ops.WorktreeState.UNKNOWN, git_ops.WorktreeState.ORPHAN,
            }:
                reason = info.state.value
            elif info.dirty:
                reason = "dirty"
            elif info.ahead:
                reason = "diverged" if info.behind else "ahead"
            elif info.current_branch is None or info.branch_drift:
                reason = "branch-drift"
            if reason:
                results.append({
                    "worktree_id": member.worktree_id, "updated": False,
                    "role": member.pair_role, "reason": reason, "behind": info.behind,
                })
                return failed("pair-sync-blocked", f"{member.repo}: {reason}")
        for member, owner in zip(records, configs, strict=True):
            repo = owner.default_repo
            report(f"Advancing {member.pair_role} worktree")
            result = git_ops.fast_forward_worktree(
                member.worktree_path, remote=repo.remote,
                default_branch=repo.default_branch, do_fetch=False,
            )
            results.append({
                "worktree_id": member.worktree_id, "updated": result.updated,
                "role": member.pair_role, "reason": result.reason, "behind": result.behind,
            })
            if result.reason not in {"updated", "up-to-date"} or result.fetch_error:
                return failed("pair-sync-blocked", f"{member.repo}: {result.reason}")

        from . import local_cache_refresh

        plugin_state = "failed"
        prior_project = cfg.active_project()
        try:
            cfg.set_active_project(harness.repo)
            with cfg.cached_load_config_scope() as cache:
                report("Reloading paired synchronization policy")
                cache.invalidate()
                refreshed = [cfg.load_project_config(member.repo) for member in records]
                for member, before, after in zip(records, configs, refreshed, strict=True):
                    old_target, new_target = before.default_repo, after.default_repo
                    if (old_target.anchor, old_target.remote, old_target.default_branch) != (
                        new_target.anchor, new_target.remote, new_target.default_branch,
                    ):
                        return failed("pair-sync-policy-changed", f"{member.repo}: synchronization target changed; retry Sync")
                harness_config = next(owner for member, owner in zip(records, refreshed, strict=True)
                                      if member.repo == harness.repo)
                report("Composing worktree plugins")
                knowledge_plugins.resolve_pair(cwd=harness.path, config=harness_config)
                composed = knowledge_plugins.compose_from_pair(cwd=harness.path, config=harness_config)
                if not composed.get("paired") or composed.get("action") not in {"composed", "disabled"}:
                    return failed("plugin-composition-failed", composed.get("pair_error") or "pair composition did not complete")
                report("Preparing worktree instructions")
                prepared = local_cache_refresh.prepare_for_launch(Path(harness.path), config=harness_config)
                if prepared.status not in {"ready", "skipped"}:
                    return failed("plugin-preparation-failed", prepared.detail)
        finally:
            cfg.set_active_project(prior_project)
        return {
            "worktree_id": record.worktree_id,
            "updated": any(result["updated"] for result in results),
            "ok": True,
            "reason": "updated" if any(result["updated"] for result in results) else "up-to-date",
            "behind": results[0]["behind"], "pair_results": results, "plugins": "ready",
        }
    except (OSError, RuntimeError, ValueError, KeyError, StopIteration, git_ops.GitError) as exc:
        return failed("pair-sync-failed", str(exc))
    finally:
        admission.close()


def cmd_sync(args: argparse.Namespace) -> int:
    if not getattr(args, "stream", False):
        return _cmd_sync(args)
    if not getattr(args, "json", False):
        print("sync: --stream requires --json", file=sys.stderr)
        return 2
    from .sync_progress import SyncProgress

    with SyncProgress() as progress:
        try:
            return _cmd_sync(args, progress=progress)
        except (OSError, RuntimeError, ValueError, git_ops.GitError) as error:
            return progress.finish({"ok": False, "reason": "sync-failed", "detail": str(error)})


def _cmd_sync(args: argparse.Namespace, *, progress=None) -> int:
    from . import worktree_ops_cli as operations

    config = cfg.load_config()
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    as_json = getattr(args, "json", False)
    single = getattr(args, "worktree_id", None)

    if single:
        if progress is None:
            wt_id = worktree_identity._resolve_worktree_id(single)
        else:
            diagnostic = io.StringIO()
            try:
                with redirect_stdout(diagnostic), redirect_stderr(diagnostic):
                    wt_id = worktree_identity._resolve_worktree_id(single)
            except SystemExit:
                return progress.finish({
                    "worktree_id": single, "ok": False, "reason": "worktree-id-rejected",
                    "detail": diagnostic.getvalue().strip() or "Worktree selection was refused",
                    "outcome": "not-started",
                })
        yaml_path = tracking_path / f"{wt_id}.yaml"
        if not yaml_path.exists():
            res = {"worktree_id": wt_id, "updated": False, "reason": "not-found", "behind": 0}
            if progress is not None:
                return progress.finish(res)
            if as_json:
                output._json_output(res)
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

    if progress is not None:
        progress.stage("Fetching repository")
    if git_ops.has_remote(repo.remote, cwd=repo.anchor):
        try:
            git_ops.fetch(repo.remote, cwd=repo.anchor)
        except Exception:
            pass
        else:
            for synced_repo in {r.repo for r in records if r.repo}:
                tracking.record_repo_fetch_confirmed(synced_repo)

    if progress is not None:
        progress.stage("Observing worktree sessions")
    session_ctx = sessions.scan_sessions_fast(records)
    active_paths = operations._build_active_paths(records, session_ctx)

    results = []
    for rec in records:
        try:
            if getattr(args, "paired", False) and (rec.pair_id or rec.pair_ref):
                result = sync_pair(
                    rec, **({"on_progress": progress.stage} if progress is not None else {}),
                )
            else:
                if progress is not None:
                    progress.stage("Inspecting and synchronizing worktree")
                result = operations._core_helper("_sync_one_record", operations._sync_one_record)(
                    rec, repo, active_paths,
                )
        except Exception as error:
            if progress is None:
                raise
            results.append({
                "worktree_id": rec.worktree_id, "ok": False, "reason": "sync-interrupted",
                "detail": str(error), "outcome": "unknown",
            })
            break
        results.append(result)

    if progress is not None:
        return progress.finish(results[0] if single else {"results": results})
    if as_json:
        output._json_output(results[0] if single else {"results": results})
    elif not results:
        print("No worktrees to sync.")
    else:
        for result in results:
            tag = f"updated ↑{result.get('behind', 0)}" if result.get("updated") else result.get("reason", "?")
            print(f"{result['worktree_id']}: {tag}")
    return 1 if any(result.get("ok") is False for result in results) else 0
