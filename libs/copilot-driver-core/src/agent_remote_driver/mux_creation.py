"""Canonical mux creation without lending a Windows worktree to idle standbys."""

from contextlib import contextmanager
import hashlib
import argparse
import json
import locale
import os
from pathlib import Path, PureWindowsPath
import platform
import subprocess
import sys
import tempfile
import time

from agent_procutil import no_window_kwargs
from single_instance_lease import AlreadyRunningError, SingleInstance

from agent_remote_driver.mux_observation_types import ObservationUnavailable


class CreationUncertain(RuntimeError):
    """A dispatched mux client failed without disproving pane creation."""


def is_psmux(binary: str) -> bool:
    return PureWindowsPath(binary).stem.casefold() == "psmux"


def has_session(binary: str, session: str, *, timeout: float = 2) -> bool:
    """Distinguish an absent mux session from an unavailable query."""
    if is_psmux(binary):
        from agent_remote_driver.mux_observation import observe_psmux

        return observe_psmux(session, timeout=timeout).presence()
    result = subprocess.run(
        [binary, "has-session", "-t", session], capture_output=True, timeout=timeout,
        cwd=str(Path.home()), **no_window_kwargs(),
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("Mux session discovery failed; refusing creation")
    return result.returncode == 0


@contextmanager
def session_gate(session: str, *, exists, enabled: bool):
    """Windows Picker and detached creators share one short kernel lease."""
    if not enabled:
        yield lambda: None
        return
    key = hashlib.sha256(session.encode("utf-8")).hexdigest()
    lease = SingleInstance(
        Path.home() / ".psmux" / "launch-locks",
        service="PSMux session creation", lock_name=key + ".lock",
    )
    deadline = time.monotonic() + 15
    try:
        while True:
            try:
                if exists():
                    yield False
                    return
            except ObservationUnavailable:
                pass
            try:
                lease.acquire()
                break
            except AlreadyRunningError as exc:
                if time.monotonic() >= deadline:
                    raise TimeoutError("PSMux session creation is busy; retry this worktree") from exc
                time.sleep(0.05)
        if exists():
            lease.release()
            yield False
        else:
            yield lease.release
    finally:
        lease.release()


def pane_work_dir(work_dir: str) -> str:
    """Preserve caller-relative pane locations before moving the mux client."""
    if platform.system() == "Windows":
        if not work_dir:
            return str(Path.cwd())
        if not Path(work_dir).is_absolute():
            return str(Path(work_dir).absolute())
    return work_dir


def _creation_env(argv: list[str]) -> dict[str, str] | None:
    if (
        platform.system() != "Windows" or len(argv) < 2
        or not is_psmux(argv[0]) or argv[1] != "new-session"
    ):
        return None
    # PSMux can discard a detached child that inherits another pane's nesting.
    nesting = {
        "TMUX", "TMUX_PANE", "PSMUX_SESSION", "PSMUX_TARGET_SESSION",
        "PSMUX_CLAUDE_TEAMMATE_MODE",
    }
    return {key: value for key, value in os.environ.items() if key.upper() not in nesting}


def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Keep the client launch directory separate from the pane's explicit CWD."""
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=15,
        cwd=str(Path.home()) if platform.system() == "Windows" else None,
        env=_creation_env(argv),
        **no_window_kwargs(),
    )


def run_or_join(argv: list[str], *, binary: str, session: str, prepare):
    """Prepare a new pane only after the native creation gate admits it."""
    gated = platform.system() == "Windows" and is_psmux(binary)
    with session_gate(
        session, exists=lambda: has_session(binary, session), enabled=gated,
    ) as release:
        if not release:
            return None
        if platform.system() != "Windows" and Path(binary).name == "tmux" and has_session(binary, "=" + session):
            return None
        argv.extend(prepare())
        if gated:
            return run_session(argv, session, release)
        result = run(argv)
        if result.returncode != 0:
            raise CreationUncertain("Mux creation client failed; pane state is uncertain")
        return result


