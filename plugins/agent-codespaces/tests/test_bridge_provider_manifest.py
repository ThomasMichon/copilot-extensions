from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("bridge_installed,command_installed", [
    (False, False), (False, True), (True, False), (True, True),
])
def test_installed_setup_publishes_plain_provider_before_either_install_order(
    tmp_path, monkeypatch, bridge_installed, command_installed,
):
    from agent_codespaces import __main__ as cli, application_provider

    home = tmp_path / "home"
    bridge = tmp_path / "custom-bridge-config"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(bridge))
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(tmp_path / "different-code-root"))
    monkeypatch.delenv("AGENT_BRIDGE_PROVIDERS_DIR", raising=False)
    original_write = application_provider.atomic_write_text
    writes = []

    def write(path, content):
        assert path.name != application_provider.MANIFEST_NAME
        writes.append(path)
        return original_write(path, content)

    monkeypatch.setattr(application_provider, "atomic_write_text", write)
    config = bridge / "config.yaml"
    if bridge_installed:
        bridge.mkdir(parents=True)
        config.write_bytes(b"existing configuration\n")
    command = home / ".local" / "bin" / (
        "agent-codespaces.cmd" if os.name == "nt" else "agent-codespaces"
    )
    if command_installed:
        command.parent.mkdir(parents=True)
        command.write_bytes(b"global marker forwarder")
    assert cli.main(["__source-setup"]) == 0
    declaration = bridge / "providers.d" / "agent-codespaces.standalone.json"
    assert json.loads(declaration.read_text()) == {
        "schema_version": 0, "namespace": "codespace", "restricted": False,
        "description": "Installed agent-codespaces application", "command": [str(command)],
    }
    modified = declaration.stat().st_mtime_ns
    assert cli.main(["__source-setup"]) == 0
    assert declaration.stat().st_mtime_ns == modified
    assert len(writes) == 1
    assert command.exists() == command_installed
    assert not (tmp_path / "different-code-root").exists()
    assert not (tmp_path / "unrelated-provider-override").exists()
    assert config.exists() == bridge_installed
    if bridge_installed:
        assert config.read_bytes() == b"existing configuration\n"
    monkeypatch.syspath_prepend(str(PLUGIN.parents[1] / "plugins" / "agent-bridge" / "src"))
    from agent_bridge import provider_sources

    monkeypatch.setattr(provider_sources, "resolve_active_plugins",
                        lambda: pytest.fail("plain discovery must not resolve plugin activation"))
    found = provider_sources.scan_provider_registry(directory=declaration.parent)
    if command_installed:
        assert found.manifests["codespace"].command == (str(command),)
    else:
        assert "codespace" not in found.manifests


@pytest.mark.parametrize("contents", [
    b"edited declaration\n",
    b'{"schema_version": 1, "plugin": "agent-codespaces@copilot-extensions"}\n',
    b'{"namespace": "codespace", "command": ["foreign-command"]}\n',
])
def test_setup_preserves_existing_provider_custody(tmp_path, monkeypatch, contents):
    from agent_codespaces import application_provider, owner_cli

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_BRIDGE_PROVIDERS_DIR", raising=False)
    monkeypatch.setattr(application_provider, "atomic_write_text",
                        lambda *a, **k: pytest.fail("rejected setup must not publish again"))
    registry = tmp_path / "providers.d"
    registry.mkdir()
    manifest = registry / "agent-codespaces.standalone.json"
    manifest.write_bytes(contents)
    before = (manifest.read_bytes(), manifest.stat().st_mtime_ns)
    with pytest.raises(RuntimeError, match="owner's migration"):
        owner_cli.cmd_source_setup(None)
    assert (manifest.read_bytes(), manifest.stat().st_mtime_ns) == before
    assert list(registry.iterdir()) == [manifest]


