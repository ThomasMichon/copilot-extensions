"""Picker-facing helpers for the ``resolve`` command family."""

from __future__ import annotations

import argparse
import atexit
import dataclasses
from pathlib import Path

from . import git_ops, reciprocal_presentation, sessions, tracking
from . import config as cfg
from .picker import ItemKind, MenuItem, pick


def _core():
    from . import __main__ as core

    return core


def _age_str(*args, **kwargs):
    return _core()._age_str(*args, **kwargs)


def _activity_age_str(*args, **kwargs):
    return _core()._activity_age_str(*args, **kwargs)


def _apply_tracking_override(*args, **kwargs):
    return _core()._apply_tracking_override(*args, **kwargs)


def _build_active_paths(*args, **kwargs):
    return _core()._build_active_paths(*args, **kwargs)


def _controller_findings(*args, **kwargs):
    return _core()._controller_findings(*args, **kwargs)


def _emit_plan(*args, **kwargs):
    return _core()._emit_plan(*args, **kwargs)


def _epoch_or_zero(*args, **kwargs):
    return _core()._epoch_or_zero(*args, **kwargs)


def _ensure_status_monitor(*args, **kwargs):
    return _core()._ensure_status_monitor(*args, **kwargs)


def _exec_worktree_manager(*args, **kwargs):
    return _core()._exec_worktree_manager(*args, **kwargs)


def _heal_stale_anchor_if_self_missing(*args, **kwargs):
    return _core()._heal_stale_anchor_if_self_missing(*args, **kwargs)


def _load_remote_machines(*args, **kwargs):
    return _core()._load_remote_machines(*args, **kwargs)


def _normalize_path(*args, **kwargs):
    return _core()._normalize_path(*args, **kwargs)


def _picker_profile_choice(*args, **kwargs):
    return _core()._picker_profile_choice(*args, **kwargs)


def _resolve_base_repo(*args, **kwargs):
    return _core()._resolve_base_repo(*args, **kwargs)


def _resolve_new(*args, **kwargs):
    return _core()._resolve_new(*args, **kwargs)


def _resolve_resume(*args, **kwargs):
    return _core()._resolve_resume(*args, **kwargs)


def _resolve_ssh_alias(*args, **kwargs):
    return _core()._resolve_ssh_alias(*args, **kwargs)


def _run_system_menu(*args, **kwargs):
    return _core()._run_system_menu(*args, **kwargs)


def _status_monitor_enabled(*args, **kwargs):
    return _core()._status_monitor_enabled(*args, **kwargs)


def _sync_status_tag(*args, **kwargs):
    return _core()._sync_status_tag(*args, **kwargs)


def _usable_worktree_manager(*args, **kwargs):
    return _core()._usable_worktree_manager(*args, **kwargs)


@dataclasses.dataclass(slots=True)
class ResolvePickerContext:
    """Explicit shared state for the legacy picker fallback."""

    config: cfg.Config
    args: argparse.Namespace
    tracking_path: Path
    current_platform: str


def _run_picker_housekeeping() -> None:
    """Run each post-refresh sweep independently; one failure never stops peers."""
    from .launch_trace import append_launch_event

    append_launch_event("housekeeping_start")
    for action in (
        _core().reap_orphan_mux_sessions,
        _core()._sweep_managed_on_exit,
        _core()._sweep_launcher_shells_on_exit,
        _core()._sweep_finished_sessions_on_cadence,
    ):
        try:
            action()
        except Exception as exc:
            append_launch_event(
                "housekeeping_error",
                step=action.__name__,
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )


def _run_new_picker(config: cfg.Config | None, args) -> int:
    """Compatibility shim after the bundled picker retired."""
    mgr = _usable_worktree_manager()
    project = None
    try:
        project = cfg.active_project()
    except Exception:
        project = None
    if mgr:
        return _exec_worktree_manager(mgr, project)
    return _core().cmd_manager_install_trigger(project)


def _start_picker_monitor_root():
    """Register either Picker implementation as a resident liveness root."""
    if not _status_monitor_enabled():
        return None
    try:
        from . import monitor_roots

        root = monitor_roots.PickerHeartbeat(
            cfg.project_name(), ensure_monitor=_ensure_status_monitor
        )
        if not root.start():
            return None
        atexit.register(root.close)
        return root
    except Exception:
        return None


