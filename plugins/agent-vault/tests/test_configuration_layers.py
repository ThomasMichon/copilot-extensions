import json

import pytest
from configuration_layers import ConfigurationError

from agent_vault import config, configuration


def setup(tmp_path, monkeypatch, binding="bound"):
    base, knowledge, local = (tmp_path / part for part in ("base", "knowledge", "local"))
    for root in (base, knowledge, local):
        root.mkdir()
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({
        "vaults": {
            name: {"kpdb": str(tmp_path / f"{name}.kdbx")}
            for name in ("base", "knowledge", "machine", "local", "explicit")
        },
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(global_path))
    for env in ("AGENT_VAULT", "KPDB", "VAULT_GROUP", "AGENT_VAULT_PORT", "AGENT_VAULT_PINNED_CONTEXT"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})
    context = {
        "repository_id": "repo-qualified", "execution_machine": "logical",
        "installation_cell": "cell", "binding": binding, "base_root": str(base),
    }
    if binding == "bound":
        context["knowledge_root"] = str(knowledge)
    report = {
        "schema": "agent-worktrees.configuration", "version": 1, "context": context,
        "repository_machine_root": str(local),
    }
    monkeypatch.setattr(configuration, "owner_report", lambda root: report)
    return base, knowledge, local, global_path, report


def write(root, values):
    (root / ".agent-vault.json").write_text(json.dumps(values), encoding="utf-8")


def test_cli_missing_repository_is_actionable_without_traceback(monkeypatch, capsys):
    from configuration_context import RepositoryContextRequired

    from agent_vault import cli

    def missing():
        raise RepositoryContextRequired()

    monkeypatch.setattr(cli, "ensure_service", missing)
    assert cli.main(["unlock"]) == 3
    error = capsys.readouterr().err
    assert "repository context is missing" in error
    assert "<repo> vault unlock" in error
    assert "Traceback" not in error


def test_payload_reader_reports_missing_repository_without_private_error_text(
    tmp_path, monkeypatch, capsys,
):
    import importlib.util
    import sys
    from pathlib import Path

    from configuration_context import RepositoryContextRequired

    script = Path(__file__).parents[1] / "scripts" / "runtime-activation.py"
    spec = importlib.util.spec_from_file_location("vault_activation_context_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "argv", [str(script), "--cwd", str(tmp_path)])

    def missing(cwd):
        raise RepositoryContextRequired()

    monkeypatch.setattr(configuration, "runtime_enabled", missing)
    assert module.main() == 3
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "failed"
    assert result["reason"] == "repository-context-missing"
    assert "<repo> vault unlock" in result["detail"]
    assert "Traceback" not in result["detail"]


def test_runtime_activation_uses_existing_qualified_layers(tmp_path, monkeypatch):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    assert configuration.runtime_enabled(str(base)) is False
    write(knowledge, {"enabled": True})
    assert configuration.runtime_enabled(str(base)) is True
    data = json.loads(global_path.read_text())
    data["enabled"] = False
    global_path.write_text(json.dumps(data))
    assert configuration.runtime_enabled(str(base)) is False
    write(local, {"enabled": True})
    assert configuration.runtime_enabled(str(base)) is True
    report["context"]["binding"] = "unresolved"
    with pytest.raises(ConfigurationError, match="unresolved"):
        configuration.runtime_enabled(str(base))


def test_batch_reports_invalid_configuration_without_touching_cache(
    tmp_path, monkeypatch, capsys,
):
    import io
    from types import SimpleNamespace

    from agent_vault import cache, cli

    setup(tmp_path, monkeypatch, binding="unresolved")
    monkeypatch.setattr(cache, "get_cache", lambda: pytest.fail("invalid context reached cache"))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(
        '[{"entry":"Example/one"},{"entry":"Example/two"}]',
    ))

    assert cli.main(["batch-get"]) == 1
    captured = capsys.readouterr()
    results = json.loads(captured.out)["results"]
    assert [row["entry"] for row in results] == ["Example/one", "Example/two"]
    assert all(row["ok"] is False and row["value"] is None for row in results)
    assert all(row["source"] == "none" for row in results)
    assert all(row["error"].startswith("context_invalid;") for row in results)
    assert captured.err == ""
    with pytest.raises(ConfigurationError):
        cli.cmd_get(SimpleNamespace(entry="Example/one", field="password"))


