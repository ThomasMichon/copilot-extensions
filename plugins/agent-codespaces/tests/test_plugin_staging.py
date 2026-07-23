"""Tests for egress-free plugin staging helpers."""

from __future__ import annotations

import base64
import io
import re
import tarfile
from pathlib import Path

from agent_codespaces import plugin_staging as ps


def _make_payload(root: Path, mkt: str, name: str) -> Path:
    d = root / "installed-plugins" / mkt / name
    (d / "skills" / "demo").mkdir(parents=True)
    (d / "plugin.json").write_text('{"name": "%s"}' % name, encoding="utf-8")
    (d / "skills" / "demo" / "SKILL.md").write_text("hi", encoding="utf-8")
    return d


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


def test_build_stage_command_roundtrips(tmp_path: Path):
    payload = _make_payload(tmp_path, "mkt", "p")
    dest = ps.dest_dir("p@mkt")
    cmd = ps.build_stage_command(payload, dest)
    # Command shape: recreate dest, then decode+extract the embedded tarball.
    assert cmd.startswith(f'rm -rf "{dest}" && mkdir -p "{dest}" && ')
    assert 'base64 -d | tar -xzf - -C' in cmd
    # Extract the embedded base64 tar and confirm the payload is faithfully
    # reproduced (arcname='.').
    m = re.search(r"printf %s (\S+) \| base64 -d", cmd)
    assert m
    raw = base64.b64decode(m.group(1))
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        names = set(tf.getnames())
    assert "./plugin.json" in names
    assert "./skills/demo/SKILL.md" in names
