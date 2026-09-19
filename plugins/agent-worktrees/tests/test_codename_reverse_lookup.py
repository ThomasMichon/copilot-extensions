"""Tests for cross-machine codename reverse lookup (pr-attribution-codenames
Phase 3)."""

from __future__ import annotations

import types

import pytest

from agent_worktrees import codename_reverse_lookup as crl
from agent_worktrees import config as cfg
from agent_worktrees.config import MachineEntry, SSHEnvironment


def _cfg(machine="lambda-core"):
    return types.SimpleNamespace(
        machine=machine,
        default_repo=types.SimpleNamespace(anchor="/anchor"),
    )


def _machine(key, *, alias="", copilot=True, ssh_ready=True):
    envs = [SSHEnvironment(name="linux", alias=alias)] if alias else []
    return MachineEntry(
        key=key, display_name=key, environment="linux",
        copilot=copilot, ssh_ready=ssh_ready, ssh_environments=envs,
    )


# --- _known_machine_keys -----------------------------------------------------

class TestKnownMachineKeys:
    def test_excludes_self_and_non_ssh_ready(self, monkeypatch):
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: _cfg("lambda-core"))
        monkeypatch.setattr(cfg, "load_machines_yaml", lambda *a, **k: {
            "lambda-core": _machine("lambda-core", alias="lc-alias"),
            "borealis": _machine("borealis", alias="borealis-alias"),
            "wheatley": _machine("wheatley", ssh_ready=False),
            "non-copilot": _machine("non-copilot", copilot=False, alias="x"),
        })
        keys = crl._known_machine_keys(exclude="lambda-core")
        assert keys == ["borealis"]

    def test_registry_unavailable_returns_empty(self, monkeypatch):
        def _raise(*a, **k):
            raise FileNotFoundError("no registry")
        monkeypatch.setattr(cfg, "load_config", _raise)
        assert crl._known_machine_keys(exclude=None) == []


# --- _remote_probe_cmd / _parse_lookup --------------------------------------

class TestRemoteHelpers:
    def test_remote_probe_cmd_bash(self):
        cmd = crl._remote_probe_cmd("bash", "test-chamber", "sturdy-crate")
        assert cmd.startswith("bash -lc '")
        assert "test-chamber codename-lookup sturdy-crate --json" in cmd

    def test_remote_probe_cmd_pwsh_encoded(self):
        import base64
        cmd = crl._remote_probe_cmd("pwsh", "test-chamber", "sturdy-crate")
        assert "-EncodedCommand" in cmd
        b64 = cmd.rsplit(" ", 1)[1]
        decoded = base64.b64decode(b64).decode("utf-16-le")
        assert "test-chamber codename-lookup sturdy-crate --json" in decoded

    def test_parse_lookup_found(self):
        assert crl._parse_lookup(
            '{"codename":"sturdy-crate","found":true,"worktree_id":"wt-A"}'
        ) == "wt-A"

    def test_parse_lookup_not_found(self):
        assert crl._parse_lookup(
            '{"codename":"sturdy-crate","found":false,"worktree_id":null}'
        ) is None

    def test_parse_lookup_tolerates_banner_noise(self):
        assert crl._parse_lookup(
            'login banner\n{"found":true,"worktree_id":"wt-A"}\n'
        ) == "wt-A"

    def test_parse_lookup_bad_json(self):
        assert crl._parse_lookup("not json") is None

    def test_parse_lookup_empty(self):
        assert crl._parse_lookup("") is None


# --- _probe_machine -----------------------------------------------------------

