"""Create mux clients without lending a Windows worktree to idle standbys."""

from contextlib import contextmanager
import hashlib
import locale
import os
from pathlib import Path, PureWindowsPath
import platform
import subprocess
import tempfile
import time

from agent_procutil import no_window_kwargs
from single_instance_lease import AlreadyRunningError, SingleInstance


class CreationUncertain(RuntimeError):
    """A dispatched mux client failed without disproving pane creation."""


def is_psmux(binary: str) -> bool:
    return PureWindowsPath(binary).stem.casefold() == "psmux"


def has_session(binary: str, session: str, *, timeout: float = 2) -> bool:
    """Distinguish an absent PSMux session from an unavailable query."""
    result = subprocess.run(
        [binary, "has-session", "-t", session], capture_output=True, timeout=timeout,
        cwd=str(Path.home()), **no_window_kwargs(),
    )
    if result.returncode not in (0, 1):
        raise RuntimeError("PSMux session discovery failed; refusing creation")
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
            if exists():
                yield False
                return
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


def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Keep the client launch directory separate from the pane's explicit CWD."""
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=15,
        cwd=str(Path.home()) if platform.system() == "Windows" else None,
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
        argv.extend(prepare())
        return run_session(argv, session, release) if gated else run(argv)


def run_session(argv: list[str], session: str, release) -> subprocess.CompletedProcess[str]:
    """Release check/create custody at discovery, not at client completion."""
    deadline = time.monotonic() + 15
    # A mux server can outlive the client and inherit its output handles.
    # Regular files cannot keep a pipe reader or its interpreter waiting.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr, subprocess.Popen(
        argv, stdout=stdout, stderr=stderr, cwd=str(Path.home()), **no_window_kwargs(),
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
                    if pending and remaining > 0 and has_session(
                        argv[0], session, timeout=min(2, remaining),
                    ):
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
