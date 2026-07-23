"""Tests for _build_launch_cmd: tool auto-approval and resume arg form."""

from __future__ import annotations

import argparse
import os

from agent_worktrees import __main__ as m
from agent_worktrees import config as cfg


def _config(launch: dict[str, list[str]] | None = None) -> cfg.Config:
    return cfg.Config(
        srcroot="/s", machine="dev6", platform="linux", repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor="/a", worktree_root="/w",
            launch=launch or {"linux": ["copilot"]},
        )},
    )


def _args(copilot_args: list[str]) -> argparse.Namespace:
    return argparse.Namespace(copilot_args=copilot_args, recovery=False)


def test_plain_launch_appends_allow_all():
    cmd = m._build_launch_cmd(_config(), _args([]), "/w/wt")
    assert cmd[-1] == "--allow-all"


def test_acp_launch_skips_allow_all():
    cmd = m._build_launch_cmd(_config(), _args(["--acp", "--stdio"]), "/w/wt")
    assert "--allow-all" not in cmd


def test_existing_all_perm_flag_not_duplicated():
    # --allow-all-tools, --allow-all, and --yolo are each an all-permissions
    # stance the caller already expressed, so we must not append our default
    # --allow-all on top of any of them.
    for flag in ("--allow-all-tools", "--allow-all", "--yolo"):
        cmd = m._build_launch_cmd(_config(), _args([flag]), "/w/wt")
        assert "--allow-all" not in [c for c in cmd if c != flag]
        assert cmd.count(flag) == 1


def test_resume_uses_equals_form():
    # copilot's --resume[=value] is an optional-value option; the id must be
    # attached with '=' or copilot treats it as a stray operand.
    cmd = m._build_launch_cmd(_config(), _args([]), "/w/wt")
    session = "46fa3c70-42d3-47b3-b60d-e472ef36c5d5"
    cmd.append(f"--resume={session}")
    assert f"--resume={session}" in cmd
    assert "--resume" not in cmd  # bare flag must not appear separately


# ---------------------------------------------------------------------------
# Normalized launch: config-declared setup_hook + session_path. See the
# agent-worktrees-normalized-launch effort, Phase 2.
# ---------------------------------------------------------------------------

def _hook_config(
    *,
    setup_hook: dict[str, str] | None = None,
    session_path: dict[str, list[str]] | None = None,
    legacy_launch: bool = False,
) -> cfg.Config:
    """A repo with NO launch template (so _build_launch_cmd hits the fallback
    branch) plus optional setup_hook / session_path."""
    return cfg.Config(
        srcroot="/s", machine="dev6", platform="linux", repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor="/a", worktree_root="/w",
            launch={"linux": ["copilot"]} if legacy_launch else {},
            setup_hook=setup_hook or {},
            session_path=session_path or {},
        )},
    )


def test_setup_hook_builds_normalized_launch(monkeypatch):
    """A setup_hook opts the repo into the normalized launcher (default-setup),
    passing the resolved hook path by argument."""
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cfg_ = _hook_config(setup_hook={"linux": "tools/setup/session-setup.sh"})
    cmd = m._build_launch_cmd(cfg_, _args([]), "/w/wt")

    assert cmd[0] == "bash"
    assert "default-setup.sh" in cmd[1]
    assert "--machine" in cmd and cmd[cmd.index("--machine") + 1] == "dev6"
    assert "--setup-hook" in cmd
    hook_arg = cmd[cmd.index("--setup-hook") + 1]
    assert hook_arg.endswith("session-setup.sh")
    # relative hook path is resolved against the anchor
    assert "tools" in hook_arg and "setup" in hook_arg
    assert cmd[-1] == "--allow-all"


