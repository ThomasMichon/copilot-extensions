"""Source-only setup-refusal protocol tests; no kernel observer or Docker claims."""

import os
import time

import pytest

from tools.coverage_guided_selection.measurement_diagnostics import Recording
from tools.coverage_guided_selection.observer_lifecycle import (
    OwnedObserverLifecycle, ObserverSetupRefused, SetupPhaseAccounting,
)


@pytest.mark.parametrize("stage",["initialize","capture"])
@pytest.mark.parametrize("error_type",[PermissionError,SystemExit])
def test_partial_setup_refusal_cleans_accounts_and_never_runs_body(stage,error_type):
    owned=[]
    closed=[]
    calls=[]
    def allocate():
        pair=os.pipe()
        owned.extend(pair)
    def initialize():
        calls.append("initialize")
        allocate()
        if stage=="initialize":
            raise error_type("injected owned setup refusal")
    def capture():
        calls.append("capture")
        allocate()
        raise error_type("injected partial capture refusal")
    def finish(session):
        pytest.fail("refused setup must not parse or admit consumers")
    def cleanup(session):
        calls.append("cleanup")
        while owned:
            fd=owned.pop()
            os.close(fd)
            closed.append(fd)
    lifecycle=OwnedObserverLifecycle(initialize,capture,finish,cleanup)
    recording=Recording(None)
    body_started=False
    started=time.monotonic()
    with pytest.raises(ObserverSetupRefused):
        with recording.failure_boundary(started):
            try:
                if not lifecycle.begin():
                    raise ObserverSetupRefused("observer setup refused")
                body_started=True
            finally:
                outcome=lifecycle.complete()
    assert not body_started and not owned
    assert len(closed)==(2 if stage=="initialize" else 4)
    for fd in closed:
        with pytest.raises(OSError):
            os.fstat(fd)
    assert calls.count("cleanup")==2
    assert outcome["observer_setup_refused"] and not outcome["all_admitted"]
    assert outcome["completed_cases"]==0 and outcome["capture_started_ns"] is None
    assert "genuine_zero_python_children" not in outcome
    assert outcome["refusal_detail"]=={"stage":"setup","error_type":error_type.__name__}
    chain=[
        int(started*1e9),outcome["setup_started_ns"],outcome["setup_failed_ns"],
        outcome["initial_cleanup_completed_ns"],outcome["stop_started_ns"],
        outcome["parse_completed_ns"],outcome["all_cleanup_completed_ns"],
        int(recording.failed_interval["ended"]*1e9),
    ]
    assert chain==sorted(chain)
    assert recording.failed_interval["error_type"]=="ObserverSetupRefused"
    assert recording.failed_interval["admission"] is False
    assert recording.failed_interval["performance_acceptance"] is False


def test_import_setup_refusal_still_completes_without_source_or_consumers():
    calls=[]
    def initialize():
        raise ImportError("observer runtime unavailable")
    def cleanup(session):
        assert session is None
        calls.append("cleanup")
    lifecycle=OwnedObserverLifecycle(initialize,lambda:pytest.fail("capture after refusal"),
        lambda session:pytest.fail("finish after refusal"),cleanup)
    assert lifecycle.begin() is False
    result=lifecycle.complete()
    assert calls==["cleanup","cleanup"]
    assert result["setup_completed_ns"] is None and result["capture_started_ns"] is None
    assert result["refusal_detail"]["error_type"]=="ImportError"
    assert not result["all_admitted"] and result["diagnostic_only"]


def test_unproven_cleanup_never_becomes_setup_or_admission_success():
    def initialize():
        raise PermissionError("setup refused")
    def cleanup(session):
        raise OSError("cleanup unproven")
    lifecycle=OwnedObserverLifecycle(initialize,lambda:None,lambda session:{"all_admitted":True},cleanup)
    assert lifecycle.begin() is False
    result=lifecycle.complete()
    assert result["initial_cleanup_completed_ns"] is None
    assert result["all_cleanup_completed_ns"] is None
    assert result["cleanup_error_types"]==["OSError","OSError"]
    assert not result["all_admitted"] and result["refusal"]=="observer cleanup not proven"


def test_setup_cleanup_is_idempotent_and_cannot_close_reused_descriptor():
    owned=[]
    def initialize():
        owned.extend(os.pipe())
        raise PermissionError("setup refused")
    def cleanup(session):
        while owned:os.close(owned.pop())
    lifecycle=OwnedObserverLifecycle(initialize,lambda:None,lambda session:None,cleanup)
    assert lifecycle.begin() is False
    sentinel=os.pipe()
    try:
        result=lifecycle.complete()
        assert result["all_cleanup_completed_ns"] is not None
        assert all(os.fstat(fd) for fd in sentinel)
    finally:
        for fd in sentinel:os.close(fd)


