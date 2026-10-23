"""Recover native attachments through engine attribution and Manager IPC."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import config as cfg
from . import installer, mux_link, mux_status_link, sessions, tracking

LOG = logging.getLogger(__name__)
_last_session: str | None = None
_retry_after: dict[tuple[str, str, str], float] = {}
_now = time.monotonic


def _valid_native_incarnation(value) -> bool:
    if not isinstance(value, str):
        return False
    session_id, separator, created = value.partition(":")
    return bool(
        separator and session_id.startswith("$")
        and session_id[1:].isdecimal() and created.isdecimal()
    )


def standalone_identity_path(registry_dir: Path, name: str) -> Path:
    return registry_dir / f".{name}.identity.json"


def write_standalone_registration(
    registry_dir: Path, name: str, path: str, incarnation: str | None,
) -> bool:
    if incarnation is not None and not _valid_native_incarnation(incarnation):
        LOG.warning("Refusing malformed native mux identity for %s", name)
        return False
    entries = [(registry_dir / name, path)]
    if incarnation:
        entries.append((standalone_identity_path(registry_dir, name), json.dumps({
            "version": 1, "path": path, "session_incarnation": incarnation,
        })))
    temporary = None
    try:
        registry_dir.mkdir(parents=True, exist_ok=True)
        for target, contents in entries:
            fd, temporary = tempfile.mkstemp(prefix=name + ".", suffix=".tmp", dir=registry_dir)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(contents)
            os.replace(temporary, target)
        return True
    except OSError as exc:
        LOG.warning("Standalone mux registration failed for %s: %s", name, exc)
        return False
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError as exc:
                LOG.warning("Standalone mux temporary cleanup failed for %s: %s", name, exc)


def _standalone_identity_current(registry_dir, name, path, incarnation) -> bool:
    if not _valid_native_incarnation(incarnation):
        LOG.warning("Refusing malformed native mux identity for %s", name)
        return False
    if registry_dir is None:
        from .status_monitor_runtime import _monitor_registry_dir

        registry_dir = _monitor_registry_dir()
    try:
        entry = json.loads(standalone_identity_path(registry_dir, name).read_text(encoding="utf-8"))
        if isinstance(entry, dict) and not _valid_native_incarnation(entry.get("session_incarnation")):
            LOG.warning("Refusing malformed persisted native mux identity for %s", name)
            return False
        return (
            isinstance(entry, dict) and type(entry.get("version")) is int and entry["version"] == 1
            and entry.get("session_incarnation") == incarnation
            and isinstance(entry.get("path"), str)
            and os.path.normcase(os.path.realpath(entry["path"]))
            == os.path.normcase(os.path.realpath(path))
        )
    except FileNotFoundError:
        return False
    except (OSError, ValueError, TypeError) as exc:
        LOG.warning("Standalone mux identity unavailable for %s: %s", name, exc)
        return False


def _tracked_worktrees():
    projects = installer.read_projects_registry().get("projects", {})
    if not isinstance(projects, dict):
        return
    for project in projects:
        for record in tracking.list_records(cfg.tracking_dir(project)):
            if record.status not in {"archived", "finalized"}:
                yield project, record


def discover_via_manager(payload: dict) -> dict | None:
    """None means Manager is not configured, not a failed admission."""
    return mux_status_link.push_discovery_via_daemon(payload)


def _register_standalone(name: str, path: str, incarnation: str, registry_dir) -> bool:
    from .status_monitor_runtime import _register_session_for_monitor

    return _register_session_for_monitor(
        name, path, session_incarnation=incarnation, registry_dir=registry_dir,
    )


def _initialize_standalone_presentation(mux_bin: str, name: str) -> bool:
    from .status_monitor_runtime import _monitor_mux_set

    options = {
        "status": "on", "status-interval": "15",
        "status-left-length": "100", "status-left": "#{@aw_ctx} ",
        "status-right-length": "150", "status-right": "#{@aw_seg} %H:%M ",
    }
    applied = [_monitor_mux_set(mux_bin, name, option, value) for option, value in options.items()]
    return all(applied)


def _session_path_matches(mux_bin: str, name: str, path: str) -> bool:
    try:
        result = subprocess.run(
            [mux_bin, "display-message", "-p", "-t",
             sessions._mux_named_session_target(name, mux_bin), "#{session_path}"],
            capture_output=True, text=True, timeout=1, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOG.warning("Connected mux path probe unavailable for %s: %s", name, exc)
        return False
    return (
        result.returncode == 0 and bool(result.stdout.strip())
        and os.path.normcase(os.path.realpath(result.stdout.strip()))
        == os.path.normcase(os.path.realpath(path))
    )


def _observed_incarnation(observation) -> str:
    if (
        isinstance(observation, tuple) and len(observation) == 2
        and _valid_native_incarnation(observation[1])
    ):
        return observation[1]
    return ""


def _current_mapping_names(
    observations, cache, registered_paths, registry_dir,
) -> tuple[set[str], set[str]]:
    entries = cache.snapshot().values() if cache is not None else []
    live_names = cache.live_session_names() if cache is not None else set()
    claimed = set()
    current = set()
    now = time.time()
    for entry in entries:
        name = entry.get("mux_session")
        if not isinstance(name, str):
            continue
        received_at = entry.get("received_at")
        if not (
            name in live_names and entry.get("live")
            and isinstance(received_at, (int, float))
            and now - received_at <= mux_link.MAPPING_STALE_AFTER_SECONDS
        ):
            continue
        claimed.add(name)
        if (
            name in observations and name in live_names and entry.get("live")
            and entry.get("worktree_path")
            and _observed_incarnation(observations[name])
            and (not entry.get("session_incarnation")
                 or entry["session_incarnation"] == _observed_incarnation(observations[name]))
        ):
            current.add(name)
    if registered_paths is None:
        return current, claimed
    for name, path in list(registered_paths.items()):
        if name not in observations:
            continue
        if name in claimed:
            continue  # The normal managed-lane prune removes both registration files.
        incarnation = _observed_incarnation(observations[name])
        if path and incarnation and _standalone_identity_current(
            registry_dir, name, path, incarnation,
        ):
            current.add(name)
        else:
            registered_paths.pop(name, None)
    return current, claimed


def discover_connected_worktrees(
    live: dict[str, tuple[int, str]] | None,
    mux_bin: str,
    managed_mux_cache,
    *,
    registered_paths: dict[str, str] | None = None,
    registration_dir: Path | None = None,
    before_discover=None,
) -> dict[str, str]:
    """Return newly attributable paths, retaining unknown and incumbent fences.

    Names must exactly match a unique local tracking record's canonical mux
    name. Neither a foreign shell's cwd nor the ``wt-`` prefix is attribution.
    Manager absence leaves the existing standalone presentation path available;
    a Manager refusal/unreachable endpoint never authorizes direct writes.
    Current served paths require no discovery work. Unresolved incarnations
    retry at most every 20 seconds, with two candidates per round-robin pass.
    """
    global _last_session
    if live is None:
        return {}
    connected = {}
    for name, observation in (live or {}).items():
        if not isinstance(observation, tuple) or len(observation) != 2:
            continue
        count, incarnation = observation
        if (
            isinstance(count, int) and not isinstance(count, bool) and count > 0
            and name.startswith("wt-")
        ):
            if not _valid_native_incarnation(incarnation):
                LOG.warning("Refusing malformed connected native mux identity for %s", name)
                continue
            connected[name] = (count, incarnation)
    for key in list(_retry_after):
        binary, name, incarnation = key
        observation = live.get(name)
        observed = _observed_incarnation(observation)
        changed = observed and observed != incarnation
        if binary == mux_bin and (name not in live or changed):
            _retry_after.pop(key)
    legacy_paths = dict(registered_paths or {})
    known, claimed = _current_mapping_names(
        live, managed_mux_cache, registered_paths, registration_dir,
    )
    eligible = {
        name: observation for name, observation in connected.items()
        if name not in known
        and _now() >= _retry_after.get((mux_bin, name, observation[1]), 0.0)
    }
    if not eligible:
        return {}
    candidates: dict[str, list[tuple[str | None, str, str]]] = {}
    try:
        for project, record in _tracked_worktrees():
            name = sessions.mux_session_name(record.worktree_id)
            if name in eligible:
                candidates.setdefault(name, []).append((project, record.worktree_id, record.worktree_path))
    except (OSError, ValueError, TypeError) as exc:
        LOG.warning("Connected mux attribution unavailable: %s", exc)
        for name, (_, incarnation) in eligible.items():
            _retry_after[(mux_bin, name, incarnation)] = _now() + 20.0
        return {}
    for name, path in legacy_paths.items():
        if name in eligible and name not in claimed and name not in candidates:
            candidates[name] = [(None, "", path)]
    for name in eligible.keys() - candidates.keys():
        _retry_after[(mux_bin, name, eligible[name][1])] = _now() + 20.0
    discovered = {}
    names = sorted(candidates)
    start = next((i for i, name in enumerate(names)
                  if _last_session is None or name > _last_session), 0)
    # Match the existing attachment observer's bounded round-robin discipline.
    for offset in range(min(2, len(names))):
        name = names[(start + offset) % len(names)]
        _last_session = name
        count, incarnation = connected[name]
        try:
            matches = candidates[name]
            if len(matches) != 1:
                LOG.warning("Connected mux %s has ambiguous worktree attribution; discovery deferred", name)
                continue
            project, worktree_id, path = matches[0]
            if not path or not Path(path).is_dir() or not _session_path_matches(mux_bin, name, path):
                continue
            payload = {
                "project": project,
                "worktree_id": worktree_id,
                "worktree_path": path,
                "mux_session": name,
                "mux_bin": mux_bin,
                "session_incarnation": incarnation,
                "attached_clients": count,
            }
            if before_discover is not None:
                before_discover()
            result = discover_via_manager(payload) if project is not None else None
            if result is None:
                if not _initialize_standalone_presentation(mux_bin, name):
                    LOG.warning("Connected mux %s session format initialization failed", name)
                    continue
                if not _register_standalone(name, path, incarnation, registration_dir):
                    LOG.warning("Connected mux %s standalone registration failed", name)
                    continue
                discovered[name] = path
            else:
                if result.get("applied") and managed_mux_cache is not None:
                    result = managed_mux_cache.apply_observation(result["entry"])
                if not result.get("applied"):
                    LOG.warning("Connected mux %s discovery deferred: %s", name, result.get("reason"))
        finally:
            _retry_after[(mux_bin, name, incarnation)] = _now() + 20.0
    return discovered
