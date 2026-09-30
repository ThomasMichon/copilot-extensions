"""Tests for the evaluator contract (the judgment half of emitters-and-evaluators)."""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_dispatch.__main__ import _cmd_evaluate, build_parser
from agent_dispatch.execution_cli import _cmd_verify_submitted
from agent_dispatch.producers import evaluator as ev


def _args(argv):
    return build_parser().parse_args(argv)


def _completed_event(labels=("recipe:reviewer",), status="submitted", **task):
    t = {"id": "t-1", "labels": list(labels), "status": status,
         "origin_ref": "o/n#42", "source": "recipe", **task}
    return {"type": "task.submitted", "task": t}


# -- spec validation ---------------------------------------------------------


def test_spec_requires_rules_list():
    with pytest.raises(ev.EvaluatorError):
        ev.SpecEvaluator({})
    with pytest.raises(ev.EvaluatorError):
        ev.SpecEvaluator({"rules": "nope"})


def test_load_evaluator_rejects_mixed_rules_and_scripts():
    with pytest.raises(ev.EvaluatorError, match="both 'rules' and 'scripts'"):
        ev.load_evaluator({"rules": [], "scripts": {"review-loop": ["python3", "x.py"]}})


def test_decision_decoder_rejects_unknown_keys():
    with pytest.raises(ev.EvaluatorError, match="unknown key"):
        ev.decision_from_dict({"decision": "emit", "title": "x", "field": {}})


def test_script_registry_rejects_timeouts_longer_than_verification_lease():
    with pytest.raises(ev.EvaluatorError, match="timeout_seconds"):
        ev.ScriptEvaluatorRegistry.from_spec(
            {
                "scripts": {"review-loop": ["python3", "x.py"]},
                "timeout_seconds": ev.MAX_SCRIPT_EVALUATOR_TIMEOUT + 1,
            }
        )


# -- matching ----------------------------------------------------------------


def test_rule_matches_on_event_type_and_labels():
    spec = ev.SpecEvaluator({"rules": [{
        "on": "task.submitted",
        "when": {"labels_any": ["recipe:reviewer"], "status": "submitted"},
        "emit": {"title_template": "unstick {origin_ref}",
                 "labels": ["recipe:conflict-resolution"],
                 "dedup_template": "evaluator:followup:{task_id}"},
    }]})
    decisions = spec.evaluate(_completed_event())
    assert len(decisions) == 1
    emit = decisions[0]
    assert isinstance(emit, ev.Emit)
    assert emit.title == "unstick o/n#42"
    assert emit.fields["labels"] == ["recipe:conflict-resolution"]
    assert emit.fields["dedup_key"] == "evaluator:followup:t-1"
    assert emit.fields["source"] == "evaluator"


def test_emit_prompt_is_appended_with_the_untrusted_content_note():
    # The event context (task title/labels/etc.) may trace back to an
    # untrusted external source (a webhook, an issue title); the rule
    # author's own prompt_template should not need to remember this itself.
    spec = ev.SpecEvaluator({"rules": [{
        "on": "task.submitted",
        "emit": {"title_template": "x", "prompt_template": "do the thing for {task_id}"},
    }]})
    emit = spec.evaluate(_completed_event())[0]
    assert "do the thing for t-1" in emit.fields["prompt"]
    assert "untrusted subject data" in emit.fields["prompt"]


def test_rule_skipped_on_wrong_event_type():
    spec = ev.SpecEvaluator({"rules": [{"on": "task.abandoned",
                                        "emit": {"title_template": "x"}}]})
    decisions = spec.evaluate(_completed_event())
    assert isinstance(decisions[0], ev.NoOp)


def test_when_labels_all_and_source_predicates():
    spec = ev.SpecEvaluator({"rules": [{
        "on": ["task.submitted"],
        "when": {"labels_all": ["a", "b"], "source": "recipe"},
        "emit": {"title_template": "ok"},
    }]})
    assert isinstance(spec.evaluate(_completed_event(labels=("a",)))[0], ev.NoOp)
    assert isinstance(
        spec.evaluate(_completed_event(labels=("a", "b")))[0], ev.Emit
    )


def test_first_matching_rule_wins():
    spec = ev.SpecEvaluator({"rules": [
        {"on": "task.submitted", "when": {"status": "queued"},
         "emit": {"title_template": "first"}},
        {"on": "task.submitted", "emit": {"title_template": "second"}},
    ]})
    emit = spec.evaluate(_completed_event())[0]
    assert emit.title == "second"


def test_emit_rule_without_title_template_raises():
    spec = ev.SpecEvaluator({"rules": [{"on": "task.submitted", "emit": {}}]})
    with pytest.raises(ev.EvaluatorError):
        spec.evaluate(_completed_event())


