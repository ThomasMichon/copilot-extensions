"""Finite backend handoffs for a native owner; never spawn or wait for Copilot."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from . import agent_policy, config as cfg, launch_execution, launch_seed_exec, launch_seed_state, locks
from . import tracking_write

_ERRORS = (ValueError, OSError, TimeoutError, tracking_write.AmbiguousWriteOutcome)


def require_current_runtime(root: Path) -> None:
    """Native callers may use only this root's current completed runtime."""
    prefix = Path(sys.prefix)
    if (
        not root.is_absolute() or prefix.parent.name != "versions"
        or not os.path.samefile(prefix.parent.parent, root)
        or not os.path.samefile(cfg.install_dir(), root)
        or (root / "current-version").read_text(encoding="utf-8").strip() != prefix.name
        or not (prefix / ".install-complete.json").is_file()
        or not (prefix / ".runtime-bootstrap" / "runtime_launcher.py").is_file()
    ):
        raise ValueError("Native handoff requires this installation's current completed runtime")


def prepare(
    record_path: Path, command: list[str], *, seed_id: str | None,
    correlation: launch_execution.Correlation | None = None,
) -> dict:
    """Claim once before native spawn; only argv crosses the in-memory pipe."""
    if not command or not all(isinstance(arg, str) for arg in command):
        raise ValueError("A literal backend command is required")
    if not launch_seed_exec._valid_request(record_path, invoke=True, seed_id=seed_id):
        raise ValueError("Backend launch policy refused preparation")
    execution = launch_execution.Execution(
        record_path, correlation or launch_execution.Correlation(uuid4().hex, uuid4().hex),
    )
    execution.append("execution-requested")
    try:
        prepared = launch_seed_exec._prepare(
            record_path, command, invoke=True, seed_id=seed_id, execution=execution,
        )
        if execution.failed:
            raise ValueError("Backend handoff observation is unconfirmed")
    except (*_ERRORS, KeyboardInterrupt):
        execution.observed("unknown")
        raise
    if isinstance(prepared, int):
        execution.observed("execution-returned", exit_code=prepared)
        raise ValueError("Backend launch preparation was refused")
    return {
        "version": 1, "argv": prepared.argv, "environment": prepared.environment,
        "attempt_id": execution.subject["attempt_id"],
        "resolution_id": execution.subject["resolution_id"],
        "operation_id": execution.subject["operation_id"],
        "parent_id": execution.subject["parent_id"],
    }


def _load(
    record_path: Path, correlation: launch_execution.Correlation, operation_id: str,
) -> tuple[launch_execution.Execution, dict]:
    agent_policy.require_record(record_path)
    execution = launch_execution.Execution(record_path, correlation)
    saved = launch_execution.read(record_path.parent, correlation.attempt_id, operation_id)
    expected = {**execution.subject, "operation_id": operation_id}
    if {key: value for key, value in saved.items() if key != "events"} != expected:
        raise ValueError("Backend execution identity or incarnation changed")
    execution.subject = expected
    if saved["events"][-1]["phase"] in {"execution-returned", "unknown"}:
        raise ValueError("Backend handoff is terminal or unconfirmed; do not replay it")
    return execution, saved


def _receipt(record_path: Path, saved: dict) -> dict | None:
    claims = [event["details"] for event in saved["events"] if event["phase"] == "seed-claimed"]
    if not claims:
        return None
    current = launch_seed_state.peek(record_path)
    claim, = claims
    if current is None or (
        current.seed_id != claim["seed_id"] or current.handoff_id != claim["handoff_id"]
    ):
        raise ValueError("Backend seed handoff is missing or superseded")
    return {"seed": asdict(current)}


