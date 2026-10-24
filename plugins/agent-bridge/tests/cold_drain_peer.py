"""Contained stdio stand-in; observes load/prompt identity, not native Copilot."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> None:
    transcript = Path(sys.argv[1])
    native = sys.argv[2]

    def record(data: dict) -> None:
        with transcript.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(data) + "\n")

    record({"boot": os.getpid()})
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
            result = {}
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
