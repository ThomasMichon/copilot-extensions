"""Complete-once candidate recipes retain every file and every unique proof."""
from __future__ import annotations

import ast
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

import candidate_complete_once as complete
import candidate_qualification_inventory as inventory
from materialized_candidate_inventory import CandidateValidationError, python_suites
import qualify_promotion_candidate as qualification
from test_qualify_promotion_candidate import (
    _candidate_workflow, _qualify, _verify, backend as backend,
)
from verify_promotion_workflows import VerificationError

ROOT = Path(__file__).resolve().parents[1]
RESIDUAL = {
    "agent-conduct-guidance", "agent-index", "agent-machines", "agent-pull-requests",
    "ai-attribution", "budget-guidance", "context-handoff", "copilot-extensions-harness",
    "customizing-copilot", "efforts", "harness-knowledge",
}


def profile_text(text: str, *, partitioned: bool = True) -> str:
    if "CANDIDATE_PORTFOLIO_PROFILE:" not in text:
        text = re.sub(
            r"(?ms)^  full:\n.*?(?=^  [a-z][a-z0-9_-]*:|\Z)",
            lambda _: complete.FULL_JOB + "\n", text, count=1,
        )
        text = text.replace(
            "        name: Resolve target plugins\n",
            "        name: Resolve target plugins\n" + complete.DISCOVERY_ENV, 1,
        )
        text = text.replace(
            '          echo "Full set:  $FULL"\n',
            complete.DISCOVERY_FILTER + '          echo "Full set:  $FULL"\n', 1,
        )
    if not partitioned:
        text = text.replace("    name: ${{ matrix.name }}", "    name: ${{ matrix.plugin }}", 1)
        text = text.replace(
            "        include: ${{ fromJson(needs.discover.outputs.smoke_matrix) }}",
            "        plugin: ${{ fromJson(needs.discover.outputs.smoke) }}", 1,
        )
        text = text.replace(complete.PARTITIONED_SMOKE_WORKERS_STEP, complete.UNPARTITIONED_SMOKE_STEP)
        text = text.replace(complete.PARTITIONED_SMOKE_STEP, complete.UNPARTITIONED_SMOKE_STEP)
    return text


@pytest.fixture(params=[True, False], ids=["partitioned", "unpartitioned"])
def candidate_workflow(tmp_path, monkeypatch, request):
    path = tmp_path / "ci.yml"
    path.write_text(profile_text(
        (ROOT / ".github/workflows/ci.yml").read_text("utf-8"), partitioned=request.param,
    ), encoding="utf-8")
    original = inventory.regular

    def regular(root, relative):
        return path if relative == Path(".github/workflows/ci.yml") else original(root, relative)

    monkeypatch.setattr(inventory, "regular", regular)
    monkeypatch.setattr(complete, "regular", regular)
    return path


def test_discovery_and_qualification_share_exact_complete_owners(candidate_workflow):
    suites = python_suites(ROOT)
    plan = complete.complete_once_plan(candidate_workflow.read_text("utf-8"), suites)
    assert plan == {
        "profile": complete.PROFILE, "full": list(complete.FULL_OWNERS), "smoke": sorted(RESIDUAL),
    }
    assert set(plan["full"]).isdisjoint(plan["smoke"])
    assert set(plan["full"]) | set(plan["smoke"]) == suites
    assert complete.candidate_smoke(
        ROOT, sorted(suites - {"agent-worktrees"}), event="workflow_dispatch",
        promotion_run="123-1", phase="candidate", profile=complete.PROFILE,
    ) == sorted(RESIDUAL)
    required, skipped, digest = inventory.required_jobs(ROOT)
    partitioned = (
        "        include: ${{ fromJson(needs.discover.outputs.smoke_matrix) }}"
        in candidate_workflow.read_text("utf-8")
    )
    assert required & suites == (RESIDUAL - {"agent-pull-requests"} if partitioned else RESIDUAL)
    if partitioned:
        assert {
            "agent-pull-requests (partition 1/2)", "agent-pull-requests (partition 2/2)",
        } <= required
        assert "agent-pull-requests" not in required
    assert {name.removeprefix("full - ") for name in required if name.startswith("full - ")} == set(
        complete.FULL_OWNERS,
    )
    assert skipped == {"PR gate (required check)", "coverage-guided-ci selection prototype (real-suite integration)"}
    assert len(digest) == 64


