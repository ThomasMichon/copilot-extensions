"""Synthetic admission controls, independent of the efforts/provider packages."""

from copy import deepcopy
import json
import subprocess
import sys
from threading import Event

import pytest

from worktree_manager import agenda_transport
from worktree_manager.agenda_contract import (
    AgendaFeed, AgendaPublicationFence, AgendaUnavailable, MAX_INTEGER, validate_snapshot,
)


def publication(sequence=0, *, kind="snapshot", revision=1, generation="a" * 32):
    return {
        "type": kind, "ok": True, "contract_version": 1,
        "generation": generation, "sequence": sequence,
        "project": "example", "worktree_id": "selected", "session_id": "head-a",
        "availability": "observed",
        "observation": {
            "schema": "efforts.agenda-observation", "version": 1,
            "source": {"worktree": "selected", "session": "head-a", "store_id": "b" * 32},
            "revision": revision, "committed_at": "2026-11-01T07:00:00Z",
            "snapshot": {
                "worktree": "selected", "revision": revision, "status": "active",
                "root": "Finish the example",
                "stack": [], "next_action": f"Action {revision}",
                "exit_criteria": ["Reviewed result"], "declared_outcomes": {"done": "Reviewed result"},
                "queued": [], "parked": 0, "notes": [], "cursor_version": 2,
                "deferred": [], "due_rechecks": [], "next_revisit_at": None,
            },
        },
    }


def publication_v2(sequence=0, *, kind="snapshot", revision=1, generation="a" * 32):
    frame = publication(sequence, kind=kind, revision=revision, generation=generation)
    frame["contract_version"] = frame["observation"]["version"] = 2
    frame["observation"]["snapshot"].update(
        cursor_version=4, status="replanning",
        stack=["Original root", "Subqueue continuation"],
        queued=["Tail item"], notes=["Context reference: context-2"],
    )
    return frame


def test_v2_snapshot_supports_cursor_four_and_unchanged_legacy_observations():
    modern = publication_v2()
    validate_snapshot(modern, "example", "selected", "head-a", contract_version=2)
    assert AgendaFeed("example", "selected", "head-a", contract_version=2).receive(modern) == modern
    legacy = {**publication(), "contract_version": 2}
    assert AgendaFeed("example", "selected", "head-a", contract_version=2).receive(legacy) == legacy
    assert legacy["observation"]["version"] == 1
    with pytest.raises(ValueError):
        AgendaFeed("example", "selected", "head-a").receive(modern)


@pytest.mark.parametrize("change", [
    {"cursor_version": 1}, {"cursor_version": 2}, {"cursor_version": 3},
    {"cursor_version": True}, {"attempts": []}, {"status": []}, {"status": "unknown"},
    {"status": "steering"},
])
def test_v2_observation_version_cursor_and_fixed_fields_are_not_silently_downgraded(change):
    frame = publication_v2()
    frame["observation"]["snapshot"].update(change)
    with pytest.raises(ValueError):
        validate_snapshot(frame, "example", "selected", "head-a", contract_version=2)


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_steering_status_remains_supported(version):
    frame = publication()
    frame["contract_version"] = version
    frame["observation"]["snapshot"]["status"] = "steering"
    assert AgendaFeed(
        "example", "selected", "head-a", contract_version=version,
    ).receive(frame) == frame


@pytest.mark.parametrize("status", ["working", "waiting", "replanning", "complete"])
def test_cursor_four_status_matches_context_qualified_core(status):
    frame = publication_v2()
    frame["observation"]["snapshot"]["status"] = status
    assert AgendaFeed(
        "example", "selected", "head-a", contract_version=2,
    ).receive(frame) == frame


@pytest.mark.parametrize("version", [0, 3, True, "2"])
def test_unknown_manager_contract_selection_is_rejected(version):
    with pytest.raises(ValueError):
        AgendaFeed("example", "selected", "head-a", contract_version=version)
    with pytest.raises(AgendaUnavailable):
        agenda_transport.feed_argv(
            "example", "selected", "head-a", engine_command=[sys.executable, "-m", "agent_worktrees"],
            contract_version=version,
        )


