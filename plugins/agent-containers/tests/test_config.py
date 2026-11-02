"""Tests for config loading and ACP command resolution."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import textwrap

import pytest
import yaml

from agent_containers.config import (
    DEFAULT_ACP_COMMAND,
    ContainersConfig,
    FleetConfig,
    load_config,
)


@pytest.fixture
def layered_container_context(tmp_path, monkeypatch):
    from agent_containers import config

    base, knowledge, machine = (tmp_path / name for name in ("base", "knowledge", "machine"))
    for root in (base, knowledge, machine):
        root.mkdir()
    report = {
        "schema": "agent-worktrees.configuration",
        "version": 1,
        "context": {
            "repository_id": "repo-qualified", "execution_machine": "logical-worker",
            "installation_cell": "cell-one", "binding": "bound",
            "base_root": str(base), "knowledge_root": str(knowledge),
        },
    }
    monkeypatch.setattr(config, "_configuration_report", lambda: report)
    monkeypatch.setattr(config, "RUNTIME_DIR", machine)
    monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
    return base, knowledge, machine, report


def _write_container_layer(root, data, *, local=False):
    target = root / ".copilot-extensions" / "agent-containers"
    target.mkdir(parents=True, exist_ok=True)
    target = target / ("containers.local.yaml" if local else "containers.yaml")
    target.write_text(yaml.safe_dump({"schema_version": 2, **data}), encoding="utf-8")
    return target


def test_layered_container_real_load_precedence_and_source_paths(layered_container_context):
    base, knowledge, machine, _ = layered_container_context
    _write_container_layer(base, {"exec_user": "base", "workspace_folder": "/base", "image_prefixes": ["base"]})
    knowledge_file = _write_container_layer(knowledge, {
        "exec_user": "knowledge", "image_prefixes": ["knowledge"],
        "fleets": {"worker": {"devcontainer_path": "../workspace"}},
    })
    (machine / "containers.yaml").write_text("schema_version: 2\nexec_user: machine\n", encoding="utf-8")
    _write_container_layer(base, {"exec_user": "local"}, local=True)
    effective = load_config(strict=True)
    assert effective.exec_user == "local"
    assert effective.workspace_folder == "/base"
    assert effective.image_prefixes == ["knowledge"]
    assert effective.fleets["worker"].devcontainer_path == str(knowledge_file.parent.parent / "workspace")
    assert len(effective.configuration_layers) == 5
    assert effective.configuration_provenance["/image_prefixes"]["origin"] == "knowledge"
    assert not (machine / "leases.json").exists()


def test_layered_container_unbound_defaults_ignore_knowledge(layered_container_context):
    base, knowledge, _, report = layered_container_context
    _write_container_layer(knowledge, {"exec_user": "private"})
    report["context"]["binding"] = "unbound"
    del report["context"]["knowledge_root"]
    assert load_config(strict=True).exec_user == "vscode"
    _write_container_layer(base, {"exec_user": "portable"})
    assert load_config(strict=True).exec_user == "portable"


def test_layered_container_explicit_selector_preserves_one_file(
    layered_container_context, monkeypatch, tmp_path,
):
    from agent_containers import config

    base, _, _, _ = layered_container_context
    _write_container_layer(base, {"exec_user": "base"})
    selected = tmp_path / "explicit.yaml"
    selected.write_text("schema_version: 2\nexec_user: explicit\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(selected))
    monkeypatch.setattr(config, "_installation_owner", lambda: None)

    def unavailable_owner():
        raise RuntimeError("configuration owner must not be queried for an explicit file")

    monkeypatch.setattr(config, "_configuration_report", unavailable_owner)
    assert load_config(strict=True).exec_user == "explicit"
    selected.unlink()
    assert load_config(strict=True).exec_user == "vscode"


def test_layered_container_binding_and_security_refusals(layered_container_context):
    from configuration_layers import ConfigurationError

    base, _, _, report = layered_container_context
    report["context"]["binding"] = "unresolved"
    with pytest.raises(ConfigurationError):
        load_config(strict=True)
    report["context"]["binding"] = "bound"
    _write_container_layer(base, {"fleets": {"worker": {"dispatch_only": "false"}}})
    with pytest.raises(RuntimeError, match="must be a boolean"):
        load_config(strict=True)


def test_layered_container_retains_native_nonstrict_and_nullable_contracts(
    layered_container_context,
):
    base, _, _, _ = layered_container_context
    _write_container_layer(base, {"exec_user": "portable", "fleets": []})
    assert load_config().exec_user == "portable"
    with pytest.raises(RuntimeError, match="fleets config must be a key/value mapping"):
        load_config(strict=True)
    _write_container_layer(base, {"fleets": {"worker": {"acp_command": None}}})
    assert load_config(strict=True).fleets["worker"].acp_command is None
    _write_container_layer(base, {"fleets": {"worker": {"copilot_auth_mode": None}}})
    with pytest.raises(RuntimeError, match="copilot_auth_mode must be"):
        load_config(strict=True)


@pytest.mark.parametrize("exit_code,version", [(1, 1), (0, 2)])
def test_layered_container_native_owner_refusal_never_selects_defaults(
    tmp_path, monkeypatch, exit_code, version,
):
    import json

    from configuration_layers import ConfigurationError
    from agent_containers import config

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "admitted-by-native-boundary")
    (tmp_path / "plugins" / "agent-worktrees").mkdir(parents=True)
    monkeypatch.setattr(config, "_installation_owner", lambda: {
        "pluginRoot": str(tmp_path), "cellRoot": str(tmp_path),
    })
    monkeypatch.setattr(config._peer_launch, "launch_prefix", lambda *args: ["qualified-owner"])
    report = {
        "schema": "agent-worktrees.configuration", "version": version,
        "context": {
            "repository_id": "repo-qualified", "execution_machine": "logical-worker",
            "installation_cell": "cell-one", "binding": "unbound", "base_root": str(tmp_path),
        },
    }
    calls = []

    def owner_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, exit_code, json.dumps(report).encode("utf-8"), b"explicit binding refused",
        )

    monkeypatch.setattr("configuration_context.subprocess.run", owner_run)
    with pytest.raises(ConfigurationError):
        load_config()
    assert calls == [["qualified-owner", "state-root", "--configuration", "--json"]]


def test_layered_container_default_read_uses_delivered_context_and_rejects_foreign_cell(
    tmp_path, monkeypatch,
):
    import json

    from configuration_layers import ConfigurationError
    from agent_containers import config

    base, knowledge, machine = (tmp_path / name for name in ("base", "knowledge", "machine"))
    for root in (base, knowledge, machine):
        root.mkdir()
    _write_container_layer(base, {"exec_user": "base"})
    _write_container_layer(knowledge, {"exec_user": "knowledge"})
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "repo-qualified", "execution_machine": "logical-worker",
            "installation_cell": "cell-one", "binding": "bound", "base_root": str(base),
            "knowledge_root": str(knowledge),
        },
    }
    monkeypatch.setattr(config, "_installation_owner", lambda: None)
    monkeypatch.setattr(config, "RUNTIME_DIR", machine)
    monkeypatch.chdir(base)
    monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    assert load_config(strict=True).exec_user == "knowledge"
    monkeypatch.setattr(config, "_installation_owner", lambda: {"cellRoot": "other-cell"})
    with pytest.raises(ConfigurationError, match="another installation cell"):
        load_config(strict=True)


def test_layered_container_absent_owner_is_standalone_without_bootstrap(tmp_path, monkeypatch):
    from agent_containers import config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(config, "_installation_owner", lambda: None)
    for name in ("AGENT_CONFIGURATION_CONTEXT", "COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONTAINERS_CONFIG"):
        monkeypatch.delenv(name, raising=False)

    def refuse_spawn(*args, **kwargs):
        raise AssertionError("ordinary config read must not invoke an ambient owner")

    monkeypatch.setattr(subprocess, "run", refuse_spawn)
    assert load_config(strict=True).exec_user == "vscode"
    assert list(tmp_path.iterdir()) == []


def test_layered_container_foreign_repository_projection_keeps_selected_source(tmp_path, monkeypatch):
    import json

    from agent_containers import config

    selected = tmp_path / "selected" / "repo"
    foreign = tmp_path / "foreign" / "repo"
    machine = tmp_path / "machine"
    for root in (selected, foreign, machine):
        root.mkdir(parents=True)
    _write_container_layer(selected, {"exec_user": "selected"})
    _write_container_layer(foreign, {"exec_user": "foreign"})
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "foreign-repository", "execution_machine": "worker",
            "installation_cell": "cell-one", "binding": "unbound", "base_root": str(foreign),
        },
    }
    monkeypatch.chdir(selected)
    monkeypatch.setattr(config, "RUNTIME_DIR", machine)
    monkeypatch.setattr(config, "_installation_owner", lambda: None)
    monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    assert load_config(strict=True).exec_user == "selected"


def test_layered_container_implicit_namespace_refuses_delivered_report(tmp_path, monkeypatch):
    import json

    from agent_containers import config

    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "qualified-repository", "execution_machine": "worker",
            "installation_cell": "foreign-cell", "binding": "unbound", "base_root": str(tmp_path),
        },
    }
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        config, "RUNTIME_DIR",
        tmp_path / "marketplaces" / "selected-cell" / "plugins" / "agent-containers",
    )
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    with pytest.raises(config._peer_launch.ContextRefused, match="context is missing"):
        load_config(strict=True)


@pytest.fixture
def optional_containers_owner(tmp_path, monkeypatch):
    from agent_containers import config

    repository = tmp_path / "repository"
    repository.mkdir()
    cell = tmp_path / "nested" / "marketplaces" / "selected-cell"
    own_root = cell / "plugins" / "agent-containers"
    own_root.mkdir(parents=True)
    events = []

    def qualify(*args):
        events.append("qualify")
        return {"cellRoot": str(cell), "pluginRoot": str(own_root)}

    monkeypatch.chdir(repository)
    monkeypatch.setattr(config, "RUNTIME_DIR", own_root)
    monkeypatch.setattr(config._peer_launch, "validate_owner", qualify)
    monkeypatch.setattr(config._peer_launch, "launch_prefix", lambda *args: ["qualified-owner"])
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "qualified-owner-receipt")
    monkeypatch.setenv("AGENT_CONTAINERS_HOME", str(own_root))
    monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    return repository, own_root, cell / "plugins" / "agent-worktrees", events


@pytest.mark.parametrize("peer_state", ["missing", "older"])
@pytest.mark.parametrize("native_file", [False, True])
def test_optional_containers_owner_preserves_namespace_files_and_defaults(
    optional_containers_owner, monkeypatch, peer_state, native_file,
):
    _, own_root, peer, events = optional_containers_owner
    if peer_state == "older":
        peer.mkdir()
    if native_file:
        (own_root / "containers.yaml").write_text(
            "schema_version: 2\nexec_user: native\n", encoding="utf-8",
        )

    def run(argv, **kwargs):
        assert peer_state == "older", "missing optional peer must not be launched"
        events.append("query")
        assert argv == ["qualified-owner", "state-root", "--configuration", "--json"]
        return subprocess.CompletedProcess(
            argv, 2, b"", b"unrecognized arguments: --configuration",
        )

    monkeypatch.setattr("configuration_context.subprocess.run", run)
    assert load_config(strict=True).exec_user == ("native" if native_file else "vscode")
    assert events[0] == "qualify"
    assert ("query" in events) == (peer_state == "older")
    assert {path.name for path in own_root.iterdir()} == ({"containers.yaml"} if native_file else set())


@pytest.mark.parametrize("exit_code", [1, 3, 126, 2])
def test_optional_containers_owner_present_refusal_never_falls_back(
    optional_containers_owner, monkeypatch, exit_code,
):
    from configuration_layers import ConfigurationError

    _, _, peer, events = optional_containers_owner
    peer.mkdir()

    def run(argv, **kwargs):
        events.append("query")
        return subprocess.CompletedProcess(
            argv, exit_code, b"", b"configured binding or runtime admission refused",
        )

    monkeypatch.setattr("configuration_context.subprocess.run", run)
    with pytest.raises(ConfigurationError, match="refused"):
        load_config()
    assert events == ["qualify", "query"]


@pytest.mark.parametrize("invalid_report", ["foreign-cell", "foreign-cell-other-repo", "unresolved"])
def test_optional_containers_owner_rejects_delivered_state_before_absence(
    optional_containers_owner, monkeypatch, invalid_report,
):
    import json

    from configuration_layers import ConfigurationError

    repository, _, peer, events = optional_containers_owner
    context = {
        "repository_id": "qualified-repository", "execution_machine": "worker",
        "installation_cell": str(peer.parent.parent), "binding": "unbound",
        "base_root": str(repository),
    }
    if invalid_report.startswith("foreign-cell"):
        context["installation_cell"] = "foreign-cell"
    if invalid_report == "foreign-cell-other-repo":
        context["base_root"] = str(repository.parent)
    if invalid_report == "unresolved":
        context["binding"] = "unresolved"
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps({
        "schema": "agent-worktrees.configuration", "version": 1, "context": context,
    }))
    with pytest.raises(ConfigurationError):
        load_config()
    assert events == ["qualify"]
    assert not peer.exists()


@pytest.mark.parametrize("failure", ["owner", "inspection", "execution"])
def test_optional_containers_owner_ambiguous_failure_never_selects_defaults(
    optional_containers_owner, monkeypatch, failure,
):
    import json
    from pathlib import Path

    from configuration_layers import ConfigurationError
    from agent_containers import config

    repository, _, peer, events = optional_containers_owner
    if failure == "owner":
        monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps({
            "schema": "agent-worktrees.configuration", "version": 1,
            "context": {
                "repository_id": "qualified-repository", "execution_machine": "worker",
                "installation_cell": str(peer.parent.parent), "binding": "unbound",
                "base_root": str(repository),
            },
        }))

        def refuse_owner(*args):
            events.append("qualify")
            raise ValueError("fresh owner qualification refused")

        monkeypatch.setattr(config._peer_launch, "validate_owner", refuse_owner)
    elif failure == "inspection":
        original = Path.lstat

        def deny_inspection(path, *args, **kwargs):
            if path == peer:
                raise PermissionError("optional peer directory is unreadable")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", deny_inspection)
    else:
        peer.mkdir()

        def refuse_execution(argv, **kwargs):
            events.append("query")
            raise PermissionError("qualified owner execution failed")

        monkeypatch.setattr("configuration_context.subprocess.run", refuse_execution)
    with pytest.raises((ConfigurationError, config._peer_launch.ContextRefused)):
        load_config()
    assert events == (["qualify", "query"] if failure == "execution" else ["qualify"])


def test_optional_containers_owner_native_foreign_cell_is_not_accepted(
    optional_containers_owner, monkeypatch,
):
    import json

    from configuration_layers import ConfigurationError

    repository, _, peer, events = optional_containers_owner
    peer.mkdir()
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "qualified-repository", "execution_machine": "worker",
            "installation_cell": "foreign-cell", "binding": "unbound", "base_root": str(repository),
        },
    }

    def run(argv, **kwargs):
        events.append("query")
        return subprocess.CompletedProcess(argv, 0, json.dumps(report).encode("utf-8"), b"")

    monkeypatch.setattr("configuration_context.subprocess.run", run)
    with pytest.raises(ConfigurationError, match="another installation cell"):
        load_config()
    assert events == ["qualify", "query"]


def test_layered_container_first_touch_isolated_import_operation(tmp_path):
    import json

    base, knowledge, machine = (tmp_path / name for name in ("base", "knowledge", "machine"))
    for root in (base, knowledge, machine):
        root.mkdir()
    _write_container_layer(base, {"exec_user": "base"})
    _write_container_layer(knowledge, {"exec_user": "knowledge"})
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "qualified-fixture-repository", "execution_machine": "fixture-worker",
            "installation_cell": "fixture-cell", "binding": "bound",
            "base_root": str(base), "knowledge_root": str(knowledge),
        },
    }
    environment = dict(os.environ)
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    environment.pop("AGENT_CONTAINERS_CONFIG", None)
    environment["AGENT_CONTAINERS_HOME"] = str(machine)
    environment["AGENT_CONFIGURATION_CONTEXT"] = json.dumps(report)
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         "from agent_containers.config import load_config; print(load_config(strict=True).exec_user)"],
        cwd=base, env=environment, capture_output=True, text=True, timeout=20,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "knowledge"
    assert list(machine.iterdir()) == []


def test_default_acp_command_is_the_trusted_allow_all_experimental_pair():
    # A literal regression assertion: every other test here derives its
    # expectation from the SAME imported DEFAULT_ACP_COMMAND constant, so
    # they'd still pass even if that constant silently reverted to the old
    # --allow-all-tools (or dropped --experimental, which SDK extension
    # loading depends on entirely). Pin the exact string so a regression in
    # the constant itself is actually caught.
    assert DEFAULT_ACP_COMMAND == "copilot --acp --stdio --allow-all --experimental"


def test_defaults():
    c = ContainersConfig()
    assert c.exec_user == "vscode"
    assert c.workspace_folder == "/workspace"
    assert c.forward_gh_token is True
    assert any(p == "vsc-" for p in c.image_prefixes)


def test_effective_acp_command_default_prefixes_cd():
    c = ContainersConfig()
    cmd = c.effective_acp_command()
    assert cmd == f"cd /workspace && {DEFAULT_ACP_COMMAND}"


def test_effective_acp_command_explicit_override_wins():
    c = ContainersConfig()
    assert c.effective_acp_command(acp_command="custom") == "custom"


def test_effective_acp_command_custom_workspace():
    c = ContainersConfig()
    cmd = c.effective_acp_command(workspace_folder="/work/x")
    assert cmd == f"cd /work/x && {DEFAULT_ACP_COMMAND}"


def test_restricted_profile_defaults_fail_closed():
    c = ContainersConfig()
    fleet = FleetConfig(security_profile="restricted")
    assert fleet.restricted
    assert fleet.effective_network() == "none"
    assert fleet.effective_memory() == "4g"
    assert fleet.effective_cpus() == 2.0
    assert fleet.effective_pids_limit() == 256
    assert c.credentials_for(fleet) == (False, False)
    with pytest.raises(RuntimeError, match="explicit per-fleet 'acp_command'"):
        c.acp_command_for(fleet)


def test_restricted_profile_explicit_command_and_credentials_stay_isolated():
    c = ContainersConfig()
    fleet = FleetConfig(
        security_profile="restricted",
        acp_command="minimal-agent --stdio",
        forward_gh_token=True,
        relay_enabled=True,
    )
    assert c.acp_command_for(fleet) == "minimal-agent --stdio"
    assert c.credentials_for(fleet) == (False, False)


def test_trusted_profile_preserves_global_defaults():
    c = ContainersConfig()
    fleet = FleetConfig()
    assert not fleet.restricted
    assert fleet.effective_network() is None
    assert c.credentials_for(fleet) == (True, True)
    assert c.acp_command_for(fleet) == (
        f"cd /workspace && {DEFAULT_ACP_COMMAND}"
    )


def test_copilot_cli_account_for_defaults_to_none():
    c = ContainersConfig()
    assert c.copilot_cli_account is None
    assert c.copilot_cli_account_for(FleetConfig()) is None
    assert c.copilot_cli_account_for(None) is None


def test_copilot_cli_account_for_global_default_applies_to_every_trusted_fleet():
    c = ContainersConfig()
    c.copilot_cli_account = "inference-user"
    assert c.copilot_cli_account_for(FleetConfig()) == "inference-user"
    assert c.copilot_cli_account_for(None) == "inference-user"


def test_copilot_cli_account_for_per_fleet_override_wins():
    c = ContainersConfig()
    c.copilot_cli_account = "inference-user"
    fleet = FleetConfig(copilot_cli_account="some-other-account")
    assert c.copilot_cli_account_for(fleet) == "some-other-account"


def test_copilot_cli_account_for_restricted_fleet_always_none():
    c = ContainersConfig()
    c.copilot_cli_account = "inference-user"
    fleet = FleetConfig(
        security_profile="restricted",
        copilot_cli_account="inference-user",
    )
    assert c.copilot_cli_account_for(fleet) is None


def test_load_config_from_file(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            exec_user: dev
            workspace_folder: /workspaces/foo
            forward_gh_token: false
            copilot_cli_account: inference-user
            image_prefixes:
              - vsc-foo-
            fleets:
              myrepo:
                repo: your-org/your-repo
                devcontainer_path: /src/myrepo-devcontainer
                size: 3
                code_model: clone
              otherrepo:
                repo: your-org/other-repo
                devcontainer_path: /src/other-devcontainer
                copilot_cli_account: some-other-account
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    c = load_config()
    assert c.exec_user == "dev"
    assert c.workspace_folder == "/workspaces/foo"
    assert c.forward_gh_token is False
    assert c.copilot_cli_account == "inference-user"
    assert c.image_prefixes == ["vsc-foo-"]
    assert "myrepo" in c.fleets
    fleet = c.fleets["myrepo"]
    assert fleet.size == 3
    assert fleet.prefix("myrepo") == "myrepo"
    assert fleet.devcontainer_path == "/src/myrepo-devcontainer"
    assert fleet.copilot_cli_account is None
    assert c.copilot_cli_account_for(fleet) == "inference-user"
    other_fleet = c.fleets["otherrepo"]
    assert other_fleet.copilot_cli_account == "some-other-account"
    assert c.copilot_cli_account_for(other_fleet) == "some-other-account"


def test_load_restricted_fleet_config(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            fleets:
              sandbox:
                image: example/agent:latest
                security_profile: restricted
                network: model-only
                memory: 6g
                cpus: 3
                pids_limit: 128
                workspace_size: 3g
                home_size: 256m
                environment:
                  MODEL_BASE_URL: http://model-proxy:8080/v1
                  MODEL_NAME: local-model
                acp_command: minimal-agent --stdio
                forward_gh_token: true
                relay_enabled: true
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    c = load_config()
    fleet = c.fleets["sandbox"]
    assert fleet.restricted
    assert fleet.effective_network() == "model-only"
    assert fleet.effective_memory() == "6g"
    assert fleet.effective_cpus() == 3.0
    assert fleet.effective_pids_limit() == 128
    assert fleet.effective_workspace_size() == "3g"
    assert fleet.effective_home_size() == "256m"
    assert fleet.environment == {
        "MODEL_BASE_URL": "http://model-proxy:8080/v1",
        "MODEL_NAME": "local-model",
    }
    assert c.credentials_for(fleet) == (False, False)


def test_invalid_security_profile_fails_loud(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        "fleets:\n  sandbox:\n    image: example/agent\n"
        "    security_profile: maybe\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    with pytest.raises(RuntimeError, match="invalid security_profile"):
        load_config()


def test_restricted_resource_limits_must_be_positive(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        "fleets:\n  sandbox:\n    image: example/agent\n"
        "    security_profile: restricted\n"
        "    acp_command: minimal-agent --stdio\n"
        "    pids_limit: -1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    with pytest.raises(RuntimeError, match="'pids_limit' must be positive"):
        load_config()


def test_restricted_environment_rejects_credentials(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        "fleets:\n  sandbox:\n    image: example/agent\n"
        "    security_profile: restricted\n"
        "    acp_command: minimal-agent --stdio\n"
        "    environment:\n      MODEL_API_KEY: not-allowed\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    with pytest.raises(RuntimeError, match="looks credential-bearing"):
        load_config()


def test_devcontainer_config_resolved_relative_to_path():
    from agent_containers.config import FleetConfig

    fleet = FleetConfig(
        devcontainer_path="/src/myrepo-devcontainer",
        devcontainer_config=".devcontainer/docker/devcontainer.json",
    )
    resolved = fleet.resolved_config()
    assert resolved is not None
    assert resolved.replace("\\", "/") == (
        "/src/myrepo-devcontainer/.devcontainer/docker/devcontainer.json"
    )


def test_devcontainer_config_absolute_kept():
    from agent_containers.config import FleetConfig

    fleet = FleetConfig(
        devcontainer_path="/src/x",
        devcontainer_config="/abs/devcontainer.json",
    )
    assert fleet.resolved_config().replace("\\", "/") == "/abs/devcontainer.json"


def test_devcontainer_config_none_when_unset():
    from agent_containers.config import FleetConfig

    assert FleetConfig(devcontainer_path="/src/x").resolved_config() is None


def test_load_config_dotfiles(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            dotfiles:
              repo: /home/me/dotfiles
              install_command: bash install.sh
            fleets:
              myrepo:
                devcontainer_path: /src/myrepo-devcontainer
                devcontainer_config: .devcontainer/docker/devcontainer.json
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    c = load_config()
    assert c.dotfiles is not None
    assert c.dotfiles.repo == "/home/me/dotfiles"
    assert c.dotfiles.target == "/workspaces/.codespaces/.persistedshare/dotfiles"
    assert c.dotfiles.install_command == "bash install.sh"
    fleet = c.fleets["myrepo"]
    assert fleet.devcontainer_config == ".devcontainer/docker/devcontainer.json"


def test_load_config_dotfiles_install_disabled(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            dotfiles:
              repo: /home/me/dotfiles
              target: /custom/dotfiles
              install_command: ""
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    c = load_config()
    assert c.dotfiles is not None
    assert c.dotfiles.target == "/custom/dotfiles"
    assert c.dotfiles.install_command is None


def test_load_config_no_dotfiles_when_repo_missing(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text("dotfiles:\n  target: /x\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    assert load_config().dotfiles is None


def test_harness_defaults_off():
    # harness is opt-in and decoupled from dotfiles: None unless configured.
    assert ContainersConfig().harness is None


def test_load_config_harness(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            harness:
              repo: /host/harness
            fleets:
              myrepo:
                devcontainer_path: /src/myrepo-devcontainer
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    c = load_config()
    assert c.harness is not None
    assert c.harness.repo == "/host/harness"
    # target derived from the repo basename by the standard convention, no install
    assert c.harness.target == "/workspaces/harness"
    assert c.harness.install_command is None
    # dotfiles and harness are independent
    assert c.dotfiles is None


def test_harness_target_derives_from_repo_basename():
    from agent_containers.config import HarnessConfig

    assert HarnessConfig(repo="/host/control-plane").target == "/workspaces/control-plane"
    assert HarnessConfig(repo="D:/Src/myharness").target == "/workspaces/myharness"


def test_load_config_no_harness_when_repo_missing(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text("harness:\n  install_command: bash x\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    assert load_config().harness is None


class TestCanonicalInRepoConfigPath:
    """The canonical in-repo location (matching every other plugin's own
    ``.copilot-extensions/<plugin-id>/...`` convention) is resolved before
    the legacy bare ``<repo>/containers.yaml``."""

    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "agent_containers.config.RUNTIME_DIR", tmp_path / "empty-runtime"
        )

    def test_canonical_path_is_used_when_present(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        canonical_dir = tmp_path / ".copilot-extensions" / "agent-containers"
        canonical_dir.mkdir(parents=True)
        (canonical_dir / "containers.yaml").write_text(
            "exec_user: canonicaluser\n", encoding="utf-8"
        )
        assert load_config().exec_user == "canonicaluser"

    def test_legacy_bare_path_still_works_when_canonical_absent(
        self, tmp_path, monkeypatch
    ):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / "containers.yaml").write_text(
            "exec_user: legacyuser\n", encoding="utf-8"
        )
        assert load_config().exec_user == "legacyuser"

    def test_canonical_path_wins_when_both_present(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / "containers.yaml").write_text(
            "exec_user: legacyuser\n", encoding="utf-8"
        )
        canonical_dir = tmp_path / ".copilot-extensions" / "agent-containers"
        canonical_dir.mkdir(parents=True)
        (canonical_dir / "containers.yaml").write_text(
            "exec_user: canonicaluser\n", encoding="utf-8"
        )
        assert load_config().exec_user == "canonicaluser"

    def test_explicit_env_override_wins_over_both(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        (tmp_path / "containers.yaml").write_text(
            "exec_user: legacyuser\n", encoding="utf-8"
        )
        canonical_dir = tmp_path / ".copilot-extensions" / "agent-containers"
        canonical_dir.mkdir(parents=True)
        (canonical_dir / "containers.yaml").write_text(
            "exec_user: canonicaluser\n", encoding="utf-8"
        )
        explicit = tmp_path / "explicit-containers.yaml"
        explicit.write_text("exec_user: explicituser\n", encoding="utf-8")
        monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(explicit))
        assert load_config().exec_user == "explicituser"


# --- E1e knowledge overlay (config-graft, #947) -------------------------------

class TestKnowledgeOverlay:
    """containers.yaml resolves from the bound knowledge repo for a stateless harness."""

    def _isolate(self, tmp_path, monkeypatch):
        # No env, cwd, or machine-local containers.yaml -> only the overlay remains.
        monkeypatch.delenv("AGENT_CONTAINERS_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("agent_containers.config.RUNTIME_DIR",
                            tmp_path / "empty-runtime")

    def test_overlay_fallback_used_when_nothing_local(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir()
        (knowledge / "containers.yaml").write_text("exec_user: knuser\n", encoding="utf-8")
        monkeypatch.setattr(
            "agent_containers.config._knowledge_overlay_config",
            lambda: knowledge / "containers.yaml")
        assert load_config().exec_user == "knuser"

    def test_machine_local_wins_over_overlay(self, tmp_path, monkeypatch):
        # A deliberate machine-local containers.yaml still takes precedence.
        self._isolate(tmp_path, monkeypatch)
        runtime = tmp_path / "rt"
        runtime.mkdir()
        (runtime / "containers.yaml").write_text("exec_user: localuser\n", encoding="utf-8")
        monkeypatch.setattr("agent_containers.config.RUNTIME_DIR", runtime)
        called = {"n": 0}
        monkeypatch.setattr(
            "agent_containers.config._knowledge_overlay_config",
            lambda: called.__setitem__("n", called["n"] + 1) or None)
        assert load_config().exec_user == "localuser"
        assert called["n"] == 0  # overlay never consulted

    def test_no_overlay_falls_back_to_defaults(self, tmp_path, monkeypatch):
        self._isolate(tmp_path, monkeypatch)
        monkeypatch.setattr(
            "agent_containers.config._knowledge_overlay_config", lambda: None)
        assert load_config().exec_user == "vscode"  # built-in default


class TestKnowledgeOverlayResolver:
    """_knowledge_overlay_config consumes the qualified owner-data seam."""

    def _mock(self, monkeypatch, payload, *, rc=0):
        bound = payload.get("requires_external") and payload.get("bound")
        context = {
            "repository_id": "qualified-test-repository", "execution_machine": "test-worker",
            "installation_cell": "test-cell", "binding": "bound" if bound else "unbound",
        }
        if bound:
            context["knowledge_root"] = payload["state_root"]
        monkeypatch.setattr(
            "agent_containers.config._configuration_report",
            lambda: {"schema": "agent-worktrees.configuration", "version": 1, "context": context},
        )

    def test_resolves_when_knowledge_has_containers_yaml(self, tmp_path, monkeypatch):
        from agent_containers.config import _knowledge_overlay_config
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir()
        (knowledge / "containers.yaml").write_text("exec_user: x\n", encoding="utf-8")
        self._mock(monkeypatch, {
            "state_root": str(knowledge), "requires_external": True, "bound": True})
        assert _knowledge_overlay_config() == knowledge / "containers.yaml"

    def test_none_when_self_hosted(self, tmp_path, monkeypatch):
        from agent_containers.config import _knowledge_overlay_config
        self._mock(monkeypatch, {
            "state_root": str(tmp_path), "requires_external": False, "bound": True})
        assert _knowledge_overlay_config() is None

    def test_none_when_knowledge_lacks_file(self, tmp_path, monkeypatch):
        from agent_containers.config import _knowledge_overlay_config
        knowledge = tmp_path / "knowledge"
        knowledge.mkdir()
        self._mock(monkeypatch, {
            "state_root": str(knowledge), "requires_external": True, "bound": True})
        assert _knowledge_overlay_config() is None

    def test_none_when_no_binstub(self, tmp_path, monkeypatch):
        from agent_containers.config import _knowledge_overlay_config
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert _knowledge_overlay_config() is None

    def test_legacy_optional_owner_does_not_spawn(self, monkeypatch):
        from agent_containers import config

        monkeypatch.setattr(config, "_installation_owner", lambda: None)
        monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)

        def run(argv, **kwargs):
            raise AssertionError("optional owner lookup must not bootstrap an ambient command")

        monkeypatch.setattr("subprocess.run", run)
        assert config._knowledge_overlay_config() is None


def test_rescue_limits_load_with_bounded_defaults(tmp_path, monkeypatch):
    config_file = tmp_path / "containers.yaml"
    config_file.write_text(
        """
