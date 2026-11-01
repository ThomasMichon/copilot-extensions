"""Real CLI/store tests for the explicit context-qualified execution mode."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from test_agenda_store import AGENDA, STORE, SKILL, PUBLISH, proof, request
from test_agenda_faults import provider_fixture as provider_fixture


CAPS = {"max_depth": 4, "max_pending_items": 32, "max_deferred_frames": 4, "max_no_parent_progress": 3}


def definition(identity: str) -> dict[str, Any]:
    return {"source_ref": f"fixture/{identity}", "scope_ref": "assigned-fixture",
        "action": f"Run {identity}", "exit": [f"{identity} result recorded"],
        "on": {"done": "advance", "blocked": "wait", "no-progress": "replan"},
        "relevant_subjects": ["dataset"]}


def board() -> dict[str, Any]:
    return {"version": 2, "source": {"kind": "task", "ref": "fixture-assignment"}, "revision": 1,
        "items": {identity: definition(identity) for identity in
                  ("parent", "sibling-a", "sibling-b", "investigate-a", "investigate-b", "nested")},
        "order": ["parent", "sibling-a", "sibling-b"]}


def context(value: Any, evidence_ref: str = "fixture-input") -> dict[str, Any]:
    return {"facts": [{"kind": "input", "subject": "dataset", "value": value, "evidence_ref": evidence_ref}]}


def v2(change: dict[str, Any], revision: int, identity: str) -> dict[str, Any]:
    return {"schema": "efforts.agenda-request", "version": 2, "mode": "context-qualified",
            "expected_revision": revision, "operation_id": identity, "change": change}


@pytest.fixture
def lane(tmp_path: Path) -> Path:
    change = {"operation": "init", "worktree": "fixture-lane", "board": board(),
              "baseline_context": context({"content": "initial"}), "resource_caps": CAPS}
    STORE.mutate(tmp_path, v2(change, 0, "init"), session="fixture-head")
    return tmp_path


def current(home: Path) -> tuple[str, str, str]:
    cursor = STORE.read(home)["documents"]["cursor"]
    frame = cursor["frames"][-1]
    item = cursor["frame_state"][frame]["pending"][0]
    return frame, item, cursor["item_state"][item]["current_attempt_id"]


def update(home: Path, change: dict[str, Any]) -> dict[str, Any]:
    revision = STORE.read(home)["revision"]
    return STORE.mutate(home, v2(change, revision, f"op-{revision}"), session="fixture-head")[0]


def outcome(home: Path, value: str = "no-progress") -> dict[str, Any]:
    frame, item, attempt = current(home)
    snapshot = STORE.read(home)["snapshot"]
    return update(home, {"operation": "outcome", "frame_id": frame, "item_id": item,
        "attempt_id": attempt, "outcome": value, "evidence": proof(snapshot["exit_criteria"][0])})


def revisit(home: Path, value: Any, causal: str = "Changed relevant input enables the same command.") -> dict[str, Any]:
    frame, item, attempt = current(home)
    return update(home, {"operation": "revisit", "frame_id": frame, "item_id": item,
        "predecessor_attempt_id": attempt, "context": context(value),
        "judgment": {"qualification": "agent-judgment", "changed_subjects": ["dataset"],
                     "causal_change": causal, "evidence_refs": ["fixture-input"]},
        "approach": {"action": "Run parent"}})


def expand(home: Path, refs: list[str]) -> dict[str, Any]:
    frame, item, attempt = current(home)
    return update(home, {"operation": "expand", "frame_id": frame, "item_id": item, "attempt_id": attempt,
        "items": [{"definition_ref": identity} for identity in refs],
        "continuation": {"parent_item_id": item, "next_action": "Reassess parent with child evidence"},
        "evidence": proof("bounded investigation")})


def test_v2_fresh_init_real_cli_read_and_history(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text(json.dumps(v2({"operation": "init", "worktree": "fixture-lane", "board": board(),
        "baseline_context": context({"content": "initial"}), "resource_caps": CAPS}, 0, "cli-init")))
    def invoke(command: str, *args: str) -> dict[str, Any]:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run([sys.executable, "-B", str(SKILL / "scripts" / "agenda_cli.py"),
            command, "--state-home", str(tmp_path), *args], capture_output=True, text=True,
            timeout=10, creationflags=flags)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    result = invoke("init", "--mode", "context-qualified", "--request", str(path))
    assert result["result"]["transition"]["attempt_id"]
    state = invoke("read")
    assert state["snapshot"]["cursor_version"] == 4
    assert set(state["snapshot"]) == {
        "worktree", "revision", "status", "root", "stack", "next_action", "exit_criteria",
        "declared_outcomes", "queued", "parked", "notes", "cursor_version", "deferred",
        "due_rechecks", "next_revisit_at"}
    assert len(state["records"]) > 3 and len(invoke("history")["history"]) == 1
    assert len(state["documents"]["cursor"]["frame_state"][state["documents"]["cursor"]["frames"][0]]["pending"]) == 3


def test_v2_productive_same_command_exceeds_old_visit_threshold(lane: Path) -> None:
    frame, item, _ = current(lane)
    original = STORE.read(lane)["records"]
    for value in range(1, 6):
        outcome(lane)
        result = revisit(lane, {"content": f"corrected-{value}"})
        assert result["transition"]["decision"] == "revisit-admitted"
    state = STORE.read(lane)
    assert state["documents"]["cursor"]["item_state"][item]["visits"] == 6
    assert all(state["records"][identity] == value for identity, value in original.items())
    assert state["documents"]["cursor"]["frames"] == [frame]
    for attempt in state["documents"]["cursor"]["item_state"][item]["attempts"][1:]:
        assert state["records"][attempt]["body"]["approach"]["action"] == "Run parent"


@pytest.mark.parametrize("value", [
    {"content": "initial"}, {"content": " initial "},
    {"content": "initial", "uuid": "new", "timestamp": "later", "session": "new"},
])
def test_v2_cosmetic_context_replans_without_a_new_attempt(lane: Path, value: Any) -> None:
    outcome(lane)
    _, item, previous = current(lane)
    result = revisit(lane, value, "Different causal prose cannot make identical facts novel.")
    assert result["transition"]["decision"] == "not-admitted"
    state = STORE.read(lane)
    assert state["snapshot"]["status"] == "replanning"
    assert state["documents"]["cursor"]["item_state"][item]["current_attempt_id"] == previous
    assert state["documents"]["cursor"]["item_state"][item]["visits"] == 1


def test_v2_nested_subqueues_preserve_two_siblings_and_parent_attempt(lane: Path) -> None:
    root_frame, root_item, original_attempt = current(lane)
    siblings = list(STORE.read(lane)["documents"]["cursor"]["frame_state"][root_frame]["pending"][1:])
    outcome(lane)
    first = expand(lane, ["investigate-a", "investigate-b"])["transition"]
    child_frame, child_item, _ = current(lane)
    second = expand(lane, ["nested"])["transition"]
    outcome(lane, "done")
    update(lane, {"operation": "return", "frame_id": second["frame_id"],
        "expansion_id": second["expansion_id"], "acceptance_ref": "nested-accepted",
        "evidence": proof("nested acceptance")})
    assert current(lane)[1] == child_item
    outcome(lane, "done")
    assert current(lane)[1] != child_item
    outcome(lane, "done")
    update(lane, {"operation": "return", "frame_id": child_frame,
        "expansion_id": first["expansion_id"], "acceptance_ref": "child-queue-accepted",
        "evidence": proof("child acceptance")})
    assert current(lane) == (root_frame, root_item, original_attempt)
    state = STORE.read(lane)
    assert state["documents"]["cursor"]["frame_state"][root_frame]["pending"][1:] == siblings
    assert state["snapshot"]["status"] == "replanning"
    assert state["documents"]["cursor"]["item_state"][root_item]["visits"] == 1


def test_v2_completed_items_cannot_be_revisited_or_relabelled(lane: Path) -> None:
    frame, item, attempt = current(lane)
    outcome(lane, "done")
    before = STORE.read(lane)
    with pytest.raises(AGENDA.AgendaError):
        update(lane, {"operation": "revisit", "frame_id": frame, "item_id": item,
            "predecessor_attempt_id": attempt, "context": context({"content": "changed"}),
            "judgment": {"qualification": "agent-judgment", "changed_subjects": ["dataset"],
                         "causal_change": "changed", "evidence_refs": ["input"]},
            "approach": {"action": "Run parent"}})
    assert STORE.read(lane) == before
    with pytest.raises(AGENDA.AgendaError):
        update(lane, {"operation": "append", "frame_id": frame,
            "items": [{"definition_ref": "parent"}], "assignment_ref": "same item new ID",
            "evidence": proof()})
    assert STORE.read(lane) == before


def test_v2_mixed_mode_writer_refuses_without_replacing_current_state(lane: Path) -> None:
    before = STORE.read(lane)
    with pytest.raises(AGENDA.AgendaError, match="policy versions disagree"):
        STORE.mutate(lane, request({"operation": "park", "ref": "legacy", "reason": "not new mode"}, 1, "v1"))
    assert STORE.read(lane) == before


def test_v2_real_cli_queue_outcome_revisit_and_return(tmp_path: Path) -> None:
    def invoke(change: dict[str, Any], *, initial: bool = False) -> dict[str, Any]:
        revision = 0 if initial else STORE.read(tmp_path)["revision"]
        path = tmp_path / "cli-request.json"
        path.write_text(json.dumps(v2(change, revision, f"cli-{revision}")))
        argv = [sys.executable, "-B", str(SKILL / "scripts" / "agenda_cli.py"),
                "init" if initial else "update", "--state-home", str(tmp_path), "--request", str(path)]
        if initial:
            argv += ["--mode", "context-qualified"]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)["result"]
    invoke({"operation": "init", "worktree": "fixture-lane", "board": board(),
        "baseline_context": context({"content": "initial"}), "resource_caps": CAPS}, initial=True)
    frame, item, attempt = current(tmp_path)
    invoke({"operation": "outcome", "frame_id": frame, "item_id": item, "attempt_id": attempt,
            "outcome": "no-progress", "evidence": proof("parent result recorded")})
    expanded = invoke({"operation": "expand", "frame_id": frame, "item_id": item, "attempt_id": attempt,
        "items": [{"definition_ref": "investigate-a"}, {"definition_ref": "investigate-b"}],
        "continuation": {"parent_item_id": item, "next_action": "Reassess parent"}, "evidence": proof()})["transition"]
    for _ in range(2):
        child, head, child_attempt = current(tmp_path)
        criterion = STORE.read(tmp_path)["snapshot"]["exit_criteria"][0]
        invoke({"operation": "outcome", "frame_id": child, "item_id": head, "attempt_id": child_attempt,
                "outcome": "done", "evidence": proof(criterion)})
    invoke({"operation": "return", "frame_id": child, "expansion_id": expanded["expansion_id"],
            "acceptance_ref": "accepted child queue", "evidence": proof()})
    assert current(tmp_path) == (frame, item, attempt)
    result = invoke({"operation": "revisit", "frame_id": frame, "item_id": item,
        "predecessor_attempt_id": attempt, "context": context({"content": "changed"}),
        "judgment": {"qualification": "agent-judgment", "changed_subjects": ["dataset"],
                     "causal_change": "Corrected input enables the same validation.", "evidence_refs": ["input"]},
        "approach": {"action": "Run parent"}})
    assert result["transition"]["decision"] == "revisit-admitted"
    assert result["transition"]["attempt_id"] != attempt


@pytest.mark.parametrize("fault", ["unknown-subject", "duplicate-subject", "empty-value", "bad-kind", "bad-judgment"])
def test_v2_invalid_context_has_no_accepted_partial_mutation(lane: Path, fault: str) -> None:
    outcome(lane)
    frame, item, attempt = current(lane)
    candidate = context({"content": "changed"})
    judgment = {"qualification": "agent-judgment", "changed_subjects": ["dataset"],
                "causal_change": "Changed relevant input", "evidence_refs": ["input"]}
    if fault == "unknown-subject":
        candidate["facts"][0]["subject"] = "foreign"
    elif fault == "duplicate-subject":
        candidate["facts"].append(deepcopy(candidate["facts"][0]))
    elif fault == "empty-value":
        candidate["facts"][0]["value"] = {"timestamp": "later", "uuid": "new"}
    elif fault == "bad-kind":
        candidate["facts"][0]["kind"] = []
    else:
        judgment["qualification"] = "authenticated-by-label"
    before = STORE.read(lane)
    with pytest.raises(AGENDA.AgendaError):
        update(lane, {"operation": "revisit", "frame_id": frame, "item_id": item,
            "predecessor_attempt_id": attempt, "context": candidate, "judgment": judgment,
            "approach": {"action": "Run parent"}})
    assert STORE.read(lane) == before


def test_v2_removed_fact_and_evidence_ref_churn_replan(tmp_path: Path) -> None:
    configured = board()
    for item in configured["items"].values():
        item["relevant_subjects"].append("condition")
    baseline = context({"content": "initial"})
    baseline["facts"].append({"kind": "condition", "subject": "condition", "value": "unchanged",
                              "evidence_ref": "condition-reference"})
    STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture-lane", "board": configured,
        "baseline_context": baseline, "resource_caps": CAPS}, 0, "init"))
    outcome(tmp_path)
    frame, item, attempt = current(tmp_path)
    for candidate in (context({"content": "initial"}, "new-reference"), baseline):
        result = update(tmp_path, {"operation": "revisit", "frame_id": frame, "item_id": item,
            "predecessor_attempt_id": attempt, "context": candidate,
            "judgment": {"qualification": "agent-judgment", "changed_subjects": [],
                         "causal_change": "Removed or renamed evidence is not new content.", "evidence_refs": ["new"]},
            "approach": {"action": "Run parent"}})
        assert result["transition"]["decision"] == "not-admitted"
    assert STORE.read(tmp_path)["documents"]["cursor"]["item_state"][item]["visits"] == 1


def test_v2_refine_is_not_context_novelty_and_preserves_origins(lane: Path) -> None:
    outcome(lane)
    before = STORE.read(lane)
    update(lane, {"operation": "refine", "board_ref": "root", "expected_definition_revision": 1,
        "patches": [{"definition_id": "parent", "label": "New label", "action": "Cosmetic new wording"}],
        "evidence": proof()})
    assert all(STORE.read(lane)["records"][identity] == value for identity, value in before["records"].items())
    assert revisit(lane, {"content": "initial"})["transition"]["decision"] == "not-admitted"
    after = STORE.read(lane)
    with pytest.raises(AGENDA.AgendaError):
        update(lane, {"operation": "refine", "board_ref": "root", "expected_definition_revision": 2,
            "patches": [{"definition_id": "parent", "source_ref": "rewrite-original"}], "evidence": proof()})
    assert STORE.read(lane) == after


def test_v2_defer_recover_preserves_closed_attempt_and_rejects_second_recovery(lane: Path) -> None:
    outcome(lane)
    original = current(lane)
    records = STORE.read(lane)["records"]
    deferred = update(lane, {"operation": "defer", "frame_id": original[0],
        "decision_ref": "pending decision", "decision_packet_ref": "preserved packet",
        "revisit_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "evidence": proof()})["transition"]["deferred_id"]
    recovered = update(lane, {"operation": "recover", "deferred_id": deferred,
        "recheck_ref": "actual recheck", "acceptance_ref": "scope unchanged",
        "attempt_policy": "resume-only", "evidence": proof()})
    assert recovered["transition"]["attempt_id"] == original[2]
    assert current(lane) == original and STORE.read(lane)["snapshot"]["status"] == "replanning"
    assert all(STORE.read(lane)["records"][identity] == value for identity, value in records.items())
    before = STORE.read(lane)
    with pytest.raises(AGENDA.AgendaError):
        update(lane, {"operation": "recover", "deferred_id": deferred,
            "recheck_ref": "repeat", "acceptance_ref": "same old result new ID",
            "attempt_policy": "resume-only", "evidence": proof()})
    assert STORE.read(lane) == before


@pytest.mark.parametrize("cap", ["max_depth", "max_pending_items", "max_no_parent_progress"])
def test_v2_decomposition_resource_bound_reports_replanning(tmp_path: Path, cap: str) -> None:
    caps = {**CAPS, cap: 1 if cap != "max_pending_items" else 3}
    STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture-lane", "board": board(),
        "baseline_context": context({"content": "initial"}), "resource_caps": caps}, 0, "init"))
    outcome(tmp_path)
    first = expand(tmp_path, ["investigate-a"])
    if cap == "max_no_parent_progress":
        assert first["transition"]["decision"] == "expanded"
        nested = expand(tmp_path, ["nested"])
        result = nested
    else:
        result = first
    assert result["transition"]["decision"] == "not-admitted"
    assert STORE.read(tmp_path)["snapshot"]["status"] == "replanning"


def test_v2_atomic_failure_and_original_acceptance_survive_delivery_changes(lane: Path) -> None:
    import sqlite3
    before = STORE.read(lane)
    req = v2({"operation": "wait", "frame_id": current(lane)[0], "item_id": current(lane)[1],
        "reason_kind": "external", "reason_ref": "review", "next_action": "Observe review",
        "evidence": proof()}, 1, "atomic-wait")
    with sqlite3.connect(lane / STORE.DATABASE) as db:
        db.execute("CREATE TRIGGER fail_v2 BEFORE INSERT ON operations BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(AGENDA.AgendaError, match="injected"):
        STORE.mutate(lane, req, session="fixture-head")
    assert STORE.read(lane) == before
    with sqlite3.connect(lane / STORE.DATABASE) as db:
        db.execute("DROP TRIGGER fail_v2")
    accepted, fresh = STORE.mutate(lane, req, session="fixture-head")
    assert fresh
    assert PUBLISH.publish(lane, [])["state"] == "pending"
    assert STORE.mutate(lane, req, session="fixture-head") == (accepted, False)
    assert STORE.history(lane)[0] == accepted


def test_v2_actual_interrupted_writer_preserves_records_and_queue(lane: Path) -> None:
    before = STORE.read(lane)
    frame, item, _ = current(lane)
    req = v2({"operation": "wait", "frame_id": frame, "item_id": item,
        "reason_kind": "external", "reason_ref": "review", "next_action": "Observe review",
        "evidence": proof()}, 1, "interrupted")
    code = """
