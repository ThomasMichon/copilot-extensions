"""Cold installer activation is a real metadata CAS, never owner startup."""

import sys

import pytest

from agent_pull_requests import watch_selection
from agent_pull_requests.watch_journal import WatchJournal
from agent_pull_requests.watch_selection import WatchSelection
from test_watch_installation import _policy
from test_watch_journal import (
    _begin, _manifest, _select, cell as cell,  # noqa: F401 -- pytest fixture
)

pytestmark = pytest.mark.timeout(180)


def _no_owner(installation):
    assert not installation.state_root.exists()
    assert not (installation.run_root / "active.json").exists()
    assert not (installation.run_root / "watch-daemon.lock").exists()
    assert not list(installation.run_root.glob("watch-owner-launch-*.json"))


def test_cold_activation_selects_actual_completed_runtime_without_spawning_or_adopting(cell, monkeypatch):
    import subprocess

    installation, _, target = cell
    monkeypatch.setattr(subprocess, "Popen", lambda *_a, **_k: pytest.fail("cold metadata must not spawn"))
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        WatchSelection(journal).publish()
        assert (installation.plugin_root / "current-version").read_text().strip() == target.runtime_version
        assert (installation.plugin_root / "last-known-good").read_text().strip() == target.runtime_version
        assert (installation.plugin_root / "deploy-manifest.json").read_bytes() == _manifest(target)
        journal.commit()
        journal.finish()
        assert journal.archive().exists()
    _no_owner(installation)


@pytest.mark.parametrize("interrupted", ["current-version", "last-known-good", "manifest"])
@pytest.mark.parametrize("recover_forward", [False, True])
def test_interrupted_cold_selection_recovers_only_captured_prior_or_target_without_owner(
    cell, monkeypatch, interrupted, recover_forward,
):
    installation, predecessor, _ = cell
    _select(installation, predecessor.runtime)
    # Rollback must preserve captured bytes, not silently normalize old metadata.
    (installation.plugin_root / "current-version").write_bytes(b"1.0.0")
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        prior = dict(journal._document["prior"])
        with monkeypatch.context() as patched:
            if interrupted == "manifest":
                original = watch_selection._replace_metadata

                def fail(path, value, **kwargs):
                    original(path, value, **kwargs)
                    raise OSError("test-owned manifest interruption")

                patched.setattr(watch_selection, "_replace_metadata", fail)
            else:
                slot_module = sys.modules[watch_selection.api.cutover_runtime_slot.__module__]
                original = slot_module._atomic_write_text

                def fail(path, value, **kwargs):
                    original(path, value, **kwargs)
                    if path.name == interrupted:
                        raise OSError("test-owned marker interruption")

                patched.setattr(slot_module, "_atomic_write_text", fail)
            with pytest.raises(OSError, match="test-owned"):
                WatchSelection(journal).publish()
        recovered = WatchJournal.recover(installation, guard, environment={})
        assert recovered._document["prior"] == prior
        if recover_forward:
            WatchSelection(recovered).publish()
            recovered.commit()
        else:
            recovered.rollback()
            WatchSelection(recovered).rollback()
            assert watch_selection._selection(installation) == prior
        recovered.finish()
        assert recovered.archive().exists()
    _no_owner(installation)


def test_committed_cold_selection_repairs_only_its_known_metadata_forward(cell):
    installation, _, target = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        WatchSelection(journal).publish()
        journal.commit()
        (installation.plugin_root / "deploy-manifest.json").unlink()
        recovered = WatchJournal.recover(installation, guard, environment={})
        with pytest.raises(PermissionError, match="forward-only"):
            recovered.rollback()
        WatchSelection(recovered).publish()
        recovered.finish()
        assert (installation.plugin_root / "deploy-manifest.json").read_bytes() == _manifest(target)
    _no_owner(installation)


@pytest.mark.parametrize("foreign", ["current-version", "deploy-manifest.json", "governance"])
def test_cold_selection_refuses_unknown_metadata_or_disabled_governance(cell, foreign):
    installation, _, _ = cell
    with installation.transaction(timeout=1, environment={}) as guard:
        journal = _begin(cell, guard, live=False)
        if foreign == "governance":
            _policy(installation.os_profile, False)
        else:
            (installation.plugin_root / foreign).write_bytes(b"foreign")
        before = watch_selection._selection(installation)
        with pytest.raises(PermissionError):
            WatchSelection(journal).publish()
        assert watch_selection._selection(installation) == before
    _no_owner(installation)


def test_nonsecret_metadata_race_is_not_overwritten_and_temporary_is_cleaned(tmp_path, monkeypatch):
    path = tmp_path / "deploy-manifest.json"
    path.write_bytes(b"prior")
    read = watch_selection._regular_bytes
    calls = []

    def concurrent(current):
        calls.append(current)
        if len(calls) == 2:
            path.write_bytes(b"foreign")
        return read(current)

    monkeypatch.setattr(watch_selection, "_regular_bytes", concurrent)
    with pytest.raises(PermissionError, match="changed before replacement"):
        watch_selection._replace_metadata(path, b"target", expected=b"prior")
    assert path.read_bytes() == b"foreign"
    assert list(tmp_path.iterdir()) == [path]
