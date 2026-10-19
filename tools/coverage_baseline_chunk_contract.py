"""Contracts for bounded, opt-in coverage chunk concurrency."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from threading import Event, Lock

import pytest

from tools import plugin_test_containment as containment
from tools.coverage_guided_selection import baseline
from tools.coverage_guided_selection import baseline_chunks as chunks
from tools.coverage_guided_selection import completion


def _complete_fixture() -> tuple[dict, dict]:
    node = "tests/test_example.py::test_example"
    toolchain = {
        "python": "3.12.1", "platform": "linux", "pytest": "8.4.0",
        "coverage": "7.10.0", "pytest-json-report": "1.5.0", "pytest-timeout": "2.4.0",
    }
    chunk = dict(
        durations={node: 0.1}, tiers={node: "T3"}, collected=[node], deselected=[],
        outcomes={node: "passed"}, toolchain=toolchain,
        coverage={"plugins/fixture/src/fixture/__init__.py": {"1": [node]}},
    )
    result = dict(
        schema_version=3, plugin="fixture", cov_source="plugins/fixture/src/fixture",
        measured_commit="a" * 40, generated_at="2026-01-01T00:00:00+00:00",
        tests={node: dict(duration_s=0.1, portfolio_tier="T3")}, coverage=chunk["coverage"],
    )
    completion.seal(
        result, [chunk], collector="b" * 64, workers=1, bounds=completion.MAXIMA.copy(),
        files={"plugins/fixture/tests/test_example.py": "c" * 40},
    )
    return result, chunk


@pytest.mark.parametrize("mutation", [
    "missing-proof", "source", "collector", "toolchain", "profile", "bounds", "corpus",
    "missing-outcome", "failed", "empty-coverage", "unknown-attribution", "files",
])
def test_complete_baseline_refuses_tampered_proof(mutation: str) -> None:
    result, _ = _complete_fixture()
    if mutation == "missing-proof":
        del result["complete_execution"]
    elif mutation == "source":
        result["measured_commit"] = "f" * 40
    elif mutation == "empty-coverage":
        result["coverage"] = {}
    elif mutation == "unknown-attribution":
        next(iter(result["coverage"].values()))["1"] = ["unknown"]
    elif mutation == "missing-outcome":
        result["complete_execution"]["outcomes"] = {}
    elif mutation == "failed":
        node = next(iter(result["tests"]))
        result["complete_execution"]["outcomes"][node] = "failed"
    elif mutation == "toolchain":
        result["complete_execution"]["toolchain"]["python"] = "3.11.9"
    elif mutation == "bounds":
        result["complete_execution"]["bounds"]["max_processes"] = 256
    else:
        result["complete_execution"][mutation] = "tampered"
    if mutation != "missing-proof":
        result["complete_execution"].pop("digest")
        result["complete_execution"]["digest"] = completion.digest(result)
    with pytest.raises(ValueError):
        completion.verify(result, source="a" * 40, collector="b" * 64, plugin="fixture")


def test_complete_baseline_source_and_collector_are_bound_to_real_git(tmp_path: Path) -> None:
    original = Path(__file__).resolve().parents[1]
    for name in completion.TOOLS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((original / name).read_bytes())
    tests = tmp_path / "plugins/fixture/tests"
    tests.mkdir(parents=True)
    path = tests / "test_example.py"
    path.write_text("def test_example(): pass\n")
    completion._git(tmp_path, "init", "-q", "-b", "main")
    completion._git(tmp_path, "add", "-A")
    completion._git(tmp_path, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-qm", "complete baseline fixture")
    source = completion._git(tmp_path, "rev-parse", "HEAD").decode().strip()
    collector = completion.collector_identity(tmp_path, source, check_working=True)
    files = completion.source_files(tmp_path, source, "fixture", check_working=True)
    assert len(collector) == 64 and list(files) == ["plugins/fixture/tests/test_example.py"]
    path.write_text("def test_changed(): pass\n")
    with pytest.raises(ValueError, match="corpus differs"):
        completion.source_files(tmp_path, source, "fixture", check_working=True)
    tool = tmp_path / completion.TOOLS[0]
    tool.write_text(tool.read_text() + "# mutation\n")
    with pytest.raises(ValueError, match="collector differs"):
        completion.collector_identity(tmp_path, source, check_working=True)
    with pytest.raises(subprocess.CalledProcessError):
        completion.collector_identity(tmp_path, "f" * 40)


def test_complete_baseline_git_failure_retains_literal_stderr_and_cwd(tmp_path: Path) -> None:
    root = tmp_path / "not-a-repository"
    root.mkdir()
    with pytest.raises(completion.GitReadError) as failure:
        completion._git(root, "show", "f" * 40 + ":tools/run-plugin-tests.py")
    error = failure.value
    assert isinstance(error, subprocess.CalledProcessError) and error.returncode == 128
    assert error.cwd == str(root.resolve())
    assert error.cmd[:5] == [
        "git", "-c", f"safe.directory={root.resolve()}", "--no-replace-objects", "show",
    ]
    assert error.stderr
    assert error.stderr.decode("utf-8", errors="replace") in str(error)
    assert str(root.resolve()) in str(error)


def test_complete_baseline_git_trust_is_exact_process_scoped_and_credential_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, other = tmp_path / "source", tmp_path / "other"
    repo.mkdir()
    other.mkdir()
    payload = b"immutable source bytes\n"
    (repo / "payload").write_bytes(payload)
    completion._git(repo, "init", "-q", "-b", "main")
    completion._git(repo, "add", "payload")
    completion._git(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "-qm", "ownership fixture",
    )
    source = completion._git(repo, "rev-parse", "HEAD").decode().strip()
    config = tmp_path / "global.gitconfig"
    config.write_text("[safe]\n\tdirectory = *\n")
    global_before, local_before = config.read_bytes(), (repo / ".git/config").read_bytes()
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(
        GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TEST_ASSUME_DIFFERENT_OWNER="1",
    )
    real_run = subprocess.run
    for scope in ([], ["-c", f"safe.directory={other.resolve()}"]):
        denied = real_run(
            ["git", *scope, "--no-replace-objects", "show", f"{source}:payload"],
            cwd=repo, env=env, capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        assert denied.returncode == 128 and b"dubious ownership" in denied.stderr
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "*")

    def different_owner(command, **kwargs):
        assert kwargs["env"]["GIT_CONFIG_GLOBAL"] == os.devnull
        assert kwargs["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
        assert not {"GIT_DIR", "GIT_WORK_TREE", "GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0",
                    "GIT_CONFIG_VALUE_0"} & kwargs["env"].keys()
        kwargs["env"]["GIT_TEST_ASSUME_DIFFERENT_OWNER"] = "1"
        return real_run(command, **kwargs)

    monkeypatch.setattr(completion.subprocess, "run", different_owner)
    assert completion._git(repo, "show", f"{source}:payload") == payload
    with pytest.raises(completion.GitReadError) as missing:
        completion._git(repo, "show", "f" * 40 + ":payload")
    assert missing.value.returncode == 128
    assert b"dubious ownership" not in missing.value.stderr
    assert missing.value.stderr.decode("utf-8", errors="replace") in str(missing.value)
    with pytest.raises(completion.GitReadError):
        completion._git(other, "show", f"{source}:payload")
    assert config.read_bytes() == global_before
    assert (repo / ".git/config").read_bytes() == local_before


@pytest.mark.parametrize("mutation", ["duplicate", "deselected", "missing", "failed", "error", "overlap"])
def test_complete_baseline_refuses_incomplete_corpus(mutation: str) -> None:
    result, chunk = _complete_fixture()
    if mutation == "duplicate":
        chunk["collected"] *= 2
    elif mutation == "deselected":
        chunk["deselected"] = ["another"]
    elif mutation == "missing":
        chunk["outcomes"] = {}
    elif mutation in {"failed", "error"}:
        chunk["outcomes"][chunk["collected"][0]] = mutation
    with pytest.raises(ValueError):
        completion.seal(
            result, [chunk, chunk] if mutation == "overlap" else [chunk],
            collector="b" * 64, workers=1, bounds=completion.MAXIMA.copy(), files={},
        )


@pytest.mark.parametrize("workers", [1, 2])
def test_complete_baseline_serial_and_parallel_share_the_managed_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workers: int,
) -> None:
    calls = []

    def contain(command, **kwargs):
        calls.append(command)
        assert command[-1] == str(workers)
        assert kwargs["limits"] == containment.Limits(
            wall_seconds=900, max_processes=128, max_memory_mb=4096, max_temp_mb=2048,
        )
        Path(command[3]).write_text(json.dumps([{"complete": True}]))
        return 0

    monkeypatch.setattr(containment, "run_contained", contain)
    assert chunks.collect_contained(
        [_spec(tmp_path)], tmp_path, workers=workers, bounds=completion.MAXIMA.copy(),
    ) == [{"complete": True}]
    assert len(calls) == 1
    env = baseline._subprocess_env(tmp_path / "coverage", tmp_path / "sandbox", complete=True)
    assert env["COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS"] == "1"
    assert env.get("COPILOT_EXTENSIONS_ALLOW_HOST_STATE") != "1"
    assert "PYTEST_ADDOPTS" not in env


@pytest.mark.parametrize("workers", [1, 2])
def test_complete_baseline_real_driver_keeps_t3_threads_and_fixture_attribution(
    tmp_path: Path, workers: int,
) -> None:
    source = tmp_path / "plugins/fixture/src/fixture"
    tests = tmp_path / "plugins/fixture/tests"
    source.mkdir(parents=True)
    tests.mkdir(parents=True)
    (source / "__init__.py").write_text(
        "def setup(): return 1\n"
        "def call(): return 2\n"
        "def teardown(): return 3\n"
        "def threaded(): return 4\n",
    )
    (tests / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\nimport pytest\n"
        "sys.path.insert(0, str(Path(__file__).parents[1] / 'src'))\n"
        "import fixture\n"
        "@pytest.fixture\n"
        "def resource():\n    fixture.setup()\n    yield\n    fixture.teardown()\n",
    )
    for index in range(3):
        (tests / f"test_{index}.py").write_text(
            "import threading\nimport pytest\nimport fixture\n"
            "@pytest.mark.portfolio_tier('T3')\n"
            "@pytest.mark.effect('process')\n"
            "def test_run(resource):\n"
            "    assert fixture.call() == 2\n"
            "    thread = threading.Thread(target=fixture.threaded)\n"
            "    thread.start()\n    thread.join()\n",
        )
    options = dict(
        cwd=tmp_path, test_path="plugins/fixture/tests", cov_source="plugins/fixture/src/fixture",
        plugin="fixture",
        prepared_python=Path(os.environ.get("CGS_PREPARED_PYTHON", sys.executable)),
        measured_commit="a" * 40,
        max_files_per_chunk=1,
    )
    legacy = baseline.collect_baseline(**options)
    result = baseline.collect_baseline(
        **options, workers=workers,
        complete=dict(
            collector="b" * 64, bounds=completion.MAXIMA.copy(),
            files={p.relative_to(tmp_path).as_posix(): "c" * 40 for p in tests.glob("test_*.py")},
        ),
    )
    assert set(result["tests"]) == set(legacy["tests"])
    assert result["coverage"] == legacy["coverage"]
    assert len(result["tests"]) == 3
    assert all(record["portfolio_tier"] == "T3" for record in result["tests"].values())
    assert set(result["complete_execution"]["outcomes"].values()) == {"passed"}
    assert result["complete_execution"]["workers"] == workers
    for line in ("1", "2", "3", "4"):
        assert set(result["coverage"]["plugins/fixture/src/fixture/__init__.py"][line]) == set(result["tests"])


def _assert_full_baseline_profile(workflow: dict) -> None:
    full = workflow["jobs"]["full"]
    assert full["timeout-minutes"] == 30
    assert full.get("continue-on-error", False) is False
    enrolled = [
        "agent-ssh", "agent-codespaces", "agent-containers", "agent-vault",
        "agent-logger", "agent-mcp", "agent-bridge", "agent-worktrees", "agent-dispatch",
    ]
    assert set(full["strategy"]["matrix"]["plugin"]) == set(enrolled)
    assert len(full["strategy"]["matrix"]["plugin"]) == len(enrolled)
    executions = [s for s in full["steps"] if "run-plugin-tests.py" in s.get("run", "")]
    collectors = [s for s in full["steps"] if "coverage_guided_selection/baseline.py" in s.get("run", "")]
    assert len(executions) == 1
    one_pass = "--complete-baseline-out" in executions[0]["run"]
    if one_pass:
        assert not collectors
        step = executions[0]
        assert step["run"].count("run-plugin-tests.py") == 1
        assert "--measured-commit" in step["run"]
        assert "needs.gate.outputs.sha" in json.dumps(step)
        assert '--baseline-workers "$BASELINE_WORKERS"' in step["run"]
        assert not any(flag in step["run"] for flag in (
            "--guards", "--collect-only", "--test-file", "--selection-file", " -k ", " -n ",
        ))
        env = {**workflow.get("env", {}), **full.get("env", {}), **step.get("env", {})}
        assert not env.get("PYTEST_ADDOPTS")
        verifiers = [
            s for s in workflow["jobs"]["promote"]["steps"]
            if "coverage_guided_selection/completion.py" in s.get("run", "")
        ]
        assert len(verifiers) == 1
        verifier = verifiers[0]
        assert verifier.get("continue-on-error", False) is False
        assert verifier.get("if") in (None, "steps.decide.outputs.proceed == 'true'")
        assert all(flag in verifier["run"] for flag in (
            "--repo", "--source", "--plugin", "--workers", "--run-id", "coverage-baselines/",
        ))
        assert all(plugin in verifier["run"] for plugin in enrolled)
    else:
        assert len(collectors) == 1
        step = collectors[0]
        assert '--workers "$BASELINE_WORKERS"' in step["run"]
    env = {**workflow.get("env", {}), **full.get("env", {}), **step.get("env", {})}
    assert env["BASELINE_WORKERS"] == (
        "${{ (matrix.plugin == 'agent-worktrees' || matrix.plugin == 'agent-dispatch') "
        "&& '2' || '1' }}"
    )
    assert step.get("continue-on-error", False) is False
    upload = next(s for s in full["steps"] if s.get("name", "").startswith("Upload coverage"))
    for candidate in (step, upload):
        if "if" not in candidate and one_pass:
            continue  # Every matrix row is enrolled in the complete profile.
        condition = re.fullmatch(
            r"contains\(fromJSON\('(\[.*\])'\), matrix\.plugin\)", candidate["if"],
        )
        assert condition is not None
        declared = json.loads(condition[1])
        assert len(declared) == len(enrolled) and set(declared) == set(enrolled)
    assert upload["with"]["name"] == "coverage-baseline-${{ matrix.plugin }}"
    assert upload["with"]["path"] == "${{ matrix.plugin }}.json"
    assert upload.get("continue-on-error", False) is False
    assert upload["with"]["if-no-files-found"] == "error"
    assert baseline.BASELINE_SCHEMA_VERSION == 3


def test_workflow_keeps_deadline_enrollment_and_only_two_heavy_opt_ins() -> None:
    import yaml

    repo = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load(
        (repo / ".github/workflows/validate-and-promote.yml").read_text(encoding="utf-8"),
    )
    _assert_full_baseline_profile(workflow)
    ci = yaml.safe_load((repo / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    names = {
        "Coverage-guided-ci selection prototype tests (Phase 0 pilot)",
        "Real-suite round-trip + fixture-phase integration tests",
    }
    selectors = [
        step["run"] for job in ci["jobs"].values() for step in job.get("steps", [])
        if step.get("name") in names
    ]
    assert len(selectors) == 2
    assert all("tools/test_coverage_guided_selection.py" in run for run in selectors)
    assert all("coverage_baseline_chunk_contract" not in run for run in selectors)


def _assert_coverage_suite_bootstrap(
    ci: dict, *, suite: str = "tools/test_coverage_guided_selection.py",
) -> None:
    assert suite in {"tools/test_coverage_guided_selection.py", "tools/test_run_plugin_tests.py"}
    consumers = 0
    for job_name, job in ci["jobs"].items():
        if not any(suite in s.get("run", "") for s in job.get("steps", [])):
            continue
        required = {"pytest", "pytest-timeout", "pyyaml"}
        if suite == "tools/test_run_plugin_tests.py":
            platform = job.get("runs-on", "")
            # The real complete-profile CLI and hardened wrapper are Linux-only;
            # generic runner/cache/preparation witnesses remain native on Windows.
            assert isinstance(platform, str) and platform.startswith(("ubuntu-", "windows-")), (
                job_name, "unsupported runner-suite platform",
            )
            if platform.startswith("ubuntu-"):
                required |= {"coverage", "pytest-json-report"}
        else:
            required |= {"coverage", "pytest-json-report"}
        previous = []
        for step in job.get("steps", []):
            run = step.get("run", "").replace("\\\n", " ")
            if suite in run:
                consumers += 1
                assert "python -m pytest" in run, (job_name, "unsupported test interpreter")
                prefix = run.split("python -m pytest", 1)[0]
                candidates = [
                    s.get("run", "").replace("\\\n", " ") for s in previous
                    if "if" not in s or s["if"] == step.get("if")
                ] + [prefix]
                installed = set()
                for command in candidates:
                    for match in re.finditer(
                        r"(?:^|\n|&&)\s*python tools/seasoned_uv\.py\s+([^\n;&]+)", command,
                    ):
                        args = shlex.split(match[1])
                        if "--system" in args and "--python" not in args:
                            for argument in args[args.index("--system") + 1:]:
                                name = re.match(r"[A-Za-z0-9_.-]+", argument)
                                if name:
                                    installed.add(re.sub(r"[-_.]+", "-", name[0]).lower())
                assert required <= installed, (job_name, "missing seasoned dependencies", required - installed)
            previous.append(step)
    assert consumers, f"{suite} has no declared CI consumer"


def test_coverage_suite_bootstraps_its_declared_driver_dependencies() -> None:
    import yaml
    from tools._promotion_workflow_test_support import source_workflow

    repo = Path(__file__).resolve().parents[1]
    _assert_coverage_suite_bootstrap(source_workflow(repo, "ci.yml"))
    workflow = yaml.safe_load((repo / ".github/workflows/validate-and-promote.yml").read_text("utf-8"))
    if any("--complete-baseline-out" in s.get("run", "") for s in workflow["jobs"]["full"]["steps"]):
        _assert_coverage_suite_bootstrap(
            yaml.safe_load((repo / ".github/workflows/ci.yml").read_text("utf-8")),
        )


def test_runner_suite_bootstraps_its_platform_driver_dependencies() -> None:
    import yaml
    from tools._promotion_workflow_test_support import source_workflow

    repo = Path(__file__).resolve().parents[1]
    suite = "tools/test_run_plugin_tests.py"
    _assert_coverage_suite_bootstrap(source_workflow(repo, "ci.yml"), suite=suite)
    workflow = yaml.safe_load((repo / ".github/workflows/validate-and-promote.yml").read_text("utf-8"))
    if any("--complete-baseline-out" in s.get("run", "") for s in workflow["jobs"]["full"]["steps"]):
        _assert_coverage_suite_bootstrap(
            yaml.safe_load((repo / ".github/workflows/ci.yml").read_text("utf-8")), suite=suite,
        )


@pytest.mark.parametrize("platform", ["ubuntu-latest", "windows-latest"])
@pytest.mark.parametrize("mutation", [
    None, "pytest", "coverage", "pytest-json-report", "pytest-timeout", "pyyaml", "unseasoned", "late",
])
def test_runner_suite_bootstrap_matches_linux_and_windows_dependencies(
    platform: str, mutation: str | None,
) -> None:
    required = {"pytest", "pytest-timeout", "pyyaml"}
    if platform == "ubuntu-latest":
        required |= {"coverage", "pytest-json-report"}
    packages = required - {mutation}
    install = "python tools/seasoned_uv.py --uv uv -- --system " + " ".join(sorted(packages))
    test = "python -m pytest -q tools/test_run_plugin_tests.py"
    steps = [dict(run=install), dict(run=test)]
    if mutation == "unseasoned":
        steps[0]["run"] = install.replace("python tools/seasoned_uv.py --uv uv --", "uv pip install")
    elif mutation == "late":
        steps.reverse()
    ci = {"jobs": {"runner": {"runs-on": platform, "steps": steps}}}
    if mutation in required or mutation in {"unseasoned", "late"}:
        with pytest.raises(AssertionError):
            _assert_coverage_suite_bootstrap(ci, suite="tools/test_run_plugin_tests.py")
    else:
        _assert_coverage_suite_bootstrap(ci, suite="tools/test_run_plugin_tests.py")


@pytest.mark.parametrize("mutation", [
    None, "coverage", "pytest-json-report", "pytest-timeout", "pyyaml", "unseasoned", "late",
])
def test_coverage_suite_bootstrap_refuses_missing_or_unseasoned_dependencies(mutation: str | None) -> None:
    install = "python tools/seasoned_uv.py --uv uv -- --system pytest coverage pytest-json-report pytest-timeout pyyaml"
    test = "python -m pytest -q tools/test_coverage_guided_selection.py"
    if mutation in {"coverage", "pytest-json-report", "pytest-timeout", "pyyaml"}:
        install = " ".join(argument for argument in install.split() if argument != mutation)
    elif mutation == "unseasoned":
        install = install.replace("python tools/seasoned_uv.py --uv uv --", "uv pip install")
    ci = {"jobs": {
        "checks": {"steps": [dict(run=install), dict(run=test)]},
        "manual": {"steps": [dict(run=install + " && " + test)]},
    }}
    if mutation == "late":
        ci["jobs"]["checks"]["steps"].reverse()
    if mutation is None:
        _assert_coverage_suite_bootstrap(ci)
    else:
        with pytest.raises(AssertionError):
            _assert_coverage_suite_bootstrap(ci)


@pytest.mark.parametrize("mutation", [
    None, "deadline", "matrix", "double-execution", "xdist", "workers",
    "missing-upload", "missing-verifier", "verifier-source", "verifier-profile",
])
def test_complete_baseline_workflow_profile_keeps_all_assurance(mutation: str | None) -> None:
    import yaml

    repo = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((repo / ".github/workflows/validate-and-promote.yml").read_text("utf-8"))
    full = workflow["jobs"]["full"]
    workers = "${{ (matrix.plugin == 'agent-worktrees' || matrix.plugin == 'agent-dispatch') && '2' || '1' }}"
    full["steps"] = [
        s for s in full["steps"] if not (
            "run-plugin-tests.py" in s.get("run", "")
            or "coverage_guided_selection/baseline.py" in s.get("run", "")
        )
    ]
    producer = dict(
        name="Full suite", env={"BASELINE_WORKERS": workers},
        run='python tools/run-plugin-tests.py "${{ matrix.plugin }}" '
            '--complete-baseline-out "${{ matrix.plugin }}.json" '
            '--measured-commit "${{ needs.gate.outputs.sha }}" --baseline-workers "$BASELINE_WORKERS"',
    )
    full["steps"].append(producer)
    verifier = dict(
        run='for plugin in agent-ssh agent-codespaces agent-containers agent-vault agent-logger '
            'agent-mcp agent-bridge agent-worktrees agent-dispatch; do\n'
            'python reviewed-helpers/tools/coverage_guided_selection/completion.py '
            '"coverage-baselines/$plugin.json" --repo "$GITHUB_WORKSPACE" --source "$DEV_SHA" '
            '--plugin "$plugin" --workers "$workers" --run-id "$GITHUB_RUN_ID"\ndone',
    )
    workflow["jobs"]["promote"]["steps"] = [verifier]
    if mutation == "deadline":
        full["timeout-minutes"] = 31
    elif mutation == "matrix":
        full["strategy"]["matrix"]["plugin"].pop()
    elif mutation == "double-execution":
        full["steps"].append(dict(run="python tools/run-plugin-tests.py agent-bridge"))
    elif mutation == "xdist":
        producer["env"]["PYTEST_ADDOPTS"] = "-n 2"
    elif mutation == "workers":
        producer["env"]["BASELINE_WORKERS"] = "2"
    elif mutation == "missing-upload":
        next(s for s in full["steps"] if s.get("name", "").startswith("Upload coverage"))["if"] = "false"
    elif mutation == "missing-verifier":
        workflow["jobs"]["promote"]["steps"] = []
    elif mutation in {"verifier-source", "verifier-profile"}:
        flag = "--source" if mutation == "verifier-source" else "--workers"
        verifier["run"] = verifier["run"].replace(flag, "--other")
    if mutation is None:
        _assert_full_baseline_profile(workflow)
    else:
        with pytest.raises(AssertionError):
            _assert_full_baseline_profile(workflow)


def _spec(tmp_path: Path, index: int = 0) -> chunks.Chunk:
    return chunks.Chunk(
        index=index, total=6, command=["driver", '["tests"]', "src", str(tmp_path),
                                     "coverage", "report", str(tmp_path / "out.json"), "pt"],
        cwd=str(tmp_path), sandbox=str(tmp_path / f"s{index}"),
        coverage_file=str(tmp_path / f"coverage{index}"),
        output_file=str(tmp_path / "out.json"), timeout_s=300.0,
    )


def test_two_workers_overlap_without_reordering_or_queuing_extra_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    second_started = Event()
    lock = Lock()
    active = peak = 0
    seen = []

    def run(spec: chunks.Chunk) -> dict:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            seen.append(spec.index)
        if spec.index == 0:
            assert second_started.wait(2), "second chunk must overlap the first"
        elif spec.index == 1:
            second_started.set()
        with lock:
            active -= 1
        return {"index": spec.index}

    monkeypatch.setattr(chunks, "run_chunk", run)
    assert chunks.run_parallel([_spec(tmp_path, i) for i in range(6)]) == [
        {"index": i} for i in range(6)
    ]
    assert peak == 2 and active == 0
    assert sorted(seen) == list(range(6))


def test_failure_drains_owned_work_without_starting_later_chunks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    second_started, first_failed = Event(), Event()
    seen = []
    error = chunks.BaselineCollectionError(2, "failed-test", "diagnostic")

    def run(spec: chunks.Chunk) -> dict:
        seen.append(spec.index)
        if spec.index == 0:
            assert second_started.wait(2)
            first_failed.set()
            raise error
        second_started.set()
        assert first_failed.wait(2)
        # The running sibling may finish, but cannot authorize more work.
        return {"index": spec.index}

    monkeypatch.setattr(chunks, "run_chunk", run)
    with pytest.raises(chunks.BaselineCollectionError) as caught:
        chunks.run_parallel([_spec(tmp_path, i) for i in range(6)])
    assert caught.value is error
    assert sorted(seen) == [0, 1]


@pytest.mark.parametrize("workers", [1, 2])
def test_collector_keeps_all_paths_and_isolates_parallel_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workers: int,
) -> None:
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    for i in range(6):
        (test_dir / f"test_{i}.py").write_text("def test_x(): pass\n")
    observed = []

    def run(spec: chunks.Chunk) -> dict:
        observed.append(spec)
        assert spec.timeout_s == 300
        paths = json.loads(spec.command[-7])
        return {
            "durations": {path: 0.1 for path in paths},
            "tiers": {path: "T1" for path in paths},
            "coverage": {"src/shared.py": {"1": paths}},
        }

    monkeypatch.setattr(baseline, "run_chunk", run)
    monkeypatch.setattr(baseline, "collect_contained", lambda specs, tmp: [run(s) for s in specs])
    result = baseline.collect_baseline(
        cwd=tmp_path, test_path="tests", cov_source="src", plugin="fixture",
        workers=workers, max_files_per_chunk=2, measured_commit="exact-source",
    )
    assert len(observed) == 3
    for field in ("coverage_file", "output_file"):
        assert len({getattr(s, field) for s in observed}) == 3
    assert len({s.sandbox for s in observed}) == (1 if workers == 1 else 3)
    assert len({s.command[-1] for s in observed}) == 3
    assert len(result["tests"]) == 6
    assert result["coverage"]["src/shared.py"]["1"] == sorted(result["tests"])
    assert result["measured_commit"] == "exact-source"
    assert all(t["portfolio_tier"] == "T1" for t in result["tests"].values())


@pytest.mark.parametrize("returncode", [0, 124])
def test_workers_share_one_unchanged_containment_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int,
) -> None:
    calls = []

    def contain(command: list[str], **kwargs: object) -> int:
        calls.append(command)
        assert kwargs["sandbox"] == tmp_path
        assert kwargs["limits"] == containment.Limits(
            wall_seconds=900, max_processes=128, max_memory_mb=4096, max_temp_mb=2048,
        )
        manifest, output = map(Path, command[2:4])
        specs = json.loads(manifest.read_text(encoding="utf-8"))
        assert len(specs) == 6
        assert all("env" not in spec for spec in specs)
        output.write_text(json.dumps([{"index": s["index"]} for s in specs]))
        return returncode

    monkeypatch.setattr(containment, "run_contained", contain)
    specs = [_spec(tmp_path, i) for i in range(6)]
    if returncode:
        with pytest.raises(chunks.BaselineCollectionError) as caught:
            chunks.collect_contained(specs, tmp_path)
        assert caught.value.returncode == 124
    else:
        assert chunks.collect_contained(specs, tmp_path) == [{"index": i} for i in range(6)]
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "missing", "invalid"])
def test_chunk_refuses_incomplete_evidence_and_keeps_diagnostics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str,
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert kwargs["timeout"] == 300
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 300, output=b"running", stderr=b"timeout")
        if failure == "invalid":
            (tmp_path / "out.json").write_text("{}")
        return subprocess.CompletedProcess(
            command, 1 if failure == "nonzero" else 0, "test-output", "driver-diagnostic",
        )

    monkeypatch.setattr(chunks.subprocess, "run", run)
    with pytest.raises(chunks.BaselineCollectionError) as caught:
        chunks.run_chunk(_spec(tmp_path))
    assert 'chunk 1/6 ["tests"]' in str(caught.value)
    if failure == "timeout":
        assert "running" in str(caught.value) and "timed out after 300" in str(caught.value)
    else:
        assert "test-output" in str(caught.value) and "driver-diagnostic" in str(caught.value)


@pytest.mark.parametrize("workers", [0, 3, True, "2", 1.5])
def test_invalid_worker_count_refuses_before_launch(tmp_path: Path, workers: object) -> None:
    with pytest.raises(ValueError, match="workers must be"):
        baseline.collect_baseline(
            cwd=tmp_path, test_path="tests", cov_source="src", plugin="fixture", workers=workers,
        )


@pytest.mark.skipif(
    os.environ.get("CGS_RUN_INTEGRATION_TEST") != "1", reason="opt-in real coverage drivers",
)
def test_real_parallel_matches_serial_contexts_tiers_and_fixture_phases(tmp_path: Path) -> None:
    source, tests = tmp_path / "src", tmp_path / "tests"
    source.mkdir()
    tests.mkdir()
    (source / "shared.py").write_text(
        "def setup_line():\n    return 'setup'\n"
        "def call_line():\n    return 'call'\n"
        "def teardown_line():\n    return 'teardown'\n",
    )
    (tmp_path / ".coveragerc").write_text(
        "[run]\nparallel = true\ndynamic_context = test_function\n",
    )
    (tests / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\nimport pytest\n"
        "sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))\n"
        "import shared\n"
        "@pytest.fixture\n"
        "def resource():\n    shared.setup_line()\n    yield\n    shared.teardown_line()\n",
    )
    for i in range(6):
        (tests / f"test_{i}.py").write_text(
            "import pytest\nimport shared\n"
            "@pytest.mark.portfolio_tier('T1')\n"
            f"def test_{i}(resource):\n    assert shared.call_line() == 'call'\n",
        )
    results = [
        baseline.collect_baseline(
            cwd=tmp_path, test_path="tests", cov_source="src", plugin="fixture",
            workers=workers, max_files_per_chunk=2, measured_commit="exact-source",
        )
        for workers in (1, 2)
    ]
    serial, parallel = results
    assert parallel["coverage"] == serial["coverage"]
    assert set(parallel["tests"]) == set(serial["tests"])
    assert len(parallel["tests"]) == 6
    for line in ("2", "4", "6"):
        assert set(parallel["coverage"]["src/shared.py"][line]) == set(parallel["tests"])
    assert all(t["portfolio_tier"] == "T1" for t in parallel["tests"].values())
    assert parallel["measured_commit"] == serial["measured_commit"] == "exact-source"


@pytest.mark.skipif(
    os.environ.get("CGS_RUN_INTEGRATION_TEST") != "1", reason="opt-in real coverage drivers",
)
def test_real_parallel_failure_cannot_write_a_baseline(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    for i in range(3):
        (tests / f"test_{i}.py").write_text(f"def test_{i}(): assert {i} != 1\n")
    output = tmp_path / "baseline.json"
    with pytest.raises(chunks.BaselineCollectionError, match="failed"):
        baseline.main([
            str(tests), "--cwd", str(tmp_path), "--cov-source", "tests",
            "--plugin", "fixture", "--workers", "2", "--max-files-per-chunk", "1",
            "--out", str(output),
        ])
    assert not output.exists()