def test_setup_hook_absolute_path_preserved(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cfg_ = _hook_config(setup_hook={"linux": "/opt/hooks/setup.sh"})
    cmd = m._build_launch_cmd(cfg_, _args([]), "/w/wt")
    hook_arg = cmd[cmd.index("--setup-hook") + 1]
    # An absolute hook path is used as-is, never joined onto the anchor.
    assert hook_arg.endswith("setup.sh")
    assert "opt" in hook_arg
    assert "a" not in hook_arg.split(os.sep)[:2]  # not prefixed by anchor "/a"


def test_session_path_templated_and_prepended(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cfg_ = _hook_config(
        setup_hook={"linux": "tools/setup/session-setup.sh"},
        session_path={"linux": ["{work_dir}/tools/bin"]},
    )
    cmd = m._build_launch_cmd(cfg_, _args([]), "/w/wt")
    assert "--session-path" in cmd
    assert cmd[cmd.index("--session-path") + 1] == "/w/wt/tools/bin"


def test_no_hook_uses_default_setup_without_hook_arg(monkeypatch):
    """No setup_hook and no legacy setup.sh -> plain default-setup, no hook arg."""
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cmd = m._build_launch_cmd(_hook_config(), _args([]), "/w/wt")
    assert cmd[0] == "bash"
    assert "default-setup.sh" in cmd[1]
    assert "--setup-hook" not in cmd


def test_setup_hook_recovery_passes_recovery_and_hook(monkeypatch):
    """In recovery, _build_launch_cmd still passes the hook + a --recovery flag;
    the launcher script is what skips the hook when recovering."""
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    args = argparse.Namespace(copilot_args=[], recovery=True)
    cfg_ = _hook_config(setup_hook={"linux": "tools/setup/session-setup.sh"})
    cmd = m._build_launch_cmd(cfg_, args, "/w/wt")
    assert "--setup-hook" in cmd
    assert "--recovery" in cmd


def test_setup_hook_and_session_path_config_parsing():
    """_build_repo_config parses setup_hook (path) and session_path (dir list)."""
    data = {
        "setup_hook": {"windows": "tools/setup/session-setup.ps1", "linux": "x.sh"},
        "session_path": {"linux": ["{work_dir}/tools/bin"]},
    }
    repo = cfg._build_repo_config(data, "/a", "/w")
    assert repo.setup_hook["windows"].endswith("session-setup.ps1")
    assert repo.setup_hook["linux"] == "x.sh"
    assert repo.session_path["linux"] == ["{work_dir}/tools/bin"]


def test_setup_hook_config_parsing_ignores_blank():
    data = {"setup_hook": {"linux": "  ", "windows": "hook.ps1"}}
    repo = cfg._build_repo_config(data, "/a", "/w")
    assert "linux" not in repo.setup_hook
    assert repo.setup_hook["windows"] == "hook.ps1"


def test_session_env_config_parsing():
    data = {"session_env": {"COPILOT_FEATURE_FLAGS": "extensions", "X": 1}}
    repo = cfg._build_repo_config(data, "/a", "/w")
    assert repo.session_env["COPILOT_FEATURE_FLAGS"] == "extensions"
    assert repo.session_env["X"] == "1"  # coerced to str


def test_build_env_merges_repo_session_env(monkeypatch):
    """Repo session_env lands in the plan env; the profile overrides it."""
    monkeypatch.setattr(cfg, "project_dir", lambda: __import__("pathlib").Path("/proj"))
    env = m._build_env(None, {"COPILOT_FEATURE_FLAGS": "extensions"})
    assert env["COPILOT_FEATURE_FLAGS"] == "extensions"
    assert "COPILOT_CUSTOM_INSTRUCTIONS_DIRS" in env


def test_build_env_profile_overrides_session_env(monkeypatch):
    monkeypatch.setattr(cfg, "project_dir", lambda: __import__("pathlib").Path("/proj"))
    prof = cfg.CopilotProfile(name="p", label="p", env={"COPILOT_FEATURE_FLAGS": "override"})
    env = m._build_env(prof, {"COPILOT_FEATURE_FLAGS": "extensions"})
    assert env["COPILOT_FEATURE_FLAGS"] == "override"


def test_repo_session_env_templates_values(monkeypatch):
    """session_env values are templated with {home}/{work_dir}/{machine} etc."""
    cfg_ = cfg.Config(
        srcroot="/s", machine="dev6", platform="linux", repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor="/a", worktree_root="/w",
            session_env={
                "SUDO_ASKPASS": "{home}/.local/bin/vault-askpass",
                "WD": "{work_dir}/x",
                "M": "{machine}",
            },
        )},
    )
    out = m._repo_session_env(cfg_, "/w/wt")
    assert out["SUDO_ASKPASS"] == os.path.expanduser("~") + "/.local/bin/vault-askpass"
    assert out["WD"] == "/w/wt/x"
    assert out["M"] == "dev6"


def test_repo_session_env_passthrough_on_bad_placeholder():
    cfg_ = cfg.Config(
        srcroot="/s", machine="dev6", platform="linux", repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor="/a", worktree_root="/w",
            session_env={"K": "{unknown_placeholder}/x"},
        )},
    )
    out = m._repo_session_env(cfg_, "/w/wt")
    assert out["K"] == "{unknown_placeholder}/x"  # passed through, no crash


# ---------------------------------------------------------------------------
# env_script: capture a repo env-priming script's environment for the exec.
# See the agent-worktrees-env-script feature (declarative enlistment priming).
# ---------------------------------------------------------------------------

