"""Tests for the config gate + `agent-codespaces owner` entrypoint (dotfiles#1333).

Covers the deploy-gated increment: a default-off ``connection_owner`` config key
and the CLI entrypoint that runs the reconcile daemon. The entrypoint is additive
+ opt-in -- it refuses to run unless enabled (or ``--force``), and ``--once`` with
no holds is a safe no-op that exercises the wiring without a real CodeSpace.
"""

from __future__ import annotations

import agent_codespaces.config as cfg
import pytest
from agent_codespaces import __main__ as m
from agent_codespaces import connection_owner as owner
from agent_codespaces.config import (
    AdoptedRepo,
    CodespacesConfig,
    ConnectionOwnerConfig,
)


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Redirect Connection Owner registry state to tmp (never touch real state)."""
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "connection-owner.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "connection-owner.lock")
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    return tmp_path


# --- config parsing --------------------------------------------------------

def test_connection_owner_default_off(monkeypatch):
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [])
    monkeypatch.setattr(cfg, "discover_dropin_configs", lambda: [])
    merged = cfg.load_merged_config(include_cwd=False)
    assert merged.connection_owner.enabled is False
    assert merged.connection_owner.reconcile_interval == 15.0


def test_connection_owner_parsed_from_repo(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    conf = repo / ".agent-codespaces" / "config.yaml"
    conf.parent.mkdir(parents=True)
    conf.write_text(
        "connection_owner:\n  enabled: true\n  reconcile_interval: 30\n",
        "utf-8",
    )
    monkeypatch.setattr(cfg, "load_adopted_repos", lambda: [AdoptedRepo(path=repo)])
    monkeypatch.setattr(cfg, "discover_dropin_configs", lambda: [])
    merged = cfg.load_merged_config(include_cwd=False)
    assert merged.connection_owner.enabled is True
    assert merged.connection_owner.reconcile_interval == 30.0


# --- CLI entrypoint gating -------------------------------------------------

def _stub_config(enabled=False, interval=15.0):
    c = CodespacesConfig()
    c.connection_owner = ConnectionOwnerConfig(
        enabled=enabled, reconcile_interval=interval
    )
    return c


def test_owner_disabled_is_noop(monkeypatch, capsys):
    """Default (disabled) + no --force: prints a note, exits 0, builds no factory."""
    monkeypatch.setattr(
        cfg, "load_merged_config", lambda include_cwd=True: _stub_config(enabled=False)
    )
    built = {"factory": False}

    def _factory(*_a, **_k):
        built["factory"] = True
        return lambda _cs: None

    monkeypatch.setattr(owner, "make_supervised_relay_factory", _factory)

    rc = m.main(["owner"])
    assert rc == 0
    assert "disabled" in capsys.readouterr().err
    assert built["factory"] is False  # never constructs the transport when off


def test_owner_force_once_reconciles(monkeypatch, capsys, store):
    """--force --once reconciles a single empty cycle: safe no-op, exits 0."""
    monkeypatch.setattr(
        cfg, "load_merged_config", lambda include_cwd=True: _stub_config(enabled=False)
    )
    # Dummy factory -- never invoked (no holds), so no real ssh/CodeSpace touched.
    monkeypatch.setattr(
        owner, "make_supervised_relay_factory", lambda *_a, **_k: (lambda _cs: None)
    )

    rc = m.main(["owner", "--force", "--once"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "reconciled once" in out
    assert "held=[]" in out


def test_owner_enabled_once_reconciles(monkeypatch, capsys, store):
    """Enabled via config (no --force): --once reconciles + exits 0."""
    monkeypatch.setattr(
        cfg, "load_merged_config", lambda include_cwd=True: _stub_config(enabled=True)
    )
    monkeypatch.setattr(
        owner, "make_supervised_relay_factory", lambda *_a, **_k: (lambda _cs: None)
    )
    rc = m.main(["owner", "--once"])
    assert rc == 0
    assert "reconciled once" in capsys.readouterr().out


def test_owner_rejects_nonpositive_interval(monkeypatch, capsys, store):
    """A 0/negative interval fails fast with a clear error, not a daemon crash."""
    monkeypatch.setattr(
        cfg, "load_merged_config", lambda include_cwd=True: _stub_config(enabled=True)
    )
    monkeypatch.setattr(
        owner, "make_supervised_relay_factory", lambda *_a, **_k: (lambda _cs: None)
    )
    rc = m.main(["owner", "--interval", "0"])
    assert rc == 1
    assert "must be > 0" in capsys.readouterr().err


def test_owner_status_disabled(monkeypatch, capsys):
    """--status prints the resolved config as JSON without starting anything."""
    monkeypatch.setattr(
        cfg, "load_merged_config", lambda include_cwd=True: _stub_config(enabled=False)
    )
    built = {"factory": False}

    def _factory(*_a, **_k):
        built["factory"] = True
        return lambda _cs: None

    monkeypatch.setattr(owner, "make_supervised_relay_factory", _factory)

    rc = m.main(["owner", "--status"])
    assert rc == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"enabled": False, "reconcile_interval": 15.0}
    assert built["factory"] is False  # never constructs the transport for a probe


def test_owner_status_enabled(monkeypatch, capsys):
    """--status reflects an enabled config (install uses it to gate provisioning)."""
    monkeypatch.setattr(
        cfg,
        "load_merged_config",
        lambda include_cwd=True: _stub_config(enabled=True, interval=30.0),
    )
    monkeypatch.setattr(
        owner, "make_supervised_relay_factory", lambda *_a, **_k: (lambda _cs: None)
    )
    rc = m.main(["owner", "--status"])
    assert rc == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"enabled": True, "reconcile_interval": 30.0}


def test_connection_owner_empty_block_claims_slot(tmp_path, monkeypatch):
    """An explicit (even empty) connection_owner block claims the first-wins slot."""
    repo1 = tmp_path / "r1"
    c1 = repo1 / ".agent-codespaces" / "config.yaml"
    c1.parent.mkdir(parents=True)
    c1.write_text("connection_owner: {}\n", "utf-8")
    repo2 = tmp_path / "r2"
    c2 = repo2 / ".agent-codespaces" / "config.yaml"
    c2.parent.mkdir(parents=True)
    c2.write_text("connection_owner:\n  enabled: true\n", "utf-8")
    monkeypatch.setattr(
        cfg,
        "load_adopted_repos",
        lambda: [AdoptedRepo(path=repo1), AdoptedRepo(path=repo2)],
    )
    monkeypatch.setattr(cfg, "discover_dropin_configs", lambda: [])
    merged = cfg.load_merged_config(include_cwd=False)
    # repo1's empty block claimed the slot -> repo2's enabled:true is ignored.
    assert merged.connection_owner.enabled is False