def test_v2_null_updates_retain_store_revision_and_body_evidence():
    feed = AgendaFeed("example", "selected", "head-a", contract_version=2)
    first = publication_v2(5, revision=3)
    feed.receive(first)
    empty = {**publication_v2(6, kind="update"), "availability": "not-published", "observation": None}
    feed.receive(empty)
    with pytest.raises(ValueError):
        feed.receive(publication_v2(7, kind="update", revision=2))
    assert feed.fence.observed == first["observation"]
    assert feed.receive(publication_v2(7, kind="update", revision=3))["observation"] == first["observation"]


def test_negotiated_header_change_does_not_reset_or_weaken_publication_evidence():
    fence = AgendaPublicationFence()
    legacy = publication(5, revision=3)
    AgendaFeed("example", "selected", "head-a", fence=fence).receive(legacy)
    modern_header = {**legacy, "contract_version": 2}
    assert AgendaFeed(
        "example", "selected", "head-a", fence=fence, contract_version=2,
    ).receive(modern_header) == modern_header
    changed = publication_v2(6, revision=3)
    with pytest.raises(ValueError):
        AgendaFeed("example", "selected", "head-a", fence=fence, contract_version=2).receive(changed)
    advanced = publication_v2(6, revision=4)
    assert AgendaFeed(
        "example", "selected", "head-a", fence=fence, contract_version=2,
    ).receive(advanced) == advanced


@pytest.mark.parametrize("caps,expected", [
    (["worktree-agendas-v1"], 1),
    (["worktree-agendas-v1", "worktree-agendas-v2"], 2),
    (["worktree-agendas-v2"], 2),
    ([], None),
])
def test_live_health_selects_contract_before_flags_with_one_probe(monkeypatch, caps, expected):
    calls = []
    base = [sys.executable, "-m", "agent_worktrees"]

    def run(argv, timeout):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "ok": True, "health": {"capabilities": caps,
                                 "worktrees": {"status": "ready", "generation": "a" * 32}},
            "cli_contract_versions": [1, 2],
        }), "")

    monkeypatch.setattr(agenda_transport, "cancellable_read_runner", lambda *args, **kwargs: run)
    supported = []
    if expected is None:
        with pytest.raises(AgendaUnavailable) as error:
            agenda_transport.require_capability(
                base, cancelled=Event(), cwd="synthetic", on_supported=lambda: supported.append(True),
            )
        assert error.value.code == "unsupported" and not supported
    else:
        selected = agenda_transport.require_capability(
            base, cancelled=Event(), cwd="synthetic", on_supported=lambda: supported.append(True),
        )
        assert selected == expected and supported == [True]
        argv = agenda_transport.feed_argv(
            "example", "selected", "head-a", engine_command=base, contract_version=selected,
        )
        assert ("--contract-version" in argv) == (selected == 2)
        if selected == 2:
            assert argv[-2:] == ["--contract-version", "2"]
        else:
            assert argv[-2:] == ["head-a", "--json"]
    assert calls == [agenda_transport.health_argv(base)]


@pytest.mark.parametrize("cli_versions,expected", [([1], 1), ([1, 2], 2), ([3], None), (None, 1)])
def test_new_resident_does_not_make_old_cli_accept_new_flags(monkeypatch, cli_versions, expected):
    value = {"ok": True, "health": {
        "capabilities": ["worktree-agendas-v1", "worktree-agendas-v2"],
        "worktrees": {"status": "ready", "generation": "a" * 32},
    }}
    if cli_versions is not None:
        value["cli_contract_versions"] = cli_versions
    calls = []

    def run(argv, timeout):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(agenda_transport, "cancellable_read_runner", lambda *args, **kwargs: run)
    base = [sys.executable, "-m", "agent_worktrees"]
    if expected is None:
        with pytest.raises(AgendaUnavailable) as error:
            agenda_transport.require_capability(base, cancelled=Event(), cwd="synthetic")
        assert error.value.code == "unsupported"
    else:
        version = agenda_transport.require_capability(base, cancelled=Event(), cwd="synthetic")
        assert version == expected
        argv = agenda_transport.feed_argv(
            "example", "selected", "head-a", engine_command=base, contract_version=version,
        )
        assert ("--contract-version" in argv) == (expected == 2)
    assert len(calls) == 1


