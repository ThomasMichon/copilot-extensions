"""Tests for the evaluator contract (the judgment half of emitters-and-evaluators)."""

from __future__ import annotations

import json

import pytest

from agent_dispatch.__main__ import _cmd_evaluate, build_parser
from agent_dispatch.producers import evaluator as ev


def _args(argv):
    return build_parser().parse_args(argv)


def _completed_event(labels=("recipe:reviewer",), status="completed", **task):
    t = {"id": "t-1", "labels": list(labels), "status": status,
         "origin_ref": "o/n#42", "source": "recipe", **task}
    return {"type": "task.completed", "task": t}


# -- spec validation ---------------------------------------------------------


def test_spec_requires_rules_list():
    with pytest.raises(ev.EvaluatorError):
        ev.SpecEvaluator({})
    with pytest.raises(ev.EvaluatorError):
        ev.SpecEvaluator({"rules": "nope"})


# -- matching ----------------------------------------------------------------


def test_rule_matches_on_event_type_and_labels():
    spec = ev.SpecEvaluator({"rules": [{
        "on": "task.completed",
        "when": {"labels_any": ["recipe:reviewer"], "status": "completed"},
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


def test_rule_skipped_on_wrong_event_type():
    spec = ev.SpecEvaluator({"rules": [{"on": "task.abandoned",
                                        "emit": {"title_template": "x"}}]})
    decisions = spec.evaluate(_completed_event())
    assert isinstance(decisions[0], ev.NoOp)


def test_when_labels_all_and_source_predicates():
    spec = ev.SpecEvaluator({"rules": [{
        "on": ["task.completed"],
        "when": {"labels_all": ["a", "b"], "source": "recipe"},
        "emit": {"title_template": "ok"},
    }]})
    assert isinstance(spec.evaluate(_completed_event(labels=("a",)))[0], ev.NoOp)
    assert isinstance(
        spec.evaluate(_completed_event(labels=("a", "b")))[0], ev.Emit
    )


def test_first_matching_rule_wins():
    spec = ev.SpecEvaluator({"rules": [
        {"on": "task.completed", "when": {"status": "queued"},
         "emit": {"title_template": "first"}},
        {"on": "task.completed", "emit": {"title_template": "second"}},
    ]})
    emit = spec.evaluate(_completed_event())[0]
    assert emit.title == "second"


def test_emit_rule_without_title_template_raises():
    spec = ev.SpecEvaluator({"rules": [{"on": "task.completed", "emit": {}}]})
    with pytest.raises(ev.EvaluatorError):
        spec.evaluate(_completed_event())


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


def test_evaluate_and_apply_dry_run_creates_nothing():
    def creator(*a, **k):  # pragma: no cover - dry run must not create
        raise AssertionError("dry run must not create")

    spec = ev.SpecEvaluator({"rules": [{"on": "task.completed",
                                        "emit": {"title_template": "x"}}]})
    report = ev.evaluate_and_apply(
        spec, _completed_event(), creator=creator, apply=False
    )
    assert "applied" not in report
    assert report["decisions"][0]["decision"] == "emit"


# -- CLI ---------------------------------------------------------------------


def test_cli_parses_evaluate():
    a = _args(["evaluate", "--spec", "s.json"])
    assert a.func is _cmd_evaluate


def test_cmd_evaluate_dry_run_reads_event_file(tmp_path, capsys):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"rules": [{
        "on": "task.completed",
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
