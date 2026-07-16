"""End-to-end: run the real ``agent-mcp bridge`` with a ``type: cli`` config.

Spawns the bridge as a subprocess whose "upstream" is not an MCP server at all --
it is a set of tool sidecars answered locally by the cli transport. Exercises
initialize + tools/list (synthesized from sidecars) + tools/call (argv bind ->
subprocess) + the isError path, plus scope gating and the legacy tools filter.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

import pytest
import yaml

# A trivial "CLI" invoked by the sidecar: echoes its argv as JSON, and exits
# non-zero when the first arg is "boom" (to exercise the isError path).
ECHO = textwrap.dedent(
    """
    import sys, json
    args = sys.argv[1:]
    if args and args[0] == "boom":
        sys.stderr.write("kaboom\\n"); sys.exit(3)
    print(json.dumps(args))
    """
)


def _sidecar(name: str, scope: str | None = None) -> str:
    mcp = {
        "name": name,
        "description": f"demo tool {name}",
        "inputSchema": {"type": "object",
                        "properties": {"q": {"type": "string"}},
                        "required": ["q"]},
        "invoke": {"command": sys.executable,
                   "args": ["-c", ECHO, "{q}"]},
    }
    if scope is not None:
        mcp["scope"] = scope
    return "---\n" + yaml.safe_dump({"mcp": mcp}) + "---\n"


def _run_bridge(cfg_path, lines):
    proc = subprocess.run(
        [sys.executable, "-m", "agent_mcp", "--log-level", "error",
         "bridge", "--config", str(cfg_path)],
        input="\n".join(lines) + "\n",
        capture_output=True, text=True, timeout=60,
    )
    out = [json.loads(x) for x in proc.stdout.splitlines() if x.strip()]
    return out, proc


@pytest.fixture()
def cli_bridge(tmp_path):
    (tmp_path / "search.md").write_text(_sidecar("do_search", scope="shared"),
                                        encoding="utf-8")
    (tmp_path / "secret.md").write_text(_sidecar("host_only", scope="other-host"),
                                        encoding="utf-8")
    cfg = tmp_path / "bridge.yaml"
    cfg.write_text(json.dumps({
        "server": {"type": "cli",
                   "tools_from": ["search.md", "secret.md"],
                   "scopes": ["shared", "lambda-core"]},
    }), encoding="utf-8")
    return cfg


def test_cli_bridge_end_to_end(cli_bridge):
    out, proc = _run_bridge(cli_bridge, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "do_search", "arguments": {"q": "bears"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "do_search", "arguments": {"q": "boom"}}}),
        json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {"name": "host_only", "arguments": {"q": "x"}}}),
    ])
    by_id = {m.get("id"): m for m in out if "id" in m}

    # initialize advertises tool capability.
    assert by_id[1]["result"]["capabilities"]["tools"] is not None, proc.stderr

    # tools/list: only the in-scope tool is advertised (host_only is gated out).
    names = [t["name"] for t in by_id[2]["result"]["tools"]]
    assert names == ["do_search"], proc.stderr

    # tools/call binds argv and runs the CLI; stdout comes back as text content.
    echoed = json.loads(by_id[3]["result"]["content"][0]["text"])
    assert echoed == ["bears"]
    assert by_id[3]["result"]["isError"] is False

    # non-zero exit -> isError with the stderr tail, not a hang or a crash.
    assert by_id[4]["result"]["isError"] is True
    assert "kaboom" in by_id[4]["result"]["content"][0]["text"]

    # an out-of-scope tool is not runnable either (belt and suspenders).
    assert by_id[5]["result"]["isError"] is True
    assert "unknown tool" in by_id[5]["result"]["content"][0]["text"]


def test_cli_bridge_tools_filter_composes(tmp_path):
    """The legacy ``tools:`` allow/deny filter still applies over the cli list."""
    (tmp_path / "a.md").write_text(_sidecar("keep_me"), encoding="utf-8")
    (tmp_path / "b.md").write_text(_sidecar("drop_me"), encoding="utf-8")
    cfg = tmp_path / "bridge.yaml"
    cfg.write_text(json.dumps({
        "server": {"type": "cli", "tools_from": ["a.md", "b.md"]},
        "tools": {"allow": ["keep_*"]},
    }), encoding="utf-8")
    out, proc = _run_bridge(cfg, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}),
    ])
    by_id = {m.get("id"): m for m in out if "id" in m}
    names = [t["name"] for t in by_id[1]["result"]["tools"]]
    assert names == ["keep_me"], proc.stderr


# --- auth injection into the spawned tool's environment ----------------------

ENV_ECHO = textwrap.dedent(
    """
    import os, sys
    sys.stdout.write(os.environ.get("MY_TOKEN", "<unset>"))
    """
)


def test_cli_bridge_injects_auth_into_tool_env(tmp_path):
    """A cli bridge's auth injector reaches the spawned tool's environment.

    This is how a cli bridge self-sources a credential (e.g. a vault-fetched
    token) and hands it to the tool, instead of depending on the session env.
    """
    mcp = {
        "name": "whoami",
        "description": "echo the injected token",
        "inputSchema": {"type": "object", "properties": {}},
        "invoke": {"command": sys.executable, "args": ["-c", ENV_ECHO]},
    }
    (tmp_path / "whoami.md").write_text(
        "---\n" + yaml.safe_dump({"mcp": mcp}) + "---\n", encoding="utf-8")
    cfg = tmp_path / "bridge.yaml"
    cfg.write_text(json.dumps({
        "server": {"type": "cli", "tools_from": ["whoami.md"]},
        # static-value injector -> MY_TOKEN in the child env (no external command)
        "auth": {"kind": "static", "value": "sekret-123", "target_env": "MY_TOKEN"},
    }), encoding="utf-8")

    out, proc = _run_bridge(cfg, [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "whoami", "arguments": {}}}),
    ])
    by_id = {m.get("id"): m for m in out if "id" in m}
    assert by_id[1]["result"]["isError"] is False, proc.stderr
    assert by_id[1]["result"]["content"][0]["text"] == "sekret-123", proc.stderr
