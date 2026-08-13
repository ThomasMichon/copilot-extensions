"""Tests for the #892 Increment 1 process-boundary seams.

agent-bridge's CodeSpace dispatch resolves the relay/auth-helper *provision
command* and the *relay launch env* by shelling out to the ``agent-codespaces``
binstub (so an agent-codespaces fix reaches the dispatch path from its OWN venv
with no agent-bridge redeploy), falling back to the in-process import when the
binstub is absent or the CLI misfires (so it can never regress while the venvs
are still coupled). These tests exercise both paths by mocking ``shutil.which``
and ``subprocess.run`` (the helpers do local ``import shutil``/``subprocess``,
which bind the shared module objects, so patching their attributes works).
"""

from __future__ import annotations

import subprocess
import sys
import types
from unittest.mock import patch

from agent_bridge.session_host import spawner
from agent_bridge import session_manager


def _fake_cs_module(**attrs):
    """Inject a fake ``agent_codespaces.<sub>`` module for the import fallback."""
    pkg = types.ModuleType("agent_codespaces")
    mods = {"agent_codespaces": pkg}
    for sub, members in attrs.items():
        m = types.ModuleType(f"agent_codespaces.{sub}")
        for k, v in members.items():
            setattr(m, k, v)
        mods[f"agent_codespaces.{sub}"] = m
    return mods


# --- _resolve_provision_command (spawner) --------------------------------

def test_provision_command_prefers_cli():
    ok = subprocess.CompletedProcess([], 0, "echo provision\n", "")
    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", return_value=ok):
        assert spawner._resolve_provision_command() == "echo provision\n"


def test_provision_command_falls_back_to_import_on_cli_failure():
    bad = subprocess.CompletedProcess([], 1, "", "boom")
    mods = _fake_cs_module(codespace_assets={"build_provision_command": lambda: "FALLBACK"})
    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", return_value=bad), \
         patch.dict(sys.modules, mods):
        assert spawner._resolve_provision_command() == "FALLBACK"


def test_provision_command_import_fallback_when_no_binstub():
    mods = _fake_cs_module(codespace_assets={"build_provision_command": lambda: "FB2"})
    with patch("shutil.which", return_value=None), \
         patch.dict(sys.modules, mods):
        assert spawner._resolve_provision_command() == "FB2"


def test_provision_command_none_when_unavailable():
    # No binstub AND no importable agent_codespaces -> None (skip the step).
    with patch("shutil.which", return_value=None), \
         patch.dict(sys.modules, {"agent_codespaces": None}):
        assert spawner._resolve_provision_command() is None


# --- _resolve_relay_launch_env (session_manager) -------------------------

def test_relay_launch_env_prefers_cli_and_parses_json():
    ok = subprocess.CompletedProcess([], 0, '{"prelude": "export T=x", "port": 50123}', "")
    seen = {}

    def _run(argv, **_kw):
        seen["argv"] = argv
        return ok

    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", side_effect=_run):
        prelude, port = session_manager._resolve_relay_launch_env("my-cs", 50123)
    assert (prelude, port) == ("export T=x", 50123)
    assert seen["argv"] == [
        "/bin/agent-codespaces", "relay-launch-env", "my-cs", "--relay-port", "50123",
    ]


def test_relay_launch_env_omits_relay_port_when_none():
    ok = subprocess.CompletedProcess([], 0, '{"prelude": "", "port": 9857}', "")
    seen = {}

    def _run(argv, **_kw):
        seen["argv"] = argv
        return ok

    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", side_effect=_run):
        session_manager._resolve_relay_launch_env("my-cs", None)
    assert "--relay-port" not in seen["argv"]


def test_relay_launch_env_falls_back_to_import_on_cli_failure():
    bad = subprocess.CompletedProcess([], 2, "", "nope")
    mods = _fake_cs_module(
        relay_launch={"build_relay_launch_env": lambda cs, relay_port=None: ("PRELUDE", 40000)}
    )
    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", return_value=bad), \
         patch.dict(sys.modules, mods):
        assert session_manager._resolve_relay_launch_env("cs", 1) == ("PRELUDE", 40000)


def test_relay_launch_env_auth_light_when_unavailable():
    with patch("shutil.which", return_value=None), \
         patch.dict(sys.modules, {"agent_codespaces": None}):
        assert session_manager._resolve_relay_launch_env("cs", None) == ("", None)


# --- _resolve_codespace_ai_plugin_dirs (session_manager) -----------------

def test_ai_plugin_dirs_shells_verb_and_splits_lines():
    ok = subprocess.CompletedProcess(
        [], 0, "/workspaces/odsp-web/.ai/atomic\n/workspaces/odsp-web/.ai/od-web\n", "",
    )
    seen = {}

    def _run(argv, **_kw):
        seen["argv"] = argv
        return ok

    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", side_effect=_run):
        dirs = session_manager._resolve_codespace_ai_plugin_dirs(
            "my-cs", "odsp-microsoft/odsp-web",
        )
    assert dirs == [
        "/workspaces/odsp-web/.ai/atomic", "/workspaces/odsp-web/.ai/od-web",
    ]
    assert seen["argv"] == [
        "/bin/agent-codespaces", "resolve-ai-plugin-dirs", "my-cs",
        "--repo", "odsp-microsoft/odsp-web",
    ]


def test_ai_plugin_dirs_passes_repo_dir_precedence():
    # The Session-Host dispatch passes the target's known workspace_folder as
    # --repo-dir even when the spawn command carried no --repo (dotfiles#1274).
    ok = subprocess.CompletedProcess([], 0, "/workspaces/odsp-web/.ai/atomic\n", "")
    seen = {}

    def _run(argv, **_kw):
        seen["argv"] = argv
        return ok

    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", side_effect=_run):
        dirs = session_manager._resolve_codespace_ai_plugin_dirs(
            "my-cs", None, repo_dir="/workspaces/odsp-web",
        )
    assert dirs == ["/workspaces/odsp-web/.ai/atomic"]
    assert seen["argv"] == [
        "/bin/agent-codespaces", "resolve-ai-plugin-dirs", "my-cs",
        "--repo-dir", "/workspaces/odsp-web",
    ]
    assert "--repo" not in seen["argv"]


def test_ai_plugin_dirs_omits_repo_when_none_and_trims_blank_lines():
    ok = subprocess.CompletedProcess([], 0, "  /a/.ai/x  \n\n\n", "")
    seen = {}

    def _run(argv, **_kw):
        seen["argv"] = argv
        return ok

    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", side_effect=_run):
        dirs = session_manager._resolve_codespace_ai_plugin_dirs("my-cs", None)
    assert dirs == ["/a/.ai/x"]
    assert "--repo" not in seen["argv"]


def test_ai_plugin_dirs_empty_on_nonzero_exit():
    bad = subprocess.CompletedProcess([], 3, "junk\n", "boom")
    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", return_value=bad):
        assert session_manager._resolve_codespace_ai_plugin_dirs("cs", "r") == []


def test_ai_plugin_dirs_empty_on_cli_exception():
    def _boom(*_a, **_kw):
        raise OSError("no exec")

    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", side_effect=_boom):
        assert session_manager._resolve_codespace_ai_plugin_dirs("cs", "r") == []


def test_ai_plugin_dirs_empty_when_no_binstub():
    with patch("shutil.which", return_value=None):
        assert session_manager._resolve_codespace_ai_plugin_dirs("cs", "r") == []
