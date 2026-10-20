"""Strict selection uses fork coordinates and never turns no evidence green."""
from __future__ import annotations

import json
import importlib.util
import argparse
import ast
import os
import shlex
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools.coverage_guided_selection import ancestor_resolution as ar
from tools.coverage_guided_selection import decide as decisions
from tools.coverage_guided_selection import execution


def _git(root: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *arguments],
        cwd=root, text=True, env=ar.scrubbed_git_env(),
    ).strip()


@pytest.fixture
def history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, dict]:
    source = tmp_path / "plugins/fixture/src"
    source.mkdir(parents=True)
    (source / "app.py").write_text("value = 2\nother = 3\n", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "fixture base")
    base = _git(tmp_path, "rev-parse", "HEAD")
    baseline = {
        "schema_version": 3, "plugin": "fixture", "measured_commit": base,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tests": {
            "tests/test_app.py::test_implicated": {"duration_s": 10},
            "tests/test_app.py::test_smoke": {"duration_s": 0.1},
        },
        "coverage": {
            "plugins/fixture/src/app.py": {
                "1": ["tests/test_app.py::test_implicated"],
                "2": ["tests/test_app.py::test_smoke"],
            },
        },
    }
    monkeypatch.setattr(
        decisions, "resolve_nearest_baseline",
        lambda *_a, **_k: ar.ResolvedBaseline(baseline=baseline, baseline_commit=base),
    )
    monkeypatch.setattr(decisions, "fetch_baseline_asset", lambda *_a, **_k: baseline)
    return tmp_path, base, baseline


def _plan(root: Path, base: str, **overrides) -> dict:
    options = {
        "repo_root": root, "repo": "example/project", "plugin": "fixture",
        "cov_source": "plugins/fixture/src", "base_ref": base, "head_ref": "HEAD",
        "main_ref": "main", "smoke_budget_s": 0.1,
        "commit_volume_threshold": 200, "age_threshold_seconds": 604800,
    }
    options.update(overrides)
    return execution.build_execution_payload(**options)


