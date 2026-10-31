"""Synthetic stdio ACP peer; never launches a model or touches an installation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys


def main() -> None:
    database, audit, mode = map(str, sys.argv[1:])
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get("method")
        if method == "initialize":
            result = {"protocolVersion": 1, "agentCapabilities": {}}
        elif method == "session/new":
            result = {
                "sessionId": "synthetic-native",
                "configOptions": [
                    {"id": "model", "name": "Model", "type": "select",
                     "currentValue": "fixture-model",
                     "options": [{"value": "fixture-model", "name": "Fixture"}]},
                    {"id": "reasoning_effort", "name": "Effort", "type": "select",
                     "currentValue": "high", "options": [{"value": "high", "name": "High"}]},
                ],
            }
        elif method == "session/prompt":
            params = message["params"]
            with sqlite3.connect(Path(database).absolute().as_uri() + "?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM turns WHERE session_id='synthetic' AND turn_index=0").fetchone()
            assert row is not None and row["native_handoff_started_at"] is not None
            handoff = json.loads(row["native_handoff_json"])
            assert handoff["consumer"]["pid"] == os.getpid()
            assert handoff["consumer"]["native_session_id"] == params["sessionId"] == "synthetic-native"
            assert row["prompt"] == params["prompt"][0]["text"] == "original synthetic prompt"
            with Path(audit).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"pid": os.getpid(), "handoff": handoff["id"]}) + "\n")
            if mode == "drop-reply":
                raise SystemExit(17)
            result = {"stopReason": "end_turn"}
        else:
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)


if __name__ == "__main__":
    main()
