"""Pure workflow-data rejection cases for the compact unit-only CI profile."""

import builtins
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

SPEC = importlib.util.spec_from_file_location(
    "promotion_unit_trusted_ci", Path(__file__).resolve().parents[1] / "check-trusted-ci.py",
)
assert SPEC is not None and SPEC.loader is not None
GUARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GUARD)


def declarations():
    routing = {
        "name": "Trusted CI periodic routing", "on": {"workflow_dispatch": ""},
        "permissions": {}, "jobs": {"periodic-route": {
            "runs-on": "ubuntu-latest", "permissions": {}, "steps": [{
                "name": "Identify the containerized integration destination",
                "run": 'echo "Integration runs in windows-coverage-rotation.yml; PR CI admits units only."',
            }],
        }},
    }
    ci = {
        "name": "CI",
        "on": {"pull_request": {
            "branches-ignore": ["main"],
            "types": ["opened", "reopened", "synchronize", "edited"],
        }},
        "env": {"PROMOTION_UNIT_PROFILE": "copilot-extensions.promotion-units/v1"},
        "jobs": {
            "checks": {"runs-on": "ubuntu-latest", "steps": [{
                "name": "Trusted self-hosted CI contract",
                "run": "python tools/check-trusted-ci.py",
            }, {
                "name": "Marketplace isolation inventory (report-only)",
                "if": "github.event_name != 'pull_request'",
                "run": "python tools/check-marketplace-isolation.py",
            }]},
            "unit-tests": {
                "runs-on": "ubuntu-latest", "timeout-minutes": 5,
                "outputs": {
                    "run": "${{ steps.impact.outputs.run }}",
                    "result": "${{ steps.units.outcome }}",
                },
                "steps": [{
                    "id": "impact", "run": "ci_requires_units(event, base, head, read_diff)",
                }, {
                    "id": "units", "if": GUARD.UNIT_CONDITION,
                    "run": 'python tools/run-promotion-unit-tests.py --source-sha "$GITHUB_SHA" '
                           '--output-dir "$RUNNER_TEMP/promotion-unit-evidence"',
                }],
            },
            "pr-gate": {
                "runs-on": "ubuntu-latest", "needs": ["checks", "unit-tests"],
                "steps": [{
                    "name": "Require actual admitted unit evidence when selected",
                    "env": {"RESULTS": "${{ toJson(needs) }}"},
                    "run": GUARD.UNIT_GATE_SCRIPT,
                }],
            },
        },
    }
    ci["jobs"]["checks"]["steps"].extend([{
        "id": "source-ast-impact",
        "timeout-minutes": 2,
        "env": {
            "EVENT": "${{ github.event_name }}",
            "BASE": "${{ github.event.pull_request.base.sha }}",
            "HEAD": "${{ github.event.pull_request.head.sha }}",
        },
        "run": GUARD.SOURCE_AST_SCRIPT,
    }, *({"run": command, "if": GUARD.SOURCE_AST_CONDITION} for command in GUARD.SOURCE_AST_COMMANDS)])
    return routing, ci


def validate(tmp_path, routing, ci):
    path = tmp_path / "trusted-ci.yml"
    path.write_text(yaml.safe_dump(routing), encoding="utf-8")
    (tmp_path / "ci.yml").write_text(yaml.safe_dump(ci), encoding="utf-8")
    return GUARD.validate_workflow(path)


def test_positive_compact_profile_has_no_self_hosted_pr_test_route(tmp_path):
    routing, ci = declarations()
    assert validate(tmp_path, routing, ci) == []


@pytest.mark.parametrize("change", [
    {"if": "false"}, {"continue-on-error": True}, {"timeout-minutes": 30},
    {"env": {}}, {"run": "echo run=false"},
])
def test_source_ast_admission_cannot_skip_failures_or_lose_pinned_inputs(tmp_path, change):
    routing, ci = declarations()
    ci["jobs"]["checks"]["steps"][2].update(change)
    assert "source AST guards require conservative pinned-input admission" in validate(tmp_path, routing, ci)


@pytest.mark.parametrize("change", [
    {"if": "false"}, {"continue-on-error": True}, {"run": "echo passed"},
])
@pytest.mark.parametrize("index", [3, 4, 5])
def test_source_ast_scan_commands_and_outcomes_cannot_be_weakened(tmp_path, change, index):
    routing, ci = declarations()
    ci["jobs"]["checks"]["steps"][index].update(change)
    assert "source AST guards must follow validated admission without weakening scans" in validate(tmp_path, routing, ci)