def run_session(argv: list[str], session: str, release) -> subprocess.CompletedProcess[str]:
    """Release check/create custody at discovery, not at client completion."""
    deadline = time.monotonic() + 15
    # A mux server can outlive the client and inherit its output handles.
    # Regular files cannot keep a pipe reader or its interpreter waiting.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr, subprocess.Popen(
        argv, stdout=stdout, stderr=stderr, cwd=str(Path.home()),
        env=_creation_env(argv), **no_window_kwargs(),
    ) as child:
        pending = True
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, 15)
                try:
                    child.wait(timeout=min(0.05, remaining))
                    if child.returncode != 0:
                        raise CreationUncertain(
                            f"PSMux creation client exited {child.returncode}; pane state is uncertain",
                        )
                    values = []
                    for stream in (stdout, stderr):
                        stream.seek(0)
                        text = stream.read(os.fstat(stream.fileno()).st_size).decode(
                            locale.getpreferredencoding(False),
                        )
                        values.append(text.replace("\r\n", "\n").replace("\r", "\n"))
                    return subprocess.CompletedProcess(argv, child.returncode, *values)
                except subprocess.TimeoutExpired:
                    remaining = deadline - time.monotonic()
                    if pending and remaining > 0:
                        try:
                            present = has_session(argv[0], session, timeout=min(2, remaining))
                        except ObservationUnavailable:
                            # A dispatched server may still be publishing its rendezvous.
                            continue
                        if present:
                            release()
                            pending = False
        except BaseException as error:
            if child.poll() is None:
                child.kill()
            child.wait()
            if isinstance(error, Exception) and not isinstance(
                error, (subprocess.TimeoutExpired, CreationUncertain),
            ):
                raise CreationUncertain(f"PSMux creation outcome is uncertain: {error}") from error
            raise


CONTRACT = "copilot-driver.mux-creation"


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate mux request property")
        value[key] = item
    return value


def _request(stream):
    raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError("Mux request exceeds the input limit")
    value = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != {"version", "binary", "session", "argv"}:
        raise ValueError("Invalid mux request properties")
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("Unsupported mux request version")
    binary, session, argv = value["binary"], value["session"], value["argv"]
    if (
        not isinstance(binary, str) or not binary or "\0" in binary
        or not isinstance(session, str) or not session or "\0" in session
        or not isinstance(argv, list) or not argv
        or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
        or len(argv) < 7 or argv[:4] != ["new-session", "-d", "-s", session]
        or argv[4] != "-c" or not argv[5]
    ):
        raise ValueError("Expected one literal detached mux creation command")
    supported = is_psmux(binary) if platform.system() == "Windows" else Path(binary).name == "tmux"
    if not supported:
        raise ValueError("This contract requires the platform's native mux")
    return binary, session, argv


def main(argv=None):
    """Finite native creation boundary; no pane attachment or effectful fallback."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--describe-contract", action="store_true")
    mode.add_argument("--create", action="store_true")
    args = parser.parse_args(argv)
    if args.describe_contract:
        print(json.dumps({
            "contract": CONTRACT, "version": 1,
            "operations": (
                ["windows-psmux-create-or-join"] if platform.system() == "Windows"
                else ["posix-tmux-create-or-join"]
            ),
            "python": str(Path(sys.executable).absolute()),
            "entrypoint": str(Path(__file__).resolve()),
        }))
        return 0
    dispatched = False

    def prepare():
        nonlocal dispatched
        dispatched = True
        return []

    try:
        binary, session, command = _request(sys.stdin)
        result = run_or_join([binary, *command], binary=binary, session=session, prepare=prepare)
        status = "joined" if result is None else "created"
        code = 0
    except Exception:
        # Never echo literal pane argv, environment values or an exception
        # containing them. Once dispatch was possible, the owner must inspect.
        status = "unknown" if dispatched else "rejected"
        code = 3
    print(json.dumps({"contract": CONTRACT, "version": 1, "status": status}))
    return code


if __name__ == "__main__":
    sys.exit(main())