@pytest.mark.parametrize("value", [None, "true", 1, {}, []])
def test_runtime_activation_rejects_nonboolean_opt_in(tmp_path, monkeypatch, value):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    write(knowledge, {"enabled": value})
    with pytest.raises(RuntimeError, match="enabled must be a boolean"):
        configuration.runtime_enabled(str(base))


def test_standalone_activation_needs_no_worktrees_or_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(configuration, "owner_report", lambda root: None)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "absent-global.json"))
    assert configuration.runtime_enabled(str(tmp_path)) is False
    write(tmp_path, {"enabled": True})
    assert configuration.runtime_enabled(str(tmp_path)) is True
    write(tmp_path, {"enabled": False})
    assert configuration.runtime_enabled(str(tmp_path)) is False


def test_native_vault_selector_all_tiers_without_opening_database(tmp_path, monkeypatch):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    write(base, {"vault": "base", "group": "base-group"})
    write(knowledge, {"vault": "knowledge", "group": "knowledge-group"})
    assert config.resolve_context(str(base), strict=True).vault_name == "knowledge"
    global_data = json.loads(global_path.read_text())
    global_data["default_vault"] = "machine"
    global_data["group"] = "machine-group"
    global_path.write_text(json.dumps(global_data))
    assert config.resolve_context(str(base), strict=True).vault_name == "machine"
    write(local, {"vault": "local", "group": "local-group"})
    result = config.resolve_context(str(base), strict=True)
    assert result.vault_name == "local"
    assert result.group == "local-group"
    assert result.kpdb == str(tmp_path / "local.kdbx")
    assert not (tmp_path / "local.kdbx").exists()
    assert result.configuration_provenance["/vault"]["source"]["root"] == str(local)
    monkeypatch.setenv("AGENT_VAULT", "explicit")
    monkeypatch.setenv("VAULT_GROUP", "explicit-group")
    result = config.resolve_context(str(base), strict=True)
    assert result.vault_name == "explicit"
    assert result.group == "explicit-group"
    assert "/vault" not in result.configuration_provenance
    assert "/group" not in result.configuration_provenance


def test_native_unbound_and_unresolved_are_distinct(tmp_path, monkeypatch):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch, "unbound")
    write(base, {"vault": "base"})
    write(knowledge, {"vault": "knowledge"})
    assert config.resolve_context(str(base), strict=True).vault_name == "base"
    report["context"]["binding"] = "unresolved"
    with pytest.raises(ConfigurationError, match="unresolved"):
        config.resolve_context(str(base), strict=True)


@pytest.mark.parametrize("protected", ["kpdb", "port", "vaults", "socket_path", "cache", "kek"])
def test_portable_vault_selection_refuses_protected_state(tmp_path, monkeypatch, protected):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    write(knowledge, {protected: {}})
    with pytest.raises(ValueError, match="custody"):
        config.resolve_context(str(base), strict=True)


def test_native_repository_database_path_retains_declaring_owner(tmp_path, monkeypatch):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    write(base, {"vault": "base", "kpdb": "native.kdbx"})
    write(knowledge, {"group": "portable-group"})
    result = config.resolve_context(str(base), strict=True)
    assert result.kpdb == str(base / "native.kdbx")
    assert result.sources["kpdb"] == "repo"


