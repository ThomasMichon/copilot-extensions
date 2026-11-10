"""Explicit paired repository sync, followed by worktree-local preparation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from . import config as cfg, git_ops, knowledge_plugins, launch_admission, sessions, state_root, tracking


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
