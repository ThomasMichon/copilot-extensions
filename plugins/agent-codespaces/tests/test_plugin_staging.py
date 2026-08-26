"""Tests for egress-free plugin staging helpers."""

from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path

from agent_codespaces import plugin_staging as ps


def _make_payload(root: Path, mkt: str, name: str, *, claude_layout: bool = False) -> Path:
    d = root / "installed-plugins" / mkt / name
    (d / "skills" / "demo").mkdir(parents=True)
    if claude_layout:
        (d / ".claude-plugin").mkdir(parents=True)
        (d / ".claude-plugin" / "plugin.json").write_text(
            '{"name": "%s"}' % name, encoding="utf-8"
        )
    else:
        (d / "plugin.json").write_text('{"name": "%s"}' % name, encoding="utf-8")
    (d / "skills" / "demo" / "SKILL.md").write_text("hi", encoding="utf-8")
    return d


def _make_local_payload(repo: Path, mkt: str, name: str) -> Path:
    marketplace = repo / ".ai"
    plugin = marketplace / name
    (marketplace / ".claude-plugin").mkdir(parents=True)
    plugin.mkdir(parents=True)
    (marketplace / ".claude-plugin" / "marketplace.json").write_text(
        '{"name": "%s", "plugins": [{"name": "%s", "source": "./%s"}]}'
        % (mkt, name, name),
        encoding="utf-8",
    )
    (plugin / "plugin.json").write_text(
        '{"name": "%s"}' % name, encoding="utf-8"
    )
    settings = repo / ".github" / "copilot"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text(
        '{"extraKnownMarketplaces": {"%s": {"source": '
        '{"source": "directory", "path": "./.ai"}}}}' % mkt,
        encoding="utf-8",
    )
    return plugin


def test_parse_source():
    assert ps.parse_source("example-web-codespace@example-marketplace") == (
        "example-web-codespace", "example-marketplace",
    )
    assert ps.parse_source("noat") is None
    assert ps.parse_source("@only") is None
    assert ps.parse_source("name@") is None


def test_dest_dir_sanitizes_and_roots():
    assert ps.dest_dir("example-web@mkt") == "$HOME/.acp-staged-plugins/example-web"
    assert ps.dest_dir("weird/../name@m").startswith("$HOME/.acp-staged-plugins/")
    assert "/" not in ps.dest_dir("weird/../name@m").rsplit("/", 1)[1]


def test_host_payload_dir_direct(tmp_path: Path):
    _make_payload(tmp_path, "example-marketplace", "example-web-codespace")
    got = ps.host_payload_dir("example-web-codespace@example-marketplace", copilot_home=tmp_path)
    assert got == tmp_path / "installed-plugins" / "example-marketplace" / "example-web-codespace"


def test_host_payload_dir_scan_fallback(tmp_path: Path):
    # Source marketplace suffix differs from the actual marketplace dir; the
    # scan-by-name fallback still finds it.
    _make_payload(tmp_path, "actual-mkt", "myplugin")
    got = ps.host_payload_dir("myplugin@some-alias", copilot_home=tmp_path)
    assert got == tmp_path / "installed-plugins" / "actual-mkt" / "myplugin"


def test_host_payload_dir_missing(tmp_path: Path):
    assert ps.host_payload_dir("nope@mkt", copilot_home=tmp_path) is None


def test_host_payload_dir_claude_layout(tmp_path: Path):
    # A local ``.ai`` marketplace plugin carries its manifest at
    # ``.claude-plugin/plugin.json``; host_payload_dir must still find it.
    _make_payload(tmp_path, "dotfiles-plugins", "figma", claude_layout=True)
    got = ps.host_payload_dir("figma@dotfiles-plugins", copilot_home=tmp_path)
    assert got == tmp_path / "installed-plugins" / "dotfiles-plugins" / "figma"


def test_host_payload_dir_claude_layout_scan_fallback(tmp_path: Path):
    _make_payload(tmp_path, "actual-mkt", "figma", claude_layout=True)
    got = ps.host_payload_dir("figma@some-alias", copilot_home=tmp_path)
    assert got == tmp_path / "installed-plugins" / "actual-mkt" / "figma"


def test_host_payload_dir_prefers_repo_local_marketplace(tmp_path: Path):
    repo = tmp_path / "repo"
    local = _make_local_payload(repo, "dotfiles-plugins", "figma")
    _make_payload(tmp_path, "dotfiles-plugins", "figma")
    got = ps.host_payload_dir(
        "figma@dotfiles-plugins",
        copilot_home=tmp_path,
        repo_roots=[repo],
    )
    assert got == local