def test_invalid_repeated_transaction_states_are_fatal():
    lifecycle=OwnedObserverLifecycle(lambda:None,lambda:object(),lambda session:{"all_admitted":True},lambda session:None)
    with pytest.raises(RuntimeError,match="completion"):
        lifecycle.complete()
    assert lifecycle.begin()
    with pytest.raises(RuntimeError,match="already attempted"):
        lifecycle.begin()
    assert lifecycle.complete()["all_admitted"]
    with pytest.raises(RuntimeError,match="completion"):
        lifecycle.complete()


def test_missing_exit_refusal_is_not_zero_source_or_abrupt_exit_acceptance():
    cleaned=[]
    def finish(session):
        raise RuntimeError("missing descendant exits")
    lifecycle=OwnedObserverLifecycle(lambda:None,lambda:object(),finish,lambda session:cleaned.append(True))
    assert lifecycle.begin()
    result=lifecycle.complete()
    assert not result["all_admitted"] and result["completed_cases"]==0
    assert result["refusal_detail"]=={"stage":"completion","error_type":"RuntimeError"}
    assert result["all_cleanup_completed_ns"] is not None and cleaned==[True]


def test_setup_subphases_preserve_call_arguments_result_and_monotonic_enclosure():
    import json
    phases=SetupPhaseAccounting()
    calls=[]
    marker=object()
    def operation(value,*,flag):
        calls.append((time.monotonic_ns(),value,flag))
        return marker
    caller_start=time.monotonic_ns()
    assert phases.run("compiler-install",operation,marker,flag=True) is marker
    report=phases.serialize()
    caller_end=time.monotonic_ns()
    rows=json.loads(report["phases_json"])
    assert len(rows)==1 and rows[0]["phase"]=="compiler-install"
    row=rows[0]
    assert caller_start<=row["started_ns"]<=calls[0][0]<=row["completed_ns"]
    assert row["completed_ns"]<=report["serialization_started_ns"]<=report["serialization_completed_ns"]<=caller_end
    assert calls[0][1:] == (marker,True) and row["outcome"]=="returned"
    assert report["diagnostic_only"] and report["schema"]=="CGS-SETUP-PHASES/1"
    assert "all_admitted" not in report


@pytest.mark.parametrize("error_type",[PermissionError,SystemExit])
def test_subphase_failure_retained_without_changing_refusal_cleanup(error_type):
    import json
    phases=SetupPhaseAccounting()
    cleanup=[]
    error=error_type("injected setup operation failure")
    def failed():
        raise error
    lifecycle=OwnedObserverLifecycle(
        lambda:phases.run("native-build",failed),
        lambda:pytest.fail("capture after refusal"),
        lambda session:pytest.fail("finish after refusal"),
        lambda session:cleanup.append(time.monotonic_ns()))
    assert not lifecycle.begin()
    outcome=lifecycle.complete()
    report=phases.serialize()
    rows=json.loads(report["phases_json"])
    assert rows==phases.rows and len(rows)==1
    assert rows[0]["outcome"]=="failed" and rows[0]["completed_ns"]>=rows[0]["started_ns"]
    assert rows[0]["completed_ns"]<=outcome["setup_failed_ns"]
    assert outcome["all_cleanup_completed_ns"]<=report["serialization_started_ns"]
    assert outcome["refusal_detail"]["error_type"]==error_type.__name__
    assert not outcome["all_admitted"] and len(cleanup)==2
    assert rows[0]["error"]["message"]=="injected setup operation failure"
    assert outcome["first_failure"]["message"]=="injected setup operation failure"
    assert report["failed_phase"]==report["reached_phase"]=="native-build"


def test_setup_diagnostic_serialization_and_outer_reply_are_inside_caller_clock(monkeypatch):
    import json
    phases=SetupPhaseAccounting()
    phases.run("component-import",lambda:None)
    lifecycle=OwnedObserverLifecycle(lambda:None,lambda:object(),lambda session:{"all_admitted":True},lambda session:None)
    assert lifecycle.begin()
    inner_dumps=json.dumps
    serialization_times=[]
    def serialized(value,**kwargs):
        result=inner_dumps(value,**kwargs)
        serialization_times.append(time.monotonic_ns())
        return result
    monkeypatch.setattr(json,"dumps",serialized)
    caller_start=time.monotonic_ns()
    outcome=lifecycle.complete()
    report=phases.serialize()
    wire=json.dumps(dict(outcome,setup_phase_accounting=report))
    received=json.loads(wire)
    caller_end=time.monotonic_ns()
    assert caller_start<=outcome["all_cleanup_completed_ns"]<=report["serialization_started_ns"]
    assert report["serialization_started_ns"]<=serialization_times[0]<=report["serialization_completed_ns"]
    assert report["serialization_completed_ns"]<=serialization_times[1]<=caller_end
    assert received["setup_phase_accounting"]==report and received["all_admitted"]


