import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
import venv

from helper_closure_probe import (
    allocation_proof, inventory, packages, python_at, run, sources, wheel_requirements,
)


class ClosureProbeTests(unittest.TestCase):
    def runtime_fixture(self, root):
        runtime = root / "runtime"
        venv.EnvBuilder(with_pip=False, symlinks=False).create(runtime)
        output, _ = run([
            python_at(runtime), "-I", "-c",
            "import sysconfig; print(sysconfig.get_path('purelib'))",
        ])
        site = Path(output.strip())
        metadata = site / "fixture_helper-1.0.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: fixture-helper\nVersion: 1.0\n",
            encoding="utf-8",
        )
        (metadata / "RECORD").write_text(
            "fixture_helper-1.0.dist-info/METADATA,,\n", encoding="utf-8"
        )
        return runtime, site, metadata

    def test_hardlinks_do_not_increase_unique_allocation(self):
        with TemporaryDirectory() as directory:
            proof = allocation_proof(Path(directory))
        self.assertEqual(proof["single"]["logical_bytes"], 4097)
        self.assertEqual(proof["hardlink"]["duplicate_file_links"], 1)
        self.assertEqual(
            proof["single"]["unique_allocated_bytes"],
            proof["hardlink"]["unique_allocated_bytes"],
        )
        self.assertEqual(
            proof["independent_copy"]["unique_allocated_bytes"],
            2 * proof["single"]["unique_allocated_bytes"],
        )

    def test_union_counts_cross_runtime_file_identity_once(self):
        with TemporaryDirectory() as directory:
            first, second = (Path(directory) / name for name in ("first", "second"))
            first.mkdir()
            second.mkdir()
            (first / "helper.py").write_bytes(b"x" * 4097)
            os.link(first / "helper.py", second / "helper.py")
            one = inventory([first])
            combined = inventory([first, second])
        self.assertEqual(combined["files"], 2)
        self.assertEqual(combined["logical_bytes"], 2 * one["logical_bytes"])
        self.assertEqual(combined["unique_allocated_bytes"], one["unique_allocated_bytes"])
        self.assertEqual(combined["duplicate_file_links"], 1)

    def test_source_cycle_is_bounded(self):
        with TemporaryDirectory() as directory:
            repo = Path(directory).resolve()
            first, second = (repo / name for name in ("first", "second"))
            first.mkdir()
            second.mkdir()
            (first / "pyproject.toml").write_text(
                '[tool.uv.sources]\nsecond = {path = "../second"}\n', encoding="utf-8"
            )
            (second / "pyproject.toml").write_text(
                '[tool.uv.sources]\nfirst = {path = "../first"}\n', encoding="utf-8"
            )
            self.assertEqual(sources(repo, first), sorted([first, second]))

    def test_escaping_source_is_refused(self):
        with TemporaryDirectory() as directory:
            repo = Path(directory).resolve()
            (repo / "pyproject.toml").write_text(
                '[tool.uv.sources]\nforeign = {path = ".."}\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "escapes"):
                sources(repo, repo)

    def test_wheel_override_preserves_required_metrics_extra(self):
        with TemporaryDirectory() as directory:
            repo = Path(directory).resolve()
            (repo / "pyproject.toml").write_text(
                '[project]\nname = "consumer"\n'
                'dependencies = ["agent-procutil[metrics]>=0.1"]\n',
                encoding="utf-8",
            )
            wheel = repo / "agent_procutil-0.1-py3-none-any.whl"
            actual = wheel_requirements(repo, repo, {"agent-procutil": wheel})
            self.assertEqual(actual, f"agent-procutil[metrics] @ {wheel.as_uri()}\n")

    def test_existing_output_root_is_refused_before_installation(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "sentinel.json"
            sentinel.write_text(json.dumps({"preserve": True}), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("helper_closure_probe.py")),
                 "--repo", str(root), "--root", str(root), "--uv", "never-invoked"],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("FileExistsError", result.stderr)
            self.assertEqual(json.loads(sentinel.read_text()), {"preserve": True})

    def test_retirement_guard_survives_optimized_python(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "closure-results.json").write_text("{}", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-O",
                 str(Path(__file__).with_name("helper_lifetime_probe.py")),
                 "--root", str(root), "--uv", "never-invoked"],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("retirement requires an accepted closure-parity report", result.stderr)

    def test_measurement_guard_survives_optimized_python(self):
        result = subprocess.run(
            [sys.executable, "-O", "-c",
             "from helper_closure_probe import require; require(False, 'reject false evidence')"],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reject false evidence", result.stderr)

    def test_in_runtime_metadata_cannot_hide_external_helper_import(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runtime, site, _ = self.runtime_fixture(root)
            outside = root / "outside"
            outside.mkdir()
            (outside / "fixture_helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            (site / "fixture-helper.pth").write_text(str(outside) + "\n", encoding="utf-8")
            self.assertEqual(packages(runtime)[0]["name"], "fixture-helper")
            with self.assertRaisesRegex(RuntimeError, "required module escapes runtime"):
                packages(runtime, ["fixture_helper"])

    def test_escaping_recorded_file_is_refused_not_filtered(self):
        with TemporaryDirectory() as directory:
            runtime, _, metadata = self.runtime_fixture(Path(directory))
            with (metadata / "RECORD").open("a", encoding="utf-8") as record:
                record.write("../../../../omitted.py,,\n")
            with self.assertRaisesRegex(RuntimeError, "recorded payload escapes runtime"):
                packages(runtime)

    def test_owned_helper_module_is_accepted(self):
        with TemporaryDirectory() as directory:
            runtime, site, _ = self.runtime_fixture(Path(directory))
            (site / "fixture_helper.py").write_text("VALUE = 1\n", encoding="utf-8")
            self.assertEqual(packages(runtime, ["fixture_helper"])[0]["name"], "fixture-helper")


if __name__ == "__main__":
    unittest.main()
