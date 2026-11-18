"""Contained stdio stand-in; observes load/prompt identity, not native Copilot."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def main() -> None:
    transcript = Path(sys.argv[1])
    native = sys.argv[2]

    def record(data: dict[str, Any]) -> None:
        with transcript.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(data) + "\n")

    record({"boot": os.getpid()})
    configured = len(sys.argv) > 3 and sys.argv[3] in {"configured", "config-failed", "config-timeout"}
    fail_effort = len(sys.argv) > 3 and sys.argv[3] == "config-failed"
    silence_effort = len(sys.argv) > 3 and sys.argv[3] == "config-timeout"
    options = [
        {"id": key, "name": key, "type": "select", "currentValue": default,
         "options": [{"value": value, "name": value} for value in (default, selected)]}
        for key, default, selected in (
            ("model", "default-model", "original-model"), ("reasoning_effort", "low", "high"),
        )
    ]
    for raw in sys.stdin.buffer:
        message = json.loads(raw)
        record(message)
        method = message.get("method")
        if "id" not in message:
            continue
        if method == "initialize":
            result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}
        elif method == "session/load":
            assert message["params"]["sessionId"] == native
            result = {"configOptions": options} if configured else {}
        elif method == "session/set_config_option" and configured:
            assert message["params"]["sessionId"] == native
            if silence_effort and message["params"]["configId"] == "reasoning_effort":
                silence_effort = False
                continue
            if fail_effort and message["params"]["configId"] == "reasoning_effort":
                fail_effort = False
                print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {
                    "code": -32000, "message": "fixture configuration interruption",
                }}), flush=True)
                continue
            for option in options:
                if option["id"] == message["params"]["configId"]:
                    option["currentValue"] = message["params"]["value"]
            result = {"configOptions": options}
        elif method == "session/prompt":
            assert message["params"]["sessionId"] == native
            prompt = "".join(block.get("text", "") for block in message["params"]["prompt"])
            if prompt != "Resume. Host agent-bridge updated.":
                continue
            if len(sys.argv) > 3 and sys.argv[3] == "reject":
                print(
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {"code": -32000, "message": "fixture provider refusal"},
                        }
                    ),
                    flush=True,
                )
                continue
            result = {"stopReason": "end_turn"}
        else:
            raise AssertionError("cold recovery must not create or replay a conversation")
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
