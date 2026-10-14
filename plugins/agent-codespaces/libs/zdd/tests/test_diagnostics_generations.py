"""Launcher/worker generation grouping in the shared daemon-health doctor.

A Windows venv redirector shows a daemon twice in a process census: the
redirector (launcher) and the base interpreter it starts (worker). These tests
pin that the doctor treats such a pair as ONE generation, never repairs one
half of the live owner's generation, and refuses every termination when the
grouping is uncertain.
"""

from __future__ import annotations

import dataclasses
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from zdd import breadcrumb, diagnostics, routing
from zdd.generations import group_generations, is_venv_redirector

LAUNCHER = "C:/rt/versions/1/venv/Scripts/python.exe"
WORKER = "C:/py/python.exe"


def _is_launcher(image: str) -> bool:
    return image == LAUNCHER


def _ctx(tmp_path: Path, state: dict, *, group: bool = True, is_superseded=None):
    def _candidates():
        return [
            diagnostics.DaemonCandidate(
                pid=pid, start_time=start,
                ppid=state["ppid"].get(pid, 1), image=state["image"].get(pid, WORKER),
                **state.get("parent", {}).get(pid, {}),
            )
            for pid, start in sorted(state["live"].items())
        ]

    def _terminate(pid, expected_start_time, *, exit_timeout=None):
        if state["live"].get(pid) != expected_start_time:
            return {"killed": False, "identity_verified": False}
        state["terminated"].append(pid)
        state["live"].pop(pid, None)
        return {"killed": True, "identity_verified": True, "exit_confirmed": True}

    return diagnostics.DiagnosticContext(
        service="test-daemon",
        config_dir=tmp_path,
        read_lock=lambda: state["lock"],
        lock_is_live=lambda data: data == state["lock"],
        list_candidates=_candidates,
        is_superseded=is_superseded or (lambda pid, generation: False),
        terminate_pid_if_identity=_terminate,
        abandoned_passive_grace_seconds=0.0,
        group_launchers=group,
        is_launcher_image=_is_launcher,
    )


def _pair_state(**extra) -> dict:
    """Owner generation: launcher 101 (redirector) -> worker 202 (base python)."""
    state = {
        "lock": {"pid": 202, "start_time": "1002"},
        "live": {101: "1001", 202: "1002"},
        "ppid": {202: 101},
        "image": {101: LAUNCHER},
        "terminated": [],
    }
    state.update(extra)
    return state


def _stale_breadcrumb(tmp_path: Path, *, new_pid: int, start: str) -> None:
    record = breadcrumb.write_breadcrumb(
        tmp_path, state="started", old={"bind": "127.0.0.1", "port": 9281},
        new_port=9282, new_pid=new_pid, new_process_start_time=start,
        publication_witness=routing.publication_witness(tmp_path, deadline=time.monotonic() + 1),
    )
    record = dict(record)
    record["updated_at"] = (datetime.now(timezone.utc) - timedelta(seconds=9999)).isoformat()
    (tmp_path / "cutover.json").write_text(json.dumps(record), encoding="utf-8")


def test_launcher_worker_pair_is_one_generation(tmp_path: Path):
    # The route records the launcher (the spawner's handle) while the lock
    # records the worker: both name the same, single, healthy generation.
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0")
    state = _pair_state()

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))

    assert report["candidate_count"] == 2
    assert report["generation_count"] == 1
    assert report["generations"] == [{"key": "202:1002", "worker_pid": 202, "launcher_pid": 101}]
    assert report["grouping_uncertain"] is False
    assert report["validated_owner"]["pid"] == 202
    assert report["validated_owner"]["generation_pids"] == [101, 202]
    assert report["findings"] == []

    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state))
    assert state["terminated"] == []
    assert result["actions"] == []


def test_two_generations_report_one_duplicate_naming_both_members(tmp_path: Path):
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0")
    state = _pair_state()
    state["live"].update({303: "1003", 404: "1004"})
    state["ppid"][404] = 303
    state["image"][303] = LAUNCHER

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))

    assert report["generation_count"] == 2
    (finding,) = report["findings"]
    assert finding["kind"] == "duplicate_resident"
    assert finding["generation_count"] == 2
    assert [(item["pid"], item["role"]) for item in finding["targets"]] == [
        (404, "worker"), (303, "launcher"),
    ]