def _run_machine_menu(config: cfg.Config) -> int | None:
    """Show the remote machines sub-menu."""
    remote_machines = _load_remote_machines(config)
    if not remote_machines:
        return None

    machine_items: list[MenuItem] = []
    machine_values: list[tuple[cfg.MachineEntry, cfg.SSHEnvironment]] = []

    for entry, envs in remote_machines:
        if len(envs) == 1:
            ssh_env = envs[0]
            subtitle = f"{entry.environment} -- {entry.role}" if entry.role else entry.environment
            machine_items.append(
                MenuItem(
                    label=f"🖥 {entry.display_name}",
                    subtitle=subtitle,
                    kind=ItemKind.NORMAL,
                    value=len(machine_values),
                )
            )
            machine_values.append((entry, ssh_env))
        else:
            for ssh_env in envs:
                env_label = ssh_env.name.upper() if ssh_env.name else ssh_env.alias
                shell_tag = f" ({ssh_env.shell})" if ssh_env.shell else ""
                machine_items.append(
                    MenuItem(
                        label=f"🖥 {entry.display_name} ({env_label})",
                        subtitle=(
                            f"{ssh_env.alias}{shell_tag} -- {entry.role}"
                            if entry.role
                            else ssh_env.alias + shell_tag
                        ),
                        kind=ItemKind.NORMAL,
                        value=len(machine_values),
                    )
                )
                machine_values.append((entry, ssh_env))

    machine_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
    machine_items.append(MenuItem(label="↩ Back to picker", kind=ItemKind.ACTION, value=-1))

    result = pick(
        machine_items,
        title=f"🖥 {config.repo_name.replace('-', ' ').title()} -- Other Machines",
        subtitle="Use ↑↓, Enter to connect, Esc back",
        default=0,
    )

    if result.selected < 0:
        return None

    value = machine_items[result.selected].value
    if value == -1:
        return None

    entry, ssh_env = machine_values[value]
    project = cfg.project_name()
    print(f"   Connecting to {entry.display_name} via {ssh_env.alias}...")
    _emit_plan(
        {
            "action": "remote",
            "ssh_alias": ssh_env.alias,
            "remote_command": project,
            "machine": entry.key,
            "display_name": entry.display_name,
        }
    )
    return 0


