"""Consume target inventory without turning discovery into native execution."""

from __future__ import annotations

from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, cast

from .client import BridgeClientError, BridgeConnectionError


class HydrationOutcomeUnknown(SystemExit):
    """A CLI terminal boundary, including through enclosing reconnect loops."""

    def __init__(self, target: str) -> None:
        self.reason = "workspace_hydration_unconfirmed"
        super().__init__(
            f"[HELD] {self.reason} for {target!r}; inspect its metadata owner. "
            "The hydration request was not replayed."
        )


def _require_complete(discovery: Any) -> None:
    if (
        not isinstance(discovery, dict) or discovery.get("completeness") != "complete"
        or discovery.get("availability") == "unavailable"
    ):
        raise BridgeClientError(
            503, {"reason": "target_inventory_incomplete", "detail": "Discovery cannot exclude another workspace owner"},
        )


def _explicit_provider(target: str) -> bool:
    """Provider/venue forms cannot be Worktrees IDs; their owner still admits them."""
    namespace, colon, name = target.partition(":")
    project, at, venue = target.partition("@")
    return bool(namespace.strip() and colon and name.strip() or project.strip() and at and venue.strip())


def _legacy_resolution(client: Any, target: str) -> None:
    if any(session.get("worktree_id") == target for session in client.list_sessions()):
        return
    if _explicit_provider(target):
        return
    read = getattr(client, "list_worktrees_for_render", None)
    result = read() if read is not None else {}
    _require_complete(result.get("discovery"))
    if any(
        entry.get("id") == target for entries in result.get("groups", {}).values()
        for entry in entries
    ):
        raise BridgeClientError(
            426, "Known workspace requires target_inventory and "
            "worktree_metadata_hydration support; no session was started",
        )


def _same_workspace(session: Any, target: str, item: dict[str, Any]) -> bool:
    return bool(
        isinstance(session, dict) and session.get("worktree_id") == target
        and session.get("agent_name") == item["owner"]
        and session.get("project") == item["project"]
        and session.get("target_dir") == item["worktree"].get("path")
    )


def _managed_projection(client: Any, target: str, item: dict[str, Any]) -> dict[str, Any] | None:
    session_id = item["worktree"].get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    try:
        session = client.get_session(session_id)
    except BridgeClientError as exc:
        if exc.status == 404:
            return None
        raise
    if not (
        isinstance(session, dict) and session.get("session_id") == session_id
        and _same_workspace(session, target, item)
    ):
        raise BridgeClientError(
            409, {"reason": "workspace_projection_changed", "detail": "The projected managed owner changed"},
        )
    return session


def _successor(
    client: Any, target: str, item: dict[str, Any], predecessor: dict[str, Any],
) -> dict[str, Any]:
    """Choose a live qualified alternative, never older inactive history."""
    transport = predecessor.get("target_type"), predecessor.get("target_host")
    candidates = [
        session for session in client.list_sessions()
        if _same_workspace(session, target, item)
        and isinstance(session.get("session_id"), str) and session["session_id"]
        and session["session_id"] != predecessor["session_id"]
        and (session.get("target_type"), session.get("target_host")) == transport
        and session.get("status") in {"created", "starting", "running", "idle"}
    ]
    for candidate in candidates:
        selected = {
            **item, "worktree": {**item["worktree"], "session_id": candidate["session_id"]},
        }
        current = _managed_projection(client, target, selected)
        if current is not None and (current.get("target_type"), current.get("target_host")) == transport:
            return current
    return predecessor


def _hydrate(client: Any, target: str) -> dict[str, Any]:
    try:
        return cast(dict[str, Any], client.hydrate_worktree(target))
    except BridgeClientError as exc:
        if exc.status < 500:
            raise
        if isinstance(exc.detail, dict) and exc.detail.get("reason"):
            raise SystemExit(
                f"[HELD] {exc.detail['reason']} for {target!r}; "
                "inspect its metadata owner. The request was not replayed."
            ) from exc
        raise HydrationOutcomeUnknown(target) from exc
    except (BridgeConnectionError, HTTPException, OSError, ValueError) as exc:
        raise HydrationOutcomeUnknown(target) from exc


