"""Production startup defers ACP construction without changing public exports."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

from agent_procutil import no_window_kwargs


def test_cold_app_and_manager_defer_sdk_until_the_public_client_is_requested(tmp_path):
    script = textwrap.dedent("""
        import json, sys
        from pathlib import Path
        from agent_bridge.app import create_app
        from agent_bridge.db import Database
        from agent_bridge.models import ServiceConfig
        from agent_bridge import session_manager
        from agent_bridge.runtime_transfer import receive_runtime

        assert "agent_bridge.app_routes" not in sys.modules
        assert "agent_bridge.agent_registry" not in sys.modules
        assert "agent_bridge.worktree_runtime" not in sys.modules
        assert "agent_bridge.routes.worktrees" not in sys.modules
        assert "agent_bridge.serving_runtime" not in sys.modules
        assert "agent_bridge.staged_relay" not in sys.modules
        config = ServiceConfig(db_path=str(Path(sys.argv[1]) / "sessions.db"))
        db = Database(Path(config.db_path))
        try:
            manager = session_manager.session_manager_from_config(db, config)
            app = create_app(config=config, token="synthetic")
            assert "agent_bridge.app_routes" in sys.modules
            assert manager.db is db
            assert app.state.config is config
            assert callable(receive_runtime)
            assert not any(name == "acp" or name.startswith("acp.") for name in sys.modules)
            assert "agent_bridge.acp_client" not in sys.modules
            from agent_bridge.session_manager import AcpClient
            from agent_bridge.acp_client import AcpClient as actual
            assert AcpClient is actual and session_manager.AcpClient is actual
            try:
                session_manager.not_a_public_export
            except AttributeError:
                pass
            else:
                raise AssertionError("Unknown module attribute did not refuse")
            print(json.dumps({"deferred": True, "public_client_identity": True}))
        finally:
            db.close()
    """)
    child = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == {"deferred": True, "public_client_identity": True}