def test_setup_phase_names_and_count_are_bounded_and_active_reports_refuse():
    phases=SetupPhaseAccounting()
    def active():
        with pytest.raises(RuntimeError,match="still active"):
            phases.serialize()
    phases.run("component-extraction",active)
    with pytest.raises(ValueError,match="repeated"):
        phases.run("component-extraction",lambda:pytest.fail("repeated operation"))
    with pytest.raises(ValueError,match="unknown"):
        phases.run("unbounded-private-phase",lambda:pytest.fail("unknown operation"))
    for name in sorted(phases.NAMES-{"component-extraction"}):
        phases.run(name,lambda:None)
    assert len(phases.rows)==len(phases.NAMES)==14
    with pytest.raises(ValueError,match="excessive"):
        phases.run("native-build",lambda:pytest.fail("excessive operation"))


def test_immutable_staging_remains_inside_owned_setup_and_cleanup():
    import json
    phases = SetupPhaseAccounting()
    calls = []
    marker = object()

    def stage():
        calls.append(("stage", time.monotonic_ns()))
        return marker

    def initialize():
        assert phases.run("immutable-artifact-stage", stage) is marker

    lifecycle = OwnedObserverLifecycle(
        initialize, lambda: object(), lambda _session: {"all_admitted": True},
        lambda _session: calls.append(("cleanup", time.monotonic_ns())),
    )
    assert lifecycle.begin()
    outcome = lifecycle.complete()
    report = phases.serialize()
    row, = json.loads(report["phases_json"])
    assert row["phase"] == "immutable-artifact-stage" and row["outcome"] == "returned"
    assert outcome["setup_started_ns"] <= row["started_ns"] <= calls[0][1] <= row["completed_ns"]
    assert row["completed_ns"] <= outcome["setup_completed_ns"] <= outcome["capture_started_ns"]
    assert outcome["parse_completed_ns"] <= calls[1][1] <= outcome["all_cleanup_completed_ns"]
    assert outcome["all_cleanup_completed_ns"] <= report["serialization_started_ns"]


def test_first_setup_error_and_subprocess_stderr_survive_secondary_cleanup_failure():
    import json
    import subprocess
    phases=SetupPhaseAccounting()
    error=subprocess.CalledProcessError(1,["compiler","cc"],output="actual stdout",
                                        stderr="actual first linker diagnostic")
    def failed():
        raise error
    def cleanup(session):
        raise OSError("secondary cleanup failure")
    lifecycle=OwnedObserverLifecycle(lambda:phases.run("native-build",failed),
        lambda:pytest.fail("capture after refusal"),lambda session:pytest.fail("admission after refusal"),cleanup)
    assert not lifecycle.begin()
    result=lifecycle.complete()
    record=result["first_failure"]
    assert record["error_type"]=="CalledProcessError" and record["returncode"]==1
    assert record["stdout"]=="actual stdout" and record["stderr"]=="actual first linker diagnostic"
    assert record["stage"]=="setup" and not result["all_admitted"]
    assert [row["message"] for row in result["cleanup_failures"]]==["secondary cleanup failure"]*2
    assert json.loads(phases.serialize()["phases_json"])[0]["error"]["stderr"]==record["stderr"]


def test_phase_diagnostics_do_not_replace_an_unprintable_original_exception():
    phases=SetupPhaseAccounting()
    class Unprintable(RuntimeError):
        def __str__(self):
            raise ValueError("formatting failed")
    error=Unprintable()
    def failed():
        raise error
    with pytest.raises(Unprintable) as caught:
        phases.run("compiler-extract-verify",failed)
    assert caught.value is error
    assert phases.rows[0]["error"]["message"] is None
    assert phases.rows[0]["outcome"]=="failed"


@pytest.mark.parametrize("character", ["\0", "\U0001f680", "\\", '"'])
def test_worst_case_failure_text_is_bounded_below_control_message_limit(character):
    import json
    phases=SetupPhaseAccounting()
    class OutputError(RuntimeError):
        stdout=character*10_000
        stderr=character*10_000
        returncode=1
    error=OutputError(character*10_000)
    def failed():
        raise error
    def cleanup(session):
        raise OSError(character*10_000)
    for name in sorted(phases.NAMES-{"native-build"}):
        phases.run(name,lambda:None)
    lifecycle=OwnedObserverLifecycle(lambda:phases.run("native-build",failed),
        lambda:None,lambda session:{"all_admitted":True},cleanup)
    assert not lifecycle.begin()
    result=lifecycle.complete()
    result["setup_phase_accounting"]=phases.serialize()
    result.update(native_build=None,actual_build_argv=None,native_elf=None,runtime_providers=None)
    wire=json.dumps(result)
    assert len(wire.encode())<65536 and not result["all_admitted"]
    assert result["first_failure"]["truncated_fields"]==["message","stdout","stderr"]
