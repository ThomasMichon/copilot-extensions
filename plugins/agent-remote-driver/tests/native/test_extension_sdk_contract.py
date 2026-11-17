"""Model-free bundled-SDK registration over an isolated synthetic parent pipe."""

import asyncio
import json
import os
from pathlib import Path
import shutil
from typing import Any

from agent_procutil import no_window_kwargs
import pytest

pytestmark = pytest.mark.portfolio_tier("T3")
_BUNDLES: list[dict[str, Any] | None] = json.loads(os.environ.get("COPILOT_EXTENSION_SDK_BUNDLES", "[]"))
_SID = "sdk-contract-fixture"


async def _read(reader: asyncio.StreamReader) -> dict[str, Any]:
    headers = {}
    while True:
        line = await reader.readline()
        assert line, "SDK pipe closed before the expected contract observation"
        if line == b"\r\n":
            break
        key, value = line.decode("ascii").strip().split(":", 1)
        headers[key.lower()] = value.strip()
    length = int(headers["content-length"])
    assert 0 < length <= 65536
    result: dict[str, Any] = json.loads(await reader.readexactly(length))
    return result


async def _write(writer: asyncio.StreamWriter, message: dict[str, Any]) -> None:
    body = json.dumps({"jsonrpc": "2.0", **message}).encode()
    writer.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
    await writer.drain()


@pytest.mark.parametrize("bundle", _BUNDLES or [None])
@pytest.mark.asyncio
async def test_real_bundled_sdk_registration_and_callbacks(
    bundle: dict[str, Any] | None, tmp_path: Path,
) -> None:
    if bundle is None:
        pytest.skip("Select existing SDK bundles with COPILOT_EXTENSION_SDK_BUNDLES")
    root = Path(bundle["sdkRoot"])
    assert root.is_absolute() and (root / "extension.js").is_file()
    resolver = root.parent / "preloads" / "extension_sdk_resolver.mjs"
    assert resolver.is_file()
    node = shutil.which("node")
    assert node, "bundled SDK controls require Node"
    tests = Path(__file__).resolve().parents[1]
    helper = tests.parents[2] / "libs" / "copilot-driver-core" / "src" \
        / "agent_remote_driver" / "bridge_extension" / "sdk-compat.mjs"
    environment = {
        key: value for key, value in os.environ.items()
        if (key.startswith("COPILOT_EXTENSIONS_")
            or not key.startswith(("COPILOT_", "AGENT_", "OTEL_")))
        and key not in {"GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN", "SESSION_ID"}
    }
    environment.update(
        SESSION_ID=_SID, COPILOT_SDK_PATH=str(root),
        HOME=str(tmp_path), USERPROFILE=str(tmp_path),
    )
    process = await asyncio.create_subprocess_exec(
        node, str(tests / "fixtures" / "sdk-contract-client.mjs"), str(resolver), str(helper),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env=environment, cwd=tmp_path, **no_window_kwargs(),
    )
    stdin, stdout, stderr = process.stdin, process.stdout, process.stderr
    assert stdin is not None and stdout is not None and stderr is not None
    errors = asyncio.create_task(stderr.read())
    observed: set[str] = set()
    joins = 0
    metadata: str | int | None = None

    async def conversation() -> None:
        nonlocal joins, metadata
        while observed != {"tool", "hook", "permission", "command"} or metadata is None:
            message = await _read(stdout)
            method, params = message.get("method"), message.get("params", {})
            result: dict[str, Any]
            if method == "connect":
                result = {"protocolVersion": bundle["protocolVersion"]}
            elif method == "session.resume":
                joins += 1
                assert joins == 1 and params["sessionId"] == _SID
                modern = bundle["grouped"]
                tools = params["tools"]["definitions"] if modern else params["tools"]
                commands = params["interaction"]["commands"] if modern else params["commands"]
                assert [tool["name"] for tool in tools] == ["fixture_tool"]
                assert [command["name"] for command in commands] == ["fixture-command"]
                assert (params["hooks"]["hasHandlers"] if modern else params["hooks"]) is True
                assert (params["permissions"]["request"] if modern else params["requestPermission"]) is True
                assert not params.get("requestedEnvironmentVariables")
                result = {"sessionId": _SID, "capabilities": {}}
            elif method == "session.metadata.snapshot":
                metadata = message["id"]
                await _write(stdin, {
                    "id": "fixture-hook", "method": "hooks.invoke",
                    "params": {"sessionId": _SID, "hookType": "preToolUse", "input": {}},
                })
                for event_type, data in (
                    ("external_tool.requested", {
                        "requestId": "fixture-tool", "toolCallId": "fixture-call",
                        "toolName": "fixture_tool", "arguments": {"value": "fixture-result"},
                    }),
                    ("permission.requested", {
                        "requestId": "fixture-permission", "permissionRequest": {"kind": "read"},
                    }),
                    ("command.execute", {
                        "requestId": "fixture-command", "commandName": "fixture-command",
                        "command": "/fixture-command fixture-args", "args": "fixture-args",
                    }),
                ):
                    await _write(stdin, {
                        "method": "session.event",
                        "params": {"sessionId": _SID, "event": {
                            "id": data["requestId"], "type": event_type,
                            "timestamp": "2026-01-01T00:00:00Z", "data": data,
                        }},
                    })
                continue
            elif method == "session.tools.handlePendingToolCall":
                assert params["requestId"] == "fixture-tool"
                assert params["result"] == "fixture-result"
                observed.add("tool")
                result = {}
            elif method == "session.permissions.handlePendingPermissionRequest":
                assert params["requestId"] == "fixture-permission"
                assert params["result"]["kind"] == "denied-interactively-by-user"
                observed.add("permission")
                result = {}
            elif method == "session.commands.handlePendingCommand":
                assert params["requestId"] == "fixture-command" and "error" not in params
                observed.add("command")
                result = {}
            elif method is None and message.get("id") == "fixture-hook":
                assert message["result"] == {"output": {"additionalContext": "fixture-hook"}}
                observed.add("hook")
                continue
            else:
                raise AssertionError(f"Unexpected synthetic-parent request: {method}")
            await _write(stdin, {"id": message["id"], "result": result})
        await _write(stdin, {"id": metadata, "result": {"sessionId": _SID}})
        assert await process.wait() == 0

    try:
        await asyncio.wait_for(conversation(), 20)
    except BaseException as error:
        if process.returncode is None:
            process.kill()
        await process.wait()
        detail = (await errors).decode(errors="replace")[-6000:]
        raise AssertionError(f"Bundled SDK contract failed: {error}\n{detail}") from error
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
        await errors