@pytest.mark.parametrize("attributed", [False, True])
def test_setup_does_not_replace_a_concurrently_published_provider(
    tmp_path, monkeypatch, attributed,
):
    from agent_codespaces import owner_cli

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_BRIDGE_PROVIDERS_DIR", raising=False)
    original_link = os.link
    winner = json.dumps({
        "schema_version": 1, "plugin": "agent-codespaces@copilot-extensions",
        "plugin_root": str(tmp_path), "namespace": "codespace", "command": ["foreign-fixture"],
    }).encode() if attributed else b"another owner\n"
    winner_time = []

    def concurrent(source, destination):
        destination.write_bytes(winner)
        winner_time.append(destination.stat().st_mtime_ns)
        original_link(source, destination)

    monkeypatch.setattr(os, "link", concurrent)
    with pytest.raises(RuntimeError, match="migration"):
        owner_cli.cmd_source_setup(None)
    registry = tmp_path / "providers.d"
    published = registry / "agent-codespaces.standalone.json"
    assert (published.read_bytes(), published.stat().st_mtime_ns) == (winner, winner_time[0])
    assert len(list(registry.iterdir())) == 1


@pytest.mark.parametrize("unsafe", ["directory-as-file", "hardlinked-declaration"])
def test_setup_refuses_unsafe_provider_paths(tmp_path, monkeypatch, unsafe):
    from agent_codespaces import owner_cli

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_BRIDGE_PROVIDERS_DIR", raising=False)
    registry = tmp_path / "providers.d"
    if unsafe == "directory-as-file":
        registry.write_bytes(b"not a directory\n")
        with pytest.raises(RuntimeError, match="ordinary ancestors"):
            owner_cli.cmd_source_setup(None)
        assert registry.read_bytes() == b"not a directory\n"
    else:
        registry.mkdir()
        foreign = tmp_path / "foreign.json"
        foreign.write_bytes(b"another owner's declaration\n")
        manifest = registry / "agent-codespaces.standalone.json"
        os.link(foreign, manifest)
        with pytest.raises(RuntimeError, match="ordinary file"):
            owner_cli.cmd_source_setup(None)
        assert manifest.read_bytes() == foreign.read_bytes() == b"another owner's declaration\n"
        assert foreign.stat().st_nlink == 2


@pytest.mark.parametrize("enabled", [False, True])
def test_plain_application_and_attributed_payload_remain_separate(
    tmp_path, monkeypatch, enabled,
):
    from dropin_registry import EntryDecision, ScanAuthority
    from plugin_activation import ActivePlugin, ActivationReport
    from agent_codespaces import owner_cli

    home = tmp_path / "home"
    registry = tmp_path / "providers.d"
    registry.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(registry))
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(registry.parent))
    payload = tmp_path / "payload"
    payload.mkdir()
    payload_command = payload / "payload-command"
    payload_command.write_text("fixture command\n")
    payload_command.chmod(0o700)
    identity = "agent-codespaces@copilot-extensions"
    attributed = registry / "agent-codespaces.json"
    attributed.write_text(json.dumps({
        "schema_version": 1, "plugin": identity, "plugin_root": str(payload),
        "namespace": "codespace", "command": [str(payload_command)],
    }))
    before = attributed.read_bytes(), attributed.stat().st_mtime_ns
    assert owner_cli.cmd_source_setup(None) == 0
    assert (attributed.read_bytes(), attributed.stat().st_mtime_ns) == before
    plain = registry / "agent-codespaces.standalone.json"
    record = json.loads(plain.read_text())
    assert record["schema_version"] == 0
    assert "plugin" not in record and "plugin_root" not in record
    command = Path(record["command"][0])
    command.parent.mkdir(parents=True)
    command.write_text("fixture installed global command\n")
    command.chmod(0o700)
    monkeypatch.syspath_prepend(str(PLUGIN.parents[1] / "plugins" / "agent-bridge" / "src"))
    from agent_bridge import provider_sources

    decisions = {identity: EntryDecision.active(ActivePlugin(
        source=identity, name="agent-codespaces", marketplace="copilot-extensions",
        root=payload.resolve(), scopes=("global",),
    ))} if enabled else {}
    found = provider_sources.scan_provider_registry(
        directory=registry,
        activation_report=ActivationReport(authority=ScanAuthority.COMPLETE, decisions=decisions),
    )
    assert found.manifests["codespace"].command == (
        str(payload_command) if enabled else str(command),
    )
    assert (attributed.read_bytes(), attributed.stat().st_mtime_ns) == before