rescue:
  max_member_bytes: 1024
  max_capture_bytes: 4096
  max_total_bytes: 8192
  retain_per_container: 2
  operation_timeout_seconds: 30
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(config_file))

    config = load_config()

    assert config.rescue.max_member_bytes == 1024
    assert config.rescue.max_capture_bytes == 4096
    assert config.rescue.max_total_bytes == 8192
    assert config.rescue.retain_per_container == 2
    assert config.rescue.operation_timeout_seconds == 30


def test_coordination_state_dir_can_be_shared_without_moving_runtime(tmp_path):
    env = os.environ.copy()
    env["AGENT_CONTAINERS_STATE_DIR"] = str(tmp_path / "shared-state")
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from agent_containers.config import RUNTIME_DIR, STATE_DIR; "
                "print(RUNTIME_DIR); print(STATE_DIR)"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    runtime, state = proc.stdout.splitlines()
    assert state == str(tmp_path / "shared-state")
    assert runtime != state


def test_ensure_state_dir_enforces_owner_only_mode(monkeypatch, tmp_path):
    from agent_containers import config as config_mod

    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o777)
    state_dir.chmod(0o777)
    monkeypatch.setattr(config_mod, "STATE_DIR", state_dir)
    previous = os.umask(0)
    try:
        config_mod.ensure_state_dir()
    finally:
        os.umask(previous)

    assert state_dir.is_dir()
    if os.name != "nt":
        assert stat.S_IMODE(state_dir.stat().st_mode) == 0o700


