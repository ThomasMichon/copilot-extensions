"""Pin query descendants while their enclosing test Job deliberately stays open."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Protocol, cast

import pytest

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

from .service_launcher_fixture import REPOSITORY, stage_service_launchers

sys.path.insert(0, str(REPOSITORY / "libs/agent-procutil/tests"))

pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows query-tree custody")


class _ProcessPin(Protocol):
    def gone(self) -> bool: ...
    def close(self) -> None: ...


class _ProcessAPI(Protocol):
    def pin(self, pid: int, birth: int) -> _ProcessPin: ...


@pytest.fixture(scope="module")
def service_query_probe(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("service-query-probe") / "uv.exe"
    spec = importlib.util.spec_from_file_location(
        "_dispatch_query_probe_builder", REPOSITORY / "tools/build-bootstrap-capture.py",
    )
    assert spec and spec.loader
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    getattr(builder, "compile_source")(
        REPOSITORY / "plugins/agent-bridge/tests/fixtures/bootstrap-capture-probe.cs", output,
    )
    return output


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize(("role", "failure"), [
    ("adapter", "timeout"), ("coordinator", "caller-death"), ("supervisor", "caller-death"),
])
def test_managed_service_query_tree_retires_before_enclosing_job(
    tmp_path: Path, service_query_probe: Path, shell: str, role: str, failure: str,
) -> None:
    host = shutil.which(shell)
    assert host, f"required Windows shell unavailable: {shell}"
    launchers = tmp_path / "package/service_launchers"
    stage_service_launchers(launchers)
    expected = tmp_path / "unexecuted-python.exe"
    expected.touch()
    marker = tmp_path / "query-identities"
    env = {
        **os.environ, "PATH": str(service_query_probe.parent),
        "BOOTSTRAP_PROBE_MARKER": str(marker),
    }
    if failure == "timeout":
        entry = tmp_path / "bounded-query.ps1"
        def quote(value: Path) -> str:
            return "'" + str(value).replace("'", "''") + "'"
        entry.write_text(
            "$ErrorActionPreference='Stop'\n"
            f". {quote(launchers / 'runtime.ps1')}\n"
            f"Resolve-DispatchServicePython -RuntimePython {quote(expected)} "
            "-TimeoutMilliseconds 2500\n",
            encoding="utf-8",
        )
        command = [host, "-NoProfile", "-NonInteractive", "-File", str(entry)]
    else:
        command = [
            host, "-NoProfile", "-NonInteractive", "-File", str(launchers / f"{role}.ps1"),
            "-InstallDir", str(tmp_path), "-RuntimePython", str(expected),
            "-RuntimeSlot", str(tmp_path),
        ]
        if role == "supervisor":
            command += ["-EnvFile", str(tmp_path / "unread-profile.env")]
    process, outer = spawn_sync_in_kill_on_close_job(
        command, env=env, cwd=tmp_path, require_job=True, allow_breakaway=False,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window_kwargs(),
    )
    assert outer is not None
    api = cast(_ProcessAPI, getattr(importlib.import_module("sdk_native_win32"), "NativeProcessAPI")())
    pins: list[_ProcessPin] = []
    try:
        deadline = time.monotonic() + 8
        while not marker.exists():
            if process.poll() is not None:
                output, error = process.communicate(timeout=2)
                pytest.fail(f"Query did not reach the native probe: {output!r} {error!r}")
            assert time.monotonic() < deadline, "query did not publish native identities"
            time.sleep(0.01)
        values = [int(value) for value in marker.read_text().split()]
        assert len(values) == 4
        for pid, birth in zip(values[::2], values[1::2]):
            pins.append(api.pin(pid, birth))
        assert all(not pin.gone() for pin in pins)
        if failure == "caller-death":
            process.kill()
        output, error = process.communicate(timeout=8)
        assert process.returncode != 0, output + error
        if failure == "timeout":
            assert b"deadline" in error.lower(), error
        deadline = time.monotonic() + 3
        while not all(pin.gone() for pin in pins) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert all(pin.gone() for pin in pins), "query descendants survived their original caller"
    finally:
        outer.terminate_and_wait(3)
        process.wait(timeout=3)
        outer.close()
        for pin in pins:
            pin.close()
