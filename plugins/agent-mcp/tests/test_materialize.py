"""Tests for MCP->CLI materialization: rendering + the on-disk stub farm."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pytest

from agent_mcp.__main__ import main
from agent_mcp.config import parse_config
from agent_mcp.materialize import (
    DISPATCHER_NAME,
    MaterializedTool,
    _artifact_label,
    _source_digest_key,
    _wait_for_source_digest_key,
    bridge_source_digest,
    build_manifest,
    plan_tools,
    render_index,
    render_sidecar,
    sanitize_stub,
    server_name_for,
    write_farm,
)

from .test_client import MCP_CHILD

TOOLS = [
    {"name": "create_issue", "description": "Open an issue.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}},
                     "required": ["title"]}},
    {"name": "list_issues", "description": "List issues.\nWith detail.",
     "inputSchema": {"type": "object", "properties": {}}},
]
SOURCE_DIGEST_KEY = b"k" * 32


def test_sanitize_stub():
    assert sanitize_stub("create_issue") == "create_issue"
    assert sanitize_stub("weird name!!") == "weird-name"
    assert sanitize_stub("--dash--") == "dash"


def test_plan_tools_collision_suffix():
    tools = [{"name": "a b"}, {"name": "a/b"}]  # both sanitize to "a-b"
    plan = plan_tools(tools)
    stubs = [mt.stub for mt in plan]
    assert stubs == ["a-b", "a-b-2"]


def test_plan_tools_skips_nameless():
    plan = plan_tools([{"description": "no name"}, {"name": "ok"}])
    assert [mt.stub for mt in plan] == ["ok"]


def test_render_sidecar_plates_schema():
    mt = MaterializedTool("create_issue", "create_issue", TOOLS[0])
    doc = render_sidecar(mt, server="gitea", bridge_ref="/x/gitea.yaml")
    assert "# create_issue" in doc
    assert "Open an issue." in doc
    # Raw inputSchema is plated verbatim.
    assert '"title"' in doc
    assert '"required"' in doc
    # No flag synthesis -- documents the raw arguments form.
    assert "--request-file" in doc
    assert "raw MCP `arguments`" in doc.lower() or "raw mcp `arguments`" in doc.lower()
    # TS signature rendered.
    assert "interface Tool" in doc


def test_render_sidecar_structured_output_note():
    tool = {"name": "s", "description": "d",
            "inputSchema": {"type": "object"},
            "outputSchema": {"type": "object", "properties": {"ok": {"type": "boolean"}}}}
    doc = render_sidecar(MaterializedTool("s", "s", tool), server="x", bridge_ref="b")
    assert "structured output schema" in doc
    assert '"ok"' in doc


def test_render_index_table():
    plan = plan_tools(TOOLS)
    idx = render_index("gitea", plan, bridge_ref="/x/gitea.yaml")
    assert "| `create_issue` | `create_issue` |" in idx
    # Newline in a description is collapsed to the first line.
    assert "With detail." not in idx
    assert "List issues." in idx


def test_build_manifest():
    plan = plan_tools(TOOLS)
    m = build_manifest("gitea", plan, bridge_ref="/x/gitea.yaml", version="9.9")
    assert m["server"] == "gitea"
    assert m["bridge"] == "/x/gitea.yaml"
    assert m["tools"]["create_issue"] == {"tool": "create_issue"}
    assert "bridge_source_digest" not in m

    with_digest = build_manifest(
        "gitea",
        plan,
        bridge_ref="/x/gitea.yaml",
        version="9.9",
        source_digest="abc123",
    )
    assert with_digest["bridge_source_digest"] == "abc123"


def test_bridge_source_digest_tracks_effective_config() -> None:
    first = parse_config(
        {"server": {"type": "http", "url": "https://api.example.com/mcp"}}
    )
    second = parse_config(
        {"server": {"type": "http", "url": "https://api.example.com/other"}}
    )
    assert bridge_source_digest(
        first, key=SOURCE_DIGEST_KEY
    ) != bridge_source_digest(second, key=SOURCE_DIGEST_KEY)


def test_bridge_source_digest_tracks_cli_sidecar_and_helper(
    tmp_path: Path,
) -> None:
    helpers = tmp_path / "helpers"
    helpers.mkdir()
    helper = helpers / "tool.py"
    helper.write_text("print('one')\n", encoding="utf-8")
    sidecar = tmp_path / "tool.md"
    sidecar.write_text(
        """---
mcp:
  name: example
  invoke:
    command: helpers/tool.py
