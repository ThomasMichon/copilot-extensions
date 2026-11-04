from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from agent_containers import __main__ as cli
from agent_containers import application_provider as provider
from agent_containers import plugin_config


@pytest.fixture
def application_provider_setup(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    config = home / "containers.yaml"
    config.write_text("{}\n")
    registry = home / "bridge" / "providers.d"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(config))
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(registry.parent))
    monkeypatch.delenv("AGENT_BRIDGE_PROVIDERS_DIR", raising=False)
    monkeypatch.setattr(plugin_config, "discover_plugin_fragments", lambda **_: [])

    def forbidden(*_args, **_kwargs):
        pytest.fail("plain application registration must not invoke a provider or CLI")

    monkeypatch.setattr(cli.subprocess, "run", forbidden)
    monkeypatch.setattr(cli.subprocess, "Popen", forbidden)
    return home, config, registry


def _snapshot(directory):
    return {
        str(path.relative_to(directory)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("bridge_first", [False, True])
@pytest.mark.parametrize("command_already_published", [False, True])
def test_application_provider_plain_registration_in_either_order_without_activation(
    application_provider_setup, bridge_first, command_already_published,
):
    home, _config, registry = application_provider_setup
    settings = home / ".copilot" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({
        "enabledPlugins": {"agent-containers@copilot-extensions": False},
    }))
    if bridge_first:
        registry.mkdir(parents=True)
    command = home / ".local" / "bin" / (
        "agent-containers.cmd" if os.name == "nt" else "agent-containers"
    )
    if command_already_published:
        command.parent.mkdir(parents=True)
        command.write_text("fixture global front door; never executed\n")
        if os.name != "nt":
            command.chmod(0o700)
    assert cli.main(["__source-setup"]) == 0
    document = json.loads((registry / provider.MANIFEST_NAME).read_text())
    assert document == {
        "schema_version": 0, "namespace": "container", "restricted": True,
        "description": "Installed agent-containers application", "command": [str(command)],
    }
    assert settings.read_text() == json.dumps({
        "enabledPlugins": {"agent-containers@copilot-extensions": False},
    })
    # A missing future global front door must not become a Bridge prerequisite
    # for publishing this otherwise explicit plain application contribution.
    assert command.exists() == command_already_published
    before = _snapshot(home)
    registry.mkdir(parents=True, exist_ok=True)
    assert cli.main(["__source-setup"]) == 0
    assert _snapshot(home) == before


@pytest.mark.parametrize("plugin_enabled", [False, True])
@pytest.mark.parametrize("marketplace", ["copilot-extensions", "another-marketplace"])
def test_application_provider_preserves_payload_identity_and_fallback_order(
    application_provider_setup, plugin_enabled, marketplace,
):
    home, _config, registry = application_provider_setup
    registry.mkdir(parents=True)
    identity = registry / "agent-containers.json"
    identity.write_text(json.dumps({
        "schema_version": 1, "plugin": f"agent-containers@{marketplace}",
        "plugin_root": str(home / "payload"), "namespace": "container",
        "command": [str(home / "payload" / "bin" / "agent-containers.cmd")],
    }))
    settings = home / ".copilot" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({
        "enabledPlugins": {f"agent-containers@{marketplace}": plugin_enabled},
    }))
    old_identity = (identity.read_bytes(), identity.stat().st_mtime_ns)
    assert cli.main(["__source-setup"]) == 0
    plain = registry / provider.MANIFEST_NAME
    assert (identity.read_bytes(), identity.stat().st_mtime_ns) == old_identity
    assert "plugin" not in json.loads(plain.read_text())
    assert sorted((identity, plain))[0] == identity
    before = _snapshot(home)
    assert cli.main(["__source-setup"]) == 0
    assert _snapshot(home) == before


@pytest.mark.parametrize("identity_bearing", [False, True])
def test_application_provider_namespace_collision_preserves_existing_owner(
    application_provider_setup, identity_bearing,
):
    home, _config, registry = application_provider_setup
    registry.mkdir(parents=True)
    claim = {"namespace": "container", "command": [str(home / "foreign-command")]}
    if identity_bearing:
        claim.update(schema_version=1, plugin="foreign@marketplace", plugin_root=str(home))
    (registry / "zz-existing-provider.json").write_text(json.dumps(claim))
    before = _snapshot(home)
    assert cli.main(["__source-setup"]) == 1
    assert _snapshot(home) == before