import json,os,sqlite3,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import agenda_store as store
class Interrupted(sqlite3.Connection):
 def execute(self,sql,*args,**kwargs):
  if sql.startswith("INSERT INTO operations"): os._exit(17)
  return super().execute(sql,*args,**kwargs)
connect=sqlite3.connect
store.sqlite3.connect=lambda *a,**kw: connect(*a,**kw,factory=Interrupted)
store.mutate(Path(sys.argv[2]),json.loads(sys.argv[3]),session="fixture-head")
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, str(SKILL / "scripts"), str(lane),
        json.dumps(req)], capture_output=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    assert result.returncode == 17, result.stderr
    assert STORE.read(lane) == before and len(STORE.history(lane)) == 1
    accepted, fresh = STORE.mutate(lane, req, session="fixture-head")
    assert fresh and accepted["revision"] == 2
    assert STORE.mutate(lane, req, session="fixture-head") == (accepted, False)


def test_v2_concurrent_context_updates_keep_one_history(lane: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    frame, item, _ = current(lane)
    def writer(identity: str) -> bool:
        try:
            STORE.mutate(lane, v2({"operation": "wait", "frame_id": frame, "item_id": item,
                "reason_kind": "external", "reason_ref": identity, "next_action": "Observe",
                "evidence": proof()}, 1, identity), session="fixture-head")
            return True
        except AGENDA.AgendaError as exc:
            assert "stale expected revision" in str(exc)
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(writer, ["writer-a", "writer-b"])) == [False, True]
    assert STORE.read(lane)["revision"] == 2 and len(STORE.history(lane)) == 2


