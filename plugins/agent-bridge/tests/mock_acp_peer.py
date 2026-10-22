"""Test-only stdio peer; never qualifies as native Copilot.

Reuses the request loop in test_acp_activation_native.acp_child_main and the
thought/message update shapes in test_acp_idle_continuation. No model or tools.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import uuid


def main():
    parser = argparse.ArgumentParser()
    for flag in ("--acp", "--stdio", "--no-auto-update"):
        parser.add_argument(flag, action="store_true", required=True)
    _args, trailing = parser.parse_known_args()
    transcript = Path(os.environ["MOCK_ACP_TRANSCRIPT"])
    session_id = "mock-acp-" + uuid.uuid4().hex
    prompts = 0

    def record(kind, **data):
        with transcript.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"kind": kind, **data}) + "\n")

    def emit(message):
        record("outbound", message=message)
        print(json.dumps(message), flush=True)

    record("boot", pid=os.getpid(), argv=sys.argv, trailing=trailing,
           native_source="UNQUALIFIED", session_id=session_id)
    for raw in sys.stdin.buffer:
        message = json.loads(raw)
        record("inbound", message=message)
        method = message.get("method")
        if method == "session/cancel":
            continue
        if method == "initialize":
            result = {"protocolVersion": 1, "agentCapabilities": {}}
        elif method == "session/new":
            result = {"sessionId": session_id}
        elif method == "session/prompt":
            assert message["params"]["sessionId"] == session_id
            prompts += 1
            for update, text in (
                ("agent_thought_chunk", "mock-thought"),
                ("agent_message_chunk", f"mock-result-{prompts}"),
            ):
                emit({"jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": session_id, "update": {
                        "sessionUpdate": update,
                        "content": {"type": "text", "text": text},
                    },
                }})
            result = {"stopReason": "end_turn"}
        else:
            emit({"jsonrpc": "2.0", "id": message["id"], "error": {
                "code": -32601, "message": "unsupported test-peer method",
            }})
            continue
        emit({"jsonrpc": "2.0", "id": message["id"], "result": result})
    record("eof", prompts=prompts)


if __name__ == "__main__":
    main()