def test_application_provider_never_downgrades_identity_at_its_destination(
    application_provider_setup,
):
    home, _config, registry = application_provider_setup
    registry.mkdir(parents=True)
    (registry / provider.MANIFEST_NAME).write_text(json.dumps({
        "schema_version": 1, "plugin": "agent-containers@copilot-extensions",
        "plugin_root": str(home / "payload"), "namespace": "container",
        "command": [str(home / "payload-command")],
    }))
    before = _snapshot(home)
    assert cli.main(["__source-setup"]) == 1
    assert _snapshot(home) == before


def test_application_provider_concurrent_identity_writer_is_not_clobbered(
    application_provider_setup, monkeypatch,
):
    home, _config, registry = application_provider_setup
    original = provider.atomic_write_text
    identity_bytes = json.dumps({
        "schema_version": 1, "plugin": "foreign@marketplace",
        "plugin_root": str(home), "namespace": "container",
        "command": [str(home / "foreign-command")],
    }).encode()

    def concurrent_writer(path, content):
        result = original(path, content)
        (registry / provider.MANIFEST_NAME).write_bytes(identity_bytes)
        return result

    monkeypatch.setattr(provider, "atomic_write_text", concurrent_writer)
    assert cli.main(["__source-setup"]) == 1
    assert (registry / provider.MANIFEST_NAME).read_bytes() == identity_bytes
    assert sorted(path.name for path in registry.iterdir()) == [provider.MANIFEST_NAME]


def test_application_provider_failed_publication_leaves_no_entry(
    application_provider_setup, monkeypatch,
):
    _home, _config, registry = application_provider_setup

    def denied(*_args):
        raise PermissionError("fixture publication refused")

    monkeypatch.setattr(provider, "atomic_write_text", denied)
    assert cli.main(["__source-setup"]) == 1
    assert not list(registry.iterdir())


@pytest.mark.parametrize("owned_target", [False, True])
def test_application_provider_oversize_isolated_for_peers_but_refused_for_target(
    application_provider_setup, owned_target,
):
    _home, _config, registry = application_provider_setup
    registry.mkdir(parents=True)
    path = registry / (provider.MANIFEST_NAME if owned_target else "unrelated.json")
    path.write_text(" " * 65537)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    assert cli.main(["__source-setup"]) == (1 if owned_target else 0)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    if not owned_target:
        assert json.loads((registry / provider.MANIFEST_NAME).read_text())["schema_version"] == 0
    assert not list(registry.glob("*.pending"))


@pytest.mark.parametrize("existing", [False, True])
def test_application_provider_enumeration_io_refuses_without_changing_files(
    application_provider_setup, monkeypatch, existing,
):
    home, _config, registry = application_provider_setup
    registry.mkdir(parents=True)
    if existing:
        assert cli.main(["__source-setup"]) == 0
    before = _snapshot(home)
    original = os.scandir
    original_iterdir = Path.iterdir

    def denied(path):
        if not isinstance(path, int) and Path(path) == registry:
            raise PermissionError("fixture registry enumeration refused")
        return original(path)

    def denied_iterdir(path):
        if path == registry:
            raise PermissionError("fixture registry enumeration refused")
        return original_iterdir(path)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "scandir", denied)
        scoped.setattr(Path, "iterdir", denied_iterdir)
        assert cli.main(["__source-setup"]) == 1
    assert _snapshot(home) == before


@pytest.mark.parametrize("provider_override", [False, True])
def test_application_provider_override_priority_over_explicit_install_root(
    application_provider_setup, monkeypatch, provider_override,
):
    home, _config, _registry = application_provider_setup
    install = home / "install"
    config = home / "config"
    explicit = home / "provider-override"
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(install))
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(config))
    if provider_override:
        monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(explicit))
    assert cli.main(["__source-setup"]) == 0
    expected = explicit if provider_override else config / "providers.d"
    assert (expected / provider.MANIFEST_NAME).is_file()
    assert not (install / "providers.d").exists()
    if provider_override:
        assert not (config / "providers.d").exists()


@pytest.mark.parametrize("filename", ["aa-foreign.json", "zz-foreign.json"])
def test_application_provider_foreign_schema1_namespace_always_preserves_owner(
    application_provider_setup, filename,
):
    home, _config, registry = application_provider_setup
    registry.mkdir(parents=True)
    (registry / filename).write_text(json.dumps({
        "schema_version": 1, "plugin": "different-provider@marketplace",
        "plugin_root": str(home / "foreign"), "namespace": "container",
        "command": [str(home / "foreign-command")],
    }))
    before = _snapshot(home)
    assert cli.main(["__source-setup"]) == 1
    assert _snapshot(home) == before


