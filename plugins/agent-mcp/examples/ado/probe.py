"""Drive an agent-mcp bridge config through the MCP handshake and report metrics.

Usage: python probe.py <bridge.yaml> [request_spec ...]

Each request_spec is NAME or NAME:tool:json_args for a tools/call. NAME is just
a label. Prints, per response: byte size + (for tools/list) tool count + names.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

PYTHON = sys.executable


def main() -> int:
    cfg = sys.argv[1]
    extra = sys.argv[2:]
    proc = subprocess.Popen(
        [PYTHON, "-m", "agent_mcp", "--log-level", "error", "bridge", "--config", cfg],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    responses: dict = {}
    lock = threading.Lock()

    def reader():
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid = msg.get("id")
            if mid is not None:
                with lock:
                    responses[mid] = (msg, len(line))

    threading.Thread(target=reader, daemon=True).start()

    def send(obj):
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def wait(mid, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            with lock:
                if mid in responses:
                    return responses[mid]
            time.sleep(0.05)
        return None, 0

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                     "clientInfo": {"name": "probe", "version": "1"}}})
    init, _ = wait(1)
    if init is None:
        sys.stderr.write("ERROR: no initialize response\n")
        sys.stderr.write(proc.stderr.read() or "")
        return 1
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    listed, size = wait(2)
    tools = (listed or {}).get("result", {}).get("tools", []) if listed else []
    names = [t.get("name") for t in tools]
    print(f"tools/list: {len(tools)} tools, {size} bytes")
    print("  names: " + ", ".join(names[:60]))

    rid = 3
    for spec in extra:
        if spec.startswith("schema="):
            want = spec[len("schema="):]
            for t in tools:
                if t.get("name") == want:
                    print(f"schema {want}: " + json.dumps(t.get("inputSchema", {}))[:900])
            continue
        if ":" in spec:
            label, tool, args_json = spec.split(":", 2)
            args = json.loads(args_json)
        else:
            label, tool, args = spec, spec, {}
        send({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
              "params": {"name": tool, "arguments": args}})
        resp, size = wait(rid, timeout=90)
        ok = resp is not None and "result" in resp
        is_err = bool(resp and resp.get("result", {}).get("isError"))
        print(f"call {label} ({tool}): {'OK' if ok and not is_err else 'ERR'}, {size} bytes")
        if resp is not None:
            text = ""
            for block in resp.get("result", {}).get("content", []) or []:
                if block.get("type") == "text":
                    text = block.get("text", "")
                    break
            print("    " + text[:400].replace("\n", " "))
        rid += 1

    proc.stdin.close()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