def test_apply_never_targets_owner_generation_launcher(tmp_path: Path):
    # A stale breadcrumb names the live owner's launcher as an abandoned
    # passive. Without grouping, that launcher (and with it the worker) would
    # be terminated; it belongs to the owner generation, so it never is.
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=202, version="1.0.0")
    _stale_breadcrumb(tmp_path, new_pid=101, start="1001")
    state = _pair_state()

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))
    assert report["findings"] == []

    diagnostics.apply_daemon_health(_ctx(tmp_path, state))
    assert state["terminated"] == []
    assert set(state["live"]) == {101, 202}


def test_superseded_generation_matches_either_member(tmp_path: Path):
    # The superseded generation was routed by its launcher's PID (303).
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9280, pid=303, version="1.0.0")
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0",
        demote_existing=True,
    )
    state = _pair_state()
    state["live"].update({303: "1003", 404: "1004"})
    state["ppid"][404] = 303
    state["image"][303] = LAUNCHER

    report = diagnostics.audit_daemon_health(
        _ctx(tmp_path, state, is_superseded=lambda pid, generation: pid == 303)
    )

    kinds = [finding["kind"] for finding in report["findings"]]
    assert kinds == ["superseded_generation"], "no duplicate noise for the worker"
    assert [item["pid"] for item in report["findings"][0]["targets"]] == [404, 303]


def test_uncertain_grouping_blocks_every_termination(tmp_path: Path):
    # An abandoned passive that is otherwise reaped (custody proven) must not
    # be touched while any candidate's parent is unreadable.
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0")
    _stale_breadcrumb(tmp_path, new_pid=303, start="passive")
    state = {
        "lock": {"pid": 101, "start_time": "owner"},
        "live": {101: "owner", 303: "passive"},
        "ppid": {303: None},
        "image": {},
        "terminated": [],
    }

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))
    assert report["grouping_uncertain"] is True
    assert "parent of candidate 303 unreadable" in report["grouping_uncertain_reason"]
    (finding,) = report["findings"]
    assert finding["kind"] == "abandoned_passive"
    assert finding["repairable"] is False
    assert finding["blocked_reason"] == "generation grouping uncertain"

    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state))
    assert state["terminated"] == []
    assert result["actions"][-1] == {
        "kind": "generation_grouping", "blocked": True,
        "reason": "generation grouping uncertain",
    }


def test_targets_from_an_uncertain_census_are_never_used(tmp_path: Path):
    # The first census cannot read 303's parent; every later one can. The
    # abandoned-passive targets apply would act on come from that first,
    # uncertain census, so nothing is terminated in this run.
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0")
    _stale_breadcrumb(tmp_path, new_pid=303, start="passive")
    state = {
        "lock": {"pid": 101, "start_time": "owner"},
        "live": {101: "owner", 303: "passive"},
        "ppid": {303: 1},
        "image": {},
        "terminated": [],
    }
    ctx = _ctx(tmp_path, state)
    census = ctx.list_candidates
    calls = []

    def first_census_uncertain():
        candidates = census()
        if not calls:
            candidates = [
                diagnostics.DaemonCandidate(pid=c.pid, start_time=c.start_time, image=c.image)
                if c.pid == 303 else c
                for c in candidates
            ]
        calls.append(True)
        return candidates

    ctx = dataclasses.replace(ctx, list_candidates=first_census_uncertain)

    result = diagnostics.apply_daemon_health(ctx)

    assert len(calls) >= 2, "a later, certain census was taken"
    assert result["before"]["grouping_uncertain"] is True
    assert state["terminated"] == []
    assert result["actions"][-1]["kind"] == "generation_grouping"

    # The next run groups with certainty and repairs as before.
    diagnostics.apply_daemon_health(ctx)
    assert state["terminated"] == [303]


def _lone_passive_state(tmp_path: Path, ppid) -> dict:
    """A one-candidate census: 303 is a stale, never-published passive while
    the routed active generation (101) is no longer running."""
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0")
    _stale_breadcrumb(tmp_path, new_pid=303, start="passive")
    return {
        "lock": None,
        "live": {303: "passive"},
        "ppid": {303: ppid},
        "image": {},
        "terminated": [],
    }


