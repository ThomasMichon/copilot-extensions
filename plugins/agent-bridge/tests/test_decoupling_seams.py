"""Tests for the #1643 pure process-boundary seams.

agent-bridge's CodeSpace dispatch resolves the relay/auth-helper *provision
command* and the *relay launch env* by shelling out to the ``agent-codespaces``
binstub (so an agent-codespaces fix reaches the dispatch path from its OWN venv
with no agent-bridge redeploy). As of #1643 there is **no** in-process
``agent_codespaces`` import fallback -- the daemon runs from its own isolated
venv where a provider package is neither importable nor on ``PATH`` -- so when
the binstub is absent or the CLI misfires the seam degrades auth-light (``None``
/ ``("", None)``) rather than importing the provider. These tests exercise both
the CLI-hit and the degrade paths by mocking ``shutil.which`` and
``subprocess.run`` (the helpers do local ``import shutil``/``subprocess``, which
bind the shared module objects, so patching their attributes works).
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from agent_bridge.session_host import spawner
from agent_bridge import session_manager


# --- _resolve_provision_command (spawner) --------------------------------

def test_provision_command_prefers_cli():
    ok = subprocess.CompletedProcess([], 0, "echo provision\n", "")
    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", return_value=ok):
        assert spawner._resolve_provision_command() == "echo provision\n"


def test_provision_command_none_on_cli_failure():
    # No in-process import fallback (#1643): a CLI failure degrades to None (the
    # caller skips the best-effort relay-helper redeploy step), never an import.
    bad = subprocess.CompletedProcess([], 1, "", "boom")
    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", return_value=bad):
        assert spawner._resolve_provision_command() is None


def test_provision_command_none_when_no_binstub():
    # Binstub absent -> None (no in-process agent_codespaces import, #1643).
    with patch("shutil.which", return_value=None):
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


def test_relay_launch_env_none_on_cli_failure():
    # No in-process import fallback (#1643): a CLI failure degrades auth-light
    # (("", None)) rather than importing agent_codespaces.
    bad = subprocess.CompletedProcess([], 2, "", "nope")
    with patch("shutil.which", return_value="/bin/agent-codespaces"), \
         patch("subprocess.run", return_value=bad):
        assert session_manager._resolve_relay_launch_env("cs", 1) == ("", None)


def test_relay_launch_env_auth_light_when_no_binstub():
    with patch("shutil.which", return_value=None):
        assert session_manager._resolve_relay_launch_env("cs", None) == ("", None)


# --- _resolve_codespace_ai_plugin_dirs (session_manager, PR2) -------------
# Now resolves in-agent-bridge via repo_own_plugins_remote over the
# transport-exec seam -- no longer shells `agent-codespaces resolve-ai-plugin-dirs`.

def test_ai_plugin_dirs_uses_in_bridge_remote_resolve():
    seen = {}

    def _resolve(session, repo_dir, **_kw):
        seen["session"] = session
        seen["repo_dir"] = repo_dir
        return (
            ["/workspaces/example-web/.ai/atomic", "/workspaces/example-web/.ai/od-web"],
            ["example-ai-hub-ecs@agency-playground"],
        )

    with patch(
        "agent_bridge.repo_own_plugins_remote.resolve_remote_repo_ai_plugin_dirs",
        side_effect=_resolve,
    ):
        dirs = session_manager._resolve_codespace_ai_plugin_dirs(
            "my-cs", "example-org/example-web", repo_dir="/workspaces/example-web",
        )
    assert dirs == [
        "/workspaces/example-web/.ai/atomic", "/workspaces/example-web/.ai/od-web",
    ]
    # target identified as a codespace by agent_name; repo_dir threaded through.
    assert seen["session"] == {"agent_name": "codespace:my-cs"}
    assert seen["repo_dir"] == "/workspaces/example-web"


def test_ai_plugin_dirs_empty_when_no_repo_dir():
    # repo_dir is the concrete workspace_folder; without it we can't resolve, and
    # repo-alone resolution never worked (dotfiles#1274) -> [] without a remote call.
    called = {"n": 0}

    def _resolve(*_a, **_kw):
        called["n"] += 1
        return ([], [])

    with patch(
        "agent_bridge.repo_own_plugins_remote.resolve_remote_repo_ai_plugin_dirs",
        side_effect=_resolve,
    ):
        assert session_manager._resolve_codespace_ai_plugin_dirs("cs", "repo") == []
    assert called["n"] == 0


def test_ai_plugin_dirs_empty_on_best_effort_failure():
    # resolve_remote_repo_ai_plugin_dirs is best-effort ([],[]) on any transport /
    # resolver failure -> the dispatch proceeds with no repo-own plugins.
    with patch(
        "agent_bridge.repo_own_plugins_remote.resolve_remote_repo_ai_plugin_dirs",
        return_value=([], []),
    ):
        assert session_manager._resolve_codespace_ai_plugin_dirs(
            "cs", None, repo_dir="/workspaces/example-web",
        ) == []