@pytest.fixture
def real_application_bridge_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("AGENT_BRIDGE_INSTALL_DIR", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_PROVIDERS_DIR", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_CONFIG_DIR", raising=False)
    command = home / ".local" / "bin" / (
        "agent-containers.cmd" if os.name == "nt" else "agent-containers"
    )
    command.parent.mkdir(parents=True)
    command.write_text("fixture command; classifier must not execute it\n")
    if os.name != "nt":
        command.chmod(0o700)
    settings = home / ".copilot" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({
        "enabledPlugins": {"agent-containers@copilot-extensions": False},
    }))
    return home, command


def _real_bridge_classification(command, *, mismatch=False, registry_error=False):
    from agent_procutil.owned_process import run_caller_owned

    root = Path(__file__).resolve().parents[3]
    bridge_environment = root / ".test-venvs" / sys.platform / "agent-bridge"
    python = bridge_environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        pytest.skip("Prepare declared Bridge test dependencies with run-plugin-tests.py agent-bridge --prepare-only")
    code = (
        "import json,sys;from pathlib import Path;"
        "sys.path.insert(0,sys.argv[1]);"
        "from agent_bridge import provider_sources as p;"
        "assert Path(p.__file__).resolve()==Path(sys.argv[1])/'agent_bridge'/'provider_sources.py';"
        "report=p.scan_provider_registry();"
        + (
            "assert not report.manifests;"
            "assert report.snapshot.authority.value=='indeterminate';"
            "assert any(f.reason=='registry-indeterminate' for f in report.findings),report.findings;"
            if registry_error else
            "assert not report.manifests;"
            "assert any(f.reason=='bridge-install-mismatch' for f in report.findings),report.findings;"
            if mismatch else
            "assert 'container' in report.manifests,report.findings;"
            "m=report.manifests['container'];"
            "assert m.schema_version==0 and m.plugin is None;"
            "assert m.command==(sys.argv[2],);"
        )
        + "print(json.dumps({'consumer':p.__file__,'namespaces':sorted(report.manifests),"
        "'findings':[f.reason for f in report.findings]}))"
    )
    settled = []
    result = run_caller_owned(
        [str(python), "-I", "-B", "-c", code,
         str(root / "plugins" / "agent-bridge" / "src"), str(command)],
        timeout=30, max_stdout_bytes=65536, max_stderr_bytes=65536,
        env=dict(os.environ), cwd=root, on_settled=lambda: settled.append(True),
    )
    assert settled
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_application_provider_install_root_is_discovered_by_real_bridge_consumer(
    real_application_bridge_home, monkeypatch,
):
    home, command = real_application_bridge_home
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(home / "explicit-bridge-installation"))
    provider.register_installed_application()
    _real_bridge_classification(command)


@pytest.mark.parametrize("existing", [False, True])
def test_application_provider_canonical_registry_override_mismatch_refuses_before_writes(
    real_application_bridge_home, monkeypatch, existing,
):
    home, command = real_application_bridge_home
    registry = home / "custom" / "providers.d"
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(home / "bridge-install"))
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(registry))
    if existing:
        with monkeypatch.context() as scoped:
            scoped.setenv("AGENT_BRIDGE_CONFIG_DIR", str(registry.parent))
            provider.register_installed_application()
        (registry / "foreign.keep").write_bytes(b"fixture-owned peer\n")
    before = _snapshot(home)
    refused = False
    try:
        provider.register_installed_application()
    except RuntimeError as error:
        assert "different Bridge installation" in str(error)
        refused = True
    _real_bridge_classification(command, mismatch=True)
    assert refused, "producer published a contribution the real consumer refuses"
    assert _snapshot(home) == before
    if not existing:
        assert not registry.parent.exists()


@pytest.mark.parametrize("mode", ["matching-install", "config-priority", "custom-directory"])
def test_application_provider_canonical_registry_override_real_consumer_positives(
    real_application_bridge_home, monkeypatch, mode,
):
    home, command = real_application_bridge_home
    install = home / "install"
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(install))
    if mode == "config-priority":
        config = home / "config"
        monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(config))
        registry = config / "providers.d"
    elif mode == "custom-directory":
        registry = home / "escape-hatch"
    else:
        registry = install / "providers.d"
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(registry))

    def forbidden_resolve(*_args, **_kwargs):
        pytest.fail("Bridge registry matching must be lexical, not physical resolution")

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "resolve", forbidden_resolve)
        assert provider.register_installed_application() == registry / provider.MANIFEST_NAME
    _real_bridge_classification(command)


