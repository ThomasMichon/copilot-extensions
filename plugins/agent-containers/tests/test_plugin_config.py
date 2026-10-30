"""Plugin-shipped containers.yaml fleet fragments (``containersConfig``)."""

from __future__ import annotations

import json
import logging
import textwrap
from pathlib import Path

import pytest

from agent_containers import __main__ as cli
from agent_containers import config as config_mod
from agent_containers import plugin_config
from agent_containers.config import load_config


def _plugin(root: Path, fragment: str | None, *, declared: str = "containers.yaml") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"name": root.name}
    if fragment is not None:
        manifest[plugin_config.MANIFEST_FIELD] = declared
        target = root / declared
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(fragment), encoding="utf-8")
    (root / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


EXAMPLE_FRAGMENT = """\
    schema_version: 2
    fleets:
      example:
        repo: example-org/example
        devcontainer_path: devcontainer
        workspace_folder: /workspaces/example
        exec_user: dev
        size: 1
        environment:
          EXAMPLE_MODE: shipped
          EXAMPLE_LEVEL: "1"
    """


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """No operator config, no knowledge overlay, and an explicit plugin set."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
    monkeypatch.setattr(config_mod, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config_mod, "_knowledge_overlay_config", lambda: None)
    roots: list[tuple[str, list[Path]]] = []
    monkeypatch.setattr(plugin_config, "_active_plugin_roots", lambda: list(roots))
    return roots


def _operator_config(tmp_path, monkeypatch, body: str) -> Path:
    path = tmp_path / "operator" / "containers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    return path


def test_no_fragments_leaves_existing_behavior_unchanged(tmp_path, monkeypatch, isolated):
    path = _operator_config(tmp_path, monkeypatch, """\
        schema_version: 2
        fleets:
          own:
            image: example:latest
    """)
    cfg = load_config()
    assert list(cfg.fleets) == ["own"]
    assert cfg.copilot_auth_mode == "host-copilot"
    assert cfg.provenance["config_file"] == str(path)
    assert cfg.provenance["plugin_fragments"] == []
    assert cfg.provenance["fleets"]["own"]["defined_by"] == f"config:{path}"


def test_fragment_alone_supplies_a_fleet_with_paths_relative_to_fragment(tmp_path, isolated):
    root = _plugin(tmp_path / "plugins" / "example-harness", EXAMPLE_FRAGMENT,
                   declared="config/containers.yaml")
    isolated.append(("example-harness@example", [root]))

    cfg = load_config()

    fleet = cfg.fleets["example"]
    assert fleet.repo == "example-org/example"
    assert Path(fleet.devcontainer_path) == root / "config" / "devcontainer"
    assert fleet.environment == {"EXAMPLE_MODE": "shipped", "EXAMPLE_LEVEL": "1"}
    # No operator file: built-in defaults are not migrated to legacy auth.
    assert cfg.copilot_auth_mode == "host-copilot"
    entry = cfg.provenance["fleets"]["example"]
    assert entry["defined_by"] == "plugin:example-harness@example"
    assert entry["overridden_by"] is None
    assert cfg.provenance["plugin_fragments"][0]["fleets"] == ["example"]


def test_operator_config_overrides_per_key_including_nested_mappings(
    tmp_path, monkeypatch, isolated,
):
    root = _plugin(tmp_path / "plugins" / "example-harness", EXAMPLE_FRAGMENT)
    isolated.append(("example-harness@example", [root]))
    path = _operator_config(tmp_path, monkeypatch, """\
        schema_version: 2
        dotfiles:
          repo: ~/dotfiles
        fleets:
          example:
            size: 3
            exec_user: null
            environment:
              EXAMPLE_LEVEL: "2"
    """)

    cfg = load_config()

    fleet = cfg.fleets["example"]
    assert fleet.size == 3
    assert fleet.exec_user is None
    assert fleet.workspace_folder == "/workspaces/example"
    assert fleet.environment == {"EXAMPLE_MODE": "shipped", "EXAMPLE_LEVEL": "2"}
    assert cfg.dotfiles is not None and cfg.dotfiles.repo == "~/dotfiles"
    entry = cfg.provenance["fleets"]["example"]
    operator = f"config:{path}"
    plugin = "plugin:example-harness@example"
    assert entry["overridden_by"] == operator
    assert entry["keys"]["size"] == operator
    assert entry["keys"]["exec_user"] == operator
    assert entry["keys"]["environment.EXAMPLE_LEVEL"] == operator
    assert entry["keys"]["environment.EXAMPLE_MODE"] == plugin
    assert entry["keys"]["workspace_folder"] == plugin


def test_unknown_nested_mapping_keys_merge_per_key(tmp_path, monkeypatch, isolated):
    root = _plugin(tmp_path / "plugins" / "example-harness", """\
        fleets:
          example:
            image: example:latest
            future_options:
              alpha: 1
              beta: 2
    """)
    isolated.append(("example-harness@example", [root]))
    _operator_config(tmp_path, monkeypatch, """\
        schema_version: 2
        fleets:
          example:
            future_options:
              beta: 3
    """)
    data, provenance = plugin_config.merge_fragments(
        {"fleets": {"example": {"future_options": {"beta": 3}}}},
        plugin_config.discover_plugin_fragments(),
        config_path=None,
    )
    assert data["fleets"]["example"]["future_options"] == {"alpha": 1, "beta": 3}
    assert provenance["fleets"]["example"]["keys"]["future_options.alpha"] == (
        "plugin:example-harness@example"
    )
    # Unknown keys are not rejected by the loader.
    assert load_config().fleets["example"].image == "example:latest"


def test_duplicate_fleet_first_plugin_source_wins_wholesale(tmp_path, isolated, caplog):
    first = _plugin(tmp_path / "plugins" / "alpha", """\
        fleets:
          example:
            image: alpha:latest
    """)
    second = _plugin(tmp_path / "plugins" / "beta", """\
        fleets:
          example:
            image: beta:latest
            size: 4
          other:
            image: other:latest
    """)
    # Registration order must not matter: source order decides.
    isolated.extend([("beta@example", [second]), ("alpha@example", [first])])

    with caplog.at_level(logging.WARNING, logger="agent-containers"):
        cfg = load_config()

    assert cfg.fleets["example"].image == "alpha:latest"
    assert cfg.fleets["example"].size == 1
    assert cfg.fleets["other"].image == "other:latest"
    assert cfg.provenance["fleets"]["example"]["defined_by"] == "plugin:alpha@example"
    assert "defined by both plugin:alpha@example and plugin:beta@example" in caplog.text


def test_fragment_top_level_keys_other_than_fleets_are_ignored(tmp_path, isolated, caplog):
    root = _plugin(tmp_path / "plugins" / "example-harness", """\
        exec_user: root
        dotfiles:
          repo: /plugin/dotfiles
        fleets:
          example:
            image: example:latest
    """)
    isolated.append(("example-harness@example", [root]))

    with caplog.at_level(logging.WARNING, logger="agent-containers"):
        cfg = load_config()

    assert cfg.exec_user == "vscode"
    assert cfg.dotfiles is None
    assert "example" in cfg.fleets
    assert "ignoring: dotfiles, exec_user" in caplog.text


@pytest.mark.parametrize("declared", ["/abs/containers.yaml", "../escape.yaml", "", 7])
def test_invalid_declarations_are_skipped(tmp_path, isolated, declared):
    root = tmp_path / "plugins" / "example-harness"
    root.mkdir(parents=True)
    (tmp_path / "plugins" / "escape.yaml").write_text("fleets: {x: {image: x}}\n")
    (root / "plugin.json").write_text(
        json.dumps({"name": "example-harness", plugin_config.MANIFEST_FIELD: declared})
    )
    isolated.append(("example-harness@example", [root]))
    assert load_config(strict=True).fleets == {}


def test_invalid_fragment_yaml_does_not_break_operator_config(tmp_path, monkeypatch, isolated):
    root = _plugin(tmp_path / "plugins" / "example-harness", "fleets: [not, a, mapping]\n")
    isolated.append(("example-harness@example", [root]))
    _operator_config(tmp_path, monkeypatch, """\
        schema_version: 2
        fleets:
          own:
            image: own:latest
    """)
    cfg = load_config(strict=True)
    assert list(cfg.fleets) == ["own"]


def test_fragment_values_go_through_existing_fleet_validation(tmp_path, isolated):
    root = _plugin(tmp_path / "plugins" / "example-harness", """\
        fleets:
          example:
            image: example:latest
            security_profile: bogus
    """)
    isolated.append(("example-harness@example", [root]))
    with pytest.raises(RuntimeError, match="invalid security_profile"):
        load_config()


def test_discovery_uses_the_active_plugin_activation_graph(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    installed = home / ".copilot" / "installed-plugins" / "example" / "example-harness"
    _plugin(installed, EXAMPLE_FRAGMENT)
    _plugin(home / ".copilot" / "installed-plugins" / "example" / "disabled", EXAMPLE_FRAGMENT)
    settings = home / ".copilot" / "settings.json"
    settings.write_text(json.dumps({
        "enabledPlugins": {"example-harness@example": True, "disabled@example": False},
    }), encoding="utf-8")

    fragments = plugin_config.discover_plugin_fragments()

    assert [f.plugin for f in fragments] == ["example-harness@example"]
    assert fragments[0].path == (installed / "containers.yaml").resolve()


def test_config_sources_cli_reports_provenance(tmp_path, monkeypatch, isolated, capsys):
    root = _plugin(tmp_path / "plugins" / "example-harness", EXAMPLE_FRAGMENT)
    isolated.append(("example-harness@example", [root]))
    path = _operator_config(tmp_path, monkeypatch, """\
        schema_version: 2
        fleets:
          example:
            size: 2
    """)

    assert cli.main(["config-sources", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["fleets"]["example"]["keys"]["size"] == f"config:{path}"

    assert cli.main(["config-sources"]) == 0
    text = capsys.readouterr().out
    assert "example: defined by plugin:example-harness@example" in text
    assert f"size: config:{path}" in text
