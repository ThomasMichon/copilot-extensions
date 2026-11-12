"""Finite backend handoffs for a native owner; never spawn or wait for Copilot."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import asdict
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4

from . import agent_policy, config as cfg, launch_attempt, launch_execution, locks
from . import tracking_write

_ERRORS = (ValueError, OSError, TimeoutError, tracking_write.AmbiguousWriteOutcome, tracking_write.ResidentRecoveryHeld)
NUL_REPLY_SCHEMA = "agent-worktrees.native-seed-reply.v1"


def nul_reply(prepared: dict) -> bytes:
    """Literal UTF-8 fields for a finite Bash reader, never shell source."""
    environment = prepared["environment"]
    fields = [
        NUL_REPLY_SCHEMA,
        prepared["attempt_id"], prepared["resolution_id"], prepared["operation_id"],
        prepared["parent_id"] or "", prepared.get("setup_seed_id") or "",
        str(len(environment)) if environment is not None else "-1",
    ]
    if environment is not None:
        for key, value in environment.items():
            if not isinstance(key, str) or not key or "=" in key:
                raise ValueError("Invalid native backend environment name")
            fields.extend((key, value))
    fields.extend((str(len(prepared["argv"])), *prepared["argv"], "complete"))
    if prepared["version"] != 1 or not prepared["argv"] or not prepared["argv"][0] or not all(
        isinstance(field, str) and "\0" not in field for field in fields
    ):
        raise ValueError("Native backend reply cannot be represented as literal fields")
    return b"\0".join(field.encode("utf-8") for field in fields) + b"\0"


def require_current_runtime(root: Path) -> None:
    """Native callers may use only this root's current completed runtime."""
    prefix = Path(sys.prefix).resolve()
    slot = prefix.parent if prefix.name == ".venv" else prefix
    if (
        not root.is_absolute() or slot.parent.name != "versions"
        or not os.path.samefile(slot.parent.parent, root)
        or not os.path.samefile(cfg.runtime_install_dir(), root)
        or (root / "current-version").read_text(encoding="utf-8").strip() != slot.name
        or not (slot / ".install-complete.json").is_file()
        or not (slot / ".runtime-bootstrap" / "runtime_launcher.py").is_file()
    ):
        raise ValueError("Native handoff requires this installation's current completed runtime")