def test_source_ast_scans_cannot_precede_or_duplicate_admission(tmp_path):
    routing, ci = declarations()
    steps = ci["jobs"]["checks"]["steps"]
    steps[2], steps[3] = steps[3], steps[2]
    assert "source AST guards must follow validated admission without weakening scans" in validate(tmp_path, routing, ci)
    steps[2], steps[3] = steps[3], steps[2]
    steps.append(dict(steps[2]))
    assert "source AST guards require conservative pinned-input admission" in validate(tmp_path, routing, ci)


def execute_source_ast_selector(output, event, read_diff):
    routing, ci = declarations()
    assert validate(output.parent, routing, ci) == []
    modules = {
        "os": SimpleNamespace(environ={
            "EVENT": event, "BASE": "a" * 40, "HEAD": "b" * 40,
            "GITHUB_OUTPUT": str(output),
        }),
        "subprocess": SimpleNamespace(check_output=read_diff),
        "sys": SimpleNamespace(path=SimpleNamespace(insert=lambda *args: None)),
    }

    def imports(name, *args, **kwargs):
        return modules[name] if name in modules else builtins.__import__(name, *args, **kwargs)

    body = "\n".join(GUARD.SOURCE_AST_SCRIPT.splitlines()[1:-1])
    exec(compile(body, "<source-ast-impact>", "exec"), {
        "__builtins__": {**vars(builtins), "__import__": imports},
    })


@pytest.mark.parametrize("event,paths,expected", [
    ("pull_request", ["docs/example.md"], False),
    ("pull_request", ["TESTING.md", "efforts/active/example/README.md"], False),
    ("pull_request", ["docs/example.py"], True),
    ("pull_request", ["plugins/example/src/mod.py", "docs/moved.md"], True),
    ("pull_request", [".github/workflows/ci.yml"], True),
    ("pull_request", [], True),
    ("push", ["docs/example.md"], True),
    ("workflow_dispatch", [], True),
    ("future-event", [], True),
])
def test_actual_source_ast_selector_preserves_non_narrative_and_non_pr_execution(
    tmp_path, event, paths, expected,
):
    output = tmp_path / "outputs"
    calls = []

    def read_diff(argv):
        calls.append(argv)
        return ("\0".join(paths) + "\0").encode()

    execute_source_ast_selector(output, event, read_diff)
    assert output.read_text() == "run=" + str(expected).lower() + "\n"
    assert calls == ([
        ["git", "diff", "--no-renames", "--name-only", "-z", "a" * 40, "b" * 40],
    ] if event == "pull_request" else [])


@pytest.mark.parametrize("failure", ["unavailable", "encoding", "path"])
def test_source_ast_selector_errors_never_emit_skipped_success(tmp_path, failure):
    output = tmp_path / "outputs"

    def read_diff(argv):
        if failure == "unavailable":
            raise RuntimeError("pinned diff unavailable")
        return b"\xff\0" if failure == "encoding" else b"../outside\0"

    with pytest.raises((RuntimeError, UnicodeDecodeError, ValueError)):
        execute_source_ast_selector(output, "pull_request", read_diff)
    assert not output.exists()


def test_legacy_pr_integration_cannot_return_through_the_retired_workflow(tmp_path):
    routing, ci = declarations()
    routing["on"] = {"pull_request_target": {"types": ["opened"]}}
    assert "unit-only CI must retire the self-hosted PR integration route" in validate(tmp_path, routing, ci)


@pytest.mark.parametrize("command", [
    "python -m pytest -q tools/test_mixed.py",
    "node --test tests/mixed.test.mjs",
    "python tools/run-plugin-tests.py example",
    "python tools/run-standalone-tests.py example",
])
def test_generic_test_runners_cannot_hide_inside_an_operational_guard(tmp_path, command):
    routing, ci = declarations()
    ci["jobs"]["checks"]["steps"].append({"run": command})
    assert "unit-only CI must not invoke a generic or integration test runner" in validate(tmp_path, routing, ci)


@pytest.mark.parametrize("change", [
    {"if": "false"}, {"timeout-minutes": 30}, {"continue-on-error": True},
])
def test_unit_selection_budget_and_outcome_cannot_be_weakened(tmp_path, change):
    routing, ci = declarations()
    ci["jobs"]["unit-tests"].update(change)
    assert "unit packet must have the reviewed independent hosted five-minute job" in validate(tmp_path, routing, ci)


def test_extra_jobs_and_conditioned_unit_execution_are_refused(tmp_path):
    routing, ci = declarations()
    ci["jobs"]["full"] = {"runs-on": "ubuntu-latest", "steps": []}
    ci["jobs"]["unit-tests"]["steps"][1]["if"] = "false"
    errors = validate(tmp_path, routing, ci)
    assert "unit-only CI must contain only guards, combined units and aggregate" in errors
    assert "unit-only CI must execute the exact admitted packet once" in errors