def test_v2_tail_append_and_closed_frame_refusal(lane: Path) -> None:
    frame, _, _ = current(lane)
    pending = list(STORE.read(lane)["documents"]["cursor"]["frame_state"][frame]["pending"])
    appended = update(lane, {"operation": "append", "frame_id": frame,
        "items": [{"definition_ref": "nested"}], "assignment_ref": "assigned tail work", "evidence": proof()})
    cursor = STORE.read(lane)["documents"]["cursor"]
    assert cursor["frame_state"][frame]["pending"] == pending + appended["transition"]["item_ids"]
    for _ in range(4):
        outcome(lane, "done")
    assert STORE.read(lane)["snapshot"]["status"] == "complete"
    before = STORE.read(lane)
    with pytest.raises(AGENDA.AgendaError, match="completed frame"):
        update(lane, {"operation": "append", "frame_id": frame,
            "items": [{"definition_ref": "investigate-a"}], "assignment_ref": "new name", "evidence": proof()})
    assert STORE.read(lane) == before


def test_v2_cannot_fork_deferred_root_or_reassign_original_attempt(lane: Path) -> None:
    frame, item, attempt = current(lane)
    saved = update(lane, {"operation": "defer", "frame_id": frame, "decision_ref": "decision",
        "decision_packet_ref": "packet", "revisit_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "evidence": proof()})["transition"]["deferred_id"]
    before = STORE.read(lane)
    for change in ({"operation": "recover", "deferred_id": saved, "recheck_ref": "check",
                   "acceptance_ref": "same acceptance", "attempt_policy": "new-fork", "evidence": proof()},
                  {"operation": "outcome", "frame_id": frame, "item_id": item, "attempt_id": attempt,
                   "outcome": "done", "evidence": proof("parent result recorded")}):
        with pytest.raises(AGENDA.AgendaError):
            update(lane, change)
        assert STORE.read(lane) == before


def test_v2_new_ids_labels_and_fact_kind_do_not_make_context(lane: Path) -> None:
    outcome(lane)
    frame, item, attempt = current(lane)
    candidate = context({"content": "initial", "metadata": {"uuid": "brand-new"}, "evidence_ref": "new"})
    candidate["facts"][0]["kind"] = "observation"
    result = update(lane, {"operation": "revisit", "frame_id": frame, "item_id": item,
        "predecessor_attempt_id": attempt, "context": candidate,
        "judgment": {"qualification": "agent-judgment", "changed_subjects": [],
                     "causal_change": "New category and labels only.", "evidence_refs": ["new"]},
        "approach": {"action": "New cosmetic action"}})
    assert result["transition"]["decision"] == "not-admitted"


def test_v2_previous_used_context_cannot_be_recycled(lane: Path) -> None:
    outcome(lane)
    revisit(lane, {"content": "updated"})
    outcome(lane)
    result = revisit(lane, {"content": "initial"})
    assert result["transition"]["reason_code"] == "previously-used-context"


def test_v2_old_mode_store_rejects_new_mode_without_migration(tmp_path: Path) -> None:
    documents = {"cursor": json.loads((SKILL / "assets" / "cursor.example.json").read_text()),
                 "boards": {"agenda.json": json.loads((SKILL / "assets" / "agenda.example.json").read_text())}}
    STORE.mutate(tmp_path, request({"operation": "init", "documents": documents}, 0, "legacy-init"))
    before = STORE.read(tmp_path)
    with pytest.raises(AGENDA.AgendaError, match="policy versions disagree"):
        STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture",
            "board": board(), "baseline_context": context({"content": "initial"}),
            "resource_caps": CAPS}, 0, "not-a-migration"))
    assert STORE.read(tmp_path) == before


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable selected-interpreter fixture")
@pytest.mark.parametrize("unsupported", [False, True])
def test_v2_actual_committed_publisher_adapter_preserves_original_result(
    lane: Path, provider_fixture: dict[str, Any], unsupported: bool
) -> None:
    package_root = provider_fixture["package"].parent
    wrapper = package_root / "selected-python"
    wrapper.write_text(f"#!{sys.executable}\nimport sys\n"
        f"sys.path.insert(0,{str(package_root)!r})\n"
        "assert sys.argv[1:4]==['-I','-B','-c']\n"
        "code=sys.argv[4]; sys.argv=['-c',*sys.argv[5:]]\nexec(compile(code,'<selected-provider>','exec'))\n")
    wrapper.chmod(0o700)
    if unsupported:
        health = json.loads(provider_fixture["health"].read_text())
        health["capabilities"] = ["worktree-agendas-v1"]
        provider_fixture["health"].write_text(json.dumps(health))
    frame, item, _ = current(lane)
    req = v2({"operation": "wait", "frame_id": frame, "item_id": item,
        "reason_kind": "external", "reason_ref": "review", "next_action": "Observe",
        "evidence": proof()}, 1, "publish")
    accepted, _ = STORE.mutate(lane, req, session="fixture-head", publisher_configured=True)
    delivery = PUBLISH.publish(lane, [], provider_python=str(wrapper), provider_project="fixture-project")
    assert delivery["state"] == ("pending" if unsupported else "delivered")
    if unsupported:
        assert delivery["error"]["code"] == "unsupported"
        assert not provider_fixture["calls"].exists()
    else:
        calls = json.loads(provider_fixture["calls"].read_text())
        assert calls["payload"]["observation"] == STORE.delivery_record(lane)[1]
        assert calls["payload"]["observation"]["version"] == 2
        assert calls["payload"]["observation"]["snapshot"]["cursor_version"] == 4
    assert STORE.read(lane)["revision"] == 2
    assert STORE.history(lane)[0] == accepted
    assert STORE.mutate(lane, req, session="fixture-head", publisher_configured=True) == (accepted, False)


