from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import construction_inventory as inventory


PROJECT = """\
[project]
name = "{name}"
requires-python = ">=3.10"
dependencies = ["example>=1"]
[project.optional-dependencies]
dev = ["pytest>=8"]
server = ["server-example>=1"]
[project.scripts]
example = "example.__main__:main"
[build-system]
requires = ["setuptools>=70"]
build-backend = "setuptools.build_meta"
"""


class ConstructionInventoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.write_roster(("apps/service",))
        self.plugins = [
            {"name": "runtime", "source": "./plugins/runtime"},
            {"name": "guidance", "source": "./plugins/guidance"},
        ]
        self.write_marketplace()
        self.write_json("plugins/runtime/plugin.json", {"name": "runtime", "runtimeScope": "universal"})
        self.write_json("plugins/guidance/plugin.json", {"name": "guidance", "runtimeScope": "none"})
        self.projects = []
        self.add_project("plugins/runtime/pyproject.toml", "runtime")
        self.add_project("apps/service/pyproject.toml", "service")
        self.add_project("libs/shared/pyproject.toml", "shared")

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_json(self, relative, content):
        self.write(relative, json.dumps(content))

    def write_marketplace(self):
        self.write_json(".github/plugin/marketplace.json", {"plugins": self.plugins})

    def write_roster(self, components):
        self.write("tools/standalone_consumers.py", f"STANDALONE_CONSUMERS = {components!r}\n")

    def add_project(self, relative, name):
        self.write(relative, PROJECT.format(name=name))
        self.projects.append(relative)

    def report(self):
        return inventory.inventory(self.root, self.projects)

    def test_uses_both_canonical_rosters_without_counting_extras_or_libraries_as_venvs(self):
        report = self.report()
        self.assertEqual(report["marketplace_entries"], 2)
        self.assertEqual(len(report["owners"]), 2)
        self.assertEqual(report["shared_library_inputs"], ["libs/shared/pyproject.toml"])
        self.assertTrue(report["source_owner_inventory_complete"])
        self.assertEqual(report["runtime_qualification"], "not-performed")
        for owner in report["owners"]:
            self.assertEqual(owner["optional_dependency_groups"], ["dev", "server"])
            self.assertEqual(owner["runtime_requirement_count"], 1)
            self.assertEqual(owner["build_requirement_count"], 1)
            self.assertEqual(owner["requires_python"], ">=3.10")
        self.assertTrue(report["remaining_classification"])

    def test_new_marketplace_runtime_is_discovered_without_an_allowlist_update(self):
        self.plugins.append({"name": "new-runtime", "source": "./plugins/new"})
        self.write_marketplace()
        self.write_json("plugins/new/plugin.json", {"name": "new-runtime", "runtimeScope": "machine-gated"})
        self.add_project("plugins/new/pyproject.toml", "new-runtime")
        self.assertIn("new-runtime", {owner["owner"] for owner in self.report()["owners"]})

    def test_nested_engine_and_unclassified_fixture_remain_visible_and_incomplete(self):
        self.add_project("plugins/runtime/engine/pyproject.toml", "engine")
        self.add_project("tools/fixtures/example/pyproject.toml", "fixture")
        report = self.report()
        self.assertFalse(report["source_owner_inventory_complete"])
        self.assertEqual(
            report["unclassified_python_projects"],
            ["plugins/runtime/engine/pyproject.toml", "tools/fixtures/example/pyproject.toml"],
        )

    def test_explicit_additional_project_is_owned_without_inferring_its_environment(self):
        self.add_project("plugins/runtime/engine/pyproject.toml", "engine")
        self.write_json(
            "plugins/runtime/plugin.json",
            {"name": "runtime", "runtimeScope": "universal", "additionalPythonProjects": ["engine/pyproject.toml"]},
        )
        report = self.report()
        self.assertTrue(report["source_owner_inventory_complete"])
        self.assertEqual(len(report["owners"]), 3)
        engine = next(owner for owner in report["owners"] if owner["kind"] == "plugin-python-project")
        self.assertEqual(engine["distribution"], "engine")
        self.assertEqual(engine["owner"], "runtime:engine/pyproject.toml")
        self.assertEqual(report["runtime_qualification"], "not-performed")

    def test_additional_project_declarations_refuse_missing_duplicate_or_escaping_paths(self):
        self.add_project("plugins/runtime/engine/pyproject.toml", "engine")
        for paths in (
            ["missing/pyproject.toml"],
            ["engine/pyproject.toml", "engine/pyproject.toml"],
            ["../foreign/pyproject.toml"],
            ["pyproject.toml"],
            ["engine/README.md"],
            "engine/pyproject.toml",
        ):
            with self.subTest(paths=paths):
                self.write_json(
                    "plugins/runtime/plugin.json",
                    {"name": "runtime", "runtimeScope": "universal", "additionalPythonProjects": paths},
                )
                with self.assertRaises(inventory.InventoryError):
                    self.report()

    def test_base_and_additional_project_ownership_cannot_depend_on_marketplace_order(self):
        child = {"name": "child", "source": "plugins/runtime/child"}
        self.add_project("plugins/runtime/child/pyproject.toml", "child")
        self.write_json("plugins/runtime/child/plugin.json", {"name": "child", "runtimeScope": "universal"})
        self.write_json(
            "plugins/runtime/plugin.json",
            {"name": "runtime", "runtimeScope": "universal", "additionalPythonProjects": ["child/pyproject.toml"]},
        )
        parent, guidance = self.plugins
        for plugins in ([parent, child, guidance], [child, parent, guidance]):
            with self.subTest(plugins=plugins):
                self.plugins = plugins
                self.write_marketplace()
                with self.assertRaisesRegex(inventory.InventoryError, "multiply owned Python project"):
                    self.report()

    def test_plugin_and_standalone_cannot_own_the_same_project(self):
        self.plugins.append({"name": "service", "source": "apps/service"})
        self.write_marketplace()
        self.write_json("apps/service/plugin.json", {"name": "service", "runtimeScope": "universal"})
        with self.assertRaisesRegex(inventory.InventoryError, "multiply owned Python project"):
            self.report()

    def test_resolved_additional_project_cannot_escape_its_plugin(self):
        path = "plugins/runtime/engine/pyproject.toml"
        self.add_project(path, "engine")
        self.write_json(
            "plugins/runtime/plugin.json",
            {"name": "runtime", "runtimeScope": "universal", "additionalPythonProjects": ["engine/pyproject.toml"]},
        )
        source_file = inventory._source_file

        def resolved_source(root, relative):
            if relative == path:
                return self.root / "apps/service/pyproject.toml"
            return source_file(root, relative)

        with patch.object(inventory, "_source_file", side_effect=resolved_source):
            with self.assertRaisesRegex(inventory.InventoryError, "escapes its owning source root"):
                self.report()

    def test_resolved_project_alias_cannot_acquire_a_second_owner(self):
        path = "plugins/runtime/alias/pyproject.toml"
        self.add_project(path, "alias")
        self.write_json(
            "plugins/runtime/plugin.json",
            {"name": "runtime", "runtimeScope": "universal", "additionalPythonProjects": ["alias/pyproject.toml"]},
        )
        source_file = inventory._source_file

        def resolved_source(root, relative):
            if relative == path:
                return self.root / "plugins/runtime/pyproject.toml"
            return source_file(root, relative)

        with patch.object(inventory, "_source_file", side_effect=resolved_source):
            with self.assertRaisesRegex(inventory.InventoryError, "multiply owned Python project"):
                self.report()

    def test_native_symlink_cannot_assign_a_foreign_project_to_a_plugin(self):
        path = self.root / "plugins/runtime/engine/pyproject.toml"
        path.parent.mkdir()
        try:
            path.symlink_to(self.root / "apps/service/pyproject.toml")
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("native file symlink privilege is unavailable")
            raise
        self.projects.append(path.relative_to(self.root).as_posix())
        self.write_json(
            "plugins/runtime/plugin.json",
            {"name": "runtime", "runtimeScope": "universal", "additionalPythonProjects": ["engine/pyproject.toml"]},
        )
        with self.assertRaisesRegex(inventory.InventoryError, "escapes its owning source root"):
            self.report()

    def test_vendored_library_inputs_are_not_counted_as_environments(self):
        self.add_project("plugins/runtime/libs/shared/pyproject.toml", "shared")
        self.add_project("plugins/guidance/libs/shared/pyproject.toml", "shared")
        report = self.report()
        self.assertEqual(len(report["owners"]), 2)
        self.assertTrue(report["source_owner_inventory_complete"])
        self.assertEqual(
            report["vendored_library_inputs"],
            [
                {"project": "plugins/guidance/libs/shared/pyproject.toml", "canonical": "libs/shared/pyproject.toml"},
                {"project": "plugins/runtime/libs/shared/pyproject.toml", "canonical": "libs/shared/pyproject.toml"},
            ],
        )

    def test_undeclared_or_differently_named_nested_libraries_cannot_disappear(self):
        self.add_project("plugins/runtime/libs/unknown/pyproject.toml", "unknown")
        self.assertFalse(self.report()["source_owner_inventory_complete"])
        self.add_project("plugins/runtime/libs/shared/pyproject.toml", "different")
        with self.assertRaisesRegex(inventory.InventoryError, "vendored library identity mismatch"):
            self.report()

    def test_library_identity_uses_python_distribution_name_normalization(self):
        self.add_project("libs/example/pyproject.toml", "Example_Name")
        self.add_project("plugins/runtime/libs/example/pyproject.toml", "example-name")
        self.assertTrue(self.report()["source_owner_inventory_complete"])

    def test_malformed_library_input_is_not_silently_classified(self):
        self.write("libs/shared/pyproject.toml", '[project]\nname = "shared"\n')
        with self.assertRaisesRegex(inventory.InventoryError, "requires-python"):
            self.report()

    def test_standalone_roster_growth_requires_its_actual_project(self):
        self.write_roster(("apps/service", "apps/new"))
        with self.assertRaisesRegex(inventory.InventoryError, "missing.*standalone"):
            self.report()

    def test_requested_root_roster_is_authoritative_and_is_never_executed(self):
        self.write_roster(())
        self.write(
            "tools/standalone_consumers.py",
            "raise AssertionError('must not execute candidate code')\nSTANDALONE_CONSUMERS = ()\n",
        )
        report = self.report()
        self.assertEqual(len(report["owners"]), 1)
        self.assertEqual(report["unclassified_python_projects"], ["apps/service/pyproject.toml"])

    def test_invalid_missing_or_computed_standalone_roster_is_refused(self):
        for source in (
            "OTHER = ()",
            "STANDALONE_CONSUMERS = make_roster()",
            "STANDALONE_CONSUMERS = 'apps/service'",
            "STANDALONE_CONSUMERS = ['apps/service']",
            "STANDALONE_CONSUMERS = ('apps/service', 'apps/service')",
            "STANDALONE_CONSUMERS = ('../foreign',)",
            "STANDALONE_CONSUMERS = ()\nSTANDALONE_CONSUMERS += ('apps/service',)",
            "if True:\n    STANDALONE_CONSUMERS = ()",
        ):
            with self.subTest(source=source):
                self.write("tools/standalone_consumers.py", source)
                with self.assertRaises(inventory.InventoryError):
                    self.report()

    def test_mutable_roster_cannot_hide_later_standalone_consumers(self):
        self.write(
            "tools/standalone_consumers.py",
            "STANDALONE_CONSUMERS = ['apps/service']\n"
            "STANDALONE_CONSUMERS.append('apps/missing')\n",
        )
        with self.assertRaisesRegex(inventory.InventoryError, "immutable literal tuple"):
            self.report()

    def test_missing_declared_runtime_cannot_be_omitted_as_payload_only(self):
        self.projects.remove("plugins/runtime/pyproject.toml")
        (self.root / "plugins/runtime/pyproject.toml").unlink()
        with self.assertRaisesRegex(inventory.InventoryError, "missing runtime project"):
            self.report()

    def test_untracked_runtime_declaration_is_not_silently_used(self):
        self.projects.remove("plugins/runtime/pyproject.toml")
        with self.assertRaisesRegex(inventory.InventoryError, "untracked runtime declaration"):
            self.report()

    def test_payload_only_runtime_contradiction_refuses_inventory(self):
        self.write_json("plugins/runtime/plugin.json", {"name": "runtime", "runtimeScope": "none"})
        with self.assertRaisesRegex(inventory.InventoryError, "runtimeScope none"):
            self.report()

    def test_duplicate_plugin_name_or_source_refuses_inventory(self):
        for entry in (
            {"name": "runtime", "source": "./plugins/other"},
            {"name": "other", "source": "plugins/runtime"},
        ):
            with self.subTest(entry=entry):
                self.plugins.append(entry)
                self.write_marketplace()
                with self.assertRaisesRegex(inventory.InventoryError, "duplicate plugin"):
                    self.report()
                self.plugins.pop()

    def test_manifest_identity_mismatch_refuses_inventory(self):
        self.write_json("plugins/runtime/plugin.json", {"name": "foreign"})
        with self.assertRaisesRegex(inventory.InventoryError, "identity mismatch"):
            self.report()

    def test_invalid_source_and_project_paths_are_refused(self):
        for value in ("../foreign", "/foreign", "C:/foreign", "plugins\\runtime", "."):
            with self.subTest(value=value):
                self.plugins[0]["source"] = value
                self.write_marketplace()
                with self.assertRaises(inventory.InventoryError):
                    self.report()

    def test_duplicate_or_missing_tracked_project_is_refused(self):
        with self.assertRaisesRegex(inventory.InventoryError, "duplicate tracked"):
            inventory.inventory(self.root, self.projects + self.projects[:1])
        (self.root / "libs/shared/pyproject.toml").unlink()
        with self.assertRaisesRegex(inventory.InventoryError, "missing or escaping"):
            self.report()

    def test_malformed_project_cannot_produce_a_complete_inventory(self):
        self.write("plugins/runtime/pyproject.toml", '[project]\nname = "runtime"\n')
        with self.assertRaisesRegex(inventory.InventoryError, "requires-python"):
            self.report()
        self.write(
            "plugins/runtime/pyproject.toml",
            PROJECT.format(name="runtime").replace('dependencies = ["example>=1"]', 'dependencies = "example"'),
        )
        with self.assertRaisesRegex(inventory.InventoryError, "array of strings"):
            self.report()

    def test_cli_reports_unknown_owners_with_nonzero_status(self):
        self.add_project("other/pyproject.toml", "unknown")
        output = io.StringIO()
        with patch.object(inventory, "tracked_projects", return_value=self.projects):
            with contextlib.redirect_stdout(output):
                self.assertEqual(inventory.main(["--root", str(self.root)]), 1)
        self.assertFalse(json.loads(output.getvalue())["source_owner_inventory_complete"])

    def test_cli_errors_are_visible_and_not_success_shaped(self):
        self.write_json(".github/plugin/marketplace.json", [])
        output = io.StringIO()
        with patch.object(inventory, "tracked_projects", return_value=self.projects):
            with contextlib.redirect_stderr(output):
                self.assertEqual(inventory.main(["--root", str(self.root)]), 2)
        self.assertIn("construction source inventory failed", output.getvalue())

    def test_git_inventory_is_finite_read_only_and_ignores_inherited_git_redirection(self):
        with patch.dict(inventory.os.environ, {"GIT_DIR": "foreign", "GIT_WORK_TREE": "foreign"}):
            with patch.object(inventory.subprocess, "run") as run:
                run.return_value.stdout = b"apps/service/pyproject.toml\0"
                self.assertEqual(inventory.tracked_projects(self.root), ["apps/service/pyproject.toml"])
        args, kwargs = run.call_args
        self.assertEqual(args[0], ["git", "ls-files", "-z", "--", "pyproject.toml", "**/pyproject.toml"])
        self.assertFalse(any(key.startswith("GIT_") for key in kwargs["env"]))
        self.assertEqual(kwargs["timeout"], 30)
        self.assertTrue(kwargs["check"])

    def test_real_cli_uses_the_tracked_tree_and_refuses_an_unclassified_project(self):
        self.add_project("other/pyproject.toml", "unknown")
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        options = {
            "cwd": self.root,
            "env": env,
            "capture_output": True,
            "text": True,
            "timeout": 30,
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        }
        for command in (
            ["git", "-c", "core.fsmonitor=false", "init", "--quiet"],
            ["git", "-c", "core.fsmonitor=false", "add", "--", "."],
        ):
            subprocess.run(command, check=True, **options)
        result = subprocess.run(
            [sys.executable, str(Path(inventory.__file__).resolve()), "--root", str(self.root)],
            check=False,
            **options,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(len(report["owners"]), 2)
        self.assertEqual(report["unclassified_python_projects"], ["other/pyproject.toml"])
        self.assertEqual(report["runtime_qualification"], "not-performed")


if __name__ == "__main__":
    unittest.main()