def _env_config(
    *,
    env_script: dict[str, str] | None = None,
    setup_hook: dict[str, str] | None = None,
    platform_name: str = "linux",
) -> cfg.Config:
    """A repo with NO launch template plus an env_script (+ optional hook)."""
    return cfg.Config(
        srcroot="/s", machine="dev6", platform=platform_name, repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor="/a", worktree_root="/w",
            launch={},
            env_script=env_script or {},
            setup_hook=setup_hook or {},
        )},
    )


def test_env_script_config_parsing():
    data = {"env_script": {"windows": "otools\\bin\\OpenEnlistment.bat", "linux": "  "}}
    repo = cfg._build_repo_config(data, "/a", "/w")
    assert repo.env_script["windows"].endswith("OpenEnlistment.bat")
    assert "linux" not in repo.env_script  # blank ignored


def test_env_script_windows_builds_default_setup_with_flag(monkeypatch):
    """env_script (no hook) routes to default-setup.ps1 with -EnvScript, resolved
    against the anchor."""
    monkeypatch.setattr(m.platform, "system", lambda: "Windows")
    cfg_ = _env_config(env_script={"windows": "otools\\bin\\OpenEnlistment.bat"},
                       platform_name="windows")
    cmd = m._build_launch_cmd(cfg_, _args([]), "/a")
    assert any("default-setup.ps1" in c for c in cmd)
    assert "-EnvScript" in cmd
    env_arg = cmd[cmd.index("-EnvScript") + 1]
    assert env_arg.endswith("OpenEnlistment.bat")
    assert "otools" in env_arg  # resolved relative to anchor


def test_env_script_linux_builds_default_setup_with_flag(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cfg_ = _env_config(env_script={"linux": "tools/prime.sh"}, platform_name="linux")
    cmd = m._build_launch_cmd(cfg_, _args([]), "/a")
    assert cmd[0] == "bash"
    assert "default-setup.sh" in cmd[1]
    assert "--env-script" in cmd
    assert cmd[cmd.index("--env-script") + 1].endswith("prime.sh")


def test_env_script_absolute_path_preserved(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cfg_ = _env_config(env_script={"linux": "/opt/prime.sh"}, platform_name="linux")
    cmd = m._build_launch_cmd(cfg_, _args([]), "/a")
    env_arg = cmd[cmd.index("--env-script") + 1]
    # An absolute env_script path is used as-is, never joined onto the anchor.
    # (Assert structurally, not by exact string: the host os.sep differs.)
    assert env_arg.endswith("prime.sh")
    assert "opt" in env_arg
    assert "a" not in env_arg.split(os.sep)[:2]  # not prefixed by anchor "/a"


def test_env_script_with_setup_hook_passes_both(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cfg_ = _env_config(
        env_script={"linux": "tools/prime.sh"},
        setup_hook={"linux": "tools/setup/hook.sh"},
        platform_name="linux",
    )
    cmd = m._build_launch_cmd(cfg_, _args([]), "/a")
    assert "--setup-hook" in cmd and "--env-script" in cmd


# ---------------------------------------------------------------------------
# The shipped launcher scripts must understand the normalized-launch contract.
# ---------------------------------------------------------------------------

_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")


def test_default_setup_sh_supports_hook_and_session_path():
    text = open(os.path.join(_SCRIPTS_DIR, "default-setup.sh"), encoding="utf-8").read()
    assert "--setup-hook" in text
    assert "--session-path" in text
    assert "--env-script" in text
    # env_script is sourced with auto-export so its vars reach the exec
    assert "set -a" in text
    # hook is skipped in recovery
    assert 'RECOVERY" != true' in text
    # PATH is prepended, and Copilot is exec'd (launcher owns the exec)
    assert 'export PATH="${SESSION_PATH}:${PATH}"' in text
    assert "exec copilot" in text
    # --stdio (ACP) mode keeps human output off the JSON-RPC channel
    assert "STDIO=true" in text
    assert 'bash "$SETUP_HOOK" --machine "$MACHINE" >&2' in text


def test_default_setup_ps1_supports_hook_and_session_path():
    text = open(os.path.join(_SCRIPTS_DIR, "default-setup.ps1"), encoding="utf-8").read()
    assert "$SetupHook" in text
    assert "$SessionPath" in text
    assert "$EnvScript" in text
    # env_script's captured environment is imported into the launcher process
    assert "SetEnvironmentVariable" in text
    assert "-not $Recovery" in text  # hook skipped in recovery
    assert "$env:PATH" in text
    assert "copilot @CopilotArgs" in text
    # --stdio (ACP) mode redirects Write-Host + hook output to stderr
    assert "StdioMode" in text
    assert "[Console]::Error.WriteLine" in text