def prepare(
    record_path: Path, command: list[str], *, seed_id: str | None,
    correlation: launch_execution.Correlation | None = None,
    setup: bool = False, admission_only: bool = False, preserve_seed_id: str | None = None,
    native_resume_session: str | None = None, pid: int | None = None,
    process_start_time: str | None = None,
    headed: bool = False,
) -> dict:
    """Claim once before native spawn; only argv crosses the in-memory pipe."""
    from . import launch_admission, launch_seed_exec

    if not command or not command[0] or not all(isinstance(arg, str) and "\0" not in arg for arg in command):
        raise ValueError("A literal backend command is required")
    if setup and headed:
        raise ValueError("Headed preparation belongs to the backend owner")
    if not setup and (pid or process_start_time):
        raise ValueError("Backend preparation cannot include setup admission")
    if setup:
        if process_start_time is None and type(pid) is int and pid > 0:
            process_start_time = locks.process_start_time(pid)
        _require_process(pid, process_start_time)
    invoke = not setup and not admission_only
    if not launch_seed_exec._valid_request(
        record_path, invoke=invoke, seed_id=seed_id, admission_only=admission_only,
        preserve_seed_id=preserve_seed_id, native_resume_session=native_resume_session,
    ):
        raise ValueError("Backend launch policy refused preparation")
    execution = launch_execution.Execution(
        record_path, correlation or launch_execution.Correlation(uuid4().hex, uuid4().hex),
    )
    execution.append("execution-requested")
    try:
        _retain_planned_status(record_path, execution)
    except (*_ERRORS, KeyboardInterrupt):
        execution.observed("unknown")
        raise
    try:
        # Admission-only preparation performs no seed mutation. Seeded preparation
        # uses the existing seed authority's own guards, never a nested lease.
        with launch_admission.guard(record_path) if admission_only else nullcontext():
            prepared = launch_seed_exec._prepare(
                record_path, command, invoke=invoke, seed_id=seed_id,
                admission_only=admission_only, preserve_seed_id=preserve_seed_id,
                native_resume_session=native_resume_session, execution=execution,
            )
        if execution.failed:
            raise ValueError("Backend handoff observation is unconfirmed")
    except (*_ERRORS, KeyboardInterrupt):
        execution.observed("unknown")
        raise
    if isinstance(prepared, int):
        execution.observed("execution-returned", exit_code=prepared)
        raise ValueError("Backend launch preparation was refused")
    if setup:
        try:
            _require_process(pid, process_start_time)
            execution.append(
                "process-spawned", pid=pid, process_start_time=process_start_time,
                boundary="launcher-command",
            )
        except (*_ERRORS, KeyboardInterrupt):
            execution.observed("unknown")
            raise
    result = {
        "version": 1, "argv": prepared.argv, "environment": prepared.environment,
        "attempt_id": execution.subject["attempt_id"],
        "resolution_id": execution.subject["resolution_id"],
        "operation_id": execution.subject["operation_id"],
        "parent_id": execution.subject["parent_id"],
        **({"setup_seed_id": prepared.setup_seed_id} if setup else {}),
    }
    if headed and (selected := _headed_command(prepared.argv)) is not None:
        from agent_remote_driver.headed.launcher import prepare_headed_command
        from agent_remote_driver.headed.receipt import NONCE_ENV

        argv, session_id = selected
        attempt = record_path.parent / "launch-attempts" / result["attempt_id"] / result["operation_id"]
        state_file = attempt / "headed.json"
        result["argv"] = prepare_headed_command(
            argv, session_id=session_id, attempt_directory=attempt,
            state_file=state_file, cwd=os.getcwd(),
        )
        result["environment"] = {**(prepared.environment or os.environ), NONCE_ENV: secrets.token_hex(32)}
        result["headed"] = {
            "version": 1, "argv": argv, "session_id": session_id,
            "state_file": str(state_file), "cwd": os.getcwd(),
        }
    return result


def _headed_command(command: list[str]) -> tuple[list[str], str] | None:
    """Select new identity only for an unambiguous local interactive invocation."""
    from .launch_seed_exec import _headed_session
    from .native_arguments import VALUE_OPTIONS, _insert

    if not command or Path(command[0]).name.lower() not in {"copilot", "copilot.exe"}:
        return None
    has_identity = False
    index = 1
    while index < len(command):
        name, separator, _value = command[index].partition("=")
        if name == "--":
            break
        if name in {
            "--connect", "--ahp", "--continue", "--acp", "--stdio", "-p", "--prompt",
            "--help", "-h", "--version", "-v", "--environment-host", "--environment-compute-id",
        } or not name.startswith("-"):
            return None
        if name in {"--resume", "-r", "--session-id"}:
            has_identity = True
            if name == "-r":
                return None
            if not separator:
                index += 1
                if index >= len(command) or command[index].startswith("-"):
                    return None
        elif not separator and name in VALUE_OPTIONS | {"-C"}:
            index += 1
            if index >= len(command):
                return None
        elif not separator and name in {
            "--allow-tool", "--deny-tool", "--available-tools", "--excluded-tools",
            "--allow-url", "--deny-url", "--secret-env-vars",
        }:
            while index + 1 < len(command) and not command[index + 1].startswith("-"):
                index += 1
        index += 1
    session = _headed_session(command)
    if session:
        return command, session
    if has_identity:
        return None
    session = str(uuid4())
    return [command[0], *_insert(command[1:], ["--session-id", session])], session


