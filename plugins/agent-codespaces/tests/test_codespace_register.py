"""Tests for the CodeSpace-scoped plugin register lane (global lane).

Covers payload construction, command assembly, and a functional run of the
embedded settings-merge script against a temp HOME.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from agent_codespaces import codespace_register as cr
from agent_codespaces.codespace_plugins import CodespacePluginSpec

MKTS = {
    "example-marketplace": {"source": {"source": "git", "url": "https://example/example-marketplace"}},
    "other": {"source": {"source": "github", "repo": "o/other"}},
}


def _spec(source: str, enable: bool = True) -> CodespacePluginSpec:
    return CodespacePluginSpec(source=source, enable=enable)


# --------------------------------------------------------------------------
# codespace_plugin_dirs (the --acp dispatch lane)
# --------------------------------------------------------------------------

def test_plugin_dirs_maps_enabled_marketplace_specs():
    dirs = cr.codespace_plugin_dirs(
        [_spec("example-web-agent@example-marketplace"), _spec("b@other")]
    )
    assert dirs == [
        "$HOME/.copilot/installed-plugins/example-marketplace/example-web-agent",
        "$HOME/.copilot/installed-plugins/other/b",
    ]


def test_plugin_dirs_skips_disabled_and_non_marketplace():
    dirs = cr.codespace_plugin_dirs(
        [
            _spec("a@example-marketplace", enable=False),   # install-only -> skip
            _spec("bare-source"),                    # not name@mkt -> skip
            _spec("c@example-marketplace"),                  # kept
        ]
    )
    assert dirs == ["$HOME/.copilot/installed-plugins/example-marketplace/c"]


def test_plugin_dirs_dedups():
    dirs = cr.codespace_plugin_dirs(
        [_spec("a@example-marketplace"), _spec("a@example-marketplace")]
    )
    assert dirs == ["$HOME/.copilot/installed-plugins/example-marketplace/a"]


# --------------------------------------------------------------------------
# build_register_payload
# --------------------------------------------------------------------------

def test_payload_collects_referenced_marketplaces_only():
    payload = cr.build_register_payload(
        [_spec("example-web-agent@example-marketplace")], MKTS,
    )
    assert payload["experimental"] is True
    assert list(payload["marketplaces"]) == ["example-marketplace"]
    assert payload["plugins"] == [
        {"source": "example-web-agent@example-marketplace", "enable": True},
    ]


def test_payload_dedups_by_source_and_keeps_enable():
    payload = cr.build_register_payload(
        [
            _spec("a@example-marketplace", enable=True),
            _spec("a@example-marketplace", enable=True),
            _spec("b@other", enable=False),
        ],
        MKTS,
    )
    sources = [p["source"] for p in payload["plugins"]]
    assert sources == ["a@example-marketplace", "b@other"]
    assert payload["plugins"][1]["enable"] is False
    assert set(payload["marketplaces"]) == {"example-marketplace", "other"}


def test_payload_skips_unknown_marketplace():
    payload = cr.build_register_payload([_spec("x@ghost")], MKTS)
    assert payload["marketplaces"] == {}
    assert payload["plugins"] == [{"source": "x@ghost", "enable": True}]


# --------------------------------------------------------------------------
# build_register_command
# --------------------------------------------------------------------------

def test_command_none_for_empty():
    assert cr.build_register_command([], MKTS) is None


def test_command_includes_merge_and_installs():
    cmd = cr.build_register_command(
        [_spec("example-web-agent@example-marketplace"), _spec("b@other", enable=False)],
        MKTS,
    )
    assert cmd is not None
    # base64-transported payload + script, then the merge under python3.
    assert "base64 -d" in cmd
    assert "python3" in cmd
    # exit code tracks the merge; installs are guarded on rc==0.
    assert "rc=$?" in cmd
    assert "if [ $rc -eq 0 ]" in cmd
    # both plugins are pre-installed (payload warming), best-effort.
    assert "copilot plugin install example-web-agent@example-marketplace || true" in cmd
    assert "copilot plugin install b@other || true" in cmd
    # temp files always cleaned up.
    assert "rm -f" in cmd


def test_command_no_install_when_disabled():
    cmd = cr.build_register_command(
        [_spec("a@example-marketplace")], MKTS, do_install=False,
    )
    assert cmd is not None
    assert "copilot plugin install" not in cmd
    assert "python3" in cmd


def test_command_reads_host_marketplaces_when_omitted(tmp_path: Path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"extraKnownMarketplaces": MKTS}), encoding="utf-8",
    )
    cmd = cr.build_register_command(
        [_spec("a@example-marketplace")], copilot_home=tmp_path,
    )
    assert cmd is not None
    assert "python3" in cmd


# --------------------------------------------------------------------------
# host_marketplaces
# --------------------------------------------------------------------------

def test_host_marketplaces_reads(tmp_path: Path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"extraKnownMarketplaces": MKTS}), encoding="utf-8",
    )
    assert cr.host_marketplaces(copilot_home=tmp_path) == MKTS


def test_host_marketplaces_absent(tmp_path: Path):
    assert cr.host_marketplaces(copilot_home=tmp_path) == {}


# --------------------------------------------------------------------------
# Functional: run the embedded merge script against a temp HOME
# --------------------------------------------------------------------------

def _run_merge(home: Path, payload: dict) -> dict:
    """Execute the embedded merge script with HOME pointed at ``home``."""
    script = home / "merge.py"
    script.write_text(cr._MERGE_SCRIPT, encoding="utf-8")
    payload_path = home / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)  # Windows expanduser
    # Neutralise HOMEDRIVE/HOMEPATH so expanduser("~") == home on Windows.
    env.pop("HOMEDRIVE", None)
    env.pop("HOMEPATH", None)
    res = subprocess.run(
        [sys.executable, str(script), str(payload_path)],
        env=env, capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    settings = home / ".copilot" / "settings.json"
    return json.loads(settings.read_text(encoding="utf-8"))


def test_merge_script_writes_fresh_settings(tmp_path: Path):
    payload = cr.build_register_payload(
        [_spec("example-web-agent@example-marketplace"), _spec("b@other", enable=False)],
        MKTS,
    )
    data = _run_merge(tmp_path, payload)
    assert data["experimental"] is True
    assert data["extraKnownMarketplaces"]["example-marketplace"] == MKTS["example-marketplace"]
    assert data["enabledPlugins"] == {"example-web-agent@example-marketplace": True}


def test_merge_script_preserves_existing_and_merges(tmp_path: Path):
    copilot = tmp_path / ".copilot"
    copilot.mkdir()
    (copilot / "settings.json").write_text(
        json.dumps({
            "model": "keep-me",
            "enabledPlugins": {"pre@existing": True},
            "extraKnownMarketplaces": {"pre": {"source": {"source": "github", "repo": "p/pre"}}},
        }),
        encoding="utf-8",
    )
    payload = cr.build_register_payload([_spec("a@example-marketplace")], MKTS)
    data = _run_merge(tmp_path, payload)
    # Pre-existing settings untouched.
    assert data["model"] == "keep-me"
    assert data["enabledPlugins"]["pre@existing"] is True
    assert "pre" in data["extraKnownMarketplaces"]
    # New enablement + marketplace merged in.
    assert data["enabledPlugins"]["a@example-marketplace"] is True
    assert data["extraKnownMarketplaces"]["example-marketplace"] == MKTS["example-marketplace"]


def test_merge_script_tolerates_garbage_settings(tmp_path: Path):
    copilot = tmp_path / ".copilot"
    copilot.mkdir()
    (copilot / "settings.json").write_text("not json {{{", encoding="utf-8")
    payload = cr.build_register_payload([_spec("a@example-marketplace")], MKTS)
    data = _run_merge(tmp_path, payload)
    assert data["enabledPlugins"] == {"a@example-marketplace": True}
    assert data["experimental"] is True
