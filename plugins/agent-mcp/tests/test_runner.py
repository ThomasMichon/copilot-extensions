from __future__ import annotations

from agent_mcp.runner import resolve_npm_command


def _which_with(*present: str):
    """A fake ``shutil.which`` that only finds the named binaries."""
    have = set(present)
    return lambda name: f"/usr/bin/{name}" if name in have else None


def test_prefers_bunx_when_present():
    argv = resolve_npm_command("gitea-mcp", which=_which_with("bunx", "npx"), env={})
    assert argv == ["bunx", "gitea-mcp"]


def test_falls_back_to_npx_when_no_bunx():
    argv = resolve_npm_command("gitea-mcp", which=_which_with("npx"), env={})
    assert argv == ["npx", "-y", "gitea-mcp"]


def test_default_npx_when_nothing_resolves():
    # Neutral default: even with nothing on PATH we emit `npx -y` so the spawn
    # raises a clear error with the standard runner name.
    argv = resolve_npm_command("gitea-mcp", which=_which_with(), env={})
    assert argv == ["npx", "-y", "gitea-mcp"]


def test_args_passthrough_after_package():
    argv = resolve_npm_command(
        "some-mcp", ["--flag", "val"], which=_which_with("bunx"), env={})
    assert argv == ["bunx", "some-mcp", "--flag", "val"]


def test_forced_runner_known_keeps_prefix():
    # Force npx even though bunx is present; npx keeps its `-y` prefix.
    argv = resolve_npm_command(
        "gitea-mcp",
        which=_which_with("bunx", "npx"),
        env={"AGENT_MCP_NPM_RUNNER": "npx"},
    )
    assert argv == ["npx", "-y", "gitea-mcp"]


def test_forced_runner_bunx_no_prefix():
    argv = resolve_npm_command(
        "gitea-mcp",
        which=_which_with("bunx"),
        env={"AGENT_MCP_NPM_RUNNER": "bunx"},
    )
    assert argv == ["bunx", "gitea-mcp"]


def test_forced_runner_unknown_used_bare():
    argv = resolve_npm_command(
        "gitea-mcp",
        ["x"],
        which=_which_with(),
        env={"AGENT_MCP_NPM_RUNNER": "pnpm-dlx-wrapper"},
    )
    assert argv == ["pnpm-dlx-wrapper", "gitea-mcp", "x"]


def test_empty_package_rejected():
    import pytest
    with pytest.raises(ValueError):
        resolve_npm_command("", which=_which_with("bunx"), env={})
