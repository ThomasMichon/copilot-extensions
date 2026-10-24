"""Owned startup command construction and pending delivery reconciliation."""

from __future__ import annotations

import argparse
import json
import shlex
import asyncio
import uuid


def ssh_namespace(
    args: argparse.Namespace, remote_cmd: str, *, timeout: float,
) -> argparse.Namespace:
    return argparse.Namespace(
        name=args.name,
        stdio=False,
        remote_cmd=remote_cmd,
        timeout=timeout,
        connect_timeout=None,
        no_provision=False,
        no_relay=False,
        auth_cache_warmup=True,
        repo=None,
        effort=getattr(args, "effort", None),
        session_id=None,
        stage_plugins=list(getattr(args, "stage_plugins", []) or []),
        plugin_roots=list(getattr(args, "plugin_roots", []) or []),
        skip_plugin_preparation=bool(getattr(args, "stop", False)),
        force=getattr(args, "force", False),
        force_claim=getattr(args, "force_claim", False),
    )


def recover_pending_delivery(
    name, plan, pending, owner, *, remote, release_cli_mode, busy_exit,
    await_forward, bridge_path_ok,
):
    recovery_tenant = plan["tenant"] + ":delivery-recovery:" + uuid.uuid4().hex
    needs_forward = pending.get("status") != "admitted"
    try:
        if needs_forward:
            port, mux = pending.get("daemon_port"), pending.get("mux_session")
            if type(port) is not int or not 0 < port <= 65535 or not isinstance(mux, str) or not mux:
                raise RuntimeError("Pending delivery lacks its original native forward metadata")
            # A bounded recovery connection does not renew the original session lease.
            owner.hold(name, recovery_tenant, daemon_port=port, mux_session=mux,
                       confirmed=True, max_lease=600)
            if not asyncio.run(await_forward(name, timeout=90)) or not bridge_path_ok(name, port):
                raise RuntimeError("The retained native session's bridge forward is unavailable")
        return _reconcile_pending_delivery(
            name, plan, pending, owner, remote=remote,
            release_cli_mode=release_cli_mode, busy_exit=busy_exit,
        )
    except (RuntimeError, OSError) as exc:
        print(json.dumps({
            "ok": False, **plan, "pending": True, "session_id": pending["session_id"],
            "error": str(exc),
        }, indent=2))
        return busy_exit
    finally:
        if needs_forward:
            owner.release(name, recovery_tenant)


def _reconcile_pending_delivery(name, plan, pending, owner, *, remote, release_cli_mode, busy_exit):
    from venue_copilot.refs import reconcile_owned_delivery, settle_deferred_seed

    result = reconcile_owned_delivery(pending)
    if result.status == "admitted" and settle_deferred_seed(
        lambda command, stdin: remote(name, command, timeout=60, input_bytes=stdin),
        plan["identity"], pending["envelope"]["plugin_context"]["cwd"],
        pending.get("creation_receipt"),
    ):
        owner.record_pending_delivery(name, plan["tenant"], None)
        release_cli_mode(plan["scope_id"], reservation_id=pending.get("reservation_id"))
        print(json.dumps({"ok": True, **plan, "session_id": result.session_id,
                          "recovered": True, "seed_delivery": "bridge", "seed_admitted": True,
                          "message_id": result.message_id}, indent=2))
        return 0
    pending.update(result.recovery())
    owner.record_pending_delivery(name, plan["tenant"], pending)
    print(json.dumps({"ok": False, **plan, "pending": True, "session_id": pending["session_id"],
                      "pending_delivery": result.recovery(),
                      "error": "owned delivery remains pending; original native session and ownership retained"}, indent=2))
    return busy_exit


def build_owned_command(
    plugin_dirs, *, captured, plan, args, seed_ready_timeout, copilot_args, reservation,
    seed, build_command, tooling, trust_folder,
):
    from venue_copilot.refs import require_cli_context

    context = require_cli_context(captured["prepared_context"]["plugin_context"])
    captured["plugin_context"] = context
    # CLI discovers the registered identities through repository settings.
    # Repeating them as external directories loads duplicate plugins.
    extra = copilot_args
    captured["plugin_dirs"] = list(plugin_dirs)
    command = build_command(
        plan["identity"], anchor=plan["anchor"], driver=args.driver, seed=None,
        seed_ready_timeout=seed_ready_timeout,
        ensure_mux=args.ensure_mux, detach=True, bridge_scope_id=plan["scope_id"],
        bridge_seed_operation=reservation["reservation_id"], bridge_seed_pending=bool(seed),
        copilot_args=extra, login_shell=False,
    )
    # Run from the product checkout, exactly like headless dispatch's
    # `cd <workspace> && copilot --acp`: embody resolves its project
    # (and the anchor) from the working directory. A CodeSpace's
    # checkout is often not an adopted agent-worktrees project yet;
    # adopt it once, as the anchor-only (base-repo) project it is.
    workspace = context["cwd"]
    if not workspace:
        return command
    prefix = (
        f"cd {shlex.quote(workspace)} && {tooling} && "
        f"{trust_folder(workspace)}"
    )
    if plan["anchor"]:
        label = plan["identity"].removeprefix("anchor-")
        prefix += (
            " && { agent-worktrees get project >/dev/null 2>&1 || "
            f"agent-worktrees register {shlex.quote(label)} --base-repo --no-agent >&2; }}"
        )
    return f"{prefix} && {command}"