class TestProbeMachine:
    def test_unresolved_machine_none(self, monkeypatch):
        monkeypatch.setattr(
            "agent_worktrees.claimant.resolve_machine_ssh", lambda k: None,
        )
        assert crl._probe_machine("borealis", "test-chamber", "sturdy-crate",
                                  timeout=1) is None

    def test_ssh_error_none(self, monkeypatch):
        monkeypatch.setattr(
            "agent_worktrees.claimant.resolve_machine_ssh",
            lambda k: ("borealis-alias", "bash"),
        )

        def _boom(*a, **k):
            raise OSError("ssh missing")

        monkeypatch.setattr(crl.subprocess, "run", _boom)
        assert crl._probe_machine("borealis", "test-chamber", "sturdy-crate",
                                  timeout=1) is None

    def test_nonzero_returncode_none(self, monkeypatch):
        monkeypatch.setattr(
            "agent_worktrees.claimant.resolve_machine_ssh",
            lambda k: ("borealis-alias", "bash"),
        )
        proc = types.SimpleNamespace(returncode=255, stdout="", stderr="down")
        monkeypatch.setattr(crl.subprocess, "run", lambda *a, **k: proc)
        assert crl._probe_machine("borealis", "test-chamber", "sturdy-crate",
                                  timeout=1) is None

    def test_success_returns_worktree_id(self, monkeypatch):
        monkeypatch.setattr(
            "agent_worktrees.claimant.resolve_machine_ssh",
            lambda k: ("borealis-alias", "bash"),
        )
        proc = types.SimpleNamespace(
            returncode=0,
            stdout='{"found":true,"worktree_id":"wt-remote"}',
            stderr="",
        )
        monkeypatch.setattr(crl.subprocess, "run", lambda *a, **k: proc)
        assert crl._probe_machine("borealis", "test-chamber", "sturdy-crate",
                                  timeout=1) == "wt-remote"


# --- resolve_codename_cross_machine / _unique -------------------------------

