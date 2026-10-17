"""Independent synthetic ACP peer for real HTTP/2/SSE and WS client tests."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from http.cookies import SimpleCookie

from hypercorn.asyncio import serve
from hypercorn.config import Config

NS = "remote-acp/explicit-approvals"
PROFILE = "acp-http-ws-durable-v1alpha1"
connections = {}
sessions = {}
evidence = {
    "prompts": 0, "answers": [], "http_versions": [], "deletes": 0,
    "auth_refused": 0, "auth_ok": 0, "redirect_followed": 0, "ws_cookies": 0,
    "accepted_actions": 0,
    "created_sessions": 0,
}


def rpc(request, result):
    return {"jsonrpc": "2.0", "id": request["id"], "result": result}


def initialize(request, *, supported=True):
    assert request["params"]["protocolVersion"] == 1
    extension = request["params"]["clientCapabilities"]["_meta"][NS]
    assert extension["applicationApprovals"]["explicitAnswers"] is True
    return rpc(request, {
        "protocolVersion": 1,
        "agentCapabilities": {
            "loadSession": True,
            "_meta": {NS: {"profile": PROFILE, "applicationApprovals": {
                "version": 1, "required": True,
            }}} if supported else {},
        },
    })


def affinity_cookie(headers):
    cookies = SimpleCookie()
    cookies.load(headers.get(b"cookie", b"").decode("ascii"))
    value = cookies.get("affinity")
    return value.value if value else None


class Connection:
    def __init__(
        self, ws_send=None, *, supported=True, creation_question=False,
        affinity=None, immediate=False,
    ):
        self.id = uuid.uuid4().hex
        self.affinity = affinity or uuid.uuid4().hex
        self.queues = {None: asyncio.Queue(32)}
        self.pending = {}
        self.retired_callbacks = {}
        self.ws_send = ws_send
        self.supported = supported
        self.creation_question = creation_question
        self.immediate = immediate

    async def emit(self, message, sid=None):
        if self.ws_send is not None:
            await self.ws_send({
                "type": "websocket.send", "text": json.dumps(message),
            })
        else:
            await self.queues[sid].put(message)

    async def handle(self, message, header_sid=None):
        method = message.get("method")
        params = message.get("params") or {}
        sid = params.get("sessionId")
        if method is None:
            if message["id"] in self.retired_callbacks:
                expected_sid = self.retired_callbacks.pop(message["id"])
                assert self.ws_send or header_sid == expected_sid
                return
            pending, expected_sid = self.pending.pop(message["id"])
            assert self.ws_send or header_sid == expected_sid
            answer = message["result"]
            if answer.get("action") == "cancel" or answer.get("outcome", {}).get("outcome") == "cancelled":
                await self.emit(rpc(pending, {"stopReason": "cancelled"}), expected_sid)
                return
            if pending["method"] == "session/new":
                assert answer["_meta"][NS]["applicationApproval"]["decisionId"] == "creation/decision"
                assert answer["action"] == "accept" and answer["content"] == {"choice": "explicit"}
                evidence["accepted_actions"] += 1
                await self.new_session(pending)
                return
            assert answer["_meta"][NS]["applicationApproval"]["decisionId"] == "decision/opaque"
            original = pending["params"]["prompt"][0]["text"]
            if original == "permission":
                assert answer["outcome"]["outcome"] == "selected"
                assert answer["outcome"]["optionId"] in {"allow/opaque", "reject/opaque"}
                evidence["accepted_actions"] += answer["outcome"]["optionId"] == "allow/opaque"
            else:
                assert answer["action"] == "accept"
                assert answer["content"] == {"choice": "explicit"}
                evidence["accepted_actions"] += 1
            evidence["answers"].append({"session": expected_sid, "result": answer})
            await self.finish(pending, expected_sid)
        elif method == "initialize":
            await self.emit(initialize(message, supported=self.supported))
        elif method == "session/new":
            assert not params.get("_meta")
            if self.creation_question:
                self.pending["creation/callback"] = message, None
                await self.emit({
                    "jsonrpc": "2.0", "id": "creation/callback",
                    "method": "elicitation/create", "params": {
                        "requestId": message["id"], "mode": "form", "message": "Create?",
                        "requestedSchema": {"type": "object", "properties": {
                            "choice": {"type": "string"},
                        }, "required": ["choice"]},
                        "_meta": {NS: {"applicationApproval": {
                            "version": 1, "decisionId": "creation/decision",
                        }}},
                    },
                })
            else:
                await self.new_session(message)
        elif method == "session/load":
            assert not params.get("_meta")
            assert sid in sessions
            assert self.affinity == sessions[sid]["affinity"], "Missing original backend affinity"
            assert self.ws_send or header_sid == sid
            for update in sessions[sid]["history"]:
                await self.emit(update)
            self.queues[sid] = asyncio.Queue(32)
            await self.emit(rpc(message, {}))
        elif method == "session/prompt":
            assert self.ws_send or header_sid == sid
            evidence["prompts"] += 1
            text = params["prompt"][0]["text"]
            if text in {"permission", "form", "form-request", "form-request-finish"}:
                self.pending["rpc/opaque"] = (message, sid)
                metadata = {NS: {"applicationApproval": {
                    "version": 1, "decisionId": "decision/opaque",
                }}}
                if text == "permission":
                    callback = {
                        "sessionId": sid,
                        "toolCall": {"toolCallId": "tool/opaque", "title": "Synthetic action"},
                        "options": [
                            {"optionId": "allow/opaque", "kind": "allow_once", "name": "Allow"},
                            {"optionId": "reject/opaque", "kind": "reject_once", "name": "Reject"},
                        ],
                        "_meta": metadata,
                    }
                    name = "session/request_permission"
                else:
                    callback = {
                        "sessionId": sid, "toolCallId": "tool/opaque",
                        "message": "Synthetic decision", "mode": "form",
                        "requestedSchema": {
                            "type": "object", "properties": {
                                "choice": {"type": "string"},
                            }, "required": ["choice"],
                        }, "_meta": metadata,
                    }
                    name = "elicitation/create"
                    if text in {"form-request", "form-request-finish"}:
                        callback.pop("sessionId")
                        callback.pop("toolCallId")
                        callback["requestId"] = message["id"]
                await self.emit({
                    "jsonrpc": "2.0", "id": "rpc/opaque", "method": name,
                    "params": callback,
                }, sid)
            elif text == "malformed-result":
                await self.emit(rpc(message, {"stopReason": "SYNTHETIC-SENSITIVE-MARKER"}), sid)
            elif text == "malformed-notification":
                await self.emit({
                    "jsonrpc": "2.0", "method": "session/update",
                    "params": {"sessionId": sid, "update": {
                        "sessionUpdate": "SYNTHETIC-SENSITIVE-MARKER",
                    }},
                }, sid)
                await self.finish(message, sid)
            elif text == "wait":
                self.pending["cancel"] = message, sid
            elif text == "drop-session-only":
                if self.ws_send:
                    await self.ws_send({"type": "websocket.close", "code": 1000})
                else:
                    await self.queues[sid].put(None)
            elif text == "drop":
                sessions[sid]["history"].append({
                    "jsonrpc": "2.0", "method": "session/update",
                    "params": {"sessionId": sid, "update": {
                        "sessionUpdate": "tool_call", "toolCallId": "historical/background",
                        "title": "Historical completed task", "kind": "other",
                        "status": "completed", "content": [{
                            "type": "content", "content": {
                                "type": "text",
                                "text": "started in background with agent_id: old-worker",
                            },
                        }],
                    }},
                })
                sessions[sid]["history"].append({
                    "jsonrpc": "2.0", "method": "session/update",
                    "params": {"sessionId": sid, "update": {
                        "sessionUpdate": "tool_call", "toolCallId": "historical/tool",
                        "title": "Historical unfinished tool", "kind": "other",
                        "status": "in_progress",
                    }},
                })
                sessions[sid]["history"].append({
                    "jsonrpc": "2.0", "method": "session/update",
                    "params": {"sessionId": sid, "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": "unseen during disconnect"},
                    }},
                })
                if self.ws_send:
                    await self.ws_send({"type": "websocket.close", "code": 1000})
                else:
                    await self.queues[sid].put(None)
                    await self.queues[None].put(None)
            else:
                await self.finish(message, sid)
        elif method == "session/cancel":
            pending = self.pending.pop("cancel", None)
            if pending is None:
                pending = self.pending.pop("rpc/opaque", None)
                if pending is not None:
                    self.retired_callbacks["rpc/opaque"] = pending[1]
            if pending:
                await self.emit(rpc(pending[0], {"stopReason": "cancelled"}), sid)
        elif method and "id" in message:
            await self.emit({
                "jsonrpc": "2.0", "id": message["id"],
                "error": {"code": -32601, "message": "Unsupported synthetic method"},
            }, sid)

    async def new_session(self, request):
        sid = uuid.uuid4().hex
        sessions[sid] = {"history": [], "affinity": self.affinity}
        self.queues[sid] = asyncio.Queue(32)
        evidence["created_sessions"] += 1
        await self.emit(rpc(request, {"sessionId": sid}))
        if self.immediate:
            await self.emit({
                "jsonrpc": "2.0", "method": "session/update",
                "params": {"sessionId": sid, "update": {
                    "sessionUpdate": "agent_message_chunk",
                    "content": {"type": "text", "text": "immediately after new"},
                }},
            }, sid)

    async def finish(self, request, sid):
        update = {
            "jsonrpc": "2.0", "method": "session/update",
            "params": {"sessionId": sid, "update": {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": "synthetic response"},
            }},
        }
        count = 5 if request["params"]["prompt"][0]["text"] == "history" else 1
        for _ in range(count):
            sessions[sid]["history"].append(update)
            await self.emit(update, sid)
        await self.emit(rpc(request, {"stopReason": "end_turn"}), sid)


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            event = await receive()
            if event["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif event["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
    headers = dict(scope["headers"])
    if scope["type"] == "http" and scope["path"] == "/complete-request":
        body = b""
        while True:
            event = await receive()
            body += event.get("body", b"")
            if not event.get("more_body"):
                break
        requested_sid = json.loads(body)["session_id"]
        for conn in connections.values():
            pending = conn.pending.get("rpc/opaque")
            if pending is not None and pending[1] == requested_sid:
                original, sid = conn.pending.pop("rpc/opaque")
                conn.retired_callbacks["rpc/opaque"] = sid
                await conn.emit(rpc(original, {"stopReason": "end_turn"}), sid)
                await respond(send, 200)
                return
        await respond(send, 404)
        return
    if scope["path"] == "/forbidden":
        evidence["redirect_followed"] += 1
    scheme = "Bear" + "er "
    authenticated = headers.get(b"authorization") == (scheme + "synthetic-credential").encode()
    if scope["path"] in {"/acp", "/auth-cookie"}:
        challenge = scope["path"] == "/auth-cookie"
        has_challenge = b"challenge=synthetic" in headers.get(b"cookie", b"")
        if not authenticated or (challenge and not has_challenge):
            evidence["auth_refused"] += 1
            refusal_headers = [
                (b"set-cookie", b"challenge=synthetic; Path=/; Secure; HttpOnly"),
            ] if challenge else []
            if scope["type"] == "websocket":
                await receive()
                await send({
                    "type": "websocket.http.response.start", "status": 401,
                    "headers": refusal_headers,
                })
                await send({"type": "websocket.http.response.body", "body": b""})
            else:
                while True:
                    event = await receive()
                    if not event.get("more_body"):
                        break
                await respond(send, 401, headers=refusal_headers)
            return
        evidence["auth_ok"] += 1
    if scope["type"] == "websocket":
        await receive()
        if scope["path"] == "/redirect":
            await send({"type": "websocket.http.response.start", "status": 307,
                        "headers": [(b"location", b"/forbidden")]})
            await send({"type": "websocket.http.response.body", "body": b""})
            return
        conn = Connection(
            send, supported=scope["path"] != "/unsupported",
            creation_question=scope["path"] == "/creation-question",
            affinity=affinity_cookie(headers),
            immediate=scope["path"] == "/immediate",
        )
        connections[conn.id] = conn
        if affinity_cookie(headers):
            evidence["ws_cookies"] += 1
        await send({
            "type": "websocket.accept",
            "headers": [(b"acp-connection-id", conn.id.encode()),
                        (b"set-cookie", f"affinity={conn.affinity}; Path=/; Secure".encode())],
        })
        await send({"type": "websocket.send", "bytes": b"synthetic-binary-frame"})
        while True:
            event = await receive()
            if event["type"] == "websocket.disconnect":
                return
            await conn.handle(json.loads(event["text"]))
    elif scope["type"] == "http":
        if scope["path"] == "/redirect":
            await respond(send, 307, headers=[(b"location", b"/forbidden")])
            return
        if scope["path"] == "/evidence":
            await respond(send, 200, json.dumps(evidence).encode())
            return
        evidence["http_versions"].append(scope["http_version"])
        method = scope["method"]
        if method == "POST":
            body = bytearray()
            while True:
                event = await receive()
                body.extend(event.get("body", b""))
                if not event.get("more_body"):
                    break
            message = json.loads(body)
            if message.get("method") == "initialize":
                conn = Connection(
                    creation_question=scope["path"] == "/creation-question",
                    affinity=affinity_cookie(headers),
                    immediate=scope["path"] == "/immediate",
                )
                connections[conn.id] = conn
                await respond(send, 200, json.dumps(initialize(
                    message, supported=scope["path"] != "/unsupported",
                )).encode(), [
                    (b"acp-connection-id", conn.id.encode()),
                    (b"set-cookie", f"affinity={conn.affinity}; Path=/; Secure; HttpOnly".encode()),
                ])
            else:
                conn = connections[headers[b"acp-connection-id"].decode()]
                assert affinity_cookie(headers) == conn.affinity
                sid = headers.get(b"acp-session-id", b"").decode() or None
                await conn.handle(message, sid)
                await respond(send, 202)
        elif method == "GET":
            conn = connections[headers[b"acp-connection-id"].decode()]
            sid = headers.get(b"acp-session-id", b"").decode() or None
            if sid not in conn.queues:
                await respond(send, 404)
                return
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"text/event-stream")]})
            await send({"type": "http.response.body", "body": b": ready\n\n",
                        "more_body": True})
            while True:
                message = await conn.queues[sid].get()
                if message is None:
                    break
                await send({"type": "http.response.body",
                            "body": b"data: " + json.dumps(message).encode() + b"\n\n",
                            "more_body": True})
            await send({"type": "http.response.body", "body": b""})
        elif method == "DELETE":
            conn = connections.pop(headers[b"acp-connection-id"].decode())
            for queue in conn.queues.values():
                await queue.put(None)
            evidence["deletes"] += 1
            await respond(send, 202)


async def respond(send, status, body=b"", headers=()):
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), *headers]})
    await send({"type": "http.response.body", "body": body})


if __name__ == "__main__":
    config = Config()
    config.bind = [f"localhost:{int(sys.argv[1])}"]
    config.certfile, config.keyfile = sys.argv[2:4]
    config.alpn_protocols = ["h2", "http/1.1"]
    config.accesslog = None
    config.errorlog = None
    asyncio.run(serve(app, config))
