"""Tests for agent_worktrees.services -- extensions auto_update parsing."""

from __future__ import annotations

import json
from pathlib import Path

from agent_worktrees import services as svc

_BASE = (
    "name: {name}\n"
    "type: systemd\n"
    "deployments:\n"
    "  testenv:\n"
    "    type: full\n"
    "    install_dir: /opt/{name}\n"
)


def _write_service(repo: Path, name: str, extra: str = "") -> Path:
    d = repo / "services" / name
    d.mkdir(parents=True)
    (d / "service.yaml").write_text(_BASE.format(name=name) + extra, encoding="utf-8")
    return Path("services") / name / "service.yaml"


def test_auto_update_defaults_true(tmp_path: Path) -> None:
    yaml_path = _write_service(tmp_path, "alpha")
    info = svc._parse_service_yaml(yaml_path, tmp_path, "testenv")
    assert info is not None
    assert info.auto_update is True


def test_auto_update_false_when_flagged(tmp_path: Path) -> None:
    extra = (
        "extensions:\n"
        "  agent-worktrees:\n"
        "    auto_update: false\n"
        "  vav:\n"
        "    managed: true\n"
    )
    yaml_path = _write_service(tmp_path, "beta", extra)
    info = svc._parse_service_yaml(yaml_path, tmp_path, "testenv")
    assert info is not None
    assert info.auto_update is False


def test_ownership_model_default_empty(tmp_path: Path) -> None:
    yaml_path = _write_service(tmp_path, "gamma")
    info = svc._parse_service_yaml(yaml_path, tmp_path, "testenv")
    assert info is not None
    assert info.ownership_model == ""


def test_ownership_model_plugin(tmp_path: Path) -> None:
    extra = "extensions:\n  ownership:\n    model: plugin\n"
    yaml_path = _write_service(tmp_path, "delta", extra)
    info = svc._parse_service_yaml(yaml_path, tmp_path, "testenv")
    assert info is not None
    assert info.ownership_model == "plugin"


def test_is_copilot_plugin_name(tmp_path: Path, monkeypatch) -> None:
    from agent_worktrees import __main__ as m

    (tmp_path / ".copilot" / "installed-plugins" / "copilot-extensions" / "agent-bridge").mkdir(
        parents=True
    )
    monkeypatch.setattr(m.Path, "home", staticmethod(lambda: tmp_path))
    assert m._is_copilot_plugin_name("agent-bridge") is True
    assert m._is_copilot_plugin_name("not-a-plugin") is False


def test_plugin_managed_notice_returns_zero(capsys) -> None:
    from agent_worktrees import __main__ as m

    assert m._plugin_managed_notice("agent-dispatch") == 0


def test_auto_update_true_when_other_extension_only(tmp_path: Path) -> None:
    extra = "extensions:\n  some-other-tool:\n    whatever: true\n"
    yaml_path = _write_service(tmp_path, "gamma", extra)
    info = svc._parse_service_yaml(yaml_path, tmp_path, "testenv")
    assert info is not None
    assert info.auto_update is True


def test_non_bool_auto_update_falls_back_true(tmp_path: Path) -> None:
    extra = "extensions:\n  agent-worktrees:\n    auto_update: \"nope\"\n"
    yaml_path = _write_service(tmp_path, "delta", extra)
    info = svc._parse_service_yaml(yaml_path, tmp_path, "testenv")
    assert info is not None
    assert info.auto_update is True


# ---------------------------------------------------------------------------
# check_marketplace_staleness (#2174) -- a `marketplace` deploy never has a
# git commit to compare (check_staleness() above always reads "unknown" for
# it), so this content-fingerprint fallback is what lets a marketplace
# install's staleness ever resolve to "current" and stop re-running the full
# self-update pipeline on every single launch.
# ---------------------------------------------------------------------------

def test_check_marketplace_staleness_current_when_fingerprint_matches(
    tmp_path: Path,
) -> None:
    from agent_worktrees import update_stage as us

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"version":"1"}', encoding="utf-8")
    fp = us.fingerprint(plugin_dir)

    manifest_path = tmp_path / "deploy-manifest.json"
    manifest_path.write_text(
        json.dumps({"source": {"kind": "marketplace", "payload_fingerprint": fp}}),
        encoding="utf-8",
    )

    assert svc.check_marketplace_staleness(manifest_path, plugin_dir) == "current"


def test_check_marketplace_staleness_stale_when_content_changed(
    tmp_path: Path,
) -> None:
    from agent_worktrees import update_stage as us

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text('{"version":"1"}', encoding="utf-8")
    fp = us.fingerprint(plugin_dir)

    manifest_path = tmp_path / "deploy-manifest.json"
    manifest_path.write_text(
        json.dumps({"source": {"kind": "marketplace", "payload_fingerprint": fp}}),
        encoding="utf-8",
    )

    (plugin_dir / "plugin.json").write_text('{"version":"2"}', encoding="utf-8")

    assert (
        svc.check_marketplace_staleness(manifest_path, plugin_dir)
        == "stale:content-changed"
    )


def test_check_marketplace_staleness_unknown_when_no_recorded_fingerprint(
    tmp_path: Path,
) -> None:
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    manifest_path = tmp_path / "deploy-manifest.json"
    manifest_path.write_text(
        json.dumps({"source": {"kind": "marketplace"}}), encoding="utf-8"
    )
    assert svc.check_marketplace_staleness(manifest_path, plugin_dir) == "unknown"


def test_check_marketplace_staleness_unknown_when_manifest_missing(
    tmp_path: Path,
) -> None:
    assert (
        svc.check_marketplace_staleness(tmp_path / "nope.json", tmp_path)
        == "unknown"
    )


def test_check_marketplace_staleness_unknown_when_plugin_dir_missing(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "deploy-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {"source": {"kind": "marketplace", "payload_fingerprint": "abc123"}}
        ),
        encoding="utf-8",
    )
    assert (
        svc.check_marketplace_staleness(manifest_path, tmp_path / "gone")
        == "unknown"
    )
