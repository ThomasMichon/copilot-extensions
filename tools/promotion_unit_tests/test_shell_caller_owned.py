"""Actor-free shell lifetime framing and admission controls."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "libs/agent-procutil/src/agent_procutil"
NONCE = "a" * 32


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def _native_imports():
    # Native bindings are dependency doubles, never framework-preloaded DLLs.
    # Separate native tests exercise the real ctypes layout and kernel calls.
    native = ModuleType("ctypes")
    native.wintypes = SimpleNamespace(
        HANDLE=lambda value=None: SimpleNamespace(value=value),
        ULONG=lambda: SimpleNamespace(value=0),
    )
    native.byref = lambda value: SimpleNamespace(_obj=value)
    native.sizeof = lambda value: 144
    native.WinError = lambda *args: OSError("native failure")
    native.get_last_error = lambda: 5
    package = ModuleType("agent_procutil")
    package._is_windows = lambda: True
    package._build_job_extended_limit_info = lambda: lambda: SimpleNamespace(
        BasicLimitInformation=SimpleNamespace(LimitFlags=0),
    )
    identity = ModuleType("agent_procutil._daemon_instance_identity")
    identity._windows_birth = lambda handle: "windows:13"
    child = ModuleType("agent_procutil._windows_caller_owned")
    child._api = lambda: pytest.fail("unmocked native API access")
    with patch.dict(sys.modules, {
        "ctypes": native, "agent_procutil": package,
        "agent_procutil._daemon_instance_identity": identity,
        "agent_procutil._windows_caller_owned": child,
    }):
        yield package


owner = _load("unit_shell_caller_owner", "_shell_caller_owned.py")
with _native_imports():
    parent_job = _load("agent_procutil._unit_shell_parent_job", "_windows_parent_job.py")


@pytest.mark.parametrize("code", [0, 1, 7, 255])
def test_callback_preserves_exact_exit_codes(code):
    assert owner._callback_code(lambda: code) == code


@pytest.mark.parametrize("code", [False, None, "0", -1, 256])
def test_invalid_callback_results_are_explicit_failures(code, capsys):
    assert owner._callback_code(lambda: code) == 1
    assert "invalid exit code" in capsys.readouterr().err


def test_callback_exception_is_reported_without_success(capsys):
    def broken():
        raise RuntimeError("controlled callback failure")

    assert owner._callback_code(broken) == 1
    assert "controlled callback failure" in capsys.readouterr().err


@pytest.mark.parametrize(
    "code,witness,completed",
    [(0, b"\0", True), (7, b"\7", True), (0, b"", False),
     (0, b"\7", False), (-9, b"\0", False), (0, None, False)],
)
def test_posix_parent_requires_return_witness_after_exact_child_exit(
    monkeypatch, capsys, code, witness, completed,
):
    class Retired(Exception):
        pass

    def retire():
        raise Retired

    def read(descriptor, size):
        if descriptor == 60:
            assert size == 1
            return b"1"
        assert descriptor == 30 and size == 2
        if witness is None:
            raise BlockingIOError("a descendant still holds the writer")
        return witness

    pipes = iter([(10, 20), (30, 40), (60, 70)])
    closed = []
    monkeypatch.setitem(
        sys.modules, "agent_procutil._caller_owned", SimpleNamespace(_retire=retire),
    )
    monkeypatch.setattr(owner, "os", SimpleNamespace(
        pipe=lambda: next(pipes), fork=lambda: 501, close=closed.append,
        getpid=lambda: 500, getpgrp=lambda: 500, getsid=lambda pid: 500,
        set_blocking=lambda fd, blocking: None, read=read,
        write=lambda fd, value: len(value), waitpid=lambda pid, flags: (501, code),
        waitstatus_to_exitcode=lambda status: status,
    ))
    with pytest.raises(Retired):
        owner._posix_callback(lambda: pytest.fail("parent ran callback"), 55, NONCE)
    captured = capsys.readouterr()
    if completed:
        assert captured.out == f"\n__COPILOT_CALLER_EXIT_{NONCE}={code}\n"
        assert not captured.err
    else:
        assert not captured.out
        assert "callback completion is unconfirmed" in captured.err
    assert sorted(closed) == [10, 20, 30, 40, 60, 70]


@pytest.mark.parametrize("failure", ["fork", "ready"])
def test_posix_watcher_failure_never_releases_the_callback_gate(
    monkeypatch: pytest.MonkeyPatch, capsys, failure: str,
) -> None:
    class Retired(Exception):
        pass

    def retire() -> None:
        raise Retired

    forks = iter([501, 502])

    def fork() -> int:
        child = next(forks)
        if child == 502 and failure == "fork":
            raise OSError("controlled watcher fork failure")
        return child

    pipes = iter([(10, 20), (30, 40), (60, 70)])
    writes: list[tuple] = []
    closed: list[int] = []
    monkeypatch.setitem(
        sys.modules, "agent_procutil._caller_owned", SimpleNamespace(_retire=retire),
    )
    monkeypatch.setattr(owner, "os", SimpleNamespace(
        pipe=lambda: next(pipes), fork=fork, close=closed.append,
        getpid=lambda: 500, getpgrp=lambda: 500, getsid=lambda pid: 500,
        set_blocking=lambda fd, blocking: None, read=lambda fd, size: b"",
        write=lambda fd, value: writes.append((fd, value)),
    ))
    with pytest.raises(Retired):
        owner._posix_callback(lambda: pytest.fail("unwatched application"), 55, NONCE)
    assert not writes
    assert sorted(closed) == [10, 20, 30, 40, 60, 70]
    assert "watcher" in capsys.readouterr().err


@pytest.mark.parametrize("group,session", [(500, 500), (999, 500), (500, 999)])
def test_posix_watcher_signals_only_its_still_occupied_original_scope(
    monkeypatch: pytest.MonkeyPatch, group: int, session: int,
) -> None:
    class Retired(Exception):
        pass

    calls: list[tuple] = []

    def exit_process(code: int) -> None:
        raise Retired

    monkeypatch.setattr(owner, "signal", SimpleNamespace(SIGKILL=9))
    monkeypatch.setattr(
        owner, "open", lambda path, mode: nullcontext(SimpleNamespace(fileno=lambda: 99)),
        raising=False,
    )
    monkeypatch.setattr(owner, "os", SimpleNamespace(
        close=lambda fd: calls.append(("close", fd)), devnull=os.devnull,
        dup2=lambda source, target: calls.append(("redirect", target)),
        getpid=lambda: 502, getpgrp=lambda: group, getsid=lambda pid: session,
        write=lambda fd, value: calls.append(("ready", fd, value)) or 1,
        read=lambda fd, size: calls.append(("lifetime", fd, size)) or b"",
        killpg=lambda pgid, signum: calls.append(("retire", pgid, signum)),
        _exit=exit_process,
    ))
    with pytest.raises(Retired):
        owner._posix_watch(55, 70, 500, (60, 20, 30))
    if group == session == 500:
        assert calls[-3:] == [
            ("close", 70), ("lifetime", 55, 1), ("retire", 500, owner.signal.SIGKILL),
        ]
    else:
        assert not any(call[0] in {"ready", "lifetime", "retire"} for call in calls)


@pytest.fixture
def windows(monkeypatch):
    calls = []
    incoming = io.BytesIO(("offer:" + NONCE + "\n" + NONCE + "\n").encode())
    monkeypatch.setattr(owner, "os", SimpleNamespace(
        name="nt", read=lambda descriptor, size: incoming.read(size),
    ))
    monkeypatch.setattr(owner.sys, "stdin", SimpleNamespace(fileno=lambda: 55))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: 505))
    monkeypatch.setitem(sys.modules, "agent_procutil", ModuleType("agent_procutil"))
    monkeypatch.setitem(sys.modules, "agent_procutil._windows_parent_job", parent_job)
    monkeypatch.setattr(owner.threading, "active_count", lambda: 1)

    def transfer(**kwargs):
        calls.append(("transfer", kwargs))
        offer = SimpleNamespace(
            receipt=parent_job.ParentJob(12, "windows:13", 14, "windows:15", 303), parent=101,
        )

        def settle(name):
            calls.append((name,))
            offer.parent = 0

        offer.accept = lambda: settle("accept")
        offer.abort = lambda: settle("abort")
        offer.abandon = lambda: settle("unknown")
        return offer

    monkeypatch.setattr(parent_job, "offer_lifetime_to_pipe_server", transfer)
    return SimpleNamespace(calls=calls, incoming=incoming)


def test_adopted_pipe_job_precedes_ack_and_application(windows, capsys):
    def callback():
        before = capsys.readouterr()
        receipt = json.loads(before.out)
        assert receipt == {
            "version": 1, "nonce": NONCE, "owner_pid": 12, "scope": "windows-job",
            "job": {
                "parent_pid": 12, "parent_birth": "windows:13", "worker_pid": 14,
                "worker_birth": "windows:15", "parent_handle": 303,
            },
        }
        assert windows.incoming.tell() == 72
        windows.calls.append(("callback",))
        print("application output")
        return 7

    assert owner.serve(callback, owner_pid=12, owner_birth="windows:13", nonce=NONCE) == 7
    assert windows.calls == [
        ("transfer", {"parent_pid": 12, "parent_birth": "windows:13", "pipe_handle": 505}),
        ("accept",),
        ("callback",),
    ]
    assert capsys.readouterr().out == f"application output\n\n__COPILOT_CALLER_EXIT_{NONCE}=7\n"


@pytest.mark.parametrize("ack", [b"", b"wrong\n", b"a" * 31, b"a" * 32 + b"\r\n"])
def test_missing_or_changed_ack_never_starts_application(windows, capsys, ack):
    windows.incoming = io.BytesIO(("offer:" + NONCE + "\n").encode() + ack)
    owner.os.read = lambda fd, size: windows.incoming.read(size)

    def forbidden():
        pytest.fail("unacknowledged caller reached application work")

    with pytest.raises(RuntimeError, match="did not acknowledge"):
        owner.serve(forbidden, owner_pid=12, owner_birth="windows:13", nonce=NONCE)
    assert windows.calls[-1] == ("unknown",)
    assert len(windows.calls) == 2
    assert "__COPILOT_CALLER_EXIT_" not in capsys.readouterr().out


@pytest.mark.parametrize("pid", [False, 0, -1, 1 << 32, "12", None])
def test_invalid_caller_never_attempts_lifetime_transfer(windows, pid):
    with pytest.raises(ValueError, match="original caller"):
        owner.serve(lambda: 0, owner_pid=pid, owner_birth="windows:13", nonce=NONCE)
    assert windows.calls == []


@pytest.mark.parametrize("nonce", [None, "", "a" * 31, "A" * 32, "z" * 32])
def test_invalid_correlation_never_attempts_lifetime_transfer(windows, nonce):
    with pytest.raises(ValueError, match="correlated request"):
        owner.serve(lambda: 0, owner_pid=12, owner_birth="windows:13", nonce=nonce)
    assert windows.calls == []


def test_native_transfer_failure_never_admits_or_starts_application(windows, monkeypatch, capsys):
    def failed(**kwargs):
        raise OSError("controlled Job transfer failure")

    monkeypatch.setattr(parent_job, "offer_lifetime_to_pipe_server", failed)
    with pytest.raises(OSError, match="controlled Job transfer"):
        owner.serve(lambda: pytest.fail("failed Job reached application"), owner_pid=12,
                    owner_birth="windows:13", nonce=NONCE)
    assert not capsys.readouterr().out


@pytest.mark.parametrize("permission", [b"", b"wrong\n", b"offer:" + b"a" * 31])
def test_no_job_is_offered_before_complete_request_permission(windows, permission: bytes) -> None:
    windows.incoming = io.BytesIO(permission)
    owner.os.read = lambda fd, size: windows.incoming.read(size)
    with pytest.raises(RuntimeError, match="did not acknowledge"):
        owner.serve(lambda: pytest.fail("unpermitted application"), owner_pid=12,
                    owner_birth="windows:13", nonce=NONCE)
    assert not windows.calls


@pytest.mark.parametrize("publication_failure", [False, True])
def test_explicit_unadopted_abort_reclaims_even_after_publication_failure(
    windows, monkeypatch: pytest.MonkeyPatch, publication_failure: bool,
) -> None:
    windows.incoming = io.BytesIO(
        ("offer:" + NONCE + "\nabort:" + NONCE + "\n").encode(),
    )
    owner.os.read = lambda fd, size: windows.incoming.read(size)
    if publication_failure:
        def failed_write(value: str) -> None:
            raise OSError("controlled receipt publication failure")

        monkeypatch.setattr(owner.sys, "stdout", SimpleNamespace(write=failed_write))
    with pytest.raises(RuntimeError, match="refused the unadopted"):
        owner.serve(lambda: pytest.fail("aborted application"), owner_pid=12,
                    owner_birth="windows:13", nonce=NONCE)
    assert windows.calls[-1] == ("abort",)


def test_adoption_process_reference_close_failure_never_starts_application(
    windows, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failed_offer(**kwargs):
        def accept() -> None:
            offer.parent = 0
            raise OSError("controlled pinned-parent close failure")

        offer = SimpleNamespace(
            receipt=parent_job.ParentJob(12, "windows:13", 14, "windows:15", 303),
            parent=101, accept=accept,
            abandon=lambda: pytest.fail("must not retry a possibly reused handle"),
        )
        return offer

    monkeypatch.setattr(parent_job, "offer_lifetime_to_pipe_server", failed_offer)
    with pytest.raises(OSError, match="pinned-parent close failure"):
        owner.serve(lambda: pytest.fail("failed handoff ran application"), owner_pid=12,
                    owner_birth="windows:13", nonce=NONCE)


@pytest.mark.parametrize("decision", ["accept", "abort", "unknown"])
def test_offer_settles_only_through_the_original_pinned_parent(
    monkeypatch: pytest.MonkeyPatch, decision: str,
) -> None:
    calls: list[tuple] = []

    def duplicate(source, handle, target, output, rights, inherit, options) -> int:
        calls.append(("reclaim", source, handle.value, target, rights, inherit, options))
        output._obj.value = 404
        return 1

    kernel = SimpleNamespace(
        GetCurrentProcess=lambda: -1, DuplicateHandle=duplicate,
        CloseHandle=lambda handle: calls.append((
            "close", handle.value if hasattr(handle, "value") else handle,
        )) or 1,
    )
    offer = parent_job.ParentJobOffer(
        parent_job.ParentJob(12, "windows:13", 14, "windows:15", 303), kernel, 101,
    )
    if decision == "abort":
        offer.abort()
        assert calls == [
            ("reclaim", 101, 303, -1, 0, False, 3), ("close", 404), ("close", 101),
        ]
    else:
        if decision == "accept":
            offer.accept()
        else:
            offer.abandon()
        assert calls == [("close", 101)]
    assert offer.parent == 0
    with pytest.raises(RuntimeError, match="already settled"):
        offer.abort()
    assert sum(call[0] == "reclaim" for call in calls) == (decision == "abort")


@pytest.mark.parametrize("failure", ["reclaim", "close-recovered", "close-parent"])
def test_offer_cleanup_failure_never_retries_a_remote_numeric_handle(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    calls: list[str] = []

    def duplicate(source, handle, target, output, rights, inherit, options) -> bool:
        calls.append("reclaim")
        output._obj.value = 404
        return failure != "reclaim"

    def close(handle) -> bool:
        name = "close-recovered" if hasattr(handle, "value") else "close-parent"
        calls.append(name)
        return failure != name

    offer = parent_job.ParentJobOffer(
        parent_job.ParentJob(12, "windows:13", 14, "windows:15", 303),
        SimpleNamespace(GetCurrentProcess=lambda: -1, DuplicateHandle=duplicate, CloseHandle=close),
        101,
    )
    with pytest.raises(OSError, match="native failure"):
        offer.abort()
    assert calls[-1] == "close-parent"
    assert offer.parent == 0
    with pytest.raises(RuntimeError, match="already settled"):
        offer.abort()
    assert calls.count("reclaim") == 1


@pytest.mark.parametrize("failure", [
    "", "birth", "pipe", "duplicate", "assign", "scope-publication", "close-local",
])
@pytest.mark.parametrize("offered", [False, True])
def test_real_pipe_transfer_core_retains_one_original_owner(monkeypatch, failure, offered):
    calls = []
    parent_pid = os.getpid() + 10000

    def result(name, value=1):
        calls.append(name)
        return value

    def pipe(handle, output):
        assert handle == 505
        output._obj.value = parent_pid if failure != "pipe" else parent_pid + 1
        return result("pipe")

    def duplicate(source, handle, target, output, rights, inherit, options):
        assert not inherit
        output._obj.value = 404 if options & 1 else 303
        return result("reclaim" if options & 1 else "duplicate", failure != "duplicate")

    scope_name = "Local\\ce-owned-job-" + "0" * 32

    def create_job(security, name):
        assert name == scope_name
        return result("create", 202)

    def publish_scope(name):
        assert name == scope_name
        result("publish-scope")
        if failure == "scope-publication":
            raise OSError("scope publication failed")

    package = ModuleType("agent_procutil")
    package._job_scopes = SimpleNamespace(
        new_name=lambda: scope_name, publish_current=publish_scope,
    )
    monkeypatch.setitem(sys.modules, "agent_procutil", package)
    kernel = SimpleNamespace(
        OpenProcess=lambda rights, inherit, pid: result("open", 101),
        GetCurrentProcess=lambda: -1,
        GetNamedPipeServerProcessId=pipe,
        WaitForSingleObject=lambda handle, timeout: result("live", 0x102),
        CreateJobObjectW=create_job,
        SetInformationJobObject=lambda *args: result("limits"),
        DuplicateHandle=duplicate,
        AssignProcessToJobObject=lambda *args: result("assign", failure != "assign"),
        CloseHandle=lambda handle: result("close", not (failure == "close-local" and handle == 202)),
    )
    monkeypatch.setattr(parent_job, "_api", lambda: kernel)
    monkeypatch.setattr(parent_job, "_is_windows", lambda: True)
    monkeypatch.setattr(parent_job, "_transferred", False)
    monkeypatch.setattr(parent_job.threading, "active_count", lambda: 1)
    monkeypatch.setattr(parent_job, "_windows_birth", lambda handle: (
        "windows:13" if handle == 101 and failure != "birth" else "windows:15"
    ))
    monkeypatch.setattr(parent_job.ctypes, "WinError", lambda *args: OSError("native failure"), raising=False)
    monkeypatch.setattr(parent_job.ctypes, "get_last_error", lambda: 5, raising=False)
    transfer = (
        parent_job.offer_lifetime_to_pipe_server if offered
        else parent_job._transfer
    )
    if failure:
        with pytest.raises((ProcessLookupError, OSError)):
            transfer(
                parent_pid=parent_pid, parent_birth="windows:13", pipe_handle=505,
            )
        if failure in ("birth", "pipe"):
            assert "create" not in calls
        if failure == "assign":
            assert "reclaim" in calls
        if failure in ("scope-publication", "close-local"):
            assert ("reclaim" in calls) == offered
    else:
        transferred = transfer(
            parent_pid=parent_pid, parent_birth="windows:13", pipe_handle=505,
        )
        receipt = transferred.receipt if offered else transferred
        assert receipt.parent_pid == parent_pid and receipt.parent_handle == 303
        if offered:
            assert calls[-1] == "close"
            assert calls.count("close") == 1
            assert transferred.parent == 101
            transferred.accept()
        assert calls == [
            "open", "pipe", "live", "create", "limits", "duplicate",
            "live", "assign", "publish-scope", "close", "close",
        ]
