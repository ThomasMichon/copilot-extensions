"""``unregister`` -- project-scoped deregistration, split out of
``installation_cli`` to keep that module under its line-count cap.

See ThomasMichon/copilot-extensions#4520: the only prior "erase this
project's registration" path was ``uninstall --remove-config``, which
deletes the SHARED ``~/.agent-worktrees`` registry root used by every
adopted project on the machine -- the wrong tool for deregistering a single
project. ``unregister`` removes only this project's own ``projects.yaml``
entry, its ``repos.yaml`` entry (or downgrades it, with
``--keep-repo-entry``), and its own per-project binstub -- never the shared
runtime, never another project's registration.
"""

from __future__ import annotations

import argparse

from . import config as cfg
from . import installer as inst
from . import output


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "unregister",
        help="Deregister this project (projects.yaml + repos.yaml + binstub) -- never touches shared runtime state",
        description=(
            "Remove ONLY this project's registration lifecycle: its "
            "projects.yaml entry, its repos.yaml entry (or, with "
            "--keep-repo-entry, downgrade that entry to a catalogued "
            "'reference' repo instead of removing it), and its own "
            "per-project binstub. Never touches the shared "
            "~/.agent-worktrees runtime (venv/lib/bin) or shared config "
            "(config.yaml, accounts.yaml), and never touches any OTHER "
            "project's registration. Use this to deregister a stale/broken "
            "project stub; use 'uninstall --remove-config' only for a "
            "genuine full-machine teardown of the shared runtime."
        ),
    )
    p.add_argument(
        "--keep-repo-entry",
        action="store_true",
        help=(
            "Keep the repos.yaml entry but downgrade its class to "
            "'reference' (catalogued, un-adopted) instead of removing it "
            "outright"
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Proceed even if this project still has live worktrees, "
            "tracked sessions, or an open PR"
        ),
    )


def _unregister_blockers(project: str) -> list[str]:
    """Best-effort, local-only signals of in-flight work for ``project``.

    Reads only this project's own tracking records (no network calls) --
    live worktrees, tracked sessions, and locally-known open PR state.
    """
    blockers: list[str] = []
    try:
        tracking_path = cfg.tracking_dir()
    except Exception:
        return blockers

    from . import tracking as _tracking

    try:
        records = _tracking.list_records(tracking_path, status_filter="active")
    except Exception:
        records = []
    if records:
        blockers.append(f"{len(records)} live worktree(s)")

    tracked_sessions = sum(len(r.sessions or []) for r in records)
    if tracked_sessions:
        blockers.append(f"{tracked_sessions} tracked session(s)")

    open_prs = sum(
        1
        for r in records
        for pr in (r.prs or [])
        if pr.state in ("creating", "open")
    )
    if open_prs:
        blockers.append(f"{open_prs} open PR(s)")

    return blockers


def cmd_unregister(args: argparse.Namespace) -> int:
    """Deregister a project's adoption lifecycle -- never the shared runtime."""
    project = cfg.project_name()
    output.header(f"Unregistering project: {project}")

    blockers = _unregister_blockers(project)
    if blockers and not args.force:
        output.err(
            f"Refusing to unregister '{project}': it still has "
            f"{', '.join(blockers)}. Clean these up first, or pass --force "
            "to unregister anyway."
        )
        return 1
    if blockers:
        output.warn(
            f"Unregistering '{project}' with outstanding "
            f"{', '.join(blockers)} (--force)"
        )

    try:
        for path in inst.remove_project_binstub(project):
            output.changed(f"Removed binstub: {path}")
    except inst.BinstubOwnershipError as exc:
        output.warn(str(exc))

    registry = inst.read_projects_registry()
    if project in registry.get("projects", {}):
        del registry["projects"][project]
        inst.write_projects_registry(registry)
        output.changed(f"Removed '{project}' from projects.yaml")
    else:
        output.skipped(f"'{project}' was not present in projects.yaml")

    from . import repos as _repos

    if args.keep_repo_entry:
        entry = _repos.find_repo(project)
        if entry is not None:
            plat = cfg.detect_platform()
            path = entry.local_path(plat)
            if path:
                _repos.add_repo(
                    project,
                    path,
                    repo_class="reference",
                    remote=entry.remote,
                    default_branch=entry.default_branch,
                    agent=False,
                    plat=plat,
                )
                output.changed(
                    f"Downgraded '{project}' repos.yaml entry to class "
                    "'reference' (catalogued, un-adopted)"
                )
            else:
                output.warn(
                    f"Could not resolve '{project}'s local path for this "
                    "platform -- leaving repos.yaml entry as-is"
                )
        else:
            output.skipped(f"'{project}' had no repos.yaml entry")
    else:
        if not _repos.remove_repo(project):
            output.skipped(f"'{project}' was not present in repos.yaml")

    output.ok(f"Project '{project}' unregistered")
    print(
        "    Shared runtime (~/.agent-worktrees) and other projects' "
        "registrations were not touched."
    )
    print(
        f"    This project's own state directory ({cfg.project_dir(project)}) "
        f"was preserved -- remove it by hand, or run 'uninstall --project "
        f"{project} --remove-config --yes', if no longer needed."
    )
    return 0
