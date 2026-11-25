"""Same-interpreter module entry; no stream replacement or child process."""
from __future__ import annotations

import atexit
import contextvars
import json
import os
import runpy
import sys
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from importlib.machinery import SourceFileLoader
from importlib.util import find_spec
from pathlib import Path
from typing import Any, NoReturn, cast

from .buffer import BufferedSink

_REPORTING: contextvars.ContextVar[int | None] = contextvars.ContextVar("command_report_pid", default=None)
_REPORT_OWNER: _ReportOwner | None = None
_FORK_STATE: _ForkState | None = None
_REPORT_LOSS_PID: int | None = None
_REPORT_WARNED_PID: int | None = None
_LOGGING_PRELOADED = "logging" in sys.modules


def _warn_incomplete_reports() -> None:
    global _REPORT_WARNED_PID
    pid = os.getpid()
    if _REPORT_WARNED_PID == pid:
        return
    _REPORT_WARNED_PID = pid
    try:
        sys.stderr.write(
            "Automatic command evidence incomplete; accepted reports are not "
            "proof of persistence; inspect command-error discovery\n",
        )
    except Exception:
        pass


def _warn_report_losses() -> None:
    if _REPORT_LOSS_PID == os.getpid():
        _warn_incomplete_reports()


def _close_reports() -> None:
    state = _FORK_STATE
    if state is not None and state.pid == os.getpid():
        with state.lock:
            state.shutdown = True
            if _REPORT_OWNER is not None:
                _REPORT_OWNER.close()
    elif _REPORT_OWNER is not None:
        _REPORT_OWNER.close()
    _warn_report_losses()


# The SDK imports logging, whose shutdown hook must run before our final drain.
# Registration is inert until main explicitly starts an invocation owner.
atexit.register(_close_reports)
from .command_errors import CommandErrorRecorder, current_recorder, recent_errors  # noqa: E402


class _ForkState:
    def __init__(self) -> None:
        self.pid = os.getpid()
        self.lock = threading.Lock()
        self.active = 0
        self.shutdown = False


class _ReportOwner:
    def __init__(self, recorder: CommandErrorRecorder) -> None:
        self.recorder = recorder
        self.pid = os.getpid()
        self.writer = BufferedSink(self._write).start()

    def _write(self, event: dict[str, Any]) -> None:
        token = _REPORTING.set(os.getpid())
        try:
            recorder = CommandErrorRecorder(
                event["command"], event["source"], directory=Path(event["directory"]),
                correlation_id=event["correlation_id"],
            )
            recorder.parent_id = event["parent_id"]
            if not recorder.record(
                event["code"], event["message"], phase=event["phase"],
                completeness="metadata-only", observed_at=event["observed_at"],
            ):
                raise OSError("Automatic command report was not persisted")
        finally:
            _REPORTING.reset(token)

    def close(self) -> None:
        if self.pid != os.getpid() or self.writer.closed:
            return
        drained = self.writer.drain(.25)
        self.writer.close()
        joined = self.writer.join(.25)
        counts = self.writer.stats()
        if (
            not drained or not joined or counts["accepted"] != counts["delivered"]
            or any(counts[key] for key in ("invalid", "full", "contended", "inactive"))
        ):
            _warn_incomplete_reports()
        _warn_report_losses()


def _fork_reports() -> None:
    global _REPORT_OWNER, _FORK_STATE
    _FORK_STATE = _ForkState()
    owner = _REPORT_OWNER
    if owner is not None:
        # Never touch the parent's queue or locks after its writer disappears.
        _REPORT_OWNER = None
        try:
            _REPORT_OWNER = _ReportOwner(owner.recorder)
        except Exception:
            _warn_report_owner()


def _warn_report_owner() -> None:
    try:
        sys.stderr.write("Automatic command evidence unavailable; original command continues\n")
    except Exception:
        pass


def _resume_reports(state: _ForkState) -> None:
    """Resume under this process's state lock, after its last native fork."""
    global _REPORT_OWNER
    owner = _REPORT_OWNER
    if (
        not state.shutdown and owner is not None
        and owner.writer.closed and owner.writer.join(0)
        and not owner.writer.stats()["sink_failed"]
    ):
        try:
            _REPORT_OWNER = _ReportOwner(owner.recorder)
        except Exception:
            _warn_report_owner()