def test_v2_projection_preserves_ancestor_tail_and_literal_wait_return_action(lane: Path) -> None:
    root = current(lane)
    update(lane, {"operation": "wait", "frame_id": root[0], "item_id": root[1],
        "reason_kind": "external", "reason_ref": "review", "next_action": "Read exact pending review",
        "evidence": proof()})
    assert STORE.read(lane)["snapshot"]["next_action"] == "Read exact pending review"
    expanded = expand(lane, ["investigate-a"])["transition"]
    state = STORE.read(lane)
    assert any("Run sibling-a" in text for text in state["snapshot"]["queued"])
    assert any("Run sibling-b" in text for text in state["snapshot"]["queued"])
    outcome(lane, "done")
    update(lane, {"operation": "return", "frame_id": expanded["frame_id"],
        "expansion_id": expanded["expansion_id"], "acceptance_ref": "accepted child",
        "evidence": proof()})
    assert STORE.read(lane)["snapshot"]["next_action"] == "Reassess parent with child evidence"


def test_v2_actual_cli_assigns_other_root_while_original_chain_is_deferred(lane: Path) -> None:
    original = current(lane)
    outcome(lane)
    original_item = deepcopy(STORE.read(lane)["documents"]["cursor"]["item_state"][original[1]])
    saved = update(lane, {"operation": "defer", "frame_id": original[0],
        "decision_ref": "original-scope-decision", "decision_packet_ref": "original-decision-packet",
        "revisit_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "evidence": proof()})["transition"]["deferred_id"]
    frozen = deepcopy(STORE.read(lane)["records"][saved])
    snapshot = STORE.read(lane)["snapshot"]
    assert snapshot["root"] == "task:fixture-assignment" and snapshot["stack"] == []
    assert snapshot["deferred"][0]["id"] == saved and snapshot["next_revisit_at"]
    assert snapshot["deferred"][0]["decision_ref"] == "original-scope-decision"
    req = v2({"operation": "assign-root", "items": [{"definition_ref": "nested"}],
        "assignment_ref": "independently assigned other work",
        "baseline_context": context({"content": "other-work-input"}), "evidence": proof()},
        STORE.read(lane)["revision"], "assigned-other-root")
    path = lane / "assign-root.json"
    path.write_text(json.dumps(req))
    result = subprocess.run([sys.executable, "-B", str(SKILL / "scripts" / "agenda_cli.py"),
        "update", "--state-home", str(lane), "--request", str(path), "--session", "fixture-head"],
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    assert result.returncode == 0, result.stderr
    accepted = json.loads(result.stdout)["result"]
    assert accepted["transition"]["decision"] == "root-assigned"
    assert current(lane)[0] != original[0]
    outcome(lane, "done")
    state = STORE.read(lane)
    assert state["records"][saved] == frozen
    assert state["documents"]["cursor"]["item_state"][original[1]] == original_item
    assert state["snapshot"]["deferred"][0]["id"] == saved
    update(lane, {"operation": "recover", "deferred_id": saved, "recheck_ref": "actual scoped recheck",
        "acceptance_ref": "original authority requalified", "attempt_policy": "resume-only",
        "evidence": proof()})
    assert current(lane) == original
    assert STORE.read(lane)["records"][saved] == frozen
    assert STORE.mutate(lane, req, session="fixture-head") == (accepted, False)


def test_v2_due_projection_uses_real_deferred_time_and_preserved_root(lane: Path) -> None:
    from agenda_v2_projection import project
    frame, _, _ = current(lane)
    revisit_at = datetime.now(timezone.utc) + timedelta(hours=1)
    saved = update(lane, {"operation": "defer", "frame_id": frame, "decision_ref": "pending decision",
        "decision_packet_ref": "packet", "revisit_at": revisit_at.isoformat(), "evidence": proof()})
    state = STORE.read(lane)
    before = project(state["documents"], state["records"], now=revisit_at - timedelta(seconds=1))
    after = project(state["documents"], state["records"], now=revisit_at + timedelta(seconds=1))
    identity = saved["transition"]["deferred_id"]
    assert before.next_revisit_at == revisit_at.isoformat() and before.due_rechecks == []
    assert after.next_revisit_at is None and after.due_rechecks == [identity]
    assert after.deferred[0].due and after.deferred[0].trigger == "time"
    assert after.root == before.root == "task:fixture-assignment"
    assert "Recheck deferred branch" in after.next_action


@pytest.mark.parametrize("old_declares_alias", [False, True])
def test_v2_source_alias_cannot_mint_a_new_item_lineage(
    lane: Path, old_declares_alias: bool
) -> None:
    frame, _, _ = current(lane)
    proposed = definition("renamed-parent")
    if old_declares_alias:
        update(lane, {"operation": "refine", "board_ref": "root", "expected_definition_revision": 1,
            "patches": [{"definition_id": "parent", "source_aliases_append": [proposed["source_ref"]]}],
            "evidence": proof()})
    else:
        proposed["source_aliases"] = ["fixture/parent"]
    revision = STORE.read(lane)["documents"]["boards"]["root"]["revision"]
    update(lane, {"operation": "refine", "board_ref": "root", "expected_definition_revision": revision,
        "patches": [{"definition_id": "renamed-parent", "add_definition": proposed}], "evidence": proof()})
    before = STORE.read(lane)
    with pytest.raises(AGENDA.AgendaError, match="semantic item"):
        update(lane, {"operation": "append", "frame_id": frame,
            "items": [{"definition_ref": "renamed-parent"}], "assignment_ref": "cosmetic rename",
            "evidence": proof()})
    assert STORE.read(lane) == before


@pytest.mark.parametrize("corruption", ["missing-context", "self-predecessor", "foreign-predecessor",
    "context-cycle", "baseline-rewrite", "parent-action", "expansion-attempt", "result-item"])
def test_v2_record_crosslinks_refuse_corrupt_state(lane: Path, corruption: str) -> None:
    from agenda_v2_schema import validate
    first = current(lane)
    outcome(lane)
    revisit(lane, {"content": "corrected"})
    second = current(lane)
    expanded = expand(lane, ["nested"])["transition"]
    state = STORE.read(lane)
    documents, records = deepcopy(state["documents"]), deepcopy(state["records"])
    cursor = documents["cursor"]
    attempt = records[second[2]]["body"]
    if corruption == "missing-context":
        attempt["context_id"] = "missing-context"
    elif corruption == "self-predecessor":
        attempt["predecessor_attempt_id"] = second[2]
    elif corruption == "foreign-predecessor":
        attempt["predecessor_attempt_id"] = expanded["attempt_id"]
    elif corruption == "context-cycle":
        records[attempt["context_id"]]["body"]["parent_context_id"] = attempt["context_id"]
    elif corruption == "baseline-rewrite":
        cursor["item_state"][first[1]]["baseline_context_id"] = attempt["context_id"]
    elif corruption == "parent-action":
        cursor["frame_state"][expanded["frame_id"]]["parent_link"]["next_action"] = "Rewritten continuation"
    elif corruption == "expansion-attempt":
        records[expanded["expansion_id"]]["body"]["parent_attempt_id"] = expanded["attempt_id"]
    else:
        result = next(value["body"] for value in records.values() if value["kind"] == "result")
        result["item_id"] = expanded["item_ids"][0]
    with pytest.raises(AGENDA.AgendaError):
        validate(documents, records)
    assert STORE.read(lane) == state


def test_v2_other_authorized_root_deferral_bound_keeps_both_continuations(tmp_path: Path) -> None:
    caps = {**CAPS, "max_deferred_frames": 1}
    STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture", "board": board(),
        "baseline_context": context({"content": "initial"}), "resource_caps": caps}, 0, "init"))
    root = current(tmp_path)
    saved = update(tmp_path, {"operation": "defer", "frame_id": root[0], "decision_ref": "decision",
        "decision_packet_ref": "packet", "revisit_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "evidence": proof()})["transition"]["deferred_id"]
    update(tmp_path, {"operation": "assign-root", "items": [{"definition_ref": "nested"}],
        "assignment_ref": "authorized other assignment", "baseline_context": context({"content": "other"}),
        "evidence": proof()})
    active = current(tmp_path)
    result = update(tmp_path, {"operation": "defer", "frame_id": active[0], "decision_ref": "other decision",
        "decision_packet_ref": "other packet", "revisit_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "evidence": proof()})
    assert result["transition"]["reason_code"] == "deferred-resource-bound"
    assert current(tmp_path) == active
    assert STORE.read(tmp_path)["documents"]["cursor"]["deferred"][0]["id"] == saved


@pytest.mark.parametrize("other_root", [False, True])
def test_v2_deferred_append_refuses_before_mutation_then_recover_allows(
    lane: Path, other_root: bool
) -> None:
    root = current(lane)
    original_req = v2({"operation": "defer", "frame_id": root[0], "decision_ref": "decision",
        "decision_packet_ref": "packet", "revisit_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "evidence": proof()}, STORE.read(lane)["revision"], "defer-original")
    accepted, _ = STORE.mutate(lane, original_req, session="fixture-head")
    deferred_id = accepted["transition"]["deferred_id"]
    if other_root:
        update(lane, {"operation": "assign-root", "items": [{"definition_ref": "nested"}],
            "assignment_ref": "unrelated authorized work", "baseline_context": context({"content": "other"}),
            "evidence": proof()})
    before = STORE.read(lane)
    history = STORE.history(lane)
    with pytest.raises(AGENDA.AgendaError, match="recover deferred custody"):
        update(lane, {"operation": "append", "frame_id": root[0],
            "items": [{"definition_ref": "investigate-a"}], "assignment_ref": "later assigned work",
            "evidence": proof()})
    assert STORE.read(lane) == before and STORE.history(lane) == history
    assert STORE.mutate(lane, original_req, session="fixture-head") == (accepted, False)
    if other_root:
        outcome(lane, "done")
    update(lane, {"operation": "recover", "deferred_id": deferred_id, "recheck_ref": "actual recheck",
        "acceptance_ref": "current authority", "attempt_policy": "resume-only", "evidence": proof()})
    pending = list(STORE.read(lane)["documents"]["cursor"]["frame_state"][root[0]]["pending"])
    appended = update(lane, {"operation": "append", "frame_id": root[0],
        "items": [{"definition_ref": "investigate-a"}], "assignment_ref": "later assigned work",
        "evidence": proof()})
    assert STORE.read(lane)["documents"]["cursor"]["frame_state"][root[0]]["pending"] == (
        pending + appended["transition"]["item_ids"])


def test_v2_active_ancestor_tail_append_preserves_child_and_original_order(lane: Path) -> None:
    root = current(lane)
    pending = list(STORE.read(lane)["documents"]["cursor"]["frame_state"][root[0]]["pending"])
    expand(lane, ["investigate-a"])
    child = current(lane)
    appended = update(lane, {"operation": "append", "frame_id": root[0],
        "items": [{"definition_ref": "investigate-b"}], "assignment_ref": "authorized root tail",
        "evidence": proof()})
    assert current(lane) == child
    assert STORE.read(lane)["documents"]["cursor"]["frame_state"][root[0]]["pending"] == (
        pending + appended["transition"]["item_ids"])


@pytest.mark.parametrize("drift", ["pending-order", "attempt-counter", "item-action", "new-owned-item", "loosened-cap"])
def test_v2_structural_validation_refuses_only_deferred_owned_drift(lane: Path, drift: str) -> None:
    from agenda_v2_schema import validate
    root = current(lane)
    saved = update(lane, {"operation": "defer", "frame_id": root[0], "decision_ref": "decision",
        "decision_packet_ref": "packet", "revisit_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "evidence": proof()})["transition"]["deferred_id"]
    update(lane, {"operation": "assign-root", "items": [{"definition_ref": "nested"}],
        "assignment_ref": "other allowed work", "baseline_context": context({"content": "other"}),
        "evidence": proof()})
    state = STORE.read(lane)
    documents, records = deepcopy(state["documents"]), deepcopy(state["records"])
    cursor = documents["cursor"]
    if drift == "pending-order":
        cursor["frame_state"][root[0]]["pending"].reverse()
    elif drift == "attempt-counter":
        cursor["item_state"][root[1]]["decompositions"] += 1
    elif drift == "item-action":
        cursor["item_state"][root[1]]["next_action"] = "Silently changed parked action"
    elif drift == "loosened-cap":
        cursor["resource_caps"]["max_pending_items"] += 1
    else:
        orphan = deepcopy(cursor["item_state"][root[1]])
        origin = deepcopy(records[orphan["origin_id"]])
        origin["id"], origin["body"]["item_id"] = "new-origin", "new-owned-item"
        origin["body"]["definition_id"] = "investigate-b"
        records["new-origin"] = origin
        orphan.update(origin_id="new-origin", current_definition_ref={"board": "root", "definition": "investigate-b"},
                      current_attempt_id=None, attempts=[], visits=0)
        cursor["item_state"]["new-owned-item"] = orphan
    with pytest.raises(AGENDA.AgendaError, match="deferred"):
        validate(documents, records)
    assert STORE.read(lane) == state
    assert state["documents"]["cursor"]["deferred"][0]["id"] == saved


def test_v2_child_return_does_not_reset_no_parent_progress_cap(tmp_path: Path) -> None:
    configured = board()
    configured["items"]["new-investigation"] = definition("new-investigation")
    caps = {**CAPS, "max_no_parent_progress": 1}
    STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture", "board": configured,
        "baseline_context": context({"content": "initial"}), "resource_caps": caps}, 0, "init"))
    outcome(tmp_path)
    root = current(tmp_path)
    expansion = expand(tmp_path, ["investigate-a"])["transition"]
    outcome(tmp_path, "done")
    update(tmp_path, {"operation": "return", "frame_id": expansion["frame_id"],
        "expansion_id": expansion["expansion_id"], "acceptance_ref": "child accepted", "evidence": proof()})
    assert current(tmp_path) == root
    assert STORE.read(tmp_path)["documents"]["cursor"]["item_state"][root[1]]["decompositions"] == 1
    blocked = expand(tmp_path, ["investigate-b"])
    assert blocked["transition"]["reason_code"] == "no-parent-progress-resource-bound"
    assert current(tmp_path) == root
    revisit(tmp_path, {"content": "relevant correction from child investigation"})
    assert STORE.read(tmp_path)["documents"]["cursor"]["item_state"][root[1]]["decompositions"] == 0
    admitted = expand(tmp_path, ["new-investigation"])
    assert admitted["transition"]["decision"] == "expanded"


@pytest.mark.parametrize(("initial", "replacement"), [
    (True, 1), (False, 0), (1, True), (0, False),
    ({"nested": [True, {"flag": False}]}, {"nested": [1, {"flag": 0}]}),
    ({"nested": [1, {"flag": 0}]}, {"nested": [True, {"flag": False}]}),
])
def test_v2_boolean_number_changes_are_type_aware(
    tmp_path: Path, initial: Any, replacement: Any
) -> None:
    STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture", "board": board(),
        "baseline_context": context(initial), "resource_caps": CAPS}, 0, "init"))
    outcome(tmp_path)
    result = revisit(tmp_path, replacement)
    assert result["transition"]["decision"] == "revisit-admitted"
    state = STORE.read(tmp_path)
    assert state["records"][result["transition"]["context_id"]]["body"]["delta"]["changed"] == ["dataset"]
    outcome(tmp_path)
    recycled = revisit(tmp_path, initial)
    assert recycled["transition"]["reason_code"] == "previously-used-context"


@pytest.mark.parametrize("value", [True, False, 1, 0, {"nested": [True, False, 1, 0]}])
def test_v2_typed_values_with_metadata_churn_remain_duplicate(tmp_path: Path, value: Any) -> None:
    baseline = {"payload": value}
    STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture", "board": board(),
        "baseline_context": context(baseline), "resource_caps": CAPS}, 0, "init"))
    outcome(tmp_path)
    result = revisit(tmp_path, {"payload": value, "uuid": "new-uuid", "timestamp": "later"})
    assert result["transition"]["decision"] == "not-admitted"


