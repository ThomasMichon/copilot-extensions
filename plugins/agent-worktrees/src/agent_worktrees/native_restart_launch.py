"""Explicit exact-original native Resume through embody's lifecycle fence."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from uuid import UUID

from . import bridge_acquisition_launch, embody_resume, launch_seed_state
from . import native_restart_check, native_restart_claims, native_restart_receipt, native_source_context
from . import output, sessions, tracking


_CONFLICTS = (
    "new", "anchor", "codename", "seed", "fresh", "resume_head", "bridge_acquisition",
    "require_native_admission", "ensure_mux", "recovery", "copilot_args",
    "copilot_process_launcher", "bridge_scope_id", "driver", "profile",
)


def is_restart_intent(args: argparse.Namespace) -> bool:
    """Keep malformed/empty restart input out of ordinary embodiment."""
    return (
        getattr(args, "restart_request", None) is not None
        or getattr(args, "restart_ack", None) is not None
        or getattr(args, "restart_receipt", False)
        or getattr(args, "status_reconciled", False)
    )


def add_arguments(parser: argparse.ArgumentParser, intent: argparse._ActionsContainer) -> None:
    """Register restart-only intent without changing ordinary Resume semantics."""
    intent.add_argument(
        "--restart-request", metavar="JSON",
        help="Explicit frozen restart request: exact-original native Resume with a "
        "durable uncertainty receipt; automatic recovery remains disabled",
    )
    intent.add_argument(
        "--restart-ack", metavar="REQUEST-ID",
        help="Acknowledge the restart seed from the resumed original native process",
    )
    parser.add_argument(
        "--status-reconciled", action="store_true",
        help="Explicitly attest completed STATUS FIRST reconciliation with --restart-ack",
    )
    parser.add_argument(
        "--restart-receipt", action="store_true",
        help="Read only the owner receipt for --restart-request, without admission or launch",
    )


def decode_json(raw: str) -> object:
    """Reject ambiguous duplicate fields and excessive nesting."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Repeated restart JSON field")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=unique)
    except RecursionError as exc:
        raise ValueError("Restart JSON nesting exceeds its budget") from exc


def validate_request(value: object) -> dict:
    """Validate bounded frozen identities; caller input is never launch authority."""
    if not isinstance(value, dict) or set(value) != {
        "request_id", "expected", "source_epoch", "native_incarnation",
    }:
        raise ValueError("Invalid exact restart request")
    if not isinstance(value["request_id"], str) or str(UUID(value["request_id"])) != value["request_id"]:
        raise ValueError("Restart request requires a canonical UUID")
    expected = native_restart_check.validate_expected(value["expected"])
    source = value["source_epoch"]
    # Reuse the strict epoch schema, without treating this old epoch as current.
    native_restart_check.validate_expected({**expected, "target_epoch": source})
    if any(source[field] != expected["target_epoch"][field] for field in ("platform", "environment_id")):
        raise ValueError("Restart source has a foreign-environment")
    if source["boot_id"] == expected["target_epoch"]["boot_id"]:
        raise ValueError("Restart source is same-boot")
    incarnation = value["native_incarnation"]
    if not isinstance(incarnation, str) or re.fullmatch(r"[1-9][0-9]{0,9}:[0-9]{1,32}", incarnation) is None:
        raise ValueError("Invalid restart native process incarnation")
    pid, birth = incarnation.split(":")
    if native_source_context.identity(expected["native_session_id"], int(pid), birth, source) != expected["source_identity"]:
        raise ValueError("Restart source-identity does not match the recorded activation")
    return value


def _request(args: argparse.Namespace) -> dict:
    raw = getattr(args, "restart_request", None)
    if raw is None:
        raise ValueError("--restart-receipt requires --restart-request")
    if getattr(args, "status_reconciled", False):
        raise ValueError("--status-reconciled requires --restart-ack")
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 8192:
        raise ValueError("Restart request exceeds its budget")
    if any(getattr(args, name, None) for name in _CONFLICTS):
        raise ValueError("Restart request cannot mix other launch intents or arguments")
    request = validate_request(decode_json(raw))
    if getattr(args, "worktree_id", None) != request["expected"]["worktree_id"]:
        raise ValueError("Restart request requires its explicit exact worktree id")
    return request


def _refusal(request: dict, path: Path) -> str | None:
    record = tracking.load_record(path)
    # Task snapshots are deliberately not generation reservations. Do not use
    # their provisional task_qualified bit to authorize an action-time launch.
    if not native_restart_claims.original_session_only(record, request["expected"]["native_session_id"]):
        return "action-time-claim-policy-unqualified"
    result = native_restart_check.check(request["expected"], loader=lambda: tracking.load_record(path))
    if result["worktree_qualified"] is not True:
        return result["reason"]
    if launch_seed_state.peek(path, read_only=True) is not None:
        return "pending-seed"
    return None