def test_native_project_scope_preserves_database_expansion(tmp_path, monkeypatch):
    from agent_vault.invocation import use_configuration_root

    base, _knowledge, _local, _global_path, _report = setup(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    expected = str(tmp_path / "${VAULT_TEST_LITERAL}" / "chosen.kdbx")
    monkeypatch.setenv("VAULT_TEST_DATABASE", "${VAULT_TEST_LITERAL}/chosen.kdbx")
    monkeypatch.setenv("VAULT_TEST_LITERAL", "must-not-expand-recursively")
    monkeypatch.setenv("KPDB", "${VAULT_TEST_DATABASE}")
    with use_configuration_root(str(base)):
        result = config.resolve_context(strict=True)
    assert result.kpdb == expected
    assert result.sources["kpdb"] == "env"


def test_native_project_scope_selects_repo_without_changing_cwd_or_native_paths(
    tmp_path, monkeypatch, capsys,
):
    from agent_vault import cli

    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    write(home, {"group": "caller", "kpdb": "caller.kdbx"})
    write(project, {"group": "selected", "enabled": True})
    (home / "native.json").write_text(json.dumps({"kpdb": "machine.kdbx"}), encoding="utf-8")
    monkeypatch.chdir(home)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", "native.json")
    monkeypatch.delenv("KPDB", raising=False)
    monkeypatch.delenv("VAULT_GROUP", raising=False)
    monkeypatch.setattr(configuration, "owner_report", lambda root: None)
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})

    assert cli.main(["--configuration-root", str(project), "which", "--json"]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected["group"] == "selected"
    assert selected["kpdb"] == str(home / "machine.kdbx")
    assert config.os.getcwd() == str(home)
    assert config.os.environ["AGENT_VAULT_CONFIG"] == "native.json"
    assert cli.main(["which", "--json"]) == 0
    previous = json.loads(capsys.readouterr().out)
    assert previous["group"] == "caller"
    assert previous["kpdb"] == str(home / "caller.kdbx")


@pytest.mark.parametrize("already_unlocked", [False, True])
def test_native_project_unlock_tags_real_requests_with_selected_database(
    tmp_path, monkeypatch, capsys, already_unlocked,
):
    from agent_vault import cli, extensions

    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir()
    project.mkdir()
    write(home, {"enabled": False, "kpdb": "wrong.kdbx"})
    write(project, {"enabled": True, "kpdb": "selected.kdbx"})
    monkeypatch.chdir(home)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "absent-native.json"))
    monkeypatch.setenv("AGENT_VAULT_HOST", "127.0.0.1")
    for name in ("KPDB", "AGENT_VAULT", "VAULT_GROUP", "AGENT_VAULT_PINNED_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(configuration, "owner_report", lambda root: None)
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})
    monkeypatch.setattr(extensions, "get_registry", lambda: extensions.ExtensionRegistry())
    monkeypatch.setattr(cli, "_discover_endpoint", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli, "gui_prompt_password", lambda *args: pytest.fail("unexpected password prompt"),
    )
    expected = str(project / "selected.kdbx")
    requests = []
    unlocked = already_unlocked

    def transport(request, *args, **kwargs):
        nonlocal unlocked
        requests.append(dict(request))
        if request["action"] == "unlock":
            unlocked = True
        return {
            "ok": True, "cli": "unlocked" if unlocked else "locked",
            "unlocked_vaults": [expected] if unlocked else [],
        }

    monkeypatch.setattr(cli, "_send_socket", transport)
    monkeypatch.setattr(cli, "_send_tcp", transport)
    assert cli.main(["--configuration-root", str(project), "unlock"]) == 0
    assert "Vault available" in capsys.readouterr().out
    assert all(request["kpdb"] == expected for request in requests)
    assert any(request["action"] == "unlock" for request in requests) is not already_unlocked
    assert config.os.getcwd() == str(home)


@pytest.mark.parametrize("arguments", [
    ["--configuration-root"],
    ["--configuration-root", "relative", "unlock"],
    ["--configuration-root", "one", "--configuration-root", "two", "unlock"],
])
def test_native_project_scope_rejects_invalid_requests_before_dispatch(
    monkeypatch, arguments,
):
    from agent_vault import cli

    monkeypatch.setattr(cli, "_main", lambda argv: pytest.fail("invalid scope reached dispatch"))
    with pytest.raises(SystemExit) as error:
        cli.main(arguments)
    assert error.value.code == 2


