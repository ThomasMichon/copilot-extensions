"""Tests for agent_worktrees.services -- extensions auto_update parsing."""

from __future__ import annotations

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
