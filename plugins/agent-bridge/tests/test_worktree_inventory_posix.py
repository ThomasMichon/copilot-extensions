"""Passive POSIX inventory retains its original group after the root exits."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

from agent_procutil.owned_process import OwnedCleanupError
import pytest

from agent_bridge import session_lifecycle_cli
from agent_bridge.routes import worktree_inventory


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["ok", "timeout", "launch-failed", "nonzero", "unconfirmed"])
async def test_posix_inventory_uses_the_existing_finite_native_owner(
    monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    calls: list[Any] = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        notify = kwargs.pop("on_settled")
        assert callable(notify)
        calls.append((argv, kwargs))
        if outcome == "launch-failed":
            raise RuntimeError("native custody unavailable")
        if outcome != "unconfirmed":
            notify()
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return subprocess.CompletedProcess(argv, 1 if outcome == "nonzero" else 0, "[]", "diagnostic")

    monkeypatch.setattr(worktree_inventory, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(worktree_inventory, "run_owned", run)
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_launch_prefix", lambda **kwargs: ["owner"])
    monkeypatch.setattr(session_lifecycle_cli, "_agent_worktrees_environment", lambda: {"OWNER": "selected"})
    result = await worktree_inventory.read("source", ["list", "--json", "--passive"], timeout=2)
    assert calls == [(
        ["owner", "--project", "source", "list", "--json", "--passive"],
        {"timeout": 2, "env": {"OWNER": "selected"}},
    )]
    assert result == {
        "ok": ("[]", "diagnostic"),
        "nonzero": (None, "diagnostic"),
        "timeout": (None, "worktree_owner_timeout"),
        "launch-failed": (None, "RuntimeError"),
        "unconfirmed": (None, "worktree_owner_cleanup_unconfirmed"),
    }[outcome]


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_uncertain", [False, True])
async def test_posix_cancellation_joins_the_owner_and_preserves_unconfirmed_cleanup(
    monkeypatch: pytest.MonkeyPatch, cleanup_uncertain: bool,
) -> None:
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    receipt = OwnedCleanupError(None)

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        entered.set()
        try:
            assert release.wait(5)
            if cleanup_uncertain:
                raise receipt
            kwargs["on_settled"]()
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        finally:
            finished.set()

    monkeypatch.setattr(worktree_inventory, "run_owned", run)
    task = asyncio.create_task(worktree_inventory._read_posix(["owner"], timeout=2, env={}))
    try:
        deadline = time.monotonic() + 3
        while not entered.is_set():
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        if cleanup_uncertain:
            with pytest.raises(OwnedCleanupError) as error:
                await task
            assert error.value is receipt
        else:
            with pytest.raises(asyncio.CancelledError):
                await task
        assert finished.is_set()
    finally:
        release.set()
        with suppress(asyncio.CancelledError, OwnedCleanupError):
            await task


@contextmanager
def pinned_exit(pid: int) -> Iterator[Callable[[], bool]]:
    native: Any = select
    if sys.platform == "darwin":
        with native.kqueue() as queue:
            change = native.kevent(
                pid, filter=native.KQ_FILTER_PROC,
                flags=native.KQ_EV_ADD | native.KQ_EV_ONESHOT, fflags=native.KQ_NOTE_EXIT,
            )
            assert not queue.control([change], 1, 0)
            exited = False

            def observed() -> bool:
                nonlocal exited
                for event in queue.control(None, 1, 0):
                    assert not event.flags & native.KQ_EV_ERROR
                    assert event.ident == pid and event.fflags & native.KQ_NOTE_EXIT
                    exited = True
                return exited

            yield observed
    else:
        from agent_procutil._caller_owned import _pidfd_exited
        from agent_procutil._linux_pidfd import open_pidfd

        descriptor = open_pidfd(pid)
        try:
            assert not _pidfd_exited(descriptor)
            yield lambda: _pidfd_exited(descriptor)
        finally:
            os.close(descriptor)


@pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="requires native POSIX group custody")
@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("cycle", [0, 1])
async def test_exited_parent_does_not_release_pipe_holding_descendant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cancelled: bool, cycle: int,
) -> None:
    marker = tmp_path / f"processes-{cycle}.json"
    root_release, child_release = tmp_path / "root.release", tmp_path / "child.release"
    child = (
        "import pathlib,time;"
        f"release=pathlib.Path({str(child_release)!r});deadline=time.monotonic()+15\n"
        "while not release.exists() and time.monotonic()<deadline: time.sleep(.01)\n"
    )
    root = (
        "import json,os,pathlib,subprocess,sys,time;"
        f"child=subprocess.Popen([sys.executable,'-I','-c',{child!r}],"
        "creationflags=(0x08000000 if sys.platform=='win32' else 0));"
        f"pathlib.Path({str(marker)!r}).write_text(json.dumps({{'root':os.getpid(),'child':child.pid}}));"
        f"release=pathlib.Path({str(root_release)!r})\n"
        "while not release.exists(): time.sleep(.01)\n"
        "os._exit(0)\n"
    )
    monkeypatch.setattr(
        session_lifecycle_cli, "_agent_worktrees_launch_prefix",
        lambda **kwargs: [sys.executable, "-I", "-c", root],
    )
    task = asyncio.create_task(worktree_inventory.read("source", ["list", "--json"], timeout=3))
    try:
        deadline = time.monotonic() + 2
        while not marker.exists() or not marker.read_text():
            assert not task.done(), "owner ended before the original processes were pinned"
            assert time.monotonic() < deadline
            await asyncio.sleep(.01)
        identities = json.loads(marker.read_text())
        with pinned_exit(identities["root"]) as root_exited, pinned_exit(identities["child"]) as child_exited:
            root_release.touch()
            while not root_exited():
                assert time.monotonic() < deadline
                await asyncio.sleep(.01)
            assert not child_exited()
            if cancelled:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert await task == (None, "worktree_owner_timeout")
            assert child_exited(), "the original pipe-holding descendant survived cleanup"
    finally:
        root_release.touch()
        child_release.touch()
        if not task.done():
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