def test_confirm_rule_matches():
    spec = ev.SpecEvaluator({"rules": [{
        "on": "task.submitted",
        "when": {"labels_any": ["recipe:goal-driven"]},
        "confirm": True,
        "confirm_reason": "goal corroborated by recipe",
    }]})
    decisions = spec.evaluate(_completed_event(labels=("recipe:goal-driven",)))
    assert len(decisions) == 1
    assert isinstance(decisions[0], ev.Confirm)
    assert decisions[0].reason == "goal corroborated by recipe"


def test_emit_rule_wins_over_confirm_when_both_present_on_same_rule():
    """``emit`` is checked first -- a rule authoring both keys is unusual, but
    the precedence must be deterministic and documented, not accidental."""
    spec = ev.SpecEvaluator({"rules": [{
        "on": "task.submitted",
        "emit": {"title_template": "x"},
        "confirm": True,
    }]})
    assert isinstance(spec.evaluate(_completed_event())[0], ev.Emit)


def test_no_matching_confirm_rule_falls_through_to_noop():
    spec = ev.SpecEvaluator({"rules": [{
        "on": "task.submitted",
        "when": {"labels_any": ["recipe:goal-driven"]},
        "confirm": True,
    }]})
    assert isinstance(spec.evaluate(_completed_event(labels=("other",)))[0], ev.NoOp)


def test_abandon_rule_matches():
    spec = ev.SpecEvaluator({"rules": [{
        "on": "task.submitted",
        "when": {"labels_any": ["recipe:goal-driven"]},
        "abandon": True,
        "abandon_reason": "closed-unmerged",
    }]})
    decisions = spec.evaluate(_completed_event(labels=("recipe:goal-driven",)))
    assert len(decisions) == 1
    assert isinstance(decisions[0], ev.Abandon)
    assert decisions[0].reason == "closed-unmerged"


# -- apply -------------------------------------------------------------------


def test_apply_creates_follow_up_and_stamps_repo():
    created = {}

    def creator(title, **fields):
        created.update(title=title, **fields)
        return {"id": "t-2", "title": title, "status": "queued"}

    decisions = [ev.Emit(title="unstick o/n#42", fields={"labels": ["x"]})]
    out = ev.apply_decisions(decisions, creator=creator, repo="o/n")
    assert out[0]["created"]["id"] == "t-2"
    assert created["repo"] == "o/n"  # stamped the lane
    assert created["labels"] == ["x"]


def test_apply_noop_records_skip():
    out = ev.apply_decisions([ev.NoOp(reason="none")], creator=lambda *a, **k: {})
    assert out[0]["decision"] == "noop"


def test_apply_confirm_calls_confirmer_with_task_id():
    calls = []

    def confirmer(task_id, **kwargs):
        calls.append((task_id, kwargs))
        return {"id": task_id, "status": "completed"}

    decisions = [ev.Confirm(reason="corroborated")]
    out = ev.apply_decisions(
        decisions, creator=lambda *a, **k: {}, task_id="t-1", confirmer=confirmer
    )
    assert calls == [("t-1", {"actor": "evaluator"})]
    assert out[0] == {"decision": "confirm", "completed": {"id": "t-1", "status": "completed"}}


def test_apply_confirm_without_confirmer_is_skipped_not_raised():
    out = ev.apply_decisions([ev.Confirm()], creator=lambda *a, **k: {}, task_id="t-1")
    assert out[0]["decision"] == "confirm"
    assert out[0]["skipped"] is True


def test_apply_confirm_without_task_id_is_skipped_not_raised():
    out = ev.apply_decisions(
        [ev.Confirm()], creator=lambda *a, **k: {}, confirmer=lambda *a, **k: {}
    )
    assert out[0]["skipped"] is True


def test_apply_abandon_calls_abandoner_with_task_id():
    calls = []

    def abandoner(task_id, **kwargs):
        calls.append((task_id, kwargs))
        return {"id": task_id, "status": "abandoned"}

    out = ev.apply_decisions(
        [ev.Abandon(reason="stale")],
        creator=lambda *a, **k: {},
        task_id="t-1",
        abandoner=abandoner,
    )
    assert calls == [("t-1", {"actor": "evaluator", "reason": "stale"})]
    assert out[0] == {"decision": "abandon", "abandoned": {"id": "t-1", "status": "abandoned"}}


def test_evaluate_and_apply_threads_task_id_to_confirmer():
    calls = []
    spec = ev.SpecEvaluator({"rules": [{"on": "task.submitted", "confirm": True}]})
    report = ev.evaluate_and_apply(
        spec,
        _completed_event(),
        creator=lambda *a, **k: {},
        confirmer=lambda tid, **k: calls.append(tid) or {"id": tid},
    )
    assert calls == ["t-1"]
    assert report["applied"][0]["decision"] == "confirm"


def test_evaluate_and_apply_dry_run_creates_nothing():
    def creator(*a, **k):  # pragma: no cover - dry run must not create
        raise AssertionError("dry run must not create")

    spec = ev.SpecEvaluator({"rules": [{"on": "task.submitted",
                                        "emit": {"title_template": "x"}}]})
    report = ev.evaluate_and_apply(
        spec, _completed_event(), creator=creator, apply=False
    )
    assert "applied" not in report
    assert report["decisions"][0]["decision"] == "emit"


