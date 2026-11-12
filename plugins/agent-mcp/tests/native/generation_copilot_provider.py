"""Bounded loopback model transport for actual native MCP tool consumption."""

from __future__ import annotations

import asyncio
import contextlib
import json


class CannedProvider:
    """Return only the advertised fixture tool and consume its real result."""

    def __init__(self) -> None:
        self.server = None
        self.tasks: set[asyncio.Task] = set()
        self.errors: list[str] = []
        self.calls: list[str] = []
        self.results: dict[str, str] = {}
        self.unknown_nonce = ""
        self.unknown_results: dict[str, str] = {}
        self.nonce = ""
        self.url = ""

    async def start(self) -> None:
        """Bind an ephemeral loopback port without credentials or inference."""
        self.server = await asyncio.start_server(self.handle, "127.0.0.1", 0, limit=65536)
        self.url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/v1"

    async def close(self) -> None:
        """Settle the listener and every owned connection."""
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def completion(self, request: dict) -> tuple[dict, str]:
        """Choose the one synthetic tool call or acknowledge its observed output."""
        assert self.nonce and len(self.calls) < 16
        assert request["model"] == "gpt-4.1"
        messages = request["messages"]
        assert self.nonce in json.dumps(messages)
        marker = "fixture-result:" + self.nonce
        results = [
            json.dumps(message.get("content"))
            for message in messages if message.get("role") == "tool"
            and marker in json.dumps(message.get("content"))
        ]
        if results:
            assert len(results) == 1 and self.nonce in self.calls
            self.results[self.nonce] = results[0]
            return {"content": "fixture-complete:" + self.nonce}, "stop"
        if self.nonce == self.unknown_nonce:
            failures = [
                message for message in messages if message.get("role") == "tool"
                and message.get("tool_call_id") == "call_" + self.nonce
            ]
            if failures:
                assert len(failures) == 1 and self.nonce in self.calls
                self.unknown_results[self.nonce] = json.dumps(failures[0].get("content"))
                return {"content": "fixture-unknown:" + self.nonce}, "stop"
        assert self.nonce not in self.calls, "fixture tool was not consumed; refuse replay"
        names = [
            tool["function"]["name"] for tool in request.get("tools", [])
            if tool.get("type") == "function"
            and tool["function"]["name"].endswith("fixture_0")
        ]
        assert len(names) == 1, names
        self.calls.append(self.nonce)
        return {"content": None, "tool_calls": [{
            "id": "call_" + self.nonce, "type": "function",
            "function": {
                "name": names[0], "arguments": json.dumps({"nonce": self.nonce}),
            },
        }]}, "tool_calls"

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve the limited Chat Completions exchange and retain visible failures."""
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            async with asyncio.timeout(30):
                header = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
                lines = header.split("\r\n")
                assert lines[0] == "POST /v1/chat/completions HTTP/1.1", lines[0]
                fields = dict(line.lower().split(": ", 1) for line in lines[1:] if line)
                assert "authorization" not in fields
                length = int(fields["content-length"])
                assert 0 < length <= 4 * 1024 * 1024
                request = json.loads(await reader.readexactly(length))
                message, finish = self.completion(request)
                base = {
                    "id": "fixture-" + self.nonce, "created": 1, "model": "gpt-4.1",
                }
                if request.get("stream"):
                    delta = {"role": "assistant", **message}
                    if "tool_calls" in delta:
                        delta["tool_calls"][0]["index"] = 0
                    chunks = [
                        {**base, "object": "chat.completion.chunk", "choices": [{
                            "index": 0, "delta": delta, "finish_reason": None,
                        }]},
                        {**base, "object": "chat.completion.chunk", "choices": [{
                            "index": 0, "delta": {}, "finish_reason": finish,
                        }]},
                    ]
                    body = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                    data = (body + "data: [DONE]\n\n").encode()
                    content_type = "text/event-stream"
                else:
                    data = json.dumps({
                        **base, "object": "chat.completion", "choices": [{
                            "index": 0, "message": {"role": "assistant", **message},
                            "finish_reason": finish,
                        }], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }).encode()
                    content_type = "application/json"
                writer.write((
                    "HTTP/1.1 200 OK\r\nConnection: close\r\n"
                    f"Content-Type: {content_type}\r\nContent-Length: {len(data)}\r\n\r\n"
                ).encode() + data)
                await writer.drain()
        except (AssertionError, KeyError, ValueError, OSError, asyncio.IncompleteReadError) as exc:
            self.errors.append(str(exc))
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            self.tasks.discard(task)


async def rpc(process, stderr, identifier: int, method: str, params: dict) -> tuple[dict, str]:
    """Perform one ACP call; unexpected permission requests never gain approval."""
    process.stdin.write((json.dumps({
        "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
    }) + "\n").encode())
    await process.stdin.drain()
    text = ""
    async with asyncio.timeout(30):
        while True:
            line = await process.stdout.readline()
            if not line:
                raise EOFError((await asyncio.wait_for(stderr, 5)).decode("utf-8", "replace"))
            message = json.loads(line)
            assert message.get("method") != "session/request_permission", message
            if message.get("method") == "session/update":
                update = message["params"]["update"]
                if update.get("sessionUpdate") == "agent_message_chunk":
                    text += update.get("content", {}).get("text", "")
            if message.get("id") == identifier and "method" not in message:
                assert "error" not in message, message
                return message["result"], text
