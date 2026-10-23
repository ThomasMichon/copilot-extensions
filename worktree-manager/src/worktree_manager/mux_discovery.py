"""Native connected-session admission at the existing Manager IPC boundary."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime

from agent_procutil import no_window_kwargs

from .mux_child_process import run as run_mux_child
from .mux_mapping_registry import MuxMappingRegistry

KIND = "mux-discover-v1"
LOG = logging.getLogger(__name__)


def _valid_native_incarnation(value) -> bool:
    if not isinstance(value, str):
        return False
    session_id, separator, created = value.partition(":")
    return bool(
        separator and session_id.startswith("$")
        and session_id[1:].isdecimal() and created.isdecimal()
    )


def parse_rendered_at(value: str) -> float | None:
    """Best-effort ISO-8601 -> epoch-seconds parse for ordering renders.

    An unparseable render retains the existing unorderable/not-older contract;
    this fence rejects old renders, not an additional class of new requests.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def handle_discovery(
    registry: MuxMappingRegistry, payload: dict, runtime=None, handler_tracker=None,
) -> dict:
    from .mux_daemon_live import _mapping_to_observation

    if runtime is not None and not runtime.accepting_requests():
        return {"applied": False, "reason": "draining"}
    if handler_tracker is not None:
        handler_tracker.enter()
    try:
        result = discover_mapping(registry, payload)
    finally:
        if handler_tracker is not None:
            handler_tracker.exit()
    if result.get("applied"):
        result["entry"] = _mapping_to_observation(result["entry"])
    return result


def status_key_content(payload: dict, values: dict) -> dict:
    """Different mapping identities must not coalesce onto an earlier render."""
    return {"values": values, **{
        field: payload[field] for field in (
            "mapping_revision", "mux_session", "session_incarnation",
        ) if field in payload
    }}


def status_fence_failure(entry: dict, payload: dict, *, registry=None) -> dict | None:
    if any(
        field in payload and payload[field] != entry.get(field)
        for field in ("mapping_revision", "mux_session", "session_incarnation")
    ):
        return {"applied": False, "reason": "stale-mapping"}
    if entry.get("native_session_incarnation"):
        confirmed = confirm_native_session(entry, connected_only=False)
        if not confirmed.get("applied"):
            if confirmed.get("reason") == "session-absent" and registry is not None:
                return retire_missing_mapping(registry, entry)
            return confirmed
    return None


def retire_missing_mapping(registry: MuxMappingRegistry, entry: dict) -> dict:
    from .mux_daemon_live import publish_live_observation

    result = registry.remove(
        entry["project"], entry["worktree_id"],
        mapping_revision=entry["mapping_revision"], mux_session=entry["mux_session"],
        session_incarnation=entry.get("session_incarnation"),
    )
    if result.get("applied"):
        current = registry.get(entry["project"], entry["worktree_id"])
        if current is not None and not current["live"] and all(
            current.get(field) == entry.get(field)
            for field in ("mapping_revision", "mux_session", "session_incarnation")
        ):
            publish_live_observation(current, ensure_monitor=False)
    return {"applied": False, "reason": "not-live"}


def initial_status_values(entry: dict, values: dict) -> dict:
    """Use the launcher's cached-option formats through the existing status writer."""
    if not entry.get("native_session_incarnation") or entry.get("last_status_rendered_at"):
        return values
    return {
        "status": "on", "status-interval": "15",
        "status-left-length": "100", "status-left": "#{@aw_ctx} ",
        "status-right-length": "150", "status-right": "#{@aw_seg} %H:%M ",
        **values,
    }


def discover_mapping(registry: MuxMappingRegistry, payload: dict) -> dict:
    """Admit native-confirmed identity only against the pre-probe registry snapshot."""
    for key in ("project", "worktree_id", "worktree_path", "mux_session",
                "mux_bin", "session_incarnation"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            raise ValueError(f"mux-discover-v1 missing {key!r}")
    name = payload["mux_session"]
    if name != "wt-" + payload["worktree_id"].replace(".", "_"):
        return {"applied": False, "reason": "unattributed-session"}
    incumbent = registry.get(payload["project"], payload["worktree_id"])
    confirmed = confirm_native_session(payload, connected_only=True)
    if not confirmed.get("applied"):
        return confirmed
    return registry.discover(
        dict(payload, attached_clients=confirmed["attached_clients"]), expected=incumbent,
    )


def _is_empty_tmux_listing(binary: str, result: subprocess.CompletedProcess[str]) -> bool:
    """Mirror the engine's mux_list_result contract without crossing process ownership."""
    name = binary.replace("\\", "/").rsplit("/", 1)[-1].casefold().removesuffix(".exe")
    if name != "tmux" or result.returncode != 1 or (result.stdout or "").strip():
        return False
    message = (result.stderr or "").strip()
    return bool(
        re.fullmatch(r"no server running on [^\r\n]+", message)
        or re.fullmatch(
            r"error connecting to [^\r\n]+ \(No such file or directory\)",
            message,
        )
    )


def confirm_native_session(
    payload: dict, *, connected_only: bool, timeout_s: float = 2.0,
) -> dict:
    """A failed enumeration remains unknown and cannot mutate a stored fence."""
    name = payload["mux_session"]
    if not _valid_native_incarnation(payload.get("session_incarnation")):
        LOG.warning("Refusing malformed native mux identity for %s", name)
        return {"applied": False, "reason": "session-unconfirmed"}
    try:
        probe = run_mux_child(
            [payload["mux_bin"], "list-sessions", "-F",
             "#{session_name}\t#{session_attached}\t#{session_id}:#{session_created}\t#{session_path}"],
            capture_output=True, text=True, timeout=timeout_s, **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        LOG.warning("Connected mux discovery probe failed for %s: %s", name, exc)
        return {"applied": False, "reason": "enumeration-unavailable"}
    if probe.returncode != 0:
        if (
            (probe.stdout is None or isinstance(probe.stdout, str))
            and (probe.stderr is None or isinstance(probe.stderr, str))
            and _is_empty_tmux_listing(payload["mux_bin"], probe)
        ):
            return {"applied": False, "reason": "session-absent"}
        return {"applied": False, "reason": "enumeration-unavailable"}
    if not isinstance(probe.stdout, str):
        return {"applied": False, "reason": "session-unconfirmed"}
    rows = [line.split("\t", 3) for line in probe.stdout.splitlines() if line.strip()]
    if any(
        len(row) != 4 or not row[0] or not row[1].isdecimal() or not row[3]
        or not _valid_native_incarnation(row[2])
        for row in rows
    ):
        return {"applied": False, "reason": "session-unconfirmed"}
    matches = [row for row in rows if row[0] == name]
    if not matches:
        return {"applied": False, "reason": "session-absent"}
    if len(matches) != 1 or len(matches[0]) != 4:
        return {"applied": False, "reason": "session-unconfirmed"}
    _, count, incarnation, path = matches[0]
    if incarnation != payload["session_incarnation"]:
        return {"applied": False, "reason": "stale-incarnation"}
    if not path or os.path.normcase(os.path.realpath(path)) != os.path.normcase(
        os.path.realpath(payload["worktree_path"])
    ):
        return {"applied": False, "reason": "unattributed-path"}
    if not count.isdecimal() or (connected_only and int(count) <= 0):
        return {"applied": False, "reason": "not-connected"}
    return {"applied": True, "attached_clients": int(count)}
