"""Tests for ``machine_identity.is_local_machine`` -- canonicalized
same-machine check (never a bare string comparison against config.machine)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import machine_identity


@pytest.fixture
def mixed_registry_config(tmp_path: Path, monkeypatch) -> cfg.Config:
    (tmp_path / "machines.yaml").write_text(
        "machines:\n"
        "  example-host:\n"
        "    hostname: EXAMPLE-HOST\n"
        "    ssh:\n"
        "      ready: true\n"
        "      environments:\n"
        "        - {name: windows, alias: example-native}\n"
        "  example-host-wsl:\n"
        "    execution_platform: wsl\n"
        "    physical_host: example-host\n"
        "    ssh:\n"
        "      ready: true\n"
        "      environments:\n"
        "        - {name: wsl, alias: example-guest}\n"
        "  peer-space:\n"
        "    execution_platform: linux\n"
        "    physical_host: peer-host\n"
        "    ssh:\n"
        "      ready: true\n"
        "      environments:\n"
        "        - {name: linux, alias: peer-ssh}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "EXAMPLE-HOST")
    entries = cfg.load_machines_yaml(tmp_path)
    assert not entries["example-host"].execution_platform
    assert entries["peer-space"].execution_platform == "linux"
    return cfg.Config(
        srcroot=str(tmp_path), machine="example-host", platform="windows",
        repo_name="proj", repos={"proj": cfg.RepoConfig(
            anchor=str(tmp_path), worktree_root=str(tmp_path / "wt"),
        )},
    )


@pytest.mark.parametrize(("name", "local"), [
    ("EXAMPLE-HOST", True), ("peer-space", False), ("example-host-wsl", False),
])
def test_mixed_registry_locality_does_not_grant_mutation_authority(
    mixed_registry_config, name, local,
):
    from agent_worktrees import execution_spaces

    config = mixed_registry_config
    assert machine_identity.is_local_machine(name, config) is local
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="owned mutation requires"):
        execution_spaces.require_current_execution_space(config)


def test_mixed_registry_locality_retains_guest_context(mixed_registry_config, monkeypatch):
    config = replace(mixed_registry_config, machine="example-host-wsl", platform="wsl")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    assert machine_identity.is_local_machine("example-host-wsl", config)
    assert not machine_identity.is_local_machine("example-host", config)
    assert not machine_identity.is_local_machine("peer-space", config)


def test_mixed_registry_remote_candidates_keep_guest_but_exclude_native(
    mixed_registry_config, monkeypatch,
):
    from agent_worktrees import resolve_machine_cli

    monkeypatch.setattr(resolve_machine_cli, "_in_ssh_session", lambda: False)
    candidates = resolve_machine_cli._load_remote_machines(mixed_registry_config)
    assert {
        entry.key: [environment.name for environment in environments]
        for entry, environments in candidates
    } == {"example-host-wsl": ["wsl"], "peer-space": ["linux"]}


def test_remote_legacy_projection_does_not_suppress_local_wsl_qualification() -> None:
    from machine_transport import normalize_machine_entries

    local = cfg.MachineEntry(key="example-host", display_name="Local")
    remote = cfg.MachineEntry(
        key="remote-host", display_name="Remote",
        ssh_environments=[
            cfg.SSHEnvironment("windows", "remote-native"),
            cfg.SSHEnvironment("wsl", "remote-guest"),
        ],
    )
    for entries in ({local.key: local}, normalize_machine_entries({local.key: local, remote.key: remote})):
        assert machine_identity.resolve_execution_machine(
            local.key, guest=True, configured=True, load_entries=lambda: entries,
        ) == "example-host-wsl"


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_execution_context_preserves_declared_dimensions(
    monkeypatch: pytest.MonkeyPatch, environment: str,
) -> None:
    entry = cfg.MachineEntry(
        key="custom-space", display_name="Custom space", execution_platform=environment,
        physical_host="physical-host-wsl", hostname="Independent-Hostname",
    )
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda repo: {entry.key: entry})
    selected = machine_identity.resolve_execution_context(
        entry.key, Path("/synthetic"), environment=environment,
    )
    assert selected.entry == entry
    assert selected.target.machine == "physical-host-wsl"
    assert selected.target.environment == environment
    assert selected.entry.key == "custom-space"
    assert selected.entry.hostname == "Independent-Hostname"


@pytest.mark.parametrize("missing", [False, True])
def test_execution_context_does_not_infer_unknown_physical_host(
    monkeypatch: pytest.MonkeyPatch, missing: bool,
) -> None:
    def entries(repo: str | Path) -> dict[str, cfg.MachineEntry]:
        if missing:
            raise FileNotFoundError("no optional registry")
        return {}

    monkeypatch.setattr(cfg, "load_machines_yaml", entries)
    selected = machine_identity.resolve_execution_context(
        "unregistered-wsl", Path("/synthetic"), environment="wsl",
    )
    assert selected.entry is None
    assert selected.target.machine == "unregistered-wsl"


def test_execution_context_refuses_conflicting_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = cfg.MachineEntry(
        key="guest-space", display_name="Guest space",
        execution_platform="wsl", physical_host="physical-host",
    )
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda repo: {entry.key: entry})
    with pytest.raises(ValueError, match="conflicts"):
        machine_identity.resolve_execution_context(
            entry.key, Path("/synthetic"), environment="windows",
        )


def test_execution_context_refuses_ambiguous_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = {
        key: cfg.MachineEntry(
            key=key, display_name=key, execution_platform="wsl",
            physical_host=key, hostname="Shared-Hostname",
        )
        for key in ("first-space", "second-space")
    }
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda repo: entries)
    with pytest.raises(ValueError):
        machine_identity.resolve_execution_context(
            "Shared-Hostname", Path("/synthetic"), environment="wsl",
        )


def test_repository_config_keeps_unregistered_detection_project_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import front_door_cli

    root = Path("/synthetic")
    monkeypatch.setattr(front_door_cli, "_git_toplevel", lambda repo: None)
    monkeypatch.setattr(front_door_cli, "_reverse_lookup_project", lambda repo: None)
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *args, **kwargs: pytest.fail("unregistered detection must not load ambient config"),
    )
    assert machine_identity.repository_config(root) is None


def test_execution_registry_keeps_checkout_local_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mixed_registry_config: cfg.Config,
) -> None:
    from agent_worktrees import front_door_cli

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "machines.yaml").write_text(
        "machines:\n  checkout-space:\n    execution_platform: windows\n"
        "    physical_host: checkout-host\n", encoding="utf-8",
    )
    monkeypatch.setattr(
        front_door_cli, "_git_toplevel",
        lambda path: pytest.fail("an existing checkout registry must not fall back"),
    )
    entries = machine_identity.load_execution_registry(checkout, config=mixed_registry_config)
    assert set(entries) == {"checkout-space"}
    assert entries["checkout-space"].physical_host == "checkout-host"


def test_execution_registry_refuses_foreign_anchor_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mixed_registry_config: cfg.Config,
) -> None:
    from agent_worktrees import front_door_cli

    checkout = tmp_path / "foreign-checkout"
    checkout.mkdir()
    monkeypatch.setattr(front_door_cli, "_git_toplevel", lambda path: tmp_path / "foreign-anchor")
    with pytest.raises(FileNotFoundError):
        machine_identity.load_execution_registry(checkout, config=mixed_registry_config)


def test_execution_registry_does_not_hide_invalid_checkout_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mixed_registry_config: cfg.Config,
) -> None:
    from agent_worktrees import front_door_cli

    checkout = tmp_path / "invalid-checkout"
    checkout.mkdir()
    (checkout / "machines.yaml").write_text("machines: []\n", encoding="utf-8")
    monkeypatch.setattr(
        front_door_cli, "_git_toplevel",
        lambda path: pytest.fail("invalid local data must not borrow the anchor registry"),
    )
    with pytest.raises(ValueError):
        machine_identity.load_execution_registry(checkout, config=mixed_registry_config)


class TestIsLocalMachine:
    @pytest.fixture(autouse=True)
    def native_execution(self, monkeypatch) -> None:
        monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")

    def _write(self, tmp_path: Path, body: str) -> Path:
        (tmp_path / "machines.yaml").write_text(body, encoding="utf-8")
        return tmp_path

    def _config(self, tmp_path: Path, machine: str) -> cfg.Config:
        repo = cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "wt"))
        return cfg.Config(
            srcroot=str(tmp_path), machine=machine, platform="windows",
            repo_name="proj", repos={"proj": repo},
        )

    def test_exact_match_against_config_machine(self, tmp_path: Path):
        config = self._config(tmp_path, "example-cloud2")
        assert machine_identity.is_local_machine("example-cloud2", config) is True

    def test_case_insensitive_direct_match(self, tmp_path: Path):
        config = self._config(tmp_path, "example-cloud2")
        assert machine_identity.is_local_machine("EXAMPLE-CLOUD2", config) is True

    def test_alias_vs_key_both_resolve_to_this_machine(self, tmp_path: Path):
        # config.machine is the alias; a caller passing the registry KEY for
        # the very same entry must still be recognized as local.
        self._write(tmp_path, (
            "machines:\n"
            "  example-cloud2:\n"
            "    display_name: cloud2\n"
            "    alias: example-cloud2\n"
            "    environment: Windows 11\n"
        ))
        config = self._config(tmp_path, "example-cloud2")
        assert machine_identity.is_local_machine("example-cloud2", config) is True

    def test_raw_hostname_vs_canonical_alias_both_resolve_to_this_machine(
        self, tmp_path: Path, monkeypatch,
    ):
        # The exact bug class this helper exists to fix: a stale
        # claim/codename recorded under the raw COMPUTERNAME before the
        # hostname-field decoupling landed, compared against the current
        # canonical alias -- a bare string `==` would wrongly call this
        # remote and SSH-loopback to the very machine running the check.
        self._write(tmp_path, (
            "machines:\n"
            "  example-cloud2:\n"
            "    display_name: cloud2\n"
            "    alias: example-cloud2\n"
            "    hostname: CPC-example-Y97MC\n"
            "    environment: Windows 11\n"
        ))
        monkeypatch.setattr(cfg.socket, "gethostname", lambda: "CPC-example-Y97MC")
        config = self._config(tmp_path, "example-cloud2")
        assert machine_identity.is_local_machine("CPC-example-Y97MC", config) is True

    def test_genuinely_different_machine_is_not_local(self, tmp_path: Path):
        self._write(tmp_path, (
            "machines:\n"
            "  example-cloud2:\n"
            "    display_name: cloud2\n"
            "    alias: example-cloud2\n"
            "    environment: Windows 11\n"
            "  example-cloud1:\n"
            "    display_name: cloud1\n"
            "    alias: example-cloud1\n"
            "    environment: Windows 11\n"
        ))
        config = self._config(tmp_path, "example-cloud2")
        assert machine_identity.is_local_machine("example-cloud1", config) is False

    def test_unknown_name_is_not_local(self, tmp_path: Path):
        config = self._config(tmp_path, "example-cloud2")
        assert machine_identity.is_local_machine("nonexistent-box", config) is False

    def test_empty_name_is_not_local(self, tmp_path: Path):
        config = self._config(tmp_path, "example-cloud2")
        assert machine_identity.is_local_machine("", config) is False

    def test_missing_registry_falls_back_gracefully(self, tmp_path: Path):
        # No machines.yaml at all -- direct-match path still works, and an
        # unresolvable name degrades to False rather than raising.
        config = self._config(tmp_path, "example-cloud2")
        assert machine_identity.is_local_machine("example-cloud2", config) is True
        assert machine_identity.is_local_machine("some-other-box", config) is False


@pytest.mark.parametrize(("actual", "configured", "guest"), [
    ("wsl", "windows", True),
    ("windows", "wsl", False),
    ("linux", "wsl", False),
])
def test_locality_uses_execution_platform_not_path_override(
    tmp_path: Path, monkeypatch, actual: str, configured: str, guest: bool,
) -> None:
    config = cfg.Config(
        srcroot=str(tmp_path), machine="example-host", platform=configured,
        repo_name="proj", repos={"proj": cfg.RepoConfig(
            anchor=str(tmp_path), worktree_root=str(tmp_path / "wt"),
        )},
    )
    captured = {}

    def check(name, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(cfg, "detect_platform", lambda: actual)
    monkeypatch.setattr(machine_identity, "_mt_is_local_machine", check)
    assert machine_identity.is_local_machine("example-host", config)
    assert captured["guest"] is guest