---
""",
        encoding="utf-8",
    )
    config = tmp_path / "bridge.yaml"
    config.write_text(
        "server:\n  type: cli\n  tools_from: [tool.md]\n",
        encoding="utf-8",
    )
    cfg = parse_config(
        {"server": {"type": "cli", "tools_from": ["tool.md"]}},
        source_path=config,
    )
    original = bridge_source_digest(cfg, key=SOURCE_DIGEST_KEY)
    sidecar.write_text(
        sidecar.read_text(encoding="utf-8") + "\nChanged docs.\n",
        encoding="utf-8",
    )
    assert bridge_source_digest(cfg, key=SOURCE_DIGEST_KEY) != original

    updated = bridge_source_digest(cfg, key=SOURCE_DIGEST_KEY)
    helper.write_text("print('two')\n", encoding="utf-8")
    assert bridge_source_digest(cfg, key=SOURCE_DIGEST_KEY) != updated


@pytest.mark.parametrize("command_kind", ["bare", "absolute"])
def test_bridge_source_digest_does_not_hash_path_lookup_or_absolute_binary(
    tmp_path: Path,
    command_kind: str,
) -> None:
    helper = tmp_path / "tool.py"
    helper.write_text("print('one')\n", encoding="utf-8")
    command = "tool.py" if command_kind == "bare" else str(helper)
    sidecar = tmp_path / "tool.md"
    sidecar.write_text(
        f"""---
mcp:
  name: example
  invoke:
    command: {command}
---
""",
        encoding="utf-8",
    )
    config = tmp_path / "bridge.yaml"
    config.write_text(
        "server:\n  type: cli\n  tools_from: [tool.md]\n",
        encoding="utf-8",
    )
    cfg = parse_config(
        {"server": {"type": "cli", "tools_from": ["tool.md"]}},
        source_path=config,
    )

    original = bridge_source_digest(cfg, key=SOURCE_DIGEST_KEY)
    helper.write_text("print('two')\n", encoding="utf-8")
    assert bridge_source_digest(cfg, key=SOURCE_DIGEST_KEY) == original


@pytest.mark.skipif(os.name == "nt", reason="symlink semantics are POSIX-specific")
def test_bridge_source_digest_resolves_helper_from_declared_symlink(
    tmp_path: Path,
) -> None:
    declarations = tmp_path / "declared"
    targets = tmp_path / "targets"
    declarations.mkdir()
    targets.mkdir()
    (declarations / "helpers").mkdir()
    (targets / "helpers").mkdir()
    executed_helper = declarations / "helpers" / "helper.py"
    executed_helper.write_text("print('executed-one')\n", encoding="utf-8")
    target_helper = targets / "helpers" / "helper.py"
    target_helper.write_text("print('not-executed-one')\n", encoding="utf-8")
    target_sidecar = targets / "tool.md"
    target_sidecar.write_text(
        """---
mcp:
  name: example
  invoke:
    command: helpers/helper.py