def test_single_candidate_with_unreadable_parent_is_uncertain_and_blocks_repair(tmp_path: Path):
    # A one-candidate census is not exempt from the structural checks: the
    # lone candidate may be one half of a pair whose other half was missed.
    state = _lone_passive_state(tmp_path, None)

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))

    assert report["candidate_count"] == 1
    assert report["grouping_uncertain"] is True
    assert "parent of candidate 303 unreadable" in report["grouping_uncertain_reason"]
    (finding,) = report["findings"]
    assert finding["kind"] == "abandoned_passive"
    assert finding["repairable"] is False
    assert finding["blocked_reason"] == "generation grouping uncertain"

    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state))
    assert state["terminated"] == []
    assert result["actions"][-1] == {
        "kind": "generation_grouping", "blocked": True,
        "reason": "generation grouping uncertain",
    }


def test_single_candidate_with_readable_non_launcher_parent_stays_certain(tmp_path: Path):
    state = _lone_passive_state(tmp_path, 1)

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))

    assert report["grouping_uncertain"] is False
    assert report["grouping_uncertain_reason"] is None
    assert report["generation_count"] == 1
    (finding,) = report["findings"]
    assert finding["kind"] == "abandoned_passive"
    assert finding["repairable"] is True
    assert finding["blocked_reason"] is None

    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state))
    assert all(action["kind"] != "generation_grouping" for action in result["actions"])


def test_single_candidate_with_unreadable_executable_is_uncertain_and_blocks_repair(
    tmp_path: Path,
):
    # A readable parent is not enough: without the executable image the
    # doctor cannot prove the lone candidate is not an unpaired launcher.
    state = _lone_passive_state(tmp_path, 1)
    state["image"][303] = None

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))

    assert report["candidate_count"] == 1
    assert report["grouping_uncertain"] is True
    assert "executable of candidate 303 unreadable" in report["grouping_uncertain_reason"]
    (finding,) = report["findings"]
    assert finding["kind"] == "abandoned_passive"
    assert finding["repairable"] is False
    assert finding["blocked_reason"] == "generation grouping uncertain"

    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state))
    assert state["terminated"] == []
    assert result["actions"][-1] == {
        "kind": "generation_grouping", "blocked": True,
        "reason": "generation grouping uncertain",
    }


def test_unreadable_executable_is_uncertain_for_every_candidate():
    def reason(*items):
        return group_generations(
            {item.pid: item for item in items}, structural=True, is_launcher_image=_is_launcher,
        )[1]

    launcher = _candidate(1, "10", 0, LAUNCHER)
    # Unpaired candidates: one unreadable image is enough, either way round.
    assert reason(_candidate(2, "11", 1, None)) == "executable of candidate 2 unreadable"
    assert reason(_candidate(2, "11", 1, WORKER), _candidate(5, "12", 1, None)) == (
        "executable of candidate 5 unreadable"
    )
    # A parent/child pair with either image unreadable stays uncertain, and the
    # problem is named once.
    assert reason(launcher, _candidate(2, "11", 1, None)) == "executable of candidate 2 unreadable"
    assert reason(_candidate(1, "10", 0, None), _candidate(2, "11", 1, WORKER)) == (
        "executable of candidate 1 unreadable"
    )


def test_without_opt_in_each_candidate_is_its_own_generation(tmp_path: Path):
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=202, version="1.0.0")
    state = _pair_state()

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state, group=False))

    assert report["generation_count"] == 2
    assert report["grouping_uncertain"] is False
    assert [finding["kind"] for finding in report["findings"]] == ["duplicate_resident"]


def _candidate(pid, start, ppid, image):
    return diagnostics.DaemonCandidate(pid=pid, start_time=start, ppid=ppid, image=image)


def test_grouping_is_uncertain_for_every_non_pair_parent_child_relation():
    def reason(*items):
        generations, why = group_generations(
            {item.pid: item for item in items}, structural=True, is_launcher_image=_is_launcher,
        )
        return why

    launcher = _candidate(1, "10", 0, LAUNCHER)
    assert reason(launcher, _candidate(2, "11", 1, WORKER)) is None
    assert "parent of candidate 2 unreadable" in reason(_candidate(2, "11", None, WORKER))
    assert reason(_candidate(2, "11", 1, WORKER)) is None
    assert group_generations({}, structural=True) == ([], None)
    assert "not a launcher/worker pair" in reason(
        _candidate(1, "10", 0, WORKER), _candidate(2, "11", 1, WORKER))
    assert "start order" in reason(launcher, _candidate(2, "09", 1, WORKER))
    assert "start order" in reason(launcher, _candidate(2, "later", 1, WORKER))
    assert "unreadable" in reason(launcher, _candidate(2, "11", 1, None))
    assert "parents 2 candidates" in reason(
        launcher, _candidate(2, "11", 1, WORKER), _candidate(3, "12", 1, WORKER))
    assert "longer chain" in reason(
        launcher, _candidate(2, "11", 1, LAUNCHER), _candidate(3, "12", 2, WORKER))


