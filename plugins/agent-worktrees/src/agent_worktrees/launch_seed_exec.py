"""Keep typed launch prompts staged until setup invokes its Copilot backend."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

from agent_procutil import no_window_kwargs

from . import embody_resume, installer, launch_admission, launch_seed_state, sessions
from . import tracking, tracking_write
from . import launch_execution
from .native_arguments import SETUP_VALUE_OPTIONS, VALUE_OPTIONS

BOUNDARY_MARKER = "# agent-worktrees:launch-seed-boundary-v1"
ATTEMPT_BOUNDARY_MARKER = "# agent-worktrees:launch-attempt-boundary-v1"
NATIVE_SETUP_MARKER = "# agent-worktrees:native-setup-owner-v1"


def _interactive_console() -> bool:
    if sys.stdin is None or not sys.stdin.isatty():
        return False
    if os.name == "nt":
        import ctypes

        # Windows NUL can report isatty() without an attached console.
        return bool(ctypes.windll.kernel32.GetConsoleCP())
    return True


def _final_cold_error(record) -> str | None:
    verdict = sessions.verify_worktree_active(record)
    if verdict is None or not verdict.probes_ok or not verdict.mux_probe_ok:
        return "Could not verify cold Copilot handoff; the seed remains staged."
    if verdict.live_session_ids or verdict.bare:
        return "A Copilot became live before handoff; the seed remains staged."
    if verdict.mux_live:
        own_pane = os.environ.get("TMUX_PANE") or os.environ.get("PSMUX_PANE")
        if not own_pane or sessions.mux_active_pane(record.worktree_id) != own_pane:
            return "Another mux became live before handoff; the seed remains staged."
        # The current setup may own a newly-created mux shell, but no bound
        # Copilot may already occupy this worktree.
        return None
    if verdict.active:
        return "The worktree became live before handoff; the seed remains staged."
    return None


def deferred_command(
    command: list[str], record_path: Path, *, seed_id: str | None = None,
    admission_only: bool = False, preserve_seed_id: str | None = None,
    native_resume_session: str | None = None,
    correlation: launch_execution.Correlation | None = None,
    prepare_native: bool = True,
) -> list[str]:
    native = _native_setup_command(
        command, record_path, seed_id=seed_id, admission_only=admission_only,
        preserve_seed_id=preserve_seed_id, native_resume_session=native_resume_session,
        correlation=correlation,
    ) if prepare_native else None
    if native is not None:
        return native
    prefix = [
        sys.executable, "-m", "agent_worktrees.launch_seed_exec",
        "--record", str(record_path),
    ]
    if seed_id:
        prefix += ["--seed-id", seed_id]
    if admission_only:
        prefix += ["--admission-only"]
    if preserve_seed_id:
        prefix += ["--preserve-seed-id", preserve_seed_id]
    if native_resume_session:
        prefix += ["--native-resume-session", native_resume_session]
    if correlation:
        prefix += correlation.options()
    return [*prefix, "--", *command]


def native_setup_index(command: list[str]) -> int | None:
    """Recognize the typed native setup envelope, not arbitrary Copilot argv."""
    index = _script_index(command)
    if index is not None and Path(command[index]).name == "launch-command.ps1":
        delimiter = _wrapper_delimiter(command, index)
        if delimiter is not None:
            inner = native_setup_index(command[delimiter + 1:])
            return delimiter + 1 + inner if inner is not None else None
    if (
        index is not None and Path(command[index]).name == "default-setup.ps1"
        and "-LaunchSeedPreparation" in command[index + 1:]
        and "-LaunchSeedNativeRoot" in command[index + 1:]
    ):
        return index
    return None


def _wrapper_delimiter(command: list[str], index: int) -> int | None:
    delimiter = index + 1
    if command[delimiter:delimiter + 1] == ["--recovery"]:
        delimiter += 1
    return delimiter if command[delimiter:delimiter + 1] == ["--"] else None


def copied_setup_command(command: list[str], record_path: Path) -> list[str]:
    """Relocate canonical setup assets without changing the caller's admission or seed owner."""
    return _native_setup_command(
        command, record_path, seed_id=None, admission_only=False, preserve_seed_id=None,
        native_resume_session=None, correlation=None, copy_only=True,
    ) or command


