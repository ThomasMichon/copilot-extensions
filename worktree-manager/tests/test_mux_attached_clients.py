"""Tests for ``worktree_manager.mux_attached_clients`` (#4564 --
attached_clients freshness). Split out of ``test_mux_daemon.py`` to mirror
the module split (see ``mux_attached_clients.py``'s own docstring for why)."""

from __future__ import annotations

import subprocess

from worktree_manager import mux_attached_clients


def _fake_run_factory(*, list_clients_lines=None, list_clients_ok=True):
    def _fake_run(argv, **kwargs):
        if not list_clients_ok:
            return subprocess.CompletedProcess(argv, 1, stdout="")
        lines = list_clients_lines if list_clients_lines is not None else []
        stdout = "\n".join(lines) + ("\n" if lines else "")
        return subprocess.CompletedProcess(argv, 0, stdout=stdout)

    return _fake_run


def test_mux_attached_clients_counts_list_clients_lines(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_run_factory(list_clients_lines=["/dev/pts/1: wt-1", "/dev/pts/2: wt-1"]),
    )
    assert mux_attached_clients.mux_attached_clients("psmux", "wt-1") == 2


def test_mux_attached_clients_reports_zero_for_empty_but_successful_listing(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(list_clients_lines=[]))
    assert mux_attached_clients.mux_attached_clients("psmux", "wt-1") == 0


def test_mux_attached_clients_returns_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run_factory(list_clients_ok=False))
    assert mux_attached_clients.mux_attached_clients("psmux", "wt-1") is None


def test_mux_attached_clients_returns_none_on_exception(monkeypatch):
    def _fake_run(argv, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    assert mux_attached_clients.mux_attached_clients("psmux", "wt-1") is None


class _FakeRegistry:
    """Minimal registry stand-in recording every ``register()`` call."""

    def __init__(self):
        self.registered: list[dict] = []

    def register(self, payload: dict) -> dict:
        self.registered.append(payload)
        return {"applied": True}


def test_refresh_attached_clients_persists_a_changed_count(monkeypatch):
    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", lambda *a: 3)
    registry = _FakeRegistry()
    current = {"mux_bin": "psmux", "mux_session": "wt-1", "attached_clients": 0}
    updated = mux_attached_clients.refresh_attached_clients(registry, current)
    assert updated["attached_clients"] == 3
    assert registry.registered == [updated]
    # the original mapping dict passed in must not be mutated in place
    assert current["attached_clients"] == 0


def test_refresh_attached_clients_skips_write_when_count_unchanged(monkeypatch):
    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", lambda *a: 5)
    registry = _FakeRegistry()
    current = {"mux_bin": "psmux", "mux_session": "wt-1", "attached_clients": 5}
    assert mux_attached_clients.refresh_attached_clients(registry, current) is current
    assert registry.registered == []


def test_refresh_attached_clients_skips_write_on_unknown_probe_result(monkeypatch):
    monkeypatch.setattr(mux_attached_clients, "mux_attached_clients", lambda *a: None)
    registry = _FakeRegistry()
    current = {"mux_bin": "psmux", "mux_session": "wt-1", "attached_clients": 5}
    assert mux_attached_clients.refresh_attached_clients(registry, current) is current
    assert registry.registered == []
