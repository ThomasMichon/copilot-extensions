from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import controller
import os_permission_smoke
import validation


class CliDiagnosticsTests(unittest.TestCase):
    def test_recovery_event_cli_preserves_invalid_stream_diagnostics(self):
        options = {"capture_output": True, "text": True}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "events.jsonl"
            for contents in (None, "", "{broken\n", "{}\n", "\ufffd\n"):
                with self.subTest(contents=contents):
                    if contents is not None:
                        path.write_text(contents, encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, validation.__file__, "recovery-events", str(path),
                         str(root), str(root / "baseline")], **options)
                    self.assertNotEqual(result.returncode, 0)
                    report = json.loads(result.stdout)
                    self.assertFalse(report["valid"])
                    self.assertTrue(report["observations"][0]["errors"])
                    self.assertNotIn("Traceback", result.stderr)

    def test_execution_exit_codes_require_integers(self):
        for key in ("shellExecution", "toolTelemetry"):
            for code in (0.0, 1.0, True, "0", None):
                metadata = {"exitCode": code} if key == "shellExecution" else {"metrics": {"exit_code": code}}
                status = validation.execution_status({"data": {"success": True, key: metadata}})
                self.assertFalse(status["success"], (key, code))
                self.assertIn("invalid optional", status["error"])
        self.assertTrue(validation.successful({"data": {"success": True, "shellExecution": {"exitCode": 0}}}))
        self.assertFalse(validation.successful({"data": {"success": True, "shellExecution": {"exitCode": 1}}}))

    def test_checkpoint_cli_rejects_invalid_json_and_nonobject_content(self):
        options = {"capture_output": True, "text": True}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        for content in ("{broken", "[]", "null"):
            result = subprocess.run(
                [sys.executable, validation.__file__, "checkpoint", ".", "missing-baseline"],
                input=content, **options)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(json.loads(result.stdout)["valid"])
            self.assertNotIn("Traceback", result.stderr)

    def test_invalid_controller_arguments_fail_before_workspace_io(self):
        options = {"capture_output": True, "text": True}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        for arguments in ([], ["unknown"], ["capture"], ["send"], ["fingerprint"],
                          ["seed", "unexpected"]):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, controller.__file__, *arguments], **options)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("usage:", result.stderr.lower())
                self.assertNotIn("Traceback", result.stderr)

    def test_worker_rejects_unavailable_linux_account_lookup_before_spawn(self):
        with patch.object(os_permission_smoke, "pwd", None), \
                patch.object(os_permission_smoke, "command") as command:
            with self.assertRaisesRegex(RuntimeError, "Linux account lookup"):
                os_permission_smoke.worker(["true"])
            command.assert_not_called()

    def test_malformed_corpus_fields_have_stable_cursor_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "corpus").mkdir()
            path = root / "corpus/shard-000.txt"
            cursor = {"path": "corpus/shard-000.txt", "last_line": 1, "last_record": 0,
                      "last_severity": 0, "next_path": "corpus/shard-001.txt", "next_line": 1}
            with patch.object(validation, "seed_baseline", return_value=({"directory": root}, [])):
                for content in ("record=0\n", "severity=0\n", "no record fields\n"):
                    path.write_text(content)
                    self.assertEqual(validation.cursor_errors(cursor, root),
                                     ["last_line does not contain record/severity fields"])
                path.write_text("record=0 severity=0\n")
                self.assertEqual(validation.cursor_errors(cursor, root), [])


if __name__ == "__main__":
    unittest.main()
