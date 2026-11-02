"""Plugin-shipped containers.yaml fleet fragments (``containersConfig``)."""

from __future__ import annotations

import json
import logging
import os
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
    monkeypatch.setattr(plugin_config, "_active_plugin_roots", lambda *_: list(roots))
    return roots


def _operator_config(tmp_path, monkeypatch, body: str) -> Path:
    path = tmp_path / "operator" / "containers.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(path))
    return path


def _owner(cfg, pointer: str) -> str | None:
    owner = cfg.configuration_provenance.get(pointer)
    return owner.get("id") if owner else None


PLUGIN = "plugin:example-harness@example"


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
    assert cfg.plugin_fragments == []
    assert cfg.configuration_layers == []
    # The compatibility single-file path records no composed provenance.
    assert cfg.configuration_provenance == {}
    assert path.exists()


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
    assert cfg.fleet_source("example") == PLUGIN
    assert cfg.configuration_layers == [PLUGIN]
    assert cfg.plugin_fragments[0]["fleets"] == ["example"]
    owner = cfg.configuration_provenance["/fleets/example/repo"]
    assert owner["tier"] == "plugin"
    assert owner["source"] == {"root": str(root.resolve()), "path": "config/containers.yaml"}


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
    operator = str(path)
    assert cfg.fleet_source("example") == PLUGIN
    assert _owner(cfg, "/fleets/example/size") == operator
    assert _owner(cfg, "/fleets/example/exec_user") == operator
    assert _owner(cfg, "/fleets/example/environment/EXAMPLE_LEVEL") == operator
    assert _owner(cfg, "/fleets/example/environment/EXAMPLE_MODE") == PLUGIN
    assert _owner(cfg, "/fleets/example/workspace_folder") == PLUGIN


def test_null_override_clears_shipped_values_to_their_defaults(
    tmp_path, monkeypatch, isolated,
):
    root = _plugin(tmp_path / "plugins" / "example-harness", """\
        fleets:
          example:
            image: example:latest
            size: 4
            forward_gh_token: false
            environment:
              KEEP: "1"
              REMOVE: "2"
    """)
    isolated.append(("example-harness@example", [root]))
    path = _operator_config(tmp_path, monkeypatch, """\
        schema_version: 2
        fleets:
          example:
            size: null
            forward_gh_token: null
            environment:
              REMOVE: null
    """)

    cfg = load_config(strict=True)

    fleet = cfg.fleets["example"]
    assert fleet.size == 1
    assert fleet.forward_gh_token is None
    assert fleet.environment == {"KEEP": "1"}
    assert _owner(cfg, "/fleets/example/size") == str(path)
    assert _owner(cfg, "/fleets/example/environment/REMOVE") == str(path)
    assert _owner(cfg, "/fleets/example/environment/KEEP") == PLUGIN


def test_null_clears_in_a_new_nested_mapping_are_dropped(tmp_path, monkeypatch, isolated):
    root = _plugin(tmp_path / "plugins" / "example-harness", """\
        fleets:
          example:
            image: example:latest
    """)
    isolated.append(("example-harness@example", [root]))
    _operator_config(tmp_path, monkeypatch, """\
        schema_version: 2
        fleets:
          example:
            environment:
              REMOVE: null
              KEEP: "1"
    """)
    assert load_config(strict=True).fleets["example"].environment == {"KEEP": "1"}


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unavailable")
def test_cyclic_symlink_declaration_is_skipped(tmp_path, monkeypatch, isolated):
    root = _plugin(tmp_path / "plugins" / "example-harness", None)
    try:
        os.symlink(root / "loop-b.yaml", root / "loop-a.yaml")
        os.symlink(root / "loop-a.yaml", root / "loop-b.yaml")
    except OSError as error:
        pytest.skip(f"cannot create symlinks: {error}")
    (root / "plugin.json").write_text(
        json.dumps({"name": "example-harness", plugin_config.MANIFEST_FIELD: "loop-a.yaml"})
    )
    isolated.append(("example-harness@example", [root]))
    _operator_config(tmp_path, monkeypatch, """\
        schema_version: 2
        fleets:
          own:
            image: own:latest
    """)
    assert list(load_config().fleets) == ["own"]


