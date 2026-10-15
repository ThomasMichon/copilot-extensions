"""Tests for ``machine_identity.is_local_machine`` -- canonicalized
same-machine check (never a bare string comparison against config.machine)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import machine_identity


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
