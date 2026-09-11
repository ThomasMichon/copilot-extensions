"""Authenticated native lifecycle and terminal presentation; never ACP."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json

from fastapi import APIRouter, Body, HTTPException, Request, WebSocket

from ..native_manager import CAPABILITY
from ..native_store import NativeError
from ..session_host.client import SessionHostClient
from ..session_host.protocol import pack_frame
from .acp_ws import _provided_token

router = APIRouter(prefix="/api/v1/native-executions", tags=["native-executions"])


def manager(request):
    value = getattr(request.app.state, "native_manager", None)
    if value is None:
        raise HTTPException(503, detail={"code": "native_unavailable"})
    return value


async def invoke(awaitable):
    try:
        return await awaitable
    except NativeError as exc:
        raise HTTPException(exc.status, detail={"code": exc.code, "detail": exc.detail}) from exc


@router.get("/capabilities")
async def capabilities(request: Request):
    return {"capability": CAPABILITY, "version": 1, "mode": "native",
            "hostResources": "native-host-resources-v1",
            "providerAvailable": bool(manager(request).provider_command())}


@router.get("/resolve")
async def resolve(request: Request, target: str):
    return {"execution": await invoke(manager(request).resolve(target))}


@router.get("")
async def list_executions(request: Request):
    owner = manager(request)
    return {"executions": [await invoke(owner.status(row["id"])) for row in owner.records()]}


@router.post("")
async def start(request: Request, body: dict = Body(...)):
    return await invoke(manager(request).start(body))


@router.get("/{execution_id}")
async def status(execution_id: str, request: Request, generation: str | None = None):
    return await invoke(manager(request).status(execution_id, generation))


@router.post("/{execution_id}/stop")
async def stop(execution_id: str, request: Request, body: dict = Body(...)):
    if not body.get("generation"):
        raise HTTPException(400, detail={"code": "generation_required"})
    return await invoke(manager(request).stop(execution_id, body["generation"]))


@router.post("/{execution_id}/messages")
async def message(execution_id: str, request: Request, body: dict = Body(...)):
    if not body.get("generation") or not body.get("expectedSessionId") or not body.get("messageId"):
        raise HTTPException(400, detail={"code": "native_message_identity_required"})
    return await invoke(manager(request).represented(execution_id, body["generation"], "message", body))


@router.post("/{execution_id}/result")
async def result(execution_id: str, request: Request, body: dict = Body(...)):
    if not body.get("generation") or not body.get("expectedSessionId"):
        raise HTTPException(400, detail={"code": "native_result_identity_required"})
    return await invoke(manager(request).represented(execution_id, body["generation"], "result", body))


@router.websocket("/{execution_id}/terminal")
async def terminal(websocket: WebSocket, execution_id: str, generation: str):
    expected = getattr(websocket.app.state, "auth_token", None)
    offered = list(websocket.scope.get("subprotocols", []))
    supplied = _provided_token(websocket, offered)
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        await websocket.close(code=1008)
        return
    client = None
    tasks = []
    try:
        endpoint = await manager(websocket).endpoint(execution_id, generation)
        client = await SessionHostClient.connect(port=int(endpoint["localPort"]))
        hello = await client.attach(0, nonce=endpoint["host"]["nonce"].encode())
        if hello.child_pid != endpoint["host"]["child_pid"]:
            raise NativeError("identity_mismatch", "Native child identity changed")
        await websocket.accept(subprotocol="native.v1" if "native.v1" in offered else None)

        async def output():
            async for seq, data in client.frames():
                await websocket.send_bytes(pack_frame(seq, data))
            if not client.child_alive:
                code = client.child_exit_code
                if code >= 2 ** 31:
                    code -= 2 ** 32
                await websocket.send_json({"type": "exit", "exitCode": code})

        async def input():
            while True:
                value = await websocket.receive()
                if value["type"] == "websocket.disconnect":
                    return
                if value.get("bytes") is not None:
                    await client.write(value["bytes"])
                elif value.get("text"):
                    control = json.loads(value["text"])
                    if control.get("type") == "resize":
                        await client.resize(int(control["rows"]), int(control["columns"]))
                    elif control.get("type") == "ack":
                        await client.ack(int(control["sequence"]))
                    elif control.get("type") == "detach":
                        return
                    else:
                        raise NativeError("invalid_control", "Unsupported native terminal control", 400)

        tasks = [asyncio.create_task(output()), asyncio.create_task(input())]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except (NativeError, OSError, ValueError, ConnectionError):
        with contextlib.suppress(Exception):
            await websocket.close(code=1013, reason="native execution unavailable; no replacement launched")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if client is not None:
            await client.detach(False)
            await client.close()
        with contextlib.suppress(Exception):
            await websocket.close()