def _start_reports(recorder: CommandErrorRecorder) -> None:
    global _REPORT_OWNER, _FORK_STATE
    if _REPORT_OWNER is None:
        _FORK_STATE = _ForkState()
        _REPORT_OWNER = _ReportOwner(recorder)
        if hasattr(os, "register_at_fork"):
            os.register_at_fork(after_in_child=_fork_reports)


def _report(code: str, message: str, *, phase: str) -> None:
    global _REPORT_LOSS_PID
    if _REPORTING.get() == os.getpid():
        return
    token = _REPORTING.set(os.getpid())
    try:
        recorder = current_recorder()
        owner = _REPORT_OWNER
        if recorder is not None and owner is not None and owner.pid == os.getpid():
            admission = owner.writer.offer({
                "command": recorder.command, "source": recorder.source,
                "correlation_id": recorder.correlation_id, "parent_id": recorder.parent_id,
                "directory": str(recorder.directory),
                "code": code, "message": message, "phase": phase, "observed_at": time.time(),
            })
            if admission != "accepted":
                _REPORT_LOSS_PID = os.getpid()
        elif recorder is not None:
            _REPORT_LOSS_PID = os.getpid()
    except Exception:
        _REPORT_LOSS_PID = os.getpid()
    finally:
        _REPORTING.reset(token)


