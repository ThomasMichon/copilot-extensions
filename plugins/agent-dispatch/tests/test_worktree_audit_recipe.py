from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_dispatch import recipes, worker_charter
from agent_dispatch.__main__ import build_parser
from agent_dispatch.recipes import worktree_audit

pytestmark = pytest.mark.guard


def _params(tmp_path):
    return {
        "scope": "fixture-project", "candidates": '["wt-b","wt-a"]',
        "budget_minutes": "10", "report_dir": str(tmp_path),
    }


def _report(values):
    candidates = json.loads(values["candidates"])
    return {
        "schema": worktree_audit.SCHEMA, "scope": values["scope"],
        "candidates": candidates, "budget_minutes": int(values["budget_minutes"]),
        "report_dir": values["report_dir"], "elapsed_seconds": 12,
        "receipts": [
            {
                "candidate": candidate, "decision": "evidence-safety-hold",
                "remaining": ["Transcript unavailable"], "proposed_steering": [],
                "evidence": {"liveness": "bundle/facts/liveness"},
                "actions_taken": [],
            }
            for candidate in candidates
        ],
    }


def _args(command, values):
    argv = ["recipes", command]
    if command != "check-report":
        argv.append("worktree-audit")
    for key, value in values.items():
        argv += ["--param", f"{key}={value}"]
    return argv


def test_render_has_bounded_report_only_contract_and_stable_roster(tmp_path):
    rendered = recipes.render_recipe("worktree-audit", _params(tmp_path))
    assert rendered.require_verification is True
    assert rendered.params["candidates"] == '["wt-a","wt-b"]'
    assert rendered.resolution == "report-submitted-for-independent-verification"
    assert "mode:report-only" in rendered.labels
    assert worktree_audit.CHARTER in rendered.prompt
    assert worker_charter.charter_text("worktree-audit") == worktree_audit.CHARTER
    reordered = {**_params(tmp_path), "candidates": '["wt-a","wt-b"]'}
    assert recipes.dedup_key_for(rendered) == recipes.dedup_key_for(
        recipes.render_recipe("worktree-audit", reordered),
    )


@pytest.mark.parametrize("key,value", [
    ("scope", "../project"), ("scope", "a" * 65), ("candidates", "[not-json"),
    ("candidates", "[]"), ("candidates", '["wt-a","wt-a"]'),
    ("candidates", '["../wt"]'), ("candidates", '[true]'),
    ("candidates", json.dumps([f"wt-{i}" for i in range(6)])),
    ("budget_minutes", "0"), ("budget_minutes", "31"),
    ("budget_minutes", "NaN"), ("report_dir", "relative"),
    ("report_dir", "/private\nnew-instruction"),
])
def test_render_rejects_unbounded_or_ambiguous_parameters(tmp_path, key, value):
    with pytest.raises(recipes.RecipeError):
        recipes.render_recipe("worktree-audit", {**_params(tmp_path), key: value})


def test_comma_roster_matches_json_roster_on_windows_friendly_surface(tmp_path):
    values = _params(tmp_path)
    comma = recipes.render_recipe("worktree-audit", {**values, "candidates": "wt-b,wt-a"})
    array = recipes.render_recipe("worktree-audit", values)
    assert comma.params == array.params
    assert recipes.dedup_key_for(comma) == recipes.dedup_key_for(array)


def test_scope_accepts_canonical_64_character_boundary(tmp_path):
    rendered = recipes.render_recipe("worktree-audit", {**_params(tmp_path), "scope": "a" * 64})
    assert len(rendered.params["scope"]) == 64


