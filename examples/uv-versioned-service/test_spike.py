"""Focused example decisions, with all writable state under this project."""

import importlib.util
import json
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import spike


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.root = HERE / ".runs" / "unit" / uuid.uuid4().hex
        self.project = self.root / "project"
        self.home = self.root / "home"
        self.project.mkdir(parents=True)
        (self.project / "pyproject.toml").write_text(
            '[project]\nname="uv-slot-example-service"\nversion="0.1.0"\n', encoding="utf-8"
        )
        (self.project / "uv.lock").write_text("locked fixture\n", encoding="utf-8")
        (self.project / ".venv").mkdir()
        (self.project / ".venv" / "must-not-copy").touch()

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_copy_does_not_execute_or_publish_and_copies_no_environment(self):
        with patch.object(spike.subprocess, "run", side_effect=AssertionError("execution")), (
            patch.object(spike.subprocess, "Popen", side_effect=AssertionError("execution"))
        ):
            result = spike.copy_candidate(self.project, self.home, "service")
        slot = Path(result["slot"])
        self.assertTrue(result["copied"])
        self.assertFalse((slot / ".venv").exists())
        self.assertFalse((slot / ".install-complete").exists())
        self.assertFalse((slot.parents[1] / "current-version").exists())
        self.assertFalse((self.home / "bin").exists())

    def test_completed_slot_skips_copy_but_incomplete_attempt_is_renamed(self):
        first = spike.copy_candidate(self.project, self.home, "service")
        slot = Path(first["slot"])
        (slot / ".install-complete").write_text("installed\n")
        (slot / "retained").touch()
        (self.project / "new-source").touch()
        self.assertFalse(spike.copy_candidate(self.project, self.home, "service")["copied"])
        self.assertFalse((slot / "new-source").exists())
        (slot / ".install-complete").unlink()
        self.assertTrue(spike.copy_candidate(self.project, self.home, "service")["copied"])
        failed = list(slot.parent.glob("0.1.0.failed.*"))
        self.assertEqual(len(failed), 1)
        self.assertTrue((failed[0] / "retained").exists())
        self.assertTrue((slot / "new-source").exists())

    def test_install_precedes_publication_and_tool_does_not_start_service(self):
        candidate = spike.copy_candidate(self.project, self.home, "tool")

        def install(argv, **kwargs):
            self.assertEqual(argv[-1], "install")
            self.assertFalse((self.home / "bin").exists())
            self.assertFalse((Path(candidate["slot"]) / ".install-complete").exists())
            return subprocess.CompletedProcess(argv, 0, "installed", "")

        with patch.object(spike.subprocess, "run", side_effect=install):
            result = spike.install_candidate(candidate, self.home, "tool")
        slot = Path(result["slot"])
        self.assertIn("install succeeded", (slot / ".install-complete").read_text())
        self.assertTrue(Path(result["binstub"]).exists())
        self.assertFalse((self.home / "services").exists())
        body = Path(result["binstub"]).read_text()
        self.assertNotIn("--no-sync", body)
        self.assertNotIn("--directory", body)
        self.assertNotIn("UV_CACHE_DIR", body)
        self.assertNotIn("uv run", body)
        for extension in ("ps1", "cmd", "sh"):
            self.assertTrue(Path(result["binstub"]).with_suffix("." + extension).exists())

    def test_failed_install_keeps_prior_publication_unchanged(self):
        with patch.object(spike.subprocess, "run", return_value=subprocess.CompletedProcess(
            [], 0, "installed", "",
        )):
            previous = spike.bootstrap(self.project, self.home, "service")
        binary = Path(previous["binstub"])
        owner = Path(previous["slot"]).parents[1]
        marker = Path(previous["slot"]) / ".install-complete"
        before = tuple(
            binary.with_suffix("." + extension).read_bytes() for extension in ("ps1", "cmd", "sh")
        ) + ((owner / "current-version").read_bytes(), marker.read_bytes())
        manifest = self.project / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace('version="0.1.0"', 'version="0.1.1"'))
        with patch.object(spike.subprocess, "run", side_effect=subprocess.CalledProcessError(
            17, ["uv", "run"],
        )):
            with self.assertRaises(subprocess.CalledProcessError):
                spike.bootstrap(self.project, self.home, "service")
        self.assertEqual(before, tuple(
            binary.with_suffix("." + extension).read_bytes() for extension in ("ps1", "cmd", "sh")
        ) + ((owner / "current-version").read_bytes(), marker.read_bytes()))
        failed = list((owner / "versions").glob("0.1.1.failed.*"))
        self.assertEqual(len(failed), 1)
        self.assertFalse((failed[0] / ".install-complete").exists())

    def test_bootstrap_refuses_unlocked_source(self):
        (self.project / "uv.lock").unlink()
        with self.assertRaisesRegex(ValueError, "already locked"):
            spike.bootstrap(self.project, self.home, "service")
        self.assertFalse((self.home / "services").exists())


class MaterializerTests(unittest.TestCase):
    def test_opt_in_uses_real_materializer_and_keeps_default_unchanged(self):
        sys.path.insert(0, str(HERE.parents[1] / "tools"))
        import materialize_main
        root = HERE / ".runs" / "composition-unit" / uuid.uuid4().hex
        source = root / "dev"
        target = root / "main"
        example = source / "examples" / "uv-versioned-service"
        example.mkdir(parents=True)
        (example / "assembly.py").write_text(
            "def assemble(destination, source, materialize_lib):\n"
            "    (destination / 'assembled').write_text('called')\n"
            "    return ['OK   example']\n", encoding="utf-8"
        )
        try:
            materialize_main.build(target, source_root=source)
            self.assertFalse((target / "assembled").exists())
            materialize_main.build(target, source_root=source, uv_example=True)
            self.assertEqual((target / "assembled").read_text(), "called")
            spec = importlib.util.spec_from_file_location("assembly_test", HERE / "assembly.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertTrue(callable(module.assemble))
        finally:
            shutil.rmtree(root)

    def test_staging_reuses_existing_folder_without_git_or_head_checks(self):
        root = HERE / ".runs" / "staging-unit" / uuid.uuid4().hex
        root.mkdir(parents=True)
        try:
            (root / "sentinel").write_text(json.dumps({"retained": True}))
            with patch.object(spike.subprocess, "run", side_effect=AssertionError("git called")):
                self.assertFalse(spike.stage("unused", "unused", root))
            self.assertTrue((root / "sentinel").exists())
        finally:
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