@pytest.mark.parametrize("filename", ["aa-large-foreign.json", "zz-large-foreign.json"])
@pytest.mark.parametrize("duplicate_namespace", [False, True])
def test_application_provider_large_valid_foreign_peer_refuses_after_real_consumer(
    real_application_bridge_home, monkeypatch, filename, duplicate_namespace,
):
    home, _command = real_application_bridge_home
    registry = home / "bridge" / "providers.d"
    registry.mkdir(parents=True)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(registry.parent))
    foreign_command = home / ("foreign.cmd" if os.name == "nt" else "foreign")
    foreign_command.write_text("fixture foreign command; never executed\n")
    if os.name != "nt":
        foreign_command.chmod(0o700)
    prefix = '"namespace":"unrelated",' if duplicate_namespace else ""
    content = (
        "{" + prefix + '"padding":' + json.dumps("x" * 70000)
        + ',"namespace":"container","schema_version":0,"command":'
        + json.dumps([str(foreign_command)]) + "}"
    )
    (registry / filename).write_text(content)
    assert (registry / filename).stat().st_size > 65536
    _real_bridge_classification(foreign_command)
    before = _snapshot(home)
    with pytest.raises(RuntimeError, match="namespace provider must be preserved"):
        provider.register_installed_application()
    assert _snapshot(home) == before


@pytest.mark.parametrize("duplicate_namespace", [False, True])
def test_application_provider_large_valid_unrelated_peer_is_classified_completely(
    real_application_bridge_home, monkeypatch, duplicate_namespace,
):
    home, command = real_application_bridge_home
    registry = home / "bridge" / "providers.d"
    registry.mkdir(parents=True)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(registry.parent))
    prefix = '"namespace":"container",' if duplicate_namespace else ""
    peer = registry / "large-unrelated.json"
    peer.write_text(
        "{" + prefix + '"padding":' + json.dumps("x" * 70000)
        + ',"namespace":"unrelated","command":' + json.dumps([str(command)]) + "}"
    )
    before = (peer.read_bytes(), peer.stat().st_mtime_ns)
    provider.register_installed_application()
    assert (peer.read_bytes(), peer.stat().st_mtime_ns) == before
    assert "unrelated" in _real_bridge_classification(command)["namespaces"]


def test_application_provider_large_valid_owned_target_retains_strict_bound(
    real_application_bridge_home, monkeypatch,
):
    home, _command = real_application_bridge_home
    registry = home / "bridge" / "providers.d"
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(registry.parent))
    target = provider.register_installed_application()
    target.write_bytes(target.read_bytes() + b" " * 70000)
    before = _snapshot(home)
    with pytest.raises(RuntimeError, match="Refusing to replace provider entry"):
        provider.register_installed_application()
    assert _snapshot(home) == before


@pytest.mark.parametrize("existing", [False, True])
def test_application_provider_linked_registry_refused_like_real_consumer(
    real_application_bridge_home, monkeypatch, existing, record_property,
):
    from agent_procutil.owned_process import run_caller_owned

    home, command = real_application_bridge_home
    actual = home / "actual-registry"
    actual.mkdir()
    alias = home / "bridge" / "providers.d"
    alias.parent.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(alias.parent))
    if existing:
        with monkeypatch.context() as scoped:
            scoped.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(actual))
            provider.register_installed_application()
    (actual / "foreign.keep").write_bytes(b"fixture-owned peer\n")
    if os.name == "nt":
        root = Path(__file__).resolve().parents[3]
        # Match the existing installation-context junction fixture through the
        # bounded native owner, without symbolic-link privileges or elevation.
        def native(path):
            value = str(path)
            return value if value.startswith("\\\\?\\") else "\\\\?\\" + value

        settled = []
        created = run_caller_owned(
            [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", native(alias), native(actual)],
            timeout=10, max_stdout_bytes=65536, max_stderr_bytes=65536, cwd=root,
            on_settled=lambda: settled.append(True),
        )
        assert settled
        if created.returncode != 0:
            pytest.skip(f"Native junction unavailable: {created.stderr or created.stdout}")
        record_property("registry_link_kind", "native-windows-junction")
    else:
        alias.symlink_to(actual, target_is_directory=True)
        record_property("registry_link_kind", "native-posix-directory-symlink")
    metadata = alias.lstat()
    assert (
        alias.is_symlink() or getattr(metadata, "st_file_attributes", 0) & 0x400
        or getattr(metadata, "st_reparse_tag", 0)
    )
    record_property("registry_reparse_tag", getattr(metadata, "st_reparse_tag", 0))
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(alias))
    result = _real_bridge_classification(command, registry_error=True)
    assert "registry-indeterminate" in result["findings"]
    before = _snapshot(actual)
    with pytest.raises(RuntimeError, match="registry"):
        provider.register_installed_application()
    assert _snapshot(actual) == before