def _native_setup_command(
    command: list[str], record_path: Path, *, seed_id: str | None,
    admission_only: bool, preserve_seed_id: str | None, native_resume_session: str | None,
    correlation: launch_execution.Correlation | None,
    copy_only: bool = False,
) -> list[str] | None:
    if os.name != "nt" or not command or PureWindowsPath(command[0]).stem.casefold() != "pwsh":
        return None
    from .process_launcher import _normalized_setup_index

    outer = _script_index(command)
    wrapper = installer.install_dir() / "scripts" / "launch-command.ps1"
    if outer is not None and Path(command[outer]).resolve() == wrapper.resolve():
        delimiter = _wrapper_delimiter(command, outer)
        if delimiter is None:
            return None
        inner = _native_setup_command(
            command[delimiter + 1:], record_path, seed_id=seed_id, admission_only=admission_only,
            preserve_seed_id=preserve_seed_id, native_resume_session=native_resume_session,
            correlation=correlation, copy_only=copy_only,
        )
        if inner is None:
            return None
        copied_setup = Path(inner[_script_index(inner)])
        return [
            *command[:outer], str(copied_setup.with_name("launch-command.ps1")),
            *command[outer + 1:delimiter + 1], *inner,
        ]
    index = _normalized_setup_index(command)
    if index is None or Path(command[index]).name != "default-setup.ps1":
        return None
    script = Path(command[index])
    if not script.is_file() or NATIVE_SETUP_MARKER not in script.read_text(
        encoding="utf-8-sig",
    ).splitlines():
        return None
    from . import session_shell
    from .manager_launch_cli import _worktree_manager_root

    if not copy_only:
        from .manager_launch_cli import _usable_worktree_manager_launcher_dir

        launch_dir = _usable_worktree_manager_launcher_dir()
        peer = launch_dir / "pane-wrapper.ps1" if launch_dir else None
        if peer is None or not peer.is_file() or session_shell.NATIVE_CONSOLE_MARKER not in peer.read_text(
            encoding="utf-8-sig",
        ).splitlines():
            return None

    prepared = session_shell.prepare(
        record_path.absolute(), script.absolute(),
        installation_roots=(installer.install_dir().absolute(), _worktree_manager_root().absolute()),
        attempt_id=correlation.attempt_id if correlation else None,
    )
    if copy_only:
        return [*command[:index], str(prepared.wrapper), *command[index + 1:]]
    flags = [
        "-LaunchSeedPreparation", "admission" if admission_only else "seed",
        "-LaunchSeedNativeRoot", str(installer.install_dir()),
        "-LaunchSeedRecord", str(record_path),
    ]
    for name, value in (
        ("LaunchSeedId", seed_id), ("LaunchSeedPreserveId", preserve_seed_id),
        ("LaunchNativeResumeSession", native_resume_session),
    ):
        if value is not None:
            flags += ["-" + name, value]
    if correlation:
        for name, value in (
            ("LaunchAttemptId", correlation.attempt_id),
            ("LaunchResolutionId", correlation.resolution_id),
            ("LaunchParentId", correlation.parent_id),
        ):
            if value is not None:
                flags += ["-" + name, value]
    return [*command[:index], str(prepared.wrapper), *flags, *command[index + 1:]]