def test_native_invocation_contract_is_read_only(monkeypatch, capsys):
    from agent_vault import cli

    monkeypatch.setattr(cli, "_main", lambda argv: pytest.fail("metadata reached dispatch"))
    assert cli.main(["invocation-contract", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema": "agent-vault.invocation", "version": 1,
        "capabilities": [
            "configuration-root-v1", "batch-get-v1", "ssh-key-install-pin-v1",
            "authenticated-curl-v1", "daemon-bind-default-v1", "preserved-cache-recovery-v1",
        ],
    }


@pytest.mark.parametrize("router_owned", [False, True])
def test_native_project_scope_consumes_only_router_owned_provisioning_guard(
    tmp_path, monkeypatch, capsys, router_owned,
):
    from agent_vault import cli

    monkeypatch.setenv("AGENT_VAULT_NO_SELFPROVISION", "1")
    monkeypatch.delenv("_AGENT_WORKTREES_VAULT_RUNTIME_GUARD", raising=False)
    if router_owned:
        monkeypatch.setenv("_AGENT_WORKTREES_VAULT_RUNTIME_GUARD", "1")
    assert cli.main(
        ["--configuration-root", str(tmp_path), "invocation-contract", "--json"],
    ) == 0
    capsys.readouterr()
    assert ("AGENT_VAULT_NO_SELFPROVISION" in config.os.environ) is not router_owned
    assert "_AGENT_WORKTREES_VAULT_RUNTIME_GUARD" not in config.os.environ


def test_native_project_scope_preserves_literal_argument_after_separator(tmp_path):
    from agent_vault.invocation import parse_invocation

    root, remaining = parse_invocation(
        ["--configuration-root", str(tmp_path), "search", "--", "--configuration-root"],
    )
    assert root == str(tmp_path)
    assert remaining == ["search", "--", "--configuration-root"]


def test_native_project_scope_composes_with_resident_source(tmp_path, monkeypatch):
    from agent_vault import configuration_scope
    from agent_vault.invocation import use_configuration_root

    resident = tmp_path / "resident"
    resident.mkdir()
    monkeypatch.setattr(configuration_scope, "_SERVICE_CONFIGURATION_ROOT", None)
    config.bind_service_configuration(str(resident))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", "native.json")
    assert config.configuration_root() == str(tmp_path / "resident")
    with use_configuration_root(str(tmp_path / "project")):
        assert config.configuration_root() == str(tmp_path / "project")
        assert config.configuration_root(str(tmp_path / "explicit")) == str(tmp_path / "explicit")
        assert config._global_config_path() == tmp_path / "native.json"
    assert config.configuration_root() == str(tmp_path / "resident")


@pytest.mark.parametrize("resident", [False, True])
def test_native_project_scope_preserves_extension_default_and_resident_selection(
    tmp_path, monkeypatch, resident,
):
    from pathlib import Path

    from agent_vault import configuration_scope, extensions
    from agent_vault.invocation import use_configuration_root

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "absent.json"))
    for name in ("AGENT_VAULT", "KPDB", "VAULT_GROUP", "AGENT_VAULT_PORT", "AGENT_VAULT_PINNED_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(configuration_scope, "_SERVICE_CONFIGURATION_ROOT", None)
    monkeypatch.setattr(configuration, "owner_report", lambda root: None)
    expected_hint = None
    if resident:
        selected = tmp_path / "resident"
        selected.mkdir()
        config.bind_service_configuration(str(selected))
        expected_hint = str(selected)
    seen = []

    def contribute(cwd):
        seen.append(cwd)
        name = Path(cwd).name if cwd is not None else "default"
        return {"kpdb": str(tmp_path / f"{name}.kdbx"), "group": name}

    registry = extensions.ExtensionRegistry()
    registry.register_config_source(contribute)
    monkeypatch.setattr(extensions, "_REGISTRY", registry)
    expected_name = "resident" if resident else "default"
    before = config.resolve_context(strict=True)
    with use_configuration_root(str(project)):
        scoped = config.resolve_context(strict=True)
    after = config.resolve_context(strict=True)
    assert before.kpdb == after.kpdb == str(tmp_path / f"{expected_name}.kdbx")
    assert before.group == after.group == expected_name
    assert scoped.kpdb == str(tmp_path / "project.kdbx")
    assert scoped.group == "project"
    assert seen == [expected_hint, str(project), expected_hint]


def test_actual_absent_owner_keeps_unbound_vault_defaults_without_open_or_bootstrap(tmp_path, monkeypatch):
    import configuration_context

    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT",
                 "AGENT_VAULT", "KPDB", "VAULT_GROUP", "AGENT_VAULT_PORT",
                 "AGENT_VAULT_PINNED_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "absent-global.json"))
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})

    def no_process(*args, **kwargs):
        pytest.fail("vault selection must not install an owner or open a vault")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    result = config.resolve_context(str(tmp_path), strict=True)
    assert result.vault_name is None
    assert result.kpdb is None
    assert result.port == config.DEFAULT_TCP_PORT
    assert list(tmp_path.iterdir()) == []


