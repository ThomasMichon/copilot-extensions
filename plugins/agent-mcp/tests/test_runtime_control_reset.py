"""Fresh reset uses real lifecycle locks and preserves competing owners."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import socket
import sys
from pathlib import Path

import pytest
from single_instance_lease import AlreadyRunningError, SingleInstance
from zdd import CutoverLock, CutoverLockedError, routing

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location(
    "mcp_control_reset_test", SCRIPTS / "reset_runtime_control.py"
)
assert SPEC and SPEC.loader
subject = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(subject)
pytestmark = pytest.mark.guard


def _reset(home: Path, *, rows: dict | None = None) -> list[dict]:
    return subject.reset_control(home, settle=lambda: [], inspect=lambda: rows or {})


def test_reset_follows_declared_scope_and_preserves_lock_identity(tmp_path: Path) -> None:
    home = tmp_path / "data"
    home.mkdir()
    controls = (
        "serve.sock",
        "serve-g12.sock",
        "serve.sock.endpoint",
        "serve-g12.sock.endpoint",
        "serve-control-999999.token",
        "active.json",
    )
    preserved = (
        "token-cache",
        "config.json",
        "serve.log",
        "serve-g12.log",
        "unknown",
        "serve-gx.sock",
        "serve-control-x.token",
    )
    for name in controls + preserved:
        (home / name).write_bytes(b"corrupt but disposable" if name in controls else b"keep")
    for name in ("active.lock", "zdd-cutover.lock", "agent-mcp-serve.lock"):
        (home / name).write_bytes(b"")
    identity = {path.name: path.stat().st_ino for path in home.glob("*.lock")}
    assert _reset(home) == []
    assert all(not (home / name).exists() for name in controls)
    assert all((home / name).read_bytes() == b"keep" for name in preserved)
    assert {path.name: path.stat().st_ino for path in home.glob("*.lock")} == identity
    assert not (tmp_path / "versions").exists()


@pytest.mark.parametrize("kind", ["normal", "cutover", "routing", "settlement"])
def test_contention_or_settlement_failure_never_unlinks_controls(
    tmp_path: Path,
    kind: str,
) -> None:
    control = tmp_path / "active.json"
    control.write_bytes(b"winning owner")
    if kind == "settlement":

        def settle() -> list[dict]:
            raise RuntimeError("unsettled process")

        with pytest.raises(RuntimeError, match="unsettled"):
            subject.reset_control(tmp_path, settle=settle, inspect=lambda: {})
    elif kind == "routing":
        # A file lock opened separately also contends within the same process.
        with routing.routing_lock(tmp_path, deadline=subject.time.monotonic() + 1):
            with pytest.raises(routing.RoutingLockTimeout):
                _reset(tmp_path)
    else:
        guard = (
            SingleInstance(tmp_path, service="agent-mcp-serve")
            if kind == "normal"
            else CutoverLock(tmp_path)
        )
        error = AlreadyRunningError if kind == "normal" else CutoverLockedError
        with guard:
            with pytest.raises(error):
                _reset(tmp_path)
    assert control.read_bytes() == b"winning owner"
    assert _reset(tmp_path) == []


@pytest.mark.parametrize(
    "kind", ["passive", "live-token", "hardlink", "directory", "live-endpoint", "string-endpoint"]
)
def test_live_or_unsafe_state_is_not_treated_as_stale(tmp_path: Path, kind: str) -> None:
    control = tmp_path / "active.json"
    control.write_bytes(b"keep")
    rows = {}
    with socket.socket() as listener:
        if kind == "passive":
            rows[123] = {
                "argv": [
                    sys.executable,
                    "-m",
                    "agent_mcp",
                    "serve",
                    "--passive",
                    "--socket",
                    str(tmp_path / "serve-g2.sock"),
                ],
            }
        elif kind == "live-token":
            (tmp_path / f"serve-control-{os.getpid()}.token").write_bytes(b"keep")
            rows[os.getpid()] = {"argv": [sys.executable]}
        elif kind == "hardlink":
            os.link(control, tmp_path / "shared-file")
        elif kind == "directory":
            (tmp_path / "serve.sock.endpoint").mkdir()
        else:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            (tmp_path / "serve.sock.endpoint").write_text(
                json.dumps(
                    {"port": str(listener.getsockname()[1]) if kind == "string-endpoint"
                     else listener.getsockname()[1]}
                ),
                encoding="utf-8",
            )
        with pytest.raises(RuntimeError):
            _reset(tmp_path, rows=rows)
    assert control.read_bytes() == b"keep"


def test_socket_link_reset_never_removes_its_foreign_target(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "foreign"
    target.write_bytes(b"keep")
    try:
        (home / "serve.sock").symlink_to(target)
    except OSError:
        pytest.skip("native symlink creation unavailable")
    with pytest.raises(RuntimeError, match="outside"):
        _reset(home)
    assert (home / "serve.sock").is_symlink()
    assert target.read_bytes() == b"keep"


@pytest.mark.parametrize(
    "name", ["active.json", "serve-control-999999.token", "serve.sock.endpoint"]
)
def test_non_socket_control_names_never_unlink_a_foreign_socket(tmp_path: Path, name: str) -> None:
    if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
        pytest.skip("Native POSIX socket file type required")
    path = tmp_path / name
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(path))
        listener.listen()
        with pytest.raises(RuntimeError, match="Unsafe"):
            _reset(tmp_path)
        assert path.is_socket()
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(path))


@pytest.mark.parametrize("role", ["active", "previous"])
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("numeric", [int, str])
def test_routing_only_endpoint_is_probed_before_deletion(
    tmp_path: Path,
    role: str,
    live: bool,
    numeric: type,
) -> None:
    path = tmp_path / "active.json"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        if live:
            listener.listen()
        path.write_text(
            json.dumps(
                {
                    role: {"bind": "127.0.0.1", "port": numeric(listener.getsockname()[1])},
                }
            ),
            encoding="utf-8",
        )
        before = path.read_bytes()
        if live:
            with pytest.raises(RuntimeError, match="live MCP routing"):
                _reset(tmp_path)
            assert path.read_bytes() == before
        else:
            listener.close()
            _reset(tmp_path)
            assert not path.exists()


@pytest.mark.parametrize("duplicate", ["active", "port"])
def test_reset_preserves_live_routes_accepted_by_the_client_json_parser(
    tmp_path: Path, duplicate: str,
) -> None:
    path = tmp_path / "active.json"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        if duplicate == "active":
            record = json.dumps({"bind": "127.0.0.1", "port": port})
            text = '{"active":null,"active":' + record + "}"
        else:
            text = '{"active":{"bind":"127.0.0.1","port":1,"port":' + str(port) + "}}"
        path.write_text(text, encoding="utf-8")
        endpoint = routing.read_active_endpoint(tmp_path, verify_listener=False)
        assert endpoint is not None and endpoint.port == port
        before = path.read_bytes()
        with pytest.raises(RuntimeError, match="live MCP routing"):
            _reset(tmp_path)
        assert path.read_bytes() == before


@pytest.mark.parametrize("occupied", [False, True])
def test_windows_timeout_requires_exclusive_kernel_endpoint_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    occupied: bool,
) -> None:
    if os.name != "nt":
        pytest.skip("Windows exclusive TCP bind required")
    path = tmp_path / "active.json"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        if occupied:
            listener.listen()
        else:
            listener.close()
        path.write_text(
            json.dumps({"active": {"bind": "127.0.0.1", "port": port}}),
            encoding="utf-8",
        )
        before = path.read_bytes()

        def timeout(*_args: object) -> None:
            raise TimeoutError("synthetic loopback connection timeout")

        monkeypatch.setattr(socket.socket, "connect", timeout)
        if occupied:
            with pytest.raises(OSError):
                _reset(tmp_path)
            assert path.read_bytes() == before
            assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
        else:
            _reset(tmp_path)
            assert not path.exists()


def test_uncertain_routing_probe_preserves_all_control_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "active.json"
    path.write_text(json.dumps({"active": {"bind": "127.0.0.1", "port": 1234}}), encoding="utf-8")
    (tmp_path / "serve.sock.endpoint").write_bytes(b"keep")
    before = path.read_bytes()

    def uncertain(address: str | tuple[str, int]) -> bool:
        raise TimeoutError("probe did not establish endpoint death")

    monkeypatch.setattr(subject, "_listening", uncertain)
    with pytest.raises(TimeoutError):
        _reset(tmp_path)
    assert path.read_bytes() == before
    assert (tmp_path / "serve.sock.endpoint").read_bytes() == b"keep"


@pytest.mark.parametrize(
    "endpoint",
    [
        {"bind": "example.invalid", "port": 1234},
        {"pid": os.getpid()},
        {"pid": str(os.getpid())},
    ],
)
def test_uncertain_or_live_pid_routing_owner_is_preserved(
    tmp_path: Path,
    endpoint: dict,
) -> None:
    path = tmp_path / "active.json"
    path.write_text(json.dumps({"active": endpoint}), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(RuntimeError, match="routing"):
        _reset(tmp_path, rows={os.getpid(): {"argv": [sys.executable]}})
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_real_serve_starts_after_reset_and_survives_losing_reset(tmp_path: Path) -> None:
    from agent_mcp.ipc import request_via_socket
    from agent_mcp.serve import Server

    (tmp_path / "active.json").write_bytes(b"{")
    _reset(tmp_path)
    handle = tmp_path / "serve.sock"
    server = Server(handle, idle_timeout=0)
    task = asyncio.create_task(server.serve_forever())
    try:
        for _ in range(100):
            if server._control_server is not None:
                break
            if task.done():
                await task
                pytest.fail("serve exited before becoming ready")
            await asyncio.sleep(0.02)
        assert (await request_via_socket(handle, {"op": "ping"}))["ok"]
        before = (tmp_path / "active.json").read_bytes()
        with pytest.raises(AlreadyRunningError):
            _reset(tmp_path)
        assert (tmp_path / "active.json").read_bytes() == before
        assert (await request_via_socket(handle, {"op": "ping"}))["ok"]
    finally:
        server.stop()
        await asyncio.wait_for(task, 5)


@pytest.mark.parametrize("language", ["ps1", "sh"])
@pytest.mark.parametrize("mode", ["fresh", "incremental"])
def test_native_cutover_runs_only_for_the_incremental_path(
    tmp_path: Path,
    language: str,
    mode: str,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "mcp_native_transactions",
        Path(__file__).with_name("test_source_install_transactions.py"),
    )
    assert spec and spec.loader
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    ROOT, _quote, _run = native.ROOT, native._quote, native._run

    text = (ROOT / "plugins/agent-mcp/scripts" / f"init.{language}").read_text(
        encoding="utf-8",
    )
    marker = tmp_path / "cutover-invoked"

    def q(value: Path | str) -> str:
        return _quote(value, language)

    if language == "ps1":
        block = re.search(
            r"(?ms)^if \(\$VersionedRuntime .*AGENT_MCP_NO_CUTOVER\) \{.*?^\}",
            text,
        )
        script = f"""