def test_inaccessible_fragment_is_skipped(tmp_path, monkeypatch, isolated):
    root = _plugin(tmp_path / "plugins" / "example-harness", EXAMPLE_FRAGMENT)
    isolated.append(("example-harness@example", [root]))
    fragment = (root / "containers.yaml").resolve()
    real_is_file = Path.is_file

    def is_file(self, *args, **kwargs):
        if self == fragment:
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", is_file)
    assert load_config(strict=True).fleets == {}


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
    data, provenance, _, _ = plugin_config.merge_fragments(
        {"fleets": {"example": {"future_options": {"beta": 3}}}},
        plugin_config.discover_plugin_fragments(),
        provenance={}, layers=[],
    )
    assert data["fleets"]["example"]["future_options"] == {"alpha": 1, "beta": 3}
    assert provenance["/fleets/example/future_options/alpha"]["id"] == PLUGIN
    assert "/fleets/example/future_options/beta" not in provenance
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
    assert cfg.fleet_source("example") == "plugin:alpha@example"
    assert cfg.fleet_source("other") == "plugin:beta@example"
    beta = next(f for f in cfg.plugin_fragments if f["plugin"] == "beta@example")
    assert beta["shadowed"] == ["example"] and beta["fleets"] == ["other"]
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
    assert data["provenance"]["/fleets/example/size"]["id"] == str(path)
    assert data["provenance"]["/fleets/example/repo"]["id"] == PLUGIN
    assert data["layers"] == [PLUGIN]
    assert data["plugin_fragments"][0]["plugin"] == "example-harness@example"

    assert cli.main(["config-sources"]) == 0
    text = capsys.readouterr().out
    assert f"/fleets/example/size: {path}" in text
    assert f"/fleets/example/repo: {PLUGIN}" in text


def test_repository_local_activation_is_discovered_without_global_or_registration(
    tmp_path, monkeypatch,
):
    home = tmp_path / "home"
    (home / ".copilot").mkdir(parents=True)
    (home / ".copilot" / "settings.json").write_text(json.dumps({"enabledPlugins": {}}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    installed = home / ".copilot" / "installed-plugins" / "example"
    enabled = _plugin(installed / "example-harness", EXAMPLE_FRAGMENT)
    _plugin(installed / "disabled-harness", """\
        fleets:
          disabled:
            image: disabled:latest
    """)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"enabledPlugins": {
        "example-harness@example": True, "disabled-harness@example": False,
    }}))
    (repo / "nested").mkdir()
    monkeypatch.chdir(repo / "nested")
    monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
    monkeypatch.setattr(config_mod, "RUNTIME_DIR", tmp_path / "runtime")
    monkeypatch.setattr(config_mod, "_knowledge_overlay_config", lambda: None)
    assert not (home / ".agent-worktrees").exists()

    cfg = load_config(strict=True)

    assert list(cfg.fleets) == ["example"]
    assert cfg.fleet_source("example") == PLUGIN
    owner = cfg.configuration_provenance["/fleets/example/repo"]
    assert owner["source"] == {"root": str(enabled.resolve()), "path": "containers.yaml"}
    assert [f["plugin"] for f in cfg.plugin_fragments] == ["example-harness@example"]
    # Machine-wide resolution alone (no invoking repository) sees nothing.
    assert plugin_config.discover_plugin_fragments() == []


CYCLIC_FRAGMENT = """\
    fleets:
      loop: &loop
        image: loop:latest
        nested: *loop
    """


@pytest.mark.parametrize("strict", [False, True])
def test_cyclic_fragment_is_rejected_while_other_sources_survive(
    tmp_path, monkeypatch, isolated, caplog, strict,
):
    cyclic = _plugin(tmp_path / "plugins" / "alpha", CYCLIC_FRAGMENT)
    valid = _plugin(tmp_path / "plugins" / "beta", EXAMPLE_FRAGMENT)
    isolated.extend([("alpha@example", [cyclic]), ("beta@example", [valid])])
    _operator_config(tmp_path, monkeypatch, """\
        schema_version: 2
        fleets:
          own:
            image: own:latest
          example:
            size: 2
    """)

    with caplog.at_level(logging.WARNING, logger="agent-containers"):
        cfg = load_config(strict=strict)

    assert sorted(cfg.fleets) == ["example", "own"]
    assert cfg.fleets["example"].size == 2
    assert cfg.fleets["example"].repo == "example-org/example"
    assert cfg.fleet_source("example") == "plugin:beta@example"
    assert [f["plugin"] for f in cfg.plugin_fragments] == ["beta@example"]
    assert "self-referencing" in caplog.text


def test_acyclic_shared_aliases_remain_usable(tmp_path, isolated):
    root = _plugin(tmp_path / "plugins" / "example-harness", """\
        fleets:
          one:
            image: one:latest
            environment: &shared
              SHARED: "1"
          two:
            image: two:latest
            environment: *shared
            native_env: {a: *shared, b: *shared}
    """)
    isolated.append(("example-harness@example", [root]))

    cfg = load_config(strict=True)

    assert cfg.fleets["one"].environment == {"SHARED": "1"}
    assert cfg.fleets["two"].environment == {"SHARED": "1"}
    assert cfg.configuration_provenance["/fleets/two/native_env/b/SHARED"]["id"] == PLUGIN