def admit(
    record_path: Path, correlation: launch_execution.Correlation, operation_id: str,
    *, admission_only: bool = False, preserve_seed_id: str | None = None,
    native_resume_session: str | None = None, planned_status: str | None = None,
) -> None:
    """Recheck the original launch immediately before its retained coordinator creates native."""
    if native_resume_session is not None:
        # The Windows native parent owns the head-writer fence through Start().
        # This finite checker must not acquire its parent's record/launch locks.
        _admission_check(
            record_path, correlation, operation_id, admission_only=admission_only,
            preserve_seed_id=preserve_seed_id, native_resume_session=native_resume_session,
            planned_status=planned_status,
        )()
        return
    with admission(
        record_path, correlation, operation_id, admission_only=admission_only,
        preserve_seed_id=preserve_seed_id, native_resume_session=native_resume_session,
        planned_status=planned_status,
    ):
        pass


_TERMINAL_STATUSES = frozenset({"finalizing", "complete", "completed", "finalized", "archived"})
# Resolve deliberately reopens these with their checkout retained; only a
# transition into them after the launch started is changed intent.
_RESUMABLE_STATUSES = frozenset({"complete", "completed", "finalized"})


def _planned_status_path(record_path: Path, attempt_id: str, operation_id: str) -> Path:
    receipt = launch_attempt._path(record_path.parent, attempt_id, operation_id, execution=True)
    return receipt.with_name(receipt.name.removesuffix(".execution.json") + ".planned-status.json")


def _retain_planned_status(record_path: Path, execution: launch_execution.Execution) -> None:
    """Persist the preparation-time status for separate native admission callbacks."""
    path = _planned_status_path(
        record_path, execution.subject["attempt_id"], execution.subject["operation_id"],
    )
    launch_attempt._mkdir(path.parent)
    launch_attempt._write(path, {"version": 1, "status": execution.planned_status})


def _read_planned_status(record_path: Path, attempt_id: str, operation_id: str) -> str | None:
    """Return the retained status, or None (strict refusal) when absent or invalid."""
    try:
        path = _planned_status_path(record_path, attempt_id, operation_id)
        with path.open("rb") as stream:
            raw = stream.read(1025)
        value = json.loads(raw) if len(raw) <= 1024 else None
    except (OSError, ValueError):
        return None
    if (
        not isinstance(value, dict) or set(value) != {"version", "status"}
        or value["version"] != 1 or not isinstance(value["status"], str)
    ):
        return None
    return value["status"]


def _status_refused(status: str, planned_status: str | None) -> bool:
    if status not in _TERMINAL_STATUSES:
        return False
    return not (planned_status == status and status in _RESUMABLE_STATUSES)


@contextmanager
def admission(
    record_path: Path, correlation: launch_execution.Correlation, operation_id: str,
    *, admission_only: bool = False, preserve_seed_id: str | None = None,
    native_resume_session: str | None = None, require_cold_stop: bool = True,
    planned_status: str | None = None,
    head_fence: ExitStack | None = None,
):
    """Keep the original creation fence until the exec or retained owner releases it."""
    from . import launch_admission, tracking

    with (
        launch_admission.guard(record_path),
        ExitStack() as held,
    ):
        record_scope = head_fence if head_fence is not None else held
        if head_fence is not None:
            held.callback(head_fence.close)
        if native_resume_session is not None:
            record_scope.enter_context(tracking._RecordLock(record_path, require_sidecar=True))
        check = _admission_check(
            record_path, correlation, operation_id, admission_only=admission_only,
            preserve_seed_id=preserve_seed_id, native_resume_session=native_resume_session,
            require_cold_stop=require_cold_stop, planned_status=planned_status,
        )
        check()
        yield check