def test_ungated_policy_guard_remains_mandatory(tmp_path):
    routing, ci = declarations()
    ci["jobs"]["checks"]["steps"][0]["continue-on-error"] = True
    assert "ci.yml must invoke the ungated trusted CI guard" in validate(tmp_path, routing, ci)


@pytest.mark.parametrize("change", [
    {"if": "false"}, {"if": "github.event_name == 'pull_request'"},
    {"run": "python tools/check-marketplace-isolation.py --strict"},
    {"continue-on-error": True},
])
def test_report_only_inventory_remains_available_without_repeating_in_pr_ci(tmp_path, change):
    routing, ci = declarations()
    ci["jobs"]["checks"]["steps"][1].update(change)
    assert "report-only inventory must remain on non-PR CI without becoming a strict PR gate" in validate(tmp_path, routing, ci)


@pytest.mark.parametrize("change", [
    {"needs": "checks"},
    {"outputs": {"run": "false", "result": "success"}},
])
def test_combined_units_cannot_serialize_behind_guards_or_fabricate_outcomes(tmp_path, change):
    routing, ci = declarations()
    ci["jobs"]["unit-tests"].update(change)
    assert "unit packet must have the reviewed independent hosted five-minute job" in validate(tmp_path, routing, ci)


@pytest.mark.parametrize("change", [
    {"if": "false"}, {"continue-on-error": True}, {"run": "print('skip')"},
])
def test_combined_impact_admission_cannot_be_skipped_or_ignored(tmp_path, change):
    routing, ci = declarations()
    ci["jobs"]["unit-tests"]["steps"][0].update(change)
    assert "combined units must resolve impact before conditional execution" in validate(tmp_path, routing, ci)


def test_combined_units_require_impact_before_execution(tmp_path):
    routing, ci = declarations()
    ci["jobs"]["unit-tests"]["steps"].reverse()
    assert "unit-only CI must execute the exact admitted packet once" in validate(tmp_path, routing, ci)


@pytest.mark.parametrize("change", [
    {"needs": ["unit-tests"]},
    {"continue-on-error": True},
    {"steps": [{"run": "echo success"}]},
])
def test_required_aggregate_cannot_omit_guards_or_accept_unverified_success(tmp_path, change):
    routing, ci = declarations()
    ci["jobs"]["pr-gate"].update(change)
    assert "unit aggregate must require successful guards and the actual selected outcome" in validate(tmp_path, routing, ci)


def execute_gate(tmp_path, results):
    routing, ci = declarations()
    assert validate(tmp_path, routing, ci) == []
    fake_os = SimpleNamespace(environ={"RESULTS": json.dumps(results)})

    def imports(name, *args, **kwargs):
        return fake_os if name == "os" else builtins.__import__(name, *args, **kwargs)

    body = "\n".join(GUARD.UNIT_GATE_SCRIPT.splitlines()[1:-1])
    exec(compile(body, "<unit-ci-gate>", "exec"), {
        "__builtins__": {**vars(builtins), "__import__": imports},
    })


@pytest.mark.parametrize("selected,outcome", [("true", "success"), ("false", "skipped")])
def test_actual_aggregate_accepts_only_selected_success_or_explicit_skip(tmp_path, selected, outcome):
    execute_gate(tmp_path, {
        "checks": {"result": "success"},
        "unit-tests": {"result": "success", "outputs": {"run": selected, "result": outcome}},
    })


@pytest.mark.parametrize("outputs", [
    {}, {"run": "true"}, {"run": True, "result": "success"},
    {"run": "false", "result": "success"}, {"run": "true", "result": "skipped"},
    {"run": "true", "result": "failure"}, {"run": "true", "result": "cancelled"},
])
def test_actual_aggregate_rejects_missing_invalid_or_mismatched_unit_outcomes(tmp_path, outputs):
    with pytest.raises(RuntimeError):
        execute_gate(tmp_path, {
            "checks": {"result": "success"},
            "unit-tests": {"result": "success", "outputs": outputs},
        })


@pytest.mark.parametrize("job", ["checks", "unit-tests"])
@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped"])
def test_actual_aggregate_rejects_unsuccessful_upstream_jobs(tmp_path, job, outcome):
    results = {
        "checks": {"result": "success"},
        "unit-tests": {"result": "success", "outputs": {"run": "true", "result": "success"}},
    }
    results[job]["result"] = outcome
    with pytest.raises(RuntimeError, match="source guard or unit-impact"):
        execute_gate(tmp_path, results)
