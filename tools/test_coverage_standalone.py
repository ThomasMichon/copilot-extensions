"""Standalone baseline provenance and promotion bootstrap contracts."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tools.coverage_guided_selection import standalone


class TestStandaloneCoverage:
    @pytest.mark.parametrize("platform,identity", [
        ("linux", "fleet-manager-linux"), ("win32", "fleet-manager-windows"),
    ])
    def test_platform_baselines_do_not_mix_native_evidence(self, platform, identity):
        assert standalone.baseline_identity(platform) == identity

    def test_unsupported_native_platform_fails(self):
        with pytest.raises(ValueError, match="native Linux and Windows"):
            standalone.baseline_identity("darwin")

    def _sources(self, tmp_path):
        repo = tmp_path / "repo"
        source = repo / standalone.SOURCE
        source.mkdir(parents=True)
        (source / "__init__.py").write_text("def selected():\n    return 3\n")
        canonical = repo / "libs/versioned-runtime/versioned_runtime.py"
        canonical.parent.mkdir(parents=True)
        canonical.write_text("def lifecycle():\n    return True\n")
        environment = tmp_path / "environment"
        installed = environment / "site-packages/fleet_manager"
        installed.mkdir(parents=True)
        (installed / "__init__.py").write_bytes((source / "__init__.py").read_bytes())
        (installed / "_versioned_runtime.py").write_bytes(canonical.read_bytes())
        tests = repo / "fleet-manager/tests"
        tests.mkdir(parents=True)
        (tests / "test_selected.py").write_text("def test_selected(): pass\n")
        for filename in ("setup.py", "pyproject.toml"):
            (repo / "fleet-manager" / filename).write_text("# fixture\n")
        return repo, environment, installed

    def test_mapping_is_byte_verified_and_repository_relative(self, tmp_path):
        repo, environment, installed = self._sources(tmp_path)
        mapping, hashes = standalone.verified_source_mapping(repo, installed, environment)
        assert mapping[str((installed / "__init__.py").resolve())] == (
            "fleet-manager/src/fleet_manager/__init__.py"
        )
        assert mapping[str((installed / "_versioned_runtime.py").resolve())] == (
            "libs/versioned-runtime/versioned_runtime.py"
        )
        assert set(mapping.values()) == set(hashes)
        assert all(len(value) == 64 for value in hashes.values())

    @pytest.mark.parametrize("corruption", ["changed", "extra", "missing", "outside"])
    def test_unearned_installed_coordinates_are_rejected(self, tmp_path, corruption):
        repo, environment, installed = self._sources(tmp_path)
        if corruption == "changed":
            (installed / "__init__.py").write_text("different = True\n")
        elif corruption == "extra":
            (installed / "extra.py").write_text("# not canonical\n")
        elif corruption == "missing":
            (installed / "_versioned_runtime.py").unlink()
        else:
            environment = repo
        with pytest.raises(ValueError):
            standalone.verified_source_mapping(repo, installed, environment)

    @pytest.mark.parametrize("head,dirty", [("other", ""), ("abc", " M fleet-manager/src/x.py")])
    def test_measured_revision_cannot_name_another_or_dirty_source(
        self, tmp_path, monkeypatch, head, dirty,
    ):
        monkeypatch.setattr(
            standalone, "_git",
            lambda argv, **kwargs: head if argv[0] == "rev-parse" else dirty,
        )
        with pytest.raises(ValueError):
            standalone.verify_revision(tmp_path, "abc")

    def _result(self):
        return {
            "durations": {"tests/test_selected.py::test_selected": 0.12},
            "tiers": {},
            "coverage": {"fleet-manager/src/fleet_manager/__init__.py": {
                "2": ["tests/test_selected.py::test_selected"],
            }},
            "summary": {"collected": 1, "total": 1, "passed": 1},
            "python_version": "3.12.0", "child_data_files": 2,
        }

    def test_baseline_records_complete_universe_without_machine_paths(self, tmp_path):
        repo, environment, installed = self._sources(tmp_path)
        _, hashes = standalone.verified_source_mapping(repo, installed, environment)
        baseline = standalone.build_baseline(repo, self._result(), "abc", hashes)
        assert baseline["schema_version"] == 3
        assert baseline["plugin"] == standalone.baseline_identity(sys.platform)
        assert baseline["measured_commit"] == "abc"
        collection = baseline["collection"]
        assert collection["installed_package"] and collection["host_contained"]
        assert collection["child_data_files"] == 2
        assert collection["source_sha256"] == hashes
        assert len(collection["test_universe_sha256"]) == 64
        assert str(tmp_path) not in json.dumps(baseline)

    @pytest.mark.parametrize("problem", [
        "deselected", "failed", "error", "missing_test", "unknown_context", "no_coverage",
    ])
    def test_partial_or_unattributed_baseline_cannot_be_earned(self, tmp_path, problem):
        repo, _, _ = self._sources(tmp_path)
        result = self._result()
        if problem in {"deselected", "failed", "error"}:
            result["summary"][problem] = 1
        elif problem == "missing_test":
            result["summary"]["collected"] = 2
        elif problem == "unknown_context":
            result["coverage"]["fleet-manager/src/fleet_manager/__init__.py"]["2"] = ["unknown"]
        else:
            result["coverage"] = {}
        with pytest.raises(ValueError):
            standalone.build_baseline(repo, result, "abc", {})

    @pytest.mark.parametrize("fail", [False, True])
    def test_collector_reuses_native_lease_containment_and_cleans_instrumentation(
        self, tmp_path, monkeypatch, fail,
    ):
        repo, environment, installed = self._sources(tmp_path)
        site = installed.parent
        python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        python.parent.mkdir()
        python.touch()
        (environment / "pyvenv.cfg").write_text("home = fixture\n")
        events = []

        class Lease:
            def release(self):
                events.append("release")

        monkeypatch.setattr(standalone, "REPO", repo)
        monkeypatch.setattr(standalone.runner, "default_python", lambda component: python)
        monkeypatch.setattr(standalone, "verify_revision", lambda *args: None)
        monkeypatch.setattr(standalone.runner, "acquire", lambda *args: Lease())
        monkeypatch.setattr(standalone, "_prepare_instrumentation", lambda *args, **kwargs: None)
        monkeypatch.setenv("PYTEST_ADDOPTS", "-k never")
        monkeypatch.setenv("COVERAGE_PROCESS_START", "untrusted.ini")

        def contained(command, *, cwd, env, sandbox, limits):
            events.append("contained")
            assert command[1] == "-I"
            assert "PYTEST_ADDOPTS" not in env
            assert "COVERAGE_PROCESS_START" not in env
            assert cwd == repo / "fleet-manager"
            if "-c" in command:
                (sandbox / "installed.json").write_text(json.dumps([str(installed), str(site)]))
            else:
                assert limits.wall_seconds == 600
                assert json.loads(command[3]) == ["tests"]
                assert (site / "_cgs_standalone.pth").is_file()
                options = json.loads(command[-1])
                assert options["installed_source"] == str(installed)
                if fail:
                    return 1
                (sandbox / "result.json").write_text(json.dumps(self._result()))
            return 0

        monkeypatch.setattr(standalone, "run_contained", contained)
        if fail:
            with pytest.raises(standalone.BaselineCollectionError):
                standalone.collect_standalone(measured_commit="abc")
        else:
            baseline = standalone.collect_standalone(measured_commit="abc")
            assert len(baseline["tests"]) == 1
        assert events == ["contained", "contained", "release"]
        assert not (site / "_cgs_standalone.pth").exists()
        assert not list((repo / ".pytmp").glob("c????????"))

    def test_promotion_enrolls_both_platforms_and_blocks_on_full_collection(self):
        import yaml

        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/validate-and-promote.yml").read_text()
        jobs = yaml.safe_load(text)["jobs"]
        job = jobs["fleet-manager-baselines"]
        assert job["strategy"]["fail-fast"] is False
        matrix = job["strategy"]["matrix"]["include"]
        assert {(row["os"], row["baseline"]) for row in matrix} == {
            ("ubuntu-latest", "fleet-manager-linux"), ("windows-latest", "fleet-manager-windows"),
        }
        assert job["timeout-minutes"] == 15
        run = next(step["run"] for step in job["steps"] if "run" in step)
        assert "coverage_guided_selection/standalone.py fleet-manager" in run
        assert '--measured-commit "${{ needs.gate.outputs.sha }}"' in run
        assert "--timeout 720" in run
        assert all(not step.get("continue-on-error") for step in job["steps"])
        assert job["steps"][-1]["with"]["if-no-files-found"] == "error"
        for owner in ("build-python-artifacts", "report-failure", "promote"):
            assert "fleet-manager-baselines" in jobs[owner]["needs"]
        promotion_runs = "\n".join(step.get("run", "") for step in jobs["promote"]["steps"])
        assert 'needs.fleet-manager-baselines.result' in promotion_runs
        assert "fleet-manager-linux fleet-manager-windows; do" in promotion_runs
        assert "needs.fleet-manager-baselines.result == 'failure'" in jobs["report-failure"]["if"]

    def test_stage_one_does_not_activate_or_trigger_the_per_pr_fleet_lane(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/ci.yml").read_text()
        patterns = re.findall(r"'(\^\(fleet-manager/[^'\n]+)'; then", workflow)
        assert len(patterns) == 2
        stage_one_paths = [
            "tools/coverage_guided_selection/baseline.py",
            "tools/coverage_guided_selection/standalone.py",
            "tools/test_coverage_guided_selection.py",
            "tools/test_coverage_standalone.py",
            ".github/workflows/validate-and-promote.yml",
            "docs/standalone-coverage-baselines.md",
            "docs/pipelines.md",
        ]
        assert all(not re.search(pattern, path) for pattern in patterns for path in stage_one_paths)
        assert workflow.count(
            "run-standalone-tests.py fleet-manager --prepare --admission-wait 60 "
            "--timeout ${{ github.event_name == 'workflow_dispatch' && 600 || 120 }}"
        ) == 2

    @pytest.mark.skipif(
        os.environ.get("CGS_RUN_STANDALONE_INTEGRATION") != "1",
        reason="requires the deliberately prepared native fleet environment",
    )
    def test_real_installed_children_keep_test_context_and_native_path_semantics(self):
        from tools.coverage_guided_selection.selection import select_tests

        repo = standalone.REPO
        python = standalone.runner.default_python("fleet-manager")
        environment = python.parent.parent
        if not python.is_file():
            pytest.fail("prepare the native fleet baseline environment first")
        lease = standalone.runner.acquire(60, environment)
        hook = None
        temporary = tempfile.TemporaryDirectory(prefix="c", dir=repo / ".pytmp")
        try:
            sandbox = Path(temporary.name)
            env = standalone.isolated_environment(os.environ, sandbox)
            for name in ("PYTEST_ADDOPTS", "COVERAGE_PROCESS_START", "COVERAGE_FILE", "PYTHONPATH"):
                env.pop(name, None)
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            # Trusted interpreter metadata only; actual tests run contained below.
            probe = subprocess.run(
                [str(python), "-I", "-c",
                 "import importlib.util; from pathlib import Path; "
                 "print(Path(importlib.util.find_spec('fleet_manager').origin).parent)"],
                check=True, capture_output=True, text=True, **flags,
            )
            installed = Path(probe.stdout.strip())
            mapping, _ = standalone.verified_source_mapping(repo, installed, environment)
            candidate = installed.parent / "_cgs_standalone.pth"
            with candidate.open("x", encoding="utf-8") as stream:
                hook = candidate
                stream.write("import coverage; coverage.process_startup()\n")
            contexts = sandbox / "contexts"
            contexts.mkdir()
            tests = sandbox / "test_children.py"
            tests.write_text(
                "import os, pytest, sqlite3, subprocess, sys\n"
                "def child():\n"
                "    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}\n"
                "    subprocess.run([sys.executable, '-I', '-c', "
                "\"from fleet_manager.planning import canonical; canonical({'child': True})\"], "
                "check=True, capture_output=True, **flags)\n"
                "def test_first(tmp_path):\n"
                "    path = tmp_path/'private-stage'/('fleet-artifact-stage-'+'x'*8)"
                "/'versions'/'example-r1'/'artifacts'/('0'*64)\n"
                "    path.mkdir(parents=True)\n"
                "    with sqlite3.connect((tmp_path/'db.sqlite').as_uri(), uri=True): pass\n"
                "    child()\n"
                "@pytest.mark.parametrize('value', ['%literal-${HOME}'])\n"
                "def test_second(value): child()\n",
                encoding="utf-8",
            )
            driver = sandbox / "driver.py"
            driver.write_text(standalone._DRIVER_SCRIPT, encoding="utf-8")
            out = sandbox / "result.json"
            status = standalone.run_contained(
                [str(python), "-I", str(driver), json.dumps([str(tests)]), standalone.SOURCE,
                 str(repo), str(sandbox / ".coverage"), str(sandbox / "report.json"), str(out),
                 str(sandbox / "p"), json.dumps({
                     "installed_source": str(installed), "source_mapping": mapping,
                     "child_context_dir": str(contexts), "compact_tmp_paths": os.name == "nt",
                 })],
                cwd=repo / "fleet-manager", env=env, sandbox=sandbox,
                limits=standalone.Limits(wall_seconds=60),
            )
            assert status == 0
            result = json.loads(out.read_text())
            assert result["summary"]["passed"] == 2
            assert result["child_data_files"] >= 2
            key = "fleet-manager/src/fleet_manager/planning.py"
            nodes = set(result["durations"])
            attributed = next(line for line, covered in result["coverage"][key].items()
                              if set(covered) == nodes)
            selected = select_tests({"coverage": result["coverage"]}, {key: [int(attributed)]})
            assert set(selected.selected_tests) == nodes
            assert not selected.fallback_triggered
        finally:
            try:
                if hook is not None:
                    hook.unlink(missing_ok=True)
            finally:
                standalone.runner.cleanup_sandbox(temporary)
                lease.release()
