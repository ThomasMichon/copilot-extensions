"""Opt-in installed target listing against private read-only HTTP and mux peers."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
import json
import os
from pathlib import Path
import runpy
import secrets
import socket
import subprocess
import time
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response
import pytest
import uvicorn

from agent_bridge.routes import health, worktree_refresh
from agent_bridge.worktree_native_resume import list_targets
from test_worktree_metadata import metadata_boundary as metadata_boundary
from test_worktree_native_resume import boundary as boundary


@pytest.mark.asyncio
@pytest.mark.parametrize(("actual", "reason"), [
    ("$7:100", "agent_route_unverified"), ("$7:101", "different-incarnation"),
    ("missing", "missing-rendezvous"), ("bad", "native-query-unavailable"),
    ("$7:100", "pinned_read_unsupported"),
])
async def test_installed_targets(
    metadata_boundary: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    actual: str, reason: str,
) -> None:
    paths = os.environ.get("BRIDGE_TEST_INSTALLED_TARGET_CLIS")
    if not paths:
        pytest.skip("requires old, new and new-with-old-driver installed console scripts")
    old, new, mixed = map(Path, paths.split(os.pathsep))
    assert len({p.resolve() for p in (old, new, mixed)}) == 3
    assert all(p.is_absolute() and p.is_file() for p in (old, new, mixed))
    wire = runpy.run_path(str(Path(__file__).resolve().parents[2] / "agent-remote-driver"
                              / "tests" / "test_mux_observation.py"))
    b = metadata_boundary
    observed_at = time.monotonic()
    monkeypatch.setattr(worktree_refresh, "time", SimpleNamespace(monotonic=lambda: observed_at, time=time.time))
    app = FastAPI()
    for k, v in vars(b.request.app.state).items():
        setattr(app.state, k, v)
    app.add_api_route("/health", health.health, methods=["GET"])
    app.add_api_route("/api/v1/targets", list_targets, methods=["GET"])
    token = secrets.token_hex(24)
    reads: list[tuple[str, str]] = []

    @app.middleware("http")
    async def restrict(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        pair = request.method, request.url.path
        reads.append(pair)
        if pair not in {("GET", "/health"), ("GET", "/api/v1/targets")} or request.url.query:
            return Response(status_code=403)
        if pair[1] != "/health" and request.headers.get("authorization", "").partition(" ")[2] != token:
            return Response(status_code=403)
        return await call_next(request)

    def no_writer() -> None:
        raise OSError("no workspace writer in fixture")

    monkeypatch.setattr(health, "observe_worktree_writer", no_writer)
    config = tmp_path / ".agent-bridge"
    config.mkdir(exist_ok=True)
    (config / "auth.yaml").write_text(f"token: {token}\n", encoding="utf-8")
    pin = tmp_path / "source.json"
    pin.write_text(json.dumps(["wt-fixture", "$7:100"]), encoding="utf-8")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "LANG") if k in os.environ}
        env.update(HOME=str(tmp_path), USERPROFILE=str(tmp_path), APPDATA=str(tmp_path),
                   LOCALAPPDATA=str(tmp_path), TEMP=str(tmp_path), TMP=str(tmp_path), PATH="",
                   PYTHONSAFEPATH="1", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
                   AGENT_BRIDGE_BASE_URL=f"http://127.0.0.1:{listener.getsockname()[1]}",
                   AGENT_BRIDGE_NO_ROUTING_TABLE="1", COPILOT_DRIVER_PSMUX_NAMESPACE="fixture")

        async def cli(exe: Path, *args: str) -> dict[str, Any]:
            offset = len(reads)
            p = await asyncio.to_thread(subprocess.run, [str(exe), "targets", "--json", *args],
                                        cwd=tmp_path, env=env, capture_output=True, timeout=30,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            assert p.returncode == 0, p.stderr
            assert set(reads[offset:]) == {("GET", "/health"), ("GET", "/api/v1/targets")}
            result = json.loads(p.stdout)
            assert isinstance(result, dict)
            return result

        try:
            for _ in range(500):
                if server.started or serving.done():
                    break
                await asyncio.sleep(.02)
            assert server.started
            baseline = await cli(old)
            exe = mixed if reason == "pinned_read_unsupported" else new
            root = tmp_path / "mux"
            root.mkdir()
            env["PSMUX_DATA_DIR"] = str(root)
            scope: Any = nullcontext([])
            if actual.startswith("$"):
                raw = (wire["_PANE"] + wire["_IDENTITY"]).replace(b"$7:100", actual.encode())
                scope = wire["native_endpoint"](root, b"OK\n" + raw, namespace="fixture")
            elif actual == "bad":
                (root / "fixture__wt-fixture.port").write_bytes(b"invalid")
                (root / "fixture__wt-fixture.key").write_bytes(b"synthetic")
            with scope as calls:
                assert await cli(exe) == baseline and not calls
                result = await cli(exe, "--discover-mux", *json.loads(pin.read_text()))
            source = result["sources"].pop("pinned_mux")
            assert result == baseline and source["route"] == "unresolved" and source["reason"] == reason
            assert source.get("incarnation") == ("$7:100" if reason == "agent_route_unverified" else None)
            assert calls[1::2] == (list(wire["mux"]._QUERIES) if exe == new and actual.startswith("$") else [])
            assert await cli(old) == baseline
            assert not b.calls and not b.manager.list_sessions() and not b.db.list_live_sessions(include_dead=True)
            assert not b.db.execute_read("SELECT * FROM turns")
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=10)
