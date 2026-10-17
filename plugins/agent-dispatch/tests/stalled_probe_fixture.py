"""Real, custody-bound probe preparation separate from timed capture."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time

READINESS_SECONDS = 10
ACTIVE_SECONDS = 5


def operation(arguments):
    if arguments[:2] == ["repos", "account-for"]:
        return "account"
    if arguments[:2] == ["repos", "gh"]:
        return "credential"
    if arguments[:2] == ["api", "user"]:
        return "identity"
    return "operation"


def prefix(root, stage, delay=0):
    return [sys.executable, str(Path(__file__).resolve()), str(root), stage, str(delay)]


class CaptureModule:
    """Adopt only the prepared spawn; delegate every cleanup operation unchanged."""

    def __init__(self, prepared):
        self.Popen = prepared.adopt

    def __getattr__(self, name):
        return getattr(subprocess, name)


class PreparedProbe:
    def __init__(self, root, argv, *, env, cwd=None):
        from agent_dispatch import companion, procutil
        from zdd.diagnostics import terminate_pid_if_identity

        self.companion = companion
        self.procutil = procutil
        self.terminate_pid_if_identity = terminate_pid_if_identity
        self.directory = Path(root)
        self.argv = list(argv)
        self.expected = {
            "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
            "stdin": subprocess.DEVNULL, "text": True,
            "env": dict(env), "cwd": cwd, **procutil._process_tree_kwargs(),
        }
        self.process = self.job = self.child_handle = None
        self.root_token = self.child = None
        self.api = self.native = None
        self.adopted = False
        self.timed_at = None
        self.startup_stderr = ""

    def __enter__(self):
        import agent_procutil as pu
        from tests import windows_durable_launch_fixture as native

        self.native = native
        self.directory.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        try:
            self.process, self.job = pu.spawn_sync_in_kill_on_close_job(
                self.argv, require_job=os.name == "nt", allow_breakaway=False,
                **self.expected,
            )
            self.root_token = self.companion.process_start_token(self.process.pid)
            self.require_owned_root(self.root_token)
            native.wait_file(self.directory / "ready.json", seconds=READINESS_SECONDS)
            record = native.read_json(self.directory / "ready.json")
            if os.name == "nt":
                self.api = native.kernel()
            self.child_handle = self.pin_child(record)
            self.child = record
            self.prepared_at = time.monotonic()
            self.readiness_seconds = self.prepared_at - started
            return self
        except BaseException as error:
            self.close()
            if self.startup_stderr:
                raise AssertionError(
                    f"real probe preparation failed: {self.startup_stderr[-2000:]}"
                ) from error
            raise

    def require_owned_root(self, expected):
        if not expected or expected != self.root_token:
            raise ValueError("unknown prepared root ownership")
        if self.process.poll() is not None:
            raise ValueError("prepared root ownership ended before timed capture")
        if self.companion.process_start_token(self.process.pid) != expected:
            raise ValueError("prepared root ownership identity unavailable or changed")

    def pin_child(self, record):
        pid, token = record.get("pid"), record.get("start_token")
        if type(pid) is not int or pid <= 0 or not isinstance(token, str) or not token:
            raise ValueError("unknown descendant identity")
        if pid == self.process.pid:
            raise ValueError("prepared root is not its descendant")
        if self.api is not None:
            handle = self.api.OpenProcess(0x00100000 | 0x1000, False, pid)
            if not handle:
                raise ValueError("descendant identity unavailable")
            try:
                member = wintypes.BOOL()
                assert self.api.IsProcessInJob(handle, self.job.handle, ctypes.byref(member))
                if (
                    not member.value or str(self.native.birth(self.api, handle)) != token
                    or self.api.WaitForSingleObject(handle, 0) != 258
                ):
                    raise ValueError("foreign or changed descendant identity")
                return handle
            except BaseException:
                self.api.CloseHandle(handle)
                raise
        if (
            self.companion.process_start_token(pid) != token
            or os.getpgid(pid) != self.process.pid
        ):
            raise ValueError("foreign or changed descendant identity")
        return None

    def adopt(self, argv, **kwargs):
        if self.adopted or list(argv) != self.argv or kwargs != self.expected:
            raise ValueError("unexpected prepared capture request")
        self.require_owned_root(self.root_token)
        if self.api is not None:
            if self.api.WaitForSingleObject(self.child_handle, 0) != 258:
                raise ValueError("prepared descendant is no longer live")
        elif self.companion.process_start_token(self.child["pid"]) != self.child["start_token"]:
            raise ValueError("prepared descendant identity changed")
        self.adopted = True
        self.timed_at = time.monotonic()
        (self.directory / "timed").touch()
        return self.process

    def wait_pinned_child(self, milliseconds):
        ctypes.set_last_error(0)
        result = self.api.WaitForSingleObject(self.child_handle, milliseconds)
        error = ctypes.get_last_error() if result == 0xFFFFFFFF else None
        return result, error

    def child_exit_snapshot(self, phase, **details):
        self.api.GetProcessId.argtypes = [wintypes.HANDLE]
        self.api.GetProcessId.restype = wintypes.DWORD
        self.api.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
        ]
        self.api.GetExitCodeProcess.restype = wintypes.BOOL
        wait, wait_error = self.wait_pinned_child(0)
        row = {
            "phase": phase, "wall_ns": time.time_ns(), "monotonic": time.monotonic(),
            "expected_pid": self.child["pid"],
            "expected_start_token": self.child["start_token"],
            "wait_result": wait,
            "wait_error": wait_error,
            "custody_open": bool(self.job is not None and self.job.handle),
            "stop_requested": (self.directory / "stop").exists(),
            "root_poll": self.process.poll(),
        }
        row["handle_pid"] = self.api.GetProcessId(self.child_handle)
        ctypes.set_last_error(0)
        try:
            row["handle_start_token"] = str(self.native.birth(self.api, self.child_handle))
        except (AssertionError, OSError) as error:
            row["identity_error"] = {
                "type": type(error).__name__, "winerror": ctypes.get_last_error(),
            }
        row["identity_matches"] = (
            row["handle_pid"] == row["expected_pid"]
            and row.get("handle_start_token") == row["expected_start_token"]
        )
        code = wintypes.DWORD()
        ctypes.set_last_error(0)
        row["exit_query_ok"] = bool(
            self.api.GetExitCodeProcess(self.child_handle, ctypes.byref(code))
        )
        row["exit_code"] = code.value if row["exit_query_ok"] else None
        row["exit_query_error"] = None if row["exit_query_ok"] else ctypes.get_last_error()
        row["elapsed_since_arm"] = row["monotonic"] - self.timed_at
        row["seconds_before_earliest_watchdog"] = ACTIVE_SECONDS - row["elapsed_since_arm"]
        heartbeat = self.directory / "heartbeat"
        try:
            row["heartbeat_content"] = heartbeat.read_text(encoding="utf-8")
            row["heartbeat_mtime_ns"] = heartbeat.stat().st_mtime_ns
        except OSError as error:
            row["heartbeat_error"] = type(error).__name__
        row.update(details)
        destination = os.environ.get("STALLED_PROBE_DIAGNOSTICS")
        if destination:
            target = Path(destination)
            target.mkdir(parents=True, exist_ok=True)
            self.companion.write_json_object_atomic(
                target / f"{self.argv[3]}-{self.argv[4]}-{phase}.json", row,
            )
        return row

    @staticmethod
    def require_known_exit_state(state):
        assert state.get("identity_matches") is True, f"pinned identity unknown: {state}"
        assert state.get("exit_query_ok") is True, f"pinned exit query unknown: {state}"
        assert type(state.get("exit_code")) is int, f"pinned exit code unknown: {state}"
        assert state.get("custody_open") is True, f"fixture custody closed: {state}"
        assert state.get("stop_requested") is False, f"fixture cleanup already started: {state}"
        assert type(state.get("wait_result")) is int and state["wait_result"] in (0, 258), (
            f"native exit observation failed or unknown: {state}"
        )

    def confirm_child_exit(self):
        state = self.child_exit_snapshot("assert-entry")
        self.require_known_exit_state(state)
        if state["wait_result"] == 258:
            # External termination is asynchronous; leave heartbeat/diagnostic margin.
            remaining = ACTIVE_SECONDS - (time.monotonic() - self.timed_at)
            milliseconds = min(1000, int((remaining - 1.0) * 1000))
            assert milliseconds > 0, f"no safe pre-watchdog confirmation window: {state}"
            result, error = self.wait_pinned_child(milliseconds)
            state = self.child_exit_snapshot(
                "after-confirmation", confirmation_milliseconds=milliseconds,
                confirmation_result=result, confirmation_error=error,
            )
            self.require_known_exit_state(state)
            assert result == 0, f"bounded pinned exit confirmation failed: {state}"
        assert state["wait_result"] == 0 and state["exit_code"] != 259, (
            f"pinned descendant exit unconfirmed: {state}"
        )
        assert time.monotonic() - self.timed_at < ACTIVE_SECONDS, (
            f"natural watchdog cannot establish cleanup: {state}"
        )
        return state

    def assert_reaped(self):
        assert self.adopted and self.timed_at is not None
        assert self.process.poll() is not None, "capture left its real root alive"
        assert self.job is None or self.job.handle, "fixture custody must remain open"
        if self.api is not None:
            self.confirm_child_exit()
        else:
            assert not self.procutil._posix_process_group_alive(self.process.pid)
        heartbeat = self.directory / "heartbeat"
        before = heartbeat.read_text(encoding="utf-8")
        time.sleep(.15)
        assert heartbeat.read_text(encoding="utf-8") == before
        if self.api is not None:
            state = self.child_exit_snapshot("after-heartbeat")
            self.require_known_exit_state(state)
            assert state["wait_result"] == 0 and state["exit_code"] != 259, state
            assert state.get("heartbeat_content") == before, state
        assert time.monotonic() - self.timed_at < ACTIVE_SECONDS, (
            "fixture watchdog cannot prove cleanup"
        )

    def close(self):
        (self.directory / "stop").touch()
        try:
            if self.job is not None:
                self.job.close()
                self.job = None
            else:
                for record in (
                    self.child,
                    {"pid": self.process.pid, "start_token": self.root_token}
                    if self.process is not None else None,
                ):
                    if record and record.get("start_token"):
                        self.terminate_pid_if_identity(record["pid"], record["start_token"])
            if self.process is not None:
                self.process.wait(timeout=5)
                _stdout, self.startup_stderr = self.process.communicate(timeout=5)
        finally:
            if self.child_handle is not None:
                self.api.CloseHandle(self.child_handle)
                self.child_handle = None

    def __exit__(self, *_exception):
        self.close()


def _actor():
    root, stage, delay = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
    arguments = sys.argv[4:]
    child = arguments == ["child"]
    if not child and operation(arguments) != stage:
        print("synthetic-credential" if operation(arguments) == "credential" else "issue-bot")
        return
    if not child:
        subprocess.Popen(
            [*prefix(root, stage, delay), "child"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            # Keep this finite actor's bootstrap independent of runtime imports.
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
    else:
        bootstrap_started = time.monotonic()
        time.sleep(delay)
        bootstrap_elapsed = time.monotonic() - bootstrap_started
        import windows_durable_launch_fixture as native

        if os.name == "nt":
            token = str(native.identity(os.getpid())["birth"])
        else:
            from agent_dispatch.companion import process_start_token

            token = process_start_token(os.getpid())
        assert token, "descendant could not establish its real creation identity"
        (root / "heartbeat").write_text(str(time.monotonic()), encoding="utf-8")
        native.publish(root / "ready.json", {
            "pid": os.getpid(), "start_token": token,
            "bootstrap_elapsed": bootstrap_elapsed, "ready_at": time.monotonic(),
        })
    deadline = time.monotonic() + READINESS_SECONDS
    while not (root / "timed").exists() and not (root / "stop").exists():
        if time.monotonic() >= deadline:
            return
        time.sleep(.01)
    deadline = time.monotonic() + ACTIVE_SECONDS
    while not (root / "stop").exists() and time.monotonic() < deadline:
        if child:
            (root / "heartbeat").write_text(str(time.monotonic()), encoding="utf-8")
        time.sleep(.02)


if __name__ == "__main__":
    _actor()
