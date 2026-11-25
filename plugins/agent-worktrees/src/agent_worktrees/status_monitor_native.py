"""Attribute and publish native mux status independently of Manager."""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
from pathlib import Path
from typing import Any

from agent_remote_driver.mux_observation import observe_psmux
from agent_remote_driver.mux_status import apply_psmux_status

from . import sessions, status_monitor_discovery

_last_session: str | None = None
LOG = logging.getLogger(__name__)


def discover_entries(
    live: dict[str, tuple[int, str]], current: set[str],
) -> dict[str, dict[str, Any]]:
    """Admit at most two uniquely tracked, pane-attributed native sessions."""
    global _last_session
    eligible = {
        name for name, (clients, incarnation) in live.items()
        if name not in current and name.startswith("wt-") and clients > 0
        and status_monitor_discovery._valid_native_incarnation(incarnation)
    }
    if not eligible:
        return {}
    candidates: dict[str, list[tuple[str, str, str]]] = {}
    try:
        for project, record in status_monitor_discovery._tracked_worktrees():
            name = sessions.mux_session_name(record.worktree_id)
            if name in eligible:
                candidates.setdefault(name, []).append((project, record.worktree_id, record.worktree_path))
    except (OSError, ValueError, TypeError) as error:
        LOG.warning("Native mux attribution unavailable: %s", type(error).__name__)
        return {}
    names = sorted(candidates)
    if not names:
        return {}
    start = next((index for index, name in enumerate(names)
                  if _last_session is None or name > _last_session), 0)
    entries: dict[str, dict[str, Any]] = {}
    for offset in range(min(2, len(names))):
        name = names[(start + offset) % len(names)]
        _last_session = name
        matches = candidates[name]
        if len(matches) != 1:
            continue
        project, worktree_id, path = matches[0]
        try:
            if not project or not path or not Path(path).is_dir():
                continue
            observed = observe_psmux(name)
            if (
                observed.status != "present" or observed.incarnation != live[name][1]
                or not any(
                    not pane.dead and pane.path
                    and os.path.normcase(os.path.realpath(pane.path))
                    == os.path.normcase(os.path.realpath(path))
                    for pane in observed.panes
                )
            ):
                continue
        except (OSError, ValueError, TypeError) as error:
            LOG.warning("Native mux candidate unavailable: %s", type(error).__name__)
            continue
        entries[name] = {
            "project": project, "worktree_id": worktree_id, "worktree_path": path,
            "mux_session": name, "session_incarnation": observed.incarnation, "live": True,
        }
    return entries


def push_status(payload: dict[str, Any]) -> dict[str, bool]:
    """Apply presentation only, fenced and read back by Driver Core."""
    values = dict(payload["values"])
    if "@aw_ctx" in values:
        values = {
            "status": "on", "status-interval": "15",
            "status-left-length": "100", "status-left": "#{@aw_ctx} ",
            "status-right-length": "150", "status-right": "#{@aw_seg} %H:%M ",
            **values,
        }
    return {"applied": apply_psmux_status(
        payload["mux_session"], payload["session_incarnation"], values,
    )}


def publish_session_status(
    *, entry: dict[str, Any], values: dict[str, str],
    published: dict[tuple[str, str], str] | None,
    before_publish: Callable[[], object],
) -> dict[str, bool]:
    """Coalesce the elected producer's values without claiming updater ownership."""
    session = entry["mux_session"]
    changed = {
        option: value for option, value in values.items()
        if option in {"@aw_ctx", "@aw_seg"}
        and (published is None or published.get((session, option)) != value)
    }
    if not changed:
        return {"applied": True, "context_published": False}
    before_publish()
    result = push_status({
        "project": entry.get("project"), "worktree_id": entry.get("worktree_id"),
        "mux_session": session, "session_incarnation": entry["session_incarnation"],
        "values": changed,
    })
    applied = result.get("applied") is True
    if applied and published is not None:
        for option, value in changed.items():
            published[(session, option)] = value
    return {"applied": applied, "context_published": applied and "@aw_ctx" in changed}