def test_kick_requires_verification_and_sets_scope_exclusivity(tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(
        "agent_dispatch.__main__._cmd_create", lambda args: captured.append(args) or 0,
    )
    args = build_parser().parse_args(_args("kick", _params(tmp_path)))
    assert args.func(args) == 0
    assert captured[0].require_verification is True
    assert captured[0].exclusive_key == "worktree-audit:fixture-project"
    assert captured[0].spawn is False
    ordinary = build_parser().parse_args(["create", captured[0].title])
    assert vars(ordinary).keys() <= vars(captured[0]).keys()
    assert captured[0].remote_create_envelope is None
    assert captured[0].producer_capability is None
    assert captured[0].supersede_exclusive_key is False
    bypass = build_parser().parse_args(
        [*_args("kick", _params(tmp_path)), "--no-require-verification"],
    )
    assert bypass.func(bypass) == 2
    assert len(captured) == 1


@pytest.mark.parametrize("signal", ["start", "idle", "done", "rejected", "abandoned", "merged"])
def test_drive_execute_never_resolves_or_spawns_waiters(signal, monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("an audit drive must not mutate or launch a waiter")

    monkeypatch.setattr("agent_dispatch.__main__._run_resolution_step", forbidden)
    monkeypatch.setattr("agent_dispatch.__main__._spawn_detached_waiter", forbidden)
    args = build_parser().parse_args(
        ["recipes", "drive", "worktree-audit", "--signal", signal, "--execute"],
    )
    assert args.func(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["executed"] is False
    assert output["action"]["kind"] == "work"


def test_report_checker_accepts_holds_without_attesting_truth(tmp_path, capsys):
    values = recipes.render_recipe("worktree-audit", _params(tmp_path)).params
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report(values)), encoding="utf-8")
    args = build_parser().parse_args(
        [*_args("check-report", values), "--report", str(report)],
    )
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["semantic_verification"] is False


def test_report_accounts_for_concluded_and_budget_exhausted_candidates(tmp_path):
    values = recipes.render_recipe("worktree-audit", _params(tmp_path)).params
    report = _report(values)
    report["elapsed_seconds"] = int(values["budget_minutes"]) * 60
    report["receipts"][0].update(
        decision="concluded", remaining=[],
        evidence={domain: f"evidence/{domain}" for domain in worktree_audit.EVIDENCE_DOMAINS},
    )
    report["receipts"][1].update(
        decision="not-reviewed", remaining=["Time budget exhausted"], evidence={},
    )
    worktree_audit.validate_report(report, values)


@pytest.mark.parametrize("mode", ["bom", "oversized", "missing"])
def test_report_file_contract_preserves_bom_and_surfaces_read_failures(tmp_path, mode, capsys):
    values = recipes.render_recipe("worktree-audit", _params(tmp_path)).params
    report = tmp_path / "report.json"
    if mode == "bom":
        report.write_bytes(b"\xef\xbb\xbf" + json.dumps(_report(values)).encode("utf-8"))
    elif mode == "oversized":
        report.write_bytes(b"x" * 65537)
    args = build_parser().parse_args(
        [*_args("check-report", values), "--report", str(report)],
    )
    assert args.func(args) == (0 if mode == "bom" else 2)
    if mode != "bom":
        assert "agent-dispatch:" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["outside", "traversal", "sibling-prefix", "symlink"])
def test_checker_rejects_outside_report_before_reading(tmp_path, mode, monkeypatch, capsys):
    root = tmp_path / "private"
    root.mkdir()
    values = recipes.render_recipe(
        "worktree-audit", {**_params(tmp_path), "report_dir": str(root)},
    ).params
    outside_dir = tmp_path / ("private-other" if mode == "sibling-prefix" else "outside")
    outside_dir.mkdir()
    outside = outside_dir / "report.json"
    outside.write_text(json.dumps(_report(values)), encoding="utf-8")
    selected = outside
    if mode == "traversal":
        selected = root / ".." / "outside" / "report.json"
    elif mode == "symlink":
        selected = root / "report.json"
        try:
            selected.symlink_to(outside)
        except OSError as exc:
            pytest.skip(f"native symlink creation unavailable: {exc}")
    original_open = Path.open

    def refuse_outside_read(path, *args, **kwargs):
        if path.resolve() == outside.resolve():
            pytest.fail("outside report was opened before containment rejection")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refuse_outside_read)
    args = build_parser().parse_args(
        [*_args("check-report", values), "--report", str(selected)],
    )
    assert args.func(args) == 2
    assert "inside the approved report_dir" in capsys.readouterr().err


