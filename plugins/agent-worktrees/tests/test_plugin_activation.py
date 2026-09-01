from __future__ import annotations

import json
import subprocess
import sys

import pytest

from agent_worktrees import activation_preservation
from plugin_activation import read_json_object, write_json_object_atomic


IDENTITY = "optional-plugin@copilot-extensions"


def _write_state(tmp_path, user_value=..., inventory_enabled=None):
    home = tmp_path / ".copilot"
    home.mkdir()
    settings = {"unrelated": {"keep": True}}
    if user_value is not ...:
        settings["enabledPlugins"] = {IDENTITY: user_value, "other@m": True}
    (home / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    config = {
        "trustedFolders": ["/repo"],
        "installedPlugins": [
            {
                "name": "optional-plugin",
                "marketplace": "copilot-extensions",
                "enabled": inventory_enabled,
                "version": "1.0.0",
            }
        ],
    }
    (home / "config.json").write_text(
        "// managed\n" + json.dumps(config), encoding="utf-8"
    )
    return home


@pytest.mark.parametrize(
    ("before", "expected"),
    [(..., None), (False, False), (True, True)],
)
def test_install_restores_user_activation_and_preserves_inventory(
    tmp_path, monkeypatch, before, expected
):
    home = _write_state(tmp_path, before, inventory_enabled=expected)

    def fake_run(argv, **kwargs):
        settings_path = home / "settings.json"
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        settings.setdefault("enabledPlugins", {})[IDENTITY] = True
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        config_path = home / "config.json"
        _, config = read_json_object(config_path, jsonc_header=True)
        config["installedPlugins"][0]["enabled"] = True
        write_json_object_atomic(config_path, config, "// managed\n")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = activation_preservation.run_install_preserving_activation(
        ["copilot", "plugin", "install", IDENTITY], IDENTITY, home=home
    )

    assert result.returncode == 0
    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    if before is ...:
        assert IDENTITY not in settings.get("enabledPlugins", {})
    else:
        assert settings["enabledPlugins"][IDENTITY] is expected
    assert settings["unrelated"] == {"keep": True}
    _, config = read_json_object(home / "config.json", jsonc_header=True)
    expected_inventory = False if before is ... else expected
    assert config["installedPlugins"][0]["enabled"] is expected_inventory
    assert config["trustedFolders"] == ["/repo"]
    assert (home / "config.json").read_text(encoding="utf-8").startswith(
        "// managed\n"
    )


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_install_restores_activation_after_failure(tmp_path, monkeypatch, failure):
    home = _write_state(tmp_path, False, inventory_enabled=False)

    def fake_run(argv, **kwargs):
        settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
        settings["enabledPlugins"][IDENTITY] = True
        (home / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(subprocess, "run", fake_run)
    if failure == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            activation_preservation.run_install_preserving_activation(
                ["copilot"], IDENTITY, home=home
            )
    else:
        result = activation_preservation.run_install_preserving_activation(
            ["copilot"], IDENTITY, home=home
        )
        assert result.returncode == 7
    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledPlugins"][IDENTITY] is False


def test_malformed_or_duplicate_state_fails_explicitly(tmp_path):
    home = tmp_path / ".copilot"
    home.mkdir()
    (home / "settings.json").write_text("{bad", encoding="utf-8")
    with pytest.raises(
        activation_preservation.PluginStateError, match="cannot read"
    ):
        activation_preservation.capture(IDENTITY, home)

    (home / "settings.json").write_text("{}", encoding="utf-8")
    (home / "config.json").write_text(
        json.dumps(
            {
                "installedPlugins": [
                    {"name": IDENTITY},
                    {"name": "optional-plugin", "marketplace": "copilot-extensions"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(activation_preservation.PluginStateError, match="duplicate"):
        activation_preservation.installed_plugin_identities(home)


def test_real_subprocess_bootstrap_cannot_promote_activation(tmp_path):
    home = _write_state(tmp_path, False, inventory_enabled=False)
    fake = tmp_path / "fake_install.py"
    fake.write_text(
        """
import json
import sys
from pathlib import Path

home = Path(sys.argv[1])
identity = sys.argv[2]
settings_path = home / "settings.json"
settings = json.loads(settings_path.read_text(encoding="utf-8"))
settings["enabledPlugins"][identity] = True
settings_path.write_text(json.dumps(settings), encoding="utf-8")
config_path = home / "config.json"
lines = config_path.read_text(encoding="utf-8").splitlines()
config = json.loads("\\n".join(lines[1:]))
config["installedPlugins"][0]["enabled"] = True
config_path.write_text("// managed\\n" + json.dumps(config), encoding="utf-8")
""".lstrip(),
        encoding="utf-8",
    )

    result = activation_preservation.run_install_preserving_activation(
        [sys.executable, str(fake), str(home), IDENTITY],
        IDENTITY,
        home=home,
    )

    assert result.returncode == 0
    settings = json.loads((home / "settings.json").read_text(encoding="utf-8"))
    assert settings["enabledPlugins"][IDENTITY] is False
    _, config = read_json_object(home / "config.json", jsonc_header=True)
    assert config["installedPlugins"][0]["enabled"] is False


def test_cli_forwards_validated_copilot_command_prefix(monkeypatch):
    calls: list[list[str]] = []

    def run(argv, identity):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(
        activation_preservation,
        "run_install_preserving_activation",
        run,
    )
    command = ["pwsh", "-NoProfile", "-File", "copilot.ps1"]
    result = activation_preservation._main(
        [
            IDENTITY,
            "--copilot-command-json",
            json.dumps(command),
        ]
    )

    assert result == 0
    assert calls == [[*command, "plugin", "install", IDENTITY]]


@pytest.mark.parametrize("raw", ["{}", "[]", '[""]', "not-json"])
def test_cli_rejects_invalid_copilot_command_prefix(raw):
    with pytest.raises(activation_preservation.PluginStateError):
        activation_preservation._parse_copilot_command(raw)
