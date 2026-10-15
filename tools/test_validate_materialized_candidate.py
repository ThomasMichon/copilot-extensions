"""Exact candidate admission and real, unprivileged consumer execution contracts."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path, PureWindowsPath

import pytest
import yaml

import validate_materialized_candidate as candidate

ROOT = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "core.fsmonitor=false", "-C", str(root), *args],
        check=True, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ).stdout.strip()


@pytest.fixture
def subject(tmp_path, monkeypatch):
    for key in candidate.FORBIDDEN_AUTHORITY:
        monkeypatch.delenv(key, raising=False)
    root = tmp_path / "candidate"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "core.hooksPath", str(root / "no-hooks"))
    files = {
        "owned.txt": "unchanged\n",
        "plugins/agent-worktrees/plugin.json": "{}",
        "plugins/agent-worktrees/tests/test_owned.py": "def test_owned(): pass\n",
        "plugins/agent-bridge/plugin.json": "{}",
        "plugins/agent-bridge/tests/test_owned.py": "def test_owned(): pass\n",
        "tools/run-plugin-tests.py": (
            "import json, sys\nfrom pathlib import Path\nfrom fixture_sibling import LABEL\n"
            "with Path('executions.jsonl').open('a') as stream:\n"
            " stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        ),
        "tools/fixture_sibling.py": 'LABEL = "supported recipe"\n',
        "tools/run-standalone-tests.py": "print('fixture standalone executed')\n",
        "tools/standalone_consumers.py": (
            "STANDALONE_CONSUMERS = "
            "('worktree-manager', 'services/index', 'fleet-manager', 'services/dispatch')\n"
        ),
        "tools/seasoned_uv.py": "print('fixture preparation executed')\n",
        "tools/test_fleet_ci_scope.py": (
            "import os\nfrom pathlib import Path\n"
            "def test_fleet_scope():\n"
            " value = os.environ['PYTEST_ADDOPTS']\n"
            " assert value == '-m \"not client_distribution_full and not observation_retention_full\"'\n"
            " Path('fleet-exclusions.txt').write_text(value)\n"
        ),
        "services/dispatch/pyproject.toml": '[project]\nname="fixture-dispatch"\n',
        "services/index/pyproject.toml": '[project]\nname="fixture-index"\n',
        "fleet-manager/pyproject.toml": '[project]\nname="fixture-fleet"\n',
        "fleet-manager/setup.py": "# fixture build entry\n",
        "fleet-manager/README.md": "Fixture Fleet consumer\n",
        "worktree-manager/pyproject.toml": '[project]\nname="fixture-manager"\n',
    }
    for name, contents in files.items():
        path = root.joinpath(*name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "main")
    main = git(root, "rev-parse", "HEAD")
    git(root, "clone", "-q", "--no-hardlinks", str(root), str(root.parent / "validated-dev"))
    state = {
        "schema": candidate.SCHEMA, "version": 1,
        "last_promotion": {"dev_head": main, "main_before": main},
    }
    (root / ".github").mkdir()
    (root / candidate.STATE).write_text(json.dumps(state), encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "candidate")
    head = git(root, "rev-parse", "HEAD")
    return root, head, main, main


def commit(root):
    git(root, "add", ".")
    previous = git(root, "rev-parse", "HEAD")
    parent = git(root, "rev-parse", "HEAD^")
    head = git(
        root, "commit-tree", git(root, "write-tree"), "-p", parent, "-m", "revised fixture candidate",
    )
    git(root, "update-ref", "HEAD", head, previous)
    return head


def test_exact_candidate_and_real_consumer_preserve_tracked_inputs(subject):
    root, head, dev, main = subject
    identity = candidate.validate(root, head, dev, main)
    assert identity["tree"] == git(root, "rev-parse", "HEAD^{tree}")
    result = candidate.run(
        root, head, dev, main, "plugin:agent-worktrees", dev_root=root.parent / "validated-dev",
    )
    assert result["outcome"] == "success"
    assert [
        json.loads(line) for line in (root / "executions.jsonl").read_text().splitlines()
    ] == [
        ["agent-worktrees", "--collect-only"], ["agent-worktrees", "--guards"],
    ]
    assert candidate.validate(root, head, dev, main) == identity


@pytest.mark.parametrize("position", [1, 2, 3])
def test_wrong_immutable_input_refuses_before_any_consumer(subject, monkeypatch, position):
    arguments = list(subject)
    arguments[position] = "f" * 40
    monkeypatch.setattr(candidate, "recipes", lambda *args: pytest.fail("consumer was reached"))
    with pytest.raises(candidate.CandidateValidationError):
        candidate.run(*arguments, "plugin:agent-bridge", dev_root=subject[0].parent / "validated-dev")


@pytest.mark.parametrize("key,value", [
    ("schema", "other"), ("version", True), ("last_promotion", None),
])
def test_malformed_composition_refuses(subject, key, value):
    root, _, dev, main = subject
    state = json.loads((root / candidate.STATE).read_text())
    state[key] = value
    (root / candidate.STATE).write_text(json.dumps(state))
    head = commit(root)
    with pytest.raises(candidate.CandidateValidationError, match="composition"):
        candidate.validate(root, head, dev, main)


def test_modified_checkout_and_foreign_git_selectors_do_not_change_admission(subject, monkeypatch):
    root, head, dev, main = subject
    monkeypatch.setenv("GIT_DIR", str(root / "not-the-repo"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root.parent))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.fsmonitor")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "fixture-unwanted-command")
    assert candidate.validate(root, head, dev, main)["candidate_sha"] == head
    (root / "owned.txt").write_text("changed\n")
    with pytest.raises(candidate.CandidateValidationError, match="tracked inputs"):
        candidate.validate(root, head, dev, main)


def test_real_failed_and_mutating_consumers_cannot_authorize_merge(subject):
    root, _, dev, main = subject
    runner = root / "tools" / "run-plugin-tests.py"
    runner.write_text("raise SystemExit(7)\n")
    head = commit(root)
    with pytest.raises(candidate.CandidateValidationError, match="exit 7"):
        candidate.run(
            root, head, dev, main, "plugin:agent-bridge", dev_root=root.parent / "validated-dev",
        )
    runner.write_text(
        "from pathlib import Path\nPath('owned.txt').write_text('mutated')\n",
    )
    head = commit(root)
    with pytest.raises(candidate.CandidateValidationError, match="tracked inputs"):
        candidate.run(
            root, head, dev, main, "plugin:agent-bridge", dev_root=root.parent / "validated-dev",
        )


def test_timed_out_consumer_still_checks_tracked_immutability(subject, monkeypatch):
    root, head, dev, main = subject
    real_run = subprocess.run

    def run(argv, **kwargs):
        if argv[0] == "git":
            return real_run(argv, **kwargs)
        (root / "owned.txt").write_text("modified by timed-out fixture")
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(candidate.subprocess, "run", run)
    with pytest.raises(candidate.CandidateValidationError, match="tracked inputs"):
        candidate.run(
            root, head, dev, main, "plugin:agent-bridge", dev_root=root.parent / "validated-dev",
        )


@pytest.mark.parametrize("name", candidate.FORBIDDEN_AUTHORITY)
def test_release_or_api_authority_refuses_before_test_execution(subject, monkeypatch, name):
    monkeypatch.setenv(name, "fixture-not-an-actual-credential")
    monkeypatch.setattr(candidate, "recipes", lambda *args: pytest.fail("consumer was reached"))
    with pytest.raises(candidate.CandidateValidationError, match="authority"):
        candidate.run(*subject, "plugin:agent-bridge", dev_root=subject[0].parent / "validated-dev")


def test_complete_matrix_and_exact_existing_recipes(subject):
    root, _, dev, _ = subject
    rows = candidate.matrix(root, root.parent / "validated-dev", dev)["include"]
    assert {(row["component"], row["os"]) for row in rows} == {
        ("plugin:agent-worktrees", "ubuntu-latest"),
        ("plugin:agent-bridge", "ubuntu-latest"),
        ("manager", "ubuntu-latest"), ("dispatch", "ubuntu-latest"),
        ("index", "ubuntu-latest"), ("index", "windows-latest"),
        ("fleet-manager", "ubuntu-latest"), ("fleet-manager", "windows-latest"),
    }
    commands = candidate.recipes(root, "plugin:agent-worktrees")
    assert [command[-1] for command, _ in commands] == ["--collect-only", "--guards"]
    assert candidate.recipes(root, "manager") == [
        (["uv", "run", "--extra", "dev", "pytest", "-q"], root / "worktree-manager"),
    ]
    standalone, directory = candidate.recipes(root, "dispatch")[0]
    assert standalone[:2] == [sys.executable, str(root / "tools" / "run-standalone-tests.py")]
    assert standalone[2:] == [
        "services/dispatch", "--prepare", "--admission-wait", "60",
        "--timeout", "180",
    ]
    assert directory == root
    assert candidate.recipes(root, "index")[0][0][-1] == "--smoke"
    fleet = candidate.recipes(root, "fleet-manager")
    assert fleet[0][0][2:] == ["--uv", "uv", "--", "--system", "pytest"]
    assert fleet[1][0][1:4] == ["-m", "pytest", "-q"]
    assert fleet[2][0][2:] == [
        "fleet-manager", "--prepare", "--admission-wait", "60",
        "--timeout", "300" if os.name == "nt" else "120",
    ]


def test_missing_inventory_or_unknown_consumer_cannot_silently_skip(subject):
    root, head, dev, main = subject
    with pytest.raises(candidate.CandidateValidationError, match="absent"):
        candidate.run(
            root, head, dev, main, "unrecognized", dev_root=root.parent / "validated-dev",
        )
    (root / "plugins" / "agent-worktrees" / "plugin.json").unlink()
    with pytest.raises(candidate.CandidateValidationError, match="absent"):
        candidate.matrix(root, root.parent / "validated-dev", dev)


def test_empty_required_recipe_is_not_a_success_receipt(subject, monkeypatch):
    monkeypatch.setattr(candidate, "recipes", lambda *args: [])
    with pytest.raises(candidate.CandidateValidationError, match="no executable recipe"):
        candidate.run(*subject, "plugin:agent-bridge", dev_root=subject[0].parent / "validated-dev")


def test_matrix_does_not_invent_python_suites_for_other_test_languages(subject):
    root, _, dev, _ = subject
    tests = root / "plugins" / "fixture-node" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_contract.cjs").write_text("// fixture\n")
    assert not any(
        row["component"] == "plugin:fixture-node"
        for row in candidate.matrix(root, root.parent / "validated-dev", dev)["include"]
    )


def test_symlinked_consumer_is_refused(subject, tmp_path):
    root, _, dev, _ = subject
    path = root / "plugins" / "agent-bridge" / "tests" / "foreign"
    try:
        path.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("native symlink creation is unavailable")
    (root / "plugins" / "alias").symlink_to(root / "plugins" / "agent-bridge", target_is_directory=True)
    with pytest.raises(candidate.CandidateValidationError, match="symlink"):
        candidate.matrix(root, root.parent / "validated-dev", dev)


@pytest.mark.parametrize("missing", ["directory", "empty"])
def test_missing_non_worktrees_suite_cannot_escape_inventory(subject, missing):
    root, _, dev, _ = subject
    tests = root / "plugins" / "agent-bridge" / "tests"
    if missing == "directory":
        shutil.rmtree(tests)
    else:
        (tests / "test_owned.py").unlink()
    with pytest.raises(candidate.CandidateValidationError, match="missing=.*agent-bridge"):
        candidate.matrix(root, root.parent / "validated-dev", dev)


@pytest.mark.parametrize("invalid", ["wrong-head", "dirty"])
def test_development_inventory_must_be_the_validated_input(subject, invalid):
    root, head, dev, _ = subject
    source = root.parent / "validated-dev"
    if invalid == "wrong-head":
        git(source, "fetch", "-q", str(root), head)
        git(source, "checkout", "-q", "--detach", head)
    else:
        (source / "owned.txt").write_text("changed")
    with pytest.raises(candidate.CandidateValidationError, match="validated immutable input"):
        candidate.matrix(root, source, dev)


def test_windows_standalone_recipe_uses_canonical_component_id(subject, monkeypatch):
    monkeypatch.setitem(candidate.STANDALONE, "index", PureWindowsPath("services", "index"))
    argv, _ = candidate.recipes(subject[0], "index")[0]
    assert argv[2] == "services/index"


def test_omitted_canonical_standalone_consumer_cannot_authorize_inventory(subject, monkeypatch):
    monkeypatch.delitem(candidate.STANDALONE, "fleet-manager")
    with pytest.raises(candidate.CandidateValidationError, match="canonical roster"):
        candidate.matrix(subject[0], subject[0].parent / "validated-dev", subject[2])


@pytest.mark.parametrize("relative", [
    "fleet-manager/pyproject.toml", "fleet-manager/setup.py",
    "fleet-manager/README.md", "tools/test_fleet_ci_scope.py",
])
def test_missing_required_fleet_consumer_files_refuse(subject, relative):
    root, _, dev, _ = subject
    root.joinpath(*relative.split("/")).unlink()
    with pytest.raises(candidate.CandidateValidationError, match="absent"):
        candidate.matrix(root, root.parent / "validated-dev", dev)


def test_real_fleet_recipe_preserves_existing_bounded_exclusions(subject):
    root, head, dev, main = subject
    assert candidate.run(
        root, head, dev, main, "fleet-manager", dev_root=root.parent / "validated-dev",
    )["outcome"] == "success"
    assert (root / "fleet-exclusions.txt").read_text() == (
        '-m "not client_distribution_full and not observation_retention_full"'
    )


def test_isolated_trusted_cli_loads_inventory_and_executes_actual_consumer(subject):
    root, head, dev, main = subject
    result = subprocess.run(
        [
            sys.executable, "-I", "-B", str(ROOT / "tools" / "validate_materialized_candidate.py"),
            "--root", str(root), "--candidate-sha", head,
            "--dev-root", str(root.parent / "validated-dev"), "--dev-sha", dev,
            "--main-before", main, "--component", "plugin:agent-worktrees",
        ],
        cwd=root, capture_output=True, text=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outcome"] == "success"
    assert len((root / "executions.jsonl").read_text().splitlines()) == 2


def test_pipeline_requires_successful_exact_candidate_jobs_and_preserves_old_gates():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "validate-and-promote.yml").read_text(),
    )
    jobs = workflow["jobs"]
    merger = jobs["merge-promotion"]
    assert set(merger["needs"]) == {
        "promote", "materialized-candidate-plan", "materialized-candidate-consumers",
        "materialized-candidate-windows",
    }
    assert "needs.materialized-candidate-plan.result == 'success'" in merger["if"]
    assert "needs.materialized-candidate-consumers.result == 'success'" in merger["if"]
    assert re.sub(r"\s+", " ", merger["if"]).strip() == (
        "needs.promote.outputs.pr_number != '' && "
        "needs.materialized-candidate-plan.result == 'success' && "
        "needs.materialized-candidate-consumers.result == 'success' && "
        "needs.materialized-candidate-windows.result == 'success'"
    )
    assert not any("checkout" in step.get("uses", "") for step in merger["steps"])
    assert "postmerge-workflows" in jobs and "python-artifacts" in jobs
    for name in (
        "materialized-candidate-plan", "materialized-candidate-consumers",
        "materialized-candidate-windows",
    ):
        job = jobs[name]
        assert job["permissions"] == {"contents": "read"} and "environment" not in job
        assert name in jobs["report-failure"]["needs"]
        assert f"needs.{name}.result == 'failure'" in jobs["report-failure"]["if"]
        checkouts = [step for step in job["steps"] if step.get("uses") == "actions/checkout@v4"]
        assert len(checkouts) == 3
        assert all(step["with"]["persist-credentials"] is False for step in checkouts)
        assert checkouts[0]["with"]["ref"] == "${{ github.workflow_sha }}"
        assert checkouts[1]["with"]["ref"] == "${{ needs.promote.outputs.candidate_sha }}"
        assert checkouts[2]["with"]["ref"] == "${{ needs.gate.outputs.sha }}"
        assert "APERTURE_RELEASE_TOKEN" not in json.dumps(job)
        assert "secrets." not in json.dumps(job)
    assert jobs["materialized-candidate-plan"]["runs-on"] == "ubuntu-latest"
    assert jobs["materialized-candidate-consumers"]["runs-on"] == "ubuntu-latest"
    assert jobs["materialized-candidate-windows"]["runs-on"] == "windows-latest"
    assert jobs["materialized-candidate-windows"]["strategy"]["matrix"]["component"] == [
        "index", "fleet-manager",
    ]
    assert jobs["materialized-candidate-windows"]["strategy"]["fail-fast"] is False
    assert jobs["materialized-candidate-consumers"]["strategy"]["fail-fast"] is False
    assert "needs.materialized-candidate-plan.result == 'success'" in (
        jobs["materialized-candidate-consumers"]["if"]
    )


def test_candidate_recipes_preserve_current_main_consumer_contracts(subject):
    root, _, _, _ = subject
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    jobs = workflow["jobs"]
    manager = next(
        step["run"] for step in jobs["worktree-manager"]["steps"]
        if step.get("name") == "Test worktree-manager"
    )
    assert candidate.recipes(root, "manager")[0][0] == manager.split()
    dispatch = next(
        step["run"] for step in jobs["agent-dispatch-service"]["steps"]
        if step.get("name", "").startswith("Test agent-dispatch-service")
    )
    assert dispatch.split()[3:] == candidate.recipes(root, "dispatch")[0][0][3:]
    for name in ("agent-index-service-linux", "agent-index-service-windows"):
        assert any('MODE="--smoke"' in step.get("run", "") for step in jobs[name]["steps"])
    assert candidate.recipes(root, "index")[0][0][-1] == "--smoke"


def test_cli_reports_failure_not_success_for_an_unbound_checkout(subject, capsys):
    root, _, dev, main = subject
    assert candidate.main([
        "--root", str(root), "--candidate-sha", "f" * 40,
        "--dev-root", str(root.parent / "validated-dev"),
        "--dev-sha", dev, "--main-before", main, "--matrix",
    ]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "does not match" in captured.err
