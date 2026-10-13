import copy
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
import shlex
import stat
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import controller
import os_permission_smoke
import validation
import inspect
import subprocess
import sys

from validation import (
    RECOVERY_PREREQUISITES, action_errors,
    content_read, pressure_events, successful,
)

TEST_SEEDS = {}


def cursor_errors(cursor, root):
    return validation.cursor_errors(cursor, root, baseline=TEST_SEEDS[str(root)])


def checkpoint_errors(document, root):
    return validation.checkpoint_errors(document, root, baseline=TEST_SEEDS[str(root)])


def witness_errors(document, root):
    return validation.witness_errors(document, root, baseline=TEST_SEEDS[str(root)])


def recovery_event_errors(events, root):
    return validation.recovery_event_errors(events, root, baseline=TEST_SEEDS[str(root)])


class WindowsSeedReadFixtures:
    """Portable logic fixtures only; Linux tests retain the real descriptor reader."""

    def setUp(self):
        super().setUp()
        if os.name != "nt":
            return
        verify = validation.workspace_seed_errors

        def fixture_verification(workspace, seed):
            allowed = {Path(workspace) / name for name in seed["manifest"]["files_sha256"]}

            def read_fixture(path):
                path = Path(path)
                if path not in allowed:
                    raise AssertionError("fixture reader escaped manifest-bound workspace inputs")
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or path.is_symlink():
                    raise OSError("fixture input is not a singly-linked regular file")
                return path.read_bytes()

            # Deliberately not an OS-boundary proof or a production Windows fallback.
            with patch.object(validation.snapshot_io, "read_bytes", side_effect=read_fixture):
                return verify(workspace, seed)

        self.enterContext(patch.object(validation, "workspace_seed_errors", side_effect=fixture_verification))
        self.enterContext(patch.object(controller, "workspace_seed_errors", side_effect=fixture_verification))