def test_v2_record_semantic_comparison_refuses_boolean_numeric_substitution(lane: Path) -> None:
    from agenda_v2_schema import validate
    state = STORE.read(lane)
    _, item, _ = current(lane)
    baseline_id = state["documents"]["cursor"]["item_state"][item]["baseline_context_id"]
    documents, records = deepcopy(state["documents"]), deepcopy(state["records"])
    baseline = records[baseline_id]["body"]
    baseline["facts"] = context({"flag": True})["facts"]
    baseline["semantic"] = {"dataset": {"value": {"flag": 1}}}
    with pytest.raises(AGENDA.AgendaError, match="semantic facts"):
        validate(documents, records)


def test_v2_oversized_publish_with_documented_provider_options_keeps_original_reason(
    lane: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from test_agenda_store import CLI

    frame, item, _ = current(lane)
    req = v2({"operation": "wait", "frame_id": frame, "item_id": item,
        "reason_kind": "external", "reason_ref": "bounded fixture",
        "next_action": "x" * 33000, "evidence": proof()}, 1, "oversized-new-mode")
    accepted, fresh = STORE.mutate(lane, req, session="fixture-head", publisher_configured=True)
    assert fresh and accepted["delivery"]["error"]["code"] == "observation-too-large"
    calls = []

    async def forbidden(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((args, kwargs))
        pytest.fail("oversized observation invoked a provider")

    monkeypatch.setattr(PUBLISH, "_invoke", forbidden)
    assert CLI.main(["publish", "--state-home", str(lane),
        "--publisher-python", sys.executable, "--publisher-project", "fixture-project"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["persisted"] is True
    assert output["delivery"]["state"] == "pending"
    assert output["delivery"]["error"]["code"] == "observation-too-large"
    assert calls == []
    assert STORE.read(lane)["revision"] == 2 and STORE.read(lane)["delivery"] == output["delivery"]
    assert STORE.history(lane)[0] == accepted
    assert STORE.mutate(lane, req, session="fixture-head", publisher_configured=True) == (accepted, False)


def test_v2_actual_publisher_outer_adapter_is_isolated_from_shadow_cwd(
    lane: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import venv

    # Bind the synthetic provider via the selected interpreter's own site context.
    # Production code never consumes this fixture path or ambient PYTHONPATH.
    isolated = lane / "selected-test-context"
    venv.EnvBuilder(with_pip=False).create(isolated)
    interpreter = isolated / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    purelib = subprocess.run([str(interpreter), "-I", "-B", "-c",
        "import sysconfig; print(sysconfig.get_path('purelib'))"], capture_output=True,
        text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
    assert purelib.returncode == 0, purelib.stderr
    library = Path(purelib.stdout.strip())
    package = library / "agent_worktrees"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "agenda_contract.py").write_text("CONTRACT_VERSIONS=(1,2)\n")
    (package / "__main__.py").write_text(
        "import argparse\n"
        "def build_parser():\n"
        " p=argparse.ArgumentParser(); s=p.add_subparsers(dest='command'); a=s.add_parser('agenda-publish')\n"
        " a.add_argument('--contract-version',type=int); a.add_argument('--json',action='store_true'); return p\n")
    (package / "worktrees_client.py").write_text(
        "def read_health(): return {'capabilities':['worktree-agendas-v2'],"
        "'worktrees':{'status':'ready','generation':'a'*32}}\n"
        "class Client:\n"
        " def _request(self, action, payload, **kw):\n"
        "  body=payload['observation']; source=body['source']\n"
        "  return {'ok':True,'contract_version':2,'generation':payload['expected_generation'],"
        "'sequence':3,'project':payload['project'],'worktree_id':source['worktree'],"
        "'session_id':source['session'],'availability':'observed','observation':body}\n"
        "def _control_client(): return Client()\n")
    shadow = lane / "shadow-cwd"
    shadow.mkdir()
    touched = lane / "unexpected-shadow-execution"
    malicious = f"open({str(touched)!r},'w').write('executed')\nraise RuntimeError('shadow import')\n"
    (shadow / "json.py").write_text(malicious)
    (shadow / "re.py").write_text(malicious)
    fake_package = shadow / "agent_worktrees"
    fake_package.mkdir()
    (fake_package / "__init__.py").write_text(malicious)
    monkeypatch.chdir(shadow)
    monkeypatch.setenv("PYTHONPATH", str(shadow))
    result = PUBLISH.publish(lane, [], provider_python=str(interpreter), provider_project="fixture-project")
    assert result["state"] == "delivered"
    assert not touched.exists()
    assert STORE.read(lane)["revision"] == 1


@pytest.mark.parametrize(("initial", "replacement"), [
    (1, 1.0), (0.0, -0.0), (-0.0, 0), (int(1e40), 1e40),
    ({"nested": [1, {"zero": 0.0}]}, {"nested": [1.0, {"zero": -0.0}]}),
])
def test_v2_equivalent_numeric_context_does_not_create_attempt_or_reset_decomposition(
    tmp_path: Path, initial: Any, replacement: Any
) -> None:
    STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture", "board": board(),
        "baseline_context": context(initial), "resource_caps": CAPS}, 0, "init"))
    outcome(tmp_path)
    root = current(tmp_path)
    child = expand(tmp_path, ["nested"])["transition"]
    outcome(tmp_path, "done")
    update(tmp_path, {"operation": "return", "frame_id": child["frame_id"],
        "expansion_id": child["expansion_id"], "acceptance_ref": "bounded investigation accepted",
        "evidence": proof()})
    before = STORE.read(tmp_path)["documents"]["cursor"]["item_state"][root[1]]
    assert before["decompositions"] == 1
    result = revisit(tmp_path, replacement)
    assert result["transition"]["decision"] == "not-admitted"
    after = STORE.read(tmp_path)["documents"]["cursor"]["item_state"][root[1]]
    assert after["attempts"] == before["attempts"]
    assert after["current_attempt_id"] == root[2] and after["decompositions"] == 1


def test_v2_omitted_known_fact_readdition_is_not_new_information(tmp_path: Path) -> None:
    configured = board()
    for item in configured["items"].values():
        item["relevant_subjects"].append("condition")
    def candidate(value: str, include_condition: bool) -> dict[str, Any]:
        data = context({"input": value})
        if include_condition:
            data["facts"].append({"kind": "condition", "subject": "condition", "value": "ready",
                                  "evidence_ref": "same-condition-new-reference"})
        return data
    STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture", "board": configured,
        "baseline_context": candidate("A", True), "resource_caps": CAPS}, 0, "init"))
    outcome(tmp_path)
    frame, item, attempt = current(tmp_path)
    changed = update(tmp_path, {"operation": "revisit", "frame_id": frame, "item_id": item,
        "predecessor_attempt_id": attempt, "context": candidate("B", False),
        "judgment": {"qualification": "agent-judgment", "changed_subjects": ["dataset"],
                     "causal_change": "New B input enables another approach.", "evidence_refs": ["input-B"]},
        "approach": {"action": "Run parent"}})
    assert changed["transition"]["decision"] == "revisit-admitted"
    outcome(tmp_path)
    child = expand(tmp_path, ["nested"])["transition"]
    outcome(tmp_path, "done")
    update(tmp_path, {"operation": "return", "frame_id": child["frame_id"],
        "expansion_id": child["expansion_id"], "acceptance_ref": "child accepted", "evidence": proof()})
    before = STORE.read(tmp_path)["documents"]["cursor"]["item_state"][item]
    restored = update(tmp_path, {"operation": "revisit", "frame_id": frame, "item_id": item,
        "predecessor_attempt_id": before["current_attempt_id"], "context": candidate("B", True),
        "judgment": {"qualification": "agent-judgment", "changed_subjects": ["condition"],
                     "causal_change": "Restating an omitted known condition is not learning.", "evidence_refs": ["new-label"]},
        "approach": {"action": "Run parent"}})
    assert restored["transition"]["decision"] == "not-admitted"
    after = STORE.read(tmp_path)["documents"]["cursor"]["item_state"][item]
    assert after["attempts"] == before["attempts"] and after["decompositions"] == before["decompositions"] == 1
    actually_new = update(tmp_path, {"operation": "revisit", "frame_id": frame, "item_id": item,
        "predecessor_attempt_id": before["current_attempt_id"],
        "context": {"facts": [
            {"kind": "input", "subject": "dataset", "value": {"input": "B"}, "evidence_ref": "input-B"},
            {"kind": "condition", "subject": "condition", "value": "newly-unblocked", "evidence_ref": "condition-change"}]},
        "judgment": {"qualification": "agent-judgment", "changed_subjects": ["condition"],
                     "causal_change": "The condition actually changed, enabling the same command.",
                     "evidence_refs": ["condition-change"]},
        "approach": {"action": "Run parent"}})
    assert actually_new["transition"]["decision"] == "revisit-admitted"
    assert STORE.read(tmp_path)["documents"]["cursor"]["item_state"][item]["decompositions"] == 0


