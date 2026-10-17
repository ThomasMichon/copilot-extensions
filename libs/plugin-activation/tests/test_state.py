from __future__ import annotations

import json
import os
import subprocess
import sys

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
from plugin_activation import state


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


def test_restore_skips_rewrite_when_nothing_changed(tmp_path):
    # restore() previously rewrote the shared, user-global settings.json/
    # config.json on every call regardless of whether anything actually
    # changed, generating a distinct file-change event per plugin per update
    # sweep. Confirm it now leaves both files untouched (same mtime) when the
    # install left activation exactly as captured.
    home = _write_state(tmp_path, True, inventory_enabled=True)
    settings_path = home / "settings.json"
    config_path = home / "config.json"
    settings_before = settings_path.stat().st_mtime_ns
    config_before = config_path.stat().st_mtime_ns

    snapshot = capture(IDENTITY, home)
    restore(snapshot, home)

    assert settings_path.stat().st_mtime_ns == settings_before
    assert config_path.stat().st_mtime_ns == config_before


def test_restore_still_writes_when_activation_actually_changed(tmp_path):
    home = _write_state(tmp_path, True, inventory_enabled=True)
    snapshot = capture(IDENTITY, home)
    _, settings = read_json_object(home / "settings.json")
    settings["enabledPlugins"][IDENTITY] = False
    write_json_object_atomic(home / "settings.json", settings)

    restore(snapshot, home)

    state = inspect_plugin_state(IDENTITY, home)
    assert state["userActivation"] == "true"


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