def test_native_unready_legacy_owner_preserves_vault_defaults(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    import configuration_context

    home = tmp_path / "home"
    scripts = home / ".agent-vault" / "scripts"
    scripts.mkdir(parents=True)
    for source in (Path(configuration.__file__).parents[2] / "scripts").glob("versioned_runtime*.py"):
        shutil.copy2(source, scripts / source.name)
    peer = home / ".agent-worktrees"
    fake_python = peer / "versions" / "1.0.0" / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("not a completed runtime", encoding="utf-8")
    (peer / "current-version").write_text("1.0.0", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("AGENT_VAULT_HOME", "AGENT_RT_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
                 "AGENT_CONFIGURATION_CONTEXT", "AGENT_VAULT", "KPDB", "VAULT_GROUP",
                 "AGENT_VAULT_PORT", "AGENT_VAULT_PINNED_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "absent-global.json"))
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})

    def no_process(*args, **kwargs):
        pytest.fail("an unready optional owner must not bootstrap or open a vault")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    assert config.resolve_context(str(tmp_path), strict=True).kpdb is None
    assert (scripts / "versioned_runtime.py").is_file()
    assert configuration._legacy_report(tmp_path) is None


@pytest.mark.parametrize("enabled,owner_code", [
    (True, 0), (False, 0), (None, 0), ("invalid", 0), (True, 126),
])
@pytest.mark.parametrize("layout", ["versioned", "application", "adjacent"])
def test_installed_reader_uses_retained_sdk_for_qualified_activation(
    tmp_path, monkeypatch, capsys, enabled, owner_code, layout,
):
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    real_report = configuration.owner_report
    base, knowledge, _local, _global_path, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    if enabled is not None:
        write(knowledge, {"enabled": enabled, "vault": "knowledge"})
    home = tmp_path / "home"
    peer = home / ".agent-worktrees"
    python = peer / "versions" / "1.0.0" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture interpreter; never executed")
    (peer / "current-version").write_text("1.0.0", encoding="utf-8")
    sdk_source = Path(configuration.__file__).parents[2] / "scripts"
    slot = home / ".agent-vault" / "versions" / "1.0.0"
    if layout == "adjacent":
        slot = tmp_path / "standalone consumer"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    for source in sdk_source.glob("versioned_runtime*.py"):
        shutil.copy2(source, bootstrap / source.name)
    sdk = configuration._peer_launch._load_primitive(
        bootstrap / "versioned_runtime.py", "_vault_installed_fixture_runtime",
    )
    sdk.mark_complete(peer, "1.0.0")
    environment = slot / ".venv" if layout == "application" else slot
    installed = environment / "Lib" / "site-packages" / "agent_vault" / "configuration.py"
    if layout == "adjacent":
        installed = slot / "agent_vault" / "configuration.py"
        environment = tmp_path / "independent interpreter"
    monkeypatch.setattr(configuration, "__file__", str(installed))
    monkeypatch.setattr(sys, "prefix", str(environment))
    monkeypatch.setattr(Path, "home", lambda: home)
    staging = tmp_path / "installer-stage"
    staging.mkdir()
    monkeypatch.chdir(staging)
    for name in ("AGENT_RT_ROOT", "AGENT_VAULT_HOME", "COPILOT_EXTENSIONS_CONTEXT",
                 "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    calls = []

    def run(argv, **kwargs):
        assert argv == [
            str(python), "-I", "-B", "-X", "utf8", "-m", "agent_worktrees",
            "state-root", "--configuration", "--json",
        ]
        assert kwargs["cwd"] == base
        assert "AGENT_CONFIGURATION_CONTEXT" not in kwargs["env"]
        calls.append(argv)
        return subprocess.CompletedProcess(argv, owner_code, json.dumps(report).encode(), b"")

    monkeypatch.setattr(subprocess, "run", run)
    if owner_code:
        with pytest.raises(ConfigurationError, match="refused the selected context"):
            configuration.runtime_enabled(str(base))
    elif enabled == "invalid":
        with pytest.raises(RuntimeError, match="enabled must be a boolean"):
            configuration.runtime_enabled(str(base))
    else:
        assert configuration.runtime_enabled(str(base)) is (enabled is True)
        from agent_vault import cli

        probes = []

        def ping(request, **kwargs):
            assert request == {"action": "ping"}
            assert kwargs == {"strict_endpoint": True}
            probes.append(request)
            return {"ok": True, "cli": "locked"}

        monkeypatch.setattr(cli, "send_command", ping)
        monkeypatch.chdir(base)
        assert cli.cmd_installer_readiness(None) == 0
        readiness = json.loads(capsys.readouterr().out)
        assert readiness["state"] == ("ready" if enabled is True else "configuration-empty")
        assert len(probes) == int(enabled is True)
    assert len(calls) == (3 if enabled is True and not owner_code else
                          2 if enabled in (False, None) else 1)
    assert not (home / ".agent-vault" / "scripts").exists()
    assert not (installed.parents[2] / "scripts").exists()


@pytest.mark.parametrize("implicit", [False, True])
@pytest.mark.parametrize("matching_cell", [False, True])
def test_real_loader_checks_delivered_cell_against_actual_owner(tmp_path, monkeypatch, implicit, matching_cell):
    real_report = configuration.owner_report
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    write(base, {"vault": "base"})
    write(knowledge, {"vault": "knowledge"})
    cell = tmp_path / "marketplaces" / "current-cell"
    own_root = cell / "plugins" / "agent-vault"
    own_root.mkdir(parents=True)
    report["context"]["installation_cell"] = str(cell if matching_cell else tmp_path / "foreign-cell")
    calls = []

    def validate(owner, root, raw):
        calls.append((owner, root, raw))
        assert root == own_root
        assert raw == str(own_root / "install.json")
        return {"cellRoot": str(cell), "pluginRoot": str(own_root)}

    monkeypatch.setattr(configuration._peer_launch, "validate_owner", validate)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own_root))
    if implicit:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_root / "install.json"))
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    if matching_cell:
        assert config.resolve_context(str(base), strict=True).vault_name == "knowledge"
    else:
        with pytest.raises(ConfigurationError, match="another installation cell"):
            config.resolve_context(str(base), strict=True)
    assert calls


