"""Tests for the tracking_write mutation-verb wire plumbing
(agent-worktrees-authoritative-daemon effort, Phase 2).

Mirrors ``test_classify_daemon.py``/``test_worktree_status_daemon.py``'s own
structure and coverage style for the read-side daemons, applied to this
module's write-side additions: verb registration/dispatch, the
daemon-unreachable logged fallback (:func:`run_direct`), and the
never-coalesce-two-writes guarantee unique keys provide.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import textwrap
import threading
import time

import pytest

from agent_worktrees import tracking_write


def _endpoint_dict(server) -> dict:
    return tracking_write.rendezvous_fields(server)


@pytest.fixture(autouse=True)
def _clean_verb_registry():
    # Tests register throwaway verbs into the module-level registry, and a
    # few reach into the _VERB_MODULES/_verb_modules_loaded loader state --
    # isolate each test so one test's changes never leak into another's.
    before_verbs = dict(tracking_write._VERBS)
    before_versions = dict(tracking_write._VERB_VERSIONS)
    before_modules = tracking_write._VERB_MODULES
    before_loaded = tracking_write._verb_modules_loaded
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before_verbs)
    tracking_write._VERB_VERSIONS.clear()
    tracking_write._VERB_VERSIONS.update(before_versions)
    tracking_write._VERB_MODULES = before_modules
    tracking_write._verb_modules_loaded = before_loaded


def test_rendezvous_fields_are_namespaced_and_parseable():
    server = tracking_write.start_server(lambda kind, payload: {"ok": True})
    server.start()
    try:
        fields = _endpoint_dict(server)
        # `rendezvous_fields` loads the real `_VERB_MODULES` (including the
        # production `status_disposition_write` verb, registered at
        # version 2), so `tracking_write_verb_versions` is always present
        # here -- not test-registered, a real permanent system fact.
        assert set(fields) == {
            "tracking_write_transport",
            "tracking_write_endpoint",
            "tracking_write_token",
            "tracking_write_generation",
            "tracking_write_verbs",
            "tracking_write_verb_versions",
        }
        assert isinstance(fields["tracking_write_verbs"], list)
        assert isinstance(fields["tracking_write_verb_versions"], dict)
        endpoint = tracking_write.endpoint_from_rendezvous(fields)
        assert endpoint is not None
        host, port, token = endpoint
        assert host == "127.0.0.1"
        assert isinstance(port, int) and port > 0
        assert token == fields["tracking_write_token"]
    finally:
        server.close()


def test_endpoint_from_rendezvous_rejects_malformed_or_absent_data():
    assert tracking_write.endpoint_from_rendezvous(None) is None
    assert tracking_write.endpoint_from_rendezvous({}) is None
    assert tracking_write.endpoint_from_rendezvous(
        {"tracking_write_endpoint": "bad"}
    ) is None
    assert (
        tracking_write.endpoint_from_rendezvous(
            {"tracking_write_endpoint": "127.0.0.1:9", "tracking_write_token": ""}
        )
        is None
    )


@pytest.mark.parametrize("port", ["0", "-1", "65536", "999999"])
def test_endpoint_from_rendezvous_rejects_out_of_range_ports(port):
    assert tracking_write.endpoint_from_rendezvous(
        {
            "tracking_write_endpoint": f"127.0.0.1:{port}",
            "tracking_write_token": "tok",
        }
    ) is None


def test_endpoint_from_rendezvous_accepts_a_valid_port():
    endpoint = tracking_write.endpoint_from_rendezvous(
        {"tracking_write_endpoint": "127.0.0.1:65535", "tracking_write_token": "tok"}
    )
    assert endpoint == ("127.0.0.1", 65535, "tok")


def test_endpoint_from_rendezvous_rejects_endpoint_missing_the_verb():
    """(2026-09-27 PR #4159 review finding, copilot-extensions#3812) An
    endpoint whose own published capability list does not include the
    requested verb must never be dialed -- a resident daemon process whose
    ``_VERB_MODULES`` registration predates that verb (a rolling-upgrade
    version-skew window) would only report ``unsupported_verb`` for it."""
    fields = {
        "tracking_write_endpoint": "127.0.0.1:65535",
        "tracking_write_token": "tok",
        "tracking_write_verbs": ["claim_settle", "session_conclude"],
    }
    assert tracking_write.endpoint_from_rendezvous(
        fields, verb="session_register",
    ) is None
    assert tracking_write.endpoint_from_rendezvous(
        fields, verb="claim_settle",
    ) is not None


def test_endpoint_from_rendezvous_rejects_pre_capability_daemon_data():
    """A daemon process running code that predates the
    ``tracking_write_verbs`` field entirely (i.e. before this fix existed)
    publishes rendezvous data with no such key -- must be treated
    identically to "lacks the verb", not "no verb requested"."""
    old_shaped_fields = {
        "tracking_write_endpoint": "127.0.0.1:65535",
        "tracking_write_token": "tok",
    }
    assert tracking_write.endpoint_from_rendezvous(
        old_shaped_fields, verb="session_register",
    ) is None
    # Without a verb filter, old-shaped data is still a valid endpoint --
    # this check is opt-in per caller, not a blanket rejection.
    assert tracking_write.endpoint_from_rendezvous(old_shaped_fields) is not None


def test_endpoint_from_rendezvous_rejects_an_endpoint_below_min_version():
    """Reusing an existing verb NAME for an enriched payload shape is
    invisible to the by-name capability check above -- a pre-upgrade daemon
    process still recognizes the verb and would silently ignore (or
    mishandle) the new field. ``min_version`` closes that gap: an endpoint
    whose published version for this verb is too low (or missing, meaning
    version 1) is never dialed, exactly like an endpoint lacking the verb's
    name."""
    fields = {
        "tracking_write_endpoint": "127.0.0.1:65535",
        "tracking_write_token": "tok",
        "tracking_write_verbs": ["status_disposition_write"],
        "tracking_write_verb_versions": {"status_disposition_write": 2},
    }
    assert tracking_write.endpoint_from_rendezvous(
        fields, verb="status_disposition_write", min_version=2,
    ) is not None
    assert tracking_write.endpoint_from_rendezvous(
        fields, verb="status_disposition_write", min_version=3,
    ) is None
    # A daemon advertising the verb but no version entry at all (an older
    # process that predates versioning, or a verb that never bumped) means
    # version 1 -- rejected by any min_version above that.
    unversioned_fields = {
        "tracking_write_endpoint": "127.0.0.1:65535",
        "tracking_write_token": "tok",
        "tracking_write_verbs": ["status_disposition_write"],
    }
    assert tracking_write.endpoint_from_rendezvous(
        unversioned_fields, verb="status_disposition_write", min_version=2,
    ) is None
    # min_version=1 (the default) never rejects on version grounds.
    assert tracking_write.endpoint_from_rendezvous(
        unversioned_fields, verb="status_disposition_write",
    ) is not None


def test_rendezvous_fields_only_publishes_versions_above_one():
    """A verb registered at the default version (1) never appears in
    ``tracking_write_verb_versions`` -- only verbs that actually bumped
    past it are worth a client checking."""
    before = dict(tracking_write._VERBS)
    before_versions = dict(tracking_write._VERB_VERSIONS)
    try:
        server = tracking_write.start_server(lambda kind, payload: {"ok": True})
        server.start()
        try:
            tracking_write.register_verb("an_unversioned_verb", lambda args: {})
            tracking_write.register_verb("a_versioned_verb", lambda args: {}, version=2)
            fields = _endpoint_dict(server)
            assert "an_unversioned_verb" not in fields["tracking_write_verb_versions"]
            assert fields["tracking_write_verb_versions"]["a_versioned_verb"] == 2
        finally:
            server.close()
    finally:
        tracking_write._VERBS.clear()
        tracking_write._VERBS.update(before)
        tracking_write._VERB_VERSIONS.clear()
        tracking_write._VERB_VERSIONS.update(before_versions)


class TestVerbRegistryAndCompute:
    def test_register_verb_then_compute_dispatches_to_it(self):
        calls = []

        def _verb(args):
            calls.append(args)
            return {"echoed": args}

        tracking_write.register_verb("test-echo", _verb)
        assert "test-echo" in tracking_write.registered_verbs()

        result = tracking_write.compute(
            tracking_write.KIND, {"verb": "test-echo", "args": {"x": 1}}
        )
        assert result == {"echoed": {"x": 1}}
        assert calls == [{"x": 1}]

    def test_compute_defaults_missing_args_to_empty_dict(self):
        tracking_write.register_verb("test-no-args", lambda args: {"args": args})
        result = tracking_write.compute(
            tracking_write.KIND, {"verb": "test-no-args"}
        )
        assert result == {"args": {}}

    def test_compute_returns_unsupported_verb_marker_for_an_unregistered_verb(self):
        """Never raises (copilot-extensions#3812): a rolling-upgrade window
        where a resident daemon's own ``_VERB_MODULES`` registration
        predates a client's verb must be a safe, JSON-safe result
        ``write_with_boot`` can fall back on -- never an exception
        indistinguishable from "the request may have already run"."""
        result = tracking_write.compute(
            tracking_write.KIND, {"verb": "does-not-exist"}
        )
        assert result == {"unsupported_verb": True}

    def test_compute_rejects_missing_verb_name(self):
        with pytest.raises(ValueError, match="missing a verb name"):
            tracking_write.compute(tracking_write.KIND, {})

    def test_compute_rejects_non_dict_args(self):
        tracking_write.register_verb("test-bad-args", lambda args: args)
        with pytest.raises(ValueError, match="must be a dict"):
            tracking_write.compute(
                tracking_write.KIND, {"verb": "test-bad-args", "args": "nope"}
            )

    def test_compute_rejects_a_request_labeled_with_a_different_kind(self):
        """2026-09-26 PR review finding: without this guard, a request
        mislabeled with a sibling daemon's own kind (e.g. "classify") would
        still be dispatched as a mutation here."""
        tracking_write.register_verb("test-wrong-kind", lambda args: {"ok": True})
        with pytest.raises(ValueError, match="does not serve kind"):
            tracking_write.compute("classify", {"verb": "test-wrong-kind"})


class TestRunDirectFallback:
    def test_run_direct_calls_the_same_registered_function(self):
        calls = []

        def _record(args):
            calls.append(args)
            return {"ok": True}

        tracking_write.register_verb("test-direct", _record)
        result = tracking_write.run_direct("test-direct", {"y": 2}, reason="test")
        assert result == {"ok": True}
        assert calls == [{"y": 2}]

    def test_run_direct_logs_the_bypass(self, caplog):
        tracking_write.register_verb("test-logged", lambda args: {"ok": True})
        with caplog.at_level(logging.WARNING, logger="agent_worktrees.tracking_write"):
            tracking_write.run_direct(
                "test-logged", {}, reason="no resident daemon reachable"
            )
        assert any(
            "test-logged" in record.message and "no resident daemon reachable" in record.message
            for record in caplog.records
        )

    def test_run_direct_rejects_unregistered_verb(self):
        with pytest.raises(ValueError, match="unregistered verb"):
            tracking_write.run_direct("nope", {}, reason="test")


class TestDispatch:
    def test_dispatch_uses_live_daemon_when_reachable(self):
        tracking_write.register_verb("test-dispatch", lambda args: {"via": "verb", **args})
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        try:
            lock_data = _endpoint_dict(server)
            result = tracking_write.dispatch(
                "test-dispatch",
                {"n": 1},
                read_lock_data=lambda: lock_data,
                ensure_monitor=None,
            )
            assert result == {"via": "verb", "n": 1}
        finally:
            server.close()

    def test_dispatch_falls_back_to_run_direct_when_no_daemon_reachable(self, caplog):
        calls = []
        tracking_write.register_verb(
            "test-fallback", lambda args: calls.append(args) or {"via": "fallback"}
        )
        with caplog.at_level(logging.WARNING, logger="agent_worktrees.tracking_write"):
            result = tracking_write.dispatch(
                "test-fallback",
                {"n": 2},
                read_lock_data=lambda: None,
                ensure_monitor=None,
            )
        assert result == {"via": "fallback"}
        assert calls == [{"n": 2}]
        assert any("test-fallback" in r.message for r in caplog.records)

    def test_dispatch_raises_ambiguous_outcome_when_dialed_daemon_times_out(self):
        """2026-09-26 PR review finding: a request that reached a
        successfully-dialed daemon and then failed (deadline exceeded) must
        NOT silently retry via run_direct -- the daemon's own owner compute
        keeps running after the client gives up (CoalescingServer has no
        cancellation), so a blind retry could double-apply a non-idempotent
        write. Uses a real socket-level timeout (the verb blocks past the
        client's own recv timeout, request_deadline_s + 1.0s) rather than a
        deadline race that could still return the daemon's real answer.
        """
        calls = []
        release_gate = threading.Event()

        def _slow_verb(args):
            calls.append(dict(args))
            # Blocks past the client's own socket timeout
            # (request_deadline_s + 1.0s, see wcs_client._send_recv) so the
            # client-side read genuinely times out -- not a deadline-field
            # race that might still return a real answer.
            release_gate.wait(timeout=5)
            return {"via": "verb"}

        tracking_write.register_verb("test-ambiguous", _slow_verb)
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        try:
            lock_data = _endpoint_dict(server)
            with pytest.raises(tracking_write.AmbiguousWriteOutcome):
                tracking_write.dispatch(
                    "test-ambiguous",
                    {},
                    read_lock_data=lambda: lock_data,
                    ensure_monitor=None,
                    request_deadline_s=0.2,
                )
            # The daemon-side owner compute was genuinely invoked (this is
            # exactly the ambiguity this behavior protects against) -- it
            # just never got a chance to answer before the client's socket
            # timed out.
            assert len(calls) == 1
        finally:
            release_gate.set()
            server.close()

    def test_dispatch_still_falls_back_when_no_daemon_endpoint_found_at_all(self):
        """The one case that IS safe to auto-retry: no endpoint was ever
        discoverable, so nothing was ever sent anywhere."""
        calls = []
        tracking_write.register_verb(
            "test-predial-miss", lambda args: calls.append(args) or {"via": "fallback"}
        )
        result = tracking_write.dispatch(
            "test-predial-miss",
            {},
            read_lock_data=lambda: None,  # no daemon ever discoverable
            ensure_monitor=None,
        )
        assert result == {"via": "fallback"}
        assert calls == [{}]

    def test_dispatch_falls_back_when_endpoint_found_but_connect_refused(self):
        """The second safe case: a stale rendezvous entry (e.g. a since-
        exited daemon's lock file) resolves to a real-looking endpoint, but
        the TCP connect itself fails -- nothing was ever sent, so this must
        degrade exactly like a pre-dial miss, never raise
        AmbiguousWriteOutcome. A short request_deadline_s bounds this test:
        some sandboxed network stacks silently drop rather than actively
        refuse a connection to an unbound port, which would otherwise wait
        out the full connect timeout."""
        calls = []
        tracking_write.register_verb(
            "test-stale-endpoint", lambda args: calls.append(args) or {"via": "fallback"}
        )
        # Port 1 on loopback: reserved, nothing listens there.
        stale_lock_data = {
            "tracking_write_endpoint": "127.0.0.1:1",
            "tracking_write_token": "stale-token",
        }
        result = tracking_write.dispatch(
            "test-stale-endpoint",
            {},
            read_lock_data=lambda: stale_lock_data,
            ensure_monitor=None,
            request_deadline_s=0.3,
        )
        assert result == {"via": "fallback"}
        assert calls == [{}]

    def test_dispatch_never_dials_an_endpoint_lacking_the_verb(self):
        """The primary defense (2026-09-27 PR #4159 review finding,
        copilot-extensions#3812): an endpoint whose own published
        ``tracking_write_verbs`` capability list doesn't cover the
        requested verb is never even connected to -- proven here against a
        REAL, live ``CoalescingServer`` that actually DOES have the verb
        registered (via ``tracking_write.compute``), so if this test's
        ``dispatch`` call reached the server at all, it would succeed there
        too and this test could not distinguish the two paths by return
        value alone (both read the same process-wide ``_VERBS`` dict).
        Distinguishes them by THREAD instead: the real server's
        ``compute()`` always runs on a spawned handler thread, never the
        calling thread, so recording ``threading.get_ident()`` inside the
        verb proves ``run_direct`` (same-thread) ran, not the server."""
        calling_thread_id = threading.get_ident()
        observed_thread_ids = []
        tracking_write.register_verb(
            "test-known-client-side",
            lambda args: observed_thread_ids.append(threading.get_ident())
            or {"via": "ran"},
        )
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        try:
            real_lock_data = tracking_write.rendezvous_fields(server)
            # Simulate a stale/old daemon's rendezvous snapshot: the real
            # endpoint/token, but a capability list from BEFORE this verb
            # was added.
            stale_capability_lock_data = dict(
                real_lock_data, tracking_write_verbs=["some-other-verb"],
            )
            result = tracking_write.dispatch(
                "test-known-client-side",
                {},
                read_lock_data=lambda: stale_capability_lock_data,
                ensure_monitor=None,
            )
        finally:
            server.close()
        assert result == {"via": "ran"}
        assert observed_thread_ids == [calling_thread_id]

    def test_dispatch_never_dials_an_endpoint_below_min_version(self):
        """Same proof technique as the by-name test above, applied to
        ``min_version``: a REAL, live server that genuinely has the verb
        registered (so dispatch *could* succeed there) must still never be
        dialed when its advertised version for that verb is too low --
        proven by observing the calling thread ran the verb, not the
        server's own handler thread."""
        calling_thread_id = threading.get_ident()
        observed_thread_ids = []
        tracking_write.register_verb(
            "test-versioned-client-side",
            lambda args: observed_thread_ids.append(threading.get_ident())
            or {"via": "ran"},
            version=2,
        )
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        try:
            real_lock_data = tracking_write.rendezvous_fields(server)
            # Simulate a stale daemon still advertising version 1 for this
            # verb name (a rolling-upgrade skew window).
            stale_version_lock_data = dict(
                real_lock_data,
                tracking_write_verb_versions={"test-versioned-client-side": 1},
            )
            result = tracking_write.dispatch(
                "test-versioned-client-side",
                {},
                read_lock_data=lambda: stale_version_lock_data,
                ensure_monitor=None,
                min_version=2,
            )
        finally:
            server.close()
        assert result == {"via": "ran"}
        assert observed_thread_ids == [calling_thread_id]

    def test_dispatch_falls_back_when_a_live_daemon_reports_unsupported_verb(self):
        """The third safe case (copilot-extensions#3812): a connection DOES
        succeed and the daemon DOES respond, but reports
        ``unsupported_verb`` -- a rolling-upgrade window where the resident
        daemon process's own ``_VERB_MODULES`` registration predates this
        verb. Nothing ever executed server-side in that case either, so it
        must degrade to the identical in-process fallback rather than raise
        AmbiguousWriteOutcome for a request that never actually ran.
        Simulates the old-daemon side with a server whose own compute
        callback always reports ``unsupported_verb`` (exactly what an old
        process's real ``compute()`` would return for a verb it never
        heard of), while the CLIENT-side ``_VERBS`` registry (used by the
        in-process fallback) has the verb -- reproducing the asymmetry a
        genuine rolling upgrade produces between two separate processes."""
        calls = []
        tracking_write.register_verb(
            "test-known-to-client-only",
            lambda args: calls.append(args) or {"via": "fallback"},
        )
        def _old_daemon_compute(kind, payload):
            return {"unsupported_verb": True}

        server = tracking_write.start_server(_old_daemon_compute)
        server.start()
        try:
            lock_data = tracking_write.rendezvous_fields(server)
            result = tracking_write.dispatch(
                "test-known-to-client-only",
                {},
                read_lock_data=lambda: lock_data,
                ensure_monitor=None,
            )
        finally:
            server.close()
        assert result == {"via": "fallback"}
        assert calls == [{}]

    def test_two_concurrent_writes_never_coalesce_even_for_the_same_verb_and_args(self):
        """The write-specific guarantee this module adds over classify/
        worktree_status: a unique key per call means two concurrent writes
        are two independent executions, never merged into one answer."""
        calls = []
        call_lock = threading.Lock()
        gate = threading.Event()

        def _verb(args):
            with call_lock:
                calls.append(dict(args))
            gate.wait(timeout=2)
            return {"n": args.get("n")}

        tracking_write.register_verb("test-concurrent", _verb)
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        try:
            lock_data = _endpoint_dict(server)
            results = [None, None]

            def call(idx):
                results[idx] = tracking_write.dispatch(
                    "test-concurrent",
                    {"n": "same-args"},
                    read_lock_data=lambda: lock_data,
                    ensure_monitor=None,
                    request_deadline_s=3.0,
                )

            t1 = threading.Thread(target=call, args=(0,))
            t1.start()
            time.sleep(0.2)
            t2 = threading.Thread(target=call, args=(1,))
            t2.start()
            time.sleep(0.2)
            gate.set()
            t1.join(timeout=3)
            t2.join(timeout=3)

            # Both calls actually ran the verb -- neither joined the other's
            # in-flight execution the way a classify/worktree_status caller
            # sharing a key would have.
            assert len(calls) == 2
            assert results[0] == {"n": "same-args"}
            assert results[1] == {"n": "same-args"}
        finally:
            server.close()


class TestVerbModuleLoading:
    """2026-09-26 PR review finding: `_VERBS` alone is process-local, so a
    `register_verb` call made in one process (a CLI invocation) can never
    populate a *different* process's (the resident daemon's) dict. These
    tests cover the fix: `_VERB_MODULES` + `_ensure_verb_modules_loaded`,
    which both `compute` and `run_direct` call so every process arrives at
    the same registry independently via import, never shared mutable state.
    """

    def test_ensure_verb_modules_loaded_imports_every_listed_module_once(self, monkeypatch):
        import_calls = []
        real_import_module = tracking_write.importlib.import_module

        def _spy(name):
            import_calls.append(name)
            return real_import_module(name)

        monkeypatch.setattr(tracking_write.importlib, "import_module", _spy)
        # A real, fully-qualified, side-effect-free-to-import-twice module.
        monkeypatch.setattr(tracking_write, "_VERB_MODULES", ("agent_worktrees.config",))
        monkeypatch.setattr(tracking_write, "_verb_modules_loaded", False)

        tracking_write._ensure_verb_modules_loaded()
        tracking_write._ensure_verb_modules_loaded()  # second call: no-op

        assert import_calls == ["agent_worktrees.config"]

    def test_compute_and_run_direct_both_trigger_the_loader(self, monkeypatch):
        calls = {"n": 0}

        def _fake_loader():
            calls["n"] += 1

        monkeypatch.setattr(tracking_write, "_ensure_verb_modules_loaded", _fake_loader)
        tracking_write.register_verb("test-loader-compute", lambda args: {"ok": True})
        tracking_write.compute(tracking_write.KIND, {"verb": "test-loader-compute"})
        tracking_write.run_direct("test-loader-compute", {}, reason="test")

        assert calls["n"] == 2

    def test_verb_self_registered_by_a_genuinely_separate_process_is_reachable(
        self, tmp_path
    ):
        """The literal process-boundary proof the review asked for: a verb
        module is registered via the *production* `_ensure_verb_modules_loaded`
        loader (never a hand-rolled import bypassing it) and invoked by two
        independent `python` subprocesses (not this test process at all) --
        one exercising the `compute` path (the daemon side), one exercising
        `run_direct` (the fallback side) -- with no shared runtime state
        between them, only the identical `_VERB_MODULES` declaration and the
        verb module's own import-time `register_verb` call.
        """
        verb_module_dir = tmp_path / "verb_pkg"
        verb_module_dir.mkdir()
        (verb_module_dir / "boundary_test_verb.py").write_text(
            textwrap.dedent(
                """
                from agent_worktrees import tracking_write

                def _apply(args):
                    return {"ran_in_subprocess": True, "args": args}

                tracking_write.register_verb("boundary-test-verb", _apply)
                """
            ),
            encoding="utf-8",
        )

        script_template = textwrap.dedent(
            """
            import sys
            sys.path.insert(0, {verb_dir!r})
            from agent_worktrees import tracking_write

            # Only declare the module -- never import it directly here.
            # The production loader (_ensure_verb_modules_loaded, invoked
            # from inside compute/run_direct below) is what must perform
            # the actual import and trigger boundary_test_verb's own
            # register_verb call.
            tracking_write._VERB_MODULES = ("boundary_test_verb",)
            assert not tracking_write._verb_modules_loaded
            assert "boundary-test-verb" not in tracking_write.registered_verbs()

            result = tracking_write.{call}
            assert result == {{"ran_in_subprocess": True, "args": {{"probe": 1}}}}, result
            assert tracking_write._verb_modules_loaded
            print("OK")
            """
        )

        for call in (
            'compute(tracking_write.KIND, {"verb": "boundary-test-verb", "args": {"probe": 1}})',
            'run_direct("boundary-test-verb", {"probe": 1}, reason="subprocess test")',
        ):
            script = script_template.format(verb_dir=str(verb_module_dir), call=call)
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert proc.returncode == 0, proc.stdout + proc.stderr
            assert "OK" in proc.stdout


class TestInflightWriteTracking:
    """2026-09-26 PR review round 3: `subscriber_count()` alone is not a
    reliable in-flight signal for a write -- a client releases its own
    lease as soon as its own request call returns or times out, even while
    the daemon-side compute (this same process, a different thread) keeps
    running. `has_inflight_write()` tracks the compute itself.
    """

    def test_has_inflight_write_is_false_when_idle(self):
        assert tracking_write.has_inflight_write() is False

    def test_has_inflight_write_is_true_while_compute_runs(self):
        entered = threading.Event()
        release_gate = threading.Event()

        def _slow_verb(args):
            entered.set()
            release_gate.wait(timeout=5)
            return {"ok": True}

        tracking_write.register_verb("test-inflight", _slow_verb)
        t = threading.Thread(
            target=tracking_write.compute,
            args=(tracking_write.KIND, {"verb": "test-inflight"}),
        )
        t.start()
        try:
            assert entered.wait(timeout=2)
            assert tracking_write.has_inflight_write() is True
        finally:
            release_gate.set()
            t.join(timeout=3)
        assert tracking_write.has_inflight_write() is False

    def test_has_inflight_write_clears_even_if_the_verb_raises(self):
        def _boom(args):
            raise RuntimeError("verb failure")

        tracking_write.register_verb("test-inflight-error", _boom)
        with pytest.raises(RuntimeError):
            tracking_write.compute(tracking_write.KIND, {"verb": "test-inflight-error"})
        assert tracking_write.has_inflight_write() is False