def test_atomic_snapshot_update_heartbeat_and_invalidation():
    feed = AgendaFeed("example", "selected", "head-a")
    first = publication()
    assert feed.receive(first) == first
    assert feed.receive({"type": "heartbeat", "generation": "a" * 32, "sequence": 0}) is None
    assert feed.receive(publication(3, kind="update", revision=2))["sequence"] == 3
    invalidation = {
        **publication(4, kind="update"), "availability": "head-changed", "observation": None,
    }
    assert feed.receive(invalidation)["observation"] is None
    assert feed.closed
    with pytest.raises(ValueError, match="post-terminal"):
        feed.receive(publication(5, kind="update", revision=3))


@pytest.mark.parametrize("change", [
    {"project": "wrong"}, {"worktree_id": "wrong"}, {"session_id": "wrong"},
    {"generation": "not-a-generation"}, {"sequence": True}, {"contract_version": True},
    {"availability": "invented"}, {"observation": None}, {"ok": 1},
    {"availability": []}, {"generation": []}, {"type": []},
])
def test_wrong_envelope_is_not_an_empty_agenda(change):
    with pytest.raises(ValueError):
        AgendaFeed("example", "selected", "head-a").receive({**publication(), **change})


@pytest.mark.parametrize("mutate", [
    lambda o: o["source"].update(worktree="other"),
    lambda o: o["source"].update(session="other"),
    lambda o: o["source"].update(store_id="not-a-uuid"),
    lambda o: o.update(revision=True),
    lambda o: o.update(version=True),
    lambda o: o.update(committed_at="2026-11-01T07:00:00"),
    lambda o: o["snapshot"].update(revision=2),
    lambda o: o["snapshot"].pop("next_action"),
    lambda o: o["snapshot"].update(queued={}),
    lambda o: o["snapshot"].update(next_action="x" * 4097),
    lambda o: o["snapshot"].update(next_action=float("nan")),
    lambda o: o["snapshot"].update(parked=[]),
    lambda o: o["snapshot"].update(declared_outcomes=[]),
    lambda o: o["snapshot"].update(root={}),
    lambda o: o["snapshot"].update(exit_criteria="not-a-list"),
    lambda o: o["snapshot"].update(cursor_version=3),
    lambda o: o["snapshot"].update(extra="not-core"),
])
def test_wrong_observation_visibly_fails(mutate):
    frame = publication()
    mutate(frame["observation"])
    with pytest.raises(ValueError):
        validate_snapshot(frame, "example", "selected", "head-a")


def test_depth_and_frame_bounds():
    frame = publication()
    nested = None
    for _ in range(17):
        nested = [nested]
    frame["observation"]["snapshot"]["notes"] = nested
    with pytest.raises(ValueError, match="nesting"):
        validate_snapshot(frame, "example", "selected", "head-a")
    frame = publication()
    frame["observation"]["snapshot"]["notes"] = ["x" * 4000] * 9
    with pytest.raises(ValueError, match="frame"):
        validate_snapshot(frame, "example", "selected", "head-a")


@pytest.mark.parametrize("later", [
    publication(1, kind="snapshot", revision=2),
    publication(0, kind="update", revision=2),
    publication(1, kind="update", revision=2, generation="c" * 32),
    publication(1, kind="update"),
])
def test_repeat_or_stale_generation_is_rejected(later):
    feed = AgendaFeed("example", "selected", "head-a")
    feed.receive(publication())
    with pytest.raises(ValueError):
        feed.receive(deepcopy(later))