def test_all_actual_runner_files_and_parametrized_definitions_are_preserved(candidate_workflow):
    spec = importlib.util.spec_from_file_location("complete_once_runner", ROOT / "tools/run-plugin-tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    suites = python_suites(ROOT)
    plan = complete.complete_once_plan(candidate_workflow.read_text("utf-8"), suites)
    groups = {
        plugin: runner._test_file_groups(plugin, 25)
        for plugin in suites
    }
    before = [
        path for plugin in [*sorted(suites - {"agent-worktrees"}), *plan["full"]]
        for group in groups[plugin] for path in group
    ]
    after = [
        path for plugin in [*plan["smoke"], *plan["full"]]
        for group in groups[plugin] for path in group
    ]
    assert set(before) == set(after)
    assert Counter(after) == Counter({path: 1 for path in before})
    for plugin in set(plan["full"]) - {"agent-worktrees"}:
        assert all(Counter(before)[path] == 2 for group in groups[plugin] for path in group)
    # Compare actual definitions, including full parametrization expressions,
    # not a count that could hide a replaced file/case.
    definitions = {
        path: tuple(
            ast.dump(node, include_attributes=False)
            for node in ast.walk(ast.parse(path.read_text("utf-8")))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
        )
        for path in set(before)
    }
    assert {path: definitions[path] for path in before} == {
        path: definitions[path] for path in after
    }
    assert all(len(group) <= 25 for plugin in suites for group in groups[plugin])


@pytest.mark.parametrize(("old", "new"), [
    ("complete-once-v1", "complete-once-v2"),
    ("          - agent-bridge\n", "          - agent-index\n"),
    ("          - agent-vault\n", ""),
    ("run: python tools/run-plugin-tests.py ${{ matrix.plugin }}",
     "run: python tools/run-plugin-tests.py ${{ matrix.plugin }} -k selected"),
    ("'-n 2 --dist=worksteal'", "'-n 3 --dist=worksteal'"),
    ("'-n 2 --dist=worksteal'", "'-n 2 --dist=worksteal -m guard'"),
    ("libs/ssh-manager/tests/test_proxy.py", "libs/ssh-manager/tests/test_process.py"),
    ("PYTHONPATH: libs/ssh-manager/src:libs/agent-procutil/src", "PYTHONPATH: libs/ssh-manager/src"),
    ("inputs.promotion_phase == 'candidate')", "inputs.promotion_phase == 'release')"),
    ("--phase \"$PROMOTION_PHASE\"", "--phase candidate"),
    ('[ "$EVENT_NAME" = "workflow_dispatch" ]', '[ "$EVENT_NAME" = "push" ]'),
    ('[ -n "$PROMOTION_RUN" ]', '[ -z "$PROMOTION_RUN" ]'),
    ('[ "$PROMOTION_PHASE" = "candidate" ]', '[ "$PROMOTION_PHASE" = "release" ]'),
    ('echo "smoke=$SMOKE"', 'echo "smoke=[]"'),
    ("      fail-fast: false\n      matrix:", "      fail-fast: true\n      matrix:"),
    ("timeout-minutes: ${{ matrix.plugin == 'agent-bridge' && 8 || 30 }}",
     "timeout-minutes: ${{ matrix.plugin == 'agent-bridge' && 12 || 30 }}"),
    ("timeout-minutes: ${{ matrix.plugin == 'agent-bridge' && 8 || 30 }}",
     "timeout-minutes: ${{ matrix.plugin == 'agent-bridge' && 8 || 60 }}"),
    ("timeout-minutes: ${{ matrix.plugin == 'agent-bridge' && 8 || 30 }}",
     "timeout-minutes: 360"),
    ("    timeout-minutes: ${{ matrix.plugin == 'agent-bridge' && 8 || 30 }}\n", ""),
])
def test_unknown_filtered_or_incomplete_recipes_cannot_authorize_subtraction(
    candidate_workflow, old, new,
):
    text = candidate_workflow.read_text("utf-8")
    assert old in text
    candidate_workflow.write_text(text.replace(old, new), encoding="utf-8")
    with pytest.raises(CandidateValidationError):
        inventory.required_jobs(ROOT)


def test_complete_owner_job_bounds_preserve_bridge_workers_and_unique_ssh_scope(candidate_workflow):
    import yaml

    plan = complete.complete_once_plan(candidate_workflow.read_text("utf-8"), python_suites(ROOT))
    full = yaml.safe_load(candidate_workflow.read_text("utf-8"))["jobs"]["full"]
    assert full["timeout-minutes"] == "${{ matrix.plugin == 'agent-bridge' && 8 || 30 }}"
    assert full["strategy"]["matrix"]["plugin"] == plan["full"]
    assert plan["smoke"] == sorted(RESIDUAL)
    execution = next(step for step in full["steps"] if step.get("name") == "Full suite - ${{ matrix.plugin }}")
    assert execution["run"] == "python tools/run-plugin-tests.py ${{ matrix.plugin }}"
    assert execution["env"] == {
        "PYTEST_ADDOPTS": (
            "${{ (matrix.plugin == 'agent-worktrees' || matrix.plugin == 'agent-bridge') "
            "&& inputs.promotion_run != '' && '-n 2 --dist=worksteal' || '' }}"
        ),
    }
    ssh = next(step for step in full["steps"] if step.get("name") == "Test shared SSH proxy contracts")
    assert ssh["if"] == (
        "matrix.plugin == 'agent-bridge' && inputs.promotion_run != '' "
        "&& inputs.promotion_phase == 'candidate'"
    )
    assert ssh["env"] == {"PYTHONPATH": "libs/ssh-manager/src:libs/agent-procutil/src"}
    assert ssh["run"].split() == [
        ".test-venvs/linux/agent-bridge/bin/python", "-m", "pytest", "-q",
        "libs/ssh-manager/tests/test_process.py", "libs/ssh-manager/tests/test_proxy.py",
    ]


@pytest.mark.parametrize("mutation", [
    "missing-helper", "ambient-filter", "quoted-filter", "smoke-filter", "defaults",
    "late-rewrite", "extra-matrix",
])
def test_profile_refuses_missing_proof_or_unaccounted_selection(candidate_workflow, monkeypatch, mutation):
    text = candidate_workflow.read_text("utf-8")
    if mutation == "missing-helper":
        original = inventory.regular

        def regular(root, relative):
            if relative == Path("tools/candidate_complete_once.py"):
                raise CandidateValidationError("required candidate consumer file is absent")
            return original(root, relative)

        monkeypatch.setattr(inventory, "regular", regular)
    elif mutation == "ambient-filter":
        text = text.replace("\nenv:\n", "\nenv:\n  PYTEST_ADDOPTS: -k selected\n", 1)
    elif mutation == "quoted-filter":
        text = text.replace("\nenv:\n", "\nenv:\n  'PYTEST_ADDOPTS': -k selected\n", 1)
    elif mutation == "defaults":
        text = text.replace("\njobs:\n", "\ndefaults:\n  run:\n    working-directory: filtered\n\njobs:\n")
    elif mutation == "smoke-filter":
        text = text.replace(
            "    needs: discover\n",
            "    env:\n      PYTEST_ADDOPTS: -k selected\n    needs: discover\n",
        )
    elif mutation == "late-rewrite":
        text = text.replace(complete.DISCOVERY_FILTER, complete.DISCOVERY_FILTER + "          SMOKE=[]\n")
    else:
        text = text.replace(
            "    name: ${{ matrix.plugin }}\n    needs: discover",
            "    name: ${{ matrix.unknown }}\n    needs: discover",
        ).replace(
            "    name: ${{ matrix.name }}\n    needs: discover",
            "    name: ${{ matrix.unknown }}\n    needs: discover",
        )
    candidate_workflow.write_text(text, encoding="utf-8")
    with pytest.raises(CandidateValidationError):
        inventory.required_jobs(ROOT)


@pytest.mark.parametrize(("event", "owner", "phase"), [
    ("pull_request", "", ""), ("push", "", ""), ("workflow_dispatch", "", "release"),
    ("workflow_dispatch", "", "candidate"), ("workflow_dispatch", "123-1", "release"),
])
def test_non_candidate_invocations_keep_the_original_smoke_set(event, owner, phase):
    original = ["agent-dispatch", "agent-bridge", "agent-index"]
    assert complete.candidate_smoke(
        ROOT, original, event=event, promotion_run=owner, phase=phase, profile=complete.PROFILE,
    ) == original


def _run_discovery_shell(root, *, event, owner, phase, profile=complete.PROFILE):
    bash = shutil.which("bash")
    if os.name == "nt":
        git = shutil.which("git")
        bash = str(Path(git).resolve().parent.parent / "bin/bash.exe") if git else None
    assert bash is not None, "literal discovery contracts require native Bash"
    calls = root / "python-calls"
    environment = {
        **os.environ, "EVENT_NAME": event, "PROMOTION_RUN": owner, "PROMOTION_PHASE": phase,
        "CANDIDATE_PORTFOLIO_PROFILE": profile, "REAL_PYTHON": Path(sys.executable).as_posix(),
        "PYTHON_CALLS": calls.as_posix(),
    }
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    script = r"""
set -euo pipefail
python() {
    printf '%s\n' "$*" >> "$PYTHON_CALLS"
    "$REAL_PYTHON" "$@"
}
SMOKE=$(python tools/run-plugin-tests.py --all --exclude agent-worktrees --list)
""" + complete.DISCOVERY_FILTER + '\nprintf \'%s\\n\' "$SMOKE"\n'
    result = subprocess.run(
        [bash, "--noprofile", "--norc"], input=script, cwd=root,
        env=environment, check=False, capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result, calls.read_text("utf-8").splitlines()


@pytest.mark.parametrize(("event", "owner", "phase"), [
    ("pull_request", "", ""), ("push", "", ""),
    ("workflow_dispatch", "", "release"), ("workflow_dispatch", "", "candidate"),
    ("workflow_dispatch", "123-1", "release"),
])
@pytest.mark.parametrize("new_helper", ["missing", "unsupported"])
def test_ordinary_discovery_never_loads_unshipped_candidate_helpers(
    tmp_path, event, owner, phase, new_helper,
):
    tools = tmp_path / "tools"
    tools.mkdir()
    original = ["agent-dispatch", "agent-bridge", "agent-index"]
    (tools / "run-plugin-tests.py").write_text(f"print({json.dumps(original)!r})\n")
    if new_helper == "unsupported":
        (tools / "ci_plugin_partitions.py").write_text(
            "raise SystemExit('legacy selector does not support candidate-smoke')\n",
        )
        (tools / "candidate_complete_once.py").write_text(
            "raise RuntimeError('new helper must not load during ordinary discovery')\n",
        )
    result, calls = _run_discovery_shell(tmp_path, event=event, owner=owner, phase=phase)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == original
    assert calls == ["tools/run-plugin-tests.py --all --exclude agent-worktrees --list"]


@pytest.mark.parametrize(("event", "owner", "phase", "profile"), [
    ("workflow_dispatch", "123-1", "unknown", complete.PROFILE),
    ("workflow_dispatch", "123-1", "", complete.PROFILE),
    ("workflow_dispatch", "123-0", "candidate", complete.PROFILE),
    ("push", "123-1", "candidate", complete.PROFILE),
    ("pull_request", "", "candidate", complete.PROFILE),
    ("unknown", "", "", complete.PROFILE),
    ("workflow_dispatch", "123-1", "candidate", "unknown"),
])
def test_invalid_profile_or_phase_cannot_select_a_smaller_portfolio(event, owner, phase, profile):
    with pytest.raises(CandidateValidationError):
        complete.candidate_smoke(
            ROOT, ["agent-index"], event=event, promotion_run=owner, phase=phase, profile=profile,
        )


@pytest.mark.parametrize("requested", [[], ["agent-index"], ["agent-index", "agent-index"], [False]])
def test_incomplete_discovery_input_never_becomes_a_smaller_success(candidate_workflow, requested):
    with pytest.raises(CandidateValidationError):
        complete.candidate_smoke(
            ROOT, requested, event="workflow_dispatch", promotion_run="123-1",
            phase="candidate", profile=complete.PROFILE,
        )


@pytest.mark.parametrize("row", [
    "full - agent-bridge", "agent-index", "Governed Python artifact build (Windows-only paths)",
    "Promotion snapshot gate",
])
@pytest.mark.parametrize("conclusion", ["missing", "failure", "cancelled", "skipped"])
def test_unique_or_complete_owner_proof_must_execute_successfully(
    candidate_workflow, backend, monkeypatch, row, conclusion,
):
    required, _, _ = inventory.required_jobs(ROOT)
    assert row in required
    monkeypatch.setattr(qualification, "required_jobs", inventory.required_jobs)
    backend["jobs"] = [
        dict(id=index, name=name, status="completed", conclusion="success")
        for index, name in enumerate(sorted(required), 1)
    ]
    receipt = _qualify()
    assert _verify(receipt) == qualification.receipt_digest(receipt)
    if conclusion == "missing":
        backend["jobs"] = [job for job in backend["jobs"] if job["name"] != row]
    else:
        next(job for job in backend["jobs"] if job["name"] == row)["conclusion"] = conclusion
    with pytest.raises(VerificationError):
        _verify(receipt)


@pytest.mark.parametrize("name", ["candidate_complete_once.py", "ci_plugin_partitions.py"])
def test_profile_and_discovery_helper_bytes_are_receipt_bound(
    candidate_workflow, tmp_path, monkeypatch, name,
):
    original = inventory.regular
    before = inventory.required_jobs(ROOT)
    modified = tmp_path / name
    modified.write_text((ROOT / "tools" / name).read_text("utf-8") + "\n# changed recipe\n")
    monkeypatch.setattr(
        inventory, "regular",
        lambda root, relative: modified if relative == Path("tools") / name else original(root, relative),
    )
    after = inventory.required_jobs(ROOT)
    assert after[:2] == before[:2]
    assert after[2] != before[2]


def test_old_complete_receipt_cannot_be_relabelled_for_the_new_profile(
    candidate_workflow, backend, monkeypatch, tmp_path,
):
    new_text = candidate_workflow.read_text("utf-8")
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    historical = _candidate_workflow(legacy_dir, monkeypatch, profile="partitioned")
    required, _, _ = inventory.required_jobs(ROOT)
    assert "agent-bridge" in required
    monkeypatch.setattr(qualification, "required_jobs", inventory.required_jobs)
    backend["jobs"] = [
        dict(id=index, name=name, status="completed", conclusion="success")
        for index, name in enumerate(sorted(required), 1)
    ]
    receipt = _qualify()
    historical.write_text(new_text, encoding="utf-8")
    with pytest.raises(VerificationError, match="absent, invalid or stale"):
        _verify(receipt)


def test_complete_once_workflow_fingerprint_normalizes_only_declared_line_endings(candidate_workflow):
    before = inventory.required_jobs(ROOT)
    text = candidate_workflow.read_text("utf-8")
    candidate_workflow.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    assert inventory.required_jobs(ROOT) == before
    candidate_workflow.write_bytes(text.replace("\n", "\r", 1).encode("utf-8"))
    with pytest.raises(CandidateValidationError):
        inventory.required_jobs(ROOT)


def test_actual_preserved_main_accepts_the_same_candidate_only_profile(tmp_path, monkeypatch):
    workflow = _candidate_workflow(tmp_path, monkeypatch, profile="main")
    legacy = inventory.required_jobs(ROOT)
    workflow.write_text(profile_text(workflow.read_text("utf-8"), partitioned=False), encoding="utf-8")
    required, allowed_skips, digest = inventory.required_jobs(ROOT)
    suites = python_suites(ROOT)
    assert required & suites == RESIDUAL
    assert {f"full - {name}" for name in complete.FULL_OWNERS} <= required
    assert legacy[0] - required == set(complete.FULL_OWNERS) - {"agent-worktrees"}
    assert allowed_skips == legacy[1]
    assert digest != legacy[2]
    # All unique Windows, native, service, installation and publication rows
    # remain requirements; only the eight repeated plugin rows disappear.
    assert required == legacy[0] - (set(complete.FULL_OWNERS) - {"agent-worktrees"})


def test_real_discovery_cli_needs_only_stdlib_and_emits_all_residual_suites(candidate_workflow, tmp_path):
    root = tmp_path / "checkout"
    tools = root / "tools"
    tools.mkdir(parents=True)
    for name in ("ci_plugin_partitions.py", "candidate_complete_once.py", "materialized_candidate_inventory.py"):
        shutil.copyfile(ROOT / "tools" / name, tools / name)
    workflow = root / ".github/workflows/ci.yml"
    workflow.parent.mkdir(parents=True)
    shutil.copyfile(candidate_workflow, workflow)
    suites = python_suites(ROOT)
    for name in suites:
        plugin = root / "plugins" / name
        (plugin / "tests").mkdir(parents=True)
        (plugin / "tests/test_fixture.py").write_text("def test_fixture(): pass\n")
        (plugin / "plugin.json").write_text(json.dumps({"name": name}))
    result = subprocess.run(
        [sys.executable, "-S", str(tools / "ci_plugin_partitions.py"), "candidate-smoke",
         json.dumps(sorted(suites - {"agent-worktrees"})), "--event", "workflow_dispatch",
         "--promotion-run", "123-1", "--phase", "candidate", "--profile", complete.PROFILE],
        check=False, capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == sorted(RESIDUAL)
    (tools / "run-plugin-tests.py").write_text(
        f"print({json.dumps(sorted(suites - {'agent-worktrees'}))!r})\n",
    )
    selected, calls = _run_discovery_shell(
        root, event="workflow_dispatch", owner="123-1", phase="candidate",
    )
    assert selected.returncode == 0, selected.stdout + selected.stderr
    assert json.loads(selected.stdout) == sorted(RESIDUAL)
    assert len(calls) == 2 and "candidate-smoke" in calls[1]
    for owner, profile in (("123-0", complete.PROFILE), ("123-1", "unknown")):
        refused, _ = _run_discovery_shell(
            root, event="workflow_dispatch", owner=owner, phase="candidate", profile=profile,
        )
        assert refused.returncode != 0
        assert "invalid complete-once invocation identity" in refused.stderr
        assert refused.stdout == ""