$ErrorActionPreference='Stop'
$VersionedRuntime=$true
$script:RuntimeConstructionSession=[pscustomobject]@{{Construction='{mode}'}}
$VenvPython='fixture-python'
$env:AGENT_MCP_NO_CUTOVER=''
function Write-Ok($value) {{ }}
function Write-Skip($value) {{ }}
function Invoke-Hidden {{
    param($FilePath,$ArgList)
    [IO.File]::WriteAllText({q(marker)},'cutover')
    '{{"ok":true}}'
}}
"""
    else:
        block = re.search(
            r'(?ms)^if \[\[ "\$VERSIONED_RUNTIME" -eq 1'
            r"[^\n]*AGENT_MCP_NO_CUTOVER[^\n]*; then.*?^fi$",
            text,
        )
        script = f"""
set -euo pipefail
VERSIONED_RUNTIME=1
_RUNTIME_CONSTRUCTION_MODE='{mode}'
AGENT_MCP_NO_CUTOVER=''
VENV_PYTHON=fixture_python
_ok() {{ :; }}
_skip() {{ :; }}
_step() {{ :; }}
fixture_python() {{
    for arg in "$@"; do
        if [[ "$arg" == cutover ]]; then
            printf cutover > {q(marker)}
            printf '{{"ok":true}}'
            return 0
        fi
    done
    {q(sys.executable)} "$@"
}}
"""
    assert block
    result = _run(tmp_path, language, script + block.group())
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists() is (mode == "incremental")
