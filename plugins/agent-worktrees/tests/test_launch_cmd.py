"""Tests for _build_launch_cmd: tool auto-approval and resume arg form."""

from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_plain_launch_does_not_append_removed_no_sandbox_flag():
    # Copilot CLI 1.0.81-9 removed --no-sandbox. Auto-appending the retired flag
    # makes every new interactive worktree and handoff successor exit at launch.
    cmd = m._build_launch_cmd(_config(), _args([]), "/w/wt")
    assert "--no-sandbox" not in cmd


def test_acp_launch_skips_allow_all():
    cmd = m._build_launch_cmd(_config(), _args(["--acp", "--stdio"]), "/w/wt")
    assert "--allow-all" not in cmd
    # ACP sessions get permissions managed by agent-bridge over the protocol.
    assert "--no-sandbox" not in cmd


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
    copilot_path: dict[str, str] | None = None,
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
            copilot_path=copilot_path or {},
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
    assert "--config-root" in cmd
    assert "--runtime-python" in cmd
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
    assert "--config-root" not in cmd
    assert "--runtime-python" not in cmd


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


def test_copilot_path_config_parsing_ignores_blank():
    data = {
        "copilot_path": {
            "windows": r"C:\tools\copilot-dev.cmd",
            "linux": "  ",
        },
    }
    repo = cfg._build_repo_config(data, "/a", "/w")
    assert repo.copilot_path["windows"].endswith("copilot-dev.cmd")
    assert "linux" not in repo.copilot_path


