"""Tests for the ``claim_add``/``claim_release``/``claim_settle`` verbs
(agent-worktrees-authoritative-daemon effort, Phase 3's third migrated
call-site cluster).

Covers the verb functions directly (guards, the reopen-on-add path, the
not-found/reserved rejections) and one end-to-end proof that
``tracking_write.dispatch`` reaches the exact same functions via a real,
live daemon.
"""

from __future__ import annotations

import json
import runpy
import socket
from argparse import Namespace
from pathlib import Path
from typing import Any, Callable, Iterator, NoReturn

from machine_transport import detect_platform

import pytest

from agent_worktrees import config as cfg, obligations, tracking, tracking_claim_write, tracking_write
from agent_worktrees import tracking_lifecycle


@pytest.fixture(autouse=True)
def _clean_verb_registry() -> Iterator[None]:
    before_verbs = dict(tracking_write._VERBS)
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before_verbs)


@pytest.fixture
def record_path(tmp_tracking_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    project_dir = tmp_tracking_dir.parent / ".example"
    monkeypatch.setattr(
        cfg, "project_dir", lambda name=None: tmp_tracking_dir.parent / f".{name or 'example'}",
    )
    project_dir.mkdir(parents=True, exist_ok=True)
    records = project_dir / "worktrees"
    records.mkdir()
    anchor, worktrees = project_dir / "anchor", project_dir / "checkouts"
    anchor.mkdir()
    monkeypatch.setattr(socket, "gethostname", lambda: "machine")
    monkeypatch.setattr(cfg, "tracking_dir", lambda *args, **kwargs: records)
    path = records / "wt-claim.yaml"
    (project_dir / "config.yaml").write_text(json.dumps({
        "repo_name": "example", "machine": "machine", "platform": detect_platform(),
        "repos": {"example": {"anchor": str(anchor), "worktree_root": str(worktrees)}},
    }), encoding="utf-8")
    tracking_lifecycle.create_new_record(
        "wt-claim", "worktree/wt-claim", str(worktrees / "wt-claim"), "example",
        "machine", detect_platform(), records,
    )
    return path


def test_importing_the_module_registers_all_three_verbs() -> None:
    verbs = tracking_write.registered_verbs()
    assert "claim_add" in verbs
    assert "claim_release" in verbs
    assert "claim_settle" in verbs
    assert {
        verb: tracking_write._VERB_VERSIONS[verb]
        for verb in ("claim_add", "claim_release", "claim_settle")
    } == {"claim_add": 5, "claim_release": 5, "claim_settle": 5}


@pytest.mark.parametrize("verb", ["add", "release", "settle"])
def test_busy_claim_record_is_a_known_non_mutating_rejection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, verb: str) -> None:
    class BusyLock:
        def __enter__(self) -> NoReturn:
            raise TimeoutError("fixture-owned reclamation is in progress")

        def __exit__(self, *args: Any) -> None:
            pytest.fail("an unentered record lock must not be released")

    monkeypatch.setattr(tracking, "_RecordLock", lambda *_args, **_kwargs: BusyLock())
    monkeypatch.setattr(
        tracking, "load_record", lambda *_args, **_kwargs: pytest.fail("no source read or mutation after contention"),
    )
    result = getattr(tracking_claim_write, f"apply_claim_{verb}")({
        "worktree_id": "owned", "yaml_path": str(tmp_path / "owned.yaml"),
        "kind": "codespace", "ref": "resource", "disposition": obligations.AT_REST,
    })
    assert result["error"] == "rejected"
    assert "no claim mutation" in result["message"]