def test_is_venv_redirector_requires_a_venv_scripts_layout(tmp_path: Path):
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    (tmp_path / "venv" / "pyvenv.cfg").write_text("home = C:/py\n", encoding="utf-8")
    assert is_venv_redirector(str(scripts / "python.exe"))
    assert is_venv_redirector(str(scripts / "pythonw.exe"))
    assert not is_venv_redirector(str(scripts / "node.exe"))
    assert not is_venv_redirector(str(tmp_path / "python.exe"))
    (tmp_path / "venv" / "pyvenv.cfg").unlink()
    assert not is_venv_redirector(str(scripts / "python.exe"))
    assert not is_venv_redirector(None)


def test_single_launcher_without_captured_worker_is_uncertain_and_blocks_repair(tmp_path: Path):
    # A readable launcher image with no captured worker: the census may have
    # missed its worker, so the lone launcher is not a proven whole
    # generation and must not be repaired as one.
    state = _lone_passive_state(tmp_path, 1)
    state["image"][303] = LAUNCHER

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))

    assert report["candidate_count"] == 1
    assert report["grouping_uncertain"] is True
    assert "launcher candidate 303 has no captured worker" in report["grouping_uncertain_reason"]
    (finding,) = report["findings"]
    assert finding["kind"] == "abandoned_passive"
    assert finding["repairable"] is False
    assert finding["blocked_reason"] == "generation grouping uncertain"

    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state))
    assert state["terminated"] == []
    assert result["actions"][-1] == {
        "kind": "generation_grouping", "blocked": True,
        "reason": "generation grouping uncertain",
    }


def test_unpaired_launcher_beside_the_owner_blocks_duplicate_repair(tmp_path: Path):
    # The owner is a proven launcher/worker pair; a second launcher (303)
    # appears without its worker. It is reported, but never terminated.
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0")
    state = _pair_state()
    state["live"][303] = "1003"
    state["image"][303] = LAUNCHER

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))

    assert report["generation_count"] == 2
    assert report["grouping_uncertain"] is True
    assert report["grouping_uncertain_reason"] == "launcher candidate 303 has no captured worker"
    (finding,) = report["findings"]
    assert finding["kind"] == "duplicate_resident"
    assert finding["repairable"] is False
    assert finding["blocked_reason"] == "generation grouping uncertain"

    diagnostics.apply_daemon_health(_ctx(tmp_path, state))
    assert state["terminated"] == []
    assert set(state["live"]) == {101, 202, 303}


def test_unpaired_launcher_reasons():
    def reason(*items):
        return group_generations(
            {item.pid: item for item in items}, structural=True, is_launcher_image=_is_launcher,
        )[1]

    assert reason(_candidate(1, "10", 0, LAUNCHER)) == (
        "launcher candidate 1 has no captured worker"
    )
    # Paired launchers stay certain; a lone worker beside them does too.
    assert reason(_candidate(1, "10", 0, LAUNCHER), _candidate(2, "11", 1, WORKER),
                  _candidate(5, "12", 0, WORKER)) is None
    # A launcher whose only "parent" candidate is a worker is both a bad pair
    # and an unpaired launcher.
    why = reason(_candidate(1, "10", 0, WORKER), _candidate(2, "11", 1, LAUNCHER))
    assert "not a launcher/worker pair" in why
    assert "launcher candidate 2 has no captured worker" in why


# -- a worker whose launcher the census missed --------------------------------


def test_worker_with_live_parent_outside_census_blocks_duplicate_repair(tmp_path: Path):
    # The owner is a proven pair (101 -> 202). A second generation's launcher
    # (303) is alive but missing from the census (its command line was
    # unreadable); only its worker 404 was captured, carrying ppid 303.
    # Repairing 404 as a whole duplicate generation would kill half a pair.
    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0")
    state = _pair_state()
    state["live"][404] = "1004"
    state["ppid"][404] = 303

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))

    assert report["grouping_uncertain"] is True
    assert report["grouping_uncertain_reason"] == "parent 303 of candidate 404 not in census"
    (finding,) = report["findings"]
    assert finding["kind"] == "duplicate_resident"
    assert finding["repairable"] is False
    assert finding["blocked_reason"] == "generation grouping uncertain"

    diagnostics.apply_daemon_health(_ctx(tmp_path, state))
    assert state["terminated"] == []
    assert set(state["live"]) == {101, 202, 404}


