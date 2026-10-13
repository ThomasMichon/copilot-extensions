import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import snapshot_io


@unittest.skipUnless(os.name == "posix", "descriptor no-follow proof requires the Linux fixture")
class SnapshotTests(unittest.TestCase):
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