def test_trusted_fleet_accepts_host_paths_and_systemd_capable():
    fleet = FleetConfig(
        security_profile="trusted",
        host_workspace_path="/mnt/data/workspaces/example-1",
        host_home_path="/mnt/data/home/example-1",
        home_folder="/home/node",
        systemd_capable=True,
    )
    fleet.validate_restricted()  # no-op for trusted; must not raise
    assert fleet.host_workspace_path == "/mnt/data/workspaces/example-1"
    assert fleet.host_home_path == "/mnt/data/home/example-1"
    assert fleet.home_folder == "/home/node"
    assert fleet.systemd_capable is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"host_workspace_path": "/mnt/data/workspaces/example-1"},
        {"host_home_path": "/mnt/data/home/example-1", "home_folder": "/home/node"},
        {"systemd_capable": True},
    ],
)
def test_restricted_fleet_rejects_trusted_only_capabilities(kwargs):
    fleet = FleetConfig(security_profile="restricted", **kwargs)
    with pytest.raises(RuntimeError, match="trusted-only capabilities"):
        fleet.validate_restricted()


def test_load_config_parses_trusted_only_fleet_fields(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            fleets:
              myrepo:
                repo: your-org/your-repo
                image: your-org/your-image:latest
                security_profile: trusted
                host_workspace_path: /mnt/data/workspaces/myrepo-1
                host_home_path: /mnt/data/home/myrepo-1
                home_folder: /home/node
                systemd_capable: true
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    c = load_config()
    fleet = c.fleets["myrepo"]
    assert fleet.host_workspace_path == "/mnt/data/workspaces/myrepo-1"
    assert fleet.host_home_path == "/mnt/data/home/myrepo-1"
    assert fleet.home_folder == "/home/node"
    assert fleet.systemd_capable is True


def test_load_config_rejects_non_boolean_systemd_capable(tmp_path, monkeypatch):
    cfg = tmp_path / "containers.yaml"
    cfg.write_text(
        textwrap.dedent(
            """
            fleets:
              myrepo:
                image: your-org/your-image:latest
                security_profile: trusted
                systemd_capable: "false"
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(cfg))
    with pytest.raises(RuntimeError, match="must be a boolean"):
        load_config()


def test_devcontainer_fleet_rejects_image_only_options():
    fleet = FleetConfig(
        devcontainer_path="/src/myrepo",
        security_profile="trusted",
        systemd_capable=True,
    )
    with pytest.raises(RuntimeError, match="apply only to image:-backed fleets"):
        fleet.validate_restricted()


def test_incomplete_home_pair_is_rejected():
    only_path = FleetConfig(security_profile="trusted", host_home_path="/mnt/data/home")
    with pytest.raises(RuntimeError, match="must be set together"):
        only_path.validate_restricted()

    only_folder = FleetConfig(security_profile="trusted", home_folder="/home/node")
    with pytest.raises(RuntimeError, match="must be set together"):
        only_folder.validate_restricted()