def _install_error_reports() -> None:
    """Observe stdlib error owners without retaining their arbitrary values."""
    if getattr(sys, "_command_error_reports_installed", False):
        return
    parser_spec = find_spec("argparse")
    if (
        parser_spec is None or parser_spec.origin is None
        or parser_spec.origin != "frozen"
        and Path(parser_spec.origin).absolute() != Path(os.__file__).with_name("argparse.py").absolute()
    ):
        raise ImportError("The stdlib argument-parser owner is overridden")
    import argparse
    import logging
    previous_error = argparse.ArgumentParser.error
    previous_handlers = logging.Logger.callHandlers
    previous_thread = threading.excepthook
    previous_unraisable = sys.unraisablehook
    previous_fork = cast(Callable[[], int] | None, getattr(os, "fork", None))

    def parser_error(parser: argparse.ArgumentParser, message: str) -> NoReturn:
        categories = (
            ("unrecognized arguments:", "parser-unrecognized-arguments", "Unrecognized command arguments"),
            ("the following arguments are required:", "parser-required-arguments", "Required command arguments are missing"),
            ("argument ", "parser-invalid-argument", "Command argument validation failed"),
        )
        code, text = "parser-error", "Command argument parsing failed"
        for prefix, category, description in categories:
            if isinstance(message, str) and message.startswith(prefix):
                code, text = category, description
                break
        _report(code, text, phase="parser")
        previous_error(parser, message)

    def call_handlers(logger: logging.Logger, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            _report("error-log-record", "Application emitted an error-level logging report", phase="application")
        previous_handlers(logger, record)

    def thread_exception(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is not SystemExit:
            _report("thread-exception", "A worker thread reported an uncaught exception", phase="application")
        previous_thread(args)

    def unraisable(args: Any) -> None:
        _report("unraisable-exception", "Python reported an unraisable exception", phase="completion")
        previous_unraisable(args)

    def fork() -> int:
        global _REPORT_LOSS_PID
        # A healthy diagnostic thread must not introduce CPython's fork warning.
        state = _FORK_STATE
        if state is not None and state.pid == os.getpid():
            with state.lock:
                state.active += 1
                owner = _REPORT_OWNER
                if owner is not None:
                    try:
                        owner.close()
                    except Exception:
                        _REPORT_LOSS_PID = os.getpid()
        try:
            assert previous_fork is not None
            return previous_fork()
        finally:
            # Never acquire a copied parent lock or hold one across user callbacks.
            if state is not None and state.pid == os.getpid():
                with state.lock:
                    state.active -= 1
                    if not state.active:
                        _resume_reports(state)
                _warn_report_losses()

    setattr(argparse.ArgumentParser, "error", parser_error)
    setattr(logging.Logger, "callHandlers", call_handlers)
    threading.excepthook = thread_exception
    sys.unraisablehook = unraisable
    if previous_fork is not None:
        setattr(os, "fork", fork)
    setattr(sys, "_command_error_reports_installed", True)


def run_module(module: str, recorder: CommandErrorRecorder | None) -> None:
    """Preserve runpy/-m results; exclude unshaped exception values and locals."""
    # CPython's -m owner retains the real __main__ through atexit and threads.
    # Public run_module/run_path restore a temporary module on return instead.
    entry = cast(Callable[[str, bool], dict[str, Any]], getattr(runpy, "_run_module_as_main"))
    _run(lambda: entry(module, True), recorder)


def run_script(script: str, recorder: CommandErrorRecorder | None) -> None:
    """Execute a script without persisting its pathname or changing cwd."""
    def execute() -> None:
        filename = str(Path(script).absolute())
        namespace = sys.modules["__main__"].__dict__
        namespace.pop("main", None)  # Only the archive's imported entry alias.
        namespace.update(
            __file__=filename, __name__="__main__", __package__=None,
            __spec__=None, __cached__=None,
            __loader__=SourceFileLoader("__main__", filename),
        )
        try:
            with open(script, "rb") as source:
                data = source.read()
        except OSError as error:
            if recorder is not None:
                recorder.record(
                    "script-open-failed", f"Script open failed (errno {error.errno})",
                    phase="entry", exit_code=2,
                )
            sys.stderr.write(
                f"{sys.executable}: can't open file {filename!r}: "
                f"[Errno {error.errno}] {error.strerror}\n",
            )
            raise SystemExit(2) from None
        code = compile(data, filename, "exec", dont_inherit=True)
        exec(code, namespace)

    _run(execute, recorder)


def _run(action: Callable[[], object], recorder: CommandErrorRecorder | None) -> None:
    with recorder.activate() if recorder is not None else nullcontext():
        try:
            action()
        except SystemExit as error:
            code = (
                int.__int__(error.code) if isinstance(error.code, int)
                else 0 if error.code is None else 1
            )
            if code and recorder is not None:
                recorder.record(
                    "nonzero-exit", "Application reported a nonzero result",
                    phase="completion", exit_code=code, completeness="metadata-only",
                )
            raise
        except KeyboardInterrupt:
            if recorder is not None:
                recorder.record(
                    "interrupted", "Invocation interrupted", phase="completion",
                    completeness="incomplete",
                )
            raise
        except BaseException as error:
            if recorder is not None:
                recorder.record(
                    "uncaught-exception", f"Uncaught {type(error).__name__}",
                    completeness="metadata-only",
                )
            raise


def main() -> None:
    """Dispatch only the packaged entry protocol; application argv stays intact."""
    if sys.argv[1:2] == ["recent"]:
        # This reader's parser never receives application arguments.
        import argparse

        parser = argparse.ArgumentParser(description="Read recent local command errors")
        parser.add_argument("--limit", type=int, default=20)
        args = parser.parse_args(sys.argv[2:])
        print(json.dumps(recent_errors(limit=args.limit), ensure_ascii=True))
        return
    if len(sys.argv) < 6 or sys.argv[1] not in {"run", "run-script"} or sys.argv[5] != "--":
        raise SystemExit("Expected: run|run-script <fixed-command> <fixed-source> <target> -- <arguments>")
    _, mode, command, source, target, _, *arguments = sys.argv
    recorder: CommandErrorRecorder | None
    try:
        recorder = CommandErrorRecorder(command, source)
    except Exception as error:
        recorder = None
        try:
            print(
                f"Command evidence initialization unavailable ({type(error).__name__}); "
                "original command continues",
                file=sys.stderr,
            )
        except Exception:
            pass
    # The process entry remains available to non-daemon threads and exit hooks;
    # nested activate() scopes still restore this containing invocation.
    setattr(sys, "_command_error_recorder", recorder)
    if recorder is not None:
        recorder.maintain()
    sys.argv = [target, *arguments]
    sys.modules["__main__"].__dict__.pop("main", None)
    # Match -m's search path, without leaving our archive ahead of app packages.
    if sys.path and sys.path[0].endswith(".pyz"):
        sys.path.pop(0)
    if not sys.flags.isolated and not getattr(sys.flags, "safe_path", False):
        sys.path.insert(0, os.getcwd() if mode == "run" else os.path.dirname(os.path.realpath(target)))
    if recorder is not None:
        try:
            _start_reports(recorder)
            _install_error_reports()
            if _LOGGING_PRELOADED:
                _report(
                    "report-shutdown-unavailable",
                    "Preloaded logging has unqualified shutdown report ordering",
                    phase="entry",
                )
        except Exception:
            if _REPORT_OWNER is not None:
                _report("report-adapter-unavailable", "Automatic stdlib error reports are unavailable", phase="entry")
            else:
                _warn_report_owner()
    if mode == "run":
        run_module(target, recorder)
    else:
        run_script(target, recorder)
