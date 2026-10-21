"""Adopted activation belongs to the constructed Manager runtime."""

from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

from worktree_manager import runtime_recovery as recovery


@pytest.fixture
def adoption(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    registry = tmp_path / "providers"
    registry.mkdir()
    marker = root / "current-version"
    marker.write_bytes(b"0.6.35-dev1\r\n")
    path = tmp_path / "adoption.json"
    path.write_text(json.dumps({
        "schema": "copilot-extensions.manager-adoption", "version": 1,
        "root": str(root), "registry": str(registry), "registrations": [],
        "evidence": [[str(marker), hashlib.sha256(marker.read_bytes()).hexdigest()]],
    }))
    return SimpleNamespace(root=root, registry=registry, marker=marker, path=path)


def test_adoption_snapshot_detects_selector_change(adoption):
    recovery.verify_adoption(adoption.path, adoption.root)
    adoption.marker.write_bytes(b"other\n")
    with pytest.raises(RuntimeError, match="adoption changed"):
        recovery.verify_adoption(adoption.path, adoption.root)


def test_adoption_snapshot_detects_registry_change(adoption):
    (adoption.registry / "new.json").write_text("{}")
    with pytest.raises(RuntimeError, match="registrations changed"):
        recovery.verify_adoption(adoption.path, adoption.root)


def test_activation_refuses_another_interpreter(adoption, monkeypatch):
    monkeypatch.setattr(sys, "prefix", str(adoption.root / "foreign-interpreter"))
    with pytest.raises(RuntimeError, match="constructed Manager slot"):
        recovery.main(["--root", str(adoption.root), "--evidence", str(adoption.path)])


def test_activation_reuses_ready_slot_and_runs_transactional_cutover(adoption, monkeypatch, capsys):
    from worktree_manager import mux_daemon_cutover, self_install

    slot = adoption.root / "versions" / "1.2.3+repair.fixture"
    (slot / ".runtime-bootstrap").mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(slot))
    monkeypatch.setattr(self_install, "payload_version", lambda payload: "1.2.3")
    monkeypatch.setattr(self_install, "needs_install", lambda version, root: True)
    calls = []
    monkeypatch.setattr(
        mux_daemon_cutover, "activate_after_update",
        lambda **kwargs: calls.append(kwargs) or {"action": "no-live-daemon"},
    )

    def complete(ready, payload, root, **kwargs):
        assert ready.slot == slot and ready.reused
        assert payload == slot / "payload" and root == adoption.root
        assert kwargs["transactional_activation"] is True
        assert kwargs["previous"] == "0.6.35-dev1"
        kwargs["before_publish"]()
        kwargs["after_activate"](ready.slot)
        return SimpleNamespace(action="installed", slot=str(slot), version="1.2.3")

    monkeypatch.setattr(self_install, "_complete_candidate", complete)
    assert recovery.main([
        "--root", str(adoption.root), "--evidence", str(adoption.path), "--reused",
    ]) == 0
    assert calls == [{"root": adoption.root, "slot": slot, "version": slot.name}]
    assert json.loads(capsys.readouterr().out)["status"] == "installed"