def test_plain_setup_has_no_source_activation_or_backend_prerequisite(tmp_path, monkeypatch):
    from agent_codespaces import __main__ as cli, source_build
    import plugin_activation

    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(tmp_path / "providers.d"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    fail = lambda *a, **k: pytest.fail("unexpected activation, source proof, or backend effect")
    monkeypatch.setattr(plugin_activation, "resolve_active_plugins", fail)
    monkeypatch.setattr(source_build, "capture_owner_runtime", fail)
    monkeypatch.setattr(subprocess, "run", fail)
    monkeypatch.setattr(subprocess, "Popen", fail)
    assert cli.main(["__source-setup"]) == 0
    record = json.loads(
        (tmp_path / "providers.d" / "agent-codespaces.standalone.json").read_text()
    )
    assert record["command"] == [str(tmp_path / "home" / ".local" / "bin" / (
        "agent-codespaces.cmd" if os.name == "nt" else "agent-codespaces"
    ))]


@pytest.mark.parametrize("attributed", [False, True])
def test_setup_preserves_a_foreign_namespace_contribution(tmp_path, monkeypatch, attributed):
    from agent_codespaces import owner_cli

    registry = tmp_path / "providers.d"
    registry.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(registry))
    foreign = registry / "foreign.json"
    contents = {"namespace": "codespace:", "command": ["foreign"]}
    if attributed:
        contents.update(schema_version=1, plugin="other-plugin@other-marketplace",
                        plugin_root=str(tmp_path))
    foreign.write_text(json.dumps(contents))
    before = foreign.read_bytes(), foreign.stat().st_mtime_ns
    with pytest.raises(RuntimeError, match="preserved"):
        owner_cli.cmd_source_setup(None)
    assert (foreign.read_bytes(), foreign.stat().st_mtime_ns) == before
    assert list(registry.iterdir()) == [foreign]


@pytest.mark.parametrize("owned", [False, True])
def test_oversized_provider_is_isolated_only_when_unrelated(tmp_path, monkeypatch, owned):
    from agent_codespaces import application_provider, owner_cli

    registry = tmp_path / "providers.d"
    registry.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(registry))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    large = registry / (application_provider.MANIFEST_NAME if owned else "unrelated.json")
    large.write_text(json.dumps({"namespace": "unrelated", "padding": "x" * 65536}))
    before = large.read_bytes(), large.stat().st_mtime_ns
    if owned:
        with pytest.raises(RuntimeError):
            owner_cli.cmd_source_setup(None)
        assert list(registry.iterdir()) == [large]
    else:
        assert owner_cli.cmd_source_setup(None) == 0
        assert (registry / application_provider.MANIFEST_NAME).is_file()
    assert (large.read_bytes(), large.stat().st_mtime_ns) == before


def test_bridge_provider_manifest_is_attributed_and_writers_stamp_root_atomically():
    template = json.loads(
        (PLUGIN / "references" / "bridge-provider.json").read_text(encoding="utf-8")
    )
    assert template["schema_version"] == 1
    assert template["plugin"] == "agent-codespaces@copilot-extensions"
    assert template["namespace"] == "codespace"

    powershell = (PLUGIN / "scripts" / "register-bridge-provider.ps1").read_text(
        encoding="utf-8"
    )
    shell = (PLUGIN / "scripts" / "register-bridge-provider.sh").read_text(
        encoding="utf-8"
    )
    assert "plugin_root" in powershell
    assert "[System.IO.File]::Replace" in powershell
    assert "[System.IO.File]::Move($tmp, $out)" in powershell
    assert 'data["plugin_root"] = os.path.realpath(plugin_root)' in shell
    assert "os.replace(tmp, out)" in shell
    assert 'Join-Path $PluginDir "bin\\$name.cmd"' in powershell
    assert 'binstub="$PluginDir/bin/$name"' in shell


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell compatibility")
def test_powershell_51_writer_creates_and_replaces_manifest(tmp_path):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    registry = tmp_path / "providers.d"
    env = {
        **os.environ,
        "AGENT_BRIDGE_PROVIDERS_DIR": str(registry),
    }
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PLUGIN / "scripts" / "register-bridge-provider.ps1"),
    ]
    subprocess.run(command, env=env, check=True)
    subprocess.run(command, env=env, check=True)
    manifest = json.loads(
        (registry / "agent-codespaces.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert Path(manifest["plugin_root"]).resolve() == PLUGIN.resolve()
    assert manifest["command"] == [str(PLUGIN / "bin" / "agent-codespaces.cmd")]
