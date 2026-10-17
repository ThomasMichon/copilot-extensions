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
from test_validation import WindowsSeedReadFixtures


class CliDiagnosticsTests(WindowsSeedReadFixtures, unittest.TestCase):
    def test_measurement_numbers_are_finite_and_within_prompt_capacity(self):
        valid = {"context_window": {"number": 1, "current_window_id": "fixture:1"},
                 "context_info": {"promptTokenLimit": 200000},
                 "tokens_left": 70000, "fraction_remaining": .35}

        def read(payload):
            return validation.measurement_result(
                {"data": {"success": True, "result": {"content": json.dumps(payload)}}})

        self.assertEqual(read(valid), (valid, None))
        for field in ("promptTokenLimit", "tokens_left", "fraction_remaining"):
            for value in (float("nan"), float("inf"), -float("inf")):
                payload = dict(valid, context_info=dict(valid["context_info"]))
                if field == "promptTokenLimit":
                    payload["context_info"][field] = value
                else:
                    payload[field] = value
                result, error = read(payload)
                self.assertIsNone(result, (field, value))
                self.assertIsNotNone(error, (field, value))
        for value in (200001, 10 ** 1000):
            result, error = read(dict(valid, tokens_left=value))
            self.assertIsNone(result)
            self.assertIsNotNone(error)
        self.assertEqual(read(dict(valid, tokens_left=0, fraction_remaining=0))[1], None)
        self.assertEqual(read(dict(valid, tokens_left=200000, fraction_remaining=1))[1], None)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "workspace"
            root.mkdir()
            task = {key: key for key in ("original_objective", "parent_gate", "current_slice",
                                        "unresolved_user_requests", "decisions", "live_references")}
            files = {"AGENTS.md": "fixture guidance\n", "task.json": json.dumps(task),
                     "audit-contract.md": "fixture contract\n", "ledger.json": "{}",
                     ".context-handoff/config.yaml": "mode: manual-only\n",
                     "corpus/shard-000.txt": "record=0 severity=0\nrecord=1 severity=1\n"}
            for name, content in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            baseline = base / "seed"
            validation.capture_baseline(root, baseline, "fixture-root")
            checkpoint = dict(
                task, workspace=str(root), session_id="fixture-root", transition_number=1,
                trigger_measurements=[valid], recovery_prerequisites=validation.RECOVERY_PREREQUISITES,
                audit_cursor={"path": "corpus/shard-000.txt", "last_line": 1, "last_record": 0,
                              "last_severity": 0, "next_path": "corpus/shard-000.txt", "next_line": 2},
                next_action={"operation": "read", "path": str(root / "corpus/shard-000.txt"),
                             "view_range": [2, 2]})
            self.assertEqual(validation.checkpoint_errors(checkpoint, root, baseline=baseline), [])
            for value in (float("nan"), float("inf")):
                malformed = json.loads(json.dumps(checkpoint))
                malformed["trigger_measurements"][0]["tokens_left"] = value
                self.assertTrue(validation.checkpoint_errors(malformed, root, baseline=baseline))

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
            root = Path(temporary) / "workspace"
            root.mkdir()
            (root / "corpus").mkdir()
            path = root / "corpus/shard-000.txt"
            (root / "corpus/shard-001.txt").write_text("record=1 severity=1\n")
            task = {key: key for key in ("original_objective", "parent_gate", "current_slice",
                                        "unresolved_user_requests", "decisions", "live_references")}
            for name, text in {"AGENTS.md": "fixture guidance\n", "task.json": json.dumps(task),
                               "audit-contract.md": "fixture contract\n", "ledger.json": "{}",
                               ".context-handoff/config.yaml": "mode: manual-only\n"}.items():
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            cursor = {"path": "corpus/shard-000.txt", "last_line": 1, "last_record": 0,
                      "last_severity": 0, "next_path": "corpus/shard-001.txt", "next_line": 1}
            for index, content in enumerate(("record=0\n", "severity=0\n", "no record fields\n",
                                             "record=0 severity=0\n")):
                path.write_text(content)
                baseline = Path(temporary) / f"seed-{index}"
                validation.capture_baseline(root, baseline, "fixture-root")
                expected = ["last_line does not contain record/severity fields"] if index < 3 else []
                self.assertEqual(validation.cursor_errors(cursor, root, baseline), expected)


if __name__ == "__main__":
    unittest.main()