def test_worker_whose_missing_parent_is_a_launcher_blocks_abandoned_repair(tmp_path: Path):
    state = _lone_passive_state(tmp_path, 77)
    state["parent"] = {303: {"parent_image": LAUNCHER}}

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))

    assert report["grouping_uncertain_reason"] == (
        "launcher parent 77 of candidate 303 not in census"
    )
    diagnostics.apply_daemon_health(_ctx(tmp_path, state))
    assert state["terminated"] == []


@pytest.mark.parametrize(
    "parent", [{"parent_image": "C:/Windows/explorer.exe"}, {"parent_gone": True}]
)
def test_worker_whose_missing_parent_is_proven_not_a_launcher_is_repaired(
    tmp_path: Path, parent
):
    state = _lone_passive_state(tmp_path, 77)
    state["parent"] = {303: parent}

    report = diagnostics.audit_daemon_health(_ctx(tmp_path, state))
    assert report["grouping_uncertain"] is False
    (finding,) = report["findings"]
    assert finding["kind"] == "abandoned_passive"
    assert finding["repairable"] is True

    result = diagnostics.apply_daemon_health(_ctx(tmp_path, state))
    assert all(action["kind"] != "generation_grouping" for action in result["actions"])


def test_outside_parent_reasons():
    def reason(item):
        return group_generations({item.pid: item}, structural=True, is_launcher_image=_is_launcher)[1]

    def worker(ppid, **parent):
        return diagnostics.DaemonCandidate(
            pid=2, start_time="11", ppid=ppid, image=WORKER, **parent)

    assert reason(worker(77)) == "parent 77 of candidate 2 not in census"
    assert reason(worker(77, parent_image=LAUNCHER)) == (
        "launcher parent 77 of candidate 2 not in census"
    )
    assert reason(worker(77, parent_image=WORKER)) is None
    assert reason(worker(77, parent_gone=True)) is None
    assert reason(worker(77, parent_image=LAUNCHER, parent_gone=True)) is None
    for no_real_parent in (0, 1, 2):
        assert reason(worker(no_real_parent)) is None
    # A launcher-image candidate's own parent is never its generation's
    # other half: only its missing worker makes it uncertain.
    assert reason(_candidate(2, "11", 77, LAUNCHER)) == (
        "launcher candidate 2 has no captured worker"
    )
    # Without structural grouping nothing changes.
    assert group_generations({2: worker(77)}, structural=False)[1] is None


def test_uncertainty_in_only_the_final_census_is_reported_as_a_refusal(tmp_path: Path):
    """The final census is held to the same fail-closed rule as every earlier
    one: a refusal it reveals is reported, never summarized as a clean apply."""
    import copy

    routing.publish_active(tmp_path, bind="127.0.0.1", port=9281, pid=101, version="1.0.0")
    _stale_breadcrumb(tmp_path, new_pid=303, start="passive")
    base = {
        "lock": {"pid": 101, "start_time": "owner"},
        "live": {101: "owner", 303: "passive"},
        "ppid": {303: 1},
        "image": {},
        "terminated": [],
    }

    def counted(state, *, uncertain_at=None):
        ctx = _ctx(tmp_path, state)
        census = ctx.list_candidates
        calls = []

        def list_candidates():
            calls.append(True)
            candidates = census()
            if len(calls) == uncertain_at:
                candidates = [
                    diagnostics.DaemonCandidate(pid=c.pid, start_time=c.start_time, image=c.image)
                    for c in candidates
                ]
            return candidates

        return dataclasses.replace(ctx, list_candidates=list_candidates), calls

    ctx, calls = counted(copy.deepcopy(base))
    clean = diagnostics.apply_daemon_health(ctx)
    assert clean["actions"] and clean["actions"][-1].get("kind") != "generation_grouping"
    _stale_breadcrumb(tmp_path, new_pid=303, start="passive")

    state = copy.deepcopy(base)
    ctx, _ = counted(state, uncertain_at=len(calls))
    result = diagnostics.apply_daemon_health(ctx)

    assert result["actions"][-1] == {
        "kind": "generation_grouping", "blocked": True,
        "reason": "generation grouping uncertain",
    }
    assert result["after"]["grouping_uncertain"] is True