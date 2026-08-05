"""Tests for the `claim` / `release-claim` CLI seam (#897 Increment B enabler)."""

from __future__ import annotations

from agent_codespaces import lease as lease_mod
from agent_codespaces.__main__ import _BUSY_EXIT, main


def _fake_lease(cs: str, owner: str) -> "lease_mod.Lease":
    return lease_mod.Lease(
        codespace=cs, effort="", pid=1, host="h",
        acquired_at=0.0, heartbeat_at=0.0, worktree=owner,
    )


def test_claim_cmd_acquires(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    monkeypatch.setattr(lease_mod, "active_worktree_ids", lambda: {"/wt/a"})
    seen: dict = {}

    def _claim(cs, owner, **kw):
        seen.update(cs=cs, owner=owner, force=kw.get("force"))
        return _fake_lease(cs, owner)

    monkeypatch.setattr(lease_mod, "claim", _claim)
    rc = main(["claim", "cs-x", "--owner", "/wt/a"])
    assert rc == 0
    assert seen == {"cs": "cs-x", "owner": "/wt/a", "force": False}
    assert "Claimed cs-x" in capsys.readouterr().out


def test_claim_cmd_bounces_on_conflict(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    monkeypatch.setattr(lease_mod, "active_worktree_ids", lambda: {"/wt/a", "/wt/b"})

    def _claim(cs, owner, **kw):
        raise lease_mod.ClaimConflict(cs, "/wt/a", "host", 99)

    monkeypatch.setattr(lease_mod, "claim", _claim)
    rc = main(["claim", "cs-x", "--owner", "/wt/b"])
    assert rc == _BUSY_EXIT
    err = capsys.readouterr().err
    assert "BUSY" in err
    assert "/wt/a" in err


def test_claim_cmd_force_passes_through(monkeypatch):
    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    monkeypatch.setattr(lease_mod, "active_worktree_ids", lambda: set())
    seen: dict = {}

    def _claim(cs, owner, **kw):
        seen["force"] = kw.get("force")
        return _fake_lease(cs, owner)

    monkeypatch.setattr(lease_mod, "claim", _claim)
    main(["claim", "cs-x", "--owner", "/wt/b", "--force-claim"])
    assert seen["force"] is True


def test_claim_cmd_no_owner_is_noop(monkeypatch):
    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    monkeypatch.setattr(
        lease_mod, "resolve_owner_worktree",
        lambda explicit=None, session_id=None: None,
    )
    called = {"claim": False}
    monkeypatch.setattr(
        lease_mod, "claim",
        lambda *a, **k: called.__setitem__("claim", True),
    )
    rc = main(["claim", "cs-x"])
    assert rc == 0
    assert called["claim"] is False  # nothing to key a claim on -> skipped


def test_release_claim_cmd(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_CODESPACES_DISABLE_CLAIM", raising=False)
    seen: dict = {}
    monkeypatch.setattr(
        lease_mod, "release_claim",
        lambda cs, owner, **kw: seen.update(cs=cs, owner=owner) or True,
    )
    rc = main(["release-claim", "cs-x", "--owner", "/wt/a"])
    assert rc == 0
    assert seen == {"cs": "cs-x", "owner": "/wt/a"}
    assert "Released claim on cs-x" in capsys.readouterr().out


def test_claim_cmd_disabled_env_is_noop(monkeypatch, capsys):
    """With the global escape hatch set, the CLI ``claim`` is a no-op success --
    parity with the ``ssh`` direct path, so a daemon shelling ``claim`` with
    claiming disabled never touches host lease state."""
    monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")
    called = {"resolve": False, "claim": False}
    monkeypatch.setattr(
        lease_mod, "resolve_owner_worktree",
        lambda *a, **k: called.__setitem__("resolve", True),
    )
    monkeypatch.setattr(
        lease_mod, "claim", lambda *a, **k: called.__setitem__("claim", True),
    )
    rc = main(["claim", "cs-x", "--owner", "/wt/a"])
    assert rc == 0
    assert called == {"resolve": False, "claim": False}
    assert "disabled" in capsys.readouterr().out.lower()


def test_release_claim_cmd_disabled_env_is_noop(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_CODESPACES_DISABLE_CLAIM", "1")
    called = {"release": False}
    monkeypatch.setattr(
        lease_mod, "release_claim",
        lambda *a, **k: called.__setitem__("release", True),
    )
    rc = main(["release-claim", "cs-x", "--owner", "/wt/a"])
    assert rc == 0
    assert called["release"] is False
    assert "disabled" in capsys.readouterr().out.lower()
