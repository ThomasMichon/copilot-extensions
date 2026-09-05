"""Tests for agent_worktrees.tracking â YAML CRUD and session registry."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_worktrees.effort_focus import ActiveEffort
from agent_worktrees.tracking import (
    ClaimRef,
    ControllerRelation,
    ResourceClaim,
    SessionEntry,
    WorktreeRecord,
    _atomic_write,
    _RecordLock,
    _strip_control_chars,
    add_resource_claim,
    cap_title,
    create_new_record,
    deregister_session,
    find_orphaned_children,
    find_paired_record,
    find_worktree_id_by_cwd,
    find_worktree_id_by_session,
    format_claim_ref,
    list_records,
    load_record,
    load_record_by_id,
    mark_resumed,
    parse_claim_ref,
    register_session,
    release_all_resources,
    resolve_worktree_path,
    save_record,
    set_disposition,
    update_status,
)


def test_session_entry_round_trips_pane_id(tmp_path: Path) -> None:
    rec = WorktreeRecord(
        worktree_id="wt-pane",
        branch="worktree/wt-pane",
        worktree_path=str(tmp_path / "wt-pane"),
        repo="test-repo",
        machine="test-machine",
        platform="wsl",
        started_at="2026-06-01T10:00:00",
        last_resumed_at="2026-06-01T10:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[SessionEntry("sess-pane", "2026-06-01T10:00:00", pane_id="%42")],
    )
    path = tmp_path / "wt-pane.yaml"

    save_record(rec, path)
    loaded = load_record(path)

    assert loaded.sessions is not None
    assert loaded.sessions[0].pane_id == "%42"


def _lock_increment_worker(yaml_path_str: str, iterations: int, hold: float) -> None:
    """Cross-process worker for the ``_RecordLock`` lost-update test.

    Each iteration does a full read-modify-write of ``resume_count`` under
    ``_RecordLock``, with a small hold between read and write to widen the race
    window. Module-level (picklable) so it runs under the ``spawn`` start method
    on both POSIX (fcntl sidecar) and Windows (msvcrt sidecar). Without a real
    cross-process lock the interleaved writers clobber one another and the final
    count falls short of ``workers * iterations`` -- exactly regression #1860.
    """
    import time
    from pathlib import Path as _Path

    from agent_worktrees.tracking import (
        _RecordLock,
        load_record,
        save_record,
    )

    path = _Path(yaml_path_str)
    for _ in range(iterations):
        with _RecordLock(path):
            record = load_record(path)
            current = record.resume_count or 0
            time.sleep(hold)  # widen the read->write window
            record.resume_count = current + 1
            save_record(record, path)


def _hold_lock_worker(yaml_path_str: str, ready_file: str, release_file: str) -> None:
    """Acquire the blocking `_RecordLock` cross-process and hold it until told.

    Signals readiness by creating ``ready_file`` once the lock is held, then
    spins until ``release_file`` appears before releasing. Module-level so it runs
    under the ``spawn`` start method. Used to test that a best-effort
    (``blocking=False``) acquirer SKIPS while the lock is genuinely held by
    another process (#4547).
    """
    import time
    from pathlib import Path as _Path

    from agent_worktrees.tracking import _RecordLock

    with _RecordLock(_Path(yaml_path_str)):
        _Path(ready_file).write_text("1")
        deadline = time.monotonic() + 30
        while not _Path(release_file).exists():
            if time.monotonic() > deadline:
                break
            time.sleep(0.01)


def _best_effort_increment_worker(
    yaml_path_str: str, iterations: int, hold: float, result_q
) -> None:
    """Cross-process best-effort (``blocking=False``) RMW worker (#4547).

    Mirrors a Picker sweep: each iteration tries the lock non-blocking and, when
    it SKIPS (another writer holds it), simply does nothing that pass -- never a
    lock-free clobbering write. It reports how many increments it actually
    applied so the test can assert the final count is EXACTLY the sum of every
    applied write (blocking + best-effort), i.e. no update from either class was
    ever lost. Module-level so it runs under the ``spawn`` start method.
    """
    import time
    from pathlib import Path as _Path

    from agent_worktrees.tracking import (
        _RecordLock,
        load_record,
        save_record,
    )

    path = _Path(yaml_path_str)
    applied = 0
    for _ in range(iterations):
        with _RecordLock(path, blocking=False) as lk:
            if not lk.acquired:
                continue  # contended -- skip this pass, like a real sweep
            record = load_record(path)
            current = record.resume_count or 0
            time.sleep(hold)  # widen the read->write window
            record.resume_count = current + 1
            save_record(record, path)
            applied += 1
    result_q.put(applied)


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------

class TestSaveLoadRoundTrip:
    """Verify YAML serialization round-trips correctly."""

    def _make_record(self, **overrides) -> WorktreeRecord:
        defaults = dict(
            worktree_id="wt-001",
            branch="worktree/wt-001",
            worktree_path="/tmp/wt",
            repo="test-repo",
            machine="test-machine",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=None,
        )
        defaults.update(overrides)
        return WorktreeRecord(**defaults)

    def test_basic_round_trip(self, tmp_path: Path):
        rec = self._make_record()
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.worktree_id == rec.worktree_id
        assert loaded.branch == rec.branch
        assert loaded.worktree_path == rec.worktree_path
        assert loaded.repo == rec.repo
        assert loaded.status == rec.status
        assert loaded.resume_count == 0

    def test_title_with_special_chars(self, tmp_path: Path):
        rec = self._make_record(title="Fix: handle edge case #42 & more")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.title == "Fix: handle edge case #42 & more"

    @pytest.mark.parametrize(
        ("serialized", "expected"),
        [
            ("false", False),
            ("'false'", True),
            ("null", True),
            (None, True),
        ],
    )
    def test_checkout_managed_only_accepts_explicit_false(
        self, tmp_path: Path, serialized: str | None, expected: bool
    ):
        path = tmp_path / "wt.yaml"
        save_record(self._make_record(checkout_managed=False), path)
        text = path.read_text()
        if serialized is None:
            text = text.replace("checkout_managed: false\n", "")
        else:
            text = text.replace(
                "checkout_managed: false", f"checkout_managed: {serialized}"
            )
        path.write_text(text)

        assert load_record(path).checkout_managed is expected

    def test_load_repairs_control_poison_and_next_save_persists_repair(
        self, tmp_path: Path
    ):
        rec = self._make_record(summary="poisoned")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        path.write_bytes(path.read_bytes().replace(b"poisoned", b"poi\x07soned"))

        loaded = load_record(path)

        assert loaded.summary == "poisoned"
        assert b"\x07" in path.read_bytes()

        save_record(loaded, path)
        assert b"\x07" not in path.read_bytes()
        assert load_record(path).summary == "poisoned"

    def test_load_does_not_repair_non_reader_yaml_errors(
        self, tmp_path: Path, monkeypatch
    ):
        path = tmp_path / "wt.yaml"
        path.write_text("summary: [unterminated\n", encoding="utf-8")
        monkeypatch.setattr(
            "agent_worktrees.tracking._strip_control_chars",
            lambda _text: pytest.fail("non-reader YAML errors must not be repaired"),
        )

        with pytest.raises(yaml.parser.ParserError):
            load_record(path)

    def test_load_rejects_repaired_non_mapping_yaml(self, tmp_path: Path):
        path = tmp_path / "wt.yaml"
        path.write_bytes(b"\x07not-a-record")

        with pytest.raises(yaml.YAMLError, match="must be a YAML mapping"):
            load_record(path)

    def test_null_title(self, tmp_path: Path):
        rec = self._make_record(title=None)
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.title is None

    def test_completed_at(self, tmp_path: Path):
        rec = self._make_record(
            status="complete",
            completed_at="2026-06-01T12:00:00",
        )
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.completed_at == "2026-06-01T12:00:00"

    def test_parent_session_round_trip(self, tmp_path: Path):
        # #1029: the originating-session pointer survives a save/load cycle.
        rec = self._make_record(parent_session="63903896")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "parent_session: 63903896" in path.read_text()
        loaded = load_record(path)
        assert loaded.parent_session == "63903896"

    def test_parent_session_absent_omitted(self, tmp_path: Path):
        # No pointer -> the key is omitted so common-case YAML stays lean.
        rec = self._make_record()
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "parent_session" not in path.read_text()
        loaded = load_record(path)
        assert loaded.parent_session is None

    def test_caller_worktree_round_trip(self, tmp_path: Path):
        # #2178: the bridge caller-worktree pointer survives save/load and is
        # omitted when unset.
        rec = self._make_record(caller_worktree="anomalous-potato-win-20260101-abcd")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "caller_worktree: anomalous-potato-win-20260101-abcd" in path.read_text()
        assert load_record(path).caller_worktree == "anomalous-potato-win-20260101-abcd"
        rec2 = self._make_record()
        path2 = tmp_path / "wt2.yaml"
        save_record(rec2, path2)
        assert "caller_worktree" not in path2.read_text()
        assert load_record(path2).caller_worktree is None

    def test_owner_ref_round_trip(self, tmp_path: Path):
        # resource-claims: the backward owner link survives save/load, is
        # omitted when unset, and parses into a qualified ClaimRef.
        ref = "anomalous-potato/test-chamber/wt-A#sess1"
        rec = self._make_record(owner_ref=ref)
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert f"owner_ref: {ref}" in path.read_text()
        loaded = load_record(path)
        assert loaded.owner_ref == ref
        cr = loaded.owner_claim_ref
        assert cr is not None and cr.worktree_id == "wt-A"
        assert cr.machine == "anomalous-potato" and cr.project == "test-chamber"
        assert cr.session == "sess1" and cr.is_qualified
        rec2 = self._make_record()
        path2 = tmp_path / "wt2.yaml"
        save_record(rec2, path2)
        assert "owner_ref" not in path2.read_text()
        assert load_record(path2).owner_ref is None
        assert load_record(path2).owner_claim_ref is None

    def test_pair_fields_round_trip(self, tmp_path: Path):
        # citadel #957: the paired -harness/-knowledge linkage survives
        # save/load, parses into a ClaimRef, and reports is_paired.
        ref = "test-machine/citadel-knowledge/wt-002"
        rec = self._make_record(
            pair_id="20260806-174915-5182",
            pair_role="harness",
            pair_ref=ref,
            pair_kind="worktree",
        )
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        txt = path.read_text()
        assert "pair_id: 20260806-174915-5182" in txt
        assert "pair_role: harness" in txt
        assert f"pair_ref: {ref}" in txt
        assert "pair_kind: worktree" in txt
        loaded = load_record(path)
        assert loaded.pair_id == "20260806-174915-5182"
        assert loaded.pair_role == "harness"
        assert loaded.pair_ref == ref
        assert loaded.pair_kind == "worktree"
        assert loaded.is_paired
        cr = loaded.pair_claim_ref
        assert cr is not None and cr.worktree_id == "wt-002"
        assert cr.machine == "test-machine" and cr.project == "citadel-knowledge"

    def test_pair_fields_absent_omitted(self, tmp_path: Path):
        # No pairing -> all four keys omitted so the common-case (unpaired)
        # YAML stays byte-identical; is_paired is False.
        rec = self._make_record()
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        txt = path.read_text()
        for key in ("pair_id:", "pair_role:", "pair_ref:", "pair_kind:"):
            assert key not in txt
        loaded = load_record(path)
        assert loaded.pair_id is None and loaded.pair_role is None
        assert loaded.pair_ref is None and loaded.pair_kind is None
        assert not loaded.is_paired
        assert loaded.pair_claim_ref is None

    def test_pair_invalid_enum_values_dropped(self, tmp_path: Path):
        # Unknown pair_role / pair_kind values degrade to None on load, so a
        # stray value can never be mistaken for a real role/kind.
        rec = self._make_record()
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        path.write_text(
            path.read_text()
            + "pair_id: p1\npair_role: bogus\npair_ref: m/p/wt-x\npair_kind: weird\n"
        )
        loaded = load_record(path)
        assert loaded.pair_id == "p1"
        assert loaded.pair_role is None
        assert loaded.pair_kind is None
        assert loaded.pair_ref == "m/p/wt-x"

    def test_resources_round_trip(self, tmp_path: Path):
        # resource-claims: the forward outbound list survives save/load and is
        # omitted when empty (legacy YAMLs stay byte-identical).
        claim = ResourceClaim(
            kind="worktree",
            ref="anomalous-potato/copilot-extensions/wt-B",
            created_at="2026-07-31T15:00:00",
        )
        rec = self._make_record(resources=[claim])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        txt = path.read_text()
        assert "resources:" in txt
        assert "ref: anomalous-potato/copilot-extensions/wt-B" in txt
        loaded = load_record(path)
        assert len(loaded.resources) == 1
        got = loaded.resources[0]
        assert got.kind == "worktree" and got.is_live
        assert got.ref == "anomalous-potato/copilot-extensions/wt-B"
        assert loaded.live_resources == loaded.resources
        # empty list omits the key entirely
        rec2 = self._make_record()
        path2 = tmp_path / "wt2.yaml"
        save_record(rec2, path2)
        assert "resources:" not in path2.read_text()
        assert load_record(path2).resources == []

    def test_disposition_absent_omitted(self, tmp_path: Path):
        # worktree-status-core: an un-annotated record emits no disposition
        # lines, so a legacy/common-case YAML stays byte-identical (no churn).
        rec = self._make_record()
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        txt = path.read_text()
        assert "follow_up" not in txt
        assert "summary" not in txt
        assert "status_note_at" not in txt
        assert "title_asserted" not in txt
        loaded = load_record(path)
        assert loaded.follow_up is False
        assert loaded.summary == ""
        assert loaded.status_note_at is None
        assert loaded.title_asserted is False

    def test_disposition_round_trip(self, tmp_path: Path):
        rec = self._make_record(
            follow_up=True, summary="Phases C/D left; PR open",
            status_note_at="2026-07-15T10:00:00",
        )
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        txt = path.read_text()
        assert "follow_up: true" in txt
        assert "summary: 'Phases C/D left; PR open'" in txt
        assert "status_note_at: 2026-07-15T10:00:00" in txt
        loaded = load_record(path)
        assert loaded.follow_up is True
        assert loaded.summary == "Phases C/D left; PR open"
        # Timestamps reload through YAML's datetime coercion (space form), the
        # same tolerated round-trip as started_at/completed_at.
        assert loaded.status_note_at.startswith("2026-07-15")

    def test_disposition_summary_apostrophe(self, tmp_path: Path):
        rec = self._make_record(summary="don't break on quotes")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert load_record(path).summary == "don't break on quotes"

    # ---- picker-cache-first-paint (dotfiles#948): session-render cache ----

    def test_session_cache_absent_omitted(self, tmp_path: Path):
        # A never-populated worktree emits no session-cache lines, so a legacy
        # YAML stays byte-identical and the cache-only load reads it as Unknown.
        rec = self._make_record()
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        txt = path.read_text()
        assert "session_turns" not in txt
        assert "session_summary" not in txt
        assert "git_state" not in txt
        assert "session_state_at" not in txt
        loaded = load_record(path)
        assert loaded.session_turns is None
        assert loaded.session_summary is None
        assert loaded.git_state is None
        assert loaded.session_state_at is None

    def test_session_cache_round_trip(self, tmp_path: Path):
        rec = self._make_record(
            session_turns=12, session_summary="Fix the thing",
            git_state="wip", session_state_at="2026-08-05T10:00:00",
        )
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        txt = path.read_text()
        assert "session_turns: 12" in txt
        assert "session_summary: 'Fix the thing'" in txt
        assert "git_state: wip" in txt
        loaded = load_record(path)
        assert loaded.session_turns == 12
        assert loaded.session_summary == "Fix the thing"
        assert loaded.git_state == "wip"
        assert loaded.session_state_at.startswith("2026-08-05")

    def test_session_cache_turns_zero_round_trips(self, tmp_path: Path):
        # 0 is a real populated value (UNUSED), distinct from None (Unknown):
        # it must serialize so the cache-only load renders UNUSED, not Unknown.
        rec = self._make_record(session_turns=0,
                                session_state_at="2026-08-05T10:00:00")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "session_turns: 0" in path.read_text()
        assert load_record(path).session_turns == 0

    def test_stamp_session_state_writes_and_preserves(
        self, tmp_path: Path, monkeypatch,
    ):
        from agent_worktrees import tracking as _t
        monkeypatch.setattr(_t.cfg, "tracking_dir", lambda: tmp_path)
        rec = self._make_record(worktree_id="wt-cache")
        save_record(rec, tmp_path / "wt-cache.yaml")

        # Full stamp writes all three fields + freshness (sync = apply inline).
        assert _t.stamp_session_state(
            "wt-cache", turns=7, summary="hello", git_state="wip",
            sync=True) is True
        r = load_record(tmp_path / "wt-cache.yaml")
        assert (r.session_turns, r.session_summary, r.git_state) == (
            7, "hello", "wip")

        # A turns-only stamp preserves the cached summary + state (None = skip).
        assert _t.stamp_session_state("wt-cache", turns=9, sync=True) is True
        r = load_record(tmp_path / "wt-cache.yaml")
        assert (r.session_turns, r.session_summary, r.git_state) == (
            9, "hello", "wip")

        # An unchanged stamp writes nothing (the render cache never ages out,
        # so there is no freshness renewal to churn the YAML).
        assert _t.stamp_session_state(
            "wt-cache", turns=9, summary="hello", git_state="wip",
            sync=True) is False

    def test_stamp_session_state_async_writes_via_queue(
        self, tmp_path: Path, monkeypatch,
    ):
        from agent_worktrees import tracking as _t
        monkeypatch.setattr(_t.cfg, "tracking_dir", lambda: tmp_path)
        rec = self._make_record(worktree_id="wt-async")
        save_record(rec, tmp_path / "wt-async.yaml")

        # Async (default): enqueues + returns True immediately; the write lands
        # after the queue is flushed.
        assert _t.stamp_session_state(
            "wt-async", turns=4, summary="async", git_state="clean") is True
        _t.flush_stamp_writes()
        r = load_record(tmp_path / "wt-async.yaml")
        assert (r.session_turns, r.session_summary, r.git_state) == (
            4, "async", "clean")

    def test_stamp_session_state_absent_record_noops(
        self, tmp_path: Path, monkeypatch,
    ):
        from agent_worktrees import tracking as _t
        monkeypatch.setattr(_t.cfg, "tracking_dir", lambda: tmp_path)
        # sync gives the real "record absent" result (async just enqueues).
        assert _t.stamp_session_state("nope", turns=1, sync=True) is False

    def test_pr_absent_round_trips_as_none(self, tmp_path: Path):
        rec = self._make_record()
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "pr:" not in path.read_text()
        loaded = load_record(path)
        assert loaded.pr is None

    def test_pr_record_round_trip(self, tmp_path: Path):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(
            prs=[PRRecord(
                state="open",
                branch="feature/fix-auth-abc123",
                base_sha="abc123",
                head_sha="def456",
                head_observed_at="2026-09-05T06:01:02+00:00",
                head_observed_api_base="https://gitea.example",
                patch_id="pid789",
                url="https://example/pulls/42",
                number=42,
                provider="gitea",
            )]
        )
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.pr is not None
        assert loaded.pr.state == "open"
        assert loaded.pr.branch == "feature/fix-auth-abc123"
        assert loaded.pr.base_sha == "abc123"
        assert loaded.pr.head_sha == "def456"
        assert loaded.pr.head_observed_at == "2026-09-05T06:01:02+00:00"
        assert loaded.pr.head_observed_api_base == "https://gitea.example"
        assert loaded.pr.patch_id == "pid789"
        assert loaded.pr.url == "https://example/pulls/42"
        assert loaded.pr.number == 42
        assert loaded.pr.provider == "gitea"

    def test_pr_record_number_optional(self, tmp_path: Path):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(prs=[PRRecord(state="creating", branch="feature/x")])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.pr is not None
        assert loaded.pr.state == "creating"
        assert loaded.pr.number is None

    # --- multi-PR schema (#1107) --------------------------------------------

    def test_legacy_pr_block_loads_as_one_element_list(self, tmp_path: Path):
        # A record written by an older tool (single `pr:` block, no `prs:`)
        # must load as a one-element prs list, with repo defaulted to the
        # worktree repo.
        path = tmp_path / "legacy.yaml"
        path.write_text(
            "worktree_id: wt-001\n"
            "branch: worktree/wt-001\n"
            "worktree_path: /tmp/wt\n"
            "repo: owner/thing\n"
            "machine: m\n"
            "platform: wsl\n"
            "started_at: 2026-06-01T10:00:00\n"
            "last_resumed_at: 2026-06-01T10:00:00\n"
            "resume_count: 0\n"
            "title: null\n"
            "status: active\n"
            "completed_at: null\n"
            "handoff_prompt: null\n"
            "pr:\n"
            "  state: open\n"
            "  branch: feature/legacy-abc\n"
            "  number: 7\n"
            "  provider: gitea\n",
            encoding="utf-8",
        )
        loaded = load_record(path)
        assert len(loaded.prs) == 1
        assert loaded.prs[0].branch == "feature/legacy-abc"
        assert loaded.prs[0].number == 7
        assert loaded.prs[0].repo == "owner/thing"  # defaulted from worktree repo
        assert loaded.pr is loaded.prs[0]

    def test_multi_pr_round_trip(self, tmp_path: Path):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(prs=[
            PRRecord(state="merged", branch="feature/one-abc", number=10,
                     provider="gitea", repo="owner/a",
                     opened_at="2026-06-01T10:00:00",
                     closed_at="2026-06-01T11:00:00"),
            PRRecord(state="open", branch="feature/two-abc", number=11,
                     provider="github", repo="owner/b",
                     opened_at="2026-06-01T12:00:00"),
        ])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert [p.number for p in loaded.prs] == [10, 11]
        assert loaded.prs[0].repo == "owner/a"
        assert loaded.prs[1].provider == "github"
        # active = most recent non-terminal -> the open one (#11)
        assert loaded.pr.number == 11

    def test_active_pr_rule(self):
        from agent_worktrees.tracking import PRRecord

        # No live PR -> most recent overall (last by opened_at).
        rec = self._make_record(prs=[
            PRRecord(state="merged", branch="a", opened_at="2026-06-01T10:00:00"),
            PRRecord(state="closed", branch="b", opened_at="2026-06-01T12:00:00"),
        ])
        assert rec.active_pr().branch == "b"
        # A live PR wins over a more-recent terminal one.
        rec2 = self._make_record(prs=[
            PRRecord(state="open", branch="live", opened_at="2026-06-01T10:00:00"),
            PRRecord(state="merged", branch="done", opened_at="2026-06-01T12:00:00"),
        ])
        assert rec2.active_pr().branch == "live"
        # Empty -> None.
        assert self._make_record(prs=[]).active_pr() is None

    def test_has_live_pr(self):
        from agent_worktrees.tracking import PRRecord
        assert self._make_record(prs=[]).has_live_pr() is False
        assert self._make_record(prs=[
            PRRecord(state="merged", branch="a"),
            PRRecord(state="closed", branch="b"),
        ]).has_live_pr() is False
        assert self._make_record(prs=[
            PRRecord(state="merged", branch="a"),
            PRRecord(state="open", branch="b"),
        ]).has_live_pr() is True

    def test_pr_setter_replaces_active(self):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(prs=[PRRecord(state="creating", branch="feature/x")])
        rec.pr = PRRecord(state="open", branch="feature/x", number=5)
        assert len(rec.prs) == 1
        assert rec.prs[0].state == "open"
        assert rec.prs[0].number == 5

    def test_pr_setter_appends_when_empty_and_clears(self):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(prs=[])
        rec.pr = PRRecord(state="open", branch="feature/x")
        assert len(rec.prs) == 1
        rec.pr = None
        assert rec.prs == []

    def test_save_mirrors_active_to_legacy_pr_block(self, tmp_path: Path):
        from agent_worktrees.tracking import PRRecord

        rec = self._make_record(prs=[
            PRRecord(state="merged", branch="a", number=1),
            PRRecord(state="open", branch="b", number=2),
        ])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        text = path.read_text(encoding="utf-8")
        assert "prs:" in text
        # Mirrored legacy pr: block points at the active PR (#2).
        import yaml as _yaml
        data = _yaml.safe_load(text)
        assert data["pr"]["number"] == 2
        assert [p["number"] for p in data["prs"]] == [1, 2]

    def test_zero_pr_emits_neither_block(self, tmp_path: Path):
        rec = self._make_record(prs=[])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        text = path.read_text(encoding="utf-8")
        assert "\npr:" not in text
        assert "prs:" not in text


# ---------------------------------------------------------------------------
# Session registry â three-state semantics
# ---------------------------------------------------------------------------

class TestSessionsField:
    """Verify None vs [] vs populated sessions semantics."""

    def _make_record(self, **overrides) -> WorktreeRecord:
        defaults = dict(
            worktree_id="wt-sess",
            branch="worktree/wt-sess",
            worktree_path="/tmp/wt-sess",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=None,
        )
        defaults.update(overrides)
        return WorktreeRecord(**defaults)

    def test_sessions_none_means_not_indexed(self, tmp_path: Path):
        """sessions=None (pre-registry) â YAML has no sessions key."""
        rec = self._make_record(sessions=None)
        path = tmp_path / "wt.yaml"
        save_record(rec, path)

        content = path.read_text()
        assert "sessions:" not in content

        loaded = load_record(path)
        assert loaded.sessions is None

    def test_sessions_empty_means_indexed(self, tmp_path: Path):
        """sessions=[] (indexed, no sessions) â YAML has sessions: []."""
        rec = self._make_record(sessions=[])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)

        content = path.read_text()
        assert "sessions: []" in content

        loaded = load_record(path)
        assert loaded.sessions == []
        assert loaded.sessions is not None

    def test_sessions_populated(self, tmp_path: Path):
        """sessions=[...] with entries."""
        entries = [
            SessionEntry(
                session_id="aaa-111",
                started_at="2026-06-01T10:00:00",
                pid=1234,
            ),
            SessionEntry(
                session_id="bbb-222",
                started_at="2026-06-01T11:00:00",
                ended_at="2026-06-01T11:30:00",
            ),
        ]
        rec = self._make_record(sessions=entries)
        path = tmp_path / "wt.yaml"
        save_record(rec, path)

        loaded = load_record(path)
        assert len(loaded.sessions) == 2
        assert loaded.sessions[0].session_id == "aaa-111"
        assert loaded.sessions[0].pid == 1234
        assert loaded.sessions[0].ended_at is None
        assert loaded.sessions[1].session_id == "bbb-222"
        assert loaded.sessions[1].ended_at == "2026-06-01T11:30:00"

    def test_session_entry_no_optional_fields(self, tmp_path: Path):
        """SessionEntry with only required fields."""
        rec = self._make_record(sessions=[
            SessionEntry(session_id="ccc-333", started_at="2026-06-01T12:00:00"),
        ])
        path = tmp_path / "wt.yaml"
        save_record(rec, path)

        loaded = load_record(path)
        assert loaded.sessions[0].pid is None
        assert loaded.sessions[0].ended_at is None

    def test_backward_compat_no_sessions_key(self, tmp_path: Path):
        """Loading a YAML written before session registry (no sessions key)."""
        content = """\
worktree_id: old-wt
branch: worktree/old-wt
worktree_path: /tmp/old
repo: test
machine: test
platform: wsl
started_at: 2026-01-01T00:00:00
last_resumed_at: 2026-01-01T00:00:00
resume_count: 3
title: Old worktree
status: active
completed_at: null
"""
        path = tmp_path / "old.yaml"
        path.write_text(content)
        loaded = load_record(path)
        assert loaded.sessions is None
        assert loaded.worktree_id == "old-wt"
        assert loaded.resume_count == 3


# ---------------------------------------------------------------------------
# register_session / deregister_session
# ---------------------------------------------------------------------------

class TestSessionRegistration:
    """Test hook-invoked session registration."""

    def test_register_new_session(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = WorktreeRecord(
            worktree_id="reg-wt",
            branch="worktree/reg-wt",
            worktree_path="/tmp/reg",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
        )
        save_record(rec, tmp_tracking_dir / "reg-wt.yaml")

        register_session("reg-wt", "session-aaa", pid=999)

        loaded = load_record(tmp_tracking_dir / "reg-wt.yaml")
        assert len(loaded.sessions) == 1
        assert loaded.sessions[0].session_id == "session-aaa"
        assert loaded.sessions[0].pid == 999

    def test_register_dedupes(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = WorktreeRecord(
            worktree_id="dup-wt",
            branch="worktree/dup-wt",
            worktree_path="/tmp/dup",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[SessionEntry("existing", "2026-06-01T09:00:00", pid=100)],
        )
        save_record(rec, tmp_tracking_dir / "dup-wt.yaml")

        register_session("dup-wt", "existing", pid=200)

        loaded = load_record(tmp_tracking_dir / "dup-wt.yaml")
        assert len(loaded.sessions) == 1
        assert loaded.sessions[0].pid == 200  # updated, not duplicated

    def test_register_initializes_none_sessions(self, tmp_tracking_dir: Path, monkeypatch_config):
        """Registering on a pre-registry record initializes the list."""
        rec = WorktreeRecord(
            worktree_id="pre-reg",
            branch="worktree/pre-reg",
            worktree_path="/tmp/pre",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=None,
        )
        save_record(rec, tmp_tracking_dir / "pre-reg.yaml")

        register_session("pre-reg", "first-session")

        loaded = load_record(tmp_tracking_dir / "pre-reg.yaml")
        assert loaded.sessions is not None
        assert len(loaded.sessions) == 1

    def test_deregister_stamps_ended_at(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = WorktreeRecord(
            worktree_id="end-wt",
            branch="worktree/end-wt",
            worktree_path="/tmp/end",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[SessionEntry("sess-end", "2026-06-01T10:00:00")],
        )
        save_record(rec, tmp_tracking_dir / "end-wt.yaml")

        deregister_session("end-wt", "sess-end")

        loaded = load_record(tmp_tracking_dir / "end-wt.yaml")
        assert loaded.sessions[0].ended_at is not None

    def test_register_nonexistent_worktree(self, tmp_tracking_dir: Path, monkeypatch_config):
        """Registering against a missing worktree is a no-op."""
        register_session("nonexistent", "some-session")
        # Should not raise

    def test_deregister_nonexistent_worktree(self, tmp_tracking_dir: Path, monkeypatch_config):
        """Deregistering against a missing worktree is a no-op."""
        deregister_session("nonexistent", "some-session")
        # Should not raise

    def test_deregister_unknown_session(self, tmp_tracking_dir: Path, monkeypatch_config):
        """Deregistering a session ID that doesn't exist is a no-op."""
        rec = WorktreeRecord(
            worktree_id="noop-wt",
            branch="worktree/noop-wt",
            worktree_path="/tmp/noop",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[SessionEntry("other-sess", "2026-06-01T10:00:00")],
        )
        save_record(rec, tmp_tracking_dir / "noop-wt.yaml")

        deregister_session("noop-wt", "nonexistent-session")

        loaded = load_record(tmp_tracking_dir / "noop-wt.yaml")
        assert len(loaded.sessions) == 1
        assert loaded.sessions[0].ended_at is None


# ---------------------------------------------------------------------------
# list_records
# ---------------------------------------------------------------------------

class TestListRecords:
    """Test record listing and filtering."""

    def _save_records(self, tracking_dir: Path, records: list[WorktreeRecord]):
        for rec in records:
            save_record(rec, tracking_dir / f"{rec.worktree_id}.yaml")

    def _make(self, wt_id: str, **overrides) -> WorktreeRecord:
        defaults = dict(
            worktree_id=wt_id,
            branch=f"worktree/{wt_id}",
            worktree_path=f"/tmp/{wt_id}",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
        )
        defaults.update(overrides)
        return WorktreeRecord(**defaults)

    def test_list_all(self, tmp_tracking_dir: Path):
        self._save_records(tmp_tracking_dir, [
            self._make("a"),
            self._make("b"),
            self._make("c"),
        ])
        records = list_records(tmp_tracking_dir)
        assert len(records) == 3

    def test_filter_by_status(self, tmp_tracking_dir: Path):
        self._save_records(tmp_tracking_dir, [
            self._make("active-1", status="active"),
            self._make("done-1", status="complete"),
            self._make("active-2", status="active"),
        ])
        active = list_records(tmp_tracking_dir, status_filter="active")
        assert len(active) == 2

    def test_filter_by_platform(self, tmp_tracking_dir: Path):
        self._save_records(tmp_tracking_dir, [
            self._make("wsl-1", platform="wsl"),
            self._make("win-1", platform="windows"),
        ])
        wsl = list_records(tmp_tracking_dir, platform_filter="wsl")
        assert len(wsl) == 1
        assert wsl[0].worktree_id == "wsl-1"

    def test_empty_dir(self, tmp_tracking_dir: Path):
        records = list_records(tmp_tracking_dir)
        assert records == []

    def test_nonexistent_dir(self, tmp_path: Path):
        records = list_records(tmp_path / "nonexistent")
        assert records == []


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

class TestStatusTransitions:
    """Test update_status and mark_resumed."""

    def _make_and_save(
        self, tmp_tracking_dir: Path, monkeypatch_config, **overrides
    ) -> WorktreeRecord:
        defaults = dict(
            worktree_id="status-wt",
            branch="worktree/status-wt",
            worktree_path="/tmp/status",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
        )
        defaults.update(overrides)
        rec = WorktreeRecord(**defaults)
        save_record(rec, tmp_tracking_dir / f"{rec.worktree_id}.yaml")
        return rec

    def test_update_to_complete(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = self._make_and_save(tmp_tracking_dir, monkeypatch_config)
        update_status(rec, "complete")
        loaded = load_record(tmp_tracking_dir / "status-wt.yaml")
        assert loaded.status == "complete"
        assert loaded.completed_at is not None

    def test_update_to_finalized(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = self._make_and_save(tmp_tracking_dir, monkeypatch_config)
        update_status(rec, "finalized")
        loaded = load_record(tmp_tracking_dir / "status-wt.yaml")
        assert loaded.status == "finalized"
        assert loaded.completed_at is not None

    def test_mark_resumed_increments(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = self._make_and_save(tmp_tracking_dir, monkeypatch_config)
        mark_resumed(rec)
        loaded = load_record(tmp_tracking_dir / "status-wt.yaml")
        assert loaded.resume_count == 1
        assert loaded.last_resumed_at != "2026-06-01T10:00:00"

    def test_mark_resumed_twice(self, tmp_tracking_dir: Path, monkeypatch_config):
        rec = self._make_and_save(tmp_tracking_dir, monkeypatch_config)
        mark_resumed(rec)
        mark_resumed(rec)
        loaded = load_record(tmp_tracking_dir / "status-wt.yaml")
        assert loaded.resume_count == 2


# ---------------------------------------------------------------------------
# create_new_record
# ---------------------------------------------------------------------------

class TestCreateNewRecord:
    """Test new record creation."""

    def test_creates_with_defaults(self, tmp_tracking_dir: Path):
        rec = create_new_record(
            worktree_id="new-001",
            branch="worktree/new-001",
            worktree_path="/tmp/new",
            repo="test-repo",
            machine="test",
            platform_name="wsl",
            tracking_path=tmp_tracking_dir,
        )
        assert rec.worktree_id == "new-001"
        assert rec.status == "active"
        assert rec.sessions == []  # indexed from creation
        assert rec.resume_count == 0
        assert rec.completed_at is None

        # Verify it was persisted
        loaded = load_record(tmp_tracking_dir / "new-001.yaml")
        assert loaded.sessions == []

    def test_seeds_parent_session(self, tmp_tracking_dir: Path):
        # #1029: an explicit parent-session pointer is recorded at creation.
        rec = create_new_record(
            worktree_id="new-002",
            branch="worktree/new-002",
            worktree_path="/tmp/new2",
            repo="test-repo",
            machine="test",
            platform_name="wsl",
            tracking_path=tmp_tracking_dir,
            parent_session="deadbeef",
        )
        assert rec.parent_session == "deadbeef"
        loaded = load_record(tmp_tracking_dir / "new-002.yaml")
        assert loaded.parent_session == "deadbeef"


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    """Test _atomic_write safety."""

    def test_creates_parent_dirs(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "file.yaml"
        _atomic_write(target, "content")
        assert target.read_text() == "content"

    def test_overwrites_existing(self, tmp_path: Path):
        target = tmp_path / "file.yaml"
        target.write_text("old")
        _atomic_write(target, "new")
        assert target.read_text() == "new"


class TestRecordLockCrossProcess:
    """_RecordLock must serialize read-modify-write ACROSS processes (#1860).

    The picker's concurrent reconcilers (reconcile_prs / reconcile_bound_live /
    the stamp writers) and a foreground CLI can RMW the same tracking YAML from
    separate processes. Before #1860 the Windows path held only an in-process
    ``threading.RLock`` (a no-op across processes), so cross-process writers
    clobbered one another. This exercises the real sidecar lock -- ``fcntl`` on
    POSIX, ``msvcrt`` on Windows -- under ``spawn`` (true separate processes on
    both platforms, so the in-process RLock cannot mask a missing sidecar lock).
    """

    def _seed(self, tmp_path: Path) -> Path:
        path = tmp_path / "wt-lock.yaml"
        rec = WorktreeRecord(
            worktree_id="wt-lock",
            branch="worktree/wt-lock",
            worktree_path="/tmp/wt-lock",
            repo="test-repo",
            machine="test-machine",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=None,
        )
        save_record(rec, path)
        return path

    def test_no_lost_updates_across_processes(self, tmp_path: Path):
        import multiprocessing as mp

        path = self._seed(tmp_path)
        workers, iterations, hold = 4, 12, 0.003
        ctx = mp.get_context("spawn")
        procs = [
            ctx.Process(
                target=_lock_increment_worker,
                args=(str(path), iterations, hold),
            )
            for _ in range(workers)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=120)
            assert p.exitcode == 0, f"worker exited {p.exitcode}"

        final = load_record(path)
        assert final.resume_count == workers * iterations

    def test_lock_acquire_release_reentrant_in_process(self, tmp_path: Path):
        # A second _RecordLock on the SAME path from the SAME thread must not
        # self-deadlock (the in-process guard is a re-entrant RLock); the sidecar
        # is opened per-context and released cleanly.
        path = self._seed(tmp_path)
        with _RecordLock(path):
            rec = load_record(path)
            rec.resume_count = 5
            save_record(rec, path)
        with _RecordLock(path):
            assert load_record(path).resume_count == 5

    def test_blocking_acquire_sets_acquired(self, tmp_path: Path):
        # The default (critical) acquire always reports acquired=True.
        path = self._seed(tmp_path)
        with _RecordLock(path) as lk:
            assert lk.acquired is True

    def test_best_effort_uncontended_acquires(self, tmp_path: Path):
        path = self._seed(tmp_path)
        with _RecordLock(path, blocking=False) as lk:
            assert lk.acquired is True

    @pytest.mark.parametrize("failure_site", ["mkdir", "open"])
    def test_setup_failure_releases_in_process_lock(
        self, tmp_path: Path, monkeypatch, failure_site: str
    ):
        import os
        import threading

        path = self._seed(tmp_path)
        lock_path = path.with_suffix(".lock")
        original_mkdir = Path.mkdir
        original_open = os.open
        failed = False

        def fail_mkdir(candidate: Path, *args, **kwargs):
            nonlocal failed
            if failure_site == "mkdir" and candidate == lock_path.parent and not failed:
                failed = True
                raise PermissionError("mkdir denied")
            return original_mkdir(candidate, *args, **kwargs)

        def fail_open(candidate, *args, **kwargs):
            nonlocal failed
            if failure_site == "open" and Path(candidate) == lock_path and not failed:
                failed = True
                raise PermissionError("open denied")
            return original_open(candidate, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", fail_mkdir)
        monkeypatch.setattr(os, "open", fail_open)
        with pytest.raises(PermissionError, match=f"{failure_site} denied"):
            with _RecordLock(path):
                pytest.fail("setup failure must prevent entry")
        assert failed

        acquired = threading.Event()

        def acquire_after_failure() -> None:
            with _RecordLock(path):
                acquired.set()

        thread = threading.Thread(target=acquire_after_failure, daemon=True)
        thread.start()
        thread.join(timeout=2)
        assert acquired.is_set(), "setup failure leaked the per-path RLock"
        assert not thread.is_alive()

    def test_best_effort_skips_and_preserves_when_held(self, tmp_path: Path):
        # #4547: while a critical (blocking) holder in ANOTHER process owns the
        # sidecar, a best-effort acquirer must SKIP (acquired=False) rather than
        # block, and must not corrupt the holder's data.
        import multiprocessing as mp
        import time

        path = self._seed(tmp_path)
        with _RecordLock(path):  # pre-set a sentinel the holder will preserve
            rec = load_record(path)
            rec.resume_count = 42
            save_record(rec, path)

        ready = tmp_path / "ready"
        release = tmp_path / "release"
        ctx = mp.get_context("spawn")
        holder = ctx.Process(
            target=_hold_lock_worker,
            args=(str(path), str(ready), str(release)),
        )
        holder.start()
        try:
            # Wait until the other process genuinely holds the cross-process lock.
            deadline = time.monotonic() + 60
            while not ready.exists():
                assert holder.is_alive(), "holder died before acquiring"
                assert time.monotonic() < deadline, "holder never acquired"
                time.sleep(0.02)

            # Best-effort acquire must skip immediately (no block, no acquire).
            t0 = time.monotonic()
            with _RecordLock(path, blocking=False) as lk:
                acquired = lk.acquired
            elapsed = time.monotonic() - t0
            assert acquired is False, "best-effort should skip a held lock"
            assert elapsed < 1.0, "best-effort must not block on a held lock"
        finally:
            release.write_text("1")
            holder.join(timeout=60)

        assert holder.exitcode == 0
        # The holder's data is intact (best-effort never wrote/clobbered).
        assert load_record(path).resume_count == 42
        # And once released, a best-effort acquire succeeds again.
        with _RecordLock(path, blocking=False) as lk:
            assert lk.acquired is True

    def test_blocking_writers_never_lose_while_best_effort_skips(
        self, tmp_path: Path
    ):
        # #4547 (foreground wraps): the cooperative outcome. Critical (blocking)
        # writers -- the foreground CLI verbs (set-pr, set-disposition,
        # mark-complete, mark_resumed, claims) now hold a blocking `_RecordLock`
        # across their RMW -- must EACH land, while best-effort sweeps skip on
        # contention rather than clobber. Run both classes concurrently and
        # assert the final count is EXACTLY the sum of every write that reported
        # applying: no update from either class is ever lost, and a skip writes
        # nothing.
        import multiprocessing as mp

        path = self._seed(tmp_path)
        b_workers, b_iters = 2, 12
        e_workers, e_iters, hold = 2, 12, 0.003
        ctx = mp.get_context("spawn")
        result_q = ctx.Queue()

        blockers = [
            ctx.Process(
                target=_lock_increment_worker,
                args=(str(path), b_iters, hold),
            )
            for _ in range(b_workers)
        ]
        best_effort = [
            ctx.Process(
                target=_best_effort_increment_worker,
                args=(str(path), e_iters, hold, result_q),
            )
            for _ in range(e_workers)
        ]
        for p in blockers + best_effort:
            p.start()

        # Collect best-effort applied counts before joining (avoid a Queue-join
        # deadlock if a worker's buffered put isn't drained).
        applied_total = sum(result_q.get(timeout=120) for _ in best_effort)

        for p in blockers + best_effort:
            p.join(timeout=120)
            assert p.exitcode == 0, f"worker exited {p.exitcode}"

        final = load_record(path)
        blocking_total = b_workers * b_iters
        # Every blocking write landed AND best-effort added exactly what it
        # reported -- no lost updates from either class, no phantom writes.
        assert final.resume_count == blocking_total + applied_total
        assert final.resume_count >= blocking_total


class TestFindWorktreeIdByCwd:
    """find_worktree_id_by_cwd -- resolve a worktree from a session cwd."""

    def _save(self, tracking_dir: Path, wt_id: str, wt_path: str) -> None:
        rec = WorktreeRecord(
            worktree_id=wt_id,
            branch=f"worktree/{wt_id}",
            worktree_path=wt_path,
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
        )
        save_record(rec, tracking_dir / f"{wt_id}.yaml")

    def test_exact_match(self, tmp_tracking_dir: Path, monkeypatch_config):
        self._save(tmp_tracking_dir, "wt-a", "/tmp/src/wt-a")
        assert find_worktree_id_by_cwd("/tmp/src/wt-a") == "wt-a"

    def test_subdirectory_match(self, tmp_tracking_dir: Path, monkeypatch_config):
        self._save(tmp_tracking_dir, "wt-a", "/tmp/src/wt-a")
        assert find_worktree_id_by_cwd("/tmp/src/wt-a/sub/dir") == "wt-a"

    def test_deepest_match_wins(self, tmp_tracking_dir: Path, monkeypatch_config):
        self._save(tmp_tracking_dir, "outer", "/tmp/src")
        self._save(tmp_tracking_dir, "inner", "/tmp/src/inner")
        assert find_worktree_id_by_cwd("/tmp/src/inner/x") == "inner"

    def test_no_match_returns_none(self, tmp_tracking_dir: Path, monkeypatch_config):
        self._save(tmp_tracking_dir, "wt-a", "/tmp/src/wt-a")
        assert find_worktree_id_by_cwd("/tmp/elsewhere") is None

    def test_empty_cwd_returns_none(self, tmp_tracking_dir: Path, monkeypatch_config):
        assert find_worktree_id_by_cwd("") is None


class TestPairedRecordResolution:
    """load_record_by_id + find_paired_record -- the #957 pairing resolver."""

    def _save(self, tracking_dir: Path, rec: WorktreeRecord) -> None:
        save_record(rec, tracking_dir / f"{rec.worktree_id}.yaml")

    def _rec(self, wt_id: str, **overrides) -> WorktreeRecord:
        base = dict(
            worktree_id=wt_id,
            branch=f"worktree/{wt_id}",
            worktree_path=f"/tmp/src/{wt_id}",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
        )
        base.update(overrides)
        return WorktreeRecord(**base)

    def test_load_record_by_id(self, tmp_tracking_dir: Path, monkeypatch_config):
        self._save(tmp_tracking_dir, self._rec("wt-a"))
        loaded = load_record_by_id("wt-a")
        assert loaded is not None and loaded.worktree_id == "wt-a"

    def test_load_record_by_id_missing(self, tmp_tracking_dir: Path, monkeypatch_config):
        assert load_record_by_id("nope") is None
        assert load_record_by_id("") is None

    def test_find_paired_record_resolves_sibling(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        harness = self._rec(
            "wt-harness",
            pair_id="pair1",
            pair_role="harness",
            pair_ref="test/citadel-knowledge/wt-knowledge",
            pair_kind="worktree",
        )
        knowledge = self._rec(
            "wt-knowledge",
            pair_id="pair1",
            pair_role="knowledge",
            pair_ref="test/citadel-harness/wt-harness",
            pair_kind="worktree",
        )
        harness_dir = tmp_tracking_dir.parent / ".citadel-harness" / "worktrees"
        knowledge_dir = (
            tmp_tracking_dir.parent / ".citadel-knowledge" / "worktrees"
        )
        self._save(harness_dir, harness)
        self._save(knowledge_dir, knowledge)
        sib = find_paired_record(harness)
        assert sib is not None and sib.worktree_id == "wt-knowledge"
        assert sib.pair_role == "knowledge"
        # Symmetric: knowledge resolves back to harness.
        assert find_paired_record(knowledge).worktree_id == "wt-harness"

    def test_find_paired_record_unpaired(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        assert find_paired_record(self._rec("solo")) is None

    def test_find_paired_record_dangling_ref(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        rec = self._rec(
            "wt-x", pair_id="p", pair_role="harness",
            pair_ref="test/proj/wt-gone", pair_kind="worktree",
        )
        assert find_paired_record(rec) is None


class TestCascadeAndOrphans:
    """release_all_resources + find_orphaned_children -- the #877 E1b cascade."""

    def _save(self, tracking_dir: Path, rec: WorktreeRecord) -> None:
        save_record(rec, tracking_dir / f"{rec.worktree_id}.yaml")

    def _rec(self, wt_id: str, **overrides) -> WorktreeRecord:
        base = dict(
            worktree_id=wt_id,
            branch=f"worktree/{wt_id}",
            worktree_path=f"/tmp/src/{wt_id}",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
        )
        base.update(overrides)
        return WorktreeRecord(**base)

    def test_release_all_resources_flips_live_claims(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        parent = self._rec("wt-parent", resources=[
            ResourceClaim(kind="worktree", ref="test/other/wt-child", state="active"),
            ResourceClaim(kind="worktree", ref="test/other/wt-old", state="released"),
        ])
        self._save(tmp_tracking_dir, parent)
        released = release_all_resources(parent)
        assert [c.ref for c in released] == ["test/other/wt-child"]
        # Persisted: reload and confirm both are released now.
        reloaded = load_record_by_id("wt-parent")
        assert all(c.state == "released" for c in reloaded.resources)
        assert reloaded.live_resources == []

    def test_release_all_resources_idempotent(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        parent = self._rec("wt-parent", resources=[
            ResourceClaim(kind="worktree", ref="test/other/wt-child", state="released"),
        ])
        self._save(tmp_tracking_dir, parent)
        assert release_all_resources(parent) == []

    def test_find_orphaned_children_finalized_and_absent_parents(
        self, tmp_tracking_dir: Path, monkeypatch_config, monkeypatch
    ):
        import types
        # Pin the local machine so the qualified test refs (machine="test") are
        # judged as same-machine rather than skipped as cross-machine.
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: types.SimpleNamespace(machine="test"))
        # A finalized parent + its child, a live parent + its child, and a child
        # whose parent record is absent.
        self._save(tmp_tracking_dir, self._rec("wt-fin", status="finalized"))
        self._save(tmp_tracking_dir, self._rec("wt-live", status="active"))
        self._save(tmp_tracking_dir, self._rec(
            "c-of-fin", owner_ref="test/test-repo/wt-fin"))
        self._save(tmp_tracking_dir, self._rec(
            "c-of-live", owner_ref="test/test-repo/wt-live"))
        self._save(tmp_tracking_dir, self._rec(
            "c-of-gone", owner_ref="test/test-repo/wt-missing"))
        self._save(tmp_tracking_dir, self._rec("unowned"))

        orphans = find_orphaned_children(tmp_tracking_dir)
        ids = {child.worktree_id for child, _ in orphans}
        assert ids == {"c-of-fin", "c-of-gone"}
        # The finalized-parent orphan pairs with its parent record; the
        # absent-parent orphan pairs with None.
        by_id = {child.worktree_id: parent for child, parent in orphans}
        assert by_id["c-of-fin"].worktree_id == "wt-fin"
        assert by_id["c-of-gone"] is None

    def test_find_orphaned_children_skips_cross_machine(
        self, tmp_tracking_dir: Path, monkeypatch, tmp_path: Path
    ):
        import types
        monkeypatch.setattr("agent_worktrees.config.tracking_dir",
                            lambda: tmp_tracking_dir)
        monkeypatch.setattr("agent_worktrees.config.load_config",
                            lambda *a, **k: types.SimpleNamespace(machine="here"))
        # A child owned by a parent on ANOTHER machine is not judged locally.
        self._save(tmp_tracking_dir, self._rec(
            "c-remote", owner_ref="elsewhere/test-repo/wt-remote"))
        assert find_orphaned_children(tmp_tracking_dir) == []


class TestFindWorktreeIdBySession:
    """find_worktree_id_by_session -- resolve an explicitly registered session."""

    def _save(
        self, tracking_dir: Path, wt_id: str, session_ids: list[str]
    ) -> None:
        rec = WorktreeRecord(
            worktree_id=wt_id,
            branch=f"worktree/{wt_id}",
            worktree_path=f"/tmp/src/{wt_id}",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[
                SessionEntry(sid, "2026-06-01T10:00:00")
                for sid in session_ids
            ],
        )
        save_record(rec, tracking_dir / f"{wt_id}.yaml")

    def test_unique_match(self, tmp_tracking_dir: Path, monkeypatch_config):
        self._save(tmp_tracking_dir, "wt-a", ["resumed-session"])
        assert find_worktree_id_by_session("resumed-session") == "wt-a"

    def test_ambiguous_match_returns_none(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        self._save(tmp_tracking_dir, "wt-a", ["duplicate"])
        self._save(tmp_tracking_dir, "wt-b", ["duplicate"])
        assert find_worktree_id_by_session("duplicate") is None

    def test_missing_or_empty_session_returns_none(
        self, tmp_tracking_dir: Path, monkeypatch_config
    ):
        self._save(tmp_tracking_dir, "wt-a", ["other"])
        assert find_worktree_id_by_session("missing") is None
        assert find_worktree_id_by_session("") is None


# ---------------------------------------------------------------------------
# System worktrees -- kind annotation, back-compat, and filtering
# ---------------------------------------------------------------------------

class TestSystemWorktreeKind:
    """The `kind` field marks daemon-owned worktrees (hidden from the Picker)."""

    def _base(self, **overrides) -> WorktreeRecord:
        defaults = dict(
            worktree_id="wt-k",
            branch="worktree/wt-k",
            worktree_path="/tmp/wt-k",
            repo="test-repo",
            machine="test",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=None,
        )
        defaults.update(overrides)
        return WorktreeRecord(**defaults)

    def test_default_kind_is_session(self, tmp_path: Path):
        rec = self._base()
        assert rec.kind == "session"

    def test_system_kind_round_trip(self, tmp_path: Path):
        rec = self._base(kind="system", owner="config-reflect")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        loaded = load_record(path)
        assert loaded.kind == "system"
        assert loaded.owner == "config-reflect"

    def test_bridge_kind_round_trip(self, tmp_path: Path):
        rec = self._base(kind="bridge")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "kind: bridge\n" in path.read_text(encoding="utf-8")
        loaded = load_record(path)
        assert loaded.kind == "bridge"

    def test_unknown_kind_degrades_to_session(self, tmp_path: Path):
        path = tmp_path / "weird.yaml"
        path.write_text(
            "worktree_id: w\nbranch: worktree/w\nworktree_path: /tmp/w\n"
            "repo: test-repo\nmachine: test\nplatform: wsl\n"
            "started_at: 2026-06-01T10:00:00\nlast_resumed_at: 2026-06-01T10:00:00\n"
            "resume_count: 0\ntitle: null\nstatus: active\ncompleted_at: null\n"
            "handoff_prompt: null\nkind: gremlin\n",
            encoding="utf-8",
        )
        assert load_record(path).kind == "session"

    def test_legacy_record_without_kind_loads_as_session(self, tmp_path: Path):
        # A pre-feature YAML has no `kind:` line.
        path = tmp_path / "legacy.yaml"
        path.write_text(
            "worktree_id: old\n"
            "branch: worktree/old\n"
            "worktree_path: /tmp/old\n"
            "repo: test-repo\n"
            "machine: test\n"
            "platform: wsl\n"
            "started_at: 2026-06-01T10:00:00\n"
            "last_resumed_at: 2026-06-01T10:00:00\n"
            "resume_count: 0\n"
            "title: null\n"
            "status: active\n"
            "completed_at: null\n"
            "handoff_prompt: null\n",
            encoding="utf-8",
        )
        loaded = load_record(path)
        assert loaded.kind == "session"
        assert loaded.owner is None

    def test_session_record_yaml_has_no_kind_line(self, tmp_path: Path):
        # Back-compat: session records must not gain a `kind:` line (no churn).
        rec = self._base(kind="session")
        path = tmp_path / "wt.yaml"
        save_record(rec, path)
        assert "kind:" not in path.read_text(encoding="utf-8")

    def test_list_records_kind_filter(self, tmp_path: Path):
        save_record(self._base(worktree_id="s1", kind="session"), tmp_path / "s1.yaml")
        save_record(
            self._base(worktree_id="d1", kind="system", owner="config-reflect"),
            tmp_path / "d1.yaml",
        )
        system = list_records(tmp_path, kind_filter="system")
        assert [r.worktree_id for r in system] == ["d1"]
        sessions_only = list_records(tmp_path, kind_filter="session")
        assert [r.worktree_id for r in sessions_only] == ["s1"]
        assert len(list_records(tmp_path)) == 2

    def test_create_new_record_system(self, tmp_path: Path):
        rec = create_new_record(
            "sys-x", "worktree/sys-x", "/tmp/sys-x", "test-repo", "test", "wsl",
            tmp_path, kind="system", owner="session-sync",
        )
        assert rec.kind == "system"
        assert rec.owner == "session-sync"
        loaded = load_record(tmp_path / "sys-x.yaml")
        assert loaded.kind == "system"
        assert loaded.owner == "session-sync"


# ---------------------------------------------------------------------------
# #2668 -- two-axis taxonomy (interface x origin) + Picker visibility
# ---------------------------------------------------------------------------

class TestOriginInterfaceTaxonomy:
    """The interface/origin marks derive from kind (+ caller) when unstamped,
    an explicit stamp always wins, and visibility keys on origin (not kind)."""

    def _base(self, **overrides) -> WorktreeRecord:
        defaults = dict(
            worktree_id="wt", branch="b", worktree_path="/tmp/wt",
            repo="r", machine="m", platform="wsl",
            started_at="t", last_resumed_at="t", resume_count=0,
            title=None, status="active", completed_at=None,
        )
        defaults.update(overrides)
        return WorktreeRecord(**defaults)

    # -- derivation from kind -------------------------------------------------

    def test_session_derives_cli_user_shown(self):
        r = self._base(kind="session")
        assert r.resolved_interface == "cli"
        assert r.resolved_origin == "user"
        assert r.is_picker_hidden is False

    def test_system_derives_system_hidden(self):
        r = self._base(kind="system")
        assert r.resolved_origin == "system"
        assert r.is_picker_hidden is True

    def test_bridge_without_caller_is_user_acp_shown(self):
        # An operator/NF-launched ACP session: no spawning caller -> user, shown.
        r = self._base(kind="bridge")
        assert r.resolved_interface == "acp"
        assert r.resolved_origin == "user"
        assert r.is_picker_hidden is False

    def test_bridge_with_caller_is_delegate_hidden(self):
        # An agent-spawned ACP session carries its caller worktree -> delegate.
        r = self._base(kind="bridge", caller_worktree="wt-parent")
        assert r.resolved_interface == "acp"
        assert r.resolved_origin == "delegate"
        assert r.is_picker_hidden is True

    # -- explicit stamp overrides derivation ----------------------------------

    def test_explicit_origin_overrides_caller_heuristic(self):
        # agent-bridge (Phase 2) stamps the authoritative origin: a bridge
        # worktree with a caller but an explicit origin=user stays shown.
        r = self._base(kind="bridge", caller_worktree="wt-parent", origin="user")
        assert r.resolved_origin == "user"
        assert r.is_picker_hidden is False

    def test_explicit_delegate_on_session_hides_it(self):
        r = self._base(kind="session", origin="delegate")
        assert r.resolved_origin == "delegate"
        assert r.is_picker_hidden is True

    def test_explicit_interface_overrides_kind(self):
        r = self._base(kind="session", interface="acp")
        assert r.resolved_interface == "acp"

    def test_invalid_stamps_fall_back_to_derivation(self):
        r = self._base(kind="session", interface="bogus", origin="bogus")  # type: ignore[arg-type]
        # Raw invalid values still derive cleanly.
        assert r.resolved_interface == "cli"
        assert r.resolved_origin == "user"

    # -- persistence ----------------------------------------------------------

    def test_stamped_marks_round_trip(self, tmp_path: Path):
        create_new_record(
            "b1", "worktree/b1", "/tmp/b1", "r", "m", "wsl", tmp_path,
            kind="bridge", interface="acp", origin="user",
        )
        loaded = load_record(tmp_path / "b1.yaml")
        assert loaded.interface == "acp"
        assert loaded.origin == "user"
        assert loaded.resolved_origin == "user"
        assert loaded.is_picker_hidden is False

    def test_unstamped_session_yaml_omits_marks(self, tmp_path: Path):
        # A plain session record stays lean: no interface/origin keys emitted
        # (values derive), so legacy YAMLs are byte-stable.
        create_new_record(
            "s1", "worktree/s1", "/tmp/s1", "r", "m", "wsl", tmp_path,
        )
        text = (tmp_path / "s1.yaml").read_text()
        assert "interface:" not in text
        assert "origin:" not in text
        # ...yet they still resolve.
        loaded = load_record(tmp_path / "s1.yaml")
        assert loaded.resolved_interface == "cli"
        assert loaded.resolved_origin == "user"


class TestSetDisposition:
    """worktree-status-core: the set_disposition helper (write path)."""

    def _rec(self, **kw):
        base = dict(
            worktree_id="wt-d", branch="b", worktree_path="/tmp/d",
            repo="r", machine="m", platform="wsl",
            started_at="2026-07-15T00:00:00", last_resumed_at="2026-07-15T00:00:00",
            resume_count=0, title="t", status="active", completed_at=None,
        )
        base.update(kw)
        return WorktreeRecord(**base)

    def test_set_follow_up_and_summary(self, tmp_path: Path, monkeypatch):
        rec = self._rec()
        p = tmp_path / "wt.yaml"
        monkeypatch.setattr("agent_worktrees.tracking.save_record",
                            lambda record, path=None: save_record(record, p))
        set_disposition(rec, summary="work left", follow_up=True)
        loaded = load_record(p)
        assert loaded.follow_up is True
        assert loaded.summary == "work left"
        assert loaded.status_note_at  # stamped

    def test_partial_update_preserves_other_field(self, tmp_path: Path, monkeypatch):
        rec = self._rec(follow_up=True, summary="old")
        p = tmp_path / "wt.yaml"
        monkeypatch.setattr("agent_worktrees.tracking.save_record",
                            lambda record, path=None: save_record(record, p))
        # summary-only update keeps the follow_up flag
        set_disposition(rec, summary="new")
        loaded = load_record(p)
        assert loaded.summary == "new"
        assert loaded.follow_up is True
        # --resolved (follow_up=False) keeps the summary
        set_disposition(loaded, follow_up=False)
        again = load_record(p)
        assert again.follow_up is False
        assert again.summary == "new"

    def test_set_title_updates_and_preserves_disposition(self, tmp_path: Path, monkeypatch):
        rec = self._rec(follow_up=True, summary="keep me")
        p = tmp_path / "wt.yaml"
        monkeypatch.setattr("agent_worktrees.tracking.save_record",
                            lambda record, path=None: save_record(record, p))
        # title-only update rewrites the headline label, leaving summary/follow_up.
        set_disposition(rec, title="New focus: nudge subsystem")
        loaded = load_record(p)
        assert loaded.title == "New focus: nudge subsystem"
        assert loaded.summary == "keep me"
        assert loaded.follow_up is True
        assert loaded.status_note_at  # a title write also stamps status_note_at
        assert loaded.title_asserted is True  # an explicit --title is authoritative
        # An all-whitespace title clears back to None (no empty headline) and
        # re-enables auto-derivation from the session summary.
        set_disposition(loaded, title="   ")
        cleared = load_record(p)
        assert cleared.title is None
        assert cleared.title_asserted is False

    def test_title_asserted_round_trips(self, tmp_path: Path, monkeypatch):
        rec = self._rec()
        p = tmp_path / "wt.yaml"
        monkeypatch.setattr("agent_worktrees.tracking.save_record",
                            lambda record, path=None: save_record(record, p))
        # A summary-only write must NOT assert the title (auto-derive still allowed).
        set_disposition(rec, summary="s")
        assert load_record(p).title_asserted is False
        assert "title_asserted" not in p.read_text()  # emitted only when True
        # Asserting a title flips + persists the marker.
        set_disposition(load_record(p), title="hand-set")
        assert "title_asserted: true" in p.read_text()
        assert load_record(p).title_asserted is True

    def test_set_disposition_caps_long_title(self, tmp_path: Path, monkeypatch):
        from agent_worktrees.tracking import TITLE_MAX
        rec = self._rec()
        p = tmp_path / "wt.yaml"
        monkeypatch.setattr("agent_worktrees.tracking.save_record",
                            lambda record, path=None: save_record(record, p))
        long_title = "Session a900: harness improvements (nudge, history, clobber fix)"
        set_disposition(rec, title=long_title)
        stored = load_record(p).title
        assert len(stored) <= TITLE_MAX
        assert stored.endswith("\u2026")            # truncated with an ellipsis
        assert stored.startswith("Session a900")     # keeps the leading text
        assert load_record(p).title_asserted is True

    def test_summary_strips_illegal_controls_before_write(
        self, tmp_path: Path, monkeypatch
    ):
        rec = self._rec()
        p = tmp_path / "wt.yaml"
        monkeypatch.setattr(
            "agent_worktrees.tracking.save_record",
            lambda record, path=None: save_record(record, p),
        )

        set_disposition(
            rec,
            summary=" \x00alpha\x07\tbeta\nline\rend\x0b\x0c\x1f\x7f ",
        )

        assert rec.summary == "alpha\tbeta line\rend"
        raw = p.read_bytes()
        assert not any(
            byte in raw
            for byte in (*range(0x09), 0x0B, 0x0C, *range(0x0E, 0x20), 0x7F)
        )
        assert b"\t" in raw
        assert b"\r" in raw


class TestCapTitle:
    """`cap_title` -- agent titles must fit the mux bar + Picker rows (TITLE_MAX)."""

    def test_none_and_whitespace_become_none(self):
        assert cap_title(None) is None
        assert cap_title("") is None
        assert cap_title("   ") is None

    def test_short_title_unchanged(self):
        assert cap_title("Fix relay port") == "Fix relay port"

    def test_terminal_whitespace_collapsed_and_stripped(self):
        assert cap_title("  fix\tthe\r\nbug  ") == "fix the bug"

    def test_illegal_controls_removed(self):
        assert cap_title("\x07Fix\x0b relay\x1f port\x7f") == "Fix relay port"

    def test_long_title_truncated_with_ellipsis(self):
        from agent_worktrees.tracking import TITLE_MAX
        out = cap_title("x" * 100)
        assert len(out) == TITLE_MAX
        assert out.endswith("\u2026")

    def test_boundary_exact_max_not_truncated(self):
        from agent_worktrees.tracking import TITLE_MAX
        exact = "y" * TITLE_MAX
        assert cap_title(exact) == exact  # == TITLE_MAX chars, no ellipsis


def test_strip_control_chars_preserves_yaml_whitespace():
    illegal = "".join(
        chr(code)
        for code in (*range(0x09), 0x0B, 0x0C, *range(0x0E, 0x20), 0x7F)
    )

    assert _strip_control_chars(None) is None
    assert _strip_control_chars(f"a{illegal}\tb\nc\rd") == "a\tb\nc\rd"


class TestForwardCompatContract:
    """The single-writer cross-layer contract (docs/architecture.md, the
    "Single-Writer Contract" invariant): a writer that touches ONE field via
    load_record -> save_record must preserve every OTHER orthogonal overlay
    untouched. Guards against a higher layer (or a future field) silently
    clobbering the ground-layer record. Add a field to WorktreeRecord? Extend
    the ``_full`` fixture below.
    """

    def _full(self):
        return WorktreeRecord(
            worktree_id="anomalous-potato-win-20260715-abcd",
            branch="worktree/x", worktree_path="/tmp/x", repo="r",
            machine="anomalous-potato", platform="wsl",
            started_at="2026-07-15T00:00:00", last_resumed_at="2026-07-15T00:00:00",
            resume_count=2, title="t", status="active", completed_at=None,
            interface="cli", origin="user",
            parent_session="sess-1", caller_worktree="anomalous-potato-win-caller",
            controller_revision=1,
            controllers=[ControllerRelation(
                kind="worktree",
                source="caller-worktree",
                controller_ref="anomalous-potato-win-caller#sess-1",
                controller_session_id="sess-1",
                relation_revision=1,
                created_at="2026-07-15T00:00:00",
            )],
            follow_up=True, summary="work left", status_note_at="2026-07-15T01:00:00",
            active_effort=ActiveEffort(
                path="efforts/active/durable-loop/README.md",
                participant="Driver",
                slice="Phase 2",
            ),
            effort_revision=3,
        )

    def _assert_overlays_intact(self, r):
        assert r.interface == "cli"
        assert r.origin == "user"
        assert r.parent_session == "sess-1"
        assert r.caller_worktree == "anomalous-potato-win-caller"
        assert r.controller_revision == 1
        assert r.controllers == [ControllerRelation(
            kind="worktree",
            source="caller-worktree",
            controller_ref="anomalous-potato-win-caller#sess-1",
            controller_session_id="sess-1",
            relation_revision=1,
            created_at="2026-07-15T00:00:00",
        )]
        assert r.follow_up is True
        assert r.summary == "work left"
        assert r.status_note_at
        assert r.active_effort == ActiveEffort(
            path="efforts/active/durable-loop/README.md",
            participant="Driver",
            slice="Phase 2",
        )
        assert r.effort_revision == 3

    def test_naive_load_save_preserves_all_overlays(self, tmp_path: Path):
        p = tmp_path / "wt.yaml"
        save_record(self._full(), p)
        loaded = load_record(p)
        save_record(loaded, p)
        self._assert_overlays_intact(load_record(p))

    def test_mark_resumed_preserves_overlays(self, tmp_path: Path, monkeypatch):
        p = tmp_path / "wt.yaml"
        save_record(self._full(), p)
        rec = load_record(p)
        # mark_resumed saves to the canonical yaml_path (no path arg); redirect
        # that internal save to the temp file.
        monkeypatch.setattr("agent_worktrees.tracking.save_record",
                            lambda record, path=None: save_record(record, p))
        mark_resumed(rec)
        reloaded = load_record(p)
        assert reloaded.resume_count == 3
        self._assert_overlays_intact(reloaded)

    def test_update_status_preserves_disposition(self, tmp_path: Path):
        p = tmp_path / "wt.yaml"
        save_record(self._full(), p)
        rec = load_record(p)
        rec.status = "finalized"
        save_record(rec, p)
        reloaded = load_record(p)
        assert reloaded.status == "finalized"
        assert reloaded.follow_up is True
        assert reloaded.summary == "work left"

# ---------------------------------------------------------------------------
# resolve_worktree_path -- authoritative path from the tracking record (#3026)
# ---------------------------------------------------------------------------

class TestResolveWorktreePath:
    """create-pr / push-changes / finalize / pr-complete must resolve a
    worktree's path from its tracking record's ``worktree_path`` (correct across
    layout changes) and only fall back to the ``worktree_root / id`` derivation
    when no usable record exists (#3026)."""

    def _record(self, worktree_id: str, worktree_path: str) -> WorktreeRecord:
        return WorktreeRecord(
            worktree_id=worktree_id,
            branch=f"worktree/{worktree_id}",
            worktree_path=worktree_path,
            repo="test-repo",
            machine="test-machine",
            platform="wsl",
            started_at="2026-06-01T10:00:00",
            last_resumed_at="2026-06-01T10:00:00",
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=None,
        )

    def test_prefers_recorded_path_over_derivation(
        self, tmp_path: Path, tmp_tracking_dir: Path, monkeypatch_config
    ):
        # Old-layout worktree that lives somewhere other than worktree_root/id.
        actual = tmp_path / "old-layout" / "test-chamber" / "wt-xyz"
        actual.mkdir(parents=True)
        worktree_root = str(tmp_path / "new-layout.worktrees")  # derivation misses
        save_record(self._record("wt-xyz", str(actual)),
                    tmp_tracking_dir / "wt-xyz.yaml")

        assert resolve_worktree_path("wt-xyz", worktree_root) == str(actual)

    def test_falls_back_to_derivation_without_record(
        self, tmp_path: Path, monkeypatch_config
    ):
        worktree_root = str(tmp_path / "roots")
        assert (
            resolve_worktree_path("untracked", worktree_root)
            == str(Path(worktree_root) / "untracked")
        )

    def test_falls_back_when_recorded_path_missing_on_disk(
        self, tmp_path: Path, tmp_tracking_dir: Path, monkeypatch_config
    ):
        # A record whose recorded path no longer exists must NOT be returned --
        # callers' "path not found" checks should still fire on the derivation.
        save_record(self._record("wt-missing", str(tmp_path / "gone")),
                    tmp_tracking_dir / "wt-missing.yaml")
        worktree_root = str(tmp_path / "roots")

        assert (
            resolve_worktree_path("wt-missing", worktree_root)
            == str(Path(worktree_root) / "wt-missing")
        )

    def test_falls_back_when_record_has_empty_path(
        self, tmp_path: Path, tmp_tracking_dir: Path, monkeypatch_config
    ):
        save_record(self._record("wt-empty", ""),
                    tmp_tracking_dir / "wt-empty.yaml")
        worktree_root = str(tmp_path / "roots")

        assert (
            resolve_worktree_path("wt-empty", worktree_root)
            == str(Path(worktree_root) / "wt-empty")
        )


# ---------------------------------------------------------------------------
# resource-claims -- qualified refs + the outbound claim ledger
# ---------------------------------------------------------------------------

class TestClaimRefHelpers:
    """format_claim_ref / parse_claim_ref round-trip both the qualified and the
    bare (legacy same-repo) forms."""

    def test_qualified_round_trip(self):
        ref = format_claim_ref("anomalous-potato", "test-chamber", "wt-A", "sess1")
        assert ref == "anomalous-potato/test-chamber/wt-A#sess1"
        cr = parse_claim_ref(ref)
        assert cr == ClaimRef("wt-A", "anomalous-potato", "test-chamber", "sess1")
        assert cr.is_qualified and cr.canonical() == ref

    def test_qualified_without_session(self):
        ref = format_claim_ref("m1", "proj", "wt-B")
        assert ref == "m1/proj/wt-B"
        cr = parse_claim_ref(ref)
        assert cr.session is None and cr.is_qualified

    def test_bare_form_degrades(self):
        # A bare worktree id (legacy same-repo) parses with machine/project None
        # and formats back to just the id.
        assert format_claim_ref(None, None, "just-an-id") == "just-an-id"
        cr = parse_claim_ref("just-an-id")
        assert cr.worktree_id == "just-an-id"
        assert cr.machine is None and cr.project is None
        assert not cr.is_qualified

    def test_partial_machine_only_stays_bare(self):
        # machine without project cannot qualify -> bare form (no false split).
        assert format_claim_ref("m1", None, "wt") == "wt"

    def test_empty_ref_is_none(self):
        assert parse_claim_ref("") is None

    def test_worktree_id_with_slashes_preserved(self):
        # Defensive: a worktree_id is not expected to contain '/', but if it did
        # the remainder after machine/project is rejoined rather than lost.
        cr = parse_claim_ref("m/p/a/b")
        assert cr.machine == "m" and cr.project == "p" and cr.worktree_id == "a/b"

    def test_anchor_ref_round_trip(self):
        # format_anchor_ref uses the reserved @anchor sentinel; no grammar change.
        from agent_worktrees.tracking import ANCHOR_ID, format_anchor_ref
        ref = format_anchor_ref("anomalous-potato", "spo-core")
        assert ref == "anomalous-potato/spo-core/@anchor"
        cr = parse_claim_ref(ref)
        assert cr.worktree_id == ANCHOR_ID and cr.is_qualified and cr.is_anchor
        assert cr.canonical() == ref

    def test_is_anchor_only_for_sentinel(self):
        assert not parse_claim_ref("anomalous-potato/spo-core/wt-A").is_anchor
        assert parse_claim_ref("anomalous-potato/spo-core/@anchor").is_anchor
        # Bare @anchor (no machine/project) is still an anchor ref by id.
        assert parse_claim_ref("@anchor").is_anchor


class TestAnchorLedger:
    """load_or_create_anchor_record lazily materializes a repo's @anchor claim
    ledger and is idempotent."""

    def test_lazy_create_then_load(self, tmp_path: Path):
        from agent_worktrees.tracking import (
            ANCHOR_ID,
            load_or_create_anchor_record,
        )
        tdir = tmp_path / ".spo-core" / "worktrees"
        tdir.mkdir(parents=True, exist_ok=True)
        adir = tmp_path / "anchors" / "spo-core"
        adir.mkdir(parents=True, exist_ok=True)
        assert not (tdir / f"{ANCHOR_ID}.yaml").exists()
        rec = load_or_create_anchor_record(
            str(adir), "spo-core", "anomalous-potato", "wsl", tdir)
        assert rec.worktree_id == ANCHOR_ID and rec.pair_kind == "anchor"
        assert rec.repo == "spo-core" and rec.worktree_path == str(adir)
        assert (tdir / f"{ANCHOR_ID}.yaml").exists()

    def test_idempotent_returns_existing(self, tmp_path: Path):
        from agent_worktrees.tracking import (
            ResourceClaim,
            add_resource_claim,
            load_or_create_anchor_record,
        )
        tdir = tmp_path / ".spo-core" / "worktrees"
        tdir.mkdir(parents=True, exist_ok=True)
        adir = tmp_path / "anchors" / "spo-core"
        rec = load_or_create_anchor_record(
            str(adir), "spo-core", "anomalous-potato", "wsl", tdir)
        add_resource_claim(rec, ResourceClaim(
            kind="pr", ref="https://github.com/o/r/pull/9", state="active"),
            save=False)
        from agent_worktrees.tracking import ANCHOR_ID, save_record
        save_record(rec, tdir / f"{ANCHOR_ID}.yaml")
        # A second call returns the SAME ledger (claim preserved), not a fresh one.
        rec2 = load_or_create_anchor_record(
            str(adir), "spo-core", "anomalous-potato", "wsl", tdir)
        assert [c.ref for c in rec2.resources] == ["https://github.com/o/r/pull/9"]


class TestResourceClaimState:
    """ResourceClaim.is_live degrades unknown/absent state to live so a stray
    value never hides a claim from reap-safety."""

    def test_default_active_is_live(self):
        assert ResourceClaim(ref="x").is_live

    def test_released_not_live(self):
        assert not ResourceClaim(ref="x", state="released").is_live

    def test_at_rest_is_still_held_but_settled(self):
        # at-rest: the work is safe, but the claim is still held (live) and no
        # longer blocks finalize.
        c = ResourceClaim(ref="x", state="at-rest")
        assert c.is_live          # still held
        assert c.is_at_rest
        assert not c.is_unsettled  # settled -> does not block finalize

    def test_active_is_unsettled_blocks(self):
        assert ResourceClaim(ref="x").is_unsettled
        assert ResourceClaim(ref="x", state="active").is_unsettled

    def test_released_is_neither_held_nor_unsettled(self):
        c = ResourceClaim(ref="x", state="released")
        assert not c.is_live and not c.is_unsettled and not c.is_at_rest

    def test_at_rest_state_round_trips_through_yaml(self, tmp_path: Path):
        path = tmp_path / "wt.yaml"
        path.write_text(
            "worktree_id: w\nbranch: b\nworktree_path: /tmp/w\nrepo: r\n"
            "machine: m\nplatform: wsl\nstarted_at: t\nlast_resumed_at: t\n"
            "resume_count: 0\ntitle: null\nstatus: active\ncompleted_at: null\n"
            "resources:\n- kind: codespace\n  ref: cs-1\n  state: at-rest\n"
        )
        loaded = load_record(path)
        assert loaded.resources[0].state == "at-rest"
        assert loaded.resources[0].is_at_rest and not loaded.resources[0].is_unsettled

    def test_unknown_state_degrades_to_live(self, tmp_path: Path):
        # An unknown persisted state loads back as "active" (never hidden).
        path = tmp_path / "wt.yaml"
        path.write_text(
            "worktree_id: w\nbranch: b\nworktree_path: /tmp/w\nrepo: r\n"
            "machine: m\nplatform: wsl\nstarted_at: t\nlast_resumed_at: t\n"
            "resume_count: 0\ntitle: null\nstatus: active\ncompleted_at: null\n"
            "resources:\n- kind: worktree\n  ref: m/p/w2\n  state: bogus\n"
        )
        loaded = load_record(path)
        assert loaded.resources[0].state == "active"
        assert loaded.resources[0].is_live


class TestAddResourceClaim:
    """add_resource_claim journals + dedups outbound claims by ref."""

    def _rec(self, tmp_path: Path) -> WorktreeRecord:
        return create_new_record(
            "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "test-chamber",
            "anomalous-potato", "wsl", tmp_path,
        )

    def test_append_and_persist(self, tmp_path: Path):
        rec = self._rec(tmp_path)
        claim = ResourceClaim(kind="worktree", ref="anomalous-potato/copilot-extensions/wt-B")
        add_resource_claim(rec, claim, save=False)
        save_record(rec, tmp_path / "wt-A.yaml")
        loaded = load_record(tmp_path / "wt-A.yaml")
        assert [c.ref for c in loaded.resources] == ["anomalous-potato/copilot-extensions/wt-B"]

    def test_dedup_by_ref_refreshes(self, tmp_path: Path):
        rec = self._rec(tmp_path)
        ref = "anomalous-potato/copilot-extensions/wt-B"
        add_resource_claim(rec, ResourceClaim(kind="worktree", ref=ref, note="first"),
                           save=False)
        add_resource_claim(rec, ResourceClaim(kind="worktree", ref=ref, state="released",
                                              note="second"), save=False)
        assert len(rec.resources) == 1
        assert rec.resources[0].state == "released"
        assert rec.resources[0].note == "second"

    def test_complete_session_worktree_can_add_claim(self, tmp_path: Path):
        rec = self._rec(tmp_path)
        rec.status = "complete"

        add_resource_claim(
            rec,
            ResourceClaim(kind="worktree", ref="host/repo/wt-B"),
            save=False,
        )

        assert [claim.ref for claim in rec.resources] == ["host/repo/wt-B"]

    def test_complete_managed_worktree_rejects_claim(self, tmp_path: Path):
        rec = self._rec(tmp_path)
        rec.kind = "bridge"
        rec.status = "complete"

        with pytest.raises(ValueError, match="creator ownership is frozen"):
            add_resource_claim(
                rec,
                ResourceClaim(kind="worktree", ref="host/repo/wt-B"),
                save=False,
            )

    def test_stamp_owner_ref_via_create(self, tmp_path: Path):
        # create_new_record stamps the backward owner link on the resource.
        create_new_record(
            "wt-B", "worktree/wt-B", str(tmp_path / "wt-B"), "copilot-extensions",
            "anomalous-potato", "wsl", tmp_path,
            owner_ref="anomalous-potato/test-chamber/wt-A#s1",
        )
        loaded = load_record(tmp_path / "wt-B.yaml")
        assert loaded.owner_ref == "anomalous-potato/test-chamber/wt-A#s1"
        assert loaded.owner_claim_ref.worktree_id == "wt-A"
