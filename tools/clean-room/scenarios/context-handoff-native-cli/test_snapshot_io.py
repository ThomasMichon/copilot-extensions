from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import snapshot_io
import controller
import test_validation


@unittest.skipUnless(os.name == "posix", "descriptor no-follow proof requires the Linux fixture")
class SnapshotTests(unittest.TestCase):
    def export_fixture(self, root, stack):
        fixture = test_validation.ValidationTests()
        stack.callback(fixture.doCleanups)
        original, state, events = fixture.analyzer_fixture(root / "original")
        capsule = test_validation.TEST_SEEDS[str(original / "workspace")]
        capsule.chmod(0o755)
        (capsule / "venue-seal.json").write_text(json.dumps({
            "session_id": "root-fixture", "operator_sudo_denied": True, "sudo_elevation_bits_removed": True,
        }))
        home = root / "home"
        session = home / ".copilot/session-state/root-fixture"
        session.mkdir(parents=True)
        (session / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
        evaluation = root / "export/eval"
        evaluation.mkdir(parents=True)
        for name, value in {"BASELINE": capsule, "EVAL": evaluation, "OUT": evaluation.parent,
                            "STATE": state, "REPO": original / "workspace",
                            "SCENARIO": Path(__file__).parent}.items():
            stack.enter_context(patch.object(controller, name, value))
        stack.enter_context(patch.object(Path, "home", return_value=home))
        stack.enter_context(patch.object(controller, "run", return_value="true"))
        stack.enter_context(patch.object(controller.subprocess, "run",
                                        return_value=type("Result", (), {"returncode": 1})()))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return original, evaluation, capsule

    def test_export_recopies_every_protected_seed_file_and_seal(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            original, evaluation, capsule = self.export_fixture(Path(temporary), stack)
            archive = evaluation / "seed-baseline"
            errors = []
            snapshot_io.snapshot(capsule, archive, errors, "fixture")
            self.assertFalse(errors)
            guidance = next(capsule.rglob("AGENTS.md")).relative_to(capsule)
            (archive / guidance).write_bytes(b"altered fixture guidance")
            (archive / "venue-seal.json").write_bytes(b"{}")
            result = controller.export()
            self.assertEqual(result["export_errors"], [])
            self.assertEqual((archive / guidance).read_bytes(), (capsule / guidance).read_bytes())
            self.assertEqual((archive / "venue-seal.json").read_bytes(), (capsule / "venue-seal.json").read_bytes())
            receipt = json.loads((evaluation / "export-errors.json").read_text())
            self.assertIn(guidance.as_posix(), receipt["seed_copied"])
            self.assertIn("venue-seal.json", receipt["seed_copied"])

    def test_uncopied_workspace_witness_is_preserved_without_credit(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            original, evaluation, capsule = self.export_fixture(Path(temporary), stack)
            (evaluation / "workspace").mkdir()
            witness = evaluation / "workspace/witness.json"
            witness.write_text('{"synthetic": "stale witness"}')
            previous = witness.read_bytes()
            result = controller.export()
            self.assertEqual(result["event_export_status"], "INVALID_EXPORT")
            self.assertNotIn("witness.json", json.loads((evaluation / "export-errors.json").read_text())["workspace_copied"])
            controller.analyze()
            observed = json.loads((evaluation / "observed-evidence.json").read_text())
            self.assertEqual(witness.read_bytes(), previous)
            self.assertNotIn("document", observed["witness_contract_observation"])
            self.assertTrue(observed["witness_contract_observation"]["errors"])
            self.assertFalse(observed["root_clear_events"])

    def test_missing_protected_copy_invalidates_archive_even_with_genuine_manifest(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            original, evaluation, capsule = self.export_fixture(Path(temporary), stack)
            archive = evaluation / "seed-baseline"
            errors = []
            snapshot_io.snapshot(capsule, archive, errors, "fixture")
            guidance = next(capsule.rglob("AGENTS.md")).relative_to(capsule)
            previous = (archive / guidance).read_bytes()
            copy = snapshot_io.write_at

            def deny_guidance(parent, name, content):
                if name == "AGENTS.md":
                    raise PermissionError("fixture denied")
                return copy(parent, name, content)

            with patch.object(snapshot_io, "write_at", side_effect=deny_guidance):
                result = controller.export()
            self.assertEqual(result["event_export_status"], "INVALID_EXPORT")
            self.assertTrue(any(error.get("path") == guidance.as_posix() for error in result["export_errors"]))
            self.assertEqual((archive / guidance).read_bytes(), previous)

    def test_regular_snapshot_and_redaction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "file.txt").write_bytes(b"synthetic-secret")
            errors = []
            snapshot_io.snapshot(source, root / "evidence", errors, "fixture")
            self.assertFalse(errors)
            self.assertEqual(snapshot_io.read_bytes(root / "evidence/file.txt"), b"synthetic-secret")
            touched = snapshot_io.redact_tree(root / "evidence", b"synthetic-secret", errors)
            self.assertEqual(touched, ["file.txt"])
            self.assertEqual(snapshot_io.read_bytes(root / "evidence/file.txt"), b"[REDACTED]")

    def test_source_and_destination_links_are_quarantined(self):
        for side in ("source-file", "source-directory", "destination-file", "destination-directory",
                     "source-root", "destination-root"):
            with self.subTest(side=side), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                outside = root / "outside"
                outside.mkdir()
                (outside / "file.txt").write_bytes(b"synthetic-secret")
                source, destination = root / "source", root / "evidence"
                source.mkdir()
                destination.mkdir()
                (source / "nested").mkdir()
                (source / "nested/file.txt").write_bytes(b"source")
                (source / "file.txt").write_bytes(b"source")
                if side.endswith("root"):
                    target = source if side.startswith("source") else destination
                    target.rename(root / "saved")
                    target.symlink_to(outside, target_is_directory=True)
                else:
                    target = source if side.startswith("source") else destination
                    name = "nested" if side.endswith("directory") else "file.txt"
                    existing = target / name
                    if existing.is_dir():
                        existing.rename(root / "saved")
                    elif existing.exists():
                        existing.unlink()
                    existing.symlink_to(outside if name == "nested" else outside / "file.txt",
                                        target_is_directory=name == "nested")
                errors = []
                snapshot_io.snapshot(source, destination, errors, "fixture")
                self.assertTrue(errors)
                self.assertEqual((outside / "file.txt").read_bytes(), b"synthetic-secret")
                for entry in destination.rglob("*") if not destination.is_symlink() else []:
                    if entry.is_file() and not entry.is_symlink():
                        self.assertNotEqual(entry.read_bytes(), b"synthetic-secret")

    def test_redaction_never_reads_or_changes_link_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "outside.txt"
            outside.write_bytes(b"synthetic-secret")
            evidence = root / "evidence"
            evidence.mkdir()
            (evidence / "link").symlink_to(outside)
            errors = []
            self.assertEqual(snapshot_io.redact_tree(evidence, b"synthetic-secret", errors), [])
            self.assertTrue(errors)
            self.assertEqual(outside.read_bytes(), b"synthetic-secret")

    def test_unmatched_existing_destination_link_also_invalidates_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / "source", root / "evidence"
            source.mkdir()
            destination.mkdir()
            outside = root / "outside"
            outside.write_bytes(b"synthetic-secret")
            (destination / "unmatched").symlink_to(outside)
            errors = []
            snapshot_io.snapshot(source, destination, errors, "fixture")
            self.assertTrue(errors)
            self.assertEqual(outside.read_bytes(), b"synthetic-secret")

    def test_path_ancestors_and_hardlinks_are_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "real").mkdir()
            (root / "real/file").write_bytes(b"synthetic-secret")
            (root / "alias").symlink_to(root / "real", target_is_directory=True)
            with self.assertRaises(OSError):
                snapshot_io.read_bytes(root / "alias/file")
            with self.assertRaises(OSError):
                snapshot_io.write_bytes(root / "alias/file", b"changed")
            os.link(root / "real/file", root / "hardlink")
            with self.assertRaises(OSError):
                snapshot_io.read_bytes(root / "hardlink")
            with self.assertRaises(OSError):
                snapshot_io.write_bytes(root / "hardlink", b"changed")
            self.assertEqual((root / "real/file").read_bytes(), b"synthetic-secret")

    def test_swapped_source_file_cannot_be_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            (source / "file").write_bytes(b"source")
            outside = root / "outside"
            outside.write_bytes(b"synthetic-secret")
            original = snapshot_io.read_at

            def swap(parent, name):
                (source / name).unlink()
                (source / name).symlink_to(outside)
                return original(parent, name)

            errors = []
            with patch.object(snapshot_io, "read_at", side_effect=swap):
                snapshot_io.snapshot(source, root / "evidence", errors, "fixture")
            self.assertTrue(errors)
            self.assertFalse((root / "evidence/file").exists())


if __name__ == "__main__":
    unittest.main()
