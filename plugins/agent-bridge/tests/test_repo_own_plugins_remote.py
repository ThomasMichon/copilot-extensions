"""Tests for the remote repo-own ``.ai`` plugin resolve (repo_own_plugins_remote).

Moved from agent-codespaces' ``test_ai_plugin_staging`` in PR2 (dotfiles#1422):
the venue-generic repo-own ``.ai`` resolve now lives in agent-bridge atop the
transport-exec seam. The staging helpers (ship ``plugin_resolve`` + build the
remote command + parse the marker) are unchanged; the driver runs over
``target_exec`` instead of an agent-codespaces ``manager.exec_command``.
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_bridge import repo_own_plugins_remote as aps
from agent_bridge import target_exec as tx


def test_find_plugin_resolve_pkg_locates_installed_package():
    pkg = aps.find_plugin_resolve_pkg()
    assert pkg is not None
    assert (pkg / "__init__.py").is_file()


def test_tar_pkg_b64_roundtrips_with_package_arcname():
    pkg = aps.find_plugin_resolve_pkg()
    assert pkg is not None
    b64 = aps.tar_pkg_b64(pkg)
    buf = io.BytesIO(base64.b64decode(b64))
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        names = tf.getnames()
    assert any(n == "plugin_resolve" or n.startswith("plugin_resolve/") for n in names)
    assert any(n.endswith("plugin_resolve/__init__.py") for n in names)


def test_build_resolve_command_quotes_and_embeds_repo():
    cmd = aps.build_resolve_command("QkFTRTY0", "/workspaces/example-web")
    assert "base64 -d" in cmd and "tar -xzf" in cmd
    assert "/workspaces/example-web" in cmd
    assert "python3" in cmd and "command -v python" in cmd
    assert aps.RESULT_MARKER in cmd


def test_parse_resolve_result_extracts_marker_line():
    payload = json.dumps({
        "resolved": {"a@m": "/workspaces/r/.ai/a", "b@m": "/workspaces/r/.ai/b"},
        "unresolved": ["c@remote"],
    })
    out = f"login banner noise\n{aps.RESULT_MARKER}{payload}\ntrailing\n"
    resolved, unresolved = aps.parse_resolve_result(out)
    assert sorted(resolved) == ["/workspaces/r/.ai/a", "/workspaces/r/.ai/b"]
    assert unresolved == ["c@remote"]


def test_parse_resolve_result_failsafe_on_garbage():
    assert aps.parse_resolve_result("no marker here") == ([], [])
    assert aps.parse_resolve_result(f"{aps.RESULT_MARKER}not-json") == ([], [])
    assert aps.parse_resolve_result("") == ([], [])


def _make_ai_repo(root: Path) -> None:
    """Synthetic repo: a `.claude/settings.json` + `.ai` local marketplace with
    one local plugin (``atomic`` -> ``atomic-dir``) and one remote-marketplace
    plugin (``flt@remote``) that must NOT resolve to a dir."""
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example-web-plugins": {"source": {"source": "directory", "path": "./.ai"}},
            "remote": {"source": {"source": "github", "repo": "org/x"}},
        },
        "enabledPlugins": {
            "atomic@example-web-plugins": True,
            "flt@remote": True,
        },
    }))
    ai = root / ".ai"
    (ai / ".claude-plugin").mkdir(parents=True)
    (ai / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "example-web-plugins",
        "plugins": [{"name": "atomic", "source": "./atomic-dir"}],
    }))
    plug = ai / "atomic-dir" / ".claude-plugin"
    plug.mkdir(parents=True)
    (plug / "plugin.json").write_text(json.dumps({"name": "atomic", "version": "1.0.0"}))


def test_end_to_end_driver_resolves_local_skips_remote(tmp_path: Path):
    """Ship-and-run the resolver driver portably against a synthetic `.ai` repo:
    the local plugin dir resolves; the remote-marketplace one does not."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_ai_repo(repo)

    pkg = aps.find_plugin_resolve_pkg()
    assert pkg is not None
    dest = tmp_path / "shipped"
    dest.mkdir()
    buf = io.BytesIO(base64.b64decode(aps.tar_pkg_b64(pkg)))
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        tf.extractall(dest)
    proc = subprocess.run(
        [sys.executable, "-c", aps._DRIVER, str(dest), str(repo)],
        capture_output=True, text=True, timeout=60,
    )
    resolved, unresolved = aps.parse_resolve_result(proc.stdout)
    assert len(resolved) == 1
    assert resolved[0].replace("\\", "/").endswith("/.ai/atomic-dir")
    assert "flt@remote" in unresolved


def test_full_bash_command_runs_on_posix(tmp_path: Path):
    """The full remote bash command (mktemp+tar+python) end-to-end -- POSIX only."""
    import os
    import shutil

    if os.name != "posix" or shutil.which("bash") is None:  # pragma: no cover
        import pytest
        pytest.skip("full-bash command test is POSIX-only")
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_ai_repo(repo)
    pkg = aps.find_plugin_resolve_pkg()
    assert pkg is not None
    command = aps.build_resolve_command(aps.tar_pkg_b64(pkg), str(repo))
    proc = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, timeout=60,
    )
    resolved, unresolved = aps.parse_resolve_result(proc.stdout)
    assert len(resolved) == 1
    assert resolved[0].endswith("/.ai/atomic-dir")
    assert "flt@remote" in unresolved


# --- resolve_remote_repo_ai_plugin_dirs (the target_exec-driven entrypoint) ---

def test_resolve_remote_uses_transport_seam():
    payload = json.dumps({"resolved": {"a@m": "/ws/.ai/a"}, "unresolved": ["b@remote"]})
    out = f"noise\n{aps.RESULT_MARKER}{payload}\n"
    seen = {}

    def _exec(session, command, *, timeout):
        seen["session"] = session
        seen["has_cmd"] = "base64 -d" in command
        return out

    with patch("agent_bridge.target_exec.exec_bash_on_target", side_effect=_exec):
        resolved, unresolved = aps.resolve_remote_repo_ai_plugin_dirs(
            {"agent_name": "codespace:my-cs"}, "/ws",
        )
    assert resolved == ["/ws/.ai/a"]
    assert unresolved == ["b@remote"]
    assert seen["session"] == {"agent_name": "codespace:my-cs"}
    assert seen["has_cmd"] is True


def test_resolve_remote_empty_when_no_repo_dir():
    assert aps.resolve_remote_repo_ai_plugin_dirs(
        {"agent_name": "codespace:x"}, "",
    ) == ([], [])


def test_resolve_remote_best_effort_on_transport_error():
    with patch(
        "agent_bridge.target_exec.exec_bash_on_target",
        side_effect=tx.TargetExecError("no transport"),
    ):
        assert aps.resolve_remote_repo_ai_plugin_dirs(
            {"agent_name": "codespace:x"}, "/ws",
        ) == ([], [])


@pytest.mark.asyncio
async def test_resolve_remote_via_selected_transport():
    payload = json.dumps({
        "resolved": {"a@m": "/ws/.ai/a"},
        "unresolved": ["b@remote"],
    })
    seen = {}

    async def _run(command, *, timeout):
        seen["has_cmd"] = "base64 -d" in command
        seen["timeout"] = timeout
        return 0, f"{aps.RESULT_MARKER}{payload}\n", ""

    resolved, unresolved = await aps.resolve_remote_repo_ai_plugin_dirs_via(
        _run,
        "/ws",
    )

    assert resolved == ["/ws/.ai/a"]
    assert unresolved == ["b@remote"]
    assert seen == {"has_cmd": True, "timeout": 90.0}