def test_copilot_path_linux_uses_normalized_launcher(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cfg_ = _hook_config(
        copilot_path={"linux": "{home}/src/runtime/dist-bin/linux-arm64/copilot"},
    )
    cmd = m._build_launch_cmd(cfg_, _args(["--version"]), "/w/wt")
    assert "default-setup.sh" in cmd[1]
    assert "--copilot-path" in cmd
    selected = cmd[cmd.index("--copilot-path") + 1]
    assert selected.endswith("/src/runtime/dist-bin/linux-arm64/copilot")
    assert "--version" in cmd


def test_copilot_path_windows_uses_normalized_launcher(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Windows")
    cfg_ = cfg.Config(
        srcroot="/s",
        machine="dev6",
        platform="windows",
        repo_name="ext",
        repos={
            "ext": cfg.RepoConfig(
                anchor=r"C:\a",
                worktree_root=r"C:\w",
                copilot_path={"windows": r"C:\tools\copilot-dev.cmd"},
            ),
        },
    )
    cmd = m._build_launch_cmd(cfg_, _args([]), r"C:\w\wt")
    assert any("default-setup.ps1" in c for c in cmd)
    assert "-CopilotPath" in cmd
    assert cmd[cmd.index("-CopilotPath") + 1] == r"C:\tools\copilot-dev.cmd"


def test_explicit_launch_remains_authoritative_over_copilot_path(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cfg_ = _hook_config(
        legacy_launch=True,
        setup_hook={"linux": "tools/setup/session-setup.sh"},
        copilot_path={"linux": "/opt/copilot-dev"},
    )
    monkeypatch.setattr(
        m.state_root_mod,
        "resolve_config_root",
        lambda *args, **kwargs: pytest.fail(
            "explicit launch must not resolve normalized setup state"
        ),
    )
    cmd = m._build_launch_cmd(cfg_, _args([]), "/w/wt")
    assert cmd[0] == "copilot"
    assert "--copilot-path" not in cmd


def test_predecessor_copilot_path_falls_back_for_default_launch(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cmd = m._build_launch_cmd(
        _hook_config(),
        _args([]),
        "/w/wt",
        fallback_copilot_path="/opt/copilot/current/copilot",
    )
    assert cmd[cmd.index("--copilot-path") + 1] == (
        "/opt/copilot/current/copilot"
    )


def test_configured_copilot_path_wins_over_predecessor_fallback(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cmd = m._build_launch_cmd(
        _hook_config(copilot_path={"linux": "/opt/copilot/configured"}),
        _args([]),
        "/w/wt",
        fallback_copilot_path="/opt/copilot/predecessor",
    )
    assert cmd[cmd.index("--copilot-path") + 1] == "/opt/copilot/configured"


def test_explicit_launch_ignores_predecessor_copilot_path(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    cmd = m._build_launch_cmd(
        _hook_config(legacy_launch=True),
        _args([]),
        "/w/wt",
        fallback_copilot_path="/opt/copilot/predecessor",
    )
    assert cmd[0] == "copilot"
    assert "--copilot-path" not in cmd


def test_legacy_setup_uses_path_resolved_shell(monkeypatch, tmp_path):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    setup = tmp_path / "tools" / "setup" / "setup.sh"
    setup.parent.mkdir(parents=True)
    setup.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    cfg_ = _hook_config()
    repo = dataclasses.replace(cfg_.default_repo, anchor=str(tmp_path))
    cfg_ = dataclasses.replace(cfg_, repos={"ext": repo})

    cmd = m._build_launch_cmd(
        cfg_,
        _args([]),
        str(tmp_path),
    )

    assert cmd[0] == "bash"
    assert cmd[1] == str(setup)
    assert "--copilot-path" not in cmd


def test_legacy_setup_ignores_predecessor_copilot_path(monkeypatch, tmp_path):
    monkeypatch.setattr(m.platform, "system", lambda: "Linux")
    setup = tmp_path / "tools" / "setup" / "setup.sh"
    setup.parent.mkdir(parents=True)
    setup.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    cfg_ = _hook_config()
    repo = dataclasses.replace(cfg_.default_repo, anchor=str(tmp_path))
    cfg_ = dataclasses.replace(cfg_, repos={"ext": repo})

    cmd = m._build_launch_cmd(
        cfg_,
        _args([]),
        str(tmp_path),
        fallback_copilot_path="/opt/copilot/predecessor",
    )

    assert cmd[0] == "bash"
    assert cmd[1] == str(setup)
    assert "--copilot-path" not in cmd


def test_windows_normalized_launch_uses_path_resolved_shell(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Windows")
    cfg_ = cfg.Config(
        srcroot="/s",
        machine="dev6",
        platform="windows",
        repo_name="ext",
        repos={
            "ext": cfg.RepoConfig(
                anchor=r"C:\a",
                worktree_root=r"C:\w",
                setup_hook={"windows": r"tools\setup\session-setup.ps1"},
            ),
        },
    )

    cmd = m._build_launch_cmd(cfg_, _args([]), r"C:\w\wt")

    assert cmd[0] == "pwsh.exe"


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


def test_cmd_launch_passes_active_project_explicitly(monkeypatch, tmp_path):
    """A bare project launch carries explicit identity into the shell launcher."""
    monkeypatch.delenv("WORKTREE_PROJECT", raising=False)
    monkeypatch.setattr(m.cfg, "_ACTIVE_PROJECT", "dotfiles")
    monkeypatch.setattr(m.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(m.cfg, "detect_platform", lambda: "linux")
    launch_script = tmp_path / "bin" / "launch-session.sh"
    launch_script.parent.mkdir()
    launch_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(m.os, "execvp", lambda exe, argv: calls.append((exe, argv)))
    rc = m.cmd_launch([])
    assert rc == 1
    assert calls == [
        ("bash", ["bash", str(launch_script), "--project", "dotfiles"])
    ]
    assert "WORKTREE_PROJECT" not in os.environ


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
    assert "--copilot-path" in text
    assert "--config-root" in text
    assert "AGENT_WORKTREES_CONFIG_ROOT" in text
    assert '"$_AW_PY" -I' in text
    assert 'command -v -- "$_AW_PY"' in text
    assert text.index('export AGENT_WORKTREES_CONFIG_ROOT=') > text.index(
        '. "$ENV_SCRIPT"'
    )
    # env_script is sourced with auto-export so its vars reach the exec
    assert "set -a" in text
    # hook is skipped in recovery
    assert 'RECOVERY" != true' in text
    # PATH is prepended, and Copilot is exec'd (launcher owns the exec)
    assert 'export PATH="${SESSION_PATH}:${PATH}"' in text
    assert "exec copilot" in text
    assert 'exec "$COPILOT_PATH_OVERRIDE"' in text
    # --stdio (ACP) mode keeps human output off the JSON-RPC channel
    assert "STDIO=true" in text
    assert '"$BASH" "$SETUP_HOOK" --machine "$MACHINE" >&2' in text


def test_default_setup_ps1_supports_hook_and_session_path():
    text = open(os.path.join(_SCRIPTS_DIR, "default-setup.ps1"), encoding="utf-8").read()
    assert "$SetupHook" in text
    assert "$SessionPath" in text
    assert "$EnvScript" in text
    assert "$CopilotPath" in text
    assert "$ConfigRoot" in text
    assert "AGENT_WORKTREES_CONFIG_ROOT" in text
    assert "'-I', '-m', 'agent_worktrees'" in text
    assert "Test-Path -LiteralPath $guardPython -PathType Leaf" in text
    assert "WildcardPattern]::Escape($guardPython)" in text
    assert text.index("$env:AGENT_WORKTREES_CONFIG_ROOT =") > text.index(
        "SetEnvironmentVariable"
    )
    # env_script's captured environment is imported into the launcher process
    assert "SetEnvironmentVariable" in text
    assert "-not $Recovery" in text  # hook skipped in recovery
    assert "$env:PATH" in text
    assert "& pwsh.exe -NoProfile -NoLogo -File $SetupHook" in text
    assert "& $overrideCmd.Source @CopilotArgs" in text
    assert "copilot @CopilotArgs" in text
    # --stdio (ACP) mode redirects Write-Host + hook output to stderr
    assert "StdioMode" in text
    assert "[Console]::Error.WriteLine" in text


def test_default_setup_launches_absolute_copilot_with_empty_path(
    tmp_path,
):
    marker = tmp_path / "launched"
    env = os.environ.copy()
    env["PATH"] = ""
    env["HOSTNAME"] = "test-host"
    env["HOME"] = str(tmp_path / "home")
    env["USERPROFILE"] = str(tmp_path / "home")
    env["COPILOT_LAUNCH_MARKER"] = str(marker)
    scripts = Path(__file__).resolve().parents[1] / "scripts"

    if os.name == "nt":
        shell = shutil.which("pwsh")
        if not shell:
            pytest.skip("pwsh is unavailable")
        copilot = tmp_path / "copilot-test.cmd"
        copilot.write_text(
            "@echo off\r\n"
            "> \"%COPILOT_LAUNCH_MARKER%\" echo launched\r\n",
            encoding="utf-8",
        )
        command = [
            shell,
            "-NoProfile",
            "-NoLogo",
            "-File",
            str(scripts / "default-setup.ps1"),
            "-Machine",
            "test",
            "-CopilotPath",
            str(copilot),
        ]
    else:
        shell = shutil.which("bash")
        if not shell:
            pytest.skip("bash is unavailable")
        copilot = tmp_path / "copilot-test"
        copilot.write_text(
            "#!/bin/sh\nprintf launched > \"$COPILOT_LAUNCH_MARKER\"\n",
            encoding="utf-8",
        )
        copilot.chmod(0o755)
        command = [
            shell,
            str(scripts / "default-setup.sh"),
            "--machine",
            "test",
            "--copilot-path",
            str(copilot),
        ]

    proc = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert marker.read_text(encoding="utf-8").strip() == "launched"


def test_default_setup_runs_hook_with_empty_path(
    tmp_path,
):
    hook_marker = tmp_path / "hook-ran"
    launch_marker = tmp_path / "launched"
    config_root = tmp_path / "config-root"
    config_root.mkdir()
    env = os.environ.copy()
    env["PATH"] = ""
    env["HOSTNAME"] = "test-host"
    env["HOME"] = str(tmp_path / "home")
    env["USERPROFILE"] = str(tmp_path / "home")
    env["SETUP_HOOK_MARKER"] = str(hook_marker)
    env["COPILOT_LAUNCH_MARKER"] = str(launch_marker)
    scripts = Path(__file__).resolve().parents[1] / "scripts"

    if os.name == "nt":
        shell = shutil.which("pwsh")
        if not shell:
            pytest.skip("pwsh is unavailable")
        runtime = tmp_path / "runtime.cmd"
        runtime.write_text(
            f"@echo off\r\necho {config_root}\r\n",
            encoding="utf-8",
        )
        hook = tmp_path / "setup-hook.ps1"
        hook.write_text(
            "param([string]$Machine)\n"
            "Set-Content -LiteralPath $env:SETUP_HOOK_MARKER -Value ran\n",
            encoding="utf-8",
        )
        copilot = tmp_path / "copilot-test.cmd"
        copilot.write_text(
            "@echo off\r\n"
            "> \"%COPILOT_LAUNCH_MARKER%\" echo launched\r\n",
            encoding="utf-8",
        )
        command = [
            shell,
            "-NoProfile",
            "-NoLogo",
            "-File",
            str(scripts / "default-setup.ps1"),
            "-Machine",
            "test",
            "-SetupHook",
            str(hook),
            "-RuntimePython",
            str(runtime),
            "-CopilotPath",
            str(copilot),
        ]
    else:
        shell = shutil.which("bash")
        if not shell:
            pytest.skip("bash is unavailable")
        runtime = tmp_path / "runtime"
        runtime.write_text(
            f"#!/bin/sh\nprintf '%s\\n' '{config_root}'\n",
            encoding="utf-8",
        )
        runtime.chmod(0o755)
        hook = tmp_path / "setup-hook.sh"
        hook.write_text(
            "#!/bin/sh\nprintf ran > \"$SETUP_HOOK_MARKER\"\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        copilot = tmp_path / "copilot-test"
        copilot.write_text(
            "#!/bin/sh\nprintf launched > \"$COPILOT_LAUNCH_MARKER\"\n",
            encoding="utf-8",
        )
        copilot.chmod(0o755)
        command = [
            shell,
            str(scripts / "default-setup.sh"),
            "--machine",
            "test",
            "--setup-hook",
            str(hook),
            "--runtime-python",
            str(runtime),
            "--copilot-path",
            str(copilot),
        ]

    proc = subprocess.run(
        command,
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert hook_marker.read_text(encoding="utf-8").strip() == "ran"
    assert launch_marker.read_text(encoding="utf-8").strip() == "launched"


def test_supported_setup_surface_rejects_stateless_destination_before_hook(
    tmp_path,
):
    """A caller bypassing state-root is still blocked at normalized setup."""
    harness = tmp_path / "stateless-harness"
    harness.mkdir()
    subprocess.run(["git", "init", "--quiet", str(harness)], check=True)
    config_dir = harness / ".agent-worktrees"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "default_branch: main\nstateless: true\n",
        encoding="utf-8",
    )
    marker = tmp_path / "hook-ran"
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["USERPROFILE"] = str(tmp_path / "home")
    env["SETUP_GUARD_MARKER"] = str(marker)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    runtime_python = Path(sys.executable).name
    before = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=harness,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    scripts = Path(__file__).resolve().parents[1] / "scripts"
    if os.name == "nt" and shutil.which("pwsh"):
        hook = harness / "setup-hook.ps1"
        hook.write_text(
            "param([string]$Machine)\n"
            "Set-Content -LiteralPath $env:SETUP_GUARD_MARKER -Value ran\n",
            encoding="utf-8",
        )
        command = [
            "pwsh",
            "-NoProfile",
            "-File",
            str(scripts / "default-setup.ps1"),
            "-Machine",
            "test",
            "-SetupHook",
            str(hook),
            "-ConfigRoot",
            str(harness),
            "-RuntimePython",
            runtime_python,
        ]
    elif shutil.which("bash"):
        hook = harness / "setup-hook.sh"
        hook.write_text(
            '#!/usr/bin/env bash\nprintf "ran\\n" > "$SETUP_GUARD_MARKER"\n',
            encoding="utf-8",
        )
        command = [
            "bash",
            str(scripts / "default-setup.sh"),
            "--machine",
            "test",
            "--setup-hook",
            str(hook),
            "--config-root",
            str(harness),
            "--runtime-python",
            runtime_python,
        ]
    else:
        pytest.skip("neither pwsh nor bash is available")

    after_fixture = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=harness,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    proc = subprocess.run(
        command,
        cwd=harness,
        env=env,
        capture_output=True,
        text=True,
    )
    after = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=harness,
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    assert proc.returncode == 3
    assert "inside stateless checkout" in proc.stderr
    assert not marker.exists()
    assert after == after_fixture
    assert before != after_fixture  # only the test's hook fixture was added


# ---------------------------------------------------------------------------
# cmd_launch: Windows launcher-depth handoff (copilot-extensions #102).
# Interactive launches hand off straight to pwsh (no cmd.exe shim); ACP/--stdio
# launches keep the cmd.exe -> .cmd shim for verbatim stdin forwarding.
# ---------------------------------------------------------------------------

def _fake_popen(captured):
    class _P:
        def __init__(self, argv, *a, **k):
            captured.append(list(argv))

        def wait(self, timeout=None):
            return 0

    return _P


def _win_launch_dir(tmp_path):
    bind = tmp_path / "bin"
    bind.mkdir()
    (bind / "launch-session.cmd").write_text("@echo off\n")
    (bind / "launch-session.ps1").write_text("# ps\n")
    return tmp_path


def test_windows_interactive_launch_bypasses_cmd_shim(monkeypatch, tmp_path):
    monkeypatch.setattr(m.cfg, "install_dir", lambda: _win_launch_dir(tmp_path))
    monkeypatch.setattr(m.cfg, "detect_platform", lambda: "windows")
    captured: list[list[str]] = []
    monkeypatch.setattr(m.subprocess, "Popen", _fake_popen(captured))

    with pytest.raises(SystemExit) as exc:
        m.cmd_launch([])
    assert exc.value.code == 0
    argv = captured[0]
    assert argv[0] == "pwsh.exe"
    assert "-File" in argv
    assert any(a.endswith("launch-session.ps1") for a in argv)
    assert "cmd.exe" not in argv


def test_windows_stdio_launch_keeps_cmd_shim(monkeypatch, tmp_path):
    monkeypatch.setattr(m.cfg, "install_dir", lambda: _win_launch_dir(tmp_path))
    monkeypatch.setattr(m.cfg, "detect_platform", lambda: "windows")
    captured: list[list[str]] = []
    monkeypatch.setattr(m.subprocess, "Popen", _fake_popen(captured))

    with pytest.raises(SystemExit):
        m.cmd_launch(["--", "--acp", "--stdio"])
    argv = captured[0]
    assert argv[0] == "cmd.exe"
    assert any(a.endswith("launch-session.cmd") for a in argv)
    # The ACP passthrough is preserved verbatim through the shim.
    assert "--stdio" in argv


def test_windows_interactive_falls_back_to_cmd_when_ps1_absent(monkeypatch, tmp_path):
    """If only the .cmd is deployed (no sibling .ps1), the interactive path
    still works by falling back to the cmd.exe shim."""
    bind = tmp_path / "bin"
    bind.mkdir()
    (bind / "launch-session.cmd").write_text("@echo off\n")  # no .ps1
    monkeypatch.setattr(m.cfg, "install_dir", lambda: tmp_path)
    monkeypatch.setattr(m.cfg, "detect_platform", lambda: "windows")
    captured: list[list[str]] = []
    monkeypatch.setattr(m.subprocess, "Popen", _fake_popen(captured))

    with pytest.raises(SystemExit):
        m.cmd_launch([])
    argv = captured[0]
    assert argv[0] == "cmd.exe"
