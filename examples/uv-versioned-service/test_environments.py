"""Focused closure/portability decisions, without constructing environments."""

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
import materialize_uv_projects as projects
sys.path.insert(0, str(HERE))
import validate_environments as witness


class EnvironmentProjectsTests(unittest.TestCase):
    def setUp(self):
        self.root = HERE / ".runs" / "environment-unit" / uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.root)

    def project(self, name, dependencies):
        path = self.root / "source" / name
        path.mkdir(parents=True)
        values = ",".join(repr(value) for value in dependencies)
        (path / "pyproject.toml").write_text(
            f"[project]\nname='{name}'\nversion='1.0'\ndependencies=[{values}]\n",
            encoding="utf-8",
        )
        return path

    def test_recursive_first_party_closure_is_relative_and_handles_cycles(self):
        app = self.project("app", ["first"])
        first = self.project("first", ["second", "app"])
        second = self.project("second", ["first"])
        output = self.root / "ready"
        projects.copy_project(app, output)
        projects.close_project(app, output, {"app": app, "first": first, "second": second}, self.root)
        self.assertEqual(projects.portable(output), [])
        self.assertEqual(
            projects.metadata(output)["tool"]["uv"]["sources"]["first"], {"path": "libs/first"}
        )
        self.assertEqual(
            projects.metadata(output / "libs/second")["tool"]["uv"]["sources"]["first"],
            {"path": "../first"},
        )
        self.assertNotIn("tool", projects.metadata(app))
        self.assertNotIn(
            "app", projects.metadata(output / "libs/first")["tool"]["uv"]["sources"],
        )

    def test_portability_rejects_host_paths_without_changing_uv_lock(self):
        project = self.project("app", [])
        lock = project / "uv.lock"
        content = 'version=1\n[[package]]\nsource={directory="C:/host/project"}\n'
        lock.write_text(content, encoding="utf-8")
        self.assertIn("absolute local", projects.portable(project)[0])
        self.assertEqual(lock.read_text(), content)

    def test_source_table_rewrite_preserves_other_project_configuration(self):
        project = self.project("app", [])
        path = project / "pyproject.toml"
        path.write_text(path.read_text() + '\n[tool.uv]\nexclude-newer="7 days"\n')
        path.write_text(path.read_text() + '\n[tool.uv.sources]\nold={path="../old"}\n')
        projects.source_table(project, {"dependency": {"path": "libs/dependency"}})
        data = projects.metadata(project)
        self.assertEqual(data["tool"]["uv"]["exclude-newer"], "7 days")
        self.assertEqual(data["tool"]["uv"]["sources"]["dependency"]["path"], "libs/dependency")

    def test_retirement_removes_only_version_leaf_and_preserves_cached_file(self):
        root = self.root / "slots"
        slot = root / "project" / "versions" / "1.0"
        cache = self.root / "cache"
        slot.mkdir(parents=True)
        cache.mkdir()
        cached = cache / "cached.py"
        cached.write_text("value = 1\n")
        before = cached.stat()
        os.link(cached, slot / "cached.py")
        with self.assertRaises(ValueError):
            witness.retire_version_slots([cache], root, cache)
        self.assertTrue(slot.exists())
        result = witness.retire_version_slots([slot], root, cache)
        self.assertFalse(slot.exists())
        self.assertTrue(result["cache_preserved"])
        self.assertTrue(os.path.samestat(before, cached.stat()))
        self.assertEqual(cached.read_text(), "value = 1\n")

    def test_rebuild_comparison_uses_aggregate_time_and_reports_regressions(self):
        first = [
            {"distribution": "a", "uv_init_and_probe_seconds": 10, "copy_seconds": 1},
            {"distribution": "b", "uv_init_and_probe_seconds": 1, "copy_seconds": 1},
        ]
        second = [
            {"distribution": "a", "uv_init_and_probe_seconds": 5, "copy_seconds": 2},
            {"distribution": "b", "uv_init_and_probe_seconds": 2, "copy_seconds": 2},
        ]
        result = witness.compare_rebuilds(first, second)
        self.assertAlmostEqual(result["aggregate_init_speedup_ratio"], 11 / 7, places=3)
        self.assertEqual(result["median_project_speedup_ratio"], 1.25)
        self.assertEqual([row["distribution"] for row in result["regressions"]], ["b"])


if __name__ == "__main__":
    unittest.main()