def run_legacy_picker(context: ResolvePickerContext) -> int:
    """Run the legacy ANSI picker fallback for ``resolve``."""
    config = context.config
    args = context.args
    tracking_path = context.tracking_path
    current_platform = context.current_platform
    repo = config.default_repo

    while True:
        records = tracking.list_records(
            tracking_path,
            status_filter="active",
            platform_filter=current_platform,
        )
        complete_records = tracking.list_records(
            tracking_path,
            status_filter="complete",
            platform_filter=current_platform,
        )
        for record in complete_records:
            if record.kind not in tracking.MANAGED_KINDS:
                tracking.update_status(record, "active")
        records = records + complete_records

        finalized_records = tracking.list_records(
            tracking_path,
            status_filter="finalized",
            platform_filter=current_platform,
        )
        finalized_still_present = [
            record for record in finalized_records if Path(record.worktree_path).exists()
        ]
        records = records + finalized_still_present

        pushed_records = tracking.list_records(
            tracking_path,
            status_filter="pushed",
            platform_filter=current_platform,
        )
        pushed_still_present = [
            record for record in pushed_records if Path(record.worktree_path).exists()
        ]
        records = records + pushed_still_present

        records = [
            record
            for record in records
            if Path(record.worktree_path).exists()
            and (Path(record.worktree_path) / ".git").exists()
            and not record.is_picker_hidden
        ]

        session_ctx = sessions.scan_sessions_fast(records)
        active_paths = _build_active_paths(records, session_ctx)

        classified: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
        for record in records:
            info = git_ops.classify_worktree(
                record.worktree_path,
                record.branch,
                remote=repo.remote,
                default_branch=repo.default_branch,
                active_paths=active_paths,
            )
            info = _apply_tracking_override(record, info)
            classified.append((record, info))

        active_wts: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
        recent_wts: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
        unused_wts: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
        completed_wts: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []

        for record, info in classified:
            if info.state == git_ops.WorktreeState.ACTIVE:
                active_wts.append((record, info))
            elif info.state == git_ops.WorktreeState.UNUSED:
                unused_wts.append((record, info))
            elif info.state == git_ops.WorktreeState.COMPLETED:
                completed_wts.append((record, info))
            else:
                recent_wts.append((record, info))

        def _bucket_sort_key(
            pair: tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo],
            session_ctx: sessions.SessionContext = session_ctx,
        ) -> float:
            record, _info = pair
            norm = _normalize_path(record.worktree_path)
            iso = session_ctx.last_activity.get(norm) or record.started_at or ""
            return _epoch_or_zero(iso)

        for bucket in (active_wts, recent_wts, unused_wts, completed_wts):
            bucket.sort(key=_bucket_sort_key, reverse=True)

        menu_items: list[MenuItem] = []
        reciprocal_relations = {
            record.worktree_id: reciprocal_presentation.derive(
                record, _controller_findings(record)
            )
            for record, _info in classified
        }

        def _wt_label(
            record: tracking.WorktreeRecord,
            info: git_ops.WorktreeStateInfo,
            icon: str,
            session_ctx: sessions.SessionContext = session_ctx,
        ) -> str:
            age = _age_str(record.started_at)
            resume = f", {record.resume_count} resumes" if record.resume_count > 0 else ""
            norm = _normalize_path(record.worktree_path)
            sessions_list = session_ctx.active_sessions.get(norm, [])
            tag = ""
            if len(sessions_list) > 1:
                tag = f" 🟢 {len(sessions_list)} sessions"
            elif len(sessions_list) == 1:
                tag = " 🟢 in session"

            drift_tag = ""
            if info.branch_drift and info.current_branch:
                drift_tag = f" ⚠ {info.current_branch}"

            sync_tag = _sync_status_tag(info)
            state_tag = (
                f" [{info.state.value}]"
                if info.state in (git_ops.WorktreeState.UNUSED, git_ops.WorktreeState.COMPLETED)
                else ""
            )
            short_id = (
                record.worktree_id[-4:] if len(record.worktree_id) > 4 else record.worktree_id
            )
            relation = reciprocal_presentation.short_label(
                reciprocal_relations[record.worktree_id]
            )
            relation_tag = f" [{relation}]" if relation else ""
            return (
                f"{icon} …{short_id}  ({age}{resume}){tag}{drift_tag}"
                f"{sync_tag}{state_tag}{relation_tag}"
            )

        def _wt_subtitle(
            record: tracking.WorktreeRecord,
            info: git_ops.WorktreeStateInfo,
            session_ctx: sessions.SessionContext = session_ctx,
        ) -> str | None:
            norm = _normalize_path(record.worktree_path)
            turns = session_ctx.turn_count.get(norm, 0)
            pct = session_ctx.context_pct.get(norm)
            age = _activity_age_str(session_ctx.last_activity.get(norm, ""))

            meta: list[str] = []
            if turns > 0:
                meta.append(f"{turns} turn{'s' if turns != 1 else ''}")
            if pct is not None:
                meta.append(f"{pct}% ctx")
            if age:
                meta.append(age)
            meta_tag = f" ({' · '.join(meta)})" if meta else ""

            title = ""
            if record.title and record.title != "null":
                title = record.title
            elif norm in session_ctx.latest_summary:
                title = session_ctx.latest_summary[norm]
            elif info.title:
                title = info.title
            if title:
                return " ".join(title.split()) + meta_tag
            count = session_ctx.session_count.get(norm, 0)
            if count > 0:
                parts = [f"{count} session{'s' if count != 1 else ''}"]
                parts.extend(meta)
                return f"({' · '.join(parts)})"
            return meta_tag.strip() or None

        for record, info in active_wts:
            menu_items.append(
                MenuItem(
                    label=_wt_label(record, info, "🟢"),
                    subtitle=_wt_subtitle(record, info),
                    kind=ItemKind.NORMAL,
                    value=("worktree", record),
                )
            )

        if active_wts:
            menu_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))

        new_idx = len(menu_items)
        menu_items.append(MenuItem(label="✨ New worktree", kind=ItemKind.ACTION, value=("new", None)))

        remote_machines = _load_remote_machines(config)
        if remote_machines:
            menu_items.append(
                MenuItem(label="🖥 Other machines  ▸", kind=ItemKind.ACTION, value=("machines", None))
            )

        menu_items.append(
            MenuItem(
                label="📂 Base repo (no worktree)",
                kind=ItemKind.ACTION,
                value=("base", None),
            )
        )

        if recent_wts:
            menu_items.append(MenuItem(label="─── recent ─────────────────────", kind=ItemKind.SEPARATOR))
        for record, info in recent_wts:
            menu_items.append(
                MenuItem(
                    label=_wt_label(record, info, "🌳"),
                    subtitle=_wt_subtitle(record, info),
                    kind=ItemKind.NORMAL,
                    value=("worktree", record),
                )
            )

        if unused_wts:
            menu_items.append(MenuItem(label="─── unused ─────────────────────", kind=ItemKind.SEPARATOR))
            for record, info in unused_wts:
                menu_items.append(
                    MenuItem(
                        label=_wt_label(record, info, "⬜"),
                        subtitle=_wt_subtitle(record, info),
                        kind=ItemKind.DIMMED,
                        value=("worktree", record),
                    )
                )

        if completed_wts:
            menu_items.append(MenuItem(label="─── completed ──────────────────", kind=ItemKind.SEPARATOR))
            for record, info in completed_wts:
                menu_items.append(
                    MenuItem(
                        label=_wt_label(record, info, "✅"),
                        subtitle=_wt_subtitle(record, info),
                        kind=ItemKind.DIMMED,
                        value=("worktree", record),
                    )
                )

        menu_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
        menu_items.append(MenuItem(label="⚙ System menu", kind=ItemKind.ACTION, value=("system", None)))

        profiles = config.copilot_profiles or [cfg.DEFAULT_PROFILE]
        assignment_armed = bool(
            config.profile_assignment
            and config.profile_assignment.armed
            and not getattr(args, "profile", None)
        )
        profile_labels = [profile.label for profile in profiles]
        if assignment_armed:
            profile_labels.insert(0, "Balanced assignment")

        profile_default = 0
        requested_profile = getattr(args, "profile", None)
        if requested_profile:
            for index, profile in enumerate(profiles):
                if profile.name == requested_profile:
                    profile_default = index
                    break

        result = pick(
            menu_items,
            title=f"🌳 {config.repo_name.replace('-', ' ').title()} -- Worktree Picker",
            subtitle="Use ↑↓, Enter select, : system menu, Esc cancel",
            default=new_idx,
            profile_labels=profile_labels if len(profiles) > 1 else None,
            profile_default=profile_default,
        )

        if result.command == "system":
            rc = _run_system_menu(config, args)
            if rc is not None:
                return rc
            continue

        if result.selected < 0:
            print("Cancelled.")
            _emit_plan({"action": "none", "exit_code": 0})
            return 0

        selected_profile, explicit_profile = _picker_profile_choice(
            profiles,
            assignment_armed=assignment_armed,
            profile_idx=result.profile_idx,
        )
        action, value = menu_items[result.selected].value

        if action == "system":
            rc = _run_system_menu(config, args)
            if rc is not None:
                return rc
            continue

        if selected_profile.name != "cloud":
            print(f"   Backend: {selected_profile.label}")

        if action == "base":
            return _resolve_base_repo(config, args, profile=selected_profile)

        if action == "remote":
            entry = value
            ssh_alias = _resolve_ssh_alias(entry)
            project = cfg.project_name()
            print(f"   Connecting to {entry.display_name} via {ssh_alias}...")
            _emit_plan(
                {
                    "action": "remote",
                    "ssh_alias": ssh_alias,
                    "remote_command": project,
                    "machine": entry.key,
                    "display_name": entry.display_name,
                }
            )
            return 0

        if action == "machines":
            result_machine = _run_machine_menu(config)
            if result_machine is not None:
                return result_machine
            continue

        if action == "worktree":
            record = value
            return _resolve_resume(
                record,
                config,
                args,
                profile=selected_profile,
                profile_is_explicit=explicit_profile is not None,
            )

        return _resolve_new(
            config,
            args,
            profile=selected_profile,
            profile_is_explicit=explicit_profile is not None,
        )
