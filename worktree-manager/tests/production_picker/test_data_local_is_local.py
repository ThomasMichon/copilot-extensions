"""Unit tests for ``data_local.is_local`` -- canonicalized "is this host?"

Regression coverage for a self-looping-SSH bug: a worktree record, an
embodied-task payload, or any other producer can carry the canonical
``machines.yaml`` key/alias for THIS machine (e.g. a cloud box's configured
``aurora-cloud2`` alias) while ``data_local.LOCAL`` is spelled from the raw OS
hostname (e.g. a devtunnel-provisioned box's real ``socket.gethostname()``,
which can differ from its configured alias). A naive
``(machine, env) == LOCAL`` tuple comparison then wrongly concludes "remote"
and dispatches over SSH to reach a host that is actually this one -- see ``engine_worktree_actions.py``'s
``_resume_embodied_worktree_cli``/``_embody_task_cli``, which depend on
``is_local`` for exactly this reason.
"""
from __future__ import annotations

import types

import pytest

from agent_worktrees import config as agent_cfg
from worktree_manager.production_picker.picker_tui import data_local


def _entry(key, *, alias="", hostname=""):
    return agent_cfg.MachineEntry(
        key=key,
        display_name=key,
        environment="",
        alias=alias,
        hostname=hostname,
        ssh_environments=[],
        ssh_ready=False,
        copilot=True,
    )


def _install_roster(monkeypatch, entries, *, machine):
    fake_config = types.SimpleNamespace(
        default_repo=types.SimpleNamespace(anchor="/repo"),
        machine=machine,
    )
    monkeypatch.setattr(data_local.cfg, "load_config", lambda: fake_config)
    monkeypatch.setattr(
        data_local.cfg, "load_machines_yaml", lambda _anchor: entries)


def test_is_local_fast_path_matches_raw_local_tuple():
    assert data_local.is_local(*data_local.LOCAL) is True


def test_is_local_rejects_a_different_env_even_with_same_machine(monkeypatch):
    # A machine name that matches but a platform/env that doesn't is never
    # "local" -- e.g. the WSL tab of this same box.
    assert data_local.is_local(data_local.LOCAL[0], "some-other-env") is False


def test_is_local_canonicalizes_a_registry_alias_for_this_host(monkeypatch):
    """The exact regression: ``machine`` is this host's canonical
    ``machines.yaml`` key (``aurora-cloud2``), not its raw OS hostname."""
    entries = {"aurora-cloud2": _entry("aurora-cloud2")}
    _install_roster(monkeypatch, entries, machine="aurora-cloud2")
    assert data_local.is_local("aurora-cloud2", data_local.LOCAL[1]) is True


def test_is_local_canonicalizes_via_hostname_field(monkeypatch):
    """The registry entry's own ``hostname:`` field matching this host's real
    OS hostname is also sufficient, even if its key/alias differ."""
    entries = {
        "my-alias": _entry("my-alias", hostname=data_local.LOCAL[0]),
    }
    _install_roster(monkeypatch, entries, machine="")
    assert data_local.is_local("my-alias", data_local.LOCAL[1]) is True


def test_is_local_canonicalizes_via_alias_field(monkeypatch):
    entries = {
        "aurora-cloud2": _entry("aurora-cloud2", alias="aurora-cloud2"),
    }
    _install_roster(monkeypatch, entries, machine="aurora-cloud2")
    assert data_local.is_local("aurora-cloud2", data_local.LOCAL[1]) is True


def test_is_local_rejects_a_genuinely_different_machine(monkeypatch):
    entries = {
        "aurora-cloud2": _entry("aurora-cloud2"),
        "aurora-cloud1": _entry("aurora-cloud1"),
    }
    _install_roster(monkeypatch, entries, machine="aurora-cloud2")
    assert data_local.is_local("aurora-cloud1", data_local.LOCAL[1]) is False


def test_is_local_rejects_unknown_machine(monkeypatch):
    _install_roster(monkeypatch, {}, machine="aurora-cloud2")
    assert data_local.is_local("nonexistent-box", data_local.LOCAL[1]) is False


def test_is_local_rejects_empty_machine():
    assert data_local.is_local("", data_local.LOCAL[1]) is False


def test_is_local_degrades_safely_when_registry_unavailable(monkeypatch):
    def _boom():
        raise FileNotFoundError("no config")

    monkeypatch.setattr(data_local.cfg, "load_config", _boom)
    assert data_local.is_local("aurora-cloud2", data_local.LOCAL[1]) is False


def test_is_local_preserves_direct_fallback_during_a_registry_outage(monkeypatch):
    """``machine_identity.is_local_machine`` checks the direct
    ``machine == config.machine`` case BEFORE loading the registry so a
    registry outage/malformed file doesn't mask an otherwise-known local
    alias; ``is_local`` must preserve that same ordering."""
    fake_config = types.SimpleNamespace(
        default_repo=types.SimpleNamespace(anchor="/repo"),
        machine="aurora-cloud2",
    )
    monkeypatch.setattr(data_local.cfg, "load_config", lambda: fake_config)

    def _boom(_anchor):
        raise ValueError("machines.yaml is malformed")

    monkeypatch.setattr(data_local.cfg, "load_machines_yaml", _boom)
    assert data_local.is_local("aurora-cloud2", data_local.LOCAL[1]) is True


def test_is_local_does_not_blanket_match_on_an_empty_config_machine(monkeypatch):
    """A legitimately empty ``config.machine`` must never blanket-match every
    roster entry's own unset/empty ``alias`` (both compare equal to ``""``
    otherwise) -- that would misclassify a genuinely remote machine as local
    the moment this host's own config carries no machine name."""
    entries = {"remote-box": _entry("remote-box")}  # alias="" by default
    _install_roster(monkeypatch, entries, machine="")
    assert data_local.is_local("remote-box", data_local.LOCAL[1]) is False


def test_data_ssh_is_local_delegates_to_data_local():
    from worktree_manager.production_picker.picker_tui import data_ssh

    assert data_ssh.is_local is data_local.is_local