def resolve_workspace(
    client: Any, target: str, *, exclude_session_id: str | None = None,
) -> dict[str, Any] | None:
    """Hydrate a known workspace through its owner, or retain legacy resolution.

    Registration-only targets cannot fall through to profile creation when
    their owning transport failed to resolve them.
    """
    read = getattr(client, "list_targets", None)
    if read is None:
        return None
    try:
        result = read()
    except BridgeClientError as exc:
        if exc.status != 426:
            raise
        _legacy_resolution(client, target)
        return None
    matches = [item for item in result["targets"] if item["handle"] == target]
    workspaces = [item for item in matches if item["kind"] == "worktree"]
    if workspaces:
        _require_complete(result["sources"].get("worktrees"))
        identities = {
            (item.get("owner"), item.get("project"), item["worktree"].get("path"))
            for item in workspaces
        }
        if len(identities) != 1:
            raise BridgeClientError(
                409, {"reason": "ambiguous_workspace", "detail": "Several workspace owners publish this handle"},
            )
        existing = _managed_projection(client, target, workspaces[0])
        if existing is not None and existing["session_id"] == exclude_session_id:
            return _successor(client, target, workspaces[0], existing)
        return existing if existing is not None else _hydrate(client, target)
    if any(item["kind"] in {"managed", "live", "session_host"} for item in matches):
        raise BridgeClientError(
            409, {
                "reason": "registered_transport_unavailable",
                "detail": "Target is registered but its owning transport did not resolve; "
                "no replacement session was started",
            },
        )
    if _explicit_provider(target):
        return None
    _require_complete(result["sources"].get("worktrees"))
    if any(
        observation.get("completeness") not in {"complete", None}
        or observation.get("availability") == "unavailable"
        for observation in result["sources"].values()
    ) or any(
        result["sources"].get(source, {}).get("availability") != "available"
        for source in ("managed", "session_host", "extension")
    ):
        raise BridgeClientError(
            503, {"reason": "target_inventory_incomplete", "detail": "Discovery cannot exclude an existing target"},
        )
    return None


def discover_mux(session: str, pin: str) -> dict[str, Any]:
    """Query the selected runtime's Driver; a mux pin is never a route."""
    from agent_procutil import no_window_kwargs

    observation: dict[str, Any] = {"availability": "unavailable", "route": "unresolved"}
    root = os.environ.get("PSMUX_DATA_DIR", "")
    namespace = os.environ.get("COPILOT_DRIVER_PSMUX_NAMESPACE")
    if not Path(root).is_absolute() or namespace is None or not re.fullmatch(r"\$[0-9]{1,20}:[0-9]{1,20}", pin):
        return {**observation, "reason": "explicit_scope_required"}
    env = dict(os.environ, PSMUX_DATA_DIR=root, COPILOT_DRIVER_PSMUX_NAMESPACE=namespace)
    argv = [sys.executable, "-I", "-B", "-X", "utf8", "-m", "agent_remote_driver.mux_observation"]

    def query(mode: str, payload: bytes) -> tuple[int, dict[str, Any]]:
        child = subprocess.run(
            [*argv, mode], input=payload, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=5, env=env, **no_window_kwargs(),
        )
        if len(child.stdout) > 4096:
            raise ValueError("oversized")
        body = json.loads(child.stdout)
        if not (isinstance(body, dict) and body.get("contract") == "copilot-driver.mux-observation"
                and type(body.get("version")) is int and body["version"] == 1):
            raise ValueError("unsupported")
        return child.returncode, body

    try:
        code, declaration = query("--describe-contract", b"")
        operations = declaration.get("operations")
        if code != 0 or not isinstance(operations, list) or "psmux-observe-pinned" not in operations:
            return {**observation, "reason": "pinned_read_unsupported"}
        code, report = query("--observe", json.dumps({
            "version": 1, "session": session, "expected_incarnation": pin,
        }).encode("utf-8"))
        if not (
            report.get("operation") == "psmux-observe-pinned" and report.get("session") == session
            and report.get("namespace") == namespace and report.get("expected_incarnation") == pin
        ):
            raise ValueError("unconfirmed")
        if code == 0 and report.get("status") == "present" and report.get("incarnation") == pin:
            observation.update(
                availability="observed", session=session, namespace=namespace,
                incarnation=pin, reason="agent_route_unverified",
            )
        elif code == 3 and report.get("status") == "unknown" and report.get("reason") in (
            "different-incarnation", "missing-rendezvous", "changed-rendezvous", "native-query-unavailable",
        ):
            observation["reason"] = report["reason"]
        else:
            raise ValueError("unconfirmed")
    except (OSError, ValueError, RecursionError, subprocess.TimeoutExpired):
        observation["reason"] = "unconfirmed_owner_report"
    return observation


def command(args: Any) -> None:
    """List the typed union, preserving source availability in JSON output."""
    from . import __main__ as core

    result = core._get_client(ensure=False).list_targets(refresh=args.refresh)
    if getattr(args, "discover_mux", None):
        result = {**result, "sources": {**result["sources"], "pinned_mux": discover_mux(*args.discover_mux)}}
    if args.json:
        core._json_out(result)
        return
    for item in result["targets"]:
        print(f"{item['handle']}  [{item['kind']}]")
        if item["kind"] == "worktree":
            workspace = item["worktree"]
            print(f"  Owner: {item['owner']}  Project: {item.get('project') or '-'}")
            print(f"  Path: {workspace.get('path') or '-'}")
    for source, observation in result["sources"].items():
        if source == "pinned_mux" and observation.get("availability") == "observed":
            print(f"[OBSERVED] mux {observation['session']} {observation['incarnation']}; agent route unresolved")
        if observation.get("availability") == "unavailable" or (
            observation.get("completeness") not in {None, "complete"}
        ):
            print(f"[WARN] {source}: {observation}")
