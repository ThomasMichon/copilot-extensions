"""Standalone baseline provenance and promotion bootstrap contracts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tools.coverage_guided_selection import standalone


class TestStandaloneCoverage:
    def test_inventory_reuses_verified_bytes_without_removing_fresh_checks(self,tmp_path):
        from tools.coverage_guided_selection import child_stream_parent as child_stream
        repo,environment,installed=self._sources(tmp_path)
        mapping,hashes,inventory=standalone.verified_source_mapping(
            repo,installed,environment,with_inventory=True)
        assert inventory==child_stream.source_inventory(mapping)
        assert {row["canonical"]:row["sha256"] for row in inventory.values()}==hashes
        (installed/"__init__.py").write_text("changed = True\n")
        with pytest.raises(ValueError,match="differs"):
            standalone.verified_source_mapping(repo,installed,environment,with_inventory=True)

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
        runtime = repo / "libs/versioned-runtime"
        runtime.mkdir(parents=True)
        resources = {
            "versioned_runtime.py": "_versioned_runtime.py",
            "versioned_runtime_leases.py": "versioned_runtime_leases.py",
            "versioned_runtime_construction.py": "versioned_runtime_construction.py",
            "versioned_runtime_source.py": "versioned_runtime_source.py",
        }
        (runtime / "standalone_build.py").write_text("RUNTIME_RESOURCES = " + repr(resources) + "\n")
        environment = tmp_path / "environment"
        installed = environment / "site-packages/fleet_manager"
        installed.mkdir(parents=True)
        (installed / "__init__.py").write_bytes((source / "__init__.py").read_bytes())
        for name, destination in resources.items():
            canonical = runtime / name
            canonical.write_text(f"RESOURCE = {name!r}\n")
            (installed / destination).write_bytes(canonical.read_bytes())
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

    def test_mapping_follows_the_current_packaged_runtime_declaration(self, tmp_path):
        repo, environment, installed = self._sources(tmp_path)
        declaration = repo / "libs/versioned-runtime/standalone_build.py"
        resources = {path.name: name for name, path in standalone.runtime_resource_mapping(repo).items()}
        resources["versioned_runtime_next.py"] = "versioned_runtime_next.py"
        declaration.write_text("RUNTIME_RESOURCES = " + repr(resources) + "\n")
        canonical = declaration.with_name("versioned_runtime_next.py")
        canonical.write_text("next_resource = True\n")
        copied = installed / canonical.name
        copied.write_bytes(canonical.read_bytes())
        mapping, _ = standalone.verified_source_mapping(repo, installed, environment)
        assert mapping[str(copied.resolve())] == "libs/versioned-runtime/versioned_runtime_next.py"
        copied.write_text("not_the_canonical_resource = True\n")
        with pytest.raises(ValueError, match="source differs"):
            standalone.verified_source_mapping(repo, installed, environment)

    @pytest.mark.parametrize("resources", [
        {"../foreign.py": "owned.py"}, {"owned.py": "../foreign.py"},
        {"first.py": "same.py", "second.py": "same.py"}, {},
    ])
    def test_runtime_declaration_refuses_unowned_or_ambiguous_coordinates(self, tmp_path, resources):
        repo, environment, installed = self._sources(tmp_path)
        declaration = repo / "libs/versioned-runtime/standalone_build.py"
        declaration.write_text("RUNTIME_RESOURCES = " + repr(resources) + "\n")
        with pytest.raises(ValueError, match="runtime resource declaration"):
            standalone.verified_source_mapping(repo, installed, environment)

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
            "python_version": "3.12.0", "children": {
                "child_streams": 2, "audited_producers": 2, "zero_hit_producers": 0,
                "prelude_seconds": [0.01, 0.01],
                "audit_complete": True,
            }, "coverage_version": "7.0.0",
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
        assert collection["children"]["audited_producers"] == 2
        assert collection["child_persistence"] == "unbuffered-sparse-writes"
        assert collection["coverage_version"] == "7.0.0"
        assert collection["source_sha256"] == hashes
        assert len(collection["test_universe_sha256"]) == 64
        assert str(tmp_path) not in json.dumps(baseline)

    @pytest.mark.parametrize("problem", [
        "deselected", "failed", "error", "missing_test", "unknown_context", "no_coverage",
        "incomplete_children",
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
        elif problem == "incomplete_children":
            result["children"]["audit_complete"] = False
        else:
            result["coverage"] = {}
        with pytest.raises(ValueError):
            standalone.build_baseline(repo, result, "abc", {})

    @pytest.mark.parametrize("fail,late_change", [(False, False), (True, False), (False, True)])
    def test_collector_reuses_native_lease_containment_and_cleans_instrumentation(
        self, tmp_path, monkeypatch, fail, late_change,
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
                parent = standalone.child_stream_parent
                inventory = {int(key): value for key, value in options["child_stream_inventory"].items()}
                control = Path(options["child_stream_control"])
                result = self._result()
                contexts = {
                    hashlib.sha256(node.encode()).hexdigest(): node
                    for node in result["durations"]
                }
                for token in contexts:
                    parent.write_manifest(
                        control / "manifests" / (token + ".manifest"),
                        nonce=options["child_stream_nonce"], context=token,
                        directory=control / "hits", inventory=inventory,
                    )
                children = parent.read_streams(
                    control / "hits", nonce=options["child_stream_nonce"],
                    contexts=contexts, inventory=inventory, audit_control=control,
                )
                children.pop("coverage")
                result["children"] = children
                (sandbox / "result.json").write_text(json.dumps(result))
                if late_change:
                    (control / "status").write_bytes(b"E")
            return 0

        monkeypatch.setattr(standalone, "run_contained", contained)
        if fail:
            with pytest.raises(standalone.BaselineCollectionError):
                standalone.collect_standalone(measured_commit="abc")
        elif late_change:
            with pytest.raises(ValueError, match="audit failure"):
                standalone.collect_standalone(measured_commit="abc")
        else:
            baseline = standalone.collect_standalone(measured_commit="abc")
            assert len(baseline["tests"]) == 1
        assert events == ["contained", "contained", "release"]
        assert not (site / "_cgs_standalone.pth").exists()
        assert not list((repo / ".pytmp").glob("c????????"))

    @pytest.mark.parametrize("platform,label", [
        ("linux", "ubuntu-latest"), ("windows", "windows-latest"),
    ])
    def test_independent_qualification_requires_both_full_native_collections(self, platform, label):
        import yaml

        root = Path(__file__).resolve().parents[1]
        text = (root / ".github/workflows/standalone-coverage-qualification.yml").read_text()
        workflow = yaml.safe_load(text)
        jobs = workflow["jobs"]
        # PyYAML's YAML 1.1 loader treats the Actions "on" key as True.
        triggers = workflow.get("on", workflow.get(True))
        assert set(triggers) == {"workflow_dispatch"}
        assert workflow["permissions"] == {"contents": "read"}
        assert set(jobs) == {"gate", "fleet-manager-baselines-linux", "fleet-manager-baselines-windows"}
        identity = f"fleet-manager-{platform}"
        job_name = f"fleet-manager-baselines-{platform}"
        job = jobs[job_name]
        assert job["runs-on"] == label
        assert "strategy" not in job
        assert job["needs"] == "gate"
        assert "if" not in job
        assert job["steps"][0]["with"] == {
            "fetch-depth": 0, "ref": "${{ needs.gate.outputs.sha }}",
            "persist-credentials": False,
        }
        assert job["timeout-minutes"] == 15
        run = next(step["run"] for step in job["steps"] if "run" in step)
        assert "coverage_guided_selection/standalone.py fleet-manager" in run
        assert '--measured-commit "${{ needs.gate.outputs.sha }}"' in run
        assert "--timeout 720" in run
        assert f"--out {identity}.json" in run
        assert all(not step.get("continue-on-error") for step in job["steps"])
        assert job["steps"][-1]["with"] == {
            "name": f"coverage-baseline-{identity}", "path": f"{identity}.json",
            "if-no-files-found": "error", "retention-days": 7,
        }
        promotion = (root / ".github/workflows/validate-and-promote.yml").read_text()
        assert job_name not in promotion
        assert "coverage-baseline-" not in promotion
        assert "source-units" in yaml.safe_load(promotion)["jobs"]

    @pytest.mark.parametrize("source,valid", [
        ("a" * 40, True), ("a" * 39, False), ("A" * 40, False),
        ("dev", False), ("a" * 40 + "\n", False),
    ])
    def test_qualification_validates_immutable_input_before_native_jobs(self, tmp_path, monkeypatch, source, valid):
        import yaml

        root = Path(__file__).resolve().parents[1]
        workflow = yaml.safe_load((root / ".github/workflows/standalone-coverage-qualification.yml").read_text())
        gate = workflow["jobs"]["gate"]
        script = next(step["run"] for step in gate["steps"] if "run" in step)
        body = script.split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        output = tmp_path / "output"
        monkeypatch.setenv("MEASURED_COMMIT", source)
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        if valid:
            exec(compile(body, "<qualification-input>", "exec"), {})
            assert output.read_text() == "sha=" + source + "\n"
        else:
            with pytest.raises(ValueError, match="exact full source SHA"):
                exec(compile(body, "<qualification-input>", "exec"), {})
            assert not output.exists()

    def test_stage_one_does_not_activate_required_fleet_coverage_selection(self):
        import yaml

        root = Path(__file__).resolve().parents[1]
        jobs = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"]
        assert "unit-plan" in jobs and "unit-tests" in jobs
        assert not any(name.startswith("fleet-native-") for name in jobs)
        runs = "\n".join(step.get("run", "") for job in jobs.values() for step in job.get("steps", []))
        assert "coverage_guided_selection/standalone.py" not in runs
        assert "fleet-manager-linux" not in runs and "fleet-manager-windows" not in runs

    @pytest.mark.parametrize("initially_compatible", [False, True])
    def test_reused_environment_checks_and_provisions_parent_context_capability(
        self, tmp_path, monkeypatch, initially_compatible,
    ):
        calls = []
        installs = []

        def run(command, **kwargs):
            calls.append(command)
            status = 0 if initially_compatible or len(calls) > 1 else 1
            return subprocess.CompletedProcess(command, status, stdout="", stderr="unsupported")

        monkeypatch.setattr(standalone.subprocess, "run", run)
        monkeypatch.setattr(
            standalone, "run_uv_install",
            lambda uv, args, **kwargs: installs.append((uv, args, kwargs)),
        )
        python = tmp_path / "python"
        standalone._prepare_instrumentation(python, repo=tmp_path, prepare=False)
        probe = [str(python), "-I", "-c", standalone.COVERAGE_CAPABILITY_PROBE]
        assert calls[0] == probe
        if initially_compatible:
            assert calls == [probe]
            assert installs == []
        else:
            assert calls == [probe, probe]
            assert len(installs) == 1
            uv, arguments, options = installs[0]
            assert uv == "uv" and arguments == [
                "--python", str(python),
                "coverage>=7", "pytest-json-report",
            ]
            assert options["trusted_source_roots"] == (tmp_path,)
            assert options["cwd"] == tmp_path and options["check"] is True
            assert options["timeout"] == 300

    def test_incompatible_coverage_after_provisioning_is_a_visible_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            standalone.subprocess, "run",
            lambda command, **kwargs: subprocess.CompletedProcess(
                command, 1 if command[1] == "-I" else 0, stdout="", stderr="unsupported patch",
            ),
        )
        monkeypatch.setattr(standalone, "run_uv_install", lambda *_args, **_kwargs: None)
        with pytest.raises(standalone.BaselineCollectionError, match="parent dynamic-context"):
            standalone._prepare_instrumentation(tmp_path / "python", repo=tmp_path, prepare=False)

    def test_instrumentation_admission_refusal_is_not_retried_or_bypassed(self, tmp_path, monkeypatch):
        from seasoned_uv import DependencyPolicyError

        monkeypatch.setattr(
            standalone.subprocess, "run",
            lambda command, **kwargs: subprocess.CompletedProcess(
                command, 1, stdout="", stderr="instrumentation missing",
            ),
        )
        calls = []

        def refuse(*args, **kwargs):
            calls.append((args, kwargs))
            raise DependencyPolicyError("dependency seasoning refused")

        monkeypatch.setattr(standalone, "run_uv_install", refuse)
        with pytest.raises(DependencyPolicyError, match="dependency seasoning refused"):
            standalone._prepare_instrumentation(tmp_path / "python", repo=tmp_path, prepare=False)
        assert len(calls) == 1

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
            standalone._prepare_instrumentation(python, repo=repo, prepare=False)
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
            hook_text, coverage_options = standalone.coverage_files(sandbox, mapping)
            candidate = installed.parent / "_cgs_standalone.pth"
            with candidate.open("x", encoding="utf-8") as stream:
                hook = candidate
                stream.write(hook_text)
            tests = sandbox / "test_children.py"
            tests.write_text(
                "import os, pytest, sqlite3, subprocess, sys\n"
                "def child(abrupt=False):\n"
                "    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}\n"
                "    result = subprocess.run([sys.executable, '-I', '-c', "
                "\"import os; from fleet_manager.planning import canonical; "
                "canonical({'child': True}); \" + ('os._exit(23)' if abrupt else '')], "
                "capture_output=True, **flags)\n"
                "    assert result.returncode == (23 if abrupt else 0)\n"
                "def test_first(tmp_path):\n"
                "    path = tmp_path/'private-stage'/('fleet-artifact-stage-'+'x'*8)"
                "/'versions'/'example-r1'/'artifacts'/('0'*64)\n"
                "    path.mkdir(parents=True)\n"
                "    with sqlite3.connect((tmp_path/'db.sqlite').as_uri(), uri=True): pass\n"
                "    child()\n"
                "@pytest.mark.parametrize('value', ['%literal-${HOME}'])\n"
                "def test_second(value): child()\n"
                "def test_abrupt_exit(): child(abrupt=True)\n",
                encoding="utf-8",
            )
            driver = sandbox / "driver.py"
            driver.write_text(standalone._DRIVER_SCRIPT, encoding="utf-8")
            out = sandbox / "result.json"
            command = [
                 str(python), "-I", str(driver), json.dumps([
                     f"{tests}::{name}" for name in ("test_first", "test_second", "test_abrupt_exit")
                 ]), standalone.SOURCE,
                 str(repo), str(sandbox / ".coverage"), str(sandbox / "report.json"), str(out),
                 str(sandbox / "p"), json.dumps({
                     "installed_source": str(installed), "source_mapping": mapping,
                     **coverage_options, "compact_tmp_paths": os.name == "nt",
                 }),
            ]
            status = standalone.run_contained(
                command,
                cwd=repo / "fleet-manager", env=env, sandbox=sandbox,
                limits=standalone.Limits(wall_seconds=60),
            )
            assert status == 0
            result = json.loads(out.read_text())
            assert result["summary"]["passed"] == 3
            assert result["children"]["child_streams"] == 3
            assert result["children"]["audited_producers"] == 3
            key = "fleet-manager/src/fleet_manager/planning.py"
            nodes = set(result["durations"])
            attributed = next(line for line, covered in result["coverage"][key].items()
                              if set(covered) == nodes)
            selected = select_tests({"coverage": result["coverage"]}, {key: [int(attributed)]})
            assert set(selected.selected_tests) == nodes
            assert any(node.endswith("::test_abrupt_exit") for node in selected.selected_tests)
            assert not selected.fallback_triggered
        finally:
            try:
                if hook is not None:
                    hook.unlink(missing_ok=True)
            finally:
                standalone.runner.cleanup_sandbox(temporary)
                lease.release()