@pytest.mark.parametrize("verb", ["release", "settle"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("caller", ["current", "prior-v4"])
def test_cli_contended_claim_never_reports_success(
    record_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str], claim_transport: Callable[[str, dict[str, Any]], dict[str, Any]], verb: str, json_mode: bool, caller: str,
) -> None:
    from agent_worktrees import claims_cli

    ref = "owned-resource"
    assert tracking_claim_write.apply_claim_add({
        "worktree_id": "wt-claim", "yaml_path": str(record_path), "kind": "codespace", "ref": ref,
    })["ok"]
    before = record_path.read_bytes()
    config = cfg.load_project_config("example", include_control_plane_related_pr=False)
    monkeypatch.setattr(cfg, "load_config", lambda *args, **kwargs: config)
    monkeypatch.setattr(
        claims_cli, "_dispatch_claim",
        claim_transport,
    )

    class BusyLock:
        def __enter__(self) -> NoReturn:
            raise TimeoutError("fixture-owned reclamation is in progress")

        def __exit__(self, *args: Any) -> None:
            pytest.fail("unentered lock must not be released")

    monkeypatch.setattr(tracking, "_RecordLock", lambda *_args, **_kwargs: BusyLock())
    args = Namespace(
        json=json_mode, release_worktree="wt-claim", claim_owner_ref=None,
        remove=False, released=False,
    )
    handler = getattr(claims_cli, f"_claims_{verb}")
    if caller == "prior-v4":
        prior = runpy.run_path(
            str(Path(__file__).parent / "fixtures" / "claims_cli_v4.py"),
        )
        handler = prior[f"_claims_{verb}"]
    assert handler(args, ref) == 1
    captured = capfd.readouterr()
    if json_mode:
        report = json.loads(captured.out)
        assert "busy" in report["error"]
        assert "action" not in report and "disposition" not in report
    else:
        assert "busy" in captured.out + captured.err
        assert "outbound claim" not in captured.out + captured.err
    assert record_path.read_bytes() == before


@pytest.fixture
def claim_transport() -> Iterator[Callable[[str, dict[str, Any]], dict[str, Any]]]:
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        lock_data = tracking_write.rendezvous_fields(server)
        yield lambda verb, args: tracking_write.dispatch(
            verb, args, read_lock_data=lambda: lock_data, ensure_monitor=None,
        )
    finally:
        server.close()


@pytest.mark.parametrize("verb,old_version", [
    (verb, version)
    for verb in ("claim_add", "claim_release", "claim_settle")
    for version in (1, 2, 3, 4)
])
def test_claim_cli_never_dials_pre_paired_claim_daemon(verb: str, old_version: int, record_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_worktrees import claims_cli, locks, status_monitor_runtime

    old = {
        "tracking_write_endpoint": "127.0.0.1:49152",
        "tracking_write_token": "fixture-token",
        "tracking_write_verbs": [verb],
        "tracking_write_verb_versions": {verb: old_version},
    }
    assert tracking_write.endpoint_from_rendezvous(old, verb=verb) is not None
    monkeypatch.setattr(locks, "read_lock", lambda *args: old)
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: False)
    monkeypatch.setattr(
        tracking_write, "_send_tracking_write_request",
        lambda *args, **kwargs: pytest.fail("claim mutation dialed an old authority-unaware daemon"),
    )
    args = {
        "worktree_id": "wt-claim", "yaml_path": str(record_path),
        "kind": "pr", "ref": "owner/example#1", "disposition": "at-rest",
    }
    retained_nonce = ""
    if verb == "claim_release" and old_version == 4:
        bound = tracking_claim_write.apply_claim_add({
            **args, "kind": "task", "ref": "retained-task",
        })
        retained_nonce = bound["claim_binding"]["claim_nonce"]
    if verb != "claim_add":
        assert tracking_claim_write.apply_claim_add(args)["ok"]
    assert claims_cli._dispatch_claim(verb, args)["ok"]
    if retained_nonce:
        retained = next(
            claim for claim in tracking.load_record(record_path).resources
            if claim.ref == "retained-task"
        )
        assert retained.state == "active" and retained.claim_nonce == retained_nonce


def test_add_journals_an_active_claim(record_path: Path) -> None:
    result = tracking_claim_write.apply_claim_add(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "kind": "codespace",
            "ref": "cs-1",
        }
    )
    assert result["ok"] is True
    assert result["state"] == obligations.ACTIVE
    assert result["reopened"] is False
    record = tracking.load_record(record_path)
    assert len(record.resources) == 1
    assert record.resources[0].ref == "cs-1"


def test_add_rejects_a_frozen_owner(record_path: Path) -> None:
    record = tracking.load_record(record_path)
    record.status = "finalizing"
    tracking.save_record(record, record_path)

    result = tracking_claim_write.apply_claim_add(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "kind": "codespace",
            "ref": "cs-1",
        }
    )
    assert result["error"] == "frozen"
    assert tracking.load_record(record_path).resources == []


