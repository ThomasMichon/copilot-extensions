from contextlib import ExitStack, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import snapshot_io
import controller
import test_validation


@unittest.skipUnless(os.name == "posix", "descriptor no-follow proof requires the Linux fixture")
class SnapshotTests(unittest.TestCase):
    def test_destination_replacement_during_copy_invalidates_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, destination = root / "source", root / "target"
            source.mkdir()
            destination.mkdir()
            (source / "file").write_bytes(b"fresh")
            original = snapshot_io.write_at

            def swap(parent, name, content):
                destination.rename(root / "detached")
                destination.mkdir()
                (destination / "file").write_bytes(b"stale")
                return original(parent, name, content)

            errors = []
            with patch.object(snapshot_io, "write_at", side_effect=swap):
                receipt = snapshot_io.snapshot(source, destination, errors, "fixture")
            self.assertFalse(receipt)
            self.assertTrue(errors)
            with self.assertRaises(OSError):
                snapshot_io.read_snapshot(destination, receipt.bindings, "file")
            self.assertEqual((root / "detached/file").read_bytes(), b"fresh")
            self.assertEqual((destination / "file").read_bytes(), b"stale")

    def test_bound_consumption_refuses_later_directory_and_file_mutation(self):
        for kind in ("directory", "file", "content"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, destination = root / "source", root / "target"
                source.mkdir()
                (source / "file").write_bytes(b"fresh")
                errors = []
                receipt = snapshot_io.snapshot(source, destination, errors, "fixture")
                self.assertFalse(errors)
                self.assertEqual(snapshot_io.read_snapshot(destination, receipt.bindings, "file"), b"fresh")
                if kind == "directory":
                    destination.rename(root / "detached")
                    destination.mkdir()
                    (destination / "file").write_bytes(b"stale")
                elif kind == "file":
                    (destination / "replacement").write_bytes(b"stale")
                    (destination / "replacement").replace(destination / "file")
                else:
                    (destination / "file").write_bytes(b"stale")
                with self.assertRaises(OSError):
                    snapshot_io.read_snapshot(destination, receipt.bindings, "file")

    def test_analysis_cannot_credit_replaced_export_directory(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            original, evaluation, capsule = self.export_fixture(Path(temporary), stack)
            result = controller.export()
            self.assertEqual(result["export_errors"], [])
            session = evaluation / "session"
            stream = (session / "events.jsonl").read_bytes()
            session.rename(evaluation / "detached-session")
            session.mkdir()
            (session / "events.jsonl").write_bytes(stream)
            controller.analyze()
            observed = json.loads((evaluation / "observed-evidence.json").read_text())
            self.assertFalse(observed["root_clear_events"])
            self.assertTrue(observed["evidence_errors"])
            self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"],
                             "INVALID_EXPORT")

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

    def test_existing_receipt_cannot_disable_snapshot_binding(self):
        for kind in ("missing", "empty", "null"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                original, evaluation, capsule = self.export_fixture(Path(temporary), stack)
                self.assertEqual(controller.export()["export_errors"], [])
                receipt_path = evaluation / "export-errors.json"
                receipt = json.loads(receipt_path.read_text())
                if kind == "missing":
                    del receipt["snapshot_bindings"]
                else:
                    receipt["snapshot_bindings"] = {} if kind == "empty" else None
                receipt_path.write_text(json.dumps(receipt))
                session = evaluation / "session"
                stream = (session / "events.jsonl").read_bytes()
                session.rename(evaluation / "detached-session")
                session.mkdir()
                (session / "events.jsonl").write_bytes(stream)
                controller.analyze()
                observed = json.loads((evaluation / "observed-evidence.json").read_text())
                self.assertFalse(observed["root_clear_events"])
                self.assertTrue(observed["evidence_errors"])
                self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"],
                                 "INVALID_EXPORT")

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

    def test_export_retains_packets_when_native_config_command_fails(self):
        for kind in ("nonzero", "missing", "encoding"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
                original, evaluation, capsule = self.export_fixture(Path(temporary), stack)
                canary = "synthetic-" + os.urandom(16).hex()
                error = (subprocess.CalledProcessError(1, ["copilot", "config"], stderr=canary)
                         if kind == "nonzero" else FileNotFoundError(canary) if kind == "missing"
                         else UnicodeDecodeError("utf-8", b"\xff", 0, 1, canary))
                with patch.object(controller, "run", side_effect=error):
                    result = controller.export()
                self.assertEqual(result["event_export_status"], "INVALID_EXPORT")
                self.assertTrue(any(item["source"] == "copilot config" for item in result["export_errors"]))
                self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"],
                                 "INVALID_EXPORT")
                self.assertIsNone(json.loads((evaluation / "effective-settings.json").read_text())["contextManagementTools"])
                self.assertNotIn(canary, (evaluation / "export-errors.json").read_text())

    def test_successful_redaction_invalidates_changed_bound_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            original, evaluation, capsule = self.export_fixture(Path(temporary), stack)
            stream = Path.home() / ".copilot/session-state/root-fixture/events.jsonl"
            rows = [json.loads(line) for line in stream.read_text().splitlines()]
            secret = "synthetic-" + os.urandom(16).hex()
            rows[0]["data"]["content"] += secret
            stream.write_text("".join(json.dumps(row) + "\n" for row in rows))
            self.assertEqual(controller.export()["export_errors"], [])
            with patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": secret}):
                errors = controller.redact()
            self.assertTrue(errors)
            self.assertNotIn(secret.encode(), (evaluation / "session/events.jsonl").read_bytes())
            receipt = json.loads((evaluation / "export-errors.json").read_text())
            self.assertEqual(receipt["status"], "INVALID_EXPORT")
            self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"],
                             "INVALID_EXPORT")
            controller.analyze()
            observed = json.loads((evaluation / "observed-evidence.json").read_text())
            self.assertFalse(observed["root_clear_events"])

    def test_successful_unbound_redaction_retains_captured_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            original, evaluation, capsule = self.export_fixture(Path(temporary), stack)
            self.assertEqual(controller.export()["export_errors"], [])
            secret = "synthetic-" + os.urandom(16).hex()
            unbound = evaluation / "transcript.txt"
            unbound.write_text(secret)
            with patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": secret}):
                self.assertEqual(controller.redact(), [])
            self.assertNotIn(secret.encode(), unbound.read_bytes())
            self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"],
                             "captured")

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


