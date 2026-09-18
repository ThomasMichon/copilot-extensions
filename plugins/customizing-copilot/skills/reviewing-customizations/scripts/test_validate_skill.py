"""Standard-library regressions; COPILOT_SKILL_LIVE_TESTS=1 adds offline CLI probes."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


PLUGIN = Path(__file__).resolve().parents[3]
SCRIPT = Path(__file__).with_name("validate_skill.py")
SPEC = importlib.util.spec_from_file_location("copilot_validate_skill", SCRIPT)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="copilot-skill-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.fixture("example", "---\nname: example\ndescription: Demo\n---\nBody\n")

    def fixture(self, name, content):
        path = self.root / "candidates" / name / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return path

    def codes(self, report):
        return {
            issue["code"]
            for issue in report["issues"] + [
                issue for candidate in report["candidates"] for issue in candidate["issues"]
            ]
        }


class ValidateSkillTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.list_response = None
        self.version_response = subprocess.CompletedProcess([], 0, "GitHub Copilot CLI 1.0.83.\n", "")
        self.which = mock.patch.object(validator.shutil, "which", return_value=str(self.root / "copilot.exe")).start()
        self.run = mock.patch.object(validator.subprocess, "run", side_effect=self.runner).start()
        self.addCleanup(mock.patch.stopall)

    def runner(self, command, **kwargs):
        cwd = Path(kwargs["cwd"])
        files = sorted(cwd.rglob("SKILL.md"))
        self.calls.append((command, kwargs, files))
        if command[-1] == "--version":
            return self.version_response
        records = [
            {"name": "example", "description": "Demo", "source": "project",
             "path": str(path.parent), "enabled": True}
            for path in files
        ]
        if self.list_response:
            return self.list_response(command, kwargs, records)
        return subprocess.CompletedProcess(command, 0, json.dumps(records), "")

    def response(self, records=None, stderr="", code=0, stdout=None):
        self.list_response = lambda command, kwargs, defaults: subprocess.CompletedProcess(
            command, code, json.dumps(defaults if records is None else records) if stdout is None else stdout, stderr,
        )

    def validate(self, paths=None, **kwargs):
        return validator.validate([self.path] if paths is None else paths, **kwargs)

    def assert_cleaned(self):
        for _, kwargs, _ in self.calls:
            self.assertFalse(Path(kwargs["cwd"]).parent.exists())

    def test_valid_exact_path_version_and_clean_cleanup(self):
        report = self.validate()
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["cli"]["version"], "1.0.83")
        candidate = report["candidates"][0]
        self.assertTrue(candidate["runtime_accepted"])
        self.assertEqual(candidate["loaded"]["path"], candidate["staged_path"])
        self.assertEqual(candidate["sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(validator.exit_code(report), 0)
        self.which.assert_called_once_with("copilot")
        self.assert_cleaned()

    def test_directory_and_multiple_paths_use_one_inventory(self):
        other = self.fixture("another", b"arbitrary unparsed input\n")
        report = self.validate([self.path.parent, other])
        self.assertEqual(report["status"], "valid")
        self.assertEqual(len(report["candidates"]), 2)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1][0][-3:], ["skill", "list", "--json"])
        self.assert_cleaned()

    def test_non_skill_missing_file_and_empty_arguments(self):
        other = self.path.with_name("README.md")
        other.write_text("Not a skill", encoding="utf-8")
        missing = self.root / "empty"; missing.mkdir()
        for path in (other, missing, self.root / "missing" / "SKILL.md"):
            with self.subTest(path=path):
                report = self.validate([path])
                self.assertEqual(report["status"], "invalid")
                self.assertIn("skill-file", self.codes(report))
        self.assertEqual(self.validate([])["status"], "blocked")
        self.run.assert_not_called()

    def test_exit_zero_without_exact_candidate_is_invalid(self):
        builtin = {"name": "example", "description": "Demo", "source": "builtin",
                   "path": str(self.root / "not-the-candidate"), "enabled": True}
        for inventory in ([], [builtin]):
            with self.subTest(inventory=inventory):
                self.response(records=inventory)
                report = self.validate()
                self.assertEqual(report["status"], "invalid")
                self.assertFalse(report["candidates"][0]["runtime_accepted"])
                self.assertIn("candidate-not-loaded", self.codes(report))
        self.assert_cleaned()

    def test_omission_reports_collision_not_a_syntax_diagnosis(self):
        other = self.fixture("same-name-other-folder", b"anything")
        self.list_response = lambda cmd, kwargs, records: subprocess.CompletedProcess(cmd, 0, json.dumps(records[:1]), "")
        report = self.validate([self.path, other])
        self.assertEqual(report["status"], "invalid")
        issue = report["candidates"][1]["issues"][0]
        self.assertIn("duplicate name", issue["message"])
        self.assertNotIn("syntax", issue["message"])

    def test_diagnostics_even_with_valid_inventory_fail_closed(self):
        self.response(stderr="Warning: something failed\n")
        report = self.validate()
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(report["candidates"][0]["runtime_accepted"])
        self.assertIn("cli-diagnostics", self.codes(report))
        self.assertEqual(report["cli"]["list_stderr"], "Warning: something failed\n")

    def test_disabled_or_wrong_source_is_not_acceptance(self):
        for changes in ({"enabled": False}, {"source": "user"}, {"source": "builtin"}):
            with self.subTest(changes=changes):
                def changed(cmd, kwargs, records):
                    records[0].update(changes)
                    return subprocess.CompletedProcess(cmd, 0, json.dumps(records), "")
                self.list_response = changed
                report = self.validate()
                self.assertEqual(report["status"], "invalid")
                self.assertIn("candidate-not-enabled-project", self.codes(report))

    def test_invalid_json_and_unknown_schema_are_blocked(self):
        for stdout in ("", "log\n[]", "{", "{}", "null", "[null]", '[{"name":"a"}]', "[] []"):
            with self.subTest(stdout=stdout):
                self.response(stdout=stdout)
                report = self.validate()
                self.assertEqual(report["status"], "blocked")
                self.assertIsNone(report["candidates"][0]["runtime_accepted"])
        for changes in ({"name": 1}, {"description": None}, {"enabled": 1}, {"path": "relative"}, {"source": []}):
            with self.subTest(changes=changes):
                def changed(cmd, kwargs, records):
                    records[0].update(changes)
                    return subprocess.CompletedProcess(cmd, 0, json.dumps(records), "")
                self.list_response = changed
                self.assertEqual(self.validate()["status"], "blocked")
        self.assert_cleaned()

    def test_duplicate_path_or_unexpected_external_skill_blocks(self):
        for duplicate in (True, False):
            with self.subTest(duplicate=duplicate):
                def changed(cmd, kwargs, records):
                    extra = dict(records[0])
                    if not duplicate:
                        extra["path"] = str(self.root / "ambient")
                    return subprocess.CompletedProcess(cmd, 0, json.dumps([*records, extra]), "")
                self.list_response = changed
                self.assertEqual(self.validate()["status"], "blocked")

    def test_subprocess_failure_missing_executable_and_timeout(self):
        self.which.return_value = None
        self.assertIn("cli-unavailable", self.codes(self.validate()))
        self.run.assert_not_called()
        self.which.return_value = str(self.root / "copilot.exe")
        for error in (FileNotFoundError("gone"), PermissionError("denied"), subprocess.TimeoutExpired("copilot", 1)):
            with self.subTest(error=error):
                def fail(cmd, kwargs, records):
                    raise error
                self.list_response = fail
                report = self.validate(timeout=1)
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(validator.exit_code(report), 2)
                self.assert_cleaned()
        self.response(code=7, stderr="failed")
        report = self.validate()
        self.assertIn("cli-exit", self.codes(report))
        self.assertEqual(report["cli"]["list_exit_code"], 7)

    def test_version_required_and_recognizable(self):
        for code, out, err in ((0, "other tool 1.2.3\n", ""), (1, "GitHub Copilot CLI 1.0.83.\n", ""),
                               (0, "", ""), (0, "GitHub Copilot CLI 1.0.83.\n", "warning")):
            with self.subTest(code=code, out=out, err=err):
                self.calls.clear()
                self.version_response = subprocess.CompletedProcess([], code, out, err)
                report = self.validate()
                self.assertEqual(report["status"], "blocked")
                self.assertEqual(len(self.calls), 1)
                self.assertIn("cli-version", self.codes(report))
                self.assert_cleaned()

    def test_original_change_or_deletion_rejects_stale_result(self):
        for deleted in (False, True):
            with self.subTest(deleted=deleted):
                self.path.write_text("initial", encoding="utf-8")
                def change(cmd, kwargs, records):
                    if deleted:
                        self.path.unlink()
                    else:
                        self.path.write_text("changed", encoding="utf-8")
                    return subprocess.CompletedProcess(cmd, 0, json.dumps(records), "")
                self.list_response = change
                report = self.validate()
                self.assertEqual(report["status"], "blocked")
                self.assertIsNone(report["candidates"][0]["runtime_accepted"])
                self.assertIn("original-changed", self.codes(report))
                self.assert_cleaned()

    def test_original_change_during_failed_or_timed_out_run_is_reported(self):
        def change(cmd, kwargs, records):
            self.path.write_text("changed", encoding="utf-8")
            raise subprocess.TimeoutExpired(cmd, 1)
        self.list_response = change
        report = self.validate()
        self.assertIn("original-changed", self.codes(report))
        self.assertIn("cli-timeout", self.codes(report))
        self.assert_cleaned()

    def test_expected_description_exact_and_one_candidate_only(self):
        self.assertEqual(self.validate(expected_description="Demo")["status"], "valid")
        for expected in ("Demo ", "demo", "Demo # rest", ""):
            with self.subTest(expected=expected):
                report = self.validate(expected_description=expected, runtime_only=True)
                self.assertIn("description-mismatch", self.codes(report))
                self.assertEqual(report["status"], "invalid")
        self.calls.clear()
        report = self.validate([self.path, self.path], expected_description="Demo")
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(self.calls, [])

    def test_hygiene_is_separate_from_runtime_and_uses_utf16(self):
        cases = [
            ({"name": "Upper_Name"}, "name-kebab-case"),
            ({"name": "double--dash"}, "name-kebab-case"),
            ({"description": " \n"}, "description-empty"),
            ({"description": "Use <xml>"}, "description-angle-brackets"),
            ({"description": "bad\x00"}, "description-controls"),
            ({"description": "bad\x1b"}, "description-controls"),
            ({"description": "\ud800"}, "description-controls"),
            ({"description": "a" * 1025}, "description-budget"),
            ({"description": "\U0001f600" * 513}, "description-budget"),
        ]
        for changes, code in cases:
            with self.subTest(code=code, changes=repr(changes)[:80]):
                def changed(cmd, kwargs, records):
                    records[0].update(changes)
                    return subprocess.CompletedProcess(cmd, 0, json.dumps(records), "")
                self.list_response = changed
                report = self.validate()
                self.assertTrue(report["candidates"][0]["runtime_accepted"])
                self.assertEqual(report["status"], "invalid")
                self.assertIn(code, self.codes(report))
                self.assertEqual(self.validate(runtime_only=True)["status"], "valid")
        for description in ("Line one\nLine two\twith tab", "\U0001f600" * 512, "a" * 1024):
            with self.subTest(description=description[:30]):
                def changed(cmd, kwargs, records):
                    records[0]["description"] = description
                    return subprocess.CompletedProcess(cmd, 0, json.dumps(records), "")
                self.list_response = changed
                self.assertEqual(self.validate()["status"], "valid")

    def test_isolation_copies_only_identical_skill_bytes(self):
        content = b"\xef\xbb\xbf---\r\nname: example\r\ndescription: Demo\r\n---\r\n"
        self.path.write_bytes(content)
        (self.path.parent / "hooks.json").write_text("poison", encoding="utf-8")
        hooks = self.path.parent / ".github" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "poison.json").write_text("poison", encoding="utf-8")
        (self.path.parent / "companion.py").write_text("raise Exception('poison')", encoding="utf-8")
        poison = {
            "COPILOT_HOME": str(self.root / "poison-home"),
            "HOME": str(self.root / "poison-home"),
            "USERPROFILE": str(self.root / "poison-home"),
            "COPILOT_SKILLS_DIRS": str(self.path.parent),
            "COPILOT_SETTINGS": "poison", "COPILOT_AGENT_SESSION_ID": "parent",
            "AGENT_HOME": "poison", "AGENT_PLUGIN_PATH": "poison",
            "GH_TOKEN": "secret", "GITHUB_TOKEN": "secret", "AZURE_TOKEN": "secret",
            "COPILOT_GITHUB_TOKEN": "secret", "NODE_OPTIONS": "--require poison",
            "NODE_PATH": "poison", "HTTP_PROXY": "poison",
            "XDG_CONFIG_HOME": str(self.root / "poison-home"),
        }
        def inspect(cmd, kwargs, records):
            env = kwargs["env"]
            project = Path(kwargs["cwd"])
            root = project.parent
            self.assertFalse(root.is_relative_to(self.root))
            self.assertFalse(root.is_relative_to(Path.home().resolve()))
            self.assertEqual(
                [path for path in project.rglob("*") if path.is_file()],
                list(project.glob(".github/skills/*/*/SKILL.md")),
            )
            for file in project.rglob("SKILL.md"):
                self.assertEqual(file.read_bytes(), content)
                self.assertEqual(file.parent.name, self.path.parent.name)
                self.assertEqual(file.relative_to(project).parts[:2], (".github", "skills"))
            for key in poison:
                if key in {"COPILOT_HOME", "HOME", "USERPROFILE", "XDG_CONFIG_HOME"}:
                    self.assertTrue(Path(env[key]).is_relative_to(root))
                else:
                    self.assertNotIn(key, env)
            for key in ("APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR", "GH_CONFIG_DIR"):
                self.assertTrue(Path(env[key]).is_relative_to(root))
            self.assertEqual(env["PATH"], os.environ["PATH"])
            self.assertIn("--no-auto-update", cmd)
            self.assertEqual(cmd[cmd.index("--config-dir") + 1], env["COPILOT_HOME"])
            self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
            self.assertFalse(kwargs["shell"])
            self.assertEqual(kwargs["encoding"], "utf-8")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(records), "")
        self.list_response = inspect
        with mock.patch.dict(os.environ, poison):
            report = self.validate()
        self.assertEqual(report["status"], "valid", report)
        self.assert_cleaned()

    def test_unicode_and_shell_metacharacter_paths_are_literal(self):
        path = self.fixture("sp ace & $() ; ' [x] \u65e5\U0001f600", b"not interpreted")
        def inspect(cmd, kwargs, records):
            self.assertEqual(Path(records[0]["path"]).name, path.parent.name)
            self.assertNotIn(str(path), cmd)
            return subprocess.CompletedProcess(cmd, 0, json.dumps(records), "")
        self.list_response = inspect
        self.assertEqual(self.validate([path])["status"], "valid")
        self.assert_cleaned()

    def test_dotdot_path_does_not_escape_staging_group(self):
        child = self.path.parent / "child"; child.mkdir()
        report = self.validate([child / ".."])
        self.assertEqual(Path(report["candidates"][0]["staged_path"]).name, "example")
        self.assertEqual(report["status"], "valid")

    def test_large_file_is_not_truncated_before_runtime(self):
        content = b"x" * (1024 * 1024 + 1)
        self.path.write_bytes(content)
        def inspect(cmd, kwargs, records):
            self.assertEqual((Path(records[0]["path"]) / "SKILL.md").read_bytes(), content)
            return subprocess.CompletedProcess(cmd, 0, "[]", "too large")
        self.list_response = inspect
        self.assertEqual(self.validate()["status"], "invalid")
        self.assert_cleaned()

    def test_explicit_executable_and_repeated_prefix_are_literal(self):
        prefix = [str(self.root / "installed package & ()" / "index.js"), "literal argument"]
        report = self.validate(copilot="node", copilot_args=prefix)
        self.which.assert_called_once_with("node")
        self.assertEqual(report["cli"]["arguments"], prefix)
        for command, _, _ in self.calls:
            self.assertEqual(command[1:3], prefix)

    @unittest.skipUnless(os.name == "nt", "Windows batch launcher guard")
    def test_batch_launchers_are_refused_without_shell_fallback(self):
        self.which.return_value = str(self.root / "copilot.cmd")
        self.assertIn("shell-launcher", self.codes(self.validate()))
        self.run.assert_not_called()

    def test_timeout_values_are_bounded_and_finite(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(timeout=value):
                self.assertIn("timeout", self.codes(self.validate(timeout=value)))
        self.run.assert_not_called()

    def test_no_safe_temp_location_is_blocked(self):
        with mock.patch.object(validator, "_temporary_workspace", side_effect=OSError("No safe temporary workspace")):
            report = self.validate()
        self.assertEqual(report["status"], "blocked")
        self.run.assert_not_called()

    def test_temp_fallback_refuses_ancestor_config_after_home_override(self):
        home = self.root / "real-home"
        (home / ".claude").mkdir(parents=True)
        temp = home / "Temp"; temp.mkdir()
        new_home = self.root / "overridden-home"; new_home.mkdir()
        with mock.patch.dict(os.environ, {"HOME": str(new_home), "USERPROFILE": str(new_home)}), \
             mock.patch.object(validator.tempfile, "gettempdir", return_value=str(temp)), \
             mock.patch.object(validator.tempfile, "TemporaryDirectory", side_effect=OSError("unavailable")) as create:
            report = self.validate()
        self.assertEqual(report["status"], "blocked")
        self.assertIn("ancestor contains", report["issues"][0]["message"])
        self.assertFalse(any(Path(call.kwargs["dir"]) == temp for call in create.call_args_list))

    def test_parallel_calls_share_no_workspace_or_config(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            reports = list(pool.map(lambda _: self.validate(), range(2)))
        self.assertEqual([report["status"] for report in reports], ["valid", "valid"])
        paths = [report["candidates"][0]["staged_path"] for report in reports]
        self.assertNotEqual(paths[0], paths[1])
        configs = {kwargs["env"]["COPILOT_HOME"] for _, kwargs, _ in self.calls}
        self.assertEqual(len(configs), 2)
        self.assert_cleaned()

    def test_argparse_help_json_and_both_key_forms(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = validator.main([
                str(self.path), "--json", "--copilot=node",
                "--copilot-arg", "installed/index.js", "--copilot-arg=another",
                "--expect-description=Demo", "--timeout", "7", "--runtime-only",
            ])
        report = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(report["cli"]["arguments"], ["installed/index.js", "another"])
        self.assertEqual(report["policy"], "runtime-only")
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as caught:
            validator.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("--expect-description", stdout.getvalue())

    def test_human_output_displays_actual_loaded_metadata(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = validator.main([str(self.path), "--expect-description", "Demo # silently lost"])
        self.assertEqual(code, 1)
        self.assertIn('name: "example"', stdout.getvalue())
        self.assertIn('description: "Demo"', stdout.getvalue())
        self.assertIn("expectation/description-mismatch", stdout.getvalue())


@unittest.skipUnless(os.environ.get("COPILOT_SKILL_LIVE_TESTS") == "1", "Set COPILOT_SKILL_LIVE_TESTS=1 for actual installed CLI probes")
class LiveCopilotTests(Fixtures):
    """Observed 1.0.83 regressions, not a claim about all past or future versions."""

    def live(self, paths, **kwargs):
        report = validator.validate(paths, **kwargs)
        self.assertNotEqual(report["status"], "blocked", json.dumps(report, ensure_ascii=True))
        self.assertIsNotNone(report["cli"]["version"], report)
        for candidate in report["candidates"]:
            self.assertFalse(Path(candidate["staged_path"]).exists())
        return report

    def test_quoted_folded_and_silent_hash_truncation(self):
        quoted = self.fixture("quoted & $() [x] \u65e5\U0001f600", '---\nname: quoted\ndescription: "Use: a # literal"\n---\nBody\n')
        folded = self.fixture("folded", "---\nname: folded\ndescription: >-\n  Folded text\n  continues here.\n---\nBody\n")
        report = self.live([quoted, folded])
        self.assertEqual(report["status"], "valid", report)
        self.assertEqual([item["loaded"]["description"] for item in report["candidates"]],
                         ["Use: a # literal", "Folded text continues here."])
        truncated = self.fixture("hash", "---\nname: hash\ndescription: Visible # lost\n---\nBody\n")
        self.assertEqual(self.live([truncated])["status"], "valid")
        report = self.live([truncated], expected_description="Visible # lost")
        self.assertTrue(report["candidates"][0]["runtime_accepted"])
        self.assertEqual(report["candidates"][0]["loaded"]["description"], "Visible")
        self.assertIn("description-mismatch", self.codes(report))

    def test_native_type_duplicate_key_and_syntax_rejections(self):
        fields = {
            "colon": "description: Unquoted: colon",
            "duplicate": "description: One\ndescription: Two",
            "duplicate-name": "name: first\nname: second\ndescription: Demo",
            "name-number": "name: 42\ndescription: Demo",
            "name-null": "name: null\ndescription: Demo",
            "name-invalid": 'name: "bad/name"\ndescription: Demo',
            "description-number": "description: 42",
            "description-null": "description: null",
            "user-invocable": 'description: Demo\nuser-invocable: "true"',
            "disable-model-invocation": "description: Demo\ndisable-model-invocation: 1",
            "allowed-tools": "description: Demo\nallowed-tools: [view, 1]",
        }
        paths = []
        for name, content in fields.items():
            # These are fixed input fixtures, not a Python YAML implementation.
            prefix = "" if name.startswith("name-") or name == "duplicate-name" else f"name: {name}\n"
            paths.append(self.fixture(name, f"---\n{prefix}{content}\n---\nBody\n"))
        report = self.live(paths)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(report["cli"]["list_stderr"])
        for candidate in report["candidates"]:
            with self.subTest(path=candidate["path"], version=report["cli"]["version"]):
                self.assertFalse(candidate["runtime_accepted"], candidate)
                self.assertEqual(candidate["issues"][0]["code"], "candidate-not-loaded")

    def test_runtime_name_and_description_boundaries(self):
        cases = [
            ("name-64", "a" * 64, "Demo", True),
            ("name-65", "a" * 65, "Demo", False),
            ("description-1024", "description-1024", "a" * 1024, True),
            ("description-1025", "description-1025", "a" * 1025, False),
            ("emoji-512", "emoji-512", "\U0001f600" * 512, True),
            ("emoji-513", "emoji-513", "\U0001f600" * 513, False),
        ]
        paths = [
            self.fixture(folder, f"---\nname: {name}\ndescription: {json.dumps(description, ensure_ascii=False)}\n---\nBody\n")
            for folder, name, description, _ in cases
        ]
        report = self.live(paths)
        for candidate, (_, _, _, accepted) in zip(report["candidates"], cases):
            with self.subTest(path=candidate["path"], version=report["cli"]["version"]):
                self.assertEqual(candidate["runtime_accepted"], accepted, candidate)

    def test_permissive_runtime_is_not_authoring_policy(self):
        paths = [
            self.fixture("uppercase", '---\nname: UpperCase\ndescription: Demo\n---\nBody\n'),
            self.fixture("empty", '---\nname: empty\ndescription: ""\n---\nBody\n'),
            self.fixture("angles", '---\nname: angles\ndescription: "<tag> text"\n---\nBody\n'),
        ]
        report = self.live(paths)
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(all(candidate["runtime_accepted"] for candidate in report["candidates"]))
        self.assertTrue({"name-kebab-case", "description-empty", "description-angle-brackets"} <= self.codes(report))
        self.assertEqual(self.live(paths, runtime_only=True)["status"], "valid")
        fallback = self.fixture("fallback-folder", "---\n{}\n---\nFallback description\n\nInstructions.\n")
        report = self.live([fallback])
        self.assertEqual(report["status"], "valid", report)
        self.assertEqual(report["candidates"][0]["loaded"]["name"], "fallback-folder")

    def test_duplicate_names_are_never_silently_accepted(self):
        a = self.fixture("duplicate-a", "---\nname: duplicate\ndescription: Demo\n---\nBody\n")
        b = self.fixture("duplicate-b", "---\nname: duplicate\ndescription: Demo\n---\nBody\n")
        report = self.live([a, b])
        self.assertEqual(report["status"], "invalid")
        self.assertIn("candidate-not-loaded", self.codes(report))
        self.assertEqual(self.live([a])["status"], "valid")
        self.assertEqual(self.live([b])["status"], "valid")

    def test_payload_copy_first_use_and_poisoned_config_do_not_run_hooks(self):
        installed = self.root / "installed-plugins" / "local" / "customizing-copilot"
        shutil.copytree(PLUGIN, installed, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        script = installed / SCRIPT.relative_to(PLUGIN)
        poison_home = self.root / "poison-home"
        marker = self.root / "HOOK-MUST-NOT-RUN"
        hook = {
            "version": 1, "hooks": {"sessionStart": [{
                "type": "command",
                "bash": "touch " + shlex.quote(str(marker)),
                "powershell": "New-Item -ItemType File -Path '" + str(marker).replace("'", "''") + "' -Force",
            }]},
        }
        for directory in (self.path.parent / ".github" / "hooks", poison_home / ".copilot" / "hooks"):
            directory.mkdir(parents=True)
            (directory / "poison.json").write_text(json.dumps(hook), encoding="utf-8")
        settings = self.path.parent / ".github" / "copilot" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"hooks": hook["hooks"]}), encoding="utf-8")
        (poison_home / ".copilot" / "settings.json").write_text(
            json.dumps({"hooks": hook["hooks"]}), encoding="utf-8",
        )
        env = os.environ.copy()
        env.update(
            HOME=str(poison_home), USERPROFILE=str(poison_home),
            COPILOT_HOME=str(poison_home / ".copilot"),
            COPILOT_SKILLS_DIRS=str(self.path.parent),
            COPILOT_AGENT_SESSION_ID="poison-parent",
            AGENT_PLUGIN_PATH=str(self.path.parent),
            GH_TOKEN="poison-not-a-real-token", GITHUB_TOKEN="poison-not-a-real-token",
            NODE_OPTIONS="--require poison-must-not-load",
        )
        result = subprocess.run(
            [sys.executable, "-I", "-S", str(script), str(self.path), "--json"],
            cwd=self.root, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "valid", report)
        self.assertEqual(result.stderr, "")
        self.assertFalse(marker.exists())
        self.assertFalse(Path(report["candidates"][0]["staged_path"]).exists())


if __name__ == "__main__":
    unittest.main()
