"""Private HTTP fixture for the installed Dispatch to Bridge trial."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack
from pathlib import Path
import socket
import sys
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch


def server(root: Path, phase: str, lost: bool) -> None:
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from agent_bridge import native_receipt_records as rr
    from agent_bridge.native_send_admission import submit
    from agent_bridge.db import Database
    from agent_bridge.events import EventLog
    from agent_bridge.protocol import HTTP_PROTOCOL_VERSION, HTTP_PROTOCOL_MIN_SUPPORTED
    from agent_bridge.routes.prompts import router
    from agent_bridge.session_manager import SessionManager, Session
    from agent_bridge.session_status import SessionStatus
    from agent_bridge.transport import SpawnTarget

    assert Path(str(rr.__file__)).resolve().is_relative_to(Path(sys.prefix).resolve())
    app = FastAPI()
    sid, native = "bridge-a", "native-a"
    records = root / "bridge-records"
    store = rr.NativeReceiptRecords.create(records) if phase == "send" else rr.NativeReceiptRecords(records / "native-receipts.db")
    app.state.native_receipt_records = store
    app.include_router(router, prefix="/api/v1/sessions")
    token = (root / "auth-key").read_text()

    @app.middleware("http")
    async def authorize(request: Request, call_next: Any) -> Any:
        if request.headers.get("authorization", "").partition(" ")[2] != token and request.url.path != "/health":
            return JSONResponse({}, status_code=403)
        if phase != "send" and request.method == "POST" and not request.url.path.endswith("/receipt"):
            (root / "forbidden-send").touch()
            return JSONResponse({}, status_code=409)
        result = await call_next(request)
        with (root / (phase + "-http.log")).open("a") as out:
            print(request.method, request.url.path, result.status_code, file=out)
        return result

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"service": "agent-bridge", "ready": True, "status": "ok", "protocol_version": HTTP_PROTOCOL_VERSION,
                "min_protocol_version": HTTP_PROTOCOL_MIN_SUPPORTED,
                "capabilities": {"native_send_receipt": 1, "native_send_no_resume": 1}}

    @app.get("/api/v1/sessions/{target}")
    async def session_info(target: str) -> dict[str, str]:
        assert target == sid and phase == "send"
        return {"session_id": sid, "acp_session_id": native, "status": "idle"}

    with ExitStack() as stack:
        if phase != "absent":
            db = Database(root / "origin" / "bridge.db")
            stack.callback(db.close)
            manager = SessionManager(db)
            session = Session(sid, "fixture", SpawnTarget(type="local", cwd=str(root)))
            session.status, session.acp_session_id, session.client = SessionStatus.IDLE, native, MagicMock(is_running=True)
            session.event_log = EventLog(db=db, session_id=sid)
            manager._sessions[sid] = session
            if phase == "send":
                db.create_session(sid, "fixture", None, str(root), "local", "idle", 1.0)
            async def counted(*_: Any) -> None:
                with (root / "prompt-count").open("a") as out:
                    out.write("1\n")
            stack.enter_context(patch.object(manager, "_run_prompt", counted))
            stack.enter_context(patch.object(manager, "_notify_host_reapable", AsyncMock()))
            for name in ("resume_session", "handoff_session"):
                stack.enter_context(patch.object(manager, name, side_effect=AssertionError(name)))
            app.state.session_manager = manager
        else:
            assert not (root / "origin").exists()
            app.state.session_manager = SimpleNamespace(get_session=lambda _: None)
        if phase == "send" and lost:
            original = submit
            async def dropped(*args: Any) -> Any:
                await original(*args)
                raise ConnectionError("synthetic response lost after original admission")
            stack.enter_context(patch.object(rr, "submit", dropped))
        sock = stack.enter_context(socket.socket())
        sock.bind(("127.0.0.1", 0))
        bound = root / ("endpoint-" + phase)
        instance = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="critical"))
        pending = bound.with_suffix(".tmp")
        pending.write_text(str(sock.getsockname()[1]))
        pending.replace(bound)
        async def serve() -> None:
            running = asyncio.create_task(instance.serve(sockets=[sock]))
            deadline = time.monotonic() + 120
            while not running.done() and not (root / ("stop-" + phase)).exists() and time.monotonic() < deadline:
                await asyncio.sleep(.05)
            instance.should_exit = True
            await running
        asyncio.run(serve())
    (root / ("closed-" + phase)).touch()


if __name__ == "__main__":
    server(Path(sys.argv[1]), sys.argv[2], bool(int(sys.argv[3])))