def test_script_evaluator_reads_stdin_and_returns_a_decision(tmp_path):
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "event = json.load(sys.stdin)\n"
        "json.dump({'decision': 'confirm', 'reason': event['task']['origin_ref']}, sys.stdout)\n",
        encoding="utf-8",
    )
    evaluator = ev.load_evaluator(
        {"scripts": {"review-loop": [sys.executable, str(script)]}},
        evaluator_ref="review-loop",
    )

    [decision] = evaluator.evaluate(_completed_event())

    assert isinstance(decision, ev.Confirm)
    assert decision.reason == "o/n#42"


def test_script_evaluator_uses_fixed_argv_and_shell_false():
    captured = {}

    def runner(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout='{"decision":"confirm"}', stderr="")

    evaluator = ev.load_evaluator(
        {"scripts": {"review-loop": ["python3", "/tmp/eval.py"]}},
        evaluator_ref="review-loop",
        runner=runner,
    )

    [decision] = evaluator.evaluate(_completed_event())

    assert isinstance(decision, ev.Confirm)
    assert captured["argv"] == ["python3", "/tmp/eval.py"]
    assert captured["kwargs"]["shell"] is False
    assert json.loads(captured["kwargs"]["input"])["task"]["id"] == "t-1"


def test_script_evaluator_timeout_returns_noop():
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    evaluator = ev.load_evaluator(
        {"scripts": {"review-loop": ["python3", "/tmp/eval.py"]}},
        evaluator_ref="review-loop",
        runner=runner,
    )

    [decision] = evaluator.evaluate(_completed_event())

    assert isinstance(decision, ev.NoOp)
    assert "timed out" in (decision.reason or "")


def test_script_evaluator_malformed_output_returns_noop():
    def runner(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="not-json", stderr="")

    evaluator = ev.load_evaluator(
        {"scripts": {"review-loop": ["python3", "/tmp/eval.py"]}},
        evaluator_ref="review-loop",
        runner=runner,
    )

    [decision] = evaluator.evaluate(_completed_event())

    assert isinstance(decision, ev.NoOp)
    assert "invalid" in (decision.reason or "")


# -- CLI ---------------------------------------------------------------------


def test_cli_parses_evaluate():
    a = _args(["evaluate", "--spec", "s.json"])
    assert a.func is _cmd_evaluate


def test_cli_parses_evaluate_ref():
    a = _args(["evaluate", "--spec", "s.json", "--evaluator-ref", "review-loop"])
    assert a.evaluator_ref == "review-loop"


def test_cli_parses_verify_submitted():
    a = _args(["verify-submitted", "task-1", "task-2"])
    assert a.func is _cmd_verify_submitted
    assert a.task_id == ["task-1", "task-2"]


def test_cmd_evaluate_dry_run_reads_event_file(tmp_path, capsys):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"rules": [{
        "on": "task.submitted",
        "when": {"labels_any": ["recipe:reviewer"]},
        "emit": {"title_template": "unstick {origin_ref}",
                 "labels": ["recipe:conflict-resolution"]},
    }]}), encoding="utf-8")
    ev_file = tmp_path / "event.json"
    ev_file.write_text(json.dumps(_completed_event()), encoding="utf-8")

    rc = _cmd_evaluate(
        _args(["evaluate", "--spec", str(spec), "--event-file", str(ev_file), "--dry-run"])
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decisions"][0]["title"] == "unstick o/n#42"
    assert "applied" not in out


def test_cmd_evaluate_bad_event_json_errors(tmp_path, capsys):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"rules": []}), encoding="utf-8")
    ev_file = tmp_path / "event.json"
    ev_file.write_text("not json", encoding="utf-8")
    rc = _cmd_evaluate(
        _args(["evaluate", "--spec", str(spec), "--event-file", str(ev_file), "--dry-run"])
    )
    assert rc == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_cmd_evaluate_loads_script_evaluator_by_ref(tmp_path, capsys):
    spec = tmp_path / "spec.json"
    script = tmp_path / "eval.py"
    script.write_text(
        "import json, sys\n"
        "json.dump({'decision': 'noop', 'reason': 'script'}, sys.stdout)\n",
        encoding="utf-8",
    )
    spec.write_text(
        json.dumps({"scripts": {"review-loop": [sys.executable, str(script)]}}),
        encoding="utf-8",
    )
    ev_file = tmp_path / "event.json"
    ev_file.write_text(json.dumps(_completed_event()), encoding="utf-8")

    rc = _cmd_evaluate(
        _args(
            [
                "evaluate",
                "--spec",
                str(spec),
                "--event-file",
                str(ev_file),
                "--dry-run",
                "--evaluator-ref",
                "review-loop",
            ]
        )
    )

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["decisions"][0]["decision"] == "noop"