---
""",
        encoding="utf-8",
    )
    (declarations / "tool.md").symlink_to(target_sidecar)
    config = tmp_path / "bridge.yaml"
    config.write_text(
        "server:\n  type: cli\n  tools_from: [declared/tool.md]\n",
        encoding="utf-8",
    )
    cfg = parse_config(
        {"server": {"type": "cli", "tools_from": ["declared/tool.md"]}},
        source_path=config,
    )

    original = bridge_source_digest(cfg, key=SOURCE_DIGEST_KEY)
    executed_helper.write_text("print('executed-two')\n", encoding="utf-8")
    assert bridge_source_digest(cfg, key=SOURCE_DIGEST_KEY) != original

    updated = bridge_source_digest(cfg, key=SOURCE_DIGEST_KEY)
    target_helper.write_text("print('not-executed-two')\n", encoding="utf-8")
    assert bridge_source_digest(cfg, key=SOURCE_DIGEST_KEY) == updated


def test_bridge_source_digest_is_keyed() -> None:
    cfg = parse_config(
        {
            "server": {"type": "http", "url": "https://api.example.com/mcp"},
            "auth": {"kind": "static", "value": "guessable-secret"},
        }
    )
    assert bridge_source_digest(cfg, key=b"a" * 32) != bridge_source_digest(
        cfg, key=b"b" * 32
    )


def test_source_digest_key_is_private_and_stable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    first = _source_digest_key()
    second = _source_digest_key()
    path = tmp_path / "source-digest.key"

    assert len(first) == 32
    assert second == first
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_source_digest_key_waits_for_concurrent_writer() -> None:
    class RacingPath:
        def __init__(self) -> None:
            self.reads = 0

        def read_bytes(self) -> bytes:
            self.reads += 1
            return b"short" if self.reads == 1 else b"k" * 32

        def __str__(self) -> str:
            return "<key>"

    path = RacingPath()
    assert _wait_for_source_digest_key(
        path,  # type: ignore[arg-type]
        attempts=2,
        sleeper=lambda _seconds: None,
    ) == b"k" * 32


def test_artifact_label_handles_external_absolute_path() -> None:
    assert _artifact_label(
        Path("/external/tool.md"),
        Path("/bridge"),
    ) == "absolute:/external/tool.md"


def test_server_name_for():
    cfg = parse_config({"server": {"type": "http", "url": "https://api.example.com/mcp"}})
    assert server_name_for(cfg) == "api.example.com"
    assert server_name_for(cfg, "custom") == "custom"


def test_server_name_for_npm_uses_package():
    # In npm mode the namespace is the package (e.g. "gitea-mcp"), not the runner.
    cfg = parse_config({"server": {"type": "stdio", "npm": "gitea-mcp"}})
    assert server_name_for(cfg) == "gitea-mcp"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink farm")
def test_write_farm_posix(tmp_path):
    plan = plan_tools(TOOLS)
    server_dir = tmp_path / "gitea"
    write_farm(server_dir, plan, server="gitea", bridge_ref="/x/g.yaml", version="1.0")

    dispatcher = server_dir / "bin" / DISPATCHER_NAME
    assert dispatcher.is_file()
    assert os.access(dispatcher, os.X_OK)

    link = server_dir / "bin" / "create_issue"
    assert link.is_symlink()
    assert os.readlink(link) == DISPATCHER_NAME

    assert (server_dir / "doc" / "create_issue.md").is_file()
    manifest = json.loads((server_dir / "manifest.json").read_text())
    assert manifest["tools"]["list_issues"] == {"tool": "list_issues"}
    assert (server_dir / "index.md").is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink farm")
def test_write_farm_atomic_rebuild(tmp_path):
    server_dir = tmp_path / "gitea"
    write_farm(server_dir, plan_tools(TOOLS), server="gitea",
               bridge_ref="b", version="1.0")
    # Re-materialize with a smaller catalog: stale stubs must be gone.
    write_farm(server_dir, plan_tools([TOOLS[0]]), server="gitea",
               bridge_ref="b", version="1.0")
    assert (server_dir / "bin" / "create_issue").exists()
    assert not (server_dir / "bin" / "list_issues").exists()
    assert not (server_dir / "doc" / "list_issues.md").exists()


def test_write_farm_windows_shims(tmp_path):
    plan = plan_tools(TOOLS)
    server_dir = tmp_path / "gitea"
    write_farm(server_dir, plan, server="gitea", bridge_ref="b", version="1.0",
               windows=True)
    bin_dir = server_dir / "bin"
    assert (bin_dir / "create_issue.ps1").is_file()
    assert (bin_dir / "create_issue.cmd").is_file()
    # No POSIX dispatcher/symlinks in the Windows farm.
    assert not (bin_dir / DISPATCHER_NAME).exists()
    assert not (bin_dir / "create_issue").is_symlink()
    ps1 = (bin_dir / "create_issue.ps1").read_text()
    assert "#Requires -Version 7.0" in ps1
    assert "agent-mcp call" in ps1


def _write_cfg(tmp_path):
    data = {
        "server": {"type": "stdio", "command": [sys.executable, "-c", MCP_CHILD]},
        "auth": {"kind": "none"},
    }
    p = tmp_path / "fixture.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink farm")
def test_materialize_verb_then_stub_call(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    dest = tmp_path / "materialized"
    rc = main(["materialize", str(cfg), "--server-name", "fix", "--dest", str(dest)])
    assert rc == 0
    server_dir = dest / "fix"
    assert (server_dir / "bin" / "greet").is_symlink()

    manifest = server_dir / "manifest.json"
    manifest_data = json.loads(manifest.read_text())
    assert Path(manifest_data["bridge"]).is_absolute()
    assert Path(manifest_data["bridge"]) == cfg.resolve()
    assert manifest_data["bridge_source_digest"]
    capsys.readouterr()  # drain
    rc = main(["call", "--manifest", str(manifest), "--stub", "greet",
               '{"name": "materialized"}'])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "hello materialized"


def test_source_digest_verb_matches_materialized_manifest(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path / "home"))
    cfg = _write_cfg(tmp_path)
    dest = tmp_path / "materialized"
    assert main(
        ["materialize", str(cfg), "--server-name", "fix", "--dest", str(dest)]
    ) == 0
    manifest = json.loads((dest / "fix" / "manifest.json").read_text())
    capsys.readouterr()

    assert main(["source-digest", str(cfg)]) == 0
    assert capsys.readouterr().out.strip() == manifest["bridge_source_digest"]


class _ServeDaemonThread:
    """Run a real ``Server`` on its own event loop in a background thread.

    ``main()`` (the synchronous CLI entry point under test) owns its *own*
    ``asyncio.run()`` call internally, so it cannot run on the same thread as
    a live ``async def`` server loop -- nesting ``asyncio.run()`` inside a
    running loop raises. Isolating the daemon on its own thread + loop lets
    the test call the real, synchronous ``main()`` exactly as a caller would,
    while a warm daemon is genuinely reachable over the socket.
    """

    def __init__(self, sock: Path) -> None:
        self.sock = sock
        self.server = None
        self._reachable = False
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        import asyncio

        from agent_mcp.serve import Server, serve_socket_if_available

        async def _go():
            server = Server(self.sock)
            self.server = server
            task = asyncio.ensure_future(server.serve_forever())
            for _ in range(50):
                if serve_socket_if_available(str(self.sock)):
                    self._reachable = True
                    break
                await asyncio.sleep(0.05)
            # Always unblock start()'s wait, reachable or not -- a caller
            # that only checked the Event (without also checking
            # _reachable) would otherwise hang forever on a daemon that
            # never bound.
            self._ready.set()
            await task

        asyncio.run(_go())

    def start(self) -> None:
        self._thread.start()
        woke = self._ready.wait(timeout=5)
        assert woke and self._reachable, "serve daemon never became reachable"

    def shutdown(self) -> None:
        import asyncio

        from agent_mcp.serve import request_via_socket
        # Best-effort: the daemon may already be gone (crashed, evicted
        # itself on idle, or never bound in the first place) -- a failed
        # shutdown request must not skip joining the thread and leave it
        # running past the test.
        try:
            asyncio.run(request_via_socket(self.sock, {"op": "shutdown"}))
        except OSError:
            pass
        self._thread.join(timeout=5)
        assert not self._thread.is_alive(), "serve daemon thread did not stop"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink farm")
def test_materialize_consults_warm_serve_daemon_instead_of_spawning_cold(
    tmp_path, monkeypatch,
):
    """Regression test for the reproduced hang (a fresh ``materialize`` always
    spawning the upstream cold contends with every other concurrent spawn of
    that bridge on a busy host and can stall for minutes). When a resident
    ``agent-mcp serve`` daemon already holds this bridge warm, ``materialize``
    must use it instead of spawning a fresh upstream process."""
    from agent_mcp import __main__ as agent_mcp_main

    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_MCP_HOME", str(home))
    home.mkdir()
    sock = home / "serve.sock"
    cfg = _write_cfg(tmp_path)
    dest = tmp_path / "materialized"

    # Prove the cold path is never reached: it would normally spawn the
    # upstream process directly, so make it fail loudly if invoked.
    async def _cold_path_must_not_run(_cfg):
        raise AssertionError("materialize spawned the upstream cold with a "
                             "warm serve daemon available")
    monkeypatch.setattr(agent_mcp_main, "_run_materialize", _cold_path_must_not_run)

    daemon = _ServeDaemonThread(sock)
    daemon.start()
    try:
        rc = main(["materialize", str(cfg), "--server-name", "fix",
                  "--dest", str(dest), "--quiet"])
        assert rc == 0
        assert (dest / "fix" / "bin" / "greet").is_symlink()
        manifest = json.loads((dest / "fix" / "manifest.json").read_text())
        assert "greet" in manifest["tools"]
    finally:
        daemon.shutdown()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink farm")
def test_materialize_no_serve_flag_forces_cold_path_even_with_daemon(
    tmp_path, monkeypatch,
):
    """``--no-serve`` is the escape hatch: always spawn cold, even when a
    warm daemon is present (mirrors ``call --no-serve``)."""
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_MCP_HOME", str(home))
    home.mkdir()
    sock = home / "serve.sock"
    cfg = _write_cfg(tmp_path)
    dest = tmp_path / "materialized"

    daemon = _ServeDaemonThread(sock)
    daemon.start()
    try:
        rc = main(["materialize", str(cfg), "--server-name", "fix",
                  "--dest", str(dest), "--quiet", "--no-serve"])
        assert rc == 0
        assert (dest / "fix" / "bin" / "greet").is_symlink()
        # A warm daemon was live and reachable throughout, yet the pool never
        # opened a session for this bridge -- proof --no-serve skipped it.
        assert daemon.server.pool.size == 0
    finally:
        daemon.shutdown()