class TestResolveCodenameCrossMachine:
    def test_empty_codename_returns_empty(self):
        assert crl.resolve_codename_cross_machine("") == []

    def test_disabled_by_env_returns_empty(self, monkeypatch):
        monkeypatch.setenv(crl.NO_REMOTE_ENV, "1")
        called = []
        monkeypatch.setattr(crl, "_probe_machine",
                            lambda *a, **k: called.append(1))
        assert crl.resolve_codename_cross_machine("sturdy-crate") == []
        assert called == []

    def test_no_project_resolvable_returns_empty(self, monkeypatch):
        monkeypatch.delenv(crl.NO_REMOTE_ENV, raising=False)

        def _raise(*a, **k):
            raise RuntimeError("no active project")
        monkeypatch.setattr(cfg, "project_name", _raise)
        assert crl.resolve_codename_cross_machine("sturdy-crate") == []

    def test_malformed_codename_never_reaches_ssh(self, monkeypatch):
        # Defense-in-depth against command injection: `codename` is
        # interpolated into a remote shell command by `_remote_probe_cmd`
        # (`bash -lc '...'` / a pwsh EncodedCommand payload); an invalid
        # codename (quotes, shell metacharacters) must never reach that
        # code path at all -- validated and rejected before any SSH call,
        # not merely escaped.
        monkeypatch.delenv(crl.NO_REMOTE_ENV, raising=False)
        called = []
        monkeypatch.setattr(crl, "_probe_machine",
                            lambda *a, **k: called.append(1))
        for malformed in (
            "not a handle",
            "'; rm -rf / #",
            "$(whoami)",
            "handle`touch pwned`",
            "handle;evil",
        ):
            assert crl.resolve_codename_cross_machine(malformed) == []
        assert called == []

    def test_malformed_explicit_project_never_reaches_ssh(self, monkeypatch):
        # Same command-injection concern as the codename, for an explicitly
        # supplied `project` argument -- also interpolated into the remote
        # shell command by `_remote_probe_cmd`.
        monkeypatch.delenv(crl.NO_REMOTE_ENV, raising=False)
        called = []
        monkeypatch.setattr(crl, "_probe_machine",
                            lambda *a, **k: called.append(1))
        for malformed_project in (
            "'; rm -rf / #",
            "$(whoami)",
            "project`touch pwned`",
            "project;evil",
            "",
        ):
            assert crl.resolve_codename_cross_machine(
                "sturdy-crate", project=malformed_project,
            ) == []
        assert called == []

    def test_valid_explicit_project_is_accepted(self, monkeypatch):
        monkeypatch.delenv(crl.NO_REMOTE_ENV, raising=False)
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: _cfg("lambda-core"))
        monkeypatch.setattr(crl, "_known_machine_keys", lambda *, exclude: [])
        # Must not raise / must not be rejected as malformed.
        assert crl.resolve_codename_cross_machine(
            "sturdy-crate", project="test-chamber",
        ) == []

    def test_scans_every_known_machine_and_collects_matches(self, monkeypatch):
        monkeypatch.delenv(crl.NO_REMOTE_ENV, raising=False)
        monkeypatch.setattr(cfg, "project_name", lambda: "test-chamber")
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: _cfg("lambda-core"))
        monkeypatch.setattr(crl, "_known_machine_keys",
                            lambda *, exclude: ["borealis", "wheatley"])

        def _fake_probe(machine_key, project, codename, *, timeout):
            assert project == "test-chamber"
            assert codename == "sturdy-crate"
            return "wt-B" if machine_key == "borealis" else None

        monkeypatch.setattr(crl, "_probe_machine", _fake_probe)
        matches = crl.resolve_codename_cross_machine("sturdy-crate")
        assert matches == [crl.RemoteCodenameMatch(machine="borealis", worktree_id="wt-B")]

    def test_collision_across_two_machines_reported(self, monkeypatch):
        monkeypatch.delenv(crl.NO_REMOTE_ENV, raising=False)
        monkeypatch.setattr(cfg, "project_name", lambda: "test-chamber")
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: _cfg("lambda-core"))
        monkeypatch.setattr(crl, "_known_machine_keys",
                            lambda *, exclude: ["borealis", "wheatley"])
        monkeypatch.setattr(crl, "_probe_machine",
                            lambda machine_key, *a, **k: f"wt-on-{machine_key}")
        matches = crl.resolve_codename_cross_machine("sturdy-crate")
        assert len(matches) == 2

    def test_collision_raises_ambiguous(self, monkeypatch):
        monkeypatch.delenv(crl.NO_REMOTE_ENV, raising=False)
        monkeypatch.setattr(cfg, "project_name", lambda: "test-chamber")
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: _cfg("lambda-core"))
        monkeypatch.setattr(crl, "_known_machine_keys",
                            lambda *, exclude: ["borealis", "wheatley"])
        monkeypatch.setattr(crl, "_probe_machine",
                            lambda machine_key, *a, **k: f"wt-on-{machine_key}")
        with pytest.raises(crl.AmbiguousCodenameError) as excinfo:
            crl.resolve_codename_cross_machine_unique("sturdy-crate")
        assert "borealis" in str(excinfo.value) and "wheatley" in str(excinfo.value)
        assert len(excinfo.value.matches) == 2

    def test_unique_returns_none_when_no_match(self, monkeypatch):
        monkeypatch.delenv(crl.NO_REMOTE_ENV, raising=False)
        monkeypatch.setattr(cfg, "project_name", lambda: "test-chamber")
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: _cfg("lambda-core"))
        monkeypatch.setattr(crl, "_known_machine_keys", lambda *, exclude: [])
        assert crl.resolve_codename_cross_machine_unique("sturdy-crate") is None

    def test_unique_returns_single_match(self, monkeypatch):
        monkeypatch.delenv(crl.NO_REMOTE_ENV, raising=False)
        monkeypatch.setattr(cfg, "project_name", lambda: "test-chamber")
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: _cfg("lambda-core"))
        monkeypatch.setattr(crl, "_known_machine_keys",
                            lambda *, exclude: ["borealis"])
        monkeypatch.setattr(crl, "_probe_machine",
                            lambda machine_key, *a, **k: "wt-B")
        match = crl.resolve_codename_cross_machine_unique("sturdy-crate")
        assert match == crl.RemoteCodenameMatch(machine="borealis", worktree_id="wt-B")