@pytest.mark.parametrize("shape", ["flat", "nested", "foreign", "deep"])
def test_qualified_deployed_owner_layout_and_refusal(tmp_path, shape):
    import os
    import shutil
    import subprocess
    from pathlib import Path

    from configuration_context.runtime import deployed_runtime_python

    root = tmp_path / "owner"
    resolver = tmp_path / ("resolve-runtime.ps1" if os.name == "nt" else "resolve-runtime.sh")
    shutil.copyfile(Path(configuration.__file__).parents[2] / "scripts" / resolver.name, resolver)
    environment = root / "versions" / "1.2.3"
    if shape == "nested":
        environment /= ".venv"
    elif shape == "foreign":
        environment = tmp_path / "foreign"
    elif shape == "deep":
        environment /= "extra/.venv"
    python = environment / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture interpreter; never executed")

    def resolve():
        return deployed_runtime_python(
            root, resolver=resolver, environment={"SystemRoot": str(tmp_path)},
            runner=lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 0, str(python).encode("utf-8"), b"",
            ),
        )

    if shape in ("foreign", "deep"):
        with pytest.raises(ConfigurationError, match="escaped"):
            resolve()
    else:
        assert resolve() == python


@pytest.mark.parametrize("tier", ["knowledge", "repository_machine"])
def test_tier_null_keeps_native_fallback_and_does_not_claim_false_provenance(tmp_path, monkeypatch, tier):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    write(base, {"vault": "base", "group": "base-group"})
    if tier == "knowledge":
        write(knowledge, {"vault": None, "group": None})
        monkeypatch.setattr(config, "_ext_config", lambda cwd: {
            "vault": "explicit", "group": "extension-group",
        })
        expected_vault, expected_group = "explicit", "extension-group"
        vault_source, group_source = "ext", "ext"
    else:
        write(knowledge, {"vault": "knowledge", "group": "knowledge-group"})
        data = json.loads(global_path.read_text())
        data.update(default_vault="machine", group="machine-group")
        data["vaults"]["machine"]["group"] = "registered-group"
        global_path.write_text(json.dumps(data), encoding="utf-8")
        write(local, {"vault": None, "group": None})
        expected_vault, expected_group = "machine", "registered-group"
        vault_source, group_source = "global", "vault:machine"
    result = config.resolve_context(str(base), strict=True)
    assert (result.vault_name, result.group) == (expected_vault, expected_group)
    assert result.sources["vault"] == vault_source
    assert result.sources["group"] == group_source
    assert "/vault" not in result.configuration_provenance
    assert "/group" not in result.configuration_provenance
    assert not (tmp_path / f"{expected_vault}.kdbx").exists()