class ValidationTests(WindowsSeedReadFixtures, unittest.TestCase):
    def test_workspace_fixture_reader_retains_changed_and_missing_input_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            evaluation, state, events = self.analyzer_fixture(Path(temporary))
            root = evaluation / "workspace"
            seed, errors = validation.seed_baseline(TEST_SEEDS[str(root)])
            self.assertFalse(errors)
            self.assertEqual(validation.workspace_seed_errors(root, seed), [])
            path = root / "task.json"
            path.write_text('{"fixture":"changed"}')
            self.assertEqual(validation.workspace_seed_errors(root, seed), ["driven seed input changed: task.json"])
            path.unlink()
            self.assertEqual(validation.workspace_seed_errors(root, seed), ["driven seed input unavailable: task.json"])

    def test_shell_command_schema_rejects_malformed_rows_before_consumers(self):
        with tempfile.TemporaryDirectory() as temporary:
            evaluation, state, original = self.analyzer_fixture(Path(temporary))
            for tool in ("bash", "shell"):
                for command in (None, [], 7, {}, "missing"):
                    with self.subTest(tool=tool, command=command):
                        arguments = {} if command == "missing" else {"command": command}
                        row = {"type": "tool.execution_start", "id": "malformed-shell", "timestamp": "2-0",
                               "data": {"toolCallId": "malformed-shell", "toolName": tool,
                                        "arguments": arguments}}
                        events = original + [row]
                        path = evaluation / "session/events.jsonl"
                        path.write_text("".join(json.dumps(event) + "\n" for event in events))
                        parsed, errors = validation.event_stream(path)
                        self.assertTrue(errors)
                        self.assertNotIn(row, parsed)
                        report = self.run_analyzer(evaluation, state, events)
                        self.assertTrue(report["event_parser_errors"])
                        self.assertEqual(report["root_clear_events"], [])
                        self.assertTrue((evaluation / "judge-packet.json").is_file())
                        options = {"capture_output": True, "text": True, "input": "", "timeout": 10}
                        if sys.platform == "win32":
                            options["creationflags"] = subprocess.CREATE_NO_WINDOW
                        result = subprocess.run(
                            [sys.executable, validation.__file__, "recovery-events", str(path),
                             str(evaluation / "workspace"), str(TEST_SEEDS[str(evaluation / "workspace")])],
                            **options)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(json.loads(result.stdout)["valid"])
                        self.assertNotIn("Traceback", result.stderr)
            for command in ("", "cat task.json"):
                row["data"]["arguments"] = {"command": command}
                path.write_text(json.dumps(row) + "\n")
                self.assertEqual(validation.event_stream(path), ([row], []))
            row["data"].update(toolName="unknown-fixture", arguments={})
            path.write_text(json.dumps(row) + "\n")
            self.assertEqual(validation.event_stream(path), ([row], []))

    @unittest.skipUnless(os.name == "posix", "nonblocking seed consumer proof requires Linux")
    def test_nonregular_workspace_seed_preserves_analyzer_packet_and_cli_failure(self):
        for kind in ("fifo", "directory", "symlink", "hardlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                original, state, events = self.analyzer_fixture(base / "original")
                events[1:1] = [
                    {"type": "tool.execution_start", "id": "measurement", "timestamp": "0-0a",
                     "data": {"toolCallId": "measurement", "toolName": "get_context_remaining", "arguments": {}}},
                    {"type": "tool.execution_complete", "id": "measurement-complete", "timestamp": "0-0b",
                     "data": {"toolCallId": "measurement", "success": True,
                              "result": {"content": json.dumps(self.full_measurement(1))}}},
                ]
                baseline = TEST_SEEDS[str(original / "workspace")]
                baseline.chmod(0o755)
                (baseline / "venue-seal.json").write_text(json.dumps({
                    "session_id": "root-fixture", "operator_sudo_denied": True,
                    "sudo_elevation_bits_removed": True,
                }))
                home = base / "home"
                session = home / ".copilot/session-state/root-fixture"
                session.mkdir(parents=True)
                (session / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
                evaluation = base / "export/eval"
                evaluation.mkdir(parents=True)
                with patch.object(controller, "BASELINE", baseline), patch.object(controller, "EVAL", evaluation), \
                        patch.object(controller, "OUT", evaluation.parent), patch.object(controller, "STATE", state), \
                        patch.object(controller, "REPO", original / "workspace"), \
                        patch.object(controller, "SCENARIO", Path(__file__).parent), \
                        patch.object(Path, "home", return_value=home), patch.object(controller, "run", return_value="true"), \
                        patch.object(controller.subprocess, "run", return_value=type("Result", (), {"returncode": 1})()), \
                        redirect_stdout(io.StringIO()):
                    controller.export()
                    controller.analyze()
                positive = json.loads((evaluation / "observed-evidence.json").read_text())
                self.assertFalse(positive["checkpoint_contract_observations"][0]["errors"])
                root = evaluation / "workspace"
                path = root / "task.json"
                path.unlink()
                if kind == "fifo":
                    os.mkfifo(path)
                elif kind == "directory":
                    path.mkdir()
                elif kind == "symlink":
                    path.symlink_to(baseline / "task.json")
                else:
                    os.link(baseline / "task.json", path)
                seed, errors = validation.seed_baseline(baseline)
                self.assertFalse(errors)
                # Bound the actual exported-workspace consumer, not just a mocked file read.
                code = ("import controller,sys; from pathlib import Path; "
                        "controller.EVAL=Path(sys.argv[1]); controller.STATE=Path(sys.argv[2]); "
                        "controller.BASELINE=Path(sys.argv[3]); controller.analyze()")
                result = subprocess.run([sys.executable, "-c", code, str(evaluation), str(state), str(baseline)],
                                        capture_output=True, text=True, input="", timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads((evaluation / "observed-evidence.json").read_text())
                self.assertTrue(any("driven seed input unavailable: task.json" in row["errors"]
                                    for row in report["semantic_recovery_validation"]))
                self.assertTrue(report["checkpoint_contract_observations"][0]["errors"])
                self.assertTrue((evaluation / "judge-packet.json").is_file())
                result = subprocess.run([sys.executable, validation.__file__, "verify-baseline",
                                         str(root), str(baseline)], capture_output=True, text=True,
                                        input="", timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(json.loads(result.stdout)["valid"])
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn("driven seed input unavailable: task.json", validation.workspace_seed_errors(root, seed))

    @unittest.skipUnless(os.name == "posix", "looped-path consumer proof requires the Linux fixture")
    def test_unresolvable_recursive_paths_preserve_cli_and_analyzer_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            evaluation, state, events = self.analyzer_fixture(Path(temporary))
            root = evaluation / "workspace"
            (root / "loop").symlink_to("loop")
            position = next(index for index, event in enumerate(events) if event["type"] == "session.context_cleared")
            events[position + 1:position + 1] = [
                {"type": "tool.execution_start", "id": "loop-reader", "timestamp": "1-0a",
                 "data": {"toolCallId": "loop-reader", "toolName": "bash",
                          "arguments": {"command": f"rg record {shlex.quote(str(root / 'loop'))}"}}},
                {"type": "tool.execution_complete", "id": "loop-complete", "timestamp": "1-0b",
                 "data": {"toolCallId": "loop-reader", "success": True, "result": {"content": "fixture output"}}},
            ]
            report = self.run_analyzer(evaluation, state, events)
            self.assertTrue(any("unresolvable" in error for row in report["semantic_recovery_validation"]
                                for error in row["errors"]))
            self.assertTrue((evaluation / "judge-packet.json").is_file())
            options = {"capture_output": True, "text": True}
            if sys.platform == "win32":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                [sys.executable, validation.__file__, "recovery-events", str(evaluation / "session/events.jsonl"),
                 str(root), str(TEST_SEEDS[str(root)])], **options)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(json.loads(result.stdout)["valid"])
            self.assertNotIn("Traceback", result.stderr)
            original = Path.resolve

            def fail_operand(candidate, *args, **kwargs):
                if candidate.name == "loop":
                    raise OSError("fixture unavailable")
                return original(candidate, *args, **kwargs)

            with patch.object(Path, "resolve", side_effect=fail_operand, autospec=True):
                paths, errors = validation.resolved_read_paths(events[position + 1], str(root))
            self.assertTrue(paths)
            self.assertTrue(any("unresolvable" in error for error in errors))

    def test_call_relationship_errors_quarantine_analysis_and_recovery_cli(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            variants = {}
            start_duplicate = copy.deepcopy(events)
            extra = copy.deepcopy(start_duplicate[1])
            extra["id"] = "duplicate-start-id"
            start_duplicate.insert(2, extra)
            variants["duplicate-start"] = start_duplicate
            complete_duplicate = copy.deepcopy(events)
            extra = copy.deepcopy(complete_duplicate[2])
            extra["id"] = "duplicate-complete-id"
            complete_duplicate.insert(3, extra)
            variants["duplicate-complete"] = complete_duplicate
            variants["completion-before-start"] = [events[0], events[2], events[1], *events[3:]]
            reversed_time = copy.deepcopy(events)
            reversed_time[2]["timestamp"] = "0-0"
            variants["completion-time-before-start"] = reversed_time
            options = {"capture_output": True, "text": True}
            if sys.platform == "win32":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            for name, bad in variants.items():
                report = self.run_analyzer(evaluation, state, bad)
                self.assertTrue(report["event_parser_errors"], name)
                self.assertEqual(report["root_clear_events"], [], name)
                self.assertEqual(report["context_measurements"], [], name)
                result = subprocess.run([sys.executable, validation.__file__, "recovery-events",
                                         str(evaluation / "session/events.jsonl"), str(evaluation / "workspace"),
                                         str(TEST_SEEDS[str(evaluation / "workspace")])], **options)
                self.assertNotEqual(result.returncode, 0, name)
                self.assertFalse(json.loads(result.stdout)["valid"], name)

    def test_recursive_workspace_reader_is_early_substantive_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for command in ("rg record .", "grep -r record .", f"rg record '{root}'",
                            f"grep -R record '{root.parent}'", "rg record", "grep -r record"):
                events = self.semantic_events(root)
                self.insert_before_guidance(events, [
                    {"type": "tool.execution_start", "id": "recursive-reader", "timestamp": "1-0a",
                     "data": {"toolCallId": "recursive-reader", "toolName": "bash", "arguments": {"command": command}}},
                    {"type": "tool.execution_complete", "id": "recursive-reader-complete", "timestamp": "1-0b",
                     "data": {"toolCallId": "recursive-reader", "success": True, "result": {"content": "reader output"}}},
                ])
                observed = recovery_event_errors(events, root)[0]
                self.assertTrue(observed["errors"], command)
                self.assertEqual(observed["first_substantive_work"], "recursive-reader", command)

    def test_call_ids_are_scoped_and_unfinished_starts_remain_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            rows = [
                {"type": "tool.execution_start", "id": "root-start", "timestamp": "0",
                 "data": {"toolCallId": "shared", "toolName": "view", "arguments": {}}},
                {"type": "tool.execution_start", "id": "child-start", "timestamp": "0", "agentId": "child-fixture",
                 "data": {"toolCallId": "shared", "toolName": "view", "arguments": {}}},
                {"type": "tool.execution_complete", "id": "root-complete", "timestamp": "0",
                 "data": {"toolCallId": "shared", "success": True, "result": {"content": "root"}}},
                {"type": "tool.execution_complete", "id": "child-complete", "timestamp": "1",
                 "data": {"toolCallId": "shared", "agentId": "child-fixture", "success": False}},
                {"type": "tool.execution_start", "id": "unfinished", "timestamp": "2",
                 "data": {"toolCallId": "still-running", "toolName": "view", "arguments": {}}},
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            parsed, errors = validation.event_stream(path)
            self.assertFalse(errors)
            self.assertEqual(parsed, rows)
            duplicate = copy.deepcopy(rows[1])
            duplicate["id"] = "another-child-start"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows + [duplicate]))
            self.assertTrue(validation.event_stream(path)[1])
            unmatched = copy.deepcopy(rows[2])
            unmatched["id"] = "unmatched-other-child"
            unmatched["agentId"] = "other-child"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows + [unmatched]))
            self.assertTrue(validation.event_stream(path)[1])

    def test_recursive_reader_unrelated_files_and_pattern_literals_are_not_corpus_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for command in ("rg record task.json", "grep -r record task.json",
                            f"rg record '{root / 'elsewhere'}'",
                            "grep -r 'corpus/shard-003.txt' task.json",
                            "rg --regexp='corpus/shard-003.txt' task.json"):
                events = self.semantic_events(root)
                self.insert_before_guidance(events, [
                    {"type": "tool.execution_start", "id": "unrelated-reader", "timestamp": "1-0a",
                     "data": {"toolCallId": "unrelated-reader", "toolName": "bash", "arguments": {"command": command}}},
                    {"type": "tool.execution_complete", "id": "unrelated-reader-complete", "timestamp": "1-0b",
                     "data": {"toolCallId": "unrelated-reader", "success": True, "result": {"content": "prerequisite excerpt"}}},
                ])
                observed = recovery_event_errors(events, root)[0]
                self.assertFalse(observed["errors"], command)
                self.assertEqual(observed["first_substantive_work"], "1-4", command)
                reader = next(event for event in events if event.get("id") == "unrelated-reader")
                completion = next(event for event in events if event.get("id") == "unrelated-reader-complete")
                self.assertFalse(content_read(reader, completion, str(root / "corpus/shard-003.txt"),
                                              "prerequisite excerpt", str(root), [61, 61]))

    def semantic_bound_events(self, root):
        return self.semantic_events(root)

    def with_semantic_bindings(self, events):
        result = []
        for window in (1, 2):
            clear = next(event for event in events if event.get("id") == f"clear-{window}")
            recovered = next(event for event in events if event.get("id") == f"complete-{window}-0")
            document = json.loads(json.loads(recovered["data"]["result"]["content"])["content"])
            document["session_id"] = "root-fixture"
            content = json.dumps(document)
            binding = {"agent_session_id": "root-fixture", "memory_scope_id": "scope-fixture",
                       "revision": window, "source_window": window - 1}
            result.extend([
                {"type": "tool.execution_start", "id": f"prewrite-{window}", "timestamp": f"{window}--a",
                 "data": {"toolCallId": f"prewrite-{window}", "toolName": "session_artifacts",
                          "arguments": {"operation": "write", "path": "checkpoint.md", "content": content}}},
                {"type": "tool.execution_complete", "id": f"prewrite-complete-{window}", "timestamp": f"{window}--b",
                 "data": {"toolCallId": f"prewrite-{window}", "success": True,
                          "result": {"content": json.dumps(dict(binding, operation="create" if window == 1 else "update"))}}},
                {"type": "tool.execution_start", "id": f"preread-{window}", "timestamp": f"{window}--c",
                 "data": {"toolCallId": f"preread-{window}", "toolName": "session_artifacts",
                          "arguments": {"operation": "read", "path": "checkpoint.md"}}},
                {"type": "tool.execution_complete", "id": f"preread-complete-{window}", "timestamp": f"{window}--d",
                 "data": {"toolCallId": f"preread-{window}", "success": True, "result": {
                     "content": json.dumps(dict(binding, history_position=f"prewrite-{window}", content=content))}}},
            ])
            clear["timestamp"] = f"{window}--e"
            recovered["data"]["result"]["content"] = json.dumps(dict(
                binding, history_position=f"prewrite-{window}", content=content))
            begin = events.index(clear)
            end = next((i for i in range(begin + 1, len(events))
                        if events[i]["type"] == "session.context_cleared"), len(events))
            result.extend(events[begin:end])
        return result

    def insert_before_guidance(self, events, additions):
        position = next(i for i, event in enumerate(events) if event.get("id") == "complete-1-0") + 1
        events[position:position] = additions

    def test_recovered_checkpoint_requires_authoritative_preclear_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = self.semantic_bound_events(root)
            self.assertTrue(all(not row["errors"] for row in recovery_event_errors(events, root)))
            mutations = [("agent_session_id", "other-owner"), ("memory_scope_id", "other-scope"),
                         ("revision", 7), ("source_window", 7), ("revision", None),
                         ("revision", True), ("source_window", 1.0), ("memory_scope_id", None)]
            for key, value in mutations:
                bad = copy.deepcopy(events)
                completion = next(event for event in bad if event.get("id") == "complete-1-0")
                payload = json.loads(completion["data"]["result"]["content"])
                payload[key] = value
                completion["data"]["result"]["content"] = json.dumps(payload)
                self.assertTrue(recovery_event_errors(bad, root)[0]["errors"], (key, value))
            for missing in ("agent_session_id", "memory_scope_id", "revision", "source_window", "history_position"):
                bad = copy.deepcopy(events)
                completion = next(event for event in bad if event.get("id") == "complete-1-0")
                payload = json.loads(completion["data"]["result"]["content"])
                del payload[missing]
                completion["data"]["result"]["content"] = json.dumps(payload)
                self.assertTrue(recovery_event_errors(bad, root)[0]["errors"], missing)

    def test_recovered_checkpoint_child_or_arbitrary_content_is_uncredited(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = self.semantic_bound_events(root)
            for form in ("child", "denial", "wrong-document", "unverified-preclear"):
                bad = copy.deepcopy(events)
                completion = next(event for event in bad if event.get("id") == "complete-1-0")
                if form == "child":
                    completion["agentId"] = "child-fixture"
                elif form == "denial":
                    completion["data"]["result"]["content"] = "Arbitrary read denial"
                elif form == "wrong-document":
                    # Correlated bytes/metadata still cannot adopt another
                    # session's document as the trusted root checkpoint.
                    for event in bad:
                        if event.get("id") == "prewrite-1":
                            document = json.loads(event["data"]["arguments"]["content"])
                            document["session_id"] = "other-session"
                            event["data"]["arguments"]["content"] = json.dumps(document)
                        elif event.get("id") in ("preread-complete-1", "complete-1-0"):
                            payload = json.loads(event["data"]["result"]["content"])
                            document = json.loads(payload["content"])
                            document["session_id"] = "other-session"
                            payload["content"] = json.dumps(document)
                            event["data"]["result"]["content"] = json.dumps(payload)
                else:
                    next(event for event in bad if event.get("id") == "preread-complete-1")["data"]["success"] = False
                report = recovery_event_errors(bad, root)[0]
                self.assertTrue(report["errors"], form)
                if form == "child":
                    self.assertIn("tool completion lacks a prior same-owner start", report["errors"])
                else:
                    self.assertTrue(any("checkpoint" in error or "session" in error or "read" in error
                                        for error in report["errors"]), form)

    def test_native_measurement_requires_exact_evidenced_ratio(self):
        def read(left, limit, fraction):
            payload = self.full_measurement(1)
            payload.update(tokens_left=left, fraction_remaining=fraction)
            payload["context_info"]["promptTokenLimit"] = limit
            return validation.measurement_result({"data": {"success": True,
                                                          "result": {"content": json.dumps(payload)}}})
        # Captured CLI 1.0.92 records serialize the exact binary ratio, including
        # these finite decimal forms; no extra decimal truncation is evidenced.
        for left, limit, fraction in ((0, 200000, 0), (200000, 200000, 1),
                                     (173839, 200000, .869195), (79576, 200000, .39788),
                                     (1, 3, 1 / 3)):
            self.assertIsNone(read(left, limit, fraction)[1])
        for left, limit, fraction in ((0, 200000, .35), (70000, 200000, .34),
                                     (1, 3, round(1 / 3, 6)), (1, 3, .3333333)):
            self.assertIsNotNone(read(left, limit, fraction)[1])

    def test_parser_corruption_overrides_earlier_captured_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            evaluation, state, events = self.analyzer_fixture(Path(temporary))
            (evaluation / "export-errors.json").write_text(json.dumps({"status": "captured", "errors": []}))
            events.append({"type": "invalid-row"})
            report = self.run_analyzer(evaluation, state, events)
            self.assertFalse(report["root_clear_events"])
            self.assertTrue(report["event_parser_errors"])
            self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"],
                             "INVALID_EXPORT")

    @unittest.skipUnless(os.name == "posix", "no-follow export proof requires the Linux fixture")
    def test_export_quarantines_links_at_actual_consumer_boundaries(self):
        for kind in ("session", "destination", "logs", "workspace", "config", "missing-events"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                original, state, events = self.analyzer_fixture(base / "original")
                capsule = TEST_SEEDS[str(original / "workspace")]
                capsule.chmod(0o755)
                (capsule / "venue-seal.json").write_text(json.dumps({
                    "session_id": "root-fixture", "operator_sudo_denied": True,
                    "sudo_elevation_bits_removed": True,
                }))
                home = base / "home"
                session = home / ".copilot/session-state/root-fixture"
                session.mkdir(parents=True)
                (session / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
                evaluation = base / "export/eval"
                evaluation.mkdir(parents=True)
                outside = base / "outside"
                canary = b"synthetic-" + os.urandom(16).hex().encode("ascii")
                outside.write_bytes(canary)
                locations = {"session": session / "linked.txt",
                             "destination": evaluation / "session/unmatched.txt",
                             "logs": home / ".copilot/logs/linked.txt",
                             "workspace": original / "workspace/linked.txt",
                             "config": original / "workspace/.context-handoff/config.yaml"}
                if kind == "missing-events":
                    (session / "events.jsonl").unlink()
                    (evaluation / "session").mkdir()
                    stale = evaluation / "session/events.jsonl"
                    stale.write_text("".join(json.dumps(event) + "\n" for event in events))
                    previous = stale.read_bytes()
                else:
                    link = locations[kind]
                    link.parent.mkdir(parents=True, exist_ok=True)
                    if link.exists():
                        link.unlink()
                    link.symlink_to(outside)
                with patch.object(controller, "BASELINE", capsule), patch.object(controller, "EVAL", evaluation), \
                        patch.object(controller, "STATE", state), \
                        patch.object(controller, "OUT", evaluation.parent), \
                        patch.object(controller, "REPO", original / "workspace"), \
                        patch.object(controller, "SCENARIO", Path(__file__).parent), \
                        patch.object(Path, "home", return_value=home), patch.object(controller, "run", return_value="true"), \
                        patch.object(controller.subprocess, "run", return_value=type("Result", (), {"returncode": 1})()), \
                        redirect_stdout(io.StringIO()):
                    result = controller.export()
                    if kind == "missing-events":
                        controller.analyze()
                        observed = json.loads((evaluation / "observed-evidence.json").read_text())
                        self.assertEqual(observed["root_clear_events"], [])
                        self.assertEqual(stale.read_bytes(), previous)
                self.assertTrue(result["export_errors"])
                self.assertEqual(result["event_export_status"], "INVALID_EXPORT")
                self.assertEqual(outside.read_bytes(), canary)
                for path in evaluation.rglob("*"):
                    if path.is_file() and not path.is_symlink():
                        self.assertNotIn(canary, path.read_bytes())

    @unittest.skipUnless(os.name == "posix", "no-follow redaction proof requires the Linux fixture")
    def test_redaction_consumer_invalidates_packet_without_touching_link_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evaluation = root / "out/eval"
            evaluation.mkdir(parents=True)
            outside = root / "outside"
            outside.write_bytes(b"synthetic-secret")
            (evaluation / "link").symlink_to(outside)
            with patch.object(controller, "OUT", evaluation.parent), patch.object(controller, "EVAL", evaluation), \
                    patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "synthetic-secret"}), redirect_stdout(io.StringIO()):
                self.assertTrue(controller.redact())
            self.assertEqual(outside.read_bytes(), b"synthetic-secret")
            self.assertEqual(json.loads((evaluation / "credential-redaction.json").read_text())["status"],
                             "INVALID_EXPORT")
            self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"],
                             "INVALID_EXPORT")

    def test_reanalysis_does_not_clear_invalid_redaction_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            evaluation, state, events = self.analyzer_fixture(Path(temporary))
            receipt = evaluation / "credential-redaction.json"
            for document in ({"status": "INVALID_EXPORT", "errors": [{"error": "unsafe entry"}]},
                             {"status": "checked", "errors": "invalid-shape"}, []):
                receipt.write_text(json.dumps(document))
                self.run_analyzer(evaluation, state, events, captured=True)
                self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"],
                                 "INVALID_EXPORT")
            receipt.write_text(json.dumps({"status": "checked", "errors": []}))
            self.run_analyzer(evaluation, state, events, captured=True)
            self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"], "captured")

    def test_only_absent_or_null_ownership_markers_are_root(self):
        for marker in ("", 0, False, [], {}):
            self.assertFalse(validation.root_event({"agentId": marker, "data": {}}))
            self.assertFalse(validation.root_event({"data": {"agentId": marker}}))
        self.assertTrue(validation.root_event({"data": {}}))
        self.assertTrue(validation.root_event({"agentId": None, "data": {"agentId": None}}))
        with tempfile.TemporaryDirectory() as temporary:
            evaluation, state, original = self.analyzer_fixture(Path(temporary))
            for marker in ("", 0, False, [], {}):
                for nested in (False, True):
                    events = copy.deepcopy(original)
                    for event in events:
                        (event["data"] if nested else event)["agentId"] = marker
                    report = self.run_analyzer(evaluation, state, events)
                    self.assertEqual(report["root_clear_events"], [])
                    self.assertEqual(report["context_measurements"], [])
                    self.assertEqual(report["native_artifact_and_terminal_calls"], [])
                    self.assertTrue(report["non_root_activity"])

    def test_absolute_path_corpus_readers_are_not_hidden(self):
        for command in ("cat {path}", "head -n 1 {path}", "tail -n 1 {path}",
                        "sed -n '61,80p' {path}", "grep record {path}", "rg record {path}",
                        "wc -l {path}", "stat {path}", "ls {path}"):
            self.reader_option_observation("/usr/bin/" + command, True)

    def test_file_valued_filters_are_reads_without_becoming_search_patterns(self):
        for reader, option in (("rg", "--ignore-file"), ("grep", "--exclude-from")):
            self.reader_option_observation(f"{reader} {option} {{path}} record task.json", True)
            self.reader_option_observation(f"{reader} {option}={{path}} record task.json", True)
            self.reader_option_observation(
                f"{reader} {option} filters.ignore 'corpus/shard-003.txt' task.json", False)
            self.assertEqual(validation.potential_read_operands(
                reader, [option, "filters.ignore", "corpus/shard-003.txt", "task.json"]),
                ["filters.ignore", "task.json"])

    def test_reader_preprocessing_is_substantive_without_mistaking_patterns(self):
        self.reader_option_observation("rg --pre 'touch witness.json' record task.json", True)
        self.reader_option_observation("rg --pre='touch witness.json' record task.json", True)
        self.reader_option_observation("rg --regexp '--pre' task.json", False)
        self.reader_option_observation("rg -- --pre task.json", False)
        self.reader_option_observation("rg --pre ignored --no-pre record task.json", False)

    def reader_option_observation(self, template, should_be_work):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = self.semantic_events(root)
            command = template.format(path=shlex.quote(str(root / "corpus/shard-003.txt")))
            self.insert_before_guidance(events, [
                {"type": "tool.execution_start", "id": "option-reader", "timestamp": "1-0a",
                 "data": {"toolCallId": "option-reader", "toolName": "bash", "arguments": {"command": command}}},
                {"type": "tool.execution_complete", "id": "option-reader-complete", "timestamp": "1-0b",
                 "data": {"toolCallId": "option-reader", "success": True, "result": {"content": "reader output"}}},
            ])
            observed = recovery_event_errors(events, root)[0]
            self.assertEqual(observed["first_substantive_work"], "option-reader" if should_be_work else "1-4", command)
            self.assertEqual(bool(observed["errors"]), should_be_work, command)

    def test_grep_cluster_attached_pattern_keeps_corpus_operand(self):
        self.reader_option_observation("grep -nerecord {path}", True)

    def test_grep_cluster_separate_pattern_file_is_corpus_work(self):
        self.reader_option_observation("grep -nf {path} task.json", True)

    def test_grep_cluster_attached_pattern_file_is_corpus_work(self):
        self.reader_option_observation("grep -nf{path} task.json", True)

    def test_rg_long_value_does_not_turn_pattern_into_file(self):
        self.reader_option_observation("rg --color never 'corpus/shard-003.txt' task.json", False)

    def test_reader_end_of_options_and_explicit_quoted_patterns(self):
        self.reader_option_observation("grep -ne 'corpus/shard-003.txt' -- task.json", False)
        self.reader_option_observation("rg --color=never --regexp='corpus/shard-003.txt' -- task.json", False)
        self.reader_option_observation("grep --regexp=record -- {path}", True)
        self.reader_option_observation("rg --file={path} -- task.json", True)
        self.reader_option_observation("grep -nA 3 record {path}", True)
        self.reader_option_observation("rg --context=2 --color never --regexp record {path}", True)
        self.reader_option_observation("grep --color never {path} task.json", True)

    def final_seed_checkpoint(self, root):
        (root / "corpus").mkdir(parents=True)
        (root / "corpus/shard-079.txt").write_text("".join(
            f"record={number:05d} severity={number % 4}\n" for number in range(80)))
        baseline = self.provisional_baseline(root)
        task = json.loads((root / "task.json").read_text())
        document = dict(task, workspace=str(root), session_id="root-fixture", transition_number=1,
                        trigger_measurements=[self.full_measurement(1)],
                        recovery_prerequisites=RECOVERY_PREREQUISITES,
                        audit_cursor={"path": "corpus/shard-079.txt", "last_line": 80, "last_record": 79,
                                      "last_severity": 3, "next_path": "corpus/shard-080.txt", "next_line": 1},
                        next_action={"operation": "read", "path": str(root / "corpus/shard-080.txt"),
                                     "view_range": [1, 1]})
        return baseline, document

    def test_final_seed_eof_cannot_approve_nonexistent_successor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline, document = self.final_seed_checkpoint(root)
            self.assertTrue(validation.checkpoint_errors(document, root, baseline=baseline))

    def test_live_checkpoint_cli_rejects_final_seed_eof_before_clear(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline, document = self.final_seed_checkpoint(root)
            options = {"input": json.dumps(document), "capture_output": True, "text": True}
            if sys.platform == "win32":
                options["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run([sys.executable, validation.__file__, "checkpoint", str(root), str(baseline)], **options)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(json.loads(result.stdout)["valid"])
            self.assertNotIn("Traceback", result.stderr)

    def test_opaque_corpus_readers_cannot_skip_refresh_before_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "corpus/shard-003.txt"
            commands = [f"grep record '{path}'", f"rg record '{path}'", f"wc -l '{path}'",
                        f"stat '{path}'", f"ls '{root / 'corpus'}'"]
            for command in commands:
                events = self.semantic_events(root)
                self.insert_before_guidance(events, [
                    {"type": "tool.execution_start", "id": "opaque-reader", "timestamp": "1-0a",
                     "data": {"toolCallId": "opaque-reader", "toolName": "bash", "arguments": {"command": command}}},
                    {"type": "tool.execution_complete", "id": "opaque-reader-complete", "timestamp": "1-0b",
                     "data": {"toolCallId": "opaque-reader", "success": True, "result": {"content": "opaque output"}}},
                ])
                observed = recovery_event_errors(events, root)[0]
                self.assertTrue(observed["errors"], command)
                self.assertEqual(observed["first_substantive_work"], "opaque-reader", command)

    def test_seed_successors_are_readable_within_and_across_bound_shards(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "corpus").mkdir()
            (root / "corpus/shard-000.txt").write_text("record=00000 severity=0\nrecord=00001 severity=1\n")
            (root / "corpus/shard-001.txt").write_text("record=00002 severity=2\n")
            baseline = self.provisional_baseline(root)
            cursor = {"path": "corpus/shard-000.txt", "last_line": 1, "last_record": 0,
                      "last_severity": 0, "next_path": "corpus/shard-000.txt", "next_line": 2}
            self.assertEqual(validation.cursor_errors(cursor, root, baseline), [])
            boundary = dict(cursor, last_line=2, last_record=1, last_severity=1,
                            next_path="corpus/shard-001.txt", next_line=1)
            self.assertEqual(validation.cursor_errors(boundary, root, baseline), [])
            action = {"operation": "read", "path": str(root / boundary["next_path"]), "view_range": [1, 1]}
            self.assertEqual(validation.action_errors(action, boundary, 1, str(root), baseline), [])
            self.assertTrue(validation.action_errors(dict(action, view_range=[1, 80]), boundary, 1, str(root), baseline))
            seed, errors = validation.seed_baseline(baseline)
            self.assertFalse(errors)
            malformed_binding = dict(seed, manifest=dict(seed["manifest"], files_sha256={
                name: digest for name, digest in seed["manifest"]["files_sha256"].items()
                if name != "corpus/shard-001.txt"}))
            self.assertTrue(validation.bound_seed_corpus(malformed_binding, "corpus/shard-001.txt")[1])
            self.assertTrue(validation.bound_seed_corpus(seed, "corpus/../task.json")[1])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "corpus").mkdir()
            (root / "corpus/shard-000.txt").write_text("record=00000 severity=0\n")
            (root / "corpus/shard-001.txt").write_text("")
            baseline = self.provisional_baseline(root)
            boundary = {"path": "corpus/shard-000.txt", "last_line": 1, "last_record": 0,
                        "last_severity": 0, "next_path": "corpus/shard-001.txt", "next_line": 1}
            self.assertTrue(validation.cursor_errors(boundary, root, baseline))

    def test_quoted_corpus_pattern_in_contract_read_is_not_task_file_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for command in ("grep 'corpus/shard-003.txt' audit-contract.md",
                            "rg -n -e 'corpus/shard-003.txt' audit-contract.md",
                            "rg --glob '*.md' 'corpus/' audit-contract.md"):
                events = self.semantic_events(root)
                self.insert_before_guidance(events, [
                    {"type": "tool.execution_start", "id": "prerequisite-reader", "timestamp": "1-0a",
                     "data": {"toolCallId": "prerequisite-reader", "toolName": "bash", "arguments": {"command": command}}},
                    {"type": "tool.execution_complete", "id": "prerequisite-reader-complete", "timestamp": "1-0b",
                     "data": {"toolCallId": "prerequisite-reader", "success": True, "result": {"content": "contract excerpt"}}},
                ])
                observed = recovery_event_errors(events, root)[0]
                self.assertFalse(observed["errors"], command)
                self.assertEqual(observed["first_substantive_work"], "1-4", command)
                path = str(root / "corpus/shard-003.txt")
                fake = {"data": {"success": True, "result": {"content": "record=60\n"}}}
                reader = next(event for event in events if event.get("id") == "prerequisite-reader")
                self.assertFalse(content_read(reader, fake, path, "record=60\n", str(root), [61, 61]))

    def test_direct_analyze_parser_error_quarantines_all_supporting_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            measurement = self.full_measurement(1)
            events[1:1] = [
                {"type": "tool.execution_start", "id": "measurement", "timestamp": "0-0a",
                 "data": {"toolCallId": "measurement", "toolName": "get_context_remaining", "arguments": {}}},
                {"type": "tool.execution_complete", "id": "measurement-complete", "timestamp": "0-0b",
                 "data": {"toolCallId": "measurement", "success": True, "result": {"content": json.dumps(measurement)}}},
            ]
            raw = "".join(json.dumps(event) + "\n" for event in events) + "not-json\n"
            stream = evaluation / "session/events.jsonl"
            stream.write_text(raw)
            with patch.object(controller, "EVAL", evaluation), patch.object(controller, "STATE", state), \
                    patch.object(controller, "BASELINE", TEST_SEEDS[str(evaluation / "workspace")]), \
                    redirect_stdout(io.StringIO()):
                controller.analyze()
            report = json.loads((evaluation / "observed-evidence.json").read_text())
            self.assertTrue(report["evidence_errors"])
            self.assertEqual(report["root_clear_events"], [])
            self.assertEqual(report["context_measurements"], [])
            self.assertEqual(report["native_artifact_and_terminal_calls"], [])
            self.assertTrue(report["quarantined_event_snapshot"])
            self.assertEqual(stream.read_text(), raw)
            positive = self.run_analyzer(evaluation, state, events)
            self.assertEqual(len(positive["root_clear_events"]), 2)
            self.assertEqual(len(positive["context_measurements"]), 1)
            self.assertTrue(positive["native_artifact_and_terminal_calls"])
            self.assertEqual(positive["quarantined_event_snapshot"], [])

    def test_process_substitution_is_early_substantive_invalid_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for command in (f"cat <(touch '{root / 'witness.json'}')",
                            f"cat >({root / 'unknown-writer'})"):
                events = self.semantic_events(root)
                self.insert_before_guidance(events, [
                    {"type": "tool.execution_start", "id": "process-substitution", "timestamp": "1-0a",
                     "data": {"toolCallId": "process-substitution", "toolName": "bash", "arguments": {"command": command}}},
                    {"type": "tool.execution_complete", "id": "process-substitution-complete", "timestamp": "1-0b",
                     "data": {"toolCallId": "process-substitution", "success": True, "result": {"content": ""}}},
                ])
                report = recovery_event_errors(events, root)[0]
                self.assertTrue(report["errors"])
                self.assertEqual(report["first_substantive_work"], "process-substitution")

    def test_owned_server_cleanup_is_directly_testable(self):
        self.assertTrue(callable(getattr(os_permission_smoke, "retire_owned_server", None)),
                        "inline PID-only cleanup lacks a direct identity-gated test surface")

    def test_server_cleanup_signals_only_matching_live_generation(self):
        captured = {"pid": 123, "uid": 1000, "uids": [1000] * 4, "start_ticks": 456,
                    "boot_id": "boot-fixture", "socket": "/tmp/owned-fixture.sock", "socket_dev": 1,
                    "socket_ino": 789, "socket_uid": 1000, "session": "owned-fixture"}
        with patch.object(os_permission_smoke.os, "pidfd_open", return_value=99, create=True) as opened, \
                patch.object(os_permission_smoke.signal, "pidfd_send_signal", create=True) as signaled, \
                patch.object(os_permission_smoke.os, "close") as closed, \
                patch.object(os_permission_smoke.select, "select", return_value=([99], [], [])), \
                patch.object(os_permission_smoke, "server_identity", return_value=captured) as identity, \
                patch.object(os_permission_smoke, "worker") as worker:
            result = os_permission_smoke.retire_owned_server(captured, 1000)
            self.assertTrue(result["retired"])
            opened.assert_called_once_with(123, 0)
            identity.assert_called_once_with(captured["socket"], captured["session"])
            signaled.assert_called_once_with(99, os_permission_smoke.signal.SIGTERM, None, 0)
            closed.assert_called_once_with(99)
            worker.assert_not_called()  # No generic/default/name-based kill.

    def test_server_cleanup_refuses_stale_reused_wrong_or_missing_identity(self):
        captured = {"pid": 123, "uid": 1000, "uids": [1000] * 4, "start_ticks": 456,
                    "boot_id": "boot-fixture", "socket": "/tmp/owned-fixture.sock", "socket_dev": 1,
                    "socket_ino": 789, "socket_uid": 1000, "session": "owned-fixture"}
        changes = [None, dict(captured, start_ticks=457), dict(captured, boot_id="other-boot"),
                   dict(captured, uid=1001, uids=[1001] * 4), dict(captured, socket_ino=790),
                   dict(captured, socket="/tmp/other.sock"), dict(captured, session="other-session"),
                   dict(captured, socket_uid=0)]
        for current in changes:
            with patch.object(os_permission_smoke.os, "pidfd_open", return_value=99, create=True), \
                    patch.object(os_permission_smoke.signal, "pidfd_send_signal", create=True) as signal, \
                    patch.object(os_permission_smoke.os, "close") as close, \
                    patch.object(os_permission_smoke, "server_identity", return_value=current), \
                    patch.object(os_permission_smoke, "worker") as worker:
                result = os_permission_smoke.retire_owned_server(captured, 1000)
                self.assertFalse(result["retired"], current)
                signal.assert_not_called()
                worker.assert_not_called()
                close.assert_called_once_with(99)
        for invalid in (None, {}, dict(captured, uid=0), dict(captured, start_ticks=None),
                        dict(captured, socket_uid=0), dict(captured, session="")):
            with patch.object(os_permission_smoke.os, "pidfd_open", create=True) as opened, \
                    patch.object(os_permission_smoke.signal, "pidfd_send_signal", create=True) as signal:
                self.assertFalse(os_permission_smoke.retire_owned_server(invalid, 1000)["retired"])
                opened.assert_not_called()
                signal.assert_not_called()

    def test_incomplete_event_rows_preserve_analysis_and_export_packet(self):
        incomplete = [
            {"type": "user.message", "id": "bad-user", "timestamp": "0-0"},
            {"type": "user.message", "id": "bad-user", "timestamp": "0-0", "data": {"content": None}},
            {"type": "", "id": "bad-type", "timestamp": "0-0", "data": {}},
            {"type": "session.info", "id": "bad-time", "timestamp": " ", "data": {}},
            {"type": "tool.execution_complete", "timestamp": "0-0",
             "data": {"toolCallId": "bad-call", "success": True, "result": {"content": "text"}}},
            {"type": "assistant.message", "id": "bad-model", "timestamp": "0-0",
             "data": {"content": "text", "model": {"bad": 1}}},
            {"type": "assistant.message", "id": "bad-model-list", "timestamp": "0-0",
             "data": {"content": "text", "model": ["bad"]}},
            {"type": "assistant.message", "id": "bad-model-number", "timestamp": "0-0",
             "data": {"content": "text", "model": 1}},
        ]
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            for row in incomplete:
                report = self.run_analyzer(evaluation, state, [row] + events)
                self.assertTrue(report["evidence_errors"])
                self.assertTrue((evaluation / "judge-packet.json").exists())

    def test_valid_snapshot_export_diagnostics_preserve_normal_root_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            report = self.run_analyzer(evaluation, state, events, captured=True)
            self.assertEqual(len(report["root_clear_events"]), 2)
            self.assertEqual(report["quarantined_event_snapshot"], [])
            self.assertFalse(any(error["source"] == "export diagnostics" for error in report["evidence_errors"]))
            for model in (None, "", "fixture-model"):
                valid_events = copy.deepcopy(events)
                valid_events[0]["data"]["model"] = model
                report = self.run_analyzer(evaluation, state, valid_events, captured=True)
                self.assertEqual(len(report["root_clear_events"]), 2)
                self.assertFalse(report["event_parser_errors"])

    @unittest.skipUnless(os.name == "posix", "snapshot export requires the Linux fixture")
    def test_completion_without_id_exports_as_negative_row_not_crash(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            original, state, events = self.analyzer_fixture(base / "original")
            capsule = TEST_SEEDS[str(original / "workspace")]
            capsule.chmod(0o755)
            (capsule / "venue-seal.json").write_text(json.dumps({
                "session_id": "root-fixture", "operator_sudo_denied": True,
                "sudo_elevation_bits_removed": True,
            }))
            home = base / "home"
            session = home / ".copilot/session-state/root-fixture"
            session.mkdir(parents=True)
            row = {"type": "tool.execution_complete", "timestamp": "0-0",
                   "data": {"toolCallId": "unmatched", "success": True, "result": {"content": "text"}}}
            (session / "events.jsonl").write_text(json.dumps(row) + "\n")
            evaluation = base / "export/eval"
            evaluation.mkdir(parents=True)
            with patch.object(controller, "BASELINE", capsule), patch.object(controller, "EVAL", evaluation), \
                    patch.object(controller, "OUT", evaluation.parent), \
                    patch.object(controller, "REPO", original / "workspace"), \
                    patch.object(controller, "SCENARIO", Path(__file__).parent), \
                    patch.object(Path, "home", return_value=home), patch.object(controller, "run", return_value="true"), \
                    patch.object(controller.subprocess, "run", return_value=type("Result", (), {"returncode": 1})()), \
                    redirect_stdout(io.StringIO()):
                controller.export()
            self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"], "INVALID_EXPORT")

    @unittest.skipUnless(os.name == "posix", "snapshot export requires the Linux fixture")
    def test_failed_event_snapshot_copy_quarantines_previous_root_clears(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            original, state, events = self.analyzer_fixture(base / "original")
            capsule = TEST_SEEDS[str(original / "workspace")]
            capsule.chmod(0o755)
            (capsule / "venue-seal.json").write_text(json.dumps({
                "session_id": "root-fixture", "operator_sudo_denied": True,
                "sudo_elevation_bits_removed": True,
            }))
            home = base / "home"
            session = home / ".copilot/session-state/root-fixture"
            session.mkdir(parents=True)
            (session / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
            evaluation = base / "export/eval"
            (evaluation / "session").mkdir(parents=True)
            prior = evaluation / "session/events.jsonl"
            prior.write_text("".join(json.dumps(e) + "\n" for e in events))
            before = prior.read_bytes()
            copy = controller.snapshot_io.write_at

            def fail_events(parent, name, content):
                if name == "events.jsonl":
                    raise PermissionError("fixture copy denied")
                return copy(parent, name, content)

            with patch.object(controller, "BASELINE", capsule), patch.object(controller, "EVAL", evaluation), \
                    patch.object(controller, "STATE", state), patch.object(controller, "OUT", evaluation.parent), \
                    patch.object(controller, "REPO", original / "workspace"), \
                    patch.object(controller, "SCENARIO", Path(__file__).parent), \
                    patch.object(Path, "home", return_value=home), patch.object(controller, "run", return_value="true"), \
                    patch.object(controller.snapshot_io, "write_at", side_effect=fail_events), \
                    patch.object(controller.subprocess, "run", return_value=type("Result", (), {"returncode": 1})()), \
                    redirect_stdout(io.StringIO()):
                controller.export()
                controller.analyze()
            report = json.loads((evaluation / "observed-evidence.json").read_text())
            self.assertEqual(report["root_clear_events"], [])
            self.assertTrue(report["evidence_errors"])
            self.assertTrue(report["event_parser_errors"])
            self.assertEqual(prior.read_bytes(), before)
            self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"], "INVALID_EXPORT")

    @unittest.skipUnless(os.name == "posix", "snapshot export requires the Linux fixture")
    def test_export_keeps_diagnostic_packet_for_invalid_event_stream(self):
        for kind in ("missing", "unreadable", "bad-row", "empty", "invalid-object"):
            with tempfile.TemporaryDirectory() as temp:
                base = Path(temp)
                original, state, events = self.analyzer_fixture(base / "original")
                capsule = TEST_SEEDS[str(original / "workspace")]
                capsule.chmod(0o755)
                (capsule / "venue-seal.json").write_text(json.dumps({
                    "session_id": "root-fixture", "operator_sudo_denied": True,
                    "sudo_elevation_bits_removed": True,
                }))
                home = base / "home"
                session = home / ".copilot/session-state/root-fixture"
                session.mkdir(parents=True)
                path = session / "events.jsonl"
                if kind != "missing":
                    path.write_text("" if kind == "empty" else "{}\n" if kind in ("unreadable", "invalid-object") else
                                    json.dumps(events[0]) + "\nnot-json\n")
                evaluation = base / "export/eval"
                evaluation.mkdir(parents=True)
                original_read = Path.read_text

                def read(candidate, *args, **kwargs):
                    if kind == "unreadable" and candidate.name == "events.jsonl":
                        raise PermissionError("fixture unreadable")
                    return original_read(candidate, *args, **kwargs)

                with patch.object(controller, "BASELINE", capsule), patch.object(controller, "EVAL", evaluation), \
                        patch.object(controller, "OUT", evaluation.parent), \
                        patch.object(controller, "REPO", original / "workspace"), \
                        patch.object(controller, "SCENARIO", Path(__file__).parent), \
                        patch.object(Path, "home", return_value=home), patch.object(Path, "read_text", read), \
                        patch.object(controller, "run", return_value="true"), \
                        patch.object(controller.subprocess, "run", return_value=type("Result", (), {"returncode": 1})()), \
                        redirect_stdout(io.StringIO()):
                    controller.export()
                self.assertTrue((evaluation / "judge-packet.json").exists(), kind)
                self.assertTrue(json.loads((evaluation / "export-errors.json").read_text())["errors"], kind)
                self.assertIn("INVALID", (evaluation / "transcript.txt").read_text(), kind)
                self.assertEqual(json.loads((evaluation / "judge-packet.json").read_text())["event_export_status"],
                                 "INVALID_EXPORT")

    def test_missing_or_unreadable_sudo_grant_refuses_before_mutation(self):
        for unreadable in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                evaluation, state, events = self.analyzer_fixture(root)
                workspace = evaluation / "workspace"
                capsule = TEST_SEEDS[str(workspace)]
                sudoers = root / "sudoers.d"
                sudoers.mkdir()
                grant = sudoers / "operator"
                if unreadable:
                    grant.write_text("operator ALL=(ALL) NOPASSWD:ALL\n")
                sudo = root / "sudo"
                sudo.write_text("fixture")
                sudo.chmod(0o6755)
                before = sudo.stat().st_mode
                flag = root / ".dockerenv"
                flag.touch()
                original_read = Path.read_text

                def read(candidate, *args, **kwargs):
                    if unreadable and candidate == grant:
                        raise PermissionError("fixture denied")
                    return original_read(candidate, *args, **kwargs)

                with patch.object(Path, "read_text", read):
                    with self.assertRaises(ValueError):
                        validation.seal_disposable_venue(workspace, capsule, sudoers, sudo, flag,
                                                         policy_check=lambda: self.fail("no mutation allowed"))
                self.assertFalse((sudoers / "zz-native-cli-operator-deny").exists())
                self.assertEqual(sudo.stat().st_mode, before)
                self.assertFalse((capsule / "venue-seal.json").exists())

    def test_baseline_api_has_no_mutable_task_override(self):
        for function in (validation.common_errors, validation.checkpoint_errors, validation.witness_errors):
            self.assertNotIn("task", inspect.signature(function).parameters)

    def test_missing_validator_arguments_have_concise_usage(self):
        options = {"capture_output": True, "text": True}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run([sys.executable, str(Path(validation.__file__))], **options)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("usage:", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)

    def test_apply_patch_limitation_is_declared_in_seed_contract_and_prompt(self):
        source = Path(controller.__file__).read_text()
        self.assertTrue('"unsupported_write_mechanisms": ["apply_patch"]' in source)
        self.assertTrue("apply_patch is unsupported" in source)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = self.semantic_events(root)
            writer = next(event for event in events if event.get("id") == "2-4")
            writer["data"]["toolName"] = "apply_patch"
            writer["data"]["arguments"] = {"patch": f"*** Add File: {root / 'witness.json'}\n+{{}}\n"}
            self.assertTrue(recovery_event_errors(events, root)[1]["errors"])

    def test_unpaid_permission_smoke_requires_all_denials_and_actual_inheritance(self):
        names = ("direct_write", "direct_chmod", "direct_rename", "trusted_validator_write",
                 "sudo_write", "sudo_chmod", "sudo_rename", "sudo_validator_write")
        report = {"uid": 1000, "no_new_privs": 0, "workspace_changed": True,
                  "checks": {name: {"denied": True} for name in names}}
        self.assertEqual(os_permission_smoke.probe_errors(report, 0, 1000), [])
        self.assertTrue(os_permission_smoke.probe_errors(report, 1, 1000))
        for name in names:
            bad = copy.deepcopy(report)
            bad["checks"][name]["denied"] = False
            self.assertTrue(os_permission_smoke.probe_errors(bad, 0, 1000), name)
        bad = dict(report, workspace_changed=False)
        self.assertTrue(os_permission_smoke.probe_errors(bad, 0, 1000))
        self.assertTrue(os_permission_smoke.probe_errors(dict(report, uid=0), 0, 1000))

        def missing():
            raise FileNotFoundError(os_permission_smoke.errno.ENOENT, "fixture missing")

        def forbidden():
            raise PermissionError(os_permission_smoke.errno.EACCES, "fixture forbidden")

        self.assertFalse(os_permission_smoke.denied(missing)["denied"])
        self.assertTrue(os_permission_smoke.denied(forbidden)["denied"])

    def setUp(self):
        super().setUp()
        TEST_SEEDS.clear()

    def provisional_baseline(self, root):
        if str(root) in TEST_SEEDS:
            return TEST_SEEDS[str(root)]
        defaults = {
            "AGENTS.md": "Seed fixture guidance.\n",
            "task.json": json.dumps({key: key for key in (
                "original_objective", "parent_gate", "current_slice", "unresolved_user_requests", "decisions", "live_references")}),
            "audit-contract.md": "Seed fixture task contract.\n",
            "ledger.json": '{"canary":"fixture","owner":"root-fixture"}\n',
            ".context-handoff/config.yaml": "mode: auto\n",
        }
        for name, text in defaults.items():
            path = root / name
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        destination = Path(holder.name) / "seed"
        validation.capture_baseline(root, destination, "root-fixture")
        TEST_SEEDS[str(root)] = destination
        return destination

    def test_agent_task_edit_cannot_change_authoritative_seed_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            workspace = evaluation / "workspace"
            baseline = self.provisional_baseline(workspace)
            task = json.loads((workspace / "task.json").read_text())
            task.update(original_objective="worker replacement", parent_gate="worker declares complete")
            (workspace / "task.json").write_text(json.dumps(task))
            document = json.loads(events[1]["data"]["arguments"]["content"])
            document.update(original_objective=task["original_objective"], parent_gate=task["parent_gate"])
            content = json.dumps(document)
            events[1]["data"]["arguments"]["content"] = content
            result = json.loads(events[4]["data"]["result"]["content"])
            result["content"] = content
            events[4]["data"]["result"]["content"] = json.dumps(result)
            with patch.object(controller, "BASELINE", baseline, create=True):
                report = self.run_analyzer(evaluation, state, events)
            errors = report["checkpoint_contract_observations"][0]["errors"]
            self.assertTrue(any("original_objective" in error or "parent_gate" in error for error in errors))

    def test_agent_guidance_edit_cannot_change_expected_seed_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = self.semantic_events(root)
            baseline = self.provisional_baseline(root)
            forged = "Worker replacement guidance.\n"
            (root / "AGENTS.md").write_text(forged)
            next(e for e in events if e.get("id") == "complete-1-1")["data"]["result"]["content"] = forged
            with patch.object(validation, "BASELINE_DIR", baseline, create=True):
                report = recovery_event_errors(events, root)
            self.assertTrue(report[0]["errors"])

    def test_counter_conflicts_and_wrong_document_kind_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            root = evaluation / "workspace"
            checkpoint = json.loads(events[1]["data"]["arguments"]["content"])
            witness = dict(checkpoint, transitions_completed=2,
                           declaration="NOT-JUDGED; parent remains Active", unexercised=["adverse cases"])
            self.assertTrue(witness_errors(witness, root))
            self.assertTrue(checkpoint_errors(dict(checkpoint, transitions_completed=1), root))

    def test_live_checkpoint_rejects_abbreviated_native_measurement(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            root = evaluation / "workspace"
            checkpoint = json.loads(events[1]["data"]["arguments"]["content"])
            checkpoint["trigger_measurements"] = [{
                "context_info": {"promptTokenLimit": 200000},
                "fraction_remaining": .35, "context_window": {"number": 1}}]
            self.assertTrue(checkpoint_errors(checkpoint, root))

    def test_stderr_file_write_is_substantive_not_fd_duplication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = self.semantic_events(root)
            mutation = [
                {"type": "tool.execution_start", "id": "stderr-writer", "timestamp": "1-0a",
                 "data": {"toolCallId": "stderr-writer", "toolName": "bash",
                          "arguments": {"command": f"printf '{{}}' 2>'{root / 'witness.json'}'"}}},
                {"type": "tool.execution_complete", "id": "stderr-writer-complete", "timestamp": "1-0b",
                 "data": {"toolCallId": "stderr-writer", "success": True, "result": {"content": ""}}},
            ]
            self.insert_before_guidance(events, mutation)
            report = recovery_event_errors(events, root)[0]
            self.assertEqual(report["first_substantive_work"], "stderr-writer")
            duplication = {"data": {"toolName": "bash", "arguments": {"command": "printf '{}' 2>&1"}}}
            self.assertIsNone(validation.write_destinations(duplication, str(root)))

    def test_missing_seed_baseline_is_uncredited_without_workspace_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            missing = Path(temp) / "absent-seed"
            with patch.object(controller, "EVAL", evaluation), patch.object(controller, "STATE", state), \
                    patch.object(controller, "BASELINE", missing), redirect_stdout(io.StringIO()):
                (evaluation / "session/events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
                controller.analyze()
            report = json.loads((evaluation / "observed-evidence.json").read_text())
            self.assertFalse(report["seed_baseline_available"])
            self.assertFalse(report["checkpoint_contract_observations"][0]["pre_clear_content_readback"])
            self.assertTrue(report["semantic_recovery_validation"][0]["errors"])
            self.assertTrue((evaluation / "judge-packet.json").exists())
            root = evaluation / "workspace"
            self.assertTrue(validation.recovery_event_errors(events, root, baseline=missing)[0]["errors"])

    def test_seed_corpus_and_contract_mutations_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            root = evaluation / "workspace"
            baseline = TEST_SEEDS[str(root)]
            document = json.loads(events[1]["data"]["arguments"]["content"])
            (root / "corpus/shard-000.txt").write_text("record=00077 severity=0\nrecord=00001 severity=1\n")
            document["audit_cursor"]["last_record"] = 77
            self.assertTrue(validation.checkpoint_errors(document, root, baseline=baseline))
            (root / "audit-contract.md").write_text("Worker weakened the contract.\n")
            report = self.run_analyzer(evaluation, state, events)
            self.assertTrue(any("audit-contract.md" in str(error) for error in report["evidence_errors"]))
            self.assertTrue(report["semantic_recovery_validation"][0]["errors"])

    def test_setup_captures_before_launch_and_pressure_does_not_reseed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            repository, evaluation, capsule = base / "driven", base / "eval", base / "trusted-seed"
            lib = base / "lib"
            lib.mkdir()
            (lib / "literal-mode.md").write_text("Literal-mode fixture.\n")
            commands = []

            def arranged_command(*args, **kwargs):
                commands.append(args)
                if args[:2] == ("sudo", "python3"):
                    if args[3] == "capture-baseline":
                        validation.capture_baseline(Path(args[4]), Path(args[5]), args[6])
                    elif args[3] == "seal-venue":
                        capsule.chmod(0o755)  # Simulated trusted root setup, not a driven mutation.
                        (capsule / "venue-seal.json").write_text(json.dumps({
                            "session_id": json.loads((capsule / "manifest.json").read_text())["session_id"],
                            "operator_sudo_denied": True, "sudo_elevation_bits_removed": True,
                        }))
                        capsule.chmod(0o555)
                return ""

            with patch.object(controller, "REPO", repository), patch.object(controller, "EVAL", evaluation), \
                    patch.object(controller, "STATE", evaluation / "driver-state.json"), \
                    patch.object(controller, "BASELINE", capsule), patch.object(controller, "LIB", lib), \
                    patch.object(controller, "SCENARIO", Path(__file__).parent), \
                    patch.object(controller, "run", side_effect=arranged_command):
                controller.seed()
                state, seed = controller.trusted_identity()
                self.assertEqual(seed["manifest"]["session_id"], state["session_id"])
                self.assertEqual(len(list((capsule / "corpus").glob("*.txt"))), 80)
                self.assertTrue(any(args[:2] == ("sudo", "python3") for args in commands))
                (repository / "task.json").write_text('{"worker":"changed"}')
                with patch.object(controller, "send") as send:
                    with self.assertRaises(RuntimeError):
                        controller.pressure()
                    send.assert_not_called()
                self.assertEqual(hashlib.sha256((capsule / "task.json").read_bytes()).hexdigest(),
                                 seed["manifest"]["files_sha256"]["task.json"])
                (repository / "task.json").write_bytes((capsule / "task.json").read_bytes())
                (evaluation / "driver-state.json").write_text('{"session_id":"worker replacement"}')
                # Launch identity is derived from the capsule, not mutable state.
                self.assertEqual(controller.trusted_identity()[0]["session_id"], state["session_id"])
                with patch.object(controller, "capture", return_value=""), patch.object(controller.time, "sleep"), \
                        patch.object(controller, "verify_tmux_server", return_value={"server_no_new_privs": True}), \
                        patch.object(controller, "TMUX_SOCKET", base / "dedicated.sock"), \
                        redirect_stdout(io.StringIO()):
                    controller.launch()
                tmux_commands = [args for args in commands if args[:3] == ("setpriv", "--no-new-privs", "tmux")]
                self.assertTrue(tmux_commands)
                self.assertTrue(all(args[3:5] == ("-S", str(base / "dedicated.sock")) for args in tmux_commands))
                launch = next(args[-1] for args in tmux_commands if args[5] == "new-session")
                self.assertIn("setpriv --no-new-privs", launch)
                self.assertIn(state["session_id"], launch)
                self.assertNotIn("worker replacement", launch)

    def test_dedicated_tmux_server_must_be_nnp_and_never_reused(self):
        with patch.object(controller, "tmux", return_value="123"), \
                patch.object(Path, "read_text", return_value="Name:\ttmux\nNoNewPrivs:\t1\n"):
            controller.verify_tmux_server()
        with patch.object(controller, "tmux", return_value="123"), \
                patch.object(Path, "read_text", return_value="Name:\ttmux\nNoNewPrivs:\t0\n"):
            with self.assertRaises(RuntimeError):
                controller.verify_tmux_server()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evaluation, state, events = self.analyzer_fixture(root)
            capsule = TEST_SEEDS[str(evaluation / "workspace")]
            capsule.chmod(0o755)
            (capsule / "venue-seal.json").write_text(json.dumps({
                "session_id": "root-fixture", "operator_sudo_denied": True,
                "sudo_elevation_bits_removed": True,
            }))
            socket = root / "occupied.sock"
            socket.touch()
            with patch.object(controller, "BASELINE", capsule), patch.object(controller, "TMUX_SOCKET", socket), \
                    patch.object(controller, "REPO", evaluation / "workspace"), patch.object(controller, "run") as run:
                with self.assertRaises(RuntimeError):
                    controller.launch()
                run.assert_not_called()

    def test_venue_seal_cuts_server_mediated_sudo_without_host_policy_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            evaluation, state, events = self.analyzer_fixture(root)
            workspace = evaluation / "workspace"
            capsule = TEST_SEEDS[str(workspace)]
            capsule.chmod(0o755)
            sudoers = root / "sudoers.d"
            sudoers.mkdir()
            grant = sudoers / "operator"
            grant.write_text("operator ALL=(ALL) NOPASSWD:ALL\n")
            sudo = root / "sudo"
            sudo.write_text("fixture sudo executable")
            sudo.chmod(0o6755)
            flag = root / ".dockerenv"
            flag.touch()
            checked = []
            marker = validation.seal_disposable_venue(workspace, capsule, sudoers, sudo, flag,
                                                       policy_check=lambda: checked.append(True))
            self.assertTrue(checked)
            self.assertFalse(grant.exists())
            self.assertEqual((sudoers / "zz-native-cli-operator-deny").read_text(), "operator ALL=(ALL:ALL) !ALL\n")
            self.assertFalse(sudo.stat().st_mode & (0o4000 | 0o2000))
            self.assertTrue(marker["operator_sudo_denied"])
            self.assertTrue(marker["sudo_elevation_bits_removed"])
            with self.assertRaises((OSError, ValueError)):
                validation.seal_disposable_venue(workspace, capsule, sudoers, sudo, root / "not-a-container",
                                                  policy_check=lambda: self.fail("host policy must not be touched"))

    def test_fabricated_shell_output_cannot_credit_a_file_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            guidance = "Expected guidance bytes.\n"
            (root / "AGENTS.md").write_text(guidance)
            target = str(root / "AGENTS.md")
            fabricated = [
                f"printf 'Expected guidance bytes.\\n'; head -n 0 '{target}'",
                f"echo 'Expected guidance bytes.' && head -n 0 '{target}'",
                f"cat '{target}' | cat",
                f"cat '{target}' 2>/dev/null",
                f"cat $(echo '{target}')",
            ]
            completion = {"data": {"success": True, "result": {"content": guidance}}}
            for command in fabricated:
                start = {"data": {"toolName": "bash", "arguments": {"command": command}}}
                self.assertFalse(content_read(start, completion, target, guidance, str(root)), command)
            positive = {"data": {"toolName": "bash", "arguments": {
                "command": f"cd '{root}' && cat AGENTS.md && cat task.json"}}}
            self.assertTrue(content_read(positive, completion, target, guidance, str(root)))
            events = self.semantic_events(root)
            start = next(e for e in events if e.get("id") == "1-4")
            path = start["data"]["arguments"]["path"]
            output = next(e for e in events if e.get("id") == "complete-1-4")["data"]["result"]["content"]
            start["data"] = {"toolName": "bash", "arguments": {
                "command": f"printf '%s' '{output}'; head -n 0 '{path}'"}}
            self.assertFalse(content_read(start, {"data": {"success": True, "result": {"content": output}}},
                                          path, output, str(root), [61, 80]))

    def test_cursor_fields_reject_boolean_and_float_aliases(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "corpus").mkdir()
            (root / "corpus/shard-000.txt").write_text("".join(
                f"record={number:05d} severity={number % 4}\n" for number in range(80)))
            (root / "corpus/shard-001.txt").write_text("record=00080 severity=0\n")
            self.provisional_baseline(root)
            first = {"path": "corpus/shard-000.txt", "last_line": 1, "last_record": 0,
                     "last_severity": 0, "next_path": "corpus/shard-000.txt", "next_line": 2}
            self.assertEqual(cursor_errors(first, root), [])
            for key in ("last_record", "last_severity"):
                for value in (False, 0.0):
                    self.assertTrue(cursor_errors(dict(first, **{key: value}), root), (key, value))
            boundary = dict(first, last_line=80, last_record=79, last_severity=3,
                            next_path="corpus/shard-001.txt", next_line=1)
            self.assertEqual(cursor_errors(boundary, root), [])
            for value in (True, 1.0):
                self.assertTrue(cursor_errors(dict(boundary, next_line=value), root), value)

    def test_malformed_optional_execution_metadata_preserves_packet(self):
        cases = [
            {"shellExecution": None},
            {"shellExecution": []},
            {"toolTelemetry": None},
            {"toolTelemetry": {"metrics": None}},
            {"toolTelemetry": {"metrics": []}},
            {"shellExecution": {"exitCode": "0"}},
        ]
        self.assertTrue(successful({"data": {"success": True}}))
        for optional in cases:
            with tempfile.TemporaryDirectory() as temp:
                evaluation, state, events = self.analyzer_fixture(Path(temp))
                events[2]["data"].update(optional)
                report = self.run_analyzer(evaluation, state, events)
                self.assertTrue(report["evidence_errors"], optional)
                self.assertFalse(report["checkpoint_contract_observations"][0]["pre_clear_content_readback"])
                self.assertTrue((evaluation / "judge-packet.json").exists())
                self.assertFalse(successful({"data": dict(success=True, **optional)}), optional)

    def test_warning_extraction(self):
        events = [
            {"type": "session.warning", "id": "1a2c1272", "data": {"message": "[Context Handoff] Native checkpoint preparation boundary reached (64%)."}},
            {"type": "session.warning", "id": "17993779", "data": {"message": "[Context Handoff] Native checkpoint preparation boundary reached (55%)."}},
            {"type": "session.warning", "data": {"message": "unrelated"}},
            {"type": "assistant.message", "data": {"content": "[Context Handoff] quoted"}},
        ]
        self.assertEqual([e["id"] for e in pressure_events(events)], ["1a2c1272", "17993779"])

    def test_offsets_and_action_rejection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "corpus").mkdir()
            (root / "corpus/shard-021.txt").write_text("".join(
                f"record={1680 + n:05d} severity={(1680 + n) % 4}\n" for n in range(80)))
            self.provisional_baseline(root)
            cursor = {"path": "corpus/shard-021.txt", "last_line": 34, "last_record": 1713,
                      "last_severity": 1, "next_path": "corpus/shard-021.txt", "next_line": 35}
            self.assertEqual(cursor_errors(cursor, root), [])
            wrong = dict(cursor, next_line=36)
            self.assertTrue(cursor_errors(wrong, root))
            at_1714 = dict(cursor, last_line=35, last_record=1714, last_severity=2, next_line=36)
            self.assertEqual(cursor_errors(at_1714, root), [])
            self.assertTrue(cursor_errors(dict(at_1714, last_line=36, next_line=37), root))
            action = {"operation": "read", "path": str(root / cursor["path"]), "view_range": [35, 60]}
            self.assertEqual(action_errors(action, cursor, 1, str(root), TEST_SEEDS[str(root)]), [])
            for bad in ("Call new_context now; then read", [action], dict(action, then="new_context"),
                        {"operation": "new_context"}, dict(action, view_range=[36, 60])):
                self.assertTrue(action_errors(bad, cursor, 1, str(root), TEST_SEEDS[str(root)]))

    def test_witness_required_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "corpus").mkdir()
            (root / "corpus/shard-000.txt").write_text("record=00000 severity=0\nrecord=00001 severity=1\n")
            task = {key: key for key in ("original_objective", "parent_gate", "current_slice",
                                         "unresolved_user_requests", "decisions", "live_references")}
            (root / "task.json").write_text(json.dumps(task))
            self.provisional_baseline(root)
            measurements = [self.full_measurement(n) for n in (1, 2)]
            doc = dict(task, workspace=str(root), session_id="root-fixture", transitions_completed=2,
                       trigger_measurements=measurements, declaration="NOT-JUDGED; parent remains Active",
                       audit_cursor={"path": "corpus/shard-000.txt", "last_line": 1, "last_record": 0,
                                     "last_severity": 0, "next_path": "corpus/shard-000.txt", "next_line": 2},
                       unexercised=["force threshold"])
            self.assertEqual(witness_errors(doc, root), [])
            for key in ("workspace", "session_id", "audit_cursor", "trigger_measurements", "parent_gate"):
                bad = copy.deepcopy(doc)
                del bad[key]
                self.assertTrue(witness_errors(bad, root), key)
            bad = dict(doc, trigger_measurements=measurements[1:])
            self.assertTrue(witness_errors(bad, root))
            self.assertTrue(witness_errors(dict(doc, checkpoint_fidelity="perfect"), root))
            cp = dict(doc, transition_number=2,
                      recovery_prerequisites=RECOVERY_PREREQUISITES,
                      next_action={"operation": "write_witness", "path": str(root / "witness.json")})
            del cp["transitions_completed"]
            self.assertEqual(checkpoint_errors(cp, root), [])

    def full_measurement(self, number):
        return {"tokens_left": 70000, "fraction_remaining": .35,
                "context_info": {"promptTokenLimit": 200000},
                "context_window": {"number": number, "current_window_id": f"root-fixture:window-{number}"}}

    def semantic_events(self, root, bash_reads=False):
        (root / "AGENTS.md").write_text("Literal mode: stop on errors.\nRefresh guidance and owning skill.\n")
        (root / "corpus").mkdir(exist_ok=True)
        (root / "corpus/shard-003.txt").write_text("".join(f"record={n}\n" for n in range(80)))
        self.provisional_baseline(root)
        events = []
        for window in (1, 2):
            events.append({"type": "session.context_cleared", "id": f"clear-{window}"})
            path = str(root / "corpus/shard-003.txt")
            action = ({"operation": "read", "path": path, "view_range": [61, 80]}
                      if window == 1 else {"operation": "write_witness", "path": str(root / "witness.json")})
            read_args = ("bash", {"command": f"cd '{root}' && cat AGENTS.md && cat task.json"}) if bash_reads \
                else ("view", {"path": str(root / "AGENTS.md")})
            tools = [
                ("session_artifacts", {"operation": "read", "path": "checkpoint.md"}),
                read_args,
                ("skill", {"skill": "context-handoff"}),
                ("get_context_remaining", {}),
                (("bash", {"command": f"sed -n '61,80p' '{path}'"}) if bash_reads else
                 ("view", {"path": action["path"], "view_range": action["view_range"]})) if window == 1
                else ("bash", {"command": f"cat > '{root / 'witness.json'}' <<'EOF'\n{{}}\nEOF"}),
            ]
            for step, (name, args) in enumerate(tools):
                call = f"{window}-{step}"
                events.append({"type": "tool.execution_start", "id": call,
                               "timestamp": f"{window}-{step}-0",
                               "data": {"toolCallId": call, "toolName": name, "arguments": args}})
                content = ""
                if step == 0:
                    content = json.dumps({"content": json.dumps({"next_action": action})})
                elif step == 1:
                    content = (root / "AGENTS.md").read_text() + ('\n{"task":"contract"}\n' if bash_reads else "")
                elif step == 2:
                    content = 'Skill "context-handoff" loaded successfully.'
                elif step == 3:
                    content = json.dumps({"tokens_left": 100000, "fraction_remaining": .5,
                                          "context_info": {"promptTokenLimit": 200000},
                                          "context_window": {"number": window + 1,
                                                             "current_window_id": f"root-fixture:window-{window + 1}"}})
                elif step == 4 and window == 1:
                    content = "".join(f"record={n}\n" for n in range(60, 80))
                events.append({"type": "tool.execution_complete", "id": f"complete-{call}",
                               "timestamp": f"{window}-{step}-1",
                               "data": {"toolCallId": call, "success": True,
                                        "interactionId": call,
                                        "result": {"content": content}}})
                if step == 2:
                    events.append({"type": "skill.context_delivered_ref", "id": f"delivery-{call}",
                                   "timestamp": f"{window}-{step}-2",
                                   "data": {"source": "skill-context-handoff", "interactionId": call,
                                            "prefix": "Base directory: /release/plugins/context-handoff/skills/context-handoff"}})
        return self.with_semantic_bindings(events)

    def test_semantic_view_and_combined_bash_content_success(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for bash_reads in (False, True):
                events = self.semantic_events(root, bash_reads)
                self.assertTrue(all(not row["errors"] for row in recovery_event_errors(events, root)))
                # Measurement may occur after the first task action; there is
                # no artificial exact position as long as its window is proved.
                measurement = [e for e in events if e.get("data", {}).get("toolCallId") == "1-3"]
                later = [e for e in events if e not in measurement]
                measurement[0]["timestamp"] = "1-5-0"
                measurement[1]["timestamp"] = "1-5-1"
                clear2 = next(i for i, e in enumerate(later) if e.get("id") == "clear-2")
                later[clear2:clear2] = measurement
                self.assertTrue(all(not row["errors"] for row in recovery_event_errors(later, root)))

    def test_semantic_missing_failed_or_bogus_refresh_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = self.semantic_events(root)
            for missing in ("1-1", "1-2", "1-3", "2-1", "2-2", "2-3"):
                bad = [e for e in events if e.get("data", {}).get("toolCallId") != missing]
                self.assertTrue(any(row["errors"] for row in recovery_event_errors(bad, root)), missing)
            for content in ("", "AGENTS.md read successfully", "different guidance"):
                bad = copy.deepcopy(events)
                next(e for e in bad if e.get("id") == "complete-1-1")["data"]["result"]["content"] = content
                self.assertTrue(any(row["errors"] for row in recovery_event_errors(bad, root)))
            bad = copy.deepcopy(events)
            next(e for e in bad if e.get("id") == "complete-1-1")["data"]["success"] = False
            self.assertTrue(any(row["errors"] for row in recovery_event_errors(bad, root)))
            # A successful RPC may still contain a failing shell command.
            bad = self.semantic_events(root, True)
            next(e for e in bad if e.get("id") == "complete-1-1")["data"]["shellExecution"] = {"exitCode": 1}
            self.assertTrue(any(row["errors"] for row in recovery_event_errors(bad, root)))
            bad = self.semantic_events(root)
            next(e for e in bad if e.get("id") == "delivery-1-2")["data"]["source"] = "wrong-skill"
            self.assertTrue(any(row["errors"] for row in recovery_event_errors(bad, root)))
            bad = copy.deepcopy(events)
            next(e for e in bad if e.get("id") == "1-1")["data"]["arguments"]["path"] = str(root / "other.md")
            self.assertTrue(any(row["errors"] for row in recovery_event_errors(bad, root)))
            bad = self.semantic_events(root, True)
            next(e for e in bad if e.get("id") == "1-1")["data"]["arguments"]["command"] = f"echo 'cat {root / 'AGENTS.md'}'"
            self.assertTrue(any(row["errors"] for row in recovery_event_errors(bad, root)))

    def test_semantic_work_before_refresh_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = self.semantic_events(root)
            for delayed in ("1-1", "1-2"):
                bad = copy.deepcopy(events)
                late = next(e for e in bad if e.get("id") == f"complete-{delayed}")
                bad.remove(late)
                work = next(i for i, e in enumerate(bad) if e.get("id") == "1-4")
                bad.insert(work + 1, late)
                self.assertTrue(any(row["errors"] for row in recovery_event_errors(bad, root)))

    def test_stream_positions_not_timestamp_labels_control_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, _ = self.analyzer_fixture(Path(temp))
            root = evaluation / "workspace"
            events = self.semantic_events(root)
            for event in events:
                event.setdefault("data", {})
                event["timestamp"] = "metadata-only"
            self.assertTrue(all(not row["errors"] for row in recovery_event_errors(events, root)))
            positive = self.run_analyzer(evaluation, state, events)
            self.assertEqual(len(positive["root_clear_events"]), 2)
            self.assertTrue(all(not row["errors"] for row in positive["semantic_recovery_validation"]))
            for delayed in ("1-1", "1-2"):
                with self.subTest(delayed=delayed):
                    bad = copy.deepcopy(events)
                    late = [event for event in bad
                            if event["data"].get("toolCallId") == delayed
                            or event["data"].get("interactionId") == delayed]
                    bad = [event for event in bad if event not in late]
                    work = next(i for i, event in enumerate(bad) if event.get("id") == "complete-1-4")
                    for index, event in enumerate(late):
                        event["timestamp"] = f"earlier-{index}"
                    bad[work + 1:work + 1] = late
                    self.assertTrue(recovery_event_errors(bad, root)[0]["errors"])
                    negative = self.run_analyzer(evaluation, state, bad)
                    self.assertTrue(negative["semantic_recovery_validation"][0]["errors"])

    def test_recorded_read_requires_exact_range_or_bounded_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = self.semantic_events(root)
            path = str(root / "corpus/shard-003.txt")
            entire = (root / "corpus/shard-003.txt").read_text()
            for arguments in ({"path": path}, {"path": path, "view_range": [1, 80]},
                              {"path": path, "view_range": [60, 80]}):
                bad = copy.deepcopy(events)
                next(e for e in bad if e.get("id") == "1-4")["data"]["arguments"] = arguments
                next(e for e in bad if e.get("id") == "complete-1-4")["data"]["result"]["content"] = entire
                self.assertTrue(recovery_event_errors(bad, root)[0]["errors"], arguments)
            bad = self.semantic_events(root, True)
            next(e for e in bad if e.get("id") == "1-4")["data"]["arguments"]["command"] = f"cat '{path}'"
            next(e for e in bad if e.get("id") == "complete-1-4")["data"]["result"]["content"] = entire
            self.assertTrue(recovery_event_errors(bad, root)[0]["errors"])
            wrong_operands = copy.deepcopy(events)
            next(e for e in wrong_operands if e.get("id") == "1-4")["data"]["arguments"]["view_range"] = [62, 80]
            self.assertTrue(recovery_event_errors(wrong_operands, root)[0]["errors"])
            exact_output = copy.deepcopy(events)
            del next(e for e in exact_output if e.get("id") == "1-4")["data"]["arguments"]["view_range"]
            self.assertFalse(recovery_event_errors(exact_output, root)[0]["errors"])

    def test_unknown_potential_writers_cannot_disappear_before_refresh(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = str(root / "witness.json")
            for command in (f"printf '{{}}' | tee '{target}'", f"touch '{target}'",
                            f"mv draft.json '{target}'", f"truncate -s 0 '{target}'",
                            f"sed -n 'w {target}' task.json", f"sed -n 'W {target}' task.json",
                            f"sed -n 'e touch {target}' task.json",
                            f"sed -n -e '1p' -e 'w {target}' task.json",
                            f"find . -fprint '{target}'", f"printf '{{}}'\ntee '{target}'",
                            f"echo $(touch '{target}')", f"echo `touch '{target}'`"):
                events = self.semantic_events(root)
                mutation = [
                    {"type": "tool.execution_start", "id": "early-writer", "timestamp": "1-0a",
                     "data": {"toolCallId": "early-writer", "toolName": "bash", "arguments": {"command": command}}},
                    {"type": "tool.execution_complete", "id": "early-writer-complete", "timestamp": "1-0b",
                     "data": {"toolCallId": "early-writer", "success": True, "result": {"content": "{}"}}},
                ]
                self.insert_before_guidance(events, mutation)
                observation = recovery_event_errors(events, root)[0]
                self.assertTrue(observation["errors"], command)
                self.assertEqual(observation["first_substantive_work"], "early-writer", command)

    def test_malformed_checkpoint_read_action_preserves_error_packet(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, initial = self.analyzer_fixture(Path(temp))
            workspace = evaluation / "workspace"
            malformed = [
                {"operation": "read", "path": str(workspace / "corpus/shard-003.txt"), "view_range": None},
                {"operation": "read", "path": None, "view_range": [61, 80]},
                {"operation": "read", "path": [], "view_range": [61, 80]},
                {"operation": "read", "path": str(workspace / "corpus/shard-003.txt"), "view_range": [0, 80]},
                {"operation": "read", "path": str(workspace / "corpus/shard-003.txt"), "view_range": [61, True]},
            ]
            for action in malformed:
                events = self.semantic_events(workspace)
                for event in events:
                    if event["type"] == "session.context_cleared":
                        event["timestamp"] = f"{event['id'][-1]}--clear"
                response = next(e for e in events if e.get("id") == "complete-1-0")
                response["data"]["result"]["content"] = json.dumps({"content": json.dumps({"next_action": action})})
                report = self.run_analyzer(evaluation, state, initial[:5] + events)
                self.assertTrue(report["semantic_recovery_validation"][0]["errors"], action)
                self.assertTrue((evaluation / "judge-packet.json").exists())

    def test_transition_counts_reject_boolean_and_float_values(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            root = evaluation / "workspace"
            checkpoint = json.loads(events[1]["data"]["arguments"]["content"])
            for count in (True, 1.0):
                bad = dict(checkpoint, transition_number=count)
                self.assertTrue(checkpoint_errors(bad, root), count)
            witness = dict(checkpoint, transitions_completed=2,
                           trigger_measurements=[checkpoint["trigger_measurements"][0],
                                                 self.full_measurement(2)],
                           declaration="NOT-JUDGED; parent remains Active", unexercised=["adverse cases"])
            del witness["transition_number"]
            self.assertEqual(witness_errors(witness, root), [])
            self.assertTrue(witness_errors(dict(witness, transitions_completed=2.0), root))

    def test_witness_path_mention_is_not_a_write_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            events = self.semantic_events(root)
            target = str(root / "witness.json")
            start = next(e for e in events if e.get("id") == "2-4")
            start["data"]["arguments"]["command"] = f"printf '%s' '{target}' > unrelated.txt"
            self.assertTrue(recovery_event_errors(events, root)[1]["errors"])
            # A later genuine write cannot repair the first substantive action.
            later_start = copy.deepcopy(start)
            later_start["id"] = "later-write"
            later_start["timestamp"] = "2-6-0"
            later_start["data"]["toolCallId"] = "later-write"
            later_start["data"]["arguments"]["command"] = f"cat > '{target}' <<'EOF'\n{{}}\nEOF"
            events.extend([later_start, {"type": "tool.execution_complete", "timestamp": "2-6-1",
                                        "data": {"toolCallId": "later-write", "success": True}}])
            self.assertTrue(recovery_event_errors(events, root)[1]["errors"])

    def analyzer_fixture(self, base):
        evaluation = base / "eval"
        workspace = evaluation / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "AGENTS.md").write_text("Literal mode: stop on errors.\nRefresh guidance and owning skill.\n")
        (workspace / "corpus").mkdir()
        (workspace / "corpus/shard-000.txt").write_text("record=00000 severity=0\nrecord=00001 severity=1\n")
        (workspace / "corpus/shard-003.txt").write_text("".join(f"record={n}\n" for n in range(80)))
        task = {key: key for key in ("original_objective", "parent_gate", "current_slice",
                                     "unresolved_user_requests", "decisions", "live_references")}
        (workspace / "task.json").write_text(json.dumps(task))
        document = dict(task, session_id="root-fixture", workspace=str(workspace), transition_number=1,
                        trigger_measurements=[self.full_measurement(1)],
                        audit_cursor={"path": "corpus/shard-000.txt", "last_line": 1, "last_record": 0,
                                      "last_severity": 0, "next_path": "corpus/shard-000.txt", "next_line": 2},
                        recovery_prerequisites=RECOVERY_PREREQUISITES,
                        next_action={"operation": "read", "path": str(workspace / "corpus/shard-000.txt"),
                                     "view_range": [2, 2]})
        content = json.dumps(document)
        binding = {"agent_session_id": "root-fixture", "memory_scope_id": "scope-fixture",
                   "revision": 1, "source_window": 0}
        events = [
            {"type": "user.message", "id": "initial-request", "timestamp": "0-0", "data": {
                "content": "Execute the bounded native CLI continuity witness"}},
            {"type": "tool.execution_start", "id": "write-id", "timestamp": "0-1",
             "data": {"toolCallId": "write", "toolName": "session_artifacts", "model": "model-fixture",
                      "arguments": {"operation": "write", "path": "checkpoint.md", "content": content}}},
            {"type": "tool.execution_complete", "id": "write-complete", "timestamp": "0-2",
             "data": {"toolCallId": "write", "success": True,
                      "result": {"content": json.dumps(dict(binding, operation="create"))}}},
            {"type": "tool.execution_start", "id": "read-id", "timestamp": "0-3",
             "data": {"toolCallId": "read", "toolName": "session_artifacts",
                      "arguments": {"operation": "read", "path": "checkpoint.md"}}},
            {"type": "tool.execution_complete", "id": "read-complete", "timestamp": "0-4",
             "data": {"toolCallId": "read", "success": True,
                      "result": {"content": json.dumps(dict(binding, history_position="write-id", content=content))}}},
            {"type": "session.context_cleared", "id": "clear-1", "timestamp": "0-5", "data": {}},
            {"type": "session.context_cleared", "id": "clear-2", "timestamp": "1-5", "data": {}},
        ]
        (evaluation / "session").mkdir()
        (evaluation / "session/context.json").write_text("{}")
        state = evaluation / "driver-state.json"
        state.write_text(json.dumps({"session_id": "root-fixture", "workspace": str(workspace)}))
        self.provisional_baseline(workspace)
        return evaluation, state, events

    def test_copy_target_directory_options_do_not_credit_witness_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = str(root / "witness.json")
            for option in ("--target-directory=elsewhere", "-telsewhere", "-ftelsewhere", "--parents"):
                events = self.semantic_events(root)
                start = next(e for e in events if e.get("id") == "2-4")
                start["data"]["arguments"]["command"] = f"cp {option} source.txt '{target}'"
                self.assertTrue(recovery_event_errors(events, root)[1]["errors"], option)
            events = self.semantic_events(root)
            start = next(e for e in events if e.get("id") == "2-4")
            start["data"]["arguments"]["command"] = f"cp source.txt '{target}'"
            self.assertFalse(recovery_event_errors(events, root)[1]["errors"])

    def run_analyzer(self, evaluation, state, events, captured=False):
        event_path = evaluation / "session/events.jsonl"
        event_path.write_text("".join(json.dumps(e) + "\n" for e in events))
        if captured:
            root_info, file_info = event_path.parent.stat(), event_path.stat()
            binding = {
                "root": {"device": root_info.st_dev, "inode": root_info.st_ino},
                "files": {"events.jsonl": {"device": file_info.st_dev, "inode": file_info.st_ino,
                                         "sha256": hashlib.sha256(event_path.read_bytes()).hexdigest()}},
            }
            (evaluation / "export-errors.json").write_text(json.dumps({
                "status": "captured", "errors": [], "snapshot_bindings": {"session": binding},
            }))
        with patch.object(controller, "EVAL", evaluation), patch.object(controller, "STATE", state), \
                patch.object(controller, "BASELINE", TEST_SEEDS[str(evaluation / "workspace")]), \
                redirect_stdout(io.StringIO()):
            controller.analyze()
        return json.loads((evaluation / "observed-evidence.json").read_text())

    def test_readback_requires_success_identity_and_bindings(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            good = self.run_analyzer(evaluation, state, events)
            self.assertTrue(good["checkpoint_contract_observations"][0]["pre_clear_content_readback"])
            mutations = [
                ("failed-read", lambda e: e[4]["data"].update(success=False)),
                ("failed-write", lambda e: e[2]["data"].update(success=False)),
                ("wrong-artifact", lambda e: e[3]["data"]["arguments"].update(path="another.md")),
                ("wrong-owner", lambda e: self.change_read_binding(e, "agent_session_id", "other-owner")),
                ("wrong-scope", lambda e: self.change_read_binding(e, "memory_scope_id", "other-scope")),
                ("wrong-revision", lambda e: self.change_read_binding(e, "revision", 2)),
                ("wrong-window", lambda e: self.change_read_binding(e, "source_window", 1)),
                ("wrong-history", lambda e: self.change_read_binding(e, "history_position", "unrelated-write")),
                ("missing-binding", lambda e: self.change_read_binding(e, "revision", None)),
                ("boolean-revision", lambda e: self.change_read_binding(e, "revision", True)),
            ]
            for label, mutate in mutations:
                bad = copy.deepcopy(events)
                mutate(bad)
                report = self.run_analyzer(evaluation, state, bad)
                self.assertFalse(report["checkpoint_contract_observations"][0]["pre_clear_content_readback"], label)
            missing_write_binding = copy.deepcopy(events)
            response = json.loads(missing_write_binding[2]["data"]["result"]["content"])
            del response["revision"]
            missing_write_binding[2]["data"]["result"]["content"] = json.dumps(response)
            self.assertFalse(self.run_analyzer(evaluation, state, missing_write_binding)
                             ["checkpoint_contract_observations"][0]["pre_clear_content_readback"])

    def change_read_binding(self, events, key, value):
        payload = json.loads(events[4]["data"]["result"]["content"])
        if value is None:
            del payload[key]
        else:
            payload[key] = value
        events[4]["data"]["result"]["content"] = json.dumps(payload)

    def test_failed_or_malformed_results_still_persist_packet(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            for name, failed in (("get_context_remaining", True), ("get_context_remaining", False),
                                 ("session_artifacts", True), ("session_artifacts", False)):
                bad = copy.deepcopy(events)
                if name == "get_context_remaining":
                    bad[1:1] = [
                        {"type": "tool.execution_start", "id": "measurement", "timestamp": "0-0a",
                         "data": {"toolCallId": "measurement", "toolName": name, "arguments": {}}},
                        {"type": "tool.execution_complete", "id": "measurement-complete", "timestamp": "0-0b",
                         "data": {"toolCallId": "measurement", "success": not failed,
                                  "result": {"content": "Plain-text policy denial"}}},
                    ]
                else:
                    bad[4]["data"].update(success=not failed, result={"content": "Plain-text read denial"})
                report = self.run_analyzer(evaluation, state, bad)
                self.assertTrue(report["evidence_errors"])
                self.assertEqual(report["context_measurements"], [])
                self.assertEqual(report["checkpoint_contract_observations"][0]["pre_clear_content_readback"],
                                 name == "get_context_remaining")
                self.assertTrue((evaluation / "judge-packet.json").exists())

    def test_child_activity_cannot_supply_root_window_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            evaluation, state, events = self.analyzer_fixture(Path(temp))
            child = copy.deepcopy(events)
            for e in child:
                e["agentId"] = "child-fixture"
            report = self.run_analyzer(evaluation, state, child)
            self.assertEqual(report["root_clear_events"], [])
            self.assertEqual(report["context_measurements"], [])
            self.assertEqual(report["native_artifact_and_terminal_calls"], [])
            self.assertTrue(report["non_root_activity"])
            self.assertTrue(report["semantic_recovery_validation"][-1]["errors"])
            # Data-level ownership markers are also excluded consistently.
            semantic = self.semantic_events(evaluation / "workspace")
            next(e for e in semantic if e.get("id") == "clear-2")["data"] = {"agentId": "child-fixture"}
            self.assertTrue(recovery_event_errors(semantic, evaluation / "workspace")[-1]["errors"])


if __name__ == "__main__":
    unittest.main()