def _admission_check(
    record_path: Path, correlation: launch_execution.Correlation, operation_id: str,
    *, admission_only: bool = False, preserve_seed_id: str | None = None,
    native_resume_session: str | None = None, require_cold_stop: bool = True,
    planned_status: str | None = None,
):
    from . import launch_seed_exec, launch_seed_state, tracking

    if not require_cold_stop and (not admission_only or native_resume_session is not None):
        raise ValueError("Only ordinary admission-only execution may omit the cold-stop check")
    def check() -> None:
        _owner, saved = _load(record_path, correlation, operation_id)
        if any(event["phase"] == "process-spawned" for event in saved["events"]):
            raise ValueError("Original headed launch was already observed")
        receipt = _receipt(record_path, saved)
        planned = planned_status
        if planned is None:
            planned = _read_planned_status(record_path, correlation.attempt_id, operation_id)
        if admission_only:
            if receipt is not None or not launch_seed_exec._valid_request(
                record_path, invoke=False, seed_id=None, admission_only=True,
                preserve_seed_id=preserve_seed_id, native_resume_session=native_resume_session,
            ):
                raise ValueError("Admission-only creation cannot claim a prompt")
            prepared = launch_seed_exec._prepare(
                record_path, ["admission-check"], invoke=False, seed_id=None, admission_only=True,
                preserve_seed_id=preserve_seed_id, native_resume_session=native_resume_session,
            )
            if isinstance(prepared, int):
                raise ValueError("Original admission-only launch intent changed before creation")
        current = tracking.load_record(record_path)
        if _status_refused(current.status, planned):
            raise ValueError(
                f"Original headed launch intent changed before creation: "
                f"worktree status is {current.status!r}"
                + (f" (launch started as {planned!r})" if planned else "")
            )
        if (
            current.pending_seed
            or not admission_only and receipt is None and launch_seed_state.peek(record_path) is not None
        ):
            raise ValueError("Original headed launch intent changed before creation")
        if require_cold_stop and (error := launch_seed_exec._final_cold_error(current)):
            raise ValueError(error)
    return check


def _require_process(pid: int | None, process_start_time: str | None) -> None:
    if (
        type(pid) is not int or pid < 1 or not process_start_time
        or locks.process_start_time(pid) != process_start_time
    ):
        raise ValueError("Backend child identity is unconfirmed")


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
    from . import launch_seed_state

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
    headed_state_file: Path | None = None, headed_session_id: str | None = None,
    headed_nonce: str | None = None,
) -> None:
    """The caller retains its direct child, or an explicitly bound headed coordinator."""
    execution, saved = _load(record_path, correlation, operation_id)
    if any(event["phase"] == "process-spawned" for event in saved["events"]):
        raise ValueError("Backend spawn was already observed; do not replay it")
    _require_process(pid, process_start_time)
    if any(value is not None for value in (headed_state_file, headed_session_id, headed_nonce)):
        if headed_state_file is None or headed_session_id is None or headed_nonce is None:
            raise ValueError("Headed receipt requires the complete original caller binding")
        from agent_remote_driver.headed.receipt import native_child

        pid, process_start_time = native_child(
            headed_state_file, coordinator_pid=pid, coordinator_birth=process_start_time,
            session_id=headed_session_id, nonce=headed_nonce,
        )
        _require_process(pid, process_start_time)
    receipt = _receipt(record_path, saved)
    execution.observed(
        "process-spawned", pid=pid, process_start_time=process_start_time,
        boundary="backend-command",
    )
    if receipt:
        from . import launch_seed_state

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
        from . import launch_seed_state

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
        or saved["events"][-1]["phase"] != "process-spawned" and not receipt_uncertain
    ):
        raise ValueError("Backend return requires its recorded spawn and actual exit code")
    execution.failed = receipt_uncertain
    execution.process_exit_code = exit_code
    execution.append("execution-returned", exit_code=exit_code or (3 if receipt_uncertain else 0))