@pytest.mark.parametrize(("initial", "replacement"), [
    (2**53 + 1, float(2**53)),
    (10**100 + 1, 10**100),
    ({"nested": [2**53 + 1]}, {"nested": [float(2**53)]}),
    (False, 0.0), (True, 1.0),
])
def test_v2_exact_large_integer_and_boolean_numeric_changes_remain_substantive(
    tmp_path: Path, initial: Any, replacement: Any
) -> None:
    STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture", "board": board(),
        "baseline_context": context(initial), "resource_caps": CAPS}, 0, "init"))
    outcome(tmp_path)
    assert revisit(tmp_path, replacement)["transition"]["decision"] == "revisit-admitted"


@pytest.mark.parametrize(("initial", "known_return"), [
    (1, 1.0), (0.0, -0.0),
    ({"nested": [1, 0.0]}, {"nested": [1.0, -0.0]}),
])
def test_v2_historical_context_signatures_share_numeric_equivalence(
    tmp_path: Path, initial: Any, known_return: Any
) -> None:
    STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture", "board": board(),
        "baseline_context": context(initial), "resource_caps": CAPS}, 0, "init"))
    outcome(tmp_path)
    assert revisit(tmp_path, {"new": "substantive-input"})["transition"]["decision"] == "revisit-admitted"
    outcome(tmp_path)
    result = revisit(tmp_path, known_return)
    assert result["transition"]["reason_code"] == "previously-used-context"


def test_v2_nested_omitted_knowledge_readdition_cannot_reset_parent_budget(tmp_path: Path) -> None:
    STORE.mutate(tmp_path, v2({"operation": "init", "worktree": "fixture", "board": board(),
        "baseline_context": context({"nested": {"input": "A", "condition": "ready"}}),
        "resource_caps": CAPS}, 0, "init"))
    outcome(tmp_path)
    assert revisit(tmp_path, {"nested": {"input": "B"}})["transition"]["decision"] == "revisit-admitted"
    outcome(tmp_path)
    root = current(tmp_path)
    child = expand(tmp_path, ["nested"])["transition"]
    outcome(tmp_path, "done")
    update(tmp_path, {"operation": "return", "frame_id": child["frame_id"],
        "expansion_id": child["expansion_id"], "acceptance_ref": "child accepted", "evidence": proof()})
    result = revisit(tmp_path, {"nested": {"input": "B", "condition": "ready"}})
    assert result["transition"]["reason_code"] == "previously-known-facts"
    assert STORE.read(tmp_path)["documents"]["cursor"]["item_state"][root[1]]["decompositions"] == 1