def test_add_rejects_a_completed_managed_record(record_path: Path) -> None:
    """2026-09-27 PR review finding: `add_resource_claim` itself also
    rejects a `system`/`bridge` record in a `complete`/`completed` state --
    this must surface as a deterministic `{"error": ...}` result here, never
    an uncaught `ValueError` a resident daemon's own `CoalescingServer`
    would otherwise swallow (turning it into an `AmbiguousWriteOutcome`)."""
    record = tracking.load_record(record_path)
    record.kind = "system"
    record.status = "complete"
    tracking.save_record(record, record_path)

    result = tracking_claim_write.apply_claim_add(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "kind": "codespace",
            "ref": "cs-1",
        }
    )
    assert result["error"] == "rejected"
    assert "frozen" in result["message"]
    assert tracking.load_record(record_path).resources == []


def test_add_reopens_a_finalized_owner(record_path: Path) -> None:
    record = tracking.load_record(record_path)
    record.status = "finalized"
    tracking.save_record(record, record_path)

    result = tracking_claim_write.apply_claim_add(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "kind": "codespace",
            "ref": "cs-1",
        }
    )
    assert result["reopened"] is True
    assert tracking.load_record(record_path).status == "active"


def test_release_marks_a_claim_released(record_path: Path) -> None:
    tracking_claim_write.apply_claim_add(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "kind": "codespace",
            "ref": "cs-1",
        }
    )
    result = tracking_claim_write.apply_claim_release(
        {"worktree_id": "wt-claim", "yaml_path": str(record_path), "ref": "cs-1"}
    )
    assert result == {"ok": True, "action": "released"}
    record = tracking.load_record(record_path)
    assert record.resources[0].state == "released"


def test_release_with_remove_deletes_the_claim(record_path: Path) -> None:
    tracking_claim_write.apply_claim_add(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "kind": "codespace",
            "ref": "cs-1",
        }
    )
    result = tracking_claim_write.apply_claim_release(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "ref": "cs-1",
            "remove": True,
        }
    )
    assert result == {"ok": True, "action": "removed"}
    assert tracking.load_record(record_path).resources == []


def test_release_rejects_an_unknown_ref(record_path: Path) -> None:
    result = tracking_claim_write.apply_claim_release(
        {"worktree_id": "wt-claim", "yaml_path": str(record_path), "ref": "cs-nope"}
    )
    assert result == {"error": "not_found"}


def test_settle_marks_a_claim_at_rest(record_path: Path) -> None:
    tracking_claim_write.apply_claim_add(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "kind": "codespace",
            "ref": "cs-1",
        }
    )
    result = tracking_claim_write.apply_claim_settle(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "ref": "cs-1",
            "disposition": obligations.AT_REST,
        }
    )
    assert result == {"ok": True, "kind": "codespace", "disposition": obligations.AT_REST}


def test_settle_rejects_an_unknown_ref(record_path: Path) -> None:
    result = tracking_claim_write.apply_claim_settle(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "ref": "cs-nope",
            "disposition": obligations.AT_REST,
        }
    )
    assert result == {"error": "not_found"}


def test_settle_skip_if_released_no_ops_an_already_released_claim(record_path: Path) -> None:
    """The ``skip_if_released`` guard (added for ``handoff_cutover.py``'s
    ``_settle_predecessor_session_claim`` repair) must never resurrect an
    already-``released`` claim -- mirrors ``finalize.py``'s
    ``_settle_current_session_claim`` guard, now shared through this verb
    instead of duplicated at the call site."""
    tracking_claim_write.apply_claim_add(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "kind": "codespace",
            "ref": "cs-1",
        }
    )
    tracking_claim_write.apply_claim_release(
        {"worktree_id": "wt-claim", "yaml_path": str(record_path), "ref": "cs-1"}
    )
    result = tracking_claim_write.apply_claim_settle(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "ref": "cs-1",
            "disposition": obligations.AT_REST,
            "skip_if_released": True,
        }
    )
    assert result == {"ok": True, "skipped": "released"}
    record = tracking.load_record(record_path)
    match = next(c for c in record.resources if c.ref == "cs-1")
    assert match.state == "released"