def test_new_connection_requires_snapshot_and_control_is_generation_fenced():
    feed = AgendaFeed("example", "selected", "head-a")
    with pytest.raises(ValueError, match="atomic snapshot"):
        feed.receive(publication(1, kind="update", revision=2))
    feed.receive(publication())
    with pytest.raises(ValueError, match="generation"):
        feed.receive({"type": "heartbeat", "generation": "c" * 32, "sequence": 0})
    with pytest.raises(AgendaUnavailable, match="going away") as error:
        feed.receive({"type": "going-away", "generation": "a" * 32, "sequence": 0, "reason": "cutover"})
    assert error.value.retry


def test_unsupported_error_is_terminal():
    feed = AgendaFeed("example", "selected", "head-a")
    with pytest.raises(AgendaUnavailable, match="incompatible") as error:
        feed.receive({
            "type": "error", "ok": False, "contract_version": 1,
            "error": {"code": "incompatible", "message": "worktree-agendas-v1 is unavailable"},
        })
    assert not error.value.retry


@pytest.mark.parametrize("count", [129, MAX_INTEGER])
def test_parked_scalar_count_is_not_limited_by_collection_size(count):
    frame = publication()
    frame["observation"]["snapshot"]["parked"] = count
    original = deepcopy(frame)
    assert AgendaFeed("example", "selected", "head-a").receive(frame) == original
    assert frame == original


@pytest.mark.parametrize("count", [-1, True, MAX_INTEGER + 1])
def test_invalid_parked_scalar_count_is_rejected(count):
    frame = publication()
    frame["observation"]["snapshot"]["parked"] = count
    with pytest.raises(ValueError):
        validate_snapshot(frame, "example", "selected", "head-a")


def test_revisit_offset_is_preserved_while_generated_commit_requires_utc():
    frame = publication()
    revisit = "2026-11-01T09:00:00-07:00"
    frame["observation"]["snapshot"]["next_revisit_at"] = revisit
    validate_snapshot(frame, "example", "selected", "head-a")
    assert frame["observation"]["snapshot"]["next_revisit_at"] == revisit
    frame["observation"]["committed_at"] = revisit
    with pytest.raises(ValueError):
        validate_snapshot(frame, "example", "selected", "head-a")


@pytest.mark.parametrize("code,retry", [
    ("disconnected", True), ("unavailable", True), ("unready", True),
    ("draining", True), ("superseded", True),
    ("unsupported", False), ("invalid-response", False), ("invalid-request", False),
    ("authentication", False), ("closed", False),
])
def test_structured_feed_errors_preserve_classified_retry_policy(code, retry):
    feed = AgendaFeed("example", "selected", "head-a")
    feed.receive(publication())
    with pytest.raises(AgendaUnavailable) as failure:
        feed.receive({
            "type": "error", "ok": False, "contract_version": 1,
            "error": {"code": code, "message": "provider-reported failure"},
        })
    assert failure.value.retry is retry


@pytest.mark.parametrize("reason,retired", [
    ("draining", None), ("unready", None), ("unavailable", None), ("disconnected", None),
    ("superseded", "a" * 32), ("closed", "a" * 32), ("shutdown", "a" * 32),
])
def test_temporary_drain_does_not_permanently_retire_generation(reason, retired):
    feed = AgendaFeed("example", "selected", "head-a")
    feed.receive(publication())
    with pytest.raises(AgendaUnavailable):
        feed.receive({
            "type": "going-away", "generation": "a" * 32, "sequence": 0, "reason": reason,
        })
    assert feed.closed and feed.retired_generation == retired


