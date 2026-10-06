"""Tests for ``machine_identity.is_local_machine`` -- canonicalized
same-machine check (never a bare string comparison against config.machine)."""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import config as cfg
from agent_worktrees import machine_identity


class TestIsLocalMachine:
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
        config = self._config(tmp_path, "tmichon-cloud2")
        assert machine_identity.is_local_machine("tmichon-cloud2", config) is True

    def test_case_insensitive_direct_match(self, tmp_path: Path):
        config = self._config(tmp_path, "tmichon-cloud2")
        assert machine_identity.is_local_machine("TMICHON-CLOUD2", config) is True

    def test_alias_vs_key_both_resolve_to_this_machine(self, tmp_path: Path):
        # config.machine is the alias; a caller passing the registry KEY for
        # the very same entry must still be recognized as local.
        self._write(tmp_path, (
            "machines:\n"
            "  tmichon-cloud2:\n"
            "    display_name: cloud2\n"
            "    alias: tmichon-cloud2\n"
            "    environment: Windows 11\n"
        ))
        config = self._config(tmp_path, "tmichon-cloud2")
        assert machine_identity.is_local_machine("tmichon-cloud2", config) is True

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
            "  tmichon-cloud2:\n"
            "    display_name: cloud2\n"
            "    alias: tmichon-cloud2\n"
            "    hostname: CPC-tmich-Y97MC\n"
            "    environment: Windows 11\n"
        ))
        monkeypatch.setattr(cfg.socket, "gethostname", lambda: "CPC-tmich-Y97MC")
        config = self._config(tmp_path, "tmichon-cloud2")
        assert machine_identity.is_local_machine("CPC-tmich-Y97MC", config) is True

    def test_genuinely_different_machine_is_not_local(self, tmp_path: Path):
        self._write(tmp_path, (
            "machines:\n"
            "  tmichon-cloud2:\n"
            "    display_name: cloud2\n"
            "    alias: tmichon-cloud2\n"
            "    environment: Windows 11\n"
            "  tmichon-cloud1:\n"
            "    display_name: cloud1\n"
            "    alias: tmichon-cloud1\n"
            "    environment: Windows 11\n"
        ))
        config = self._config(tmp_path, "tmichon-cloud2")
        assert machine_identity.is_local_machine("tmichon-cloud1", config) is False

    def test_unknown_name_is_not_local(self, tmp_path: Path):
        config = self._config(tmp_path, "tmichon-cloud2")
        assert machine_identity.is_local_machine("nonexistent-box", config) is False

    def test_empty_name_is_not_local(self, tmp_path: Path):
        config = self._config(tmp_path, "tmichon-cloud2")
        assert machine_identity.is_local_machine("", config) is False

    def test_missing_registry_falls_back_gracefully(self, tmp_path: Path):
        # No machines.yaml at all -- direct-match path still works, and an
        # unresolvable name degrades to False rather than raising.
        config = self._config(tmp_path, "tmichon-cloud2")
        assert machine_identity.is_local_machine("tmichon-cloud2", config) is True
        assert machine_identity.is_local_machine("some-other-box", config) is False
