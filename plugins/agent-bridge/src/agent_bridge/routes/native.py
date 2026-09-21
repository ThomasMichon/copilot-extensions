"""Authenticated native lifecycle and terminal presentation; never ACP."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json

from fastapi import APIRouter, Body, HTTPException, Request, WebSocket

from ..native_manager import CAPABILITY
from ..native_store import NativeError
from ..session_host.client import SessionHostClient, TerminalOwnershipError
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
    from ..native_capabilities import capabilities as supported
    return {"capability": CAPABILITY, "version": 1, "mode": "native",
            "hostResources": "native-host-resources-v1",
            "capabilities": supported(),
            "providers": {
                namespace: bool(manager(request).provider_command(namespace))
                for namespace in ("codespace", "container")
            },
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
    return await invoke(
        manager(request).stop(execution_id, body["generation"], force=bool(body.get("force"))),
    )


@router.post("/{execution_id}/messages")
async def message(execution_id: str, request: Request, body: dict = Body(...)):
    if not body.get("generation") or not body.get("expectedSessionId") or not body.get("messageId"):
        raise HTTPException(400, detail={"code": "native_message_identity_required"})
    return await invoke(manager(request).represented(execution_id, body["generation"], "message", body))


@router.post("/{execution_id}/result")
async def result(execution_id: str, request: Request, body: dict = Body(...)):
    if not body.get("generation") or not body.get("expectedSessionId"):
        raise HTTPException(400, detail={"code": "native_result_identity_required"})
    value = await invoke(manager(request).represented(execution_id, body["generation"], "result", body))
    return {**value, "nativeExecution": {
        "executionId": execution_id, "generation": body["generation"], "sessionId": body["expectedSessionId"],
    }}


@router.websocket("/{execution_id}/terminal")
async def terminal(
    websocket: WebSocket, execution_id: str, generation: str,
    role: str = "writer", takeover: bool = False, after: int = 0,
):
    expected = getattr(websocket.app.state, "auth_token", None)
    offered = list(websocket.scope.get("subprotocols", []))
    supplied = _provided_token(websocket, offered)
    # Single-user localhost tool: trust a loopback Host, no token needed.
    from ..auth import request_is_trusted_local
    if not request_is_trusted_local(websocket.headers) and (
        not expected or not supplied or not hmac.compare_digest(supplied, expected)
    ):
        await websocket.close(code=1008)
        return
    client = None
    tasks = []
    try:
        if role not in {"writer", "observer"} or not 0 <= after < 2 ** 64 or (role == "observer" and takeover):
            raise NativeError("invalid_control", "Invalid terminal attachment options", 400)
        endpoint = await manager(websocket).endpoint(execution_id, generation)
        client = await SessionHostClient.connect(port=int(endpoint["localPort"]))
        await websocket.accept(subprotocol="native.v1" if "native.v1" in offered else None)
        hello = await client.attach(
            after, nonce=endpoint["host"]["nonce"].encode(), observer=role == "observer", takeover=takeover, terminal=True,
        )
        if hello.child_pid != endpoint["host"]["child_pid"]:
            raise NativeError("identity_mismatch", "Native child identity changed")
        await websocket.send_json({
            "type": "attached", "role": role, "executionId": execution_id, "generation": generation,
            "minSequence": hello.min_seq, "maxSequence": hello.max_seq,
        })

        async def output():
            expected_sequence = after + 1
            async for seq, data in client.frames():
                if seq != expected_sequence:
                    await websocket.send_json({"type": "gap", "after": expected_sequence - 1, "nextSequence": seq})
                await websocket.send_bytes(pack_frame(seq, data))
                expected_sequence = seq + 1
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
                    if role == "observer":
                        raise TerminalOwnershipError("read_only")
                    await client.write(value["bytes"])
                elif value.get("text"):
                    control = json.loads(value["text"])
                    if control.get("type") == "resize":
                        if role == "observer":
                            raise TerminalOwnershipError("read_only")
                        await client.resize(int(control["rows"]), int(control["columns"]))
                    elif control.get("type") == "ack":
                        await client.ack(int(control["sequence"]))
                    elif control.get("type") == "detach":
                        return
                    else:
                        raise NativeError("invalid_control", "Unsupported native terminal control", 400)

        tasks = [asyncio.create_task(output()), asyncio.create_task(input())]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except TerminalOwnershipError as exc:
        with contextlib.suppress(Exception):
            await websocket.close(code={"writer_busy": 4409, "writer_revoked": 4410}.get(exc.code, 4403), reason=exc.code)
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