def test_settle_without_skip_if_released_still_resettles_a_released_claim(record_path: Path) -> None:
    """Confirms the guard is opt-in only: the public ``claims settle`` CLI
    command's existing behavior (unconditional resettle, no guard arg) is
    unchanged."""
    tracking_claim_write.apply_claim_add(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "kind": "codespace",
            "ref": "cs-1",
        }
    )
    tracking_claim_write.apply_claim_release(
        {"worktree_id": "wt-claim", "yaml_path": str(record_path), "ref": "cs-1"}
    )
    result = tracking_claim_write.apply_claim_settle(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "ref": "cs-1",
            "disposition": obligations.AT_REST,
        }
    )
    assert result == {"ok": True, "kind": "codespace", "disposition": obligations.AT_REST}
    record = tracking.load_record(record_path)
    match = next(c for c in record.resources if c.ref == "cs-1")
    assert match.state == obligations.AT_REST


def test_settle_skip_if_released_wins_over_a_stale_reservation(record_path: Path) -> None:
    """The ``skip_if_released`` no-op must run BEFORE the reservation check
    (2026-09-27 PR #3911 review finding): a released claim that still
    carries a stale ``handoff_bundle`` reservation must still silently
    no-op, exactly like the old inline repair (which never even reached a
    reservation check once a claim was already ``released``) -- not
    surface as ``{"error": "reserved"}``."""
    tracking_claim_write.apply_claim_add(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "kind": "codespace",
            "ref": "cs-1",
        }
    )
    tracking_claim_write.apply_claim_release(
        {"worktree_id": "wt-claim", "yaml_path": str(record_path), "ref": "cs-1"}
    )
    record = tracking.load_record(record_path)
    match = next(c for c in record.resources if c.ref == "cs-1")
    match.handoff_bundle = "stale-bundle"
    tracking.save_record(record, record_path)

    result = tracking_claim_write.apply_claim_settle(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "ref": "cs-1",
            "disposition": obligations.AT_REST,
            "skip_if_released": True,
        }
    )
    assert result == {"ok": True, "skipped": "released"}


def test_settle_always_hard_requires_the_sidecar_lock(record_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``claim_settle`` always hard-requires the sidecar, for both the
    outer ``_RecordLock`` and the nested ``save_record`` call -- including
    for the ``skip_if_released`` repair caller (2026-09-27 PR #3911 review
    findings): the pre-migration inline transaction's own OUTER
    ``_RecordLock`` degraded on contention, but its final
    ``save_record`` call already hard-required the sidecar internally
    (``save_record`` never accepted a softer policy), so on real
    contention the old code's net effect was ALWAYS to fail the write
    and let this repair's own best-effort
    ``contextlib.suppress(Exception)`` swallow it -- never to persist a
    stale in-memory snapshot without cross-process exclusion. Matching
    that here avoids silently resurrecting an already-``released`` claim
    if a concurrent ``deregister_session`` released it mid-transaction."""
    calls = []
    real_lock = tracking._RecordLock

    def _spy(path: Path, **kwargs: Any) -> tracking._RecordLock:
        calls.append(kwargs)
        return real_lock(path, **kwargs)

    monkeypatch.setattr(tracking, "_RecordLock", _spy)
    tracking_claim_write.apply_claim_add(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "kind": "codespace",
            "ref": "cs-1",
        }
    )
    calls.clear()
    tracking_claim_write.apply_claim_settle(
        {
            "worktree_id": "wt-claim",
            "yaml_path": str(record_path),
            "ref": "cs-1",
            "disposition": obligations.AT_REST,
            "skip_if_released": True,
        }
    )
    assert calls == [{"require_sidecar": True}, {"require_sidecar": True}]


def test_dispatch_reaches_claim_add_through_a_live_daemon(record_path: Path) -> None:
    """End-to-end: proves ``tracking_write.dispatch`` reaches
    ``apply_claim_add`` via an actual ``CoalescingServer``, the
    two-process-shaped path real production traffic will use."""
    server = tracking_write.start_server(tracking_write.compute)
    server.start()
    try:
        lock_data = tracking_write.rendezvous_fields(server)
        result = tracking_write.dispatch(
            "claim_add",
            {
                "worktree_id": "wt-claim",
                "yaml_path": str(record_path),
                "kind": "codespace",
                "ref": "cs-via-daemon",
            },
            read_lock_data=lambda: lock_data,
            ensure_monitor=None,
        )
    finally:
        server.close()
    assert result["ok"] is True
    record = tracking.load_record(record_path)
    assert record.resources[0].ref == "cs-via-daemon"
