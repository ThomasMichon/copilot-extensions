"""Regression tests: ``_load_local_two_phase``'s generation guard.

Mirrors ``#2347``'s fix to the sibling ``_load_remote_stream`` in the
plugins/agent-worktrees copy of this module: a stale-generation Phase 1
call (superseded by a newer ``reload()``/``reload_source()`` while its own
fetch was still in flight) must never clobber a fresher generation's
already-committed state/records -- neither on failure nor on success.

``_load_local_two_phase`` is the in-process seam reached by a local
``Source`` with no ``argv`` (synthetic/older-fixture sources); the
production local source streams through ``_load_remote_stream`` instead,
which already carries this guard on every write.
"""
from __future__ import annotations

from worktree_manager.production_picker.picker_tui import data_ssh


def test_stale_generation_local_phase1_failure_does_not_clobber_fresher_ready_state(
    monkeypatch,
):
    source = data_ssh.Source("host", "Win", None, local=True)
    loader = data_ssh.LiveLoader([source])
    loader._gen[source.cache_key] = 0  # this call's own (soon-to-be-stale) gen

    def _fetch_then_supersede(*_a, **_k):
        # Simulate a newer reload() superseding this call's generation WHILE
        # its own fetch is still in flight, then this (stale) call fails.
        loader._gen[source.cache_key] = 1
        loader._state[source.cache_key] = "ready"
        loader._records[source.cache_key] = [{"id4": "real-content"}]
        raise RuntimeError("boom")

    monkeypatch.setattr(data_ssh, "_fetch", _fetch_then_supersede)

    loader._load_local_two_phase(source)

    # The stale failure must not have clobbered the newer, real success.
    assert loader.state_for_source(source.cache_key) == "ready"
    assert loader._records[source.cache_key] == [{"id4": "real-content"}]


def test_stale_generation_local_phase1_success_does_not_clobber_fresher_state(
    monkeypatch,
):
    source = data_ssh.Source("host", "Win", None, local=True)
    loader = data_ssh.LiveLoader([source])
    loader._gen[source.cache_key] = 0  # this call's own (soon-to-be-stale) gen

    def _fetch_then_supersede(*_a, **_k):
        # Simulate a newer reload() superseding this call's generation WHILE
        # its own fetch is still in flight, then this (stale) call succeeds
        # with now-outdated content.
        loader._gen[source.cache_key] = 1
        loader._state[source.cache_key] = "ready"
        loader._records[source.cache_key] = [{"id4": "real-content"}]
        return [{"id4": "stale-content"}]

    monkeypatch.setattr(data_ssh, "_fetch", _fetch_then_supersede)

    loader._load_local_two_phase(source)

    assert loader.state_for_source(source.cache_key) == "ready"
    assert loader._records[source.cache_key] == [{"id4": "real-content"}]


def test_current_generation_local_phase1_failure_is_recorded(monkeypatch):
    """Sanity counterpart: a genuine (non-superseded) Phase 1 failure still
    sets the failed state -- the generation guard must not swallow real
    failures."""
    source = data_ssh.Source("host", "Win", None, local=True)
    loader = data_ssh.LiveLoader([source])
    monkeypatch.setattr(
        data_ssh, "_fetch",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    loader._load_local_two_phase(source)

    assert loader.state_for_source(source.cache_key) == "failed"


def test_local_classify_failure_preserves_mux_rows_and_reports_error(monkeypatch):
    source = data_ssh.Source("host", "Win", None, local=True)
    loader = data_ssh.LiveLoader([source])
    mux_rows = [{"id4": "example", "sess": "MUX(2)", "mux_live": True}]

    def fetch(*_args, classify=True, **_kwargs):
        if classify:
            raise RuntimeError("classification timed out")
        return mux_rows

    failures = []
    monkeypatch.setattr(data_ssh, "_fetch", fetch)
    monkeypatch.setattr(loader, "_log_fetch_failure",
                        lambda *_args, **kwargs: failures.append(kwargs["phase"]))
    loader._load_local_two_phase(source)

    assert loader.records_for_source(source.cache_key) == mux_rows
    assert loader.state_for_source(source.cache_key) == "ready"
    assert loader._error[source.cache_key] == "classification timed out"
    assert failures == ["local-classify"]


def test_remote_cache_first_retains_mux_details_when_classification_fails(monkeypatch):
    source = data_ssh.Source(
        "remote", "Linux",
        ["ssh", "remote", "example list --json --classify --mux-details"],
    )
    loader = data_ssh.LiveLoader([source])
    monkeypatch.setattr(data_ssh, "_CACHE_ONLY_GATE", True)
    monkeypatch.setattr(data_ssh, "_ssh_log", lambda *_args: None)
    calls = []
    mux_rows = [{"id4": "example", "sess": "MUX(0)", "mux_live": True}]

    def fetch(_source, *, argv=None, **_kwargs):
        calls.append(argv)
        if argv is None:
            raise RuntimeError("classification timed out")
        command = " ".join(argv)
        assert "--cache-only" in command
        assert "--mux-details" in command
        assert "--classify" not in command
        return mux_rows

    monkeypatch.setattr(data_ssh, "_fetch", fetch)
    loader._load_remote_two_phase(source)

    assert len(calls) == 2
    assert loader.records_for_source(source.cache_key) == mux_rows
    assert loader.state_for_source(source.cache_key) == "ready"
