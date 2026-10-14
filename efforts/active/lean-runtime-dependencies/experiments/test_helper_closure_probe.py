import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from helper_closure_probe import allocation_proof, inventory, sources, wheel_requirements


class ClosureProbeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