def test_replacement_selects_old_coverage_and_adds_fallback_without_budget_truncation(history):
    root, base, _ = history
    (root / "plugins/fixture/src/app.py").write_text("value = 999\nother = 3\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "runtime replacement")
    payload = _plan(root, base)
    assert payload["fork_commit"] == base
    assert payload["mode"] == "fallback"
    assert set(payload["selection"]["nodes"]) == {
        "tests/test_app.py::test_implicated", "tests/test_app.py::test_smoke",
    }
    assert payload["fallback_set"]["total_runtime_s"] == 0.1
    assert payload["selection"]["include_guards"]


def test_empty_diff_still_executes_the_measured_cross_cutting_floor(history):
    root, base, _ = history
    payload = _plan(root, base)
    assert payload["mode"] == "selected"
    assert payload["selection"]["nodes"] == ["tests/test_app.py::test_smoke"]


def test_changed_new_test_files_and_out_of_scope_changes_remain_visible(history):
    root, base, _ = history
    tests = root / "plugins/fixture/tests"
    tests.mkdir()
    (tests / "test_new.py").write_text("def test_added(): pass\n", encoding="utf-8")
    (tests / "conftest.py").write_text("# fixture dependency\n", encoding="utf-8")
    (root / "shared.py").write_text("dependency = True\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "new tests and shared dependency")
    payload = _plan(root, base)
    assert payload["selection"]["files"] == ["tests/test_new.py"]
    assert payload["mode"] == "fallback"
    assert {reason["file"] for reason in payload["selection_fallback_reasons"]} == {
        "plugins/fixture/tests/test_new.py", "plugins/fixture/tests/conftest.py", "shared.py",
    }


def test_partial_test_split_keeps_both_files_and_does_not_invent_moved_node_credit(history):
    root, _, baseline = history
    tests = root / "plugins/fixture/tests"
    tests.mkdir()
    old = tests / "test_app.py"
    old.write_text(
        "def test_implicated(): pass\n\ndef test_smoke(): pass\n", encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "original test module")
    base = _git(root, "rev-parse", "HEAD")
    baseline["measured_commit"] = base
    old.write_text("def test_smoke(): pass\n", encoding="utf-8")
    (tests / "test_helpers.py").write_text(
        "def test_implicated(): pass\n", encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "mechanical partial split")

    payload = _plan(root, base, smoke_budget_s=20)

    assert payload["mode"] == "fallback"
    assert payload["changed_test_files"] == [
        "tests/test_app.py", "tests/test_helpers.py",
    ]
    assert payload["selection"]["files"] == payload["changed_test_files"]
    assert "tests/test_app.py::test_implicated" in payload["selection"]["nodes"]
    assert "tests/test_helpers.py::test_implicated" not in payload["selection"]["nodes"]
    assert {reason["file"] for reason in payload["selection_fallback_reasons"]} == {
        "plugins/fixture/tests/test_app.py", "plugins/fixture/tests/test_helpers.py",
    }


@pytest.mark.parametrize("change", ["insertion", "deletion", "new", "binary", "rename"])
def test_unusual_diff_shapes_do_not_disappear(history, change: str):
    root, base, _ = history
    source = root / "plugins/fixture/src/app.py"
    if change == "insertion":
        source.write_text("new = 1\nvalue = 2\nother = 3\n", encoding="utf-8")
    elif change == "deletion":
        source.write_text("other = 3\n", encoding="utf-8")
    elif change == "new":
        (source.parent / "new.py").write_text("new = 1\n", encoding="utf-8")
    elif change == "binary":
        source.write_bytes(b"\x00binary")
    else:
        source.rename(source.with_name("renamed.py"))
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "changed source shape")
    payload = _plan(root, base)
    assert payload["selection"]["nodes"]
    if change == "deletion":
        assert "tests/test_app.py::test_implicated" in payload["selection"]["nodes"]
    else:
        assert payload["mode"] == "fallback"


@pytest.mark.parametrize("failure", ["missing", "fetch", "wrong-plugin"])
def test_no_usable_baseline_explicitly_runs_full_default(history, monkeypatch, failure: str):
    root, base, baseline = history
    if failure == "missing":
        monkeypatch.setattr(decisions, "resolve_nearest_baseline", lambda *_a, **_k: None)
    elif failure == "wrong-plugin":
        baseline["plugin"] = "other"
    else:
        def fetch(*_args, **_kwargs):
            raise decisions.BaselineFetchError("fixture unavailable")
        monkeypatch.setattr(decisions, "fetch_baseline_asset", fetch)
    payload = _plan(root, base)
    assert payload["execution"] == "full"
    assert payload["selection"] is None


@pytest.mark.parametrize("debt", ["age", "commits"])
def test_debt_saturation_is_audited_and_curates_real_smoke(history, debt: str):
    root, base, baseline = history
    options = {}
    if debt == "age":
        baseline["generated_at"] = "2000-01-01T00:00:00+00:00"
    else:
        (root / "earlier.txt").write_text("intervening commit\n", encoding="utf-8")
        _git(root, "add", ".")
        _git(root, "commit", "-qm", "intervening commit")
        _git(root, "commit", "--allow-empty", "-qm", "second intervening commit")
        base = _git(root, "rev-parse", "HEAD")
        options["commit_volume_threshold"] = 1
    payload = _plan(root, base, **options)
    assert payload["mode"] == "fallback"
    assert payload["debt"]["exceeded"]
    assert payload["selection"]["nodes"] == ["tests/test_app.py::test_smoke"]


@pytest.mark.parametrize("debt", ["age", "commits"])
def test_exceeded_debt_cannot_drop_an_implicated_test_over_the_smoke_budget(history, debt):
    root, base, baseline = history
    options = {}
    baseline["tests"]["tests/test_app.py::test_implicated"]["duration_s"] = 121
    if debt == "age":
        baseline["generated_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    else:
        for index in range(2):
            _git(root, "commit", "--allow-empty", "-qm", f"intervening commit {index}")
        base = _git(root, "rev-parse", "HEAD")
        options["commit_volume_threshold"] = 1
    (root / "plugins/fixture/src/app.py").write_text("value = 999\nother = 3\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "runtime regression after coverage debt")
    payload = _plan(root, base, smoke_budget_s=120, **options)
    assert payload["mode"] == "fallback"
    assert payload["debt"]["exceeded"]
    assert payload["reason"].startswith("coverage_debt:")
    assert payload["fallback_set"]["selected_tests"] == ["tests/test_app.py::test_smoke"]
    assert set(payload["selection"]["nodes"]) == {
        "tests/test_app.py::test_implicated", "tests/test_app.py::test_smoke",
    }
    assert payload["fallback_set"]["total_runtime_s"] <= 120


def test_empty_curated_evidence_is_a_hard_error_not_only_guards(history):
    root, base, _ = history
    with pytest.raises(ValueError, match="empty selection"):
        _plan(root, base, smoke_budget_s=0.00001)


@pytest.mark.parametrize("failure", ["budget", "invalid-timings"])
def test_implicated_tests_cannot_hide_an_empty_cross_cutting_floor(history, failure):
    root, base, baseline = history
    (root / "plugins/fixture/src/app.py").write_text("value = 999\nother = 3\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "implicated test with no affordable floor")
    budget = 120
    for info in baseline["tests"].values():
        info["duration_s"] = 121 if failure == "budget" else None
    decision = decisions.decide(
        root, "example/project", "fixture", base,
        {"plugins/fixture/src/app.py": [0, 1]},
        fallback_runtime_budget_s=budget, include_smoke_floor=True,
    )
    assert decision.selected_tests == ("tests/test_app.py::test_implicated",)
    assert decision.fallback_set is not None
    assert decision.fallback_set["selected_tests"] == []
    with pytest.raises(ValueError, match="empty selection for the smoke floor"):
        _plan(root, base, smoke_budget_s=budget)


@pytest.mark.parametrize("mode,exit_code", [("selected", 0), ("selected", 7), ("full", 0)])
def test_execution_cli_preserves_failure_and_reports_complete_invocation(tmp_path, monkeypatch, capfd, mode, exit_code):
    script = Path(__file__).resolve().parent / "run-coverage-selected-tests.py"
    spec = importlib.util.spec_from_file_location("coverage_execution_cli", script)
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    selection = {
        "version": 1, "plugin": "agent-dispatch",
        "nodes": [f"tests/test_x.py::test_x_{index}" for index in range(3000)],
        "files": [], "include_guards": True,
    }
    reason = ("fixture `tick`\n</code> <unsafe> &" * 200)[:6000]
    payload = {
        "execution": mode, "selection": selection if mode == "selected" else None,
        "reason": reason, "baseline_generation": "fixture-generation",
    }
    monkeypatch.setattr(cli, "build_execution_payload", lambda **_k: payload)
    commands = []
    manifests = []

    def run(command, **kwargs):
        commands.append(command)
        if "--selection-file" in command:
            path = Path(command[command.index("--selection-file") + 1])
            assert json.loads(path.read_text()) == selection
            manifests.append(path)
        return subprocess.CompletedProcess(command, exit_code)

    monkeypatch.setattr(cli.subprocess, "run", run)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    output = tmp_path / "decision.json"
    assert cli.main([
        "--repo", "example/project", "--plugin", "agent-dispatch", "--base-ref", "fixture",
        "--smoke-budget", "120", "--commit-volume-threshold", "200",
        "--age-threshold-seconds", "604800", "--out", str(output),
    ]) == exit_code
    decision = json.loads(output.read_text())
    assert decision["exit_code"] == exit_code
    assert decision["invocation_seconds"] >= 0
    summary_text = summary.read_text()
    assert "Complete invocation" in summary_text
    assert "<unsafe>" not in summary_text
    assert "&lt;/code&gt; &lt;unsafe&gt; &amp;" in summary_text
    assert "reason: <code>" in summary_text
    console = capfd.readouterr().out
    assert len(console) < 2000
    assert "tests/test_x.py" not in console
    assert json.loads(console.splitlines()[0])["selected_node_count"] == (
        3000 if mode == "selected" else None
    )
    assert decision["reason"] == reason
    if mode == "selected":
        assert commands[0][-4:] == ["--timeout", "540", "--plugin-timeout", "540"]
        assert manifests and all(not path.exists() for path in manifests)
    else:
        assert commands[0][-1] == "agent-dispatch"
        assert "--selection-file" not in commands[0]


def _policy_value(expression, *, plugin: str, event: str):
    """Evaluate this lane's boolean/numeric policy without executing code."""
    if not isinstance(expression, str):
        return expression
    source = expression.strip().removeprefix("${{").removesuffix("}}").strip()
    node = ast.parse(source.replace("&&", " and ").replace("||", " or "), mode="eval").body
    context = {"matrix.plugin": plugin, "github.event_name": event, "true": True, "false": False}

    def value(part):
        if isinstance(part, ast.Constant):
            return part.value
        if isinstance(part, (ast.Name, ast.Attribute)):
            return context[ast.unparse(part)]
        if isinstance(part, ast.Compare) and len(part.ops) == 1:
            left, right = value(part.left), value(part.comparators[0])
            if isinstance(part.ops[0], ast.Eq):
                return left == right
            if isinstance(part.ops[0], ast.NotEq):
                return left != right
        if isinstance(part, ast.BoolOp):
            result = value(part.values[0])
            for child in part.values[1:]:
                if isinstance(part.op, ast.And):
                    result = value(child) if result else result
                else:
                    result = result if result else value(child)
            return result
        raise AssertionError(f"unsupported workflow policy: {ast.dump(part)}")

    return value(node)


@pytest.mark.parametrize("expression,expected", [
    ("github.event_name=='pull_request' && matrix.plugin=='agent-dispatch'", True),
    ("${{ (matrix.plugin == 'agent-dispatch') && (github.event_name == 'pull_request') }}", True),
    ("(matrix.plugin == 'agent-index' || matrix.plugin == 'agent-dispatch') && 12 || 8", 12),
    ("matrix.plugin != 'agent-dispatch' || github.event_name != 'pull_request'", False),
    ("matrix.plugin == 'agent-dispatch' && 1200 || 8", 1200),
])
def test_workflow_policy_checks_values_not_expression_format(expression, expected):
    assert _policy_value(expression, plugin="agent-dispatch", event="pull_request") == expected


def test_workflow_policy_never_executes_function_calls():
    with pytest.raises(AssertionError, match="unsupported workflow policy"):
        _policy_value("make_green()", plugin="agent-dispatch", event="pull_request")


def test_ci_pilot_is_pr_only_and_does_not_increase_job_deadline():
    import yaml

    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(),
    )
    job = workflow["jobs"]["smoke"]
    selected = next(
        step for step in job["steps"]
        if "tools/run-coverage-selected-tests.py" in shlex.split(step.get("run", ""))
    )
    ordinary = next(
        step for step in job["steps"]
        if "tools/run-plugin-tests.py" in shlex.split(step.get("run", ""))
    )
    for plugin in ("agent-dispatch", "agent-index", "agent-bridge", "other", "agent-dispatch-other"):
        for event in ("pull_request", "push", "workflow_dispatch", "pull_request_target"):
            context = {"plugin": plugin, "event": event}
            expected_deadline = 12 if plugin in {"agent-dispatch", "agent-index", "agent-bridge"} else 8
            assert _policy_value(job["timeout-minutes"], **context) == expected_deadline
            is_pilot = plugin == "agent-dispatch" and event == "pull_request"
            assert bool(_policy_value(selected.get("if", "true"), **context)) == is_pilot
            assert bool(_policy_value(ordinary.get("if", "true"), **context)) != is_pilot
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--smoke-budget", type=float)
    parser.add_argument("--commit-volume-threshold", type=int)
    parser.add_argument("--age-threshold-seconds", type=float)
    budgets, _ = parser.parse_known_args(shlex.split(selected["run"]))
    assert budgets.smoke_budget == 120
    assert budgets.commit_volume_threshold == 200
    assert budgets.age_threshold_seconds == 604800
    assert workflow["env"]["COVERAGE_GUIDED_SELECTION_MODE"] == "shadow"
    full = next(
        shlex.split(step["run"])
        for step in workflow["jobs"]["full"]["steps"]
        if "tools/run-plugin-tests.py" in shlex.split(step.get("run", ""))
    )
    assert not {"--selection-file", "--guards", "--collect-only", "--test-file", "-k"} & set(full)


def test_cli_missing_suite_cannot_turn_full_fallback_into_a_successful_skip(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parent / "run-coverage-selected-tests.py"
    spec = importlib.util.spec_from_file_location("coverage_execution_missing_suite", script)
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "REPO", tmp_path)
    monkeypatch.setattr(
        cli, "build_execution_payload", lambda **_k: pytest.fail("planned an absent suite"),
    )
    with pytest.raises(SystemExit) as error:
        cli.main([
            "--repo", "example/project", "--plugin", "agent-dispatch", "--base-ref", "fixture",
            "--smoke-budget", "120", "--commit-volume-threshold", "200",
            "--age-threshold-seconds", "604800", "--out", str(tmp_path / "decision.json"),
        ])
    assert error.value.code == 2


def _run_discovery(
    history: tuple[Path, str, dict], changed: str, event: str, listed: tuple[str, ...] = (),
) -> dict[str, str]:
    import yaml

    root, _, _ = history
    tools = root / "tools"
    tools.mkdir()
    stub = tools / "run-plugin-tests.py"
    stub.write_text(f"print({json.dumps(list(listed))!r})\n", encoding="utf-8")
    shutil.copyfile(
        Path(__file__).resolve().parent / "ci_plugin_partitions.py",
        tools / "ci_plugin_partitions.py",
    )
    _git(root, "add", "tools/run-plugin-tests.py", "tools/ci_plugin_partitions.py")
    _git(root, "commit", "-qm", "discovery list stub")
    base = _git(root, "rev-parse", "HEAD")
    target = root / changed
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write("# changed input\n")
    _git(root, "add", changed)
    _git(root, "commit", "-qm", "changed discovery input")
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(),
    )
    script = next(
        step["run"] for step in workflow["jobs"]["discover"]["steps"]
        if step.get("id") == "set"
    ).replace("${{ github.event_name }}", event).replace(
        "${{ github.event.pull_request.base.sha }}", base,
    )
    output = root / "discovery-output"
    env = ar.scrubbed_git_env()
    env.update(HEAVY_PLUGIN="agent-worktrees", GITHUB_OUTPUT=str(output))
    subprocess.run(
        ["bash", "-e", "-c", script], cwd=root, env=env,
        capture_output=True, text=True, check=True, timeout=30,
    )
    return dict(line.split("=", 1) for line in output.read_text().splitlines())


@pytest.mark.skipif(os.name == "nt", reason="the discovery job executes Bash on Linux")
@pytest.mark.parametrize("changed,event,expected", [
    ("tools/coverage_guided_selection/fallback.py", "pull_request", True),
    ("tools/run-coverage-selected-tests.py", "pull_request", True),
    ("tools/run-plugin-tests.py", "pull_request", True),
    ("tools/ci_plugin_partitions.py", "pull_request", True),
    ("tools/plugin_test_selection.py", "pull_request", True),
    ("tools/plugin_test_containment.py", "pull_request", True),
    ("tools/pytest_portfolio_guard.py", "pull_request", True),
    ("tools/_devcontainer_selection.py", "pull_request", True),
    ("tools/_devcontainer_network_scope.py", "pull_request", True),
    (".github/workflows/ci.yml", "pull_request", True),
    ("docs/unrelated.md", "pull_request", False),
    ("tools/unrelated.py", "pull_request", False),
    ("tools/plugin_test_selection.py", "push", False),
])
def test_discovery_runs_dispatch_for_its_selection_inputs(history, changed, event, expected):
    values = _run_discovery(history, changed, event)
    assert json.loads(values["smoke"]) == (["agent-dispatch"] if expected else [])
    assert values["heavy"] == "false"
    assert json.loads(values["smoke_matrix"]) == (
        [{"plugin": "agent-dispatch", "partition": 0, "partitions": 1, "name": "agent-dispatch"}]
        if expected else []
    )


@pytest.mark.skipif(os.name == "nt", reason="the discovery job executes Bash on Linux")
@pytest.mark.parametrize("event", ["pull_request", "push", "workflow_dispatch"])
def test_discovery_keeps_full_partition_defaults_outside_the_pr_pilot(history, event):
    from tools.ci_plugin_partitions import smoke_matrix

    listed = ("agent-dispatch", "agent-bridge")
    values = _run_discovery(history, "docs/unrelated.md", event, listed)
    rows = json.loads(values["smoke_matrix"])
    override = frozenset({"agent-dispatch"}) if event == "pull_request" else frozenset()
    assert rows == smoke_matrix(list(listed), single_job_plugins=override)
    assert len({row["name"] for row in rows}) == len(rows)
    assert sum(row["plugin"] == "agent-dispatch" for row in rows) == (
        1 if event == "pull_request" else 3
    )


@pytest.mark.skipif(
    os.environ.get("CGS_RUN_INTEGRATION_TEST") != "1",
    reason="opt-in real coverage collection and contained runtime regression",
)
@pytest.mark.parametrize("stale", [False, True])
def test_runtime_covered_regression_is_selected_and_fails(tmp_path, monkeypatch, stale):
    from tools.coverage_guided_selection.baseline import collect_baseline
    from tools.test_plugin_test_selection import runner

    plugin = tmp_path / "plugins/fixture"
    source = plugin / "src"
    tests = plugin / "tests"
    source.mkdir(parents=True)
    tests.mkdir()
    (source / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    (source / "other.py").write_text("def cheap():\n    return True\n", encoding="utf-8")
    (plugin / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths=["tests"]\n', encoding="utf-8",
    )
    (tests / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))\n",
        encoding="utf-8",
    )
    (tests / "test_app.py").write_text(
        "import time\nfrom app import value\nfrom other import cheap\n"
        "def test_implicated():\n    time.sleep(.1)\n    assert value() == 2\n"
        "def test_smoke():\n    assert cheap()\n",
        encoding="utf-8",
    )
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "clean runtime fixture")
    base = _git(tmp_path, "rev-parse", "HEAD")
    baseline = collect_baseline(
        cwd=tmp_path, test_path="plugins/fixture/tests", cov_source="plugins/fixture/src",
        plugin="fixture", measured_commit=base,
    )
    if stale:
        baseline["generated_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    hot = "tests/test_app.py::test_implicated"
    cold = "tests/test_app.py::test_smoke"
    assert hot in baseline["coverage"]["plugins/fixture/src/app.py"]["2"]
    budget = baseline["tests"][cold]["duration_s"] + 0.000001
    assert baseline["tests"][hot]["duration_s"] > budget
    monkeypatch.setattr(
        decisions, "resolve_nearest_baseline",
        lambda *_a, **_k: ar.ResolvedBaseline(baseline, base),
    )
    monkeypatch.setattr(decisions, "fetch_baseline_asset", lambda *_a, **_k: baseline)
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    # Use a real managed environment, just like ordinary contained execution.
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    (runner.VENV_ROOT / "fixture").mkdir(parents=True)
    monkeypatch.setattr(runner, "_has_pyproject", lambda _name: False)
    manifest = tmp_path / "selection.json"
    clean = _plan(tmp_path, base, smoke_budget_s=budget)
    assert bool(clean["debt"]["exceeded"]) is stale
    assert hot not in clean["selection"]["nodes"]  # the smoke budget cannot buy it
    manifest.write_text(json.dumps(clean["selection"]), encoding="utf-8")
    kwargs = dict(
        reinstall=False, kexpr=None, limits=runner.Limits(),
        plugin_timeout=60, test_timeout=10, max_files_per_subsuite=25,
        selection_file=manifest,
    )
    assert runner.run_plugin("fixture", "uv", **kwargs) == 0
    (source / "app.py").write_text("def value():\n    return 999\n", encoding="utf-8")
    _git(tmp_path, "add", "plugins/fixture/src/app.py")
    _git(tmp_path, "commit", "-qm", "introduce runtime regression")
    broken = _plan(tmp_path, base, smoke_budget_s=budget)
    assert bool(broken["debt"]["exceeded"]) is stale
    assert hot in broken["selection"]["nodes"]
    manifest.write_text(json.dumps(broken["selection"]), encoding="utf-8")
    assert runner.run_plugin("fixture", "uv", **kwargs) == 1
    # Restore the value with different byte length to avoid stale .pyc aliases.
    (source / "app.py").write_text("def value():\n    return 2  # fixed\n", encoding="utf-8")
    assert runner.run_plugin("fixture", "uv", **kwargs) == 0
