"""Thin PR-watch facade over the optional existing subscription owner."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

from agent_procutil import no_window_kwargs
from agent_procutil.owned_process import require_contained_worker, run_owned

_REQUIRED_CAPABILITIES = {
    "acknowledged_notifications/v1", "native_snapshots/v1", "continuous_notifications/v1",
}
_TRANSITIONS = (
    "merged", "closed", "head_changed", "votes_changed", "approvals_changed",
    "threads_changed", "policies_changed", "gates_changed", "mergeable_changed",
    "checks_changed", "review_changed", "ready_for_human",
)


def _command(value: str | None) -> str:
    if not value or not Path(value).is_absolute() or not Path(value).is_file():
        raise ValueError("an attributable absolute command is required")
    return str(Path(value))


def _payload_commands(
    worktrees_command: str | None, owner_command: str | None,
) -> tuple[str, str | list[str]]:
    from . import registry_paths

    if "COPILOT_EXTENSIONS_CONTEXT" in os.environ:
        from .peer_launch_adapter import validate_context

        try:
            own = validate_context()
        except (OSError, ValueError, RuntimeError, ImportError) as error:
            raise ValueError("the subscription owner installation context was refused") from error
        if own is None:
            raise ValueError("the subscription owner requires a valid installation context")
        root = Path(own["payloadRoot"])
        suffix = ".cmd" if os.name == "nt" else ""
        worktrees = _command(str(root / "bin" / f"agent-worktrees{suffix}"))
        if owner_command is not None or (
            worktrees_command is not None and Path(worktrees_command).resolve() != Path(worktrees).resolve()
        ):
            raise ValueError("cell peer commands cannot be overridden")
        return worktrees, _cell_peer_prefix("agent-pull-requests")
    root = registry_paths._payload_root().resolve()
    peer = root.parent / "agent-pull-requests"
    for directory, name in ((root, "agent-worktrees"), (peer, "agent-pull-requests")):
        if directory.resolve().parent != root.parent:
            raise ValueError("peer payload escaped the selected marketplace")
        manifest = json.loads((directory / "plugin.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("name") != name:
            raise ValueError("selected peer payload ownership is unavailable")
    suffix = ".cmd" if os.name == "nt" else ""
    worktrees = root / "bin" / f"agent-worktrees{suffix}"
    owner = peer / "bin" / f"agent-pull-requests{suffix}"
    for expected, override in ((worktrees, worktrees_command), (owner, owner_command)):
        _command(str(expected))
        if override is not None and Path(_command(override)).resolve() != expected.resolve():
            raise ValueError("command override does not belong to the selected payload")
    return str(worktrees), str(owner)


def _cell_peer_prefix(peer: str) -> list[str]:
    from ._peer_launch import launch_prefix
    from .peer_launch_adapter import validate_context

    own = validate_context()
    if own is None:
        raise ValueError("a validated installation context is required")
    return launch_prefix(
        "agent-worktrees", Path(own["pluginRoot"]), str(own["installReceipt"]), peer,
    )


def _run(
    command: str | list[str], arguments: list[str], *, timeout: float = 30.0,
    input_payload: dict | None = None, input_text: str | None = None, owned: bool = False,
    inherit_process_group: bool = False,
) -> dict:
    if any(any(character in argument for character in "\0\r\n&|<>^%!") for argument in arguments):
        raise ValueError("invalid owner command argument")
    input_value = json.dumps(input_payload) if input_payload is not None else input_text
    if isinstance(command, str):
        launch = [_command(command)]
    else:
        if not command or any(not isinstance(argument, str) or not argument for argument in command):
            raise ValueError("invalid attributed owner prefix")
        launch = [_command(command[0]), *command[1:]]
    if owned:
        result = run_owned(
            [*launch, *arguments], timeout=timeout, input_text=input_value,
            inherit_process_group=inherit_process_group,
        )
    else:
        if inherit_process_group:
            raise ValueError("inherited work requires the canonical bounded child runner")
        result = subprocess.run(
            [*launch, *arguments], capture_output=True, text=True,
            encoding="utf-8", errors="strict", timeout=timeout, check=False,
            input=input_value, **no_window_kwargs(),
        )
    try:
        payload = json.loads(result.stdout)
    except (TypeError, ValueError):
        raise ValueError("subscription owner returned an invalid response") from None
    if not isinstance(payload, dict):
        raise ValueError("subscription owner returned an invalid response")
    if result.returncode or payload.get("error"):
        raise ValueError("subscription owner operation failed; reconcile before retry")
    return payload


def dispatch_owner(
    argv: list[str], *, worktrees_command: str | None = None,
    owner_command: str | None = None,
) -> int | None:
    """Dispatch subscribe/stop with already-resolved, attributable peer commands."""
    if argv[:1] == ["deliver"]:
        return deliver(argv[1:])
    if not argv or argv[0] not in ("subscribe", "stop", "ownerstatus"):
        return None
    parser = argparse.ArgumentParser(prog="pr-watch")
    verbs = parser.add_subparsers(dest="verb", required=True)
    subscribe = verbs.add_parser("subscribe")
    subscribe.add_argument("repo")
    subscribe.add_argument("pr", type=int)
    subscribe.add_argument("--notify", required=True)
    subscribe.add_argument("--bridge", required=True)
    subscribe.add_argument("--json", action="store_true")
    subscribe.add_argument("--pull-requests")
    stop = verbs.add_parser("stop")
    stop.add_argument("--notify", required=True)
    stop.add_argument("--json", action="store_true")
    stop.add_argument("--pull-requests")
    owner_status = verbs.add_parser("ownerstatus")
    owner_status.add_argument("--json", action="store_true")
    owner_status.add_argument("--pull-requests")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    subscriber = f"pr-watch:{getattr(args, 'notify', '')}"
    try:
        worktrees, owner = _payload_commands(
            worktrees_command, args.pull_requests or owner_command,
        )
        if args.verb == "ownerstatus":
            status = _run(owner, ["watch", "status", "--json"])
            if not _REQUIRED_CAPABILITIES <= set(status.get("capabilities", [])):
                raise ValueError("subscription owner status is incompatible")
            print(json.dumps(status))
            return 0
        if args.verb == "subscribe":
            bridge = _command(args.bridge)
            if args.pr <= 0 or not args.notify.strip():
                raise ValueError("a positive PR and notification worktree are required")
            command = [
                "watch", "subscribe", "--repo", args.repo, "--number", str(args.pr),
                "--subscriber-id", subscriber, "--worktrees-command", worktrees,
                "--continuous", "--acknowledged-notifications", "--json",
                "--notify-stdin",
            ]
            for transition in _TRANSITIONS:
                command.extend(["--until", transition])
            callback = [
                sys.executable, "-m", "agent_worktrees.pr_watch_owner",
                "deliver", "--contained-worker", "--notify", args.notify, "--bridge", bridge,
            ]
            if "COPILOT_EXTENSIONS_CONTEXT" in os.environ:
                callback = [
                    *_cell_peer_prefix("agent-worktrees"), "pr-watch",
                    "deliver", "--contained-worker", "--notify", args.notify, "--bridge", bridge,
                ]
            response = _run(owner, command, input_payload={"argv": callback}, timeout=45.0)
            if (
                response.get("registered") is not True
                or response.get("notification_protocol") != "acknowledged_notifications/v1"
                or response.get("snapshot_protocol") != "agent-worktrees.pr-snapshot/v1"
                or response.get("continuous") is not True
                or not isinstance(response.get("registration_id"), str)
                or not response["registration_id"]
            ):
                raise ValueError("ambiguous registration; reconcile before retry")
            print(json.dumps(response))
            return 0
        status = _run(owner, ["watch", "status", "--json"])
        if not args.notify.strip():
            raise ValueError("notification worktree is required")
        if (
            not _REQUIRED_CAPABILITIES <= set(status.get("capabilities", []))
            or not isinstance(status.get("registrations"), list)
            or not isinstance(status.get("in_flight"), list)
        ):
            raise ValueError("subscription owner stop status is unavailable")
        stopped, running = [], []
        for entry in status["registrations"]:
            if not isinstance(entry, dict):
                raise ValueError("invalid subscription status")
            if entry.get("subscriber_id") != subscriber:
                continue
            if (
                not isinstance(entry.get("repo"), str) or type(entry.get("number")) is not int
                or not isinstance(entry.get("registration_id"), str)
                or not entry["registration_id"]
                or not isinstance(entry.get("worktrees_command"), str)
                or not entry["worktrees_command"]
            ):
                raise ValueError("invalid subscription status")
            response = _run(owner, [
                "watch", "unsubscribe", "--repo", entry["repo"],
                "--number", str(entry["number"]), "--subscriber-id", subscriber,
                "--registration-id", entry["registration_id"],
                "--worktrees-command", entry["worktrees_command"], "--json",
            ])
            target = {"repo": entry["repo"], "pr": entry["number"]}
            if (
                response.get("unregistered") is True
                and response.get("registration_id") == entry["registration_id"]
                and response.get("in_flight") is False
            ):
                stopped.append(target)
            else:
                running.append(target)
        final = _run(owner, ["watch", "status", "--json"])
        if (
            not _REQUIRED_CAPABILITIES <= set(final.get("capabilities", []))
            or not isinstance(final.get("registrations"), list)
            or not isinstance(final.get("in_flight"), list)
        ):
            raise ValueError("subscription owner final status is unavailable")
        for entry in final["registrations"] + final["in_flight"]:
            if not isinstance(entry, dict):
                raise ValueError("invalid subscription owner final status")
            if entry.get("subscriber_id") == subscriber:
                running.append({"repo": entry.get("repo"), "pr": entry.get("number")})
        print(json.dumps({"stopped": stopped, "still_running": running}))
        return 1 if running else 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, UnicodeError):
        result = {"error": "PR observation owner unavailable or incompatible"}
        if args.verb == "stop":
            result.update(stopped=[], still_running=[{"notify": args.notify, "state": "unknown"}])
        print(json.dumps(result))
        return 1


def _delivery_parser():
    parser = argparse.ArgumentParser(prog="pr-watch owner callback")
    parser.add_argument("--notify", required=True)
    parser.add_argument("--bridge", required=True)
    parser.add_argument("--contained-worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def deliver(argv: list[str]) -> int:
    """Supervise a standalone delivery without relying on ambient containment."""
    try:
        args = _delivery_parser().parse_args(argv)
        if args.contained_worker:
            return deliver_contained(argv)
        raw = sys.stdin.read(2_097_153)
        if len(raw.encode("utf-8")) > 2_097_152:
            return 1
        completed = run_owned(
            [
                sys.executable, "-I", "-m", "agent_worktrees.pr_watch_owner", "deliver", "--contained-worker",
                "--notify", args.notify, "--bridge", args.bridge,
            ],
            timeout=30.0, input_text=raw, capture=False,
        )
        return 0 if completed.returncode == 0 else 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, UnicodeError):
        return 1


def deliver_contained(argv: list[str]) -> int:
    """Use only the verified outer scope; ACK remains durable inbox admission."""
    try:
        require_contained_worker()
        args = _delivery_parser().parse_args(argv)
        raw = sys.stdin.read(2_097_153)
        if len(raw.encode("utf-8")) > 2_097_152:
            return 1
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or payload.get("notification_protocol") != "acknowledged_notifications/v1"
            or payload.get("subscriber_id") != f"pr-watch:{args.notify}"
            or any(not isinstance(payload.get(field), str) or not payload[field]
                   for field in ("event_id", "registration_id", "repo"))
            or type(payload.get("number")) is not int or payload["number"] <= 0
            or not isinstance(payload.get("transitions"), list)
            or type(payload.get("timed_out")) is not bool
        ):
            return 1
        snapshot = payload.get("snapshot")
        if not payload["timed_out"] and (
            not isinstance(snapshot, dict)
            or snapshot.get("schema") != "agent-worktrees.pr-snapshot"
            or type(snapshot.get("version")) is not int or snapshot["version"] != 1
            or snapshot.get("repo") != payload["repo"]
            or type(snapshot.get("pr")) is not int or snapshot["pr"] != payload["number"]
        ):
            return 1
        bridge = (
            _cell_peer_prefix("agent-bridge")
            if "COPILOT_EXTENSIONS_CONTEXT" in os.environ else _command(args.bridge)
        )
        live = _run(
            bridge, ["--json", "live-sessions", "resolve", "--handle", args.notify],
            timeout=8.0, owned=True, inherit_process_group=True,
        )
        session = live.get("session_id")
        if not isinstance(session, str) or not session:
            return 1
        message = (
            "A watched pull request changed. Re-read pr-watch status before acting. "
            "Human-owned gates remain human-owned. This is observation, not merge authority.\n"
            + json.dumps(payload, sort_keys=True)
        )
        admitted = _run(
            bridge, [
                "--json", "send", args.notify, "--expected-session-id", session,
                "--min-daemon-protocol", "39", "--idempotency-key", payload["event_id"],
                "--kind", "prompt", "--delivery", "queue", "--no-wait", "--prompt-file", "-",
            ],
            timeout=12.0, input_text=message, owned=True, inherit_process_group=True,
        )
        receipt = admitted.get("receipt")
        message_id = admitted.get("message_id")
        return 0 if (
            admitted.get("ok") is True and admitted.get("receipt_current") is True
            and admitted.get("receipt_error") is None
            and admitted.get("session_id") == session
            and type(message_id) is int and message_id > 0
            and isinstance(receipt, dict)
            and receipt.get("session_id") == session
            and type(receipt.get("message_id")) is int
            and receipt["message_id"] == message_id
            and receipt.get("stage") in ("admitted", "sdk_accepted", "consumed")
            and type(receipt.get("admitted_at")) in (int, float)
            and math.isfinite(receipt["admitted_at"]) and receipt["admitted_at"] >= 0
        ) else 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, UnicodeError):
        return 1


def dispatch(argv: list[str]) -> int | None:
    """Route owner verbs, leaving all other PR-watch commands to their existing owner."""
    return dispatch_owner(argv)


if __name__ == "__main__":
    raise SystemExit(dispatch_owner(sys.argv[1:]) or 0)
