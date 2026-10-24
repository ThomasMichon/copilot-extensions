"""Retention is bounded, sanitized and explicitly not observation authority."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tools.coverage_guided_selection import measurement_diagnostics as diagnostics
from tools.coverage_guided_selection import measurement_launch as launch
from tools.test_measurement_launch import TestMeasurementContainer as _SharedCases


def test_optional_observer_transaction_stays_inside_original_recovery_clocks():
    import ast
    import inspect
    import textwrap
    source=textwrap.dedent(inspect.getsource(
        _SharedCases.test_real_recovery_pair_keeps_original_deadlines_and_concurrency))
    measured=next(node for node in ast.walk(ast.parse(source))
                  if isinstance(node,ast.FunctionDef) and node.name=="measured_run")
    statements=[ast.unparse(node) for node in measured.body]
    assert "recording.lifecycle.install_launch_recorder" in statements[0]
    assert "else:\n    registry.install()" in statements[0]
    assert statements[1]=="started = time.monotonic()"
    guarded=measured.body[2]
    assert isinstance(guarded,ast.With)
    assert ast.unparse(guarded.items[0].context_expr)=="recording.failure_boundary(started)"
    timed=guarded.body[0]
    assert isinstance(timed,ast.Try)
    assert ast.unparse(timed.body[0])=="if recording.lifecycle:\n    recording.lifecycle.begin(started)"
    assert "pytest.main" in ast.unparse(timed.body[1])
    final="\n".join(ast.unparse(node) for node in timed.finalbody)
    assert "recording.lifecycle.complete()" in final
    assert "registry.close()" in final
    assert statements[3]=="elapsed = time.monotonic() - started"


def test_diagnostic_snapshot_omits_secrets_paths_and_unobserved_exits(tmp_path):
    activation=tmp_path/"activation.py"
    activation.write_text("def activate(payload):pass\n")
    registry=launch.Registry(tmp_path/"tickets",sys.executable,activation,"private-context")
    root=registry.root/("a"*64)
    root.mkdir()
    registry._issued[root]={"kind":"python"}
    launch.signed_write(root/"request.json",{
        "context":"private-context","argv":["private-argument"],
        "activation":{"path":str(activation)},"secret":"do-not-retain",
    },registry.key)
    result=diagnostics.snapshot(registry)
    text=json.dumps(result)
    assert all(secret not in text for secret in
               ("private-context","private-argument","do-not-retain",str(tmp_path),registry.key.hex()))
    assert result["diagnostic_only"] and not result["native_completeness_proven"]
    assert not result["authorizes_admission"]
    assert result["issued"][0]["exit_observed"] is None
    assert result["issued"][0]["actual_kernel_exit_ns"] is None
    assert result["issued"][0]["records"]["birth"]=="missing"


def test_phase_reports_and_failure_are_emitted_before_cleanup(tmp_path,capsys):
    activation=tmp_path/"activation.py"
    activation.write_text("def activate(payload):pass\n")
    registry=launch.Registry(tmp_path/"tickets",sys.executable,activation,"test-context")
    recording=diagnostics.Recording(registry)
    report=SimpleNamespace(when="call",outcome="failed",duration=.25,start=10.,stop=10.25)
    hook=recording.plugin("measured").pytest_runtest_makereport(
        SimpleNamespace(nodeid="/private/path/test_example.py::test_name[private-value]"),None,
    )
    next(hook)
    with pytest.raises(StopIteration):
        hook.send(report)
    with pytest.raises(AssertionError,match="retained failure"):
        with recording:
            raise AssertionError("retained failure")
    output=capsys.readouterr().out
    result=json.loads(output.split("MEASUREMENT_DIAGNOSTICS ",1)[1])
    assert "private" not in output and "retained failure" not in output
    assert result["exception_type"]=="AssertionError"
    assert result["pytest_phases"][0]["file"]=="test_example.py"
    assert result["pytest_phases"][0]["duration_s"]==.25
    assert result["pytest_phases"][0]["outcome"]=="failed"


def test_snapshot_failure_preserves_phase_reports_and_original_error(monkeypatch,capsys):
    recording=diagnostics.Recording(None)
    recording.phases.append({"phase":"call","outcome":"failed"})
    def broken(*args):
        raise RuntimeError("private snapshot failure")
    monkeypatch.setattr(diagnostics,"snapshot",broken)
    with pytest.raises(ValueError,match="original failure"):
        with recording:
            raise ValueError("original failure")
    output=capsys.readouterr().out
    result=json.loads(output.split("MEASUREMENT_DIAGNOSTICS ",1)[1])
    assert result["snapshot_error"]=="RuntimeError"
    assert result["exception_type"]=="ValueError"
    assert result["pytest_phases"]==[{"phase":"call","outcome":"failed"}]
    assert "private" not in output


@pytest.mark.skipif(os.environ.get("CGS_ISOLATED_MEASUREMENT")!="1",
                    reason="real wait/exit diagnostic contract requires the isolated native container")
class TestMeasurementDiagnosticsNative:
    _activation=_SharedCases._activation

    def test_actual_birth_existing_wait_exit_and_launch_phases_retained(self,tmp_path,capsys):
        registry=launch.Registry(tmp_path/"tickets",sys.executable,self._activation(tmp_path),"safe-context")
        recording=diagnostics.Recording(registry)
        with recording:
            registry.install()
            try:
                child=subprocess.run([sys.executable,"-I","-S","-B","-c","raise SystemExit(17)"],
                                     capture_output=True,text=True)
            finally:
                registry.close()
            assert child.returncode==17
            recording.verified=registry.verify()
        output=capsys.readouterr().out
        report=json.loads(output.split("MEASUREMENT_DIAGNOSTICS ",1)[1])
        row=report["issued"][0]
        assert row["birth_pid"]==next(iter(registry.processes.values())).pid==row["spawn_pid"]
        assert row["audit_created_ns"]<=row["birth_born_ns"]<=row["exit_observed"]["observed_ns"]
        assert row["exit_observed"]["returncode"]==17
        assert row["actual_kernel_exit_ns"] is None
        phases=row["launch_phases"]
        ordered=[phases[name] for name in (
            "entered_ns","plan_started_ns","plan_finished_ns","request_written_ns",
            "spawn_started_ns","spawn_returned_ns","ledger_written_ns")]
        assert ordered==sorted(ordered)
        assert not report["authorizes_admission"] and not report["native_completeness_proven"]
        print("DIAGNOSTIC_RETENTION_PROOF "+json.dumps(report,sort_keys=True))

    def test_actual_pytest_failure_retains_setup_call_teardown(self,tmp_path,capsys):
        test=tmp_path/"test_failure.py"
        test.write_text("def test_expected_failure():\n    assert False\n")
        registry=launch.Registry(tmp_path/"tickets",sys.executable,self._activation(tmp_path),"phase-context")
        recording=diagnostics.Recording(registry)
        with recording:
            result=pytest.main(["-q",str(test),"--basetemp",str(tmp_path/"nested"),
                               "-p","no:cacheprovider"],plugins=[recording.plugin("functional")])
            assert result==1
        output=capsys.readouterr().out
        report=json.loads(output.split("MEASUREMENT_DIAGNOSTICS ",1)[1])
        phases=report["pytest_phases"]
        assert [row["phase"] for row in phases]==["setup","call","teardown"]
        assert [row["outcome"] for row in phases]==["passed","failed","passed"]
        assert all(row["duration_s"]>=0 for row in phases)
        assert not report["authorizes_admission"]