class StartupTests(unittest.TestCase):
    def launch_keys(self, frames):
        position = 0

        def capture(label):
            nonlocal position
            frame = frames[min(position, len(frames) - 1)]
            position += 1
            return frame

        with patch.object(controller, "trusted_identity", return_value=({"session_id": "fixture"}, {})), \
                patch.object(controller, "workspace_seed_errors", return_value=[]), \
                patch.object(Path, "exists", return_value=False), \
                patch.object(controller, "verify_tmux_server", return_value={"server_no_new_privs": True}), \
                patch.object(controller, "write"), patch.object(controller.time, "sleep"), \
                patch.object(controller, "capture", side_effect=capture), \
                patch.object(controller, "tmux") as command, redirect_stdout(io.StringIO()):
            controller.launch()
        return [call.args[-1] for call in command.call_args_list if call.args[0] == "send-keys"]

    def test_startup_history_prompts_are_handled_once(self):
        trust = "Confirm folder trust"
        install = trust + "\nInstall it now?"
        permissions = install + "\nAllow all tools"
        self.assertEqual(self.launch_keys([trust, trust, install, install, permissions, permissions + "\nIdle"]),
                         ["Enter", "n", "Enter"])

    def test_only_latest_prompt_in_full_history_receives_a_response(self):
        self.assertEqual(self.launch_keys(["Confirm folder trust\nInstall it now?\nEnable all permissions\nIdle"]),
                         ["Enter"])

    def test_permission_alias_does_not_repeat_confirmation(self):
        self.assertEqual(self.launch_keys(["Allow all tools", "Allow all tools\nEnable all permissions\nIdle"]),
                         ["Enter"])

    def test_manifest_declares_the_same_exact_two_transition_boundary(self):
        manifest = json.loads(Path(__file__).with_name("manifest.json").read_text())
        self.assertTrue(any("exactly two native new_context transitions" in step
                            for step in manifest["expected_outcome"]["steps"]))
        self.assertIn("transition count other than two", manifest["expected_outcome"]["false_pass_if"])


if __name__ == "__main__":
    unittest.main()