def test_checker_requires_existing_directory_not_a_file(tmp_path, capsys):
    not_directory = tmp_path / "file"
    not_directory.write_text("not a directory", encoding="utf-8")
    values = {**_params(tmp_path), "report_dir": str(not_directory)}
    args = build_parser().parse_args(
        [*_args("check-report", values), "--report", str(tmp_path / "report.json")],
    )
    assert args.func(args) == 2
    assert "existing directory" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["missing-root", "directory-report"])
def test_checker_explains_missing_directory_and_non_file_report(tmp_path, mode, capsys):
    values = _params(tmp_path)
    report = tmp_path / "nested"
    if mode == "missing-root":
        values["report_dir"] = str(tmp_path / "missing")
        expected = "existing directory"
    else:
        report.mkdir()
        expected = "regular file"
    args = build_parser().parse_args(
        [*_args("check-report", values), "--report", str(report)],
    )
    assert args.func(args) == 2
    assert expected in capsys.readouterr().err


def test_audit_drive_remains_non_resolving_if_resolution_wording_changes():
    from dataclasses import replace

    recipe = replace(recipes.get_recipe("worktree-audit"), resolution="reworded")
    assert recipes.decide(recipe, "abandoned").kind == "work"


@pytest.mark.parametrize("target", ["root", "report"])
def test_report_resolution_loop_is_validation_error_on_older_python(
    tmp_path, monkeypatch, capsys, target,
):
    values = recipes.render_recipe("worktree-audit", _params(tmp_path)).params
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_report(values)), encoding="utf-8")
    failing = tmp_path if target == "root" else report
    original_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        if path == failing:
            raise RuntimeError("Symlink loop")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    args = build_parser().parse_args(
        [*_args("check-report", values), "--report", str(report)],
    )
    assert args.func(args) == 2
    assert "symlink loop" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("fault", [
    "schema", "scope", "roster", "duplicate", "mutation", "partial-evidence",
    "remaining", "elapsed", "unsupported-evidence", "steering", "bool-budget",
])
def test_report_checker_rejects_incomplete_or_mutated_receipts(tmp_path, fault):
    values = recipes.render_recipe("worktree-audit", _params(tmp_path)).params
    report = copy.deepcopy(_report(values))
    receipt = report["receipts"][0]
    if fault == "schema":
        report["schema"] = "unknown"
    elif fault == "scope":
        report["scope"] = "other"
    elif fault == "roster":
        report["candidates"] = ["wt-c"]
    elif fault == "duplicate":
        report["receipts"][1] = receipt
    elif fault == "mutation":
        receipt["actions_taken"] = ["claims settle"]
    elif fault == "partial-evidence":
        receipt.update(decision="concluded", remaining=[])
    elif fault == "remaining":
        receipt["remaining"] = []
    elif fault == "elapsed":
        report["elapsed_seconds"] = float("nan")
    elif fault == "unsupported-evidence":
        receipt["evidence"] = {"assistant-final-prose": "done"}
    elif fault == "steering":
        receipt.update(
            decision="concluded", remaining=[], proposed_steering=["approve?"],
            evidence={domain: f"evidence/{domain}" for domain in worktree_audit.EVIDENCE_DOMAINS},
        )
    else:
        report["budget_minutes"] = True
    with pytest.raises(ValueError):
        worktree_audit.validate_report(report, values)


def test_actual_cli_render_is_service_free_and_preserves_explicit_contract(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src"
    env = {**os.environ, "PYTHONPATH": str(source)}
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-m", "agent_dispatch", *_args("render", _params(tmp_path))],
        capture_output=True, text=True, env=env, timeout=20, check=True, **flags,
    )
    rendered = json.loads(result.stdout)
    assert rendered["recipe"] == "worktree-audit"
    assert rendered["require_verification"] is True
    assert rendered["params"]["report_dir"] == str(tmp_path)