@pytest.mark.parametrize("code,diagnostic", [
    (2, "unrecognized arguments: --configuration"),
    (2, "unknown option: --configuration"),
    (1, "binding refused"), (3, "binding unresolved"), (126, "owner refused"),
    (2, "unrecognized arguments: --unrelated"), (0, "malformed"),
])
def test_optional_older_peer_preserves_native_vault_or_fails_real_refusal(tmp_path, monkeypatch, code, diagnostic):
    import subprocess
    real_report = configuration.owner_report
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    write(base, {"vault": "base"})
    cell = tmp_path / "marketplaces" / "current-cell"
    own = cell / "plugins" / "agent-vault"
    (cell / "plugins" / "agent-worktrees").mkdir(parents=True)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own / "install.json"))
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    monkeypatch.setattr(configuration._peer_launch, "validate_owner", lambda *args:
                        {"cellRoot": str(cell), "pluginRoot": str(own)})
    monkeypatch.setattr(configuration._peer_launch, "launch_prefix", lambda *args: ["qualified-peer"])
    calls = []

    def run(argv, **kwargs):
        assert argv == ["qualified-peer", "state-root", "--configuration", "--json"]
        calls.append(argv)
        return subprocess.CompletedProcess(argv, code, b"{bad-json", diagnostic.encode())

    monkeypatch.setattr(subprocess, "run", run)
    if code == 2 and diagnostic.endswith("--configuration"):
        assert config.resolve_context(str(base), strict=True).vault_name == "base"
    else:
        with pytest.raises(ConfigurationError):
            config.resolve_context(str(base), strict=True)
    assert len(calls) == 1


def test_unqualified_delivery_cannot_be_admitted_by_optional_absence(tmp_path, monkeypatch):
    real_report = configuration.owner_report
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_RT_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    monkeypatch.setattr(configuration, "_legacy_report", lambda root: None)
    with pytest.raises(ConfigurationError, match="matching qualified owner"):
        config.resolve_context(str(base), strict=True)
