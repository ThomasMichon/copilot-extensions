"""Keep typed launch prompts staged until setup invokes its Copilot backend."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import embody_resume, installer, launch_seed_state, tracking, tracking_write

BOUNDARY_MARKER = "# agent-worktrees:launch-seed-boundary-v1"


def deferred_command(
    command: list[str], record_path: Path, *, seed_id: str | None = None,
) -> list[str]:
    prefix = [
        sys.executable, "-m", "agent_worktrees.launch_seed_exec",
        "--record", str(record_path),
    ]
    if seed_id:
        prefix += ["--seed-id", seed_id]
    return [*prefix, "--", *command]


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


def _invocation_command(record_path: Path, seed_id: str, command: list[str]) -> list[str]:
    return [
        sys.executable, "-I", "-m", "agent_worktrees.launch_seed_exec",
        "--invoke", "--record", str(record_path), "--seed-id", seed_id, "--", *command,
    ]


def _setup_command(record_path: Path, seed_id: str, command: list[str]) -> list[str] | None:
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
                inner = _setup_command(record_path, seed_id, command[delimiter + 1:])
                return [*command[:delimiter + 1], *inner] if inner is not None else None
        if script.is_file() and BOUNDARY_MARKER in script.read_text(encoding="utf-8-sig").splitlines():
            flags = (
                ["-LaunchSeedRecord", str(record_path), "-LaunchSeedId", seed_id,
                 "-LaunchSeedRuntimePython", sys.executable]
                if script.suffix.lower() == ".ps1"
                else ["--launch-seed-record", str(record_path), "--launch-seed-id", seed_id,
                      "--launch-seed-runtime-python", Path(sys.executable).as_posix()]
            )
            return [*command[:index + 1], *flags, *command[index + 1:]]
        return None
    host = Path(command[0]).name.lower() if command else ""
    if host in {"copilot", "copilot.exe", "copilot.cmd"} or (
        host in {"gh", "gh.exe"} and command[1:2] == ["copilot"]
    ):
        return _invocation_command(record_path, seed_id, command)
    return None


def launch(
    record_path: Path, command: list[str], *, invoke: bool = False, seed_id: str | None = None,
) -> int:
    try:
        record = tracking.load_record(record_path)
    except (OSError, ValueError) as exc:
        print(f"Could not read launch-prompt worktree: {exc}", file=sys.stderr)
        return 3
    current = launch_seed_state.peek(record_path)
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
    seed = None
    receipt = None
    argv = command
    if invoke and current is not None:
        receipt = launch_seed_state.take(record_path, seed_id=current.seed_id)
        if receipt["seed"] is None:
            print("Launch prompt was already handed off or superseded; refresh and retry.", file=sys.stderr)
            return 3
        seed = receipt["seed"]["text"]
        argv = embody_resume.with_seed(command, seed)
    elif current is not None:
        argv = _setup_command(record_path, current.seed_id, command)
        if argv is None:
            print(
                "Launch prompt remains staged: this launcher must adopt "
                "the launch-seed invocation boundary or use normalized setup.",
                file=sys.stderr,
            )
            return 3
    try:
        if os.name != "nt" and not invoke:
            os.execvp(argv[0], argv)
        # A real interactive child inherits this console; non-interactive
        # consumers must not allocate a new one.
        process = subprocess.Popen(
            argv, stdout=sys.stdout, stderr=sys.stderr,
            **({} if sys.stdin.isatty() else no_window_kwargs()),
        )
    except OSError as exc:
        restored = launch_seed_state.restore(record_path, receipt) if receipt else None
        print(f"Could not start Copilot command: {exc}; seed restoration: {restored}", file=sys.stderr)
        return 3
    cleanup_failed = False
    if receipt:
        try:
            launch_seed_state.finish(record_path, receipt)
        except (ValueError, OSError, TimeoutError, tracking_write.AmbiguousWriteOutcome) as exc:
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
            return result if result or not cleanup_failed else 3
        except KeyboardInterrupt:
            continue


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", type=Path, required=True)
    parser.add_argument("--invoke", action="store_true")
    parser.add_argument("--seed-id", default=None)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a launch command is required")
    try:
        return launch(args.record, command, invoke=args.invoke, seed_id=args.seed_id)
    except (ValueError, OSError, TimeoutError, tracking_write.AmbiguousWriteOutcome) as exc:
        print(f"Could not hand off staged launch prompt: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
