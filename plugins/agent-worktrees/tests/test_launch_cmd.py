"""Tests for _build_launch_cmd: tool auto-approval and resume arg form."""

from __future__ import annotations

import argparse

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


def test_plain_launch_appends_allow_all_tools():
    cmd = m._build_launch_cmd(_config(), _args([]), "/w/wt")
    assert cmd[-1] == "--allow-all-tools"


def test_acp_launch_skips_allow_all_tools():
    cmd = m._build_launch_cmd(_config(), _args(["--acp", "--stdio"]), "/w/wt")
    assert "--allow-all-tools" not in cmd


def test_existing_all_perm_flag_not_duplicated():
    # --allow-all and --yolo already imply --allow-all-tools, so we must not
    # append a redundant one; an explicit --allow-all-tools is also not doubled.
    for flag in ("--allow-all-tools", "--allow-all", "--yolo"):
        cmd = m._build_launch_cmd(_config(), _args([flag]), "/w/wt")
        assert cmd.count("--allow-all-tools") == (1 if flag == "--allow-all-tools" else 0)


def test_resume_uses_equals_form():
    # copilot's --resume[=value] is an optional-value option; the id must be
    # attached with '=' or copilot treats it as a stray operand.
    cmd = m._build_launch_cmd(_config(), _args([]), "/w/wt")
    session = "46fa3c70-42d3-47b3-b60d-e472ef36c5d5"
    cmd.append(f"--resume={session}")
    assert f"--resume={session}" in cmd
    assert "--resume" not in cmd  # bare flag must not appear separately
