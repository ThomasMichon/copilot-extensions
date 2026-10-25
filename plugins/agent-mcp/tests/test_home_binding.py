"""Native, temporary-home proof through the real daemon and artifact writers."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

from agent_procutil import no_window_kwargs
import pytest


@pytest.mark.parametrize(
    "selection",
    [
        "missing",
        "empty",
        "relative",
        "absolute",
        pytest.param(
            "relative-parent",
            marks=pytest.mark.skipif(
                os.name == "nt", reason="POSIX symlink-plus-parent resolution"
            ),
        ),
        pytest.param(
            "absolute-parent",
            marks=pytest.mark.skipif(
                os.name == "nt", reason="POSIX symlink-plus-parent resolution"
            ),
        ),
        pytest.param(
            "socket-parent",
            marks=pytest.mark.skipif(
                os.name == "nt", reason="POSIX symlink-plus-parent resolution"
            ),
        ),
    ],
)
def test_serve_keeps_all_home_consumers_bound_after_chdir(tmp_path, selection):
    launch = tmp_path / "launch"
    profile = tmp_path / "profile"
    launch.mkdir()
    profile.mkdir()
    env = dict(os.environ, HOME=str(profile), USERPROFILE=str(profile))
    env.pop("AGENT_MCP_SERVE_SOCKET", None)
    env.pop("AGENT_MCP_CONTROL_TOKEN", None)
    parent_case = selection.endswith("-parent")
    if parent_case:
        nested = tmp_path / "durable" / "nested"
        nested.mkdir(parents=True)
        (launch / "link").symlink_to(nested, target_is_directory=True)
    if selection == "missing":
        env.pop("AGENT_MCP_HOME", None)
    elif selection == "empty":
        env["AGENT_MCP_HOME"] = ""
    elif selection == "relative":
        env["AGENT_MCP_HOME"] = "state"
    elif selection in ("relative-parent", "absolute-parent"):
        relative = Path("link") / ".." / "state"
        env["AGENT_MCP_HOME"] = str(
            launch / relative if selection == "absolute-parent" else relative
        )
    elif selection == "socket-parent":
        env["AGENT_MCP_HOME"] = str(tmp_path / "durable" / "state")
    else:
        env["AGENT_MCP_HOME"] = str(tmp_path / "absolute-state")
    expected = (
        tmp_path / "durable" / "state"
        if parent_case
        else launch / "state"
        if selection == "relative"
        else tmp_path / "absolute-state"
        if selection == "absolute"
        else profile / ".agent-mcp"
    )
    raw_home = env.get("AGENT_MCP_HOME")
    bound_home = (
        launch / raw_home
        if raw_home and not Path(raw_home).is_absolute()
        else Path(raw_home)
        if raw_home
        else profile / ".agent-mcp"
    )
    socket_arg = (
        str(Path("link") / ".." / "state" / "serve.sock") if selection == "socket-parent" else ""
    )
    bound_socket = launch / socket_arg if socket_arg else bound_home / "serve.sock"
    script = textwrap.dedent("""
        import argparse
        import json
        import os
        import sys
        from pathlib import Path
        from unittest.mock import patch

        from agent_mcp.__main__ import _cmd_serve
        from agent_mcp import config, materialize, serve, sockio
        from agent_mcp.auth import token_cache
        from agent_mcp.decorators import storage

        expected = Path(sys.argv[1])
        bound_home = Path(sys.argv[2])
        socket_arg = sys.argv[3] or None
        bound_socket = Path(sys.argv[4])
        launch = Path.cwd()
        assert sockio.default_home_dir() == bound_home
        assert sockio.absolute_handle(bound_socket) == str(bound_socket)
        assert config.BRIDGES_DIR == bound_home / "bridges"
        assert config.OVERRIDES_DIR == bound_home / "overrides"
        assert storage._DEFAULT_DIR == bound_home / "storage"
        for directory in ("bridges", "overrides"):
            (expected / directory).mkdir(parents=True)
        (expected / "bridges" / "fixture.json").write_text(json.dumps({
            "server": {"type": "http", "url": "https://example.test/mcp"}
        }), encoding="utf-8")
        (expected / "overrides" / "fixture.json").write_text(
            json.dumps({"timeout": 17}), encoding="utf-8")
        observed = []

        class ObservedServer(serve.Server):
            async def _start_control_listener(self):
                await super()._start_control_listener()
                assert Path.cwd() == expected.resolve()
                assert os.environ["AGENT_MCP_HOME"] == str(bound_home)
                assert sockio.default_home_dir() == bound_home
                assert sockio.default_socket_path() == bound_home / "serve.sock"
                assert self.socket_path == bound_socket
                assert self._config_dir().resolve() == expected.resolve()
                assert (expected / "active.json").is_file()
                assert (expected / "active.lock").is_file()
                assert (expected / "agent-mcp-serve.lock").is_file()
                assert config.load_config("fixture").timeout == 17
                assert materialize.default_dest() == bound_home / "materialized"
                assert len(materialize._source_digest_key()) >= 32
                assert (expected / "source-digest.key").is_file()
                assert token_cache.write("fixture", "synthetic-value", ttl="120")
                assert (expected / "token-cache" / "fixture.json").is_file()
                backend = storage.build_backend({})
                handle = backend.store("synthetic payload")
                assert backend.dir == bound_home / "storage"
                assert backend.load(handle) == "synthetic payload"
                observed.append(True)
                self.stop()

        with patch.object(serve, "Server", ObservedServer):
            assert _cmd_serve(argparse.Namespace(
                passive=False, socket=socket_arg, idle_timeout=300.0,
                control_port=None,
            )) == 0
        assert observed == [True]
        assert not (expected / "state").exists(), "home must not nest after chdir"
        assert not (launch / "active.json").exists()
        assert not (launch / "active.lock").exists()
        assert not (launch / "agent-mcp-serve.lock").exists()
        assert not (launch / "source-digest.key").exists()
        print("home binding verified")
    """)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(expected),
            str(bound_home),
            socket_arg,
            str(bound_socket),
        ],
        cwd=launch,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        **no_window_kwargs(),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "home binding verified"
    assert sorted(path.name for path in launch.iterdir()) == (
        ["link"] if parent_case else ["state"] if selection == "relative" else []
    )
