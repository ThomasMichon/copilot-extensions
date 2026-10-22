"""Durable seed admission evidence, independent of disposable SSH keepers."""

from __future__ import annotations

import hashlib
import os
import re

from ssh_manager.forward_keeper import KeeperStore
from venue_copilot.refs import reconcile_owned_delivery, settle_deferred_seed


def _store():
    from .copilot_detach import _STORE
    directory = _STORE.state_dir / "pending-deliveries"
    if directory.is_symlink():
        raise RuntimeError("SSH delivery journal directory must not be a symlink")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        directory.chmod(0o700)
    return KeeperStore(directory)


def read(
    target: str, scope: str, *, requested_workspace: str | None = None,
    execution_workspace: str | None = None,
) -> dict | None:
    store = _store()
    data = store.read(target)
    if data is None:
        if store.state_path(target).exists():
            raise RuntimeError("Pending SSH delivery journal is unreadable; ownership retained")
        return None
    if (
        type(data.get("version")) is not int or data["version"] != 1
        or not isinstance(data.get("pending_deliveries"), dict)
    ):
        raise RuntimeError("Unsupported pending SSH delivery journal")
    for key, entry in data["pending_deliveries"].items():
        if not isinstance(entry, dict):
            raise RuntimeError("Invalid pending SSH delivery journal")
        if key != scope and (
            requested_workspace is not None and entry.get("requested_workspace") == requested_workspace
            or execution_workspace is not None and entry.get("envelope", {}).get(
                "plugin_context", {},
            ).get("cwd") == execution_workspace
        ):
            raise RuntimeError("Pending SSH delivery native identity changed; original ownership retained")
    pending = data["pending_deliveries"].get(scope)
    if pending is not None and not isinstance(pending, dict):
        raise RuntimeError("Invalid pending SSH delivery journal")
    return pending


def record(
    target: str, scope: str, pending: dict | None, *, expected: dict | None = None,
) -> None:
    from .copilot_detach import _holds
    with _holds().lock(target):
        current = read(target, scope)
        if expected is not None and current != expected:
            raise RuntimeError("Pending SSH delivery ownership changed; journal retained")
        data = _store().read(target) or {"version": 1, "pending_deliveries": {}}
        ledger = dict(data["pending_deliveries"])
        if pending is None:
            ledger.pop(scope, None)
        else:
            ledger[scope] = pending
        if ledger:
            _store().write(target, {**data, "pending_deliveries": ledger})
        else:
            _store().remove(target)


def retire_stopped(target: str, plan: dict, pending: dict) -> None:
    """Archive only the captured operation after its exact native mux was stopped."""
    from .copilot_detach import _holds

    scope = plan["scope_id"]
    with _holds().lock(target):
        current = read(target, scope)
        if (
            current != pending or pending.get("scope_id") != scope
            or pending.get("mux_session") != plan["mux_session"]
            or pending.get("envelope", {}).get("plugin_context", {}).get("cwd") != plan["workspace"]
        ):
            raise RuntimeError("Stopped SSH delivery ownership changed; journal retained")
        directory = _store().state_dir / "retired"
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink():
            raise RuntimeError("Retired SSH delivery directory must not be a symlink")
        if os.name != "nt":
            directory.chmod(0o700)
        key = hashlib.sha256(
            f"{target}\0{scope}\0{pending['operation']}".encode("utf-8"),
        ).hexdigest()
        KeeperStore(directory).write(key, {
            "version": 1, "status": "verified_stopped", "target": target, "delivery": pending,
        })
        store = _store()
        data = store.read(target)
        ledger = dict(data["pending_deliveries"])
        del ledger[scope]
        if ledger:
            store.write(target, {**data, "pending_deliveries": ledger})
        else:
            store.remove(target)


def pending_keeper_hold_id(pending: dict, scope: str) -> str:
    hold_id = pending.get("keeper_hold_id")
    if not isinstance(hold_id, str) or (
        hold_id != scope and not re.fullmatch(r"attached:[1-9][0-9]*:[a-f0-9]{32}", hold_id)
    ):
        raise RuntimeError("Pending SSH delivery lacks its original keeper hold identity")
    return hold_id


def recover(adapter, plan, pending) -> tuple[int, dict]:
    from venue_copilot import release_cli_mode
    from venue_copilot.detached import _bridge_path_ok, public_plan

    context = pending.get("envelope", {}).get("plugin_context", {})
    if (
        pending.get("scope_id") != plan["scope_id"]
        or pending.get("requested_workspace") != adapter.plugin_args.workspace
        or not isinstance(context.get("cwd"), str) or not context["cwd"].startswith("/")
        or context["cwd"] != plan["workspace"]
    ):
        raise RuntimeError("Pending SSH delivery belongs to a different execution checkout")
    plan["workspace"] = context["cwd"]
    port = pending.get("daemon_port")
    mux = pending.get("mux_session")
    if type(port) is not int or not 0 < port < 65536 or not isinstance(mux, str) or not mux:
        raise RuntimeError("Pending SSH delivery lacks its original forward metadata")
    hold_id = pending_keeper_hold_id(pending, plan["scope_id"])
    plan["mux_session"] = mux
    plan["venue"]["mux_session_name"] = mux
    adapter.hold_id = hold_id
    adapter.ensure_keeper(venue_port=port, mux=mux)
    adapter.keeper_hold_acquired = True
    if not _bridge_path_ok(adapter, port):
        raise RuntimeError("The retained SSH session's bridge forward is unavailable")
    result = reconcile_owned_delivery(pending)
    updated = {**pending, **result.recovery()}
    record(adapter.target, plan["scope_id"], updated, expected=pending)
    if result.status == "admitted" and settle_deferred_seed(
        lambda command, stdin: adapter.run_input(command, stdin, timeout=60),
        plan["identity"], plan["workspace"], pending.get("creation_receipt"),
    ):
        record(adapter.target, plan["scope_id"], None, expected=updated)
        adapter.pending_delivery = False
        release_cli_mode(plan["scope_id"], reservation_id=pending.get("reservation_id"))
        return 0, {
            "ok": True, **public_plan(plan), "session_id": result.session_id,
            "recovered": True, "seed_delivery": "bridge", "seed_admitted": True,
            "message_id": result.message_id,
        }
    return 75, {
        "ok": False, **public_plan(plan), "pending": True, "session_id": result.session_id,
        "pending_delivery": result.recovery(),
        "error": "owned delivery remains pending; original SSH session and ownership retained",
    }
