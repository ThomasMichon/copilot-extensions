"""Synthetic local ACP peer; independent original launch and turn readback."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys


def main() -> None:
    database, audit, delivery, mode = sys.argv[1:]
    assert "AGENT_BRIDGE_ORIGINAL_ACQUISITION" not in os.environ
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        if method == "initialize":
            result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}
        elif method in ("session/new", "session/load", "session/prompt"):
            with sqlite3.connect(Path(database).absolute().as_uri() + "?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM acquisition_attempts WHERE delivery_id=?", (delivery,),
                ).fetchone()
                assert row is not None and row["launch_started_at"] is not None
                handoff = json.loads(row["launch_handoff_json"])
                assert handoff["admission"] == row["runtime_custody_json"]
                if method == "session/prompt":
                    turn = conn.execute(
                        "SELECT * FROM turns WHERE acquisition_delivery_id=?", (delivery,),
                    ).fetchone()
                    assert turn is not None and turn["native_handoff_started_at"] is not None
                    native_handoff = json.loads(turn["native_handoff_json"])
                    assert native_handoff["consumer"]["pid"] == os.getpid()
                    assert turn["runtime_custody_json"] == row["runtime_custody_json"]
                    assert turn["prompt"] == message["params"]["prompt"][0]["text"]
            with Path(audit).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"method": method, "handoff": handoff["id"], "pid": os.getpid()}) + "\n")
            if mode == "drop-reply" and method != "session/prompt":
                raise SystemExit(17)
            if method == "session/new":
                assert handoff["consumer"]["native_session_id"] is None
                result = {"sessionId": "synthetic-created"}
            elif method == "session/load":
                assert handoff["consumer"]["native_session_id"] == message["params"]["sessionId"]
                result = {}
            else:
                result = {"stopReason": "end_turn"}
        else:
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
