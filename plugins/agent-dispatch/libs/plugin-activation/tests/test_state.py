from __future__ import annotations

import json
import subprocess

import pytest

from plugin_activation import (
    PluginStateError,
    capture,
    inspect_plugin_state,
    installed_plugin_identities,
    read_json_object,
    remove_user_activation,
    restore,
    run_install_preserving_activation,
    write_json_object_atomic,
)


IDENTITY = "optional-plugin@example-marketplace"


def _write_state(tmp_path, user_value=..., inventory_enabled=None):
    home = tmp_path / ".copilot"
    home.mkdir()
    settings = {"unrelated": {"keep": True}}
    if user_value is not ...:
        settings["enabledPlugins"] = {IDENTITY: user_value, "other@m": True}
    (home / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    config = {
        "trustedFolders": [],
        "installedPlugins": [
            {
                "name": "optional-plugin",
                "marketplace": "example-marketplace",
                "enabled": inventory_enabled,
                "version": "1.0.0",
            }
        ],
    }
    (home / "config.json").write_text(
        "// managed\n" + json.dumps(config),
        encoding="utf-8",
    )
    return home


def test_inspect_and_remove_preserve_inventory_and_unrelated_state(tmp_path):
    home = _write_state(tmp_path, True, inventory_enabled=True)

    state = inspect_plugin_state(IDENTITY, home)
    assert state["installed"] is True
    assert state["userActivation"] == "true"
    assert state["installedButNotUserEnabled"] is False

    preview = remove_user_activation(IDENTITY, home)
    assert preview["changed"] is True
    assert inspect_plugin_state(IDENTITY, home)["userActivation"] == "true"

    applied = remove_user_activation(IDENTITY, home, apply=True)
    assert applied["changed"] is True
    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert settings == {
        "enabledPlugins": {"other@m": True},
        "unrelated": {"keep": True},
    }
    header, config = read_json_object(home / "config.json", jsonc_header=True)
    assert header == "// managed\n"
    assert config["installedPlugins"][0]["enabled"] is False
    assert installed_plugin_identities(home) == [IDENTITY]
    assert remove_user_activation(IDENTITY, home, apply=True)["changed"] is False


def test_remove_does_not_rewrite_already_disabled_inventory(tmp_path):
    home = _write_state(tmp_path, True, inventory_enabled=False)
    _, config = read_json_object(home / "config.json", jsonc_header=True)
    raw_config = "// managed\n" + json.dumps(config, indent=4) + "\n"
    (home / "config.json").write_text(raw_config, encoding="utf-8")

    result = remove_user_activation(IDENTITY, home, apply=True)

    assert result["changes"] == [f"remove enabledPlugins.{IDENTITY}"]
    assert (home / "config.json").read_text(encoding="utf-8") == raw_config


@pytest.mark.parametrize("before", [..., False, True])
def test_snapshot_restore_preserves_user_activation_tristate(tmp_path, before):
    inventory_enabled = None if before is ... else before
    home = _write_state(tmp_path, before, inventory_enabled=inventory_enabled)
    snapshot = capture(IDENTITY, home)
    _, settings = read_json_object(home / "settings.json")
    settings.setdefault("enabledPlugins", {})[IDENTITY] = True
    write_json_object_atomic(home / "settings.json", settings)

    restore(snapshot, home)

    state = inspect_plugin_state(IDENTITY, home)
    expected = "absent" if before is ... else str(before).lower()
    assert state["userActivation"] == expected


def test_installing_missing_inventory_preserves_absent_user_activation(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / ".copilot"

    def install(argv, **kwargs):
        write_json_object_atomic(
            home / "settings.json",
            {"enabledPlugins": {IDENTITY: True}},
        )
        write_json_object_atomic(
            home / "config.json",
            {
                "installedPlugins": [
                    {
                        "name": IDENTITY,
                        "enabled": True,
                        "version": "1.0.0",
                    }
                ]
            },
            "// managed\n",
        )
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", install)
    result = run_install_preserving_activation(["copilot"], IDENTITY, home=home)

    assert result.returncode == 0
    state = inspect_plugin_state(IDENTITY, home)
    assert state["installed"] is True
    assert state["userActivation"] == "absent"
    assert state["inventoryEnabled"] is False


def test_install_restores_activation_after_failure(tmp_path, monkeypatch):
    home = _write_state(tmp_path, False, inventory_enabled=False)

    def fail(argv, **kwargs):
        _, settings = read_json_object(home / "settings.json")
        settings["enabledPlugins"][IDENTITY] = True
        write_json_object_atomic(home / "settings.json", settings)
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(subprocess, "run", fail)
    result = run_install_preserving_activation(["copilot"], IDENTITY, home=home)
    assert result.returncode == 7
    assert inspect_plugin_state(IDENTITY, home)["userActivation"] == "false"


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("settings.json", '{"enabledPlugins":[]}', "must be an object"),
        (
            "settings.json",
            '{"enabledPlugins":{"a@m":true,"a@m":false}}',
            "duplicate JSON key",
        ),
        ("config.json", '{"installedPlugins":{}}', "must be an array"),
    ],
)
def test_malformed_state_fails_closed(tmp_path, name, content, message):
    home = _write_state(tmp_path)
    (home / name).write_text(content, encoding="utf-8")
    with pytest.raises(PluginStateError, match=message):
        inspect_plugin_state(IDENTITY, home)