def _seed(request: dict) -> str:
    return (
        "Execution-environment restart recovery of this ORIGINAL native conversation. "
        "STATUS FIRST: before continuing work, inspect current worktree/head, objective, "
        "tasks, claims, holds, and outstanding external operations through their owners. "
        "Reconcile changed or completed work; stop on uncertainty or missing authority. "
        "This seed is not renewed permission for physical, service, deployment, or other "
        "consequential operations. Launch and registration are NOT acknowledgement of "
        "this seed or completed recovery. "
        f"Recovery request: {request['request_id']}."
        " AFTER completing that reconciliation, use this session's attributable "
        "agent-worktrees command from its session catalog with these arguments: "
        f"embody --worktree-id {request['expected']['worktree_id']} "
        f"--restart-ack {request['request_id']} --status-reconciled --json. "
        "Do not acknowledge before checking status; report refusal or uncertainty."
    )


def _emit(receipt: dict, *, repeated: bool = False) -> int:
    launched = receipt["state"] in {"mux-created", "seed-acknowledged"}
    output._json_output({
        **receipt, "ok": launched, "repeated": repeated,
        **({} if launched else {"error": "Native launch outcome is uncertain; do not retry"}),
    })
    return 0 if launched else 4


def cmd_embody(args: argparse.Namespace) -> int:
    """Opt-in restart branch of embody; never called by automatic recovery."""
    from . import handoff_cli as cli

    if getattr(args, "restart_ack", None) is not None or getattr(args, "status_reconciled", False):
        from .native_restart_ack import cmd_acknowledge

        return cmd_acknowledge(args)
    try:
        request = _request(args)
        expected = request["expected"]
        path = cli.cfg.tracking_dir() / f"{expected['worktree_id']}.yaml"
        # Epoch qualification precedes Linux-only private-state primitives.
        epoch = native_source_context.epoch()
        inspecting = getattr(args, "restart_receipt", False)
        fields = ("platform", "environment_id") if inspecting else ("platform", "environment_id", "boot_id")
        if epoch is None or any(epoch[key] != expected["target_epoch"][key] for key in fields):
            raise ValueError("Restart target has a foreign-environment")
        if inspecting:
            receipt = native_restart_receipt.find(path, request)
            output._json_output(receipt or {
                "schema": "native-restart-launch", "version": 1, "request": request,
                "state": "not-reserved", "seed_acknowledged": False,
                "restored": False, "automatic_eligible": False,
            })
            return 0
        config = cli.cfg.load_config()
        record = tracking.load_record(path)
        from . import agent_policy

        agent_policy.require(record.worktree_path)
        repo = cli._repo_for_record(config, record)
        if repo is None or repo is not config.default_repo:
            raise ValueError("Restart target does not match the selected repository")
        if getattr(getattr(config, "profile_assignment", None), "armed", False):
            raise ValueError("profile-policy-unqualified")
        if repo.launch:
            raise ValueError("Restart custom launch templates are unqualified")
        if (
            not repo.setup_hook.get("linux") and not repo.env_script.get("linux")
            and not repo.copilot_path.get("linux")
            and (Path(repo.anchor) / "tools" / "setup" / "setup.sh").exists()
        ):
            raise ValueError("Restart legacy setup launch is unqualified")
        if getattr(args, "dry_run", False):
            reason = _refusal(request, path)
            if reason:
                raise ValueError(reason)
            output._json_output({
                "ok": True, "dry_run": True, "request": request,
                "launch_authorized": False, "automatic_eligible": False,
            })
            return 0
        with bridge_acquisition_launch.launch_fence(repo, path):
            receipt = native_restart_receipt.find(path, request)
            if receipt is not None:
                return _emit(receipt, repeated=True)
            previous = native_restart_receipt.read(path)
            if previous is not None:
                native_restart_receipt.validate_successor(previous, request)
            if reason := _refusal(request, path):
                raise ValueError(reason)
            preflight = cli._preflight_launch(config, args, expected["worktree_path"])
            if preflight.error:
                raise ValueError(preflight.error)
            command = cli._build_launch_cmd(
                config, args, expected["worktree_path"], profile=None, preflight=preflight,
            )
            command = embody_resume.with_seed(
                embody_resume.with_resume(command, expected["native_session_id"]), _seed(request),
            )
            env = cli._build_env(
                None, cli._repo_session_env(config, expected["worktree_path"]),
                work_dir=expected["worktree_path"],
            )
            with tracking._RecordLock(path, require_sidecar=True):
                if reason := _refusal(request, path):
                    raise ValueError(reason)
                receipt = {
                    "schema": "native-restart-launch", "version": 1, "request": request,
                    "state": "launch-uncertain", "seed_acknowledged": False,
                    "restored": False, "automatic_eligible": False,
                }
                native_restart_receipt.write(path, receipt, reserve=True)
                # Do not downgrade failures/timeouts to "not launched": mux may
                # already have created its descendant when the client fails.
                result = sessions.mux_new_session(
                    expected["worktree_id"], expected["worktree_path"], command, env,
                )
                if result.get("ok") is True and result.get("created") is not False:
                    receipt = {**receipt, "state": "mux-created"}
                    native_restart_receipt.write(path, receipt, reserve=False)
            return _emit(receipt)
    except (OSError, ValueError, TimeoutError) as exc:
        return output._json_error(f"Restart Resume refused or uncertain: {exc}", exit_code=3)