def test_publication_fence_keeps_observed_revision_across_null_updates():
    feed = AgendaFeed("example", "selected", "head-a")
    first = publication(5, revision=3)
    feed.receive(first)
    empty = {**publication(6, kind="update"), "availability": "not-published", "observation": None}
    feed.receive(empty)
    with pytest.raises(ValueError, match="store revision"):
        feed.receive(publication(7, kind="update", revision=2))
    assert feed.current == empty
    assert feed.fence.observed == first["observation"]
    restored = publication(7, kind="update", revision=3)
    assert feed.receive(restored) == restored


@pytest.mark.parametrize("change", ["revision", "body", "status", "availability"])
def test_publication_fence_refuses_equal_sequence_changes_before_updating_evidence(change):
    fence = AgendaPublicationFence()
    first = publication(5, revision=3)
    AgendaFeed("example", "selected", "head-a", fence=fence).receive(first)
    changed = deepcopy(first)
    if change == "revision":
        changed["observation"]["revision"] = changed["observation"]["snapshot"]["revision"] = 4
    elif change == "body":
        changed["observation"]["snapshot"]["next_action"] = "Changed action"
    elif change == "status":
        changed["observation"]["snapshot"]["status"] = "steering"
    else:
        changed.update(availability="not-published", observation=None)
    with pytest.raises(ValueError, match="without advancing"):
        AgendaFeed("example", "selected", "head-a", fence=fence).receive(changed)
    assert fence.last == first and fence.observed == first["observation"]


def test_publication_fence_is_independent_of_mutable_display_data():
    fence = AgendaPublicationFence()
    first = publication(5, revision=3)
    original = deepcopy(first)
    AgendaFeed("example", "selected", "head-a", fence=fence).receive(first)
    first["observation"]["revision"] = first["observation"]["snapshot"]["revision"] = 1
    first.update(availability="not-published", observation=None)
    assert fence.last == original and fence.observed == original["observation"]
    with pytest.raises(ValueError, match="store revision"):
        AgendaFeed("example", "selected", "head-a", fence=fence).receive(
            publication(6, revision=2, generation="c" * 32)
        )


def test_publication_fence_refuses_store_replacement_for_the_same_selected_head():
    fence = AgendaPublicationFence()
    first = publication(5, revision=3)
    AgendaFeed("example", "selected", "head-a", fence=fence).receive(first)
    changed = publication(6, revision=4, generation="c" * 32)
    changed["observation"]["source"]["store_id"] = "d" * 32
    with pytest.raises(ValueError, match="source store changed"):
        AgendaFeed("example", "selected", "head-a", fence=fence).receive(changed)
    assert fence.observed == first["observation"]


@pytest.mark.parametrize("module", ["agent_worktrees", "agent_worktrees.__main__"])
def test_executable_agenda_module_whitelist_preserves_the_resolved_argv(module):
    base = [sys.executable, "-B", "-m", module]
    assert agenda_transport.health_argv(base) == [
        sys.executable, "-B", "-I", "-c", agenda_transport._HEALTH_PROBE,
    ]
    assert agenda_transport.feed_argv("example", "selected", "head-a", engine_command=base) == [
        *base, "agenda-feed", "--project", "example", "--worktree", "selected",
        "--session", "head-a", "--json",
    ]


@pytest.mark.parametrize("suffix", [
    ["-m", "agent_worktrees.current_roster_entry"],
    ["-m", "agent_worktrees.agenda_cli"],
    ["-m", "unknown_provider"],
    ["-m", "agent_worktrees.current_roster_entry", "--project", "example"],
])
def test_nonexecutable_agenda_modules_are_terminal_before_probe_or_feed(suffix):
    base = [sys.executable, *suffix]
    for build in (
        lambda: agenda_transport.health_argv(base),
        lambda: agenda_transport.feed_argv("example", "selected", "head-a", engine_command=base),
    ):
        with pytest.raises(AgendaUnavailable, match="unsupported owned agenda module entry") as error:
            build()
        assert error.value.code == "unsupported-entry" and not error.value.retry