def _script_index(command: list[str]) -> int | None:
    if not command:
        return None
    host = Path(command[0]).name.lower()
    if host in {"bash", "bash.exe"}:
        return 1 if len(command) > 1 and not command[1].startswith("-") else None
    if host in {"pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
        return next(
            (i + 1 for i, arg in enumerate(command[:-1]) if arg.lower() == "-file"),
            None,
        )
    return None


def _invocation_command(
    record_path: Path, seed_id: str, command: list[str],
    correlation: launch_execution.Correlation | None = None,
) -> list[str]:
    return [
        sys.executable, "-I", "-m", "agent_worktrees.launch_seed_exec",
        "--invoke", "--record", str(record_path), "--seed-id", seed_id,
        *(correlation.options() if correlation else []), "--", *command,
    ]


def _setup_command(
    record_path: Path, seed_id: str, command: list[str],
    correlation: launch_execution.Correlation | None = None,
    *, native_only: bool = False, environment: dict[str, str] | None = None,
) -> list[str] | None:
    index = _script_index(command)
    if index is not None:
        script = Path(command[index])
        own_scripts = {
            installer.install_dir() / "scripts",
            Path(__file__).resolve().parents[2] / "scripts",
        }
        if any(script.resolve() == (root / script.name).resolve() for root in own_scripts):
            if script.name in {"launch-command.ps1", "launch-command.sh"}:
                delimiter = next(
                    (i for i in range(index + 1, len(command)) if command[i] == "--"),
                    None,
                )
                if delimiter is None:
                    return None
                inner = _setup_command(
                    record_path, seed_id, command[delimiter + 1:], correlation,
                    native_only=native_only, environment=environment,
                )
                return [*command[:delimiter + 1], *inner] if inner is not None else None
        markers = script.read_text(encoding="utf-8-sig").splitlines() if script.is_file() else []
        if BOUNDARY_MARKER in markers:
            if native_only and script.name in {"default-setup.ps1", "default-setup.sh"}:
                options = {}
                position = index + 1
                setup_values = SETUP_VALUE_OPTIONS | {
                    "-nativeargumentsjson", "-executiondirectory", "--execution-directory",
                }
                while position < len(command):
                    argument = command[position]
                    flag = argument.casefold()
                    if argument == "--":
                        break
                    if flag in setup_values:
                        if position + 1 >= len(command):
                            return None
                        options[flag] = command[position + 1]
                        position += 2
                    elif argument in VALUE_OPTIONS or argument == "-C":
                        position += 2
                    else:
                        position += 1
                path = {**os.environ, **(environment or {})}.get("PATH", "")
                for flag in ("-sessionpath", "--session-path"):
                    if flag in options:
                        path = options[flag] + os.pathsep + path
                override = options.get("-copilotpath") or options.get("--copilot-path")
                if options.get("-envscript") or options.get("--env-script"):
                    return None
                backend = override or "copilot"
                if (
                    PureWindowsPath(backend).stem.casefold() == "gh"
                    or not ((override and Path(backend).is_file()) or shutil.which(backend, path=path))
                ):
                    return None
            if correlation and ATTEMPT_BOUNDARY_MARKER not in markers:
                return None
            flags = (
                ["-LaunchSeedRecord", str(record_path), "-LaunchSeedId", seed_id,
                 "-LaunchSeedRuntimePython", sys.executable]
                if script.suffix.lower() == ".ps1"
                else ["--launch-seed-record", str(record_path), "--launch-seed-id", seed_id,
                      "--launch-seed-runtime-python", Path(sys.executable).as_posix()]
            )
            if correlation:
                for name, value in (
                    ("LaunchAttemptId", correlation.attempt_id),
                    ("LaunchResolutionId", correlation.resolution_id),
                    ("LaunchParentId", correlation.parent_id),
                ):
                    if value is not None:
                        flags += [("-" + name) if script.suffix.lower() == ".ps1" else {
                            "LaunchAttemptId": "--launch-attempt-id",
                            "LaunchResolutionId": "--launch-resolution-id",
                            "LaunchParentId": "--launch-parent-id",
                        }[name], value]
            return [*command[:index + 1], *flags, *command[index + 1:]]
        return None
    host = Path(command[0]).name.lower() if command else ""
    if native_only and host in {"gh", "gh.exe"}:
        return None
    if host in {"copilot", "copilot.exe", "copilot.cmd"} or (
        host in {"gh", "gh.exe"} and command[1:2] == ["copilot"]
    ):
        return _invocation_command(record_path, seed_id, command, correlation)
    return None


def supports_seed_command(
    command: list[str], record_path: Path, *, environment: dict[str, str] | None = None,
) -> bool:
    """Recognize the existing finite setup/direct-backend contract without effects."""
    try:
        return _setup_command(
            record_path, "preview", command, native_only=True, environment=environment,
        ) is not None
    except UnicodeDecodeError:
        logging.getLogger(__name__).info(
            "Native startup does not support this setup encoding; retaining terminal fallback",
        )
        return False


def launch(
    record_path: Path, command: list[str], *, invoke: bool = False, seed_id: str | None = None,
    admission_only: bool = False, preserve_seed_id: str | None = None,
    native_resume_session: str | None = None,
    correlation: launch_execution.Correlation | None = None,
) -> int:
    def work(execution=None):
        return _admitted_launch(
            record_path, command, invoke=invoke, seed_id=seed_id, admission_only=admission_only,
            preserve_seed_id=preserve_seed_id, native_resume_session=native_resume_session,
            execution=execution,
        )
    return launch_execution.run(record_path, correlation, work) if correlation else work()


def _admitted_launch(
    record_path: Path, command: list[str], *, invoke: bool, seed_id: str | None,
    admission_only: bool, preserve_seed_id: str | None, native_resume_session: str | None,
    execution: launch_execution.Execution | None,
) -> int:
    if not _valid_request(
        record_path, invoke=invoke, seed_id=seed_id, admission_only=admission_only,
        preserve_seed_id=preserve_seed_id, native_resume_session=native_resume_session,
    ):
        return 3
    if admission_only:
        with launch_admission.guard(record_path):
            return _launch(
                record_path, command, invoke=invoke, seed_id=seed_id, admission_only=True,
                preserve_seed_id=preserve_seed_id,
                native_resume_session=native_resume_session,
                execution=execution,
            )
    return _launch(record_path, command, invoke=invoke, seed_id=seed_id, execution=execution)


def _valid_request(
    record_path: Path, *, invoke: bool, seed_id: str | None,
    admission_only: bool = False, preserve_seed_id: str | None = None,
    native_resume_session: str | None = None,
) -> bool:
    from . import agent_policy

    try:
        agent_policy.require_record(record_path)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return False
    if (preserve_seed_id and not admission_only) or (admission_only and (seed_id or invoke)):
        raise ValueError("Admission-only launch cannot claim a prompt")
    if native_resume_session and not admission_only:
        raise ValueError("Native resume requires owner-held admission-only execution")
    return True


@dataclass(frozen=True)
class PreparedLaunch:
    argv: list[str]
    receipt: dict | None
    environment: dict[str, str] | None
    setup_seed_id: str | None = None


def _prepare(
    record_path: Path, command: list[str], *, invoke: bool, seed_id: str | None,
    admission_only: bool = False, preserve_seed_id: str | None = None,
    native_resume_session: str | None = None,
    execution: launch_execution.Execution | None = None,
) -> PreparedLaunch | int:
    try:
        record = tracking.load_record(record_path)
    except (OSError, ValueError) as exc:
        print(f"Could not read launch-prompt worktree: {exc}", file=sys.stderr)
        return 3
    from . import agent_policy
    from repository_policy import AgentHostingError

    try:
        agent_policy.require(record.worktree_path)
    except AgentHostingError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    current = launch_seed_state.peek(record_path)
    if native_resume_session:
        from .session_liveness import session_liveness

        if (
            record.resolved_head_session != native_resume_session
            or record.pending_handoffs
            or getattr(record, "execution_leg_opaque", False)
            or getattr(record, "session_backend_opaque", False)
            or record.status in {"finalizing", "complete", "completed", "finalized", "archived"}
            or sessions.has_mux_session(record.worktree_id)
            or session_liveness(native_resume_session) != "dead"
        ):
            print("Native head changed, is live, or has unconfirmed liveness.", file=sys.stderr)
            return 3
        cold_error = _final_cold_error(record)
        if cold_error:
            print(cold_error, file=sys.stderr)
            return 3
    if admission_only:
        if (
            record.pending_seed
            or (preserve_seed_id and (current is None or current.seed_id != preserve_seed_id))
            or (not preserve_seed_id and current is not None)
        ):
            print("Launch intent changed after planning; refresh and retry.", file=sys.stderr)
            return 3
        current = None
    if record.pending_seed:
        current = launch_seed_state.stage(record_path)
    if seed_id and (current is None or current.seed_id != seed_id):
        print("Staged launch prompt is missing or superseded; refresh and retry.", file=sys.stderr)
        return 3
    if current is not None and current.handoff_id:
        print(
            "A previous launch-prompt handoff is in progress or unconfirmed; "
            "the seed is retained, but will not be submitted twice.",
            file=sys.stderr,
        )
        return 3
    receipt = None
    argv = command
    if invoke and current is not None:
        if Path(command[0]).name.lower() in {"gh", "gh.exe"} and command[1:2] == ["copilot"]:
            print(
                "Launch prompt remains staged: gh copilot is an intermediary, "
                "not a confirmed Copilot startup boundary. Configure a direct "
                "Copilot executable before retrying.",
                file=sys.stderr,
            )
            return 3
        cold_error = _final_cold_error(record)
        if cold_error:
            print(cold_error, file=sys.stderr)
            return 3
        receipt = launch_seed_state.take(record_path, seed_id=current.seed_id)
        if receipt["seed"] is None:
            print("Launch prompt was already handed off or superseded; refresh and retry.", file=sys.stderr)
            return 3
        seed = receipt["seed"]["text"]
        argv = embody_resume.with_seed(command, seed)
        if execution:
            execution.observed(
                "seed-claimed", seed_id=current.seed_id, handoff_id=receipt["seed"]["handoff_id"],
            )
    elif current is not None:
        argv = _setup_command(
            record_path, current.seed_id, command, execution.child() if execution else None,
        )
        if argv is None:
            print(
                "Launch prompt remains staged: this launcher must adopt "
                "the required launch-seed/attempt invocation boundary or use normalized setup.",
                file=sys.stderr,
            )
            return 3
    child_environment = execution.environment(invoke=invoke) if execution else None
    if child_environment is None and launch_execution.NATIVE_HINT_ENV in os.environ:
        child_environment = os.environ.copy()
        child_environment.pop(launch_execution.NATIVE_HINT_ENV)
    return PreparedLaunch(
        argv, receipt, child_environment, current.seed_id if current is not None and not invoke else None,
    )


def _launch(
    record_path: Path, command: list[str], *, invoke: bool, seed_id: str | None,
    admission_only: bool = False, preserve_seed_id: str | None = None,
    native_resume_session: str | None = None,
    execution: launch_execution.Execution | None = None,
) -> int:
    prepared = _prepare(
        record_path, command, invoke=invoke, seed_id=seed_id, admission_only=admission_only,
        preserve_seed_id=preserve_seed_id, native_resume_session=native_resume_session,
        execution=execution,
    )
    if isinstance(prepared, int):
        return prepared
    argv, receipt, child_environment = prepared.argv, prepared.receipt, prepared.environment
    try:
        if os.name != "nt" and not invoke and not admission_only and execution is None:
            if child_environment is not None:
                os.execvpe(argv[0], argv, child_environment)
            os.execvp(argv[0], argv)
        # A real interactive child inherits this console; non-interactive
        # consumers must not allocate a new one.
        process = subprocess.Popen(
            argv, stdout=sys.stdout, stderr=sys.stderr,
            **({"env": child_environment} if child_environment is not None else {}),
            **({} if _interactive_console() else no_window_kwargs()),
        )
    except OSError as exc:
        restored = launch_seed_state.restore(record_path, receipt) if receipt else None
        print(f"Could not start Copilot command: {exc}; seed restoration: {restored}", file=sys.stderr)
        return 3
    cleanup_failed = False
    if execution:
        execution.spawned(process.pid, invoke=invoke)
    if receipt:
        try:
            launch_seed_state.finish(record_path, receipt)
        except (ValueError, OSError, TimeoutError, tracking_write.AmbiguousWriteOutcome, KeyboardInterrupt) as exc:
            cleanup_failed = True
            print(
                f"Copilot started, but staged-seed cleanup is unconfirmed: {exc}. "
                "The retained handoff will not be automatically resubmitted.",
                file=sys.stderr,
            )
    # The terminal delivers control signals to the backend too. Let it decide
    # whether Ctrl+C cancels a turn or ends the session; keep owning its wait.
    while True:
        try:
            result = process.wait()
            if execution:
                execution.process_exit_code = result
            return result if result or not cleanup_failed else 3
        except KeyboardInterrupt:
            continue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--invoke", action="store_true")
    parser.add_argument("--seed-id", default=None)
    parser.add_argument("--admission-only", action="store_true")
    parser.add_argument("--preserve-seed-id", default=None)
    parser.add_argument("--native-resume-session", default=None)
    parser.add_argument("--launch-attempt-id", default=None)
    parser.add_argument("--launch-resolution-id", default=None)
    parser.add_argument("--launch-parent-id", default=None)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a launch command is required")
    try:
        correlation = (
            launch_execution.Correlation(
                args.launch_attempt_id, args.launch_resolution_id, args.launch_parent_id,
            )
            if any((args.launch_attempt_id, args.launch_resolution_id, args.launch_parent_id))
            else None
        )
        return launch(
            args.record, command, invoke=args.invoke, seed_id=args.seed_id,
            admission_only=args.admission_only,
            preserve_seed_id=args.preserve_seed_id,
            native_resume_session=args.native_resume_session,
            correlation=correlation,
        )
    except (ValueError, OSError, TimeoutError, tracking_write.AmbiguousWriteOutcome) as exc:
        print(f"Could not hand off staged launch prompt: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
