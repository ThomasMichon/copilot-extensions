"""Owned startup command construction and pending delivery reconciliation."""

from __future__ import annotations

import json
import shlex


def recover_pending_delivery(name, plan, pending, owner, *, remote, release_cli_mode, busy_exit):
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