def test_host_payload_dir_honors_first_marketplace_definition(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _make_local_payload(first, "dotfiles-plugins", "other")
    _make_local_payload(second, "dotfiles-plugins", "figma")
    got = ps.host_payload_dir(
        "figma@dotfiles-plugins",
        copilot_home=tmp_path / "home",
        repo_roots=[first, second],
    )
    assert got is None


def test_local_marketplace_claim_blocks_stale_installed_fallback(tmp_path: Path):
    first = tmp_path / "first"
    _make_local_payload(first, "dotfiles-plugins", "other")
    stale = _make_payload(tmp_path, "dotfiles-plugins", "figma")
    assert stale.is_dir()
    got = ps.host_payload_dir(
        "figma@dotfiles-plugins",
        copilot_home=tmp_path,
        repo_roots=[first],
    )
    assert got is None


def test_host_payload_dir_rejects_plugin_outside_marketplace(tmp_path: Path):
    repo = tmp_path / "repo"
    marketplace = repo / ".ai"
    outside = repo / "outside"
    (marketplace / ".claude-plugin").mkdir(parents=True)
    outside.mkdir()
    (outside / "plugin.json").write_text('{"name": "figma"}', encoding="utf-8")
    (marketplace / ".claude-plugin" / "marketplace.json").write_text(
        '{"name": "dotfiles-plugins", "plugins": '
        '[{"name": "figma", "source": "../outside"}]}',
        encoding="utf-8",
    )
    settings = repo / ".github" / "copilot"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text(
        '{"extraKnownMarketplaces": {"dotfiles-plugins": {"source": '
        '{"source": "directory", "path": "./.ai"}}}}',
        encoding="utf-8",
    )
    assert (
        ps.host_payload_dir(
            "figma@dotfiles-plugins",
            copilot_home=tmp_path / "home",
            repo_roots=[repo],
        )
        is None
    )


def test_host_payload_dir_rejects_absolute_marketplace_outside_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    marketplace = tmp_path / "external-marketplace"
    plugin = marketplace / "figma"
    (marketplace / ".claude-plugin").mkdir(parents=True)
    plugin.mkdir()
    (plugin / "plugin.json").write_text('{"name": "figma"}', encoding="utf-8")
    (marketplace / ".claude-plugin" / "marketplace.json").write_text(
        '{"name": "dotfiles-plugins", "plugins": '
        '[{"name": "figma", "source": "./figma"}]}',
        encoding="utf-8",
    )
    settings = repo / ".github" / "copilot"
    settings.mkdir(parents=True)
    (settings / "settings.json").write_text(
        '{"extraKnownMarketplaces": {"dotfiles-plugins": {"source": '
        '{"source": "directory", "path": "%s"}}}}'
        % str(marketplace).replace("\\", "\\\\"),
        encoding="utf-8",
    )
    assert (
        ps.host_payload_dir(
            "figma@dotfiles-plugins",
            copilot_home=tmp_path / "home",
            repo_roots=[repo],
        )
        is None
    )


def test_build_stage_command_roundtrips(tmp_path: Path):
    payload = _make_payload(tmp_path, "mkt", "p")
    dest = ps.dest_dir("p@mkt")
    cmd, payload_b64 = ps.build_stage_command(payload, dest)
    # Command shape: recreate dest, then decode+extract the tarball read from STDIN.
    assert cmd.startswith(f'rm -rf "{dest}" && mkdir -p "{dest}" && ')
    assert cmd.endswith(f'base64 -d | tar -xzf - -C "{dest}"')
    # The payload travels on stdin, NOT embedded in the command string (so a large
    # plugin never overruns the Windows ~32 KB command-line limit / WinError 206).
    assert "printf" not in cmd
    assert payload_b64.decode("ascii").isprintable()
    # Confirm the stdin payload faithfully reproduces the tree (arcname='.').
    raw = base64.b64decode(payload_b64)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        names = set(tf.getnames())
    assert "./plugin.json" in names
    assert "./skills/demo/SKILL.md" in names


def test_build_stage_command_excludes_local_junk_and_secrets(tmp_path: Path):
    payload = _make_payload(tmp_path, "mkt", "p")
    (payload / ".env").write_text("SECRET=value", encoding="utf-8")
    (payload / ".env.example").write_text("SECRET=", encoding="utf-8")
    cache = payload / "node_modules" / "package"
    cache.mkdir(parents=True)
    (cache / "index.js").write_text("junk", encoding="utf-8")
    _cmd, payload_b64 = ps.build_stage_command(payload, ps.dest_dir("p@mkt"))
    with tarfile.open(
        fileobj=io.BytesIO(base64.b64decode(payload_b64)), mode="r:gz"
    ) as tf:
        names = set(tf.getnames())
    assert "./.env" not in names
    assert "./.env.example" in names
    assert not any("node_modules" in name for name in names)


def test_build_stage_command_large_payload_keeps_command_tiny(tmp_path: Path):
    """A large plugin must NOT bloat the command string -- the payload rides on
    stdin, so the command stays far below the Windows ~32 KB command-line limit
    (the cause of the ``[WinError 206]`` staging failures)."""
    payload = _make_payload(tmp_path, "mkt", "big")
    # ~1.5 MB of incompressible data across several files.
    import os

    for i in range(6):
        (payload / f"blob{i}.bin").write_bytes(os.urandom(256 * 1024))
    dest = ps.dest_dir("big@mkt")
    cmd, payload_b64 = ps.build_stage_command(payload, dest)
    # The command carries none of the payload -- only the fixed extract pipeline.
    assert len(cmd) < 1024
    # The payload (on stdin) is large; that's fine because it never hits argv.
    assert len(payload_b64) > 200_000