def interrupted(
    record_path: Path, correlation: launch_execution.Correlation, operation_id: str,
) -> None:
    execution, _ = _load(record_path, correlation, operation_id)
    execution.append("unknown")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--describe-contract", action="version", version=json.dumps({
        "contract": "agent-worktrees.native-seed", "version": 1,
        "preparation_output_formats": ["json", "nul"], "nul_reply_schema": NUL_REPLY_SCHEMA,
        "preparation_environment_modes": ["full", "execution-hint"],
        "setup_process_start_time_sources": ["caller", "provider"],
        "backend_execution_modes": [
            "posix-exec-v1", "posix-exec-env-v1", "posix-headed-env-v1",
        ] if os.name == "posix" else [],
        "backend_process_receipts": ["direct-v1", "headed-native-child-v1"],
        "backend_preparation_modes": ["direct-v1", "headed-live-owner-v1"],
        "setup_admission_modes": ["posix-live-owner-v1"] if os.name == "posix" else [],
    }))
    parser.add_argument("--contract-version", type=int, choices=[1], required=True)
    parser.add_argument("--output-format", choices=["json", "nul"], default="json")
    parser.add_argument("--environment-reply", choices=["full", "execution-hint"], default="full")
    parser.add_argument(
        "--phase", choices=["prepare", "setup", "admit", "spawned", "spawn-failed", "returned", "interrupted", "exec"],
        required=True,
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--seed-id")
    parser.add_argument("--admission-only", action="store_true")
    parser.add_argument("--preserve-seed-id")
    parser.add_argument("--native-resume-session")
    parser.add_argument("--launch-attempt-id")
    parser.add_argument("--launch-resolution-id")
    parser.add_argument("--launch-parent-id")
    parser.add_argument("--operation-id")
    parser.add_argument("--pid", type=int)
    parser.add_argument("--process-start-time")
    parser.add_argument("--headed-state-file", type=Path)
    parser.add_argument("--headed-session-id")
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--receipt-uncertain", action="store_true")
    parser.add_argument("--backend-mode", choices=["direct-v1", "headed-live-owner-v1"], default="direct-v1")
    parser.add_argument("--completion-file", type=Path)
    parser.add_argument("--backend-environment-fd", type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    try:
        if args.backend_mode != "direct-v1" and (
            args.phase not in {"prepare", "exec"}
            or args.output_format != "json" or args.environment_reply != "full"
        ):
            raise ValueError("Headed preparation requires a full JSON backend reply")
        if any(value is not None for value in (
            args.headed_state_file, args.headed_session_id,
        )) and (
            args.phase != "spawned" or args.headed_state_file is None
            or args.headed_session_id is None
        ):
            raise ValueError("Headed identity is only available for a complete spawn callback")
        if args.completion_file is not None and args.phase != "exec":
            raise ValueError("Exec completion cannot be used by another phase")
        if args.backend_environment_fd is not None and args.phase != "exec":
            raise ValueError("Backend environment cannot be used by another phase")
        if args.output_format == "nul" and args.phase not in {"prepare", "setup"}:
            raise ValueError("Literal field replies are only available for preparation")
        if args.environment_reply != "full" and args.phase not in {"prepare", "setup"}:
            raise ValueError("Environment reply modes are only available for preparation")
        if args.output_format == "nul":
            for value in (str(args.record), *command):
                value.encode("utf-8")
        require_current_runtime(args.root)
        correlation = launch_execution.Correlation(
            args.launch_attempt_id, args.launch_resolution_id, args.launch_parent_id,
        ) if any((args.launch_attempt_id, args.launch_resolution_id, args.launch_parent_id)) else None
        if args.phase in {"prepare", "setup"}:
            setup = args.phase == "setup"
            if (
                args.receipt_uncertain or args.operation_id is not None or args.exit_code is not None
                or (not setup and (args.pid is not None or args.process_start_time is not None))
            ):
                raise ValueError("Preparation cannot include a backend outcome")
            result = prepare(
                args.record, command, seed_id=args.seed_id, correlation=correlation, setup=setup,
                admission_only=args.admission_only, preserve_seed_id=args.preserve_seed_id,
                native_resume_session=args.native_resume_session, pid=args.pid,
                process_start_time=args.process_start_time,
                headed=args.backend_mode == "headed-live-owner-v1",
            )
            if args.environment_reply == "execution-hint":
                result["environment"] = {
                    key: value for key, value in (result["environment"] or {}).items()
                    if key == launch_execution.NATIVE_HINT_ENV
                }
        elif args.phase == "exec":
            if (
                not correlation or not args.operation_id or not args.completion_file
                or args.output_format != "json" or args.seed_id or args.receipt_uncertain
                or not args.admission_only and (args.preserve_seed_id or args.native_resume_session)
                or any(value is not None for value in (args.pid, args.process_start_time, args.exit_code))
            ):
                raise ValueError("Exec requires only its prepared backend identity and completion file")
            from .launch_seed_posix import execute, execute_headed

            execute_backend = execute_headed if args.backend_mode == "headed-live-owner-v1" else execute
            code = execute_backend(
                args.record, correlation, args.operation_id, command, args.completion_file,
                **({"backend_environment_fd": args.backend_environment_fd}
                   if args.backend_environment_fd is not None else {}),
                **({
                    "admission_only": True, "preserve_seed_id": args.preserve_seed_id,
                    "native_resume_session": args.native_resume_session,
                } if args.admission_only else {}),
            )
            if args.backend_mode == "direct-v1":
                print("Native backend exec was not confirmed; inspect the retained handoff.", file=sys.stderr)
            return code
        else:
            if (
                not correlation or not args.operation_id or command or args.seed_id
                or (args.admission_only or args.preserve_seed_id or args.native_resume_session)
                and args.phase != "admit"
                or not args.admission_only and (args.preserve_seed_id or args.native_resume_session)
            ):
                raise ValueError("Backend callback requires only its original execution identity")
            if args.phase == "spawned" and args.exit_code is None and not args.receipt_uncertain:
                from agent_remote_driver.headed.receipt import NONCE_ENV

                spawned(
                    args.record, correlation, args.operation_id,
                    pid=args.pid, process_start_time=args.process_start_time,
                    headed_state_file=args.headed_state_file,
                    headed_session_id=args.headed_session_id,
                    headed_nonce=os.environ.get(NONCE_ENV) if args.headed_state_file is not None else None,
                )
            elif args.phase == "admit" and not args.receipt_uncertain and all(value is None for value in (
                args.pid, args.process_start_time, args.exit_code,
            )):
                try:
                    admit(
                        args.record, correlation, args.operation_id,
                        admission_only=args.admission_only, preserve_seed_id=args.preserve_seed_id,
                        native_resume_session=args.native_resume_session,
                    )
                except ValueError as exc:
                    # The Windows responder relays this bounded line to the session host.
                    from agent_remote_driver.session_host.startup_admission import refusal_reason
                    print(f"Startup admission refused: {refusal_reason(exc)}", file=sys.stderr)
                    raise
            elif args.phase == "spawn-failed" and not args.receipt_uncertain and all(value is None for value in (
                args.pid, args.process_start_time, args.exit_code,
            )):
                spawn_failed(args.record, correlation, args.operation_id)
            elif args.phase == "returned" and args.pid is None and args.process_start_time is None:
                returned(
                    args.record, correlation, args.operation_id, exit_code=args.exit_code,
                    receipt_uncertain=args.receipt_uncertain,
                )
            elif args.phase == "interrupted" and not args.receipt_uncertain and all(value is None for value in (
                args.pid, args.process_start_time, args.exit_code,
            )):
                interrupted(args.record, correlation, args.operation_id)
            else:
                raise ValueError("Invalid backend outcome arguments")
            result = {"recorded": True}
        if args.output_format == "nul":
            try:
                encoded = nul_reply(result)
            except ValueError:
                interrupted(args.record, launch_execution.Correlation(
                    result["attempt_id"], result["resolution_id"], result["parent_id"],
                ), result["operation_id"])
                raise
    except (*_ERRORS, KeyboardInterrupt):
        print("Native backend handoff is unconfirmed; do not automatically retry.", file=sys.stderr)
        return 3
    if args.output_format == "nul":
        sys.stdout.buffer.write(encoded)
        sys.stdout.buffer.flush()
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
