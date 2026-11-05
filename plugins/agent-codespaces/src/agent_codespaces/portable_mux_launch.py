"""CodeSpace launch through selected Bridge support, never guest-suite fallback."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import PurePosixPath
import shlex
import subprocess
import uuid

from repository_policy import validate_hosting_result
from repository_policy.receiving import build_receiving_policy_check
from venue_copilot import (
    SESSION_SELECTORS, await_claim, last_json, live_session_for,
    read_seed, release_cli_mode, reserve_with_retry,
)
from venue_copilot.portable_mux import MuxEndpoint
from venue_copilot.refs import (
    owned_delivery_journal, owned_result, reconcile_owned_delivery, require_cli_context,
)

from . import connection_owner as owner
from .bridge_receiving import BridgeReceiving
from .copilot_detach_startup import ssh_namespace
from .model_launch import model_copilot_args
from .portable_mux_control import require_owner, retained_endpoint

KIND = "portable-mux-launch"


def _wire(bridge: BridgeReceiving, request: dict) -> tuple[str, bytes]:
    command, payload = bridge.launch(request)
    if json.loads(payload)["request"]["policy_context"] != bridge.policy_context:
        raise ValueError("Selected Bridge receiving context changed during preparation")
    return command, payload


def _plan_endpoint(plan: dict, endpoint: MuxEndpoint) -> None:
    plan["mux_session"] = endpoint.mux_session
    plan["venue"].update(mux_session_name=endpoint.mux_session, mux_endpoint=endpoint.record())


def _probe(name, workspace, context, remote) -> str:
    command, payload = build_receiving_policy_check(workspace, context)
    result = remote(name, command, input_bytes=payload, timeout=90)
    if result is None or result[0]:
        raise RuntimeError("Portable repository hosting refused before launch preparation")
    validate_hosting_result(json.loads(result[1]))
    result = remote(
        name, "python3 -I -S -c " + shlex.quote(
            "from pathlib import Path; print((Path.home()/'.agent-bridge'/'receiving').resolve())"
        ),
    )
    if result is None or result[0]:
        raise RuntimeError("Cannot resolve the selected receiving state directory")
    root = result[1].strip()
    if not PurePosixPath(root).is_absolute() or "\0" in root or "\n" in root:
        raise RuntimeError("Invalid receiving state directory")
    return root


def cmd_launch(args, plan, config, codespace, *, ssh_session) -> int:
    from .copilot_detach import (
        _BUSY_EXIT, _commands, _fail, _progress, _remote, _send_refs,
        _int_port_map, _local_forwards_ready, _pending_local_forwards,
        _reported_local_forwards, _venue_ports_listening, parse_local_forwards, parse_reverse_forwards,
    )
    from .copilot_venue import claim_or_exit_code
    from .session_forwards import await_owner_bridge_forward

    pending = owner.get_pending_delivery(args.name, plan["tenant"])
    try:
        if pending is not None and (
            not isinstance(pending, dict) or pending.get("kind") != KIND
            or type(pending.get("version")) is not int or pending["version"] != 1
        ):
            raise ValueError("An existing owned delivery must be reconciled in its original mode")
        if getattr(args, "worktree_id", None):
            raise ValueError("Portable receiving cannot allocate or resolve a managed worktree")
        if getattr(codespace, "state", None) != "Available":
            raise ValueError("Portable launch requires an explicitly activated, inspectable CodeSpace")
        given = list(getattr(args, "copilot_args", None) or [])
        if any(arg.split("=", 1)[0] in SESSION_SELECTORS for arg in given):
            raise ValueError("Portable launch retains its own native session identity; use rejoin")
        seed = read_seed(args)
        if pending and (seed or getattr(args, "ref_files", None) or given):
            raise ValueError("Recover the original portable attempt without replacing its inputs")
        local = parse_local_forwards(getattr(args, "local_forwards", None) or [])
        reverse = parse_reverse_forwards(getattr(args, "reverse_forwards", None) or [])
        if getattr(args, "dry_run", False):
            print(json.dumps({"ok": True, **plan, "dry_run": True, "receiving_support": "portable"}))
            return 0
        bridge = BridgeReceiving()
        daemon_port = bridge.capabilities.get("daemon_port")
        if type(daemon_port) is not int or not 0 < daemon_port <= 65535:
            raise ValueError("Selected Bridge has no qualified daemon port")
        row = live_session_for(plan["scope_id"], run=bridge.run, strict=True)
        held = owner.get_hold(args.name)
        endpoint = retained_endpoint(plan, held, row)
        if pending:
            saved = MuxEndpoint.parse(pending["endpoint"])
            if endpoint is not None and (
                saved.attempt, saved.session_id, saved.mux_socket,
            ) != (endpoint.attempt, endpoint.session_id, endpoint.mux_socket):
                raise ValueError("Pending portable launch disagrees with retained native custody")
            endpoint = saved
            if pending["daemon_port"] != daemon_port or pending["policy_context"] != bridge.policy_context:
                raise ValueError("Original portable launch belongs to another Bridge route or context")
        if row.get("status") == "live" and endpoint is None:
            raise ValueError("A live legacy session must be rejoined in its original mode")
        state_root = _probe(args.name, plan["workspace_folder"], bridge.policy_context, _remote)
        claim_rc = claim_or_exit_code(args, launch_policy=endpoint is None)
        if claim_rc is not None:
            return claim_rc
        if not owner.ensure_owner_running(config):
            raise RuntimeError("The Connection Owner is unavailable")
        require_owner()
        new = endpoint is None
        if endpoint is None:
            attempt, native_id = str(uuid.uuid4()), str(uuid.uuid4())
            endpoint = MuxEndpoint.parse({
                "version": 1, "attempt": attempt, "session_id": native_id,
                "mux_session": "wt-bridge-" + attempt,
                "mux_socket": str(PurePosixPath(state_root) / attempt / "tmux.sock"),
                "native": None, "socket_identity": None,
            })
        _plan_endpoint(plan, endpoint)
        owner.hold(
            args.name, plan["tenant"], daemon_port=daemon_port,
            mux_session=endpoint.mux_session, mux_endpoint=endpoint.record(), fresh=new,
            **({"local_forwards": local} if local else {}),
            **({"reverse_forwards": reverse} if reverse else {}),
        )
        # From here, neither a failed registration nor unknown native creation
        # may drop the session tenant or invoke the legacy default-socket kill.
        journal = pending
        try:
            if not asyncio.run(await_owner_bridge_forward(args.name, timeout=90)):
                raise RuntimeError("The Owner's Bridge forward is unavailable")
            if journal is None:
                if getattr(args, "ref_files", None):
                    from venue_copilot.refs import upload_for

                    note = _send_refs(args.name, upload_for(args.ref_files, plan["scope_id"]))
                    if note is None:
                        raise RuntimeError("Could not copy the portable launch references")
                    seed = f"{seed.rstrip()}\n\n{note}" if seed else note
                reservation = reserve_with_retry(plan["scope_id"], plan["venue"], run=bridge.run, ttl_seconds=1800)
                journal = {
                    "version": 1, "kind": KIND, "phase": "prepared",
                    "endpoint": endpoint.record(), "daemon_port": daemon_port,
                    "policy_context": bridge.policy_context, "reservation": reservation,
                    "request": ({
                        "cwd": plan["workspace_folder"], "state_root": state_root,
                        "scope_id": plan["scope_id"], "attempt": endpoint.attempt,
                        "session_id": endpoint.session_id, "copilot_args": given + model_copilot_args(given),
                        "ensure_mux": bool(args.ensure_mux), "registration": {"port": daemon_port},
                        **({"driven_by": args.driver} if getattr(args, "driver", None) else {}),
                    } if new else None),
                    "seed": seed, "workspace": plan["workspace_folder"],
                    "operation": reservation["reservation_id"],
                }
                owner.record_pending_delivery(args.name, plan["tenant"], journal)
            captured = {}

            def builder(plugin_dirs):
                context = require_cli_context(captured["prepared"]["plugin_context"])
                if context["cwd"] != journal["workspace"]:
                    raise ValueError("Prepared execution checkout differs from the original portable launch")
                if "context" in journal and context != journal["context"]:
                    raise ValueError("Prepared plugin inputs changed; original attempt retained")
                journal["context"] = context
                captured["plugin_dirs"] = list(plugin_dirs)
                if journal["request"] is not None:
                    request = journal["request"]
                    command, payload = _wire(bridge, request)
                    digest = hashlib.sha256(payload).hexdigest()
                    if journal.get("payload_sha256", digest) != digest:
                        raise ValueError("Selected receiving support changed; original attempt retained")
                    journal.update(request=request, payload_sha256=digest, phase="submitted")
                    captured["stdin"] = payload
                else:
                    command = endpoint.command("observe")
                owner.record_pending_delivery(args.name, plan["tenant"], journal)
                return command

            def sink(result):
                captured["result"] = result
                return result.exit_code

            captured["prepared"] = {}
            ssh_args = ssh_namespace(args, "portable receiving", timeout=args.register_timeout + 300)
            ssh_args.no_relay = bool(getattr(args, "no_relay", False))
            ssh_args.plugin_prepared_context = captured["prepared"]
            _progress("launch", "selected portable receiver; original native attempt retained")
            rc = ssh_session(
                ssh_args, remote_cmd_builder=builder,
                remote_input_builder=lambda: captured.get("stdin", b""),
                receiving_context=bridge.policy_context, result_sink=sink, settle_on_disconnect=False,
            )
            result = captured.get("result")
            if rc or result is None:
                raise RuntimeError("Portable receiving did not confirm the original launch")
            if journal["request"] is not None:
                receipt = last_json(result.stdout or "")
                received = MuxEndpoint.from_receipt(receipt)
                if receipt.get("ok") is not True or not received.confirmed:
                    raise RuntimeError("Original portable native creation remains pending")
                if (
                    received.attempt, received.session_id, received.mux_socket,
                ) != (endpoint.attempt, endpoint.session_id, endpoint.mux_socket) or (
                    endpoint.confirmed and received != endpoint
                ):
                    raise RuntimeError("Portable receiver returned a different native attempt")
                endpoint = received
            elif (result.stdout or "").strip() != "present":
                raise RuntimeError("Retained portable native incarnation is not confirmed live")
            journal.update(endpoint=endpoint.record(), phase="created")
            owner.record_pending_delivery(args.name, plan["tenant"], journal)
            _plan_endpoint(plan, endpoint)
            owner.hold(
                args.name, plan["tenant"], daemon_port=daemon_port,
                mux_session=endpoint.mux_session, mux_endpoint=endpoint.record(), confirmed=True,
            )
            live = live_session_for(plan["scope_id"], run=bridge.run, strict=True)
            session_id = live.get("session_id") if live.get("status") == "live" else None
            if not session_id:
                session_id = await_claim(
                    plan["scope_id"], journal["reservation"]["reservation_id"],
                    args.register_timeout, run=bridge.run,
                )
            if session_id != endpoint.session_id:
                raise RuntimeError("The original portable native session has not registered with Bridge")
            if journal["seed"]:
                if "delivery" not in journal:
                    journal["delivery"] = owned_delivery_journal(
                        session_id, journal["seed"], journal["context"], operation=journal["operation"],
                        plan=plan, reservation_id=journal["reservation"]["reservation_id"],
                        creation_receipt=None, daemon_port=daemon_port,
                    )
                    owner.record_pending_delivery(args.name, plan["tenant"], journal)
                delivery = reconcile_owned_delivery(journal["delivery"], run=bridge.run)
                delivery = owned_result(
                    delivery, session_id=session_id, operation=journal["operation"],
                    note=journal["seed"], context=journal["context"],
                )
                journal["delivery"].update(delivery.recovery())
                owner.record_pending_delivery(args.name, plan["tenant"], journal)
                if delivery.status != "admitted":
                    raise RuntimeError("Original portable seed admission remains " + delivery.status)
            release_cli_mode(
                plan["scope_id"], reservation_id=journal["reservation"]["reservation_id"], run=bridge.run,
            )
            prior_local = dict(getattr(held, "local_forwards", None) or {})
            assigned = dict(getattr(held, "assigned_local_forwards", None) or {})
            forwards = {}
            if local or prior_local:
                try:
                    reported = _reported_local_forwards(args.name, local, prior_local, assigned)
                    forwards.update(
                        local_forwards=reported,
                        local_forwards_ready=_local_forwards_ready(args.name, reported, local, assigned),
                    )
                except TimeoutError as exc:
                    forwards.update(
                        local_forwards_pending=_pending_local_forwards(local, prior_local, assigned),
                        error=str(exc),
                    )
            reported_reverse = reverse or _int_port_map(getattr(held, "reverse_forwards", None) or {})
            if reported_reverse:
                forwards.update(
                    reverse_forwards=reported_reverse,
                    reverse_forwards_ready=_venue_ports_listening(args.name, sorted(reported_reverse)),
                )
            owner.record_pending_delivery(args.name, plan["tenant"], None)
            print(json.dumps({
                "ok": True, **plan, "receiving_support": "portable", "session_id": session_id,
                "created": new, "resumed": not new, "seeded": bool(journal["seed"]),
                "seed_delivery": "bridge" if journal["seed"] else None,
                "commands": _commands(plan, session_id, getattr(args, "effort", None)),
                "plugin_dirs": captured.get("plugin_dirs", []),
                **forwards,
            }, indent=2))
            return 0
        except (RuntimeError, ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
            if journal is None and new:
                owner.release(args.name, plan["tenant"])
            if journal is not None:
                print(json.dumps({
                    "ok": False, **plan, "pending": True, "receiving_support": "portable",
                    "session_id": endpoint.session_id, "error": str(exc),
                }, indent=2))
                return _BUSY_EXIT
            raise
    except (RuntimeError, ValueError, OSError, KeyError, subprocess.SubprocessError) as exc:
        return _fail(str(exc), plan)
