"""Negative controls for the artifact verifier, separate from the real installer run."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "fresh-installer-failure"
SPEC = importlib.util.spec_from_file_location("installer_failure_verify", SCENARIO / "verify.py")
assert SPEC and SPEC.loader
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)
COMMIT = "a" * 40
GENERATION = f"1.2.3+source.{COMMIT}.{'b' * 16}"
OPTION_ERROR = (
    "error: the argument `UV_OFFLINE` (environment variable) cannot be used with `--refresh`"
)
FEED_ERROR = (
    "error: Failed to fetch: https://packagefeedproxy.microsoft.io/pypi/simple/setuptools/\n"
    "  cause: Connection refused (os error 111)"
)


class FailureVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.logs = self.root / "logs"
        self.runtime = self.root / "runtime"
        self.logs.mkdir()
        self.runtime.mkdir()
        self.write("installer.exit", "1")
        self.write("installer.pid", "123")
        self.write("source-install.log", f"[OK]   Venv created\n{FEED_ERROR}\n")
        self.write("prepared-source.json", json.dumps({
            "identity": {
                "source_commit": COMMIT,
                "runtime_version": GENERATION,
                "package_version": "1.2.3",
            },
        }))
        self.events = [
            {
                "service": "agent-bridge", "action": "install", "outcome": outcome,
                "pid": 123, "version": version, "node": "fixture",
                "ts": f"2026-01-01T00:00:0{index}+00:00",
            }
            for index, (outcome, version) in enumerate(
                (("begin", "1.2.3"), ("fail", GENERATION))
            )
        ]

    def write(self, name: str, content: str) -> None:
        (self.logs / name).write_text(content, encoding="utf-8")

    def check(self, plugin: str = "agent-bridge") -> dict[str, object]:
        self.write("lifecycle.log", "".join(json.dumps(event) + "\n" for event in self.events))
        return VERIFIER.verify(self.logs, self.runtime, plugin, COMMIT)

    def test_truthful_failure_accepts_cleaned_or_incomplete_unpublished_candidate(self) -> None:
        result = self.check()
        self.assertEqual(result["installer_pid"], 123)
        self.assertEqual(result["installer_exit"], 1)
        self.assertEqual(result["failure_kind"], "dependency-feed-unreachable")
        self.assertEqual(result["lifecycle_outcomes"], ["begin", "fail"])
        self.assertIs(result["published_runtime"], False)
        candidate = self.runtime / "versions" / GENERATION
        candidate.mkdir(parents=True)
        (candidate / "pyvenv.cfg").write_text("incomplete", encoding="utf-8")
        self.assertEqual(self.check(), result)

    def test_success_timeout_launch_failure_and_signals_are_not_proof(self) -> None:
        for status in (0, 124, 125, 126, 127, 137, 143, -9):
            with self.subTest(status=status):
                self.write("installer.exit", str(status))
                with self.assertRaises(AssertionError):
                    self.check()

    def test_unrelated_or_interpreter_failure_is_not_proof(self) -> None:
        for output in (
            "No interpreter found",
            FEED_ERROR,
            f"[OK]   Venv created\n{OPTION_ERROR}",
            "[OK]   Venv created\nNo solution found\nnetwork is disabled",
            "[OK]   Venv created\nmalformed runtime-source.json",
        ):
            with self.subTest(output=output):
                self.write("source-install.log", output)
                with self.assertRaises(AssertionError):
                    self.check()

    def test_wrong_lifecycle_identity_order_or_terminal_outcome_is_not_proof(self) -> None:
        for key, value in (
            ("pid", 124), ("pid", "123"), ("pid", True), ("service", "agent-dispatch"),
            ("action", "update"), ("outcome", "ok"), ("version", "1.2.3"),
            ("node", ""), ("ts", "2026-01-01T00:00:01"),
        ):
            with self.subTest(key=key, value=value):
                original = self.events[1][key]
                self.events[1][key] = value
                with self.assertRaises(AssertionError):
                    self.check()
                self.events[1][key] = original
        self.events.reverse()
        with self.assertRaises(AssertionError):
            self.check()

    def test_missing_extra_and_malformed_lifecycle_records_are_not_proof(self) -> None:
        original = self.events[:]
        for events in ([], original[:1], original + original[:1]):
            with self.subTest(events=events):
                self.events = events
                with self.assertRaises(AssertionError):
                    self.check()
        self.write("lifecycle.log", '{"outcome":')
        with self.assertRaises(json.JSONDecodeError):
            VERIFIER.verify(self.logs, self.runtime, "agent-bridge", COMMIT)

    def test_any_publication_including_dangling_selection_or_other_complete_slot_fails(self) -> None:
        for name in (
            "current-version", "last-known-good", "deploy-manifest.json",
            "active.json", "run/endpoint.json", "running-version.json", "venv",
            "versions/other/.install-complete.json",
        ):
            with self.subTest(name=name):
                path = self.runtime / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(self.root / "nonexistent")
                with self.assertRaises(AssertionError):
                    self.check()
                path.unlink()

    def test_dispatch_rendezvous_file_or_dangling_advertisement_is_not_absence(self) -> None:
        for event in self.events:
            event["service"] = "agent-dispatch"
        self.assertIs(self.check("agent-dispatch")["published_runtime"], False)
        endpoint = self.runtime / "run" / "endpoint.json"
        endpoint.parent.mkdir()
        endpoint.write_text('{"transport":"tcp","port":12345}', encoding="utf-8")
        with self.assertRaises(AssertionError):
            self.check("agent-dispatch")
        endpoint.unlink()
        endpoint.symlink_to(self.root / "absent-endpoint")
        with self.assertRaises(AssertionError):
            self.check("agent-dispatch")

    def test_prepared_identity_must_match_the_pinned_commit(self) -> None:
        with self.assertRaises(AssertionError):
            self.write("lifecycle.log", "\n".join(json.dumps(event) for event in self.events))
            VERIFIER.verify(self.logs, self.runtime, "agent-bridge", "c" * 40)

    def test_manifest_never_borrows_credentials_and_declares_only_linux_pair(self) -> None:
        manifest = json.loads((SCENARIO / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["auth"], {"copilot": "none", "gh": "none", "ado": "none"})
        self.assertEqual(manifest["tier"], "P")
        scenario = (SCENARIO / "scenario.sh").read_text(encoding="utf-8")
        for required in (
            "UV_PYTHON_DOWNLOADS=never", "--no-service --no-supervisor",
            'addresses == {"127.0.0.1"}', "UV_HTTP_RETRIES=0",
            'exec bash "$@"', 'printf "%s\\n" "$$"', "timeout 120",
            '"$CHECKOUT/tools/prepare_runtime_source.py"',
        ):
            self.assertIn(required, scenario)


if __name__ == "__main__":
    unittest.main()