def spawned(
    record_path: Path, correlation: launch_execution.Correlation, operation_id: str,
    *, pid: int, process_start_time: str,
) -> None:
    """The native caller retains the actual child handle across this callback."""
    execution, saved = _load(record_path, correlation, operation_id)
    if any(event["phase"] == "process-spawned" for event in saved["events"]):
        raise ValueError("Backend spawn was already observed; do not replay it")
    if (
        type(pid) is not int or pid < 1 or not process_start_time
        or locks.process_start_time(pid) != process_start_time
    ):
        raise ValueError("Backend child identity is unconfirmed")
    receipt = _receipt(record_path, saved)
    execution.observed(
        "process-spawned", pid=pid, process_start_time=process_start_time,
        boundary="backend-command",
    )
    if receipt:
        if launch_seed_state.finish(record_path, receipt) != {"finished": True}:
            raise ValueError("Backend started, but its seed completion is unconfirmed")
    if execution.failed:
        raise ValueError("Backend started, but its spawn observation is unconfirmed")


def spawn_failed(
    record_path: Path, correlation: launch_execution.Correlation, operation_id: str,
) -> None:
    """Only a definite native OS spawn failure permits the existing restoration."""
    execution, saved = _load(record_path, correlation, operation_id)
    if any(event["phase"] == "process-spawned" for event in saved["events"]):
        raise ValueError("A spawned backend cannot restore its seed")
    receipt = _receipt(record_path, saved)
    if receipt:
        if launch_seed_state.restore(record_path, receipt) != {"restored": True}:
            raise ValueError("Backend seed restoration is unconfirmed")
    execution.append("execution-returned", exit_code=3)


def returned(
    record_path: Path, correlation: launch_execution.Correlation, operation_id: str,
    *, exit_code: int, receipt_uncertain: bool = False,
) -> None:
    execution, saved = _load(record_path, correlation, operation_id)
    if (
        type(exit_code) is not int or type(receipt_uncertain) is not bool
        or saved["events"][-1]["phase"] != "process-spawned"
    ):
        raise ValueError("Backend return requires its recorded spawn and actual exit code")
    execution.failed = receipt_uncertain
    execution.process_exit_code = exit_code
    execution.append("execution-returned", exit_code=exit_code or (3 if receipt_uncertain else 0))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract-version", type=int, choices=[1], required=True)
    parser.add_argument("--phase", choices=["prepare", "spawned", "spawn-failed", "returned"], required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--seed-id")
    parser.add_argument("--launch-attempt-id")
    parser.add_argument("--launch-resolution-id")
    parser.add_argument("--launch-parent-id")
    parser.add_argument("--operation-id")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--process-start-time")
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--receipt-uncertain", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        require_current_runtime(args.root)
        correlation = launch_execution.Correlation(
            args.launch_attempt_id, args.launch_resolution_id, args.launch_parent_id,
        ) if any((args.launch_attempt_id, args.launch_resolution_id, args.launch_parent_id)) else None
        if args.phase == "prepare":
            if args.receipt_uncertain or any(value is not None for value in (
                args.operation_id, args.pid, args.process_start_time, args.exit_code,
            )):
                raise ValueError("Preparation cannot include a backend outcome")
            result = prepare(args.record, command, seed_id=args.seed_id, correlation=correlation)
        else:
            if not correlation or not args.operation_id or command or args.seed_id:
                raise ValueError("Backend callback requires only its original execution identity")
            if args.phase == "spawned" and args.exit_code is None and not args.receipt_uncertain:
                spawned(
                    args.record, correlation, args.operation_id,
                    pid=args.pid, process_start_time=args.process_start_time,
                )
            elif args.phase == "spawn-failed" and not args.receipt_uncertain and all(value is None for value in (
                args.pid, args.process_start_time, args.exit_code,
            )):
                spawn_failed(args.record, correlation, args.operation_id)
            elif args.phase == "returned" and args.pid is None and args.process_start_time is None:
                returned(
                    args.record, correlation, args.operation_id, exit_code=args.exit_code,
                    receipt_uncertain=args.receipt_uncertain,
                )
            else:
                raise ValueError("Invalid backend outcome arguments")
            result = {"recorded": True}
    except (*_ERRORS, KeyboardInterrupt):
        print("Native backend handoff is unconfirmed; do not automatically retry.", file=sys.stderr)
        return 3
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
