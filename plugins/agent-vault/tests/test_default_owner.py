"""Native machine defaults do not require a repository-aware peer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from configuration_layers import ConfigurationError

from agent_vault import config, configuration, default_owner
from agent_vault.invocation import use_configuration_root

Owners = tuple[Path, Path, Path, Path]


@pytest.fixture
def owners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Owners:
    home, base, project = (tmp_path / name for name in ("neutral", "default", "project"))
    for root in (home, base, project):
        root.mkdir()
    native = tmp_path / "native.json"
    native.write_text(
        json.dumps(
            {
                "enabled": True,
                "default_configuration_root": str(base),
                "vaults": {
                    "first": {"kpdb": str(tmp_path / "first.kdbx")},
                    "second": {"kpdb": str(tmp_path / "second.kdbx")},
                },
            }
        )
    )
    (base / ".agent-vault.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "vault": "first",
                "group": "Example",
            }
        )
    )
    (project / ".agent-vault.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "vault": "second",
            }
        )
    )
    monkeypatch.chdir(home)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(native))
    for key in (
        "AGENT_VAULT",
        "KPDB",
        "VAULT_GROUP",
        "AGENT_VAULT_PORT",
        "AGENT_VAULT_PINNED_CONTEXT",
        "AGENT_CONFIGURATION_CONTEXT",
        "COPILOT_EXTENSIONS_CONTEXT",
        "AGENT_RT_ROOT",
        "_AGENT_VAULT_FIRST_USE_ROOT",
        "COPILOT_PLUGIN_INSTALL_STAGED",
        "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "_ext_config", lambda _cwd: {})
    from agent_vault import configuration_scope

    monkeypatch.setattr(configuration_scope, "_SERVICE_CONFIGURATION_ROOT", None)
    monkeypatch.setattr(configuration_scope, "_SERVICE_NATIVE_DEFAULT", False)
    return home, base, project, native


def test_neutral_directory_uses_default_without_a_peer_or_git(
    owners: Owners, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, base, _, _ = owners
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(
        configuration,
        "_legacy_report",
        lambda *_args: pytest.fail("native default attempted a peer subprocess"),
    )
    assert configuration.runtime_enabled() is True
    assert configuration.runtime_enabled(str(home)) is True
    assert configuration.service_configuration_root(str(home)) == str(base)
    result = config.resolve_context(strict=True)
    assert result.vault_name == "first"
    assert result.group == "Example"
    assert config.configuration_root() == str(base)
    assert Path.cwd() == home
    assert result.kpdb is not None
    assert not Path(result.kpdb).exists()


def test_real_cli_uses_native_default_with_no_git_or_worktrees_path(owners: Owners) -> None:
    home, base, _, native = owners
    environment = {
        key: os.environ[key]
        for key in ("SystemRoot", "WINDIR", "COMSPEC", "OS", "PATHEXT")
        if key in os.environ
    }
    environment.update(
        {
            "PATH": "",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(home),
            "LOCALAPPDATA": str(home),
            "XDG_CONFIG_HOME": str(home),
            "XDG_CACHE_HOME": str(home),
            "AGENT_VAULT_CONFIG": str(native),
            "AGENT_VAULT_HOME": str(home / "unused-runtime"),
            "AGENT_VAULT_NO_SELFPROVISION": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-m", "agent_vault", "which", "--json"],
        cwd=home,
        env=environment,
        capture_output=True,
        check=False,
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    selected = json.loads(result.stdout)
    assert selected["vault_name"] == "first"
    assert selected["group"] == "Example"
    assert not (home / "unused-runtime").exists()
    assert not (base / ".agent-worktrees").exists()


def test_cold_activation_reader_uses_default_without_construction(owners: Owners) -> None:
    home, _, _, native = owners
    script = Path(__file__).parents[1] / "scripts" / "runtime-activation.py"
    environment = {
        key: os.environ[key]
        for key in ("SystemRoot", "WINDIR", "COMSPEC", "OS", "PATHEXT")
        if key in os.environ
    }
    environment.update(
        {
            "PATH": "",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "AGENT_VAULT_CONFIG": str(native),
            "AGENT_VAULT_HOME": str(home / "unused-runtime"),
        }
    )
    result = subprocess.run(  # noqa: S603 -- current interpreter and source-owned reader only.
        [sys.executable, "-I", "-B", str(script), "--cwd", str(home)],
        cwd=home,
        env=environment,
        capture_output=True,
        check=False,
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert json.loads(result.stdout)["state"] == "configured"
    assert not (home / "unused-runtime").exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires native FIFOs")
@pytest.mark.parametrize("entrypoint", ["cli", "activation"])
def test_native_default_fifo_refuses_without_reading(
    owners: Owners, entrypoint: str,
) -> None:
    home, base, _, native = owners
    source = base / ".agent-vault.json"
    source.unlink()
    getattr(os, "mkfifo")(source)
    environment = {
        key: os.environ[key]
        for key in ("SystemRoot", "WINDIR", "COMSPEC", "OS", "PATHEXT")
        if key in os.environ
    }
    environment.update({
        "PATH": "", "HOME": str(home), "USERPROFILE": str(home),
        "APPDATA": str(home), "LOCALAPPDATA": str(home),
        "XDG_CONFIG_HOME": str(home), "XDG_CACHE_HOME": str(home),
        "AGENT_VAULT_CONFIG": str(native),
        "AGENT_VAULT_HOME": str(home / "unused-runtime"),
        "AGENT_VAULT_NO_SELFPROVISION": "1",
    })
    arguments = ["-m", "agent_vault", "which", "--json"]
    if entrypoint == "activation":
        arguments = [
            str(Path(__file__).parents[1] / "scripts" / "runtime-activation.py"),
            "--cwd", str(home),
        ]
    result = subprocess.run(
        [sys.executable, "-I", "-B", *arguments],
        cwd=home, env=environment, capture_output=True, check=False, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode != 0
    if entrypoint == "activation":
        assert json.loads(result.stdout)["state"] == "failed"
    else:
        assert b"ordinary file" in result.stderr
    assert not (home / "unused-runtime").exists()


def test_explicit_and_repository_sources_precede_default(
    owners: Owners, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, base, project, _ = owners
    monkeypatch.setattr(configuration, "_legacy_report", lambda *_args: None)
    with use_configuration_root(str(project)):
        assert config.configuration_root() == str(project)
        assert config.resolve_context(strict=True).vault_name == "second"
        assert Path.cwd() == home
    monkeypatch.chdir(project)
    assert config.configuration_root() == str(project)
    assert config.resolve_context(strict=True).vault_name == "second"
    monkeypatch.chdir(home)
    assert config.configuration_root() == str(base)


def test_retained_resident_source_is_not_replaced_by_default(
    owners: Owners, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_vault import configuration_scope

    home, _, project, _ = owners
    monkeypatch.setattr(configuration_scope, "_SERVICE_CONFIGURATION_ROOT", str(project))
    assert config.configuration_root() == str(project)
    assert config.configuration_root(str(home)) == str(home)


@pytest.mark.parametrize("selection", ["explicit", "caller", "resident"])
def test_same_root_selection_keeps_qualified_layers(
    owners: Owners,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    home, base, knowledge, _ = owners
    (base / ".agent-vault.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "vault": "first",
                "kpdb": "native.kdbx",
            }
        )
    )
    local = home / "local"
    local.mkdir()
    (local / ".agent-vault.json").write_text('{"group":"machine-group"}')
    report = {
        "schema": "agent-worktrees.configuration",
        "version": 1,
        "context": {
            "repository_id": "qualified",
            "execution_machine": "fixture",
            "installation_cell": "legacy",
            "binding": "bound",
            "base_root": str(base),
            "knowledge_root": str(knowledge),
        },
        "repository_machine_root": str(local),
    }
    monkeypatch.setattr(configuration, "_legacy_report", lambda root: report)
    if selection == "caller":
        monkeypatch.chdir(base)
    elif selection == "resident":
        config.bind_service_configuration(str(base))
    with use_configuration_root(str(base) if selection == "explicit" else None):
        result = config.resolve_context(strict=True)
    assert result.vault_name == "second"
    assert result.group == "machine-group"
    assert result.kpdb == str(base / "native.kdbx")


def test_native_default_provenance_survives_resident_capture(
    owners: Owners,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault import configuration_scope
    from agent_vault.service_arguments import parse_service_arguments

    home, base, _, _ = owners
    monkeypatch.setattr(configuration_scope, "_SERVICE_CONFIGURATION_ROOT", None)
    monkeypatch.setattr(
        configuration,
        "_legacy_report",
        lambda root: pytest.fail("fallback queried a peer"),
    )
    snapshot = configuration.service_configuration_snapshot()
    assert snapshot["root"] == str(base)
    assert snapshot["native_default_owner"] == "1"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "service",
            "--foreground",
            "--configuration-root",
            snapshot["root"],
            "--native-default-owner",
        ],
    )
    parse_service_arguments()
    monkeypatch.chdir(home)
    assert config.resolve_context(strict=True).vault_name == "first"
    assert configuration.runtime_enabled() is True
    with pytest.raises(ValueError, match="differs"):
        config.bind_service_configuration(str(home), native_default=True)


def test_native_default_launch_retains_selection_provenance(
    owners: Owners,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_vault import launch

    home, base, _, _ = owners
    monkeypatch.setenv("AGENT_VAULT_HOME", str(home / "runtime"))
    monkeypatch.setattr(
        configuration,
        "_legacy_report",
        lambda root: pytest.fail("fallback queried a peer"),
    )
    arguments: list[str] = []
    monkeypatch.setattr(launch, "spawn_daemon", lambda argv, **kwargs: arguments.extend(argv))
    launch.spawn_service(["--foreground"])
    assert arguments[-3:] == ["--configuration-root", str(base), "--native-default-owner"]


@pytest.mark.parametrize("damage", ["missing", "kpdb", "port"])
def test_bound_default_is_revalidated_before_each_read(
    owners: Owners,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
) -> None:
    home, base, _, _ = owners
    (base.parent / ".agent-vault.json").write_text('{"vault":"second"}')
    config.bind_service_configuration(str(base), native_default=True)
    source = base / ".agent-vault.json"
    if damage == "missing":
        source.rename(base / "retained-config.json")
    else:
        source.write_text(json.dumps({"enabled": True, damage: "foreign"}))
    monkeypatch.chdir(home)
    with pytest.raises(ConfigurationError, match=r"requires|native custody"):
        config.resolve_context(strict=True)
    with pytest.raises(ConfigurationError, match=r"requires|native custody"):
        configuration.runtime_enabled()


def test_malformed_native_json_keeps_tolerant_context_resolution(
    owners: Owners,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, _, native = owners
    native.write_text("{")
    monkeypatch.setattr(configuration, "_legacy_report", lambda root: None)
    assert config.resolve_context(strict=False).vault_name is None
    with pytest.raises(RuntimeError, match="invalid configuration"):
        config.resolve_context(strict=True)


@pytest.mark.parametrize("staged", [False, True])
def test_first_use_metadata_requires_staging_and_keeps_explicit_selection(
    owners: Owners,
    monkeypatch: pytest.MonkeyPatch,
    staged: bool,
) -> None:
    _, base, project, _ = owners
    monkeypatch.setattr(configuration, "_legacy_report", lambda root: None)
    monkeypatch.setenv("_AGENT_VAULT_FIRST_USE_ROOT", str(project))
    monkeypatch.setenv("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT", str(project))
    if staged:
        monkeypatch.setenv("COPILOT_PLUGIN_INSTALL_STAGED", "1")
    assert config.configuration_root() == str(project if staged else base)
    assert config.resolve_context(strict=True).vault_name == ("second" if staged else "first")


def test_invalid_staged_selection_refuses_but_explicit_cli_still_wins(
    owners: Owners,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, _, project, _ = owners
    monkeypatch.setattr(configuration, "_legacy_report", lambda root: None)
    monkeypatch.setenv("_AGENT_VAULT_FIRST_USE_ROOT", str(project))
    monkeypatch.setenv("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT", str(home))
    monkeypatch.setenv("COPILOT_PLUGIN_INSTALL_STAGED", "1")
    with pytest.raises(ValueError, match="staged project"):
        config.resolve_context(strict=True)
    with use_configuration_root(str(project)):
        assert config.resolve_context(strict=True).vault_name == "second"


@pytest.mark.parametrize("selection", ["explicit", "caller", "resident"])
@pytest.mark.parametrize("damage", ["custody", "missing", "relative", "nonstring", "nul"])
def test_unrelated_invalid_default_does_not_veto_selected_owner(
    owners: Owners, monkeypatch: pytest.MonkeyPatch, selection: str, damage: str
) -> None:
    from agent_vault import configuration_scope

    _, base, project, native = owners
    if damage == "custody":
        (base / ".agent-vault.json").write_text('{"vaults":{}}')
    elif damage == "missing":
        (base / ".agent-vault.json").rename(base / "retained-config.json")
    else:
        document = json.loads(native.read_text())
        document["default_configuration_root"] = {
            "relative": ".",
            "nonstring": False,
            "nul": str(base / "invalid\0owner"),
        }[damage]
        native.write_text(json.dumps(document))
    monkeypatch.setattr(configuration, "_legacy_report", lambda *_args: None)
    if selection == "explicit":
        with use_configuration_root(str(project)):
            assert config.resolve_context(strict=True).vault_name == "second"
    elif selection == "caller":
        monkeypatch.chdir(project)
        assert config.resolve_context(strict=True).vault_name == "second"
    else:
        monkeypatch.setattr(configuration_scope, "_SERVICE_CONFIGURATION_ROOT", str(project))
        assert config.resolve_context(strict=True).vault_name == "second"


@pytest.mark.parametrize("value", [None, False, "", ".", "/missing-default-owner"])
def test_invalid_default_refuses_without_trying_a_peer(
    owners: Owners, monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    _, _, _, native = owners
    document = json.loads(native.read_text())
    document["default_configuration_root"] = value
    native.write_text(json.dumps(document))
    monkeypatch.setattr(
        configuration,
        "_legacy_report",
        lambda *_args: pytest.fail("invalid default attempted another owner"),
    )
    with pytest.raises(ConfigurationError, match="default configuration"):
        config.resolve_context(strict=True)


def test_default_source_cannot_replace_registry_or_cache_custody(owners: Owners) -> None:
    _, base, _, _ = owners
    (base / ".agent-vault.json").write_text(
        json.dumps(
            {
                "enabled": True,
                "vaults": {"untrusted": {"kpdb": "other.kdbx"}},
            }
        )
    )
    with pytest.raises(ConfigurationError, match="native custody"):
        config.resolve_context(strict=True)


def test_missing_default_source_is_not_silently_ignored(owners: Owners) -> None:
    _, base, _, _ = owners
    (base / ".agent-vault.json").rename(base / "retained-config.json")
    with pytest.raises(ConfigurationError, match=r"requires \.agent-vault\.json"):
        config.resolve_context(strict=True)


def test_absent_default_preserves_existing_optional_owner_behavior(
    owners: Owners, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _, _, native = owners
    document = json.loads(native.read_text())
    del document["default_configuration_root"]
    native.write_text(json.dumps(document))
    observed: list[Path] = []
    monkeypatch.setattr(configuration, "_legacy_report", lambda root: observed.append(root))
    assert default_owner.fallback_root() is None
    assert configuration.owner_report(home) is None
    assert observed == [home]


def test_invalid_delivered_context_does_not_fall_back(
    owners: Owners, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", "{}")
    with pytest.raises(ConfigurationError):
        config.resolve_context(strict=True)
