"""System-menu helpers used by the legacy ``resolve`` picker fallback."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import git_ops, sessions, tracking
from . import config as cfg
from .picker import ItemKind, MenuItem, pick


def _core():
    from . import __main__ as core

    return core


def _apply_tracking_override(*args, **kwargs):
    return _core()._apply_tracking_override(*args, **kwargs)


def _build_active_paths(*args, **kwargs):
    return _core()._build_active_paths(*args, **kwargs)


def _age_str(*args, **kwargs):
    return _core()._age_str(*args, **kwargs)


def _normalize_path(*args, **kwargs):
    return _core()._normalize_path(*args, **kwargs)


def _run_machine_menu(*args, **kwargs):
    return _core()._run_machine_menu(*args, **kwargs)


def cmd_cleanup(*args, **kwargs):
    return _core().cmd_cleanup(*args, **kwargs)


def cmd_remove_system(*args, **kwargs):
    return _core().cmd_remove_system(*args, **kwargs)


def _run_system_menu(config: cfg.Config, args: argparse.Namespace) -> int | None:
    """Show system menu and run the selected action."""
    system_items = [
        MenuItem(label="🧹 Cleanup worktrees", kind=ItemKind.ACTION, value="cleanup"),
        MenuItem(label="⬆ Update stale worktrees", kind=ItemKind.ACTION, value="update"),
        MenuItem(label="📊 Worktree status", kind=ItemKind.ACTION, value="status"),
        MenuItem(
            label="🛠 System worktrees (daemon-owned)",
            kind=ItemKind.ACTION,
            value="system-worktrees",
        ),
        MenuItem(label="", kind=ItemKind.SEPARATOR),
        MenuItem(label="↩ Back to picker", kind=ItemKind.ACTION, value="back"),
    ]

    result = pick(
        system_items,
        title=f"⚙ {config.repo_name.replace('-', ' ').title()} -- System Menu",
        subtitle="Use ↑↓, Enter select, Esc back",
        default=0,
    )

    if result.selected < 0:
        return None

    action = system_items[result.selected].value
    if action == "back":
        return None

    if action == "cleanup":
        return _system_cleanup(config)
    if action == "update":
        return _system_update(config)
    if action == "status":
        return _system_status(config)
    if action == "system-worktrees":
        return _system_worktrees_browse(config)
    return None


def _system_cleanup(config: cfg.Config) -> int | None:
    """Compact cleanup flow for the system menu -- picker-style UX."""
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(tracking_path)

    if not records:
        _system_pause("No tracked worktrees.")
        return None

    records = [record for record in records if record.kind not in tracking.MANAGED_KINDS]
    if not records:
        _system_pause("No tracked worktrees.")
        return None

    git_ops.fetch(repo.remote, cwd=repo.anchor)
    upstream = f"{repo.remote}/{repo.default_branch}"
    active_paths = _build_active_paths(records)

    cleanable: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
    unused: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []

    for record in records:
        if record.worktree_path and Path(record.worktree_path).exists():
            info = git_ops.classify_worktree(
                record.worktree_path,
                record.branch,
                fetch=False,
                remote=repo.remote,
                default_branch=repo.default_branch,
                active_paths=active_paths,
            )
            info = _apply_tracking_override(record, info)
        elif record.status == "finalized":
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
        else:
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE)

        if info.state == git_ops.WorktreeState.COMPLETED:
            if not record.has_live_pr():
                cleanable.append((record, info))
        elif info.state == git_ops.WorktreeState.GONE:
            if not record.has_live_pr() and (
                not record.branch
                or git_ops.is_branch_merged(
                    record.branch,
                    upstream,
                    cwd=repo.anchor,
                )
            ):
                cleanable.append((record, info))
        elif info.state == git_ops.WorktreeState.UNUSED:
            unused.append((record, info))

    if not cleanable and not unused:
        _system_pause("Nothing to clean -- all worktrees are active or have unmerged work.")
        return None

    confirm_items: list[MenuItem] = []
    if cleanable:
        confirm_items.append(
            MenuItem(
                label=f"🧹 Clean {len(cleanable)} completed worktree(s)",
                subtitle=", ".join(record.worktree_id[-4:] for record, _ in cleanable),
                kind=ItemKind.ACTION,
                value="clean",
            )
        )
    if unused:
        confirm_items.append(
            MenuItem(
                label=f"🧹 Also clean {len(unused)} unused worktree(s) (empty)",
                subtitle=", ".join(record.worktree_id[-4:] for record, _ in unused),
                kind=ItemKind.ACTION,
                value="clean-all",
            )
        )

    confirm_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
    confirm_items.append(MenuItem(label="↩ Cancel", kind=ItemKind.ACTION, value="cancel"))

    result = pick(
        confirm_items,
        title="🧹 Cleanup -- select action",
        subtitle="Use ↑↓, Enter select, Esc cancel",
        default=0,
    )

    if result.selected < 0:
        return None

    choice = confirm_items[result.selected].value
    if choice == "cancel":
        return None

    include_unused = choice == "clean-all"
    cleanup_args = argparse.Namespace(
        clean=True,
        include_unused=include_unused,
        max_age_days=None,
    )
    cmd_cleanup(cleanup_args)
    _system_pause("Cleanup complete.")
    return None


def _system_update(config: cfg.Config) -> int | None:
    """Fast-forward stale worktrees to the default branch (FF-only)."""
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(
        tracking_path,
        status_filter="active",
        platform_filter=cfg.detect_platform(),
    )
    records = [record for record in records if record.worktree_path and Path(record.worktree_path).exists()]
    records = [record for record in records if record.kind not in tracking.MANAGED_KINDS]

    if not records:
        _system_pause("No tracked worktrees.")
        return None

    if git_ops.has_remote(repo.remote, cwd=repo.anchor):
        try:
            git_ops.fetch(repo.remote, cwd=repo.anchor)
        except Exception:
            pass

    active_paths = _build_active_paths(records)
    eligible: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
    for record in records:
        info = git_ops.classify_worktree(
            record.worktree_path,
            record.branch,
            fetch=False,
            remote=repo.remote,
            default_branch=repo.default_branch,
            active_paths=active_paths,
        )
        info = _apply_tracking_override(record, info)
        if info.state == git_ops.WorktreeState.ACTIVE:
            continue
        if git_ops.can_fast_forward(info):
            eligible.append((record, info))

    if not eligible:
        _system_pause("All worktrees are up to date.")
        return None

    while True:
        update_items: list[MenuItem] = [
            MenuItem(
                label=f"⬆ Update all ({len(eligible)} eligible)",
                kind=ItemKind.ACTION,
                value="all",
            ),
            MenuItem(label="", kind=ItemKind.SEPARATOR),
        ]
        index_map: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
        for record, info in eligible:
            short_id = record.worktree_id[-4:] if len(record.worktree_id) > 4 else record.worktree_id
            update_items.append(
                MenuItem(
                    label=f"⬜ …{short_id}  ↓{info.behind}",
                    subtitle=_age_str(record.started_at) + " old",
                    kind=ItemKind.NORMAL,
                    value=len(index_map),
                )
            )
            index_map.append((record, info))
        update_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
        update_items.append(MenuItem(label="↩ Back", kind=ItemKind.ACTION, value="back"))

        result = pick(
            update_items,
            title=f"⬆ {config.repo_name.replace('-', ' ').title()} -- Update Worktrees",
            subtitle="Use ↑↓, Enter select, Esc back",
            default=0,
        )
        if result.selected < 0:
            return None
        choice = update_items[result.selected].value
        if choice == "back":
            return None

        if choice == "all":
            targets = list(eligible)
        else:
            targets = [index_map[choice]]  # type: ignore[index]

        updated = 0
        skipped = 0
        for record, _info in targets:
            ff = git_ops.fast_forward_worktree(
                record.worktree_path,
                remote=repo.remote,
                default_branch=repo.default_branch,
                do_fetch=False,
            )
            if ff.updated:
                updated += 1
            else:
                skipped += 1

        done_paths = {record.worktree_path for record, _ in targets}
        eligible = [
            (record, info) for record, info in eligible if record.worktree_path not in done_paths
        ]

        msg = f"Fast-forwarded {updated} worktree{'s' if updated != 1 else ''}"
        if skipped:
            msg += f", skipped {skipped}"
        if not eligible:
            _system_pause(msg + ". All up to date.")
            return None
        _system_pause(msg + ".")


def _system_status(config: cfg.Config) -> int | None:
    """Compact status view for the system menu."""
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(tracking_path)

    if not records:
        _system_pause("No tracked worktrees.")
        return None

    session_ctx = sessions.scan_sessions_fast(records)
    active_paths = _build_active_paths(records, session_ctx)

    status_items: list[MenuItem] = []
    state_icons = {
        "active": "🟢",
        "unused": "⬜",
        "completed": "✅",
        "wip": "🌳",
        "dirty": "🔴",
        "gone": "💀",
        "orphan": "❓",
    }

    for record in records:
        info = git_ops.classify_worktree(
            record.worktree_path,
            record.branch,
            fetch=True,
            remote=repo.remote,
            default_branch=repo.default_branch,
            active_paths=active_paths,
        )
        info = _apply_tracking_override(record, info)
        short_id = record.worktree_id[-4:]
        icon = state_icons.get(info.state.value, "·")
        age = _age_str(record.started_at)
        state_str = info.state.value

        label = f"{icon} …{short_id}  {state_str:<10} {age}"
        norm = _normalize_path(record.worktree_path)
        title = record.title if (record.title and record.title != "null") else None
        if not title and norm in session_ctx.latest_summary:
            title = session_ctx.latest_summary[norm]
        if not title and info.title:
            title = info.title
        subtitle = " ".join(title.split()) if title else None

        status_items.append(
            MenuItem(
                label=label,
                subtitle=subtitle,
                kind=ItemKind.DIMMED,
                value=None,
            )
        )

    status_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
    status_items.append(MenuItem(label="↩ Back", kind=ItemKind.ACTION, value="back"))

    pick(
        status_items,
        title=f"📊 {config.repo_name.replace('-', ' ').title()} -- Status",
        subtitle="Esc or Enter to return",
        default=len(status_items) - 1,
    )
    return None


def _system_pause(msg: str) -> None:
    """Display a short status message in a picker-style pause dialog."""
    pick(
        [MenuItem(label="OK", kind=ItemKind.ACTION, value="ok")],
        title=msg,
        subtitle="Press Enter or Esc to continue",
        default=0,
    )


def _system_worktrees_browse(config: cfg.Config) -> int | None:
    """Browse and force-remove daemon-owned worktrees from the system menu."""
    tracking_path = cfg.tracking_dir()
    records = [record for record in tracking.list_records(tracking_path) if record.is_picker_hidden]
    records = [record for record in records if record.repo == config.repo_name]

    if not records:
        _system_pause("No system worktrees.")
        return None

    active_paths = _build_active_paths(records)

    while True:
        records = [
            record
            for record in tracking.list_records(tracking_path)
            if record.is_picker_hidden and record.repo == config.repo_name
        ]
        if not records:
            _system_pause("No system worktrees remain.")
            return None

        items: list[MenuItem] = []
        for record in records:
            live = _normalize_path(record.worktree_path) in active_paths
            gone = not (record.worktree_path and Path(record.worktree_path).exists())
            owner = record.owner or "?"
            if live:
                tag = "live"
            elif gone:
                tag = "missing dir"
            else:
                tag = "likely leaked"
            items.append(
                MenuItem(
                    label=f"🛠 {owner} · {record.worktree_id}",
                    subtitle=f"{tag} · {_age_str(record.started_at)} · {record.worktree_path}",
                    kind=ItemKind.DIMMED if live else ItemKind.NORMAL,
                    value=record.worktree_id,
                )
            )
        items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
        items.append(MenuItem(label="↩ Back", kind=ItemKind.ACTION, value="back"))

        result = pick(
            items,
            title="🛠 System Worktrees -- daemon-owned",
            subtitle="Enter to force-remove a leaked one, Esc back",
            default=0,
        )
        if result.selected < 0:
            return None
        choice = items[result.selected].value
        if choice == "back":
            return None

        selected = next((record for record in records if record.worktree_id == choice), None)
        if selected is None:
            continue
        live = _normalize_path(selected.worktree_path) in active_paths
        warn = (
            "  ⚠ has a LIVE session -- removing may disrupt a running daemon" if live else ""
        )
        confirm = pick(
            [
                MenuItem(
                    label=f"🗑 Force-remove {selected.worktree_id}",
                    kind=ItemKind.ACTION,
                    value="yes",
                    subtitle=warn or None,
                ),
                MenuItem(label="↩ Cancel", kind=ItemKind.ACTION, value="no"),
            ],
            title="Force-remove system worktree?",
            subtitle="This deletes the git worktree + tracking record",
            default=1,
        )
        if confirm.selected != 0:
            continue

        rc = cmd_remove_system(argparse.Namespace(worktree_id=selected.worktree_id, json=False))
        _system_pause("Removed." if rc == 0 else "Remove failed (see logs).")
