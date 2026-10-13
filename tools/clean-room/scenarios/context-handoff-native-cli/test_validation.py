import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import controller

from validation import (
    RECOVERY_PREREQUISITES, action_errors, checkpoint_errors, cursor_errors,
    pressure_events, recovery_event_errors, witness_errors,
)


class ValidationTests(unittest.TestCase):
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
            cursor = {"path": "corpus/shard-021.txt", "last_line": 34, "last_record": 1713,
                      "last_severity": 1, "next_path": "corpus/shard-021.txt", "next_line": 35}
            self.assertEqual(cursor_errors(cursor, root), [])
            wrong = dict(cursor, next_line=36)
            self.assertTrue(cursor_errors(wrong, root))
            at_1714 = dict(cursor, last_line=35, last_record=1714, last_severity=2, next_line=36)
            self.assertEqual(cursor_errors(at_1714, root), [])
            self.assertTrue(cursor_errors(dict(at_1714, last_line=36, next_line=37), root))
            action = {"operation": "read", "path": str(root / cursor["path"]), "view_range": [35, 60]}
            self.assertEqual(action_errors(action, cursor, 1, str(root)), [])
            for bad in ("Call new_context now; then read", [action], dict(action, then="new_context"),
                        {"operation": "new_context"}, dict(action, view_range=[36, 60])):
                self.assertTrue(action_errors(bad, cursor, 1, str(root)))

    def test_witness_required_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "corpus").mkdir()
            (root / "corpus/shard-000.txt").write_text("record=00000 severity=0\nrecord=00001 severity=1\n")
            task = {key: key for key in ("original_objective", "parent_gate", "current_slice",
                                         "unresolved_user_requests", "decisions", "live_references")}
            measurements = [{"context_info": {"promptTokenLimit": 200000}, "fraction_remaining": .35,
                             "context_window": {"number": n}} for n in (1, 2)]
            doc = dict(task, workspace=str(root), session_id="root", transitions_completed=2,
                       trigger_measurements=measurements, declaration="NOT-JUDGED; parent remains Active",
                       audit_cursor={"path": "corpus/shard-000.txt", "last_line": 1, "last_record": 0,
                                     "last_severity": 0, "next_path": "corpus/shard-000.txt", "next_line": 2},
                       unexercised=["force threshold"])
            self.assertEqual(witness_errors(doc, root, task), [])
            for key in ("workspace", "session_id", "audit_cursor", "trigger_measurements", "parent_gate"):
                bad = copy.deepcopy(doc)
                del bad[key]
                self.assertTrue(witness_errors(bad, root, task), key)
            bad = dict(doc, trigger_measurements=measurements[1:])
            self.assertTrue(witness_errors(bad, root, task))
            self.assertTrue(witness_errors(dict(doc, checkpoint_fidelity="perfect"), root, task))
            cp = dict(doc, transition_number=2,
                      recovery_prerequisites=RECOVERY_PREREQUISITES,
                      next_action={"operation": "write_witness", "path": str(root / "witness.json")})
            self.assertEqual(checkpoint_errors(cp, root, task), [])

    def semantic_events(self, root, bash_reads=False):
        (root / "AGENTS.md").write_text("Literal mode: stop on errors.\nRefresh guidance and owning skill.\n")
        (root / "corpus").mkdir(exist_ok=True)
        (root / "corpus/shard-003.txt").write_text("".join(f"record={n}\n" for n in range(80)))
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
        return events

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
                next(e for e in bad if e.get("id") == f"complete-{delayed}")["timestamp"] = "1-9-1"
                self.assertTrue(any(row["errors"] for row in recovery_event_errors(bad, root)))

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
        (workspace / "AGENTS.md").write_text("Literal fixture guidance.\n")
        (workspace / "corpus").mkdir()
        (workspace / "corpus/shard-000.txt").write_text("record=00000 severity=0\nrecord=00001 severity=1\n")
        task = {key: key for key in ("original_objective", "parent_gate", "current_slice",
                                     "unresolved_user_requests", "decisions", "live_references")}
        (workspace / "task.json").write_text(json.dumps(task))
        document = dict(task, session_id="root-fixture", workspace=str(workspace), transition_number=1,
                        trigger_measurements=[{"context_info": {"promptTokenLimit": 200000},
                                               "fraction_remaining": .35, "context_window": {"number": 1}}],
                        audit_cursor={"path": "corpus/shard-000.txt", "last_line": 1, "last_record": 0,
                                      "last_severity": 0, "next_path": "corpus/shard-000.txt", "next_line": 2},
                        recovery_prerequisites=RECOVERY_PREREQUISITES,
                        next_action={"operation": "read", "path": str(workspace / "corpus/shard-000.txt"),
                                     "view_range": [2, 2]})
        content = json.dumps(document)
        binding = {"agent_session_id": "root-fixture", "memory_scope_id": "scope-fixture",
                   "revision": 1, "source_window": 0}
        events = [
            {"type": "user.message", "timestamp": "0-0", "data": {
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

    def run_analyzer(self, evaluation, state, events):
        (evaluation / "session/events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
        with patch.object(controller, "EVAL", evaluation), patch.object(controller, "STATE", state), \
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