@pytest.mark.parametrize("before", [..., False, True])
@pytest.mark.parametrize("blocked_file", ["settings.json", "config.json"])
@pytest.mark.parametrize("command_timeout", [False, True])
def test_transient_restoration_reuses_original_snapshot_without_reinstall(
    tmp_path, monkeypatch, before, blocked_file, command_timeout,
):
    home = _write_state(tmp_path, before, inventory_enabled=False)
    commands, writes, waits = [], [], []
    monkeypatch.setattr(state, "sleep", waits.append)

    def install(argv, **kwargs):
        commands.append(list(argv))
        settings = {"enabledPlugins": {IDENTITY: True, "added@m": True}}
        (home / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        _, config = read_json_object(home / "config.json", jsonc_header=True)
        config["installedPlugins"][0].update(enabled=True, version="2.0.0")
        (home / "config.json").write_text(json.dumps(config), encoding="utf-8")
        if command_timeout:
            raise subprocess.TimeoutExpired(argv, 1)
        return subprocess.CompletedProcess(argv, 0)

    replace = state.os.replace

    def locked_once(source, destination):
        if destination == home / blocked_file:
            writes.append(destination)
            if len(writes) == 1:
                error = OSError("localized sharing violation")
                error.winerror = 32
                raise error
        return replace(source, destination)

    monkeypatch.setattr(subprocess, "run", install)
    monkeypatch.setattr(state.os, "replace", locked_once)
    if command_timeout:
        with pytest.raises(subprocess.TimeoutExpired):
            run_install_preserving_activation(["copilot"], IDENTITY, home=home)
    else:
        assert run_install_preserving_activation(["copilot"], IDENTITY, home=home).returncode == 0

    observed = inspect_plugin_state(IDENTITY, home)
    assert observed["userActivation"] == ("absent" if before is ... else str(before).lower())
    assert observed["inventoryEnabled"] is False
    _, config = read_json_object(home / "config.json", jsonc_header=True)
    assert config["installedPlugins"][0]["version"] == "2.0.0"
    assert read_json_object(home / "settings.json")[1]["enabledPlugins"]["added@m"] is True
    assert commands == [["copilot"]]
    unchanged = before is True and blocked_file == "settings.json"
    assert len(writes) == (0 if unchanged else 2)
    assert waits == ([] if unchanged else [1.0])


@pytest.mark.parametrize("code,attempts", [(32, 2), (33, 2), (5, 1), (13, 1)])
def test_restoration_contention_is_bounded_and_permission_is_terminal(
    tmp_path, monkeypatch, code, attempts,
):
    home = _write_state(tmp_path, False, inventory_enabled=False)
    snapshot = capture(IDENTITY, home)
    (home / "settings.json").write_text(
        json.dumps({"enabledPlugins": {IDENTITY: True}}), encoding="utf-8",
    )
    writes, waits = [], []
    monkeypatch.setattr(state, "sleep", waits.append)

    def locked(source, destination):
        writes.append(destination)
        error = OSError("localized error")
        error.winerror = code
        raise error

    monkeypatch.setattr(state.os, "replace", locked)
    with pytest.raises(OSError, match="localized error"):
        restore(snapshot, home)
    assert len(writes) == attempts
    assert waits == ([1.0] if attempts == 2 else [])
    assert not list(home.glob("*.tmp"))


def test_restoration_malformed_state_never_retries(tmp_path, monkeypatch):
    home = _write_state(tmp_path, False, inventory_enabled=False)
    snapshot = capture(IDENTITY, home)
    (home / "settings.json").write_text('{"enabledPlugins":[]}', encoding="utf-8")
    waits = []
    monkeypatch.setattr(state, "sleep", waits.append)
    with pytest.raises(PluginStateError, match="must be an object"):
        restore(snapshot, home)
    assert waits == []


def test_restoration_retries_wrapped_read_contention(tmp_path, monkeypatch, caplog):
    home = _write_state(tmp_path, False, inventory_enabled=False)
    snapshot = capture(IDENTITY, home)
    settings = home / "settings.json"
    settings.write_text(json.dumps({"enabledPlugins": {IDENTITY: True}}), encoding="utf-8")
    read = state.read_json_object
    attempts = []
    monkeypatch.setattr(state, "sleep", lambda seconds: None)

    def locked_once(path, **kwargs):
        if path == settings:
            attempts.append(path)
            if len(attempts) == 1:
                error = OSError("localized lock violation")
                error.winerror = 33
                raise PluginStateError(f"cannot read {path}") from error
        return read(path, **kwargs)

    monkeypatch.setattr(state, "read_json_object", locked_once)
    restore(snapshot, home)
    assert inspect_plugin_state(IDENTITY, home)["userActivation"] == "false"
    assert "retrying the original snapshot once" in caplog.text
    assert "Windows lock error 33" in caplog.text


@pytest.mark.parametrize("fresh", [False, True])
def test_native_install_restores_after_a_transient_lock_without_replay(
    tmp_path, monkeypatch, fresh,
):
    if fresh:
        home = tmp_path / ".copilot"
        home.mkdir()
    else:
        home = _write_state(tmp_path, False, inventory_enabled=False)
    blocked = home / ("config.json" if fresh else "settings.json")
    attempts = tmp_path / "commands"
    program = (
        "import json,pathlib,sys;"
        "home=pathlib.Path(sys.argv[1]);"
        "open(sys.argv[2], 'a').write('ran\\n');"
        "(home/'settings.json').write_text(json.dumps("
        "{'enabledPlugins':{sys.argv[3]:True}}));"
        "(home/'config.json').write_text(json.dumps({'installedPlugins':["
        "{'name':sys.argv[3],'version':'2.0.0','enabled':True}]}))"
    )
    replace, writes = state.os.replace, []
    monkeypatch.setattr(state, "sleep", lambda seconds: None)

    def locked_once(source, destination):
        if destination == blocked:
            writes.append(destination)
            if len(writes) == 1:
                error = OSError("sharing violation")
                error.winerror = 32
                raise error
        return replace(source, destination)

    monkeypatch.setattr(state.os, "replace", locked_once)
    result = run_install_preserving_activation(
        [sys.executable, "-c", program, str(home), str(attempts), IDENTITY],
        IDENTITY, home=home, capture_output=True, text=True, timeout=10,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert attempts.read_text() == "ran\n"
    assert len(writes) == 2
    observed = inspect_plugin_state(IDENTITY, home)
    assert observed["userActivation"] == ("absent" if fresh else "false")
    assert observed["inventoryEnabled"] is False
    assert observed["installed"] is True
    assert (home / "settings.json").exists() is (not fresh)


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


def test_a_built_in_plugin_entry_is_left_alone(tmp_path):
    """Copilot CLI's first-party plugins write an unqualified key (no
    ``@marketplace``) into enabledPlugins. It is not this library's: reads and
    removals work around it and never drop or rewrite it."""
    home = _write_state(tmp_path, True, inventory_enabled=True)
    settings_path = home / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["enabledPlugins"]["computer-use"] = True
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    assert inspect_plugin_state(IDENTITY, home)["userActivation"] == "true"
    snapshot = capture(IDENTITY, home)
    assert remove_user_activation(IDENTITY, home, apply=True)["changed"] is True
    after = json.loads(settings_path.read_text(encoding="utf-8"))["enabledPlugins"]
    assert after == {"other@m": True, "computer-use": True}
    restore(snapshot, home)
    after = json.loads(settings_path.read_text(encoding="utf-8"))["enabledPlugins"]
    assert after == {IDENTITY: True, "other@m": True, "computer-use": True}


@pytest.mark.parametrize("key", ["@m", "a@", "a@b@c"])
def test_a_malformed_marketplace_key_still_fails_closed(tmp_path, key):
    home = _write_state(tmp_path)
    (home / "settings.json").write_text(json.dumps({"enabledPlugins": {key: True}}),
                                        encoding="utf-8")
    with pytest.raises(PluginStateError, match="<name>@<marketplace>"):
        inspect_plugin_state(IDENTITY, home)


def test_a_marketplace_key_still_needs_a_boolean(tmp_path):
    home = _write_state(tmp_path)
    (home / "settings.json").write_text(json.dumps({"enabledPlugins": {"a@m": "yes"}}),
                                        encoding="utf-8")
    with pytest.raises(PluginStateError, match="must be boolean"):
        inspect_plugin_state(IDENTITY, home)
