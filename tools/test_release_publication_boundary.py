"""Execute prepared release-control snippets without network or repository writes.

Run through the bounded test supervisor, optionally with ``--workflow PATH``
and ``--base-branch-workflow PATH``. An explicit controller must implement the
new boundary. A legacy default controller is explicitly skipped, not qualified.
Passing these fixture tests is not live CI, artifact, or release qualification.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

from _promotion_workflow_test_support import api_response, qualification_response, unit_profile

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/validate-and-promote.yml"
BASE_WORKFLOW: Path | None = None
EXPLICIT = False
CANDIDATE, BASE, DEV, CONTROL, TREE, MERGED = (letter * 40 for letter in "abcdef")
MANIFEST = ".github/promotion-control.json"
CONTROLS = [".github/workflows/ci.yml", ".github/workflows/validate-and-promote.yml"]


def jobs(text: str) -> dict[str, str]:
    body = text.split("\njobs:\n", 1)[1]
    matches = list(re.finditer(r"^  ([a-z][a-z0-9-]*):\s*$", body, re.M))
    return {
        match[1]: body[match.start():matches[index + 1].start() if index + 1 < len(matches) else None]
        for index, match in enumerate(matches)
    }


def needs(body: str) -> set[str]:
    match = re.search(r"^    needs: *(\[[^\n]*\]|[a-z][a-z0-9-]*)?$", body, re.M)
    if not match:
        return set()
    if match[1]:
        return {part.strip() for part in match[1].strip("[]").split(",")}
    return set(re.findall(r"^      - ([a-z][a-z0-9-]*)$", body[match.end():].split("    runs-on:", 1)[0], re.M))


def step(body: str, identifier: str) -> str:
    blocks = re.split(r"(?=^      - )", body, flags=re.M)
    found = [
        block for block in blocks
        if re.search(rf"^        id: {re.escape(identifier)}$", block, re.M)
        or block.startswith(f"      - name: {identifier}\n")
    ]
    if len(found) != 1:
        raise AssertionError(f"expected exactly one workflow step: {identifier}")
    return found[0]


def script(block: str) -> str:
    lines = block.split("        run: |\n", 1)[1].splitlines()
    end = next((index for index, line in enumerate(lines)
                if line.strip() and not line.startswith("          ")), len(lines))
    return "\n".join(line[10:] for line in lines[:end]).rstrip()


def python_body(shell: str, marker: str | None = None) -> str:
    matches = re.findall(r"<<'PY'\s*\n(.*?)^PY\s*$", shell, re.M | re.S)
    if marker is not None:
        matches = [body for body in matches if marker in body]
    if len(matches) != 1:
        raise AssertionError("expected exactly one complete literal Python heredoc")
    ast.parse(matches[0])
    return matches[0]


def trusted_control(_path: Path) -> types.ModuleType:
    # Prepared YAML is input data; it must not select the Python helper authority.
    source = Path(__file__).resolve().with_name("promotion_control.py")
    module = types.ModuleType("boundary_trusted_control")
    module.__file__ = str(source)
    exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), module.__dict__)
    return module


class TrustedHarnessTests(unittest.TestCase):
    def test_standalone_prepared_yaml_does_not_require_an_adjacent_checkout_or_helper(self) -> None:
        with tempfile.TemporaryDirectory(prefix="prepared-control-yaml-") as temporary:
            staged = Path(temporary) / "standalone" / "base-branch-reminder.yml"
            staged.parent.mkdir()
            staged.write_bytes(b"name: prepared control data\n")
            control = trusted_control(staged)
            self.assertEqual(
                Path(control.__file__), Path(__file__).resolve().with_name("promotion_control.py"),
            )
            self.assertEqual(control.MANIFEST_PATH, MANIFEST)
            self.assertTrue(callable(control._paths))


@dataclass
class _ShellResult:
    returncode: int
    stdout: str
    stderr: str
    output: str


class _ShellFixture(unittest.TestCase):
    def shell(
        self, body: str, env: dict[str, str], *, timeout: float = 10,
    ) -> _ShellResult:
        bash = shutil.which("bash")
        git = shutil.which("git")
        child_env = {"PATH": os.defpath, **env}
        if os.name == "nt":
            native = Path(git).resolve().parent.parent / "bin/bash.exe" if git else None
            bash = str(native) if native is not None and native.is_file() else None
            child_env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
        if bash is None:
            self.skipTest("Native Bash (Git for Windows on Windows) is required for literal shell fixtures")
        with tempfile.TemporaryDirectory(prefix="literal-shell-") as directory:
            output = Path(directory) / "github-output"
            output.write_text("", encoding="utf-8")
            child_env["GITHUB_OUTPUT"] = output.as_posix()
            completed = subprocess.run(
                [bash, "--noprofile", "--norc", "-e"], input=body, text=True,
                capture_output=True, timeout=timeout, env=child_env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            return _ShellResult(
                completed.returncode, completed.stdout, completed.stderr,
                output.read_text(encoding="utf-8"),
            )


class ShellFixtureTests(_ShellFixture):
    def test_windows_shell_launch_is_native_and_consoleless(self) -> None:
        fake_os = types.SimpleNamespace(name="nt", defpath="synthetic-path",
                                        environ={"SYSTEMROOT": "synthetic-windows"})
        resolved = {"bash": "/wsl/bash.exe", "git": "/native/git/cmd/git.exe"}
        with (
            mock.patch.object(sys.modules[__name__], "os", fake_os),
            mock.patch("shutil.which", side_effect=resolved.get),
            mock.patch.object(Path, "resolve", lambda path, **kwargs: path),
            mock.patch.object(Path, "is_file", return_value=True),
            mock.patch.object(subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True),
            mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run,
        ):
            self.shell("exit 0", {}, timeout=5)
        argv = run.call_args.args[0]
        self.assertEqual(Path(argv[0]), Path("/native/git/bin/bash.exe"))
        self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)
        self.assertEqual(run.call_args.kwargs["env"]["SYSTEMROOT"], "synthetic-windows")
        self.assertEqual(run.call_args.kwargs["timeout"], 5)

    def test_output_uses_an_owned_file_and_preserves_process_channels(self) -> None:
        for returncode in (0, 7):
            with self.subTest(returncode=returncode):
                outputs = []

                def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                    output = Path(kwargs["env"]["GITHUB_OUTPUT"])
                    self.assertNotEqual(output.as_posix(), "/dev/stdout")
                    self.assertNotEqual(output.as_posix(), "/unowned/output")
                    self.assertTrue(output.is_file())
                    output.write_text("proceed=true\n", encoding="utf-8")
                    outputs.append(output)
                    return subprocess.CompletedProcess(argv, returncode, "notice\n", "diagnostic\n")

                with mock.patch("subprocess.run", side_effect=run):
                    completed = self.shell("exit 0", {"GITHUB_OUTPUT": "/unowned/output"}, timeout=5)
                self.assertEqual(completed.returncode, returncode)
                self.assertEqual(completed.stdout, "notice\n")
                self.assertEqual(completed.stderr, "diagnostic\n")
                self.assertEqual(completed.output, "proceed=true\n")
                self.assertEqual(len(outputs), 1)
                self.assertFalse(outputs[0].exists())


class PromotionJobAdmissionTests(_ShellFixture):
    def setUp(self) -> None:
        self.promote = jobs(WORKFLOW.read_text(encoding="utf-8"))["promote"]

    def test_declined_or_failed_gate_does_not_allocate_a_promote_runner(self) -> None:
        condition = re.search(r"^    if: (.+)$", self.promote, re.M)
        self.assertIsNotNone(condition)
        self.assertEqual(
            condition[1],
            "always() && needs.gate.result == 'success' && needs.gate.outputs.is_dev == 'true'",
        )
        parts = condition[1].split(" && ")
        self.assertEqual(parts.pop(0), "always()")
        comparisons = []
        for part in parts:
            comparison = re.fullmatch(r"(needs\.gate(?:\.outputs)?\.[a-z_]+) == '([a-z]+)'", part)
            self.assertIsNotNone(comparison)
            comparisons.append(comparison.groups())
        for gate, is_dev in itertools.product(
            ("success", "SUCCESS", "failure", "cancelled", "skipped", "", "unknown"),
            ("true", "True", "TRUE", "false", "", "unknown"),
        ):
            with self.subTest(gate=gate, is_dev=is_dev):
                values = {"needs.gate.result": gate, "needs.gate.outputs.is_dev": is_dev}
                # Actions string comparisons are case-insensitive.
                admitted = all(values[field].casefold() == expected for field, expected in comparisons)
                self.assertEqual(admitted, gate.casefold() == "success" and is_dev.casefold() == "true")

    def test_admitted_source_still_requires_every_validation_result(self) -> None:
        template = script(step(self.promote, "decide"))
        validation = [
            "full", "worktree-manager", "agent-index-service-linux",
            "agent-index-service-windows", "guards-full-sweep", "python-artifacts",
        ]
        if "needs.source-units.result" in template:
            validation = ["source-units", "python-artifacts"]
        fields = {"needs.gate.result": "success", "needs.gate.outputs.is_dev": "true"}
        if "needs.compose-candidate.outputs.composed" in template:
            fields["needs.compose-candidate.outputs.composed"] = "true"
        fields.update({f"needs.{name}.result": "success" for name in validation})
        scenarios = [
            ({}, True),
            *(
                ({f"needs.{name}.result": result}, False)
                for name, result in itertools.product(
                    validation, ("failure", "cancelled", "skipped", "", "unknown"),
                )
            ),
            ({"needs.gate.result": "SUCCESS"}, False),
            ({"needs.gate.outputs.is_dev": "True"}, False),
        ]
        if "needs.compose-candidate.outputs.composed" in fields:
            scenarios.append(({"needs.compose-candidate.outputs.composed": "false"}, False))
        for overrides, proceed in scenarios:
            with self.subTest(overrides=overrides):
                values = {**fields, **overrides}
                command = re.sub(
                    r"\$\{\{\s*([^{}]+?)\s*\}\}",
                    lambda match: values[match[1].strip()],
                    template,
                )
                completed = self.shell("set -u\n" + command, {}, timeout=5)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                decisions = completed.output.splitlines()
                self.assertEqual(decisions, [f"proceed={str(proceed).lower()}"])
                if not proceed and not overrides.keys() & {
                    "needs.gate.result", "needs.gate.outputs.is_dev",
                    "needs.compose-candidate.outputs.composed",
                }:
                    self.assertIn("refusing to promote", completed.stdout)


def controller(text: str, marker: str) -> None:
    if marker not in text:
        message = "Legacy controller: new publication boundary is NOT active; use an explicit prepared workflow."
        if EXPLICIT:
            raise AssertionError(message)
        raise unittest.SkipTest(message)


def environment() -> dict[str, str]:
    result = {
        "GITHUB_REPOSITORY": "example/project", "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "4",
        "PROMOTION_PR": "456", "CANDIDATE_BRANCH": "release/promote-123",
        "CANDIDATE_SHA": CANDIDATE, "MAIN_BEFORE": BASE, "DEV_SHA": DEV, "CONTROL_SHA": CONTROL,
        "QUALIFIED_CANDIDATE": CANDIDATE, "QUALIFIED_DEV": DEV, "QUALIFIED_MAIN_BEFORE": BASE,
        "QUALIFIED_CONTROL": CONTROL, "QUALIFIED_TREE": TREE, "QUALIFICATION_RUN": "789",
        "QUALIFICATION_ATTEMPT": "2", "QUALIFICATION_DIGEST": "1" * 64,
        "QUALIFICATION_PROMOTION_RUN": "123-4",
        "PUBLICATION_DIGEST": "2" * 64, "SOURCE_RELEASE": f"python-artifacts-{DEV}",
        "GH_TOKEN": "synthetic-read-token", "PROMOTION_MERGE_TOKEN": "synthetic-nonadmin-token",
        "GITHUB_OUTPUT": "fixture-output",
        "ADMITTED_MAIN": BASE,
    }
    if "  source-units:" in WORKFLOW.read_text(encoding="utf-8"):
        result.update({
            "QUALIFICATION_PROFILE": unit_profile(jobs(WORKFLOW.read_text(encoding="utf-8"))),
            "QUALIFICATION_RUN": "123",
            "QUALIFICATION_PROMOTION_RUN": "123-2",
            "QUALIFICATION_UNIT_JOB": "900", "QUALIFICATION_UNIT_ARTIFACT": "901",
            "QUALIFICATION_UNIT_ARTIFACT_DIGEST": "sha256:" + "a" * 64,
            "QUALIFICATION_PACKET_DIGEST": "b" * 64,
            "QUALIFICATION_UNIT_MANIFEST_DIGEST": "c" * 64,
            "QUALIFICATION_UNIT_INPUTS_DIGEST": "d" * 64,
        })
    return result


@dataclass
class Provider:
    changes: dict[str, Any] = field(default_factory=dict)
    pr_changes: dict[str, Any] = field(default_factory=dict)
    buckets: list[str] = field(default_factory=lambda: ["pass"])
    strict: bool = True
    api_error: bool = False
    moved_head: bool = False
    merged: bool = False
    main: str = BASE
    checked: int = 0
    calls: list[list[str]] = field(default_factory=list)
    output: io.StringIO = field(default_factory=io.StringIO)

    def run(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        assert len(self.calls) < 30, "fixture refuses unbounded polling"
        assert argv[0] == "gh" and 0 < kwargs["timeout"] <= 60
        env = kwargs["env"]
        assert "PROMOTION_MERGE_TOKEN" not in env
        merging = argv[1:3] == ["pr", "merge"]
        assert env["GH_TOKEN"] == ("synthetic-nonadmin-token" if merging else "synthetic-read-token")
        result: Any
        code = 0
        if argv[1:3] == ["pr", "view"]:
            result = {
                "state": "MERGED" if self.merged else "OPEN", "mergeCommit": {"oid": MERGED},
                "headRefName": "release/promote-123", "headRefOid": CANDIDATE,
                "baseRefName": "main", "baseRefOid": BASE, **self.pr_changes,
            }
            if self.moved_head and self.checked:
                result["headRefOid"] = DEV
        elif argv[1:3] == ["pr", "checks"]:
            bucket = self.buckets[min(self.checked, len(self.buckets) - 1)]
            self.checked += 1
            code = 8 if bucket == "pending" else 0
            result = [{"name": "main source gate", "bucket": bucket, "link": ""}]
        elif argv[1:] == ["api", "repos/example/project/rules/branches/main"]:
            result = [{"type": "required_status_checks", "parameters": {
                "strict_required_status_checks_policy": self.strict,
                "required_status_checks": [{"context": "main source gate"}],
            }}]
        elif argv[1:] == ["api", f"repos/example/project/actions/runs/{os.environ['QUALIFICATION_RUN']}"]:
            if self.api_error:
                raise subprocess.CalledProcessError(1, argv, stderr="qualification unavailable")
            result = {**qualification_response(), **self.changes}
        elif argv[1] == "api" and os.environ.get("QUALIFICATION_PROFILE") in ("source-units-v2", "candidate-units-v3"):
            result = json.loads(api_response(argv, []))
        elif merging:
            last = (
                f"repos/example/project/actions/artifacts/{os.environ['QUALIFICATION_UNIT_ARTIFACT']}"
                if os.environ.get("QUALIFICATION_PROFILE") in ("source-units-v2", "candidate-units-v3")
                else "repos/example/project/actions/runs/789"
            )
            assert self.calls[-2][1:] == ["api", last]
            assert "--admin" not in argv and "--auto" not in argv and "--squash" in argv
            assert argv[argv.index("--match-head-commit") + 1] == CANDIDATE
            assert kwargs["check"] is True
            self.merged, self.main, result = True, MERGED, None
        else:
            raise AssertionError(f"unexpected backend operation: {argv}")
        return subprocess.CompletedProcess(argv, code, "" if result is None else json.dumps(result), "")

    def open(self, filename: str, mode: str, **kwargs: Any) -> Any:
        assert self.merged and filename == "fixture-output" and mode == "a"
        return contextlib.nullcontext(self.output)


class BoundaryTests(_ShellFixture):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        controller(cls.text, "  qualify-candidate:")
        cls.jobs = jobs(cls.text)
        cls.merge = python_body(script(step(cls.jobs["merge-promotion"], "merged")))

    def execute(self, provider: Provider, env: dict[str, str] | None = None) -> None:
        with (
            mock.patch.dict(os.environ, environment() if env is None else env, clear=True),
            mock.patch("subprocess.run", provider.run),
            mock.patch("time.sleep", return_value=None),
            mock.patch("time.monotonic", side_effect=itertools.count()),
            mock.patch("builtins.open", provider.open),
            contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()),
        ):
            exec(compile(self.merge, "<literal-merge-waiter>", "exec"), {})

    def unmerged(self, provider: Provider) -> None:
        self.assertFalse(provider.merged)
        self.assertEqual(provider.main, BASE)
        self.assertEqual(provider.output.getvalue(), "")
        self.assertFalse(any(call[1:3] == ["pr", "merge"] for call in provider.calls))

    def test_complete_premerge_graph_and_no_checkout_with_merge_authority(self) -> None:
        def ancestors(name: str) -> set[str]:
            direct = needs(self.jobs[name])
            return direct.union(*(ancestors(parent) for parent in direct))
        self.assertIn("qualify-candidate", ancestors("publish-python-artifacts"))
        self.assertLessEqual({"qualify-candidate", "publish-python-artifacts"}, ancestors("merge-promotion"))
        self.assertNotIn("merge-promotion", ancestors("publish-python-artifacts"))
        self.assertFalse(any(re.match(r"^\s*(?:- )?uses:", line)
                             for line in self.jobs["merge-promotion"].splitlines()))
        self.assertNotIn("secrets.", self.jobs["merge-promotion"])
        for dependency in ("qualify-candidate", "publish-python-artifacts"):
            self.assertIn(f"needs.{dependency}.result == 'success'", self.jobs["merge-promotion"])
        self.assertRegex(self.jobs["merge-promotion"], r"(?m)^      actions: read$")
        ci = jobs((WORKFLOW.parent / "ci.yml").read_text(encoding="utf-8"))
        if "source-units" in self.jobs:
            self.assertEqual(set(ci), {"checks", "unit-tests", "pr-gate"}
                             if "unit-tests" in ci else {"main-metadata"})
            self.assertNotIn("full", self.jobs)
            self.assertNotIn("promotion-gate", ci)
        else:
            excluded = {"promotion-gate", "pr-gate", "coverage-guided-selection-integration"}
            self.assertEqual(needs(ci["promotion-gate"]), set(ci) - excluded)
            self.assertIn("inputs.promotion_phase == 'candidate'", ci["full"])
            self.assertEqual(len(re.findall(r"^          - [a-z][a-z0-9-]*$", ci["full"], re.M)), 9)

    def test_postlanding_attribution_refresh_is_bounded_and_not_release_qualification(self) -> None:
        body = self.jobs["refresh-promotion-attribution"]
        self.assertEqual(needs(body), {"merge-promotion"})
        for expected in (
            "if: needs.merge-promotion.outputs.merge_sha != ''",
            "timeout-minutes: 5", "continue-on-error: true", "actions: write",
            "MERGE_SHA: ${{ needs.merge-promotion.outputs.merge_sha }}",
            "PROMOTION_RUN: ${{ github.run_id }}-${{ github.run_attempt }}",
        ):
            self.assertIn(expected, body)
        self.assertNotIn("uses:", body)
        self.assertNotIn("secrets.", body)
        code = python_body(script(step(
            body, "Dispatch the bounded idempotent attribution refresh without release qualification",
        )))
        for merged, run in ((MERGED, "123-4"), ("invalid", "123-4"), (MERGED, "invalid")):
            with self.subTest(merged=merged, run=run):
                env = {**environment(), "MERGE_SHA": merged, "PROMOTION_RUN": run}
                with mock.patch.dict(os.environ, env, clear=True), mock.patch("subprocess.run") as dispatch:
                    if merged == "invalid" or run == "invalid":
                        with self.assertRaises(RuntimeError):
                            exec(compile(code, "<literal-attribution-refresh>", "exec"), {})
                        dispatch.assert_not_called()
                        continue
                    exec(compile(code, "<literal-attribution-refresh>", "exec"), {})
                    dispatch.assert_called_once()
                    argv, = dispatch.call_args.args
                    self.assertEqual(argv, [
                        "gh", "api", "--method", "POST",
                        "repos/example/project/actions/workflows/workflow-lockdown-guard.yml/dispatches",
                        "--input", "-",
                    ])
                    self.assertEqual(json.loads(dispatch.call_args.kwargs["input"]), {
                        "ref": "main", "inputs": {"promotion_sha": MERGED, "promotion_run": "123-4"},
                    })
                    self.assertIs(dispatch.call_args.kwargs["check"], True)
                    self.assertEqual(dispatch.call_args.kwargs["timeout"], 60)
        with (
            mock.patch.dict(os.environ, {**environment(), "MERGE_SHA": MERGED, "PROMOTION_RUN": "123-4"}, clear=True),
            mock.patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, ["gh"])),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            exec(compile(code, "<literal-attribution-refresh>", "exec"), {})

    def test_pause_defers_normal_work_but_not_explicit_replacement_without_weakening_evidence(self) -> None:
        gate = self.jobs["gate"]
        pause = step(gate, "pause")
        self.assertIn("git fetch --no-tags origin main", script(pause))
        self.assertIn("if: steps.pause.outputs.normal_paused != 'true'", step(gate, "check"))
        self.assertIn(
            "is_dev: ${{ steps.pause.outputs.normal_paused == 'true' && 'false' || steps.check.outputs.is_dev }}",
            gate,
        )
        code = python_body(script(pause))
        state = {"schema": "copilot-extensions.release-pipeline-state", "version": 1, "paused": True}
        for paused, qualification in itertools.product((True, False), ("true", "false")):
            with self.subTest(paused=paused, qualification=qualification):
                output = io.StringIO()
                env = {**environment(), "QUALIFICATION_ONLY": qualification}
                result = subprocess.CompletedProcess([], 0, json.dumps({**state, "paused": paused}), "")
                with (
                    mock.patch.dict(os.environ, env, clear=True),
                    mock.patch("subprocess.run", return_value=result) as read,
                    mock.patch("builtins.open", return_value=contextlib.nullcontext(output)),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    exec(compile(code, "<literal-early-pause>", "exec"), {})
                state_ref = BASE if "main_base=" in code else "refs/remotes/origin/main"
                read.assert_called_once_with(
                    ["git", "show", f"{state_ref}:.github/release-pipeline-state.json"],
                    check=True, capture_output=True, text=True, timeout=30,
                )
                expected = str(paused and qualification == "false").lower()
                expected_output = f"normal_paused={expected}\n"
                if "released_source=" in code:
                    expected_output += "released_source=\n"
                if "main_base=" in code:
                    expected_output += f"main_base={BASE}\n"
                self.assertEqual(output.getvalue(), expected_output)
        invalid = [
            None, [], {}, {**state, "schema": "other"}, {**state, "version": True},
            {**state, "version": 2}, {**state, "paused": "true"}, {**state, "paused": None},
        ]
        for value in invalid:
            with self.subTest(invalid=value):
                result = subprocess.CompletedProcess([], 0, json.dumps(value), "")
                with (
                    mock.patch.dict(os.environ, {**environment(), "QUALIFICATION_ONLY": "false"}, clear=True),
                    mock.patch("subprocess.run", return_value=result),
                    mock.patch("builtins.open") as output,
                    self.assertRaises(RuntimeError),
                ):
                    exec(compile(code, "<literal-early-pause>", "exec"), {})
                output.assert_not_called()
        with (
            mock.patch.dict(os.environ, {**environment(), "QUALIFICATION_ONLY": "false"}, clear=True),
            mock.patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, ["git"])),
            mock.patch("builtins.open") as output,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            exec(compile(code, "<literal-early-pause>", "exec"), {})
        output.assert_not_called()

    def test_pending_checks_then_exact_nonadmin_merge(self) -> None:
        provider = Provider(buckets=["pending", "pass"])
        self.execute(provider)
        self.assertTrue(provider.merged)
        self.assertEqual(provider.checked, 2)
        self.assertEqual(provider.output.getvalue(), f"merge_sha={MERGED}\n")
        self.assertEqual(sum(call[1:3] == ["pr", "merge"] for call in provider.calls), 1)

    def test_missing_or_invalid_receipt_identity_cannot_merge(self) -> None:
        fields = [
            "QUALIFICATION_RUN", "QUALIFICATION_ATTEMPT", "QUALIFICATION_DIGEST", "PUBLICATION_DIGEST",
            "SOURCE_RELEASE", "QUALIFIED_TREE", "QUALIFIED_CANDIDATE", "QUALIFIED_DEV",
            "QUALIFIED_MAIN_BEFORE", "QUALIFIED_CONTROL",
        ]
        for name in fields:
            for value in (None, "", "invalid"):
                with self.subTest(field=name, value=value):
                    env, provider = environment(), Provider()
                    env.pop(name) if value is None else env.update({name: value})
                    with self.assertRaises((RuntimeError, KeyError, ValueError)):
                        self.execute(provider, env)
                    self.unmerged(provider)
        for name in ("QUALIFIED_CANDIDATE", "QUALIFIED_DEV", "QUALIFIED_MAIN_BEFORE", "QUALIFIED_CONTROL"):
            with self.subTest(mismatched=name):
                env, provider = environment(), Provider()
                env[name] = MERGED
                with self.assertRaises(RuntimeError):
                    self.execute(provider, env)
                self.unmerged(provider)
        env, provider = environment(), Provider()
        env["SOURCE_RELEASE"] = f"python-artifacts-{MERGED}"
        with self.assertRaises(RuntimeError):
            self.execute(provider, env)
        self.unmerged(provider)

    def test_provider_qualification_identity_and_freshness_cannot_drift(self) -> None:
        cases = [
            ("id", 790), ("id", True), ("run_attempt", 3), ("run_attempt", None), ("run_attempt", True),
            ("head_sha", DEV), ("head_branch", "other"), ("path", ".github/workflows/other.yml"),
            ("event", "push"), ("status", "in_progress"), ("conclusion", "failure"),
            ("conclusion", "cancelled"), ("conclusion", None), ("display_title", "unrelated"),
        ]
        if "source-units" in self.jobs:
            cases = [
                (key, value) for key, value in cases
                if key not in ("status", "conclusion", "display_title")
                and (key, value) != ("run_attempt", 3)
            ] + [("run_attempt", 1)]
        for key, value in cases:
            with self.subTest(field=key, value=value):
                provider = Provider(changes={key: value})
                with self.assertRaises(RuntimeError):
                    self.execute(provider)
                self.unmerged(provider)
        provider = Provider(api_error=True)
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute(provider)
        self.unmerged(provider)

    def test_unit_receipt_reuses_the_original_job_without_waiting_for_the_controller(self) -> None:
        if "source-units" not in self.jobs:
            self.skipTest("legacy candidate-CI profile requires a whole completed run")
        for key, value in (
            ("run_attempt", 3), ("status", "in_progress"), ("conclusion", None),
            ("display_title", "unrelated"),
        ):
            with self.subTest(field=key, value=value):
                provider = Provider(changes={key: value})
                self.execute(provider)
                self.assertTrue(provider.merged)

    def test_stale_base_head_required_checks_and_policy_refuse_without_owner_pat(self) -> None:
        cases = [
            Provider(pr_changes={"baseRefOid": DEV}), Provider(pr_changes={"headRefOid": DEV}),
            Provider(moved_head=True, buckets=["pending", "pass"]), Provider(strict=False),
            *(Provider(buckets=[bucket]) for bucket in ("fail", "cancel", "skipping", "invalid")),
        ]
        for provider in cases:
            with self.subTest(case=repr(provider)):
                with self.assertRaises(RuntimeError):
                    self.execute(provider)
                self.unmerged(provider)
        env, provider = environment(), Provider()
        env["PROMOTION_MERGE_TOKEN"] = ""
        with self.assertRaises(RuntimeError):
            self.execute(provider, env)
        self.unmerged(provider)
        self.assertFalse(provider.calls)

    def test_helper_selection_preserves_immutable_source_or_explicit_legacy_pin(self) -> None:
        gate = self.jobs["gate"]
        identifier = "Require an immutable reviewed release-helper pin"
        if "RELEASE_HELPERS_SHA" not in gate:
            self.assertIn("ref: ${{ github.workflow_sha }}", gate)
            for name in (
                "compose-candidate", "source-units", "build-python-artifacts", "python-artifacts",
                "promote", "qualify-candidate", "publish-python-artifacts", "record-promotion",
            ):
                with self.subTest(job=name):
                    helper = self.jobs[name].split("path: reviewed-helpers", 1)
                    self.assertEqual(len(helper), 2)
                    self.assertIn("ref: ${{ needs.gate.outputs.sha }}", helper[0])
                    self.assertTrue(helper[1].lstrip().startswith("persist-credentials: false"))
                    self.assertIn("gate", needs(self.jobs[name]))
            return
        self.assertLess(gate.index(identifier), gate.index("uses: actions/checkout@"))
        body = script(step(gate, identifier))
        self.assertNotEqual(self.shell(body, {"RELEASE_HELPERS_SHA": "0" * 40}).returncode, 0)
        self.assertEqual(self.shell(body, {"RELEASE_HELPERS_SHA": CONTROL}).returncode, 0)

    def test_only_manual_trusted_main_can_admit_paused_qualification(self) -> None:
        body = script(step(self.jobs["gate"], "mode"))
        env = {
            "REQUESTED_QUALIFICATION_ONLY": "true", "GITHUB_EVENT_NAME": "workflow_dispatch",
            "DEFAULT_REF": "refs/heads/main", "GITHUB_REF": "refs/heads/main",
            "RUN_SHA": CONTROL, "WORKFLOW_SHA": CONTROL,
        }
        accepted = self.shell(body, env)
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertIn("qualification_only=true", accepted.output)
        for key, value in [
            ("GITHUB_EVENT_NAME", "repository_dispatch"), ("GITHUB_REF", "refs/heads/dev"),
            ("DEFAULT_REF", "refs/heads/dev"), ("WORKFLOW_SHA", DEV), ("RUN_SHA", ""),
        ]:
            with self.subTest(field=key):
                self.assertNotEqual(self.shell(body, {**env, key: value}).returncode, 0)
        automatic = self.shell(body, {**env, "REQUESTED_QUALIFICATION_ONLY": "false",
                                      "GITHUB_EVENT_NAME": "repository_dispatch"})
        self.assertEqual(automatic.returncode, 0)
        self.assertIn("qualification_only=false", automatic.output)

    def test_pause_is_preserved_and_normal_dispatch_cannot_bypass_it(self) -> None:
        code = python_body(script(step(self.jobs["promote"], "ready")))
        for mode, states, succeeds in [
            ("false", [False, False], True), ("false", [True, True], False),
            ("true", [True, True], True), ("true", [False, False], False),
            ("true", [False, True], False), ("true", [True, False], False),
        ]:
            with self.subTest(mode=mode, states=states):
                def read(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                    self.assertEqual(argv[:2], ["git", "show"])
                    self.assertLessEqual(kwargs["timeout"], 30)
                    paused = states[1 if "^:" in argv[2] else 0]
                    return subprocess.CompletedProcess(argv, 0, json.dumps({"paused": paused}), "")
                with mock.patch.object(sys, "argv", ["-", CANDIDATE, mode]), mock.patch("subprocess.run", read):
                    if succeeds:
                        exec(compile(code, "<literal-pause-fence>", "exec"), {})
                    else:
                        with self.assertRaises(RuntimeError):
                            exec(compile(code, "<literal-pause-fence>", "exec"), {})


class ControlAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = BASE_WORKFLOW or WORKFLOW.parent / "base-branch-reminder.yml"
        cls.text = cls.path.read_text(encoding="utf-8")
        controller(cls.text, "canonical manifest and regular workflow blobs verified")
        cls.shell = script(step(jobs(cls.text)["main-gate"],
                                "Admit only generated releases, owner workflow/control transactions, or recognized rollback state"))
        cls.code = python_body(cls.shell, "declared = set(_paths(raw))")
        cls.control = trusted_control(cls.path)

    def setUp(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.before: dict[str, tuple[str, str, str]] = {}
        self.after: dict[str, tuple[str, str, str]] = {}
        for path in [*CONTROLS, ".github/workflows/base-branch-reminder.yml"]:
            self.put(path, b"name: trusted workflow\n")
        self.before = self.after.copy()
        self.manifest(CONTROLS)

    def put(self, path: str, raw: bytes, mode: str = "100644", kind: str = "blob") -> None:
        oid = hashlib.sha256(raw).hexdigest()[:40]
        self.blobs[oid] = raw
        self.after[path] = (mode, kind, oid)

    def manifest(self, paths: list[str]) -> None:
        data = {"schema": "copilot-extensions.promotion-control", "version": 1, "files": sorted(paths)}
        self.put(MANIFEST, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode())

    def execute(self, foreign: bool = False) -> None:
        def git(repo: Path, argv: list[str]) -> bytes:
            raw = self.blobs[argv[2]]
            if argv[:2] == ["cat-file", "-s"]:
                return str(len(raw)).encode()
            self.assertEqual(argv[:2], ["cat-file", "blob"])
            return raw
        env = {"BASE_SHA": BASE, "HEAD_SHA": CANDIDATE, "TARGET_REPOSITORY": "example/project",
               "HEAD_REPOSITORY": "example/fork" if foreign else "example/project"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.dict(sys.modules, {"promotion_control": self.control}),
            mock.patch.object(sys, "path", sys.path.copy()),
            mock.patch.object(self.control, "_tree", side_effect=[self.before, self.after]),
            mock.patch.object(self.control, "_git", git),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            exec(compile(self.code, "<literal-control-admission>", "exec"), {})

    def test_canonical_owner_manifest_and_exact_regular_workflows_are_admitted(self) -> None:
        self.execute()
        self.manifest([*CONTROLS, ".github/workflows/base-branch-reminder.yml"])
        self.put(CONTROLS[0], b"name: reviewed replacement\n")
        self.execute()

    def test_foreign_repository_is_refused(self) -> None:
        with self.assertRaises(RuntimeError):
            self.execute(foreign=True)

    def test_non_owner_is_refused_before_git_or_helper_execution(self) -> None:
        prefix = self.shell.split('changed="$(git diff', 1)[0] + "\nexit 0\n"
        result = BoundaryTests.shell(self, prefix, {
            "HEAD_REF": "control-repair", "PR_AUTHOR": "contributor", "REPO_OWNER": "owner",
        })
        self.assertNotEqual(result.returncode, 0)

    def test_unsafe_manifest_and_blob_transactions_are_refused(self) -> None:
        for case in [
            "deleted-manifest", "deleted-workflow", "executable-manifest", "symlink-manifest",
            "executable-workflow", "symlink-workflow", "submodule", "case-alias", "noncanonical",
            "undeclared-product", "undeclared-workflow", "undeclared-helper", "product-declaration", "missing-required",
            "duplicate-key", "boolean-version", "removed-declaration",
        ]:
            with self.subTest(case=case):
                self.setUp()
                if case.startswith("deleted-"):
                    self.after.pop(MANIFEST if case.endswith("manifest") else CONTROLS[0])
                elif case.startswith(("executable-", "symlink-")) or case == "submodule":
                    path = MANIFEST if case.endswith("manifest") else CONTROLS[0]
                    mode, kind = ("160000", "commit") if case == "submodule" else (
                        "100755" if case.startswith("executable") else "120000", "blob")
                    self.after[path] = (mode, kind, self.after[path][2])
                elif case == "case-alias":
                    self.put(".GITHUB/workflows/ci.yml", b"name: alias\n")
                elif case == "noncanonical":
                    self.put(MANIFEST, self.blobs[self.after[MANIFEST][2]].strip())
                elif case.startswith("undeclared-"):
                    paths = {"product": "src/product.py", "workflow": ".github/workflows/other.yml",
                             "helper": "tools/promotion_control.py"}
                    self.put(paths[case.removeprefix("undeclared-")], b"raise AssertionError('PR code executed')")
                elif case in ("product-declaration", "missing-required"):
                    self.manifest([*CONTROLS, "src/product.py"] if case.startswith("product") else CONTROLS[:1])
                elif case == "duplicate-key":
                    raw = self.blobs[self.after[MANIFEST][2]].replace(b'"version": 1', b'"version": 1, "version": 1')
                    self.put(MANIFEST, raw)
                elif case == "boolean-version":
                    self.put(MANIFEST, self.blobs[self.after[MANIFEST][2]].replace(b'"version": 1', b'"version": true'))
                else:
                    self.manifest([*CONTROLS, ".github/workflows/base-branch-reminder.yml"])
                    self.before[MANIFEST] = self.after[MANIFEST]
                    self.manifest(CONTROLS)
                with self.assertRaises((RuntimeError, self.control.ControlPreservationError)):
                    self.execute()


class FunctionalControlGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = BASE_WORKFLOW or WORKFLOW.parent / "base-branch-reminder.yml"
        text = cls.path.read_text(encoding="utf-8")
        controller(text, "Canonical owning run executes the current reviewed Main control generation.")
        cls.gate = jobs(text)["main-gate"]
        body = script(step(cls.gate,
                           "Admit only generated releases, owner workflow/control transactions, or recognized rollback state"))
        cls.code = python_body(body, 'executing = run["head_sha"]')
        cls.control = trusted_control(cls.path)

    def setUp(self) -> None:
        self.run_data: dict[str, Any] = {
            "id": 123, "run_attempt": 2, "path": ".github/workflows/validate-and-promote.yml",
            "event": "repository_dispatch", "head_branch": "main", "head_sha": CONTROL,
            "repository": {"full_name": "example/project"},
            "head_repository": {"full_name": "example/project"},
        }
        self.env = {
            "TARGET_REPOSITORY": "example/project", "HEAD_REPOSITORY": "example/project",
            "HEAD_REF": "release/promote-123", "BASE_SHA": BASE, "HEAD_SHA": CANDIDATE,
            "GH_TOKEN": "synthetic-read-token",
        }
        self.blobs: dict[str, bytes] = {}
        self.source: dict[str, tuple[str, str, str]] = {}
        self.current: dict[str, tuple[str, str, str]] = {}
        self.api_calls: list[list[str]] = []
        self.git_calls: list[list[str]] = []
        self.tree_reads: list[str] = []
        self.unrelated = False
        self.api_error = False
        for path in CONTROLS:
            self.source[path] = self.blob(b"name: trusted release control\n")
        self.current = self.source.copy()

    def blob(self, raw: bytes) -> tuple[str, str, str]:
        oid = hashlib.sha256(raw).hexdigest()[:40]
        self.blobs[oid] = raw
        return "100644", "blob", oid

    def provider(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.assertEqual(argv, ["gh", "api", "repos/example/project/actions/runs/123"])
        self.assertTrue(kwargs["check"])
        self.assertTrue(kwargs["capture_output"])
        self.assertLessEqual(kwargs["timeout"], 45)
        self.assertEqual(os.environ["GH_TOKEN"], "synthetic-read-token")
        self.assertNotIn("APERTURE_RELEASE_TOKEN", os.environ)
        self.assertNotIn("PROMOTION_MERGE_TOKEN", os.environ)
        self.api_calls.append(argv)
        if self.api_error:
            raise subprocess.CalledProcessError(1, argv, stderr="owning run unavailable")
        return subprocess.CompletedProcess(argv, 0, json.dumps(self.run_data), "")

    def git(self, repo: Path, argv: list[str]) -> bytes:
        self.git_calls.append(argv)
        self.assertNotIn(CANDIDATE, argv, "PR head must remain unexecuted data")
        if argv[:2] == ["merge-base", "--is-ancestor"]:
            if self.unrelated or argv[2] != CONTROL:
                raise self.control.ControlPreservationError("executing commit is not established on Main")
            self.assertEqual(argv[3], BASE)
            return b""
        self.assertIn(argv[:2], (["cat-file", "-s"], ["cat-file", "blob"]))
        if argv[2] not in self.blobs:
            raise self.control.ControlPreservationError("missing Git object")
        raw = self.blobs[argv[2]]
        return str(len(raw)).encode() if argv[1] == "-s" else raw

    def tree(self, repo: Path, commit: str) -> dict[str, tuple[str, str, str]]:
        self.tree_reads.append(commit)
        if commit == CONTROL:
            return self.source
        self.assertEqual(commit, BASE, "must inspect trusted executing/Main containers, never PR code")
        return self.current

    def execute(self) -> str:
        output = io.StringIO()
        with (
            mock.patch.dict(os.environ, self.env, clear=True),
            mock.patch.dict(sys.modules, {"promotion_control": self.control}),
            mock.patch.object(sys, "path", sys.path.copy()),
            mock.patch.object(self.control, "_tree", self.tree),
            mock.patch.object(self.control, "_git", self.git),
            mock.patch("subprocess.run", self.provider),
            contextlib.redirect_stdout(output),
        ):
            exec(compile(self.code, "<literal-control-generation>", "exec"), {})
        return output.getvalue()

    def test_different_main_containers_with_identical_control_bytes_are_accepted(self) -> None:
        self.assertNotEqual(CONTROL, BASE)
        for event in ("repository_dispatch", "workflow_dispatch"):
            with self.subTest(event=event):
                self.run_data["event"] = event
                self.assertIn("current reviewed Main control generation", self.execute())
                self.assertEqual(self.tree_reads[-2:], [CONTROL, BASE])
        self.assertEqual(len(self.api_calls), 2)
        self.assertNotIn("secrets.", self.gate)
        self.assertRegex(self.gate, r"(?m)^      actions: read$")
        self.assertRegex(self.gate, r"(?m)^      contents: read$")
        self.assertIn("GH_TOKEN: ${{ github.token }}", self.gate)

    def test_each_obsolete_executing_workflow_is_refused(self) -> None:
        for path in CONTROLS:
            with self.subTest(path=path):
                self.setUp()
                self.source[path] = self.blob(b"name: obsolete executing graph\n")
                with self.assertRaisesRegex(RuntimeError, "executing release control differs"):
                    self.execute()

    def test_provider_owning_run_identity_is_fail_closed(self) -> None:
        invalid = [
            ("id", True), ("id", 124), ("id", "123"), ("run_attempt", True),
            ("run_attempt", 0), ("run_attempt", -1), ("run_attempt", "2"),
            ("head_sha", "unknown"), ("head_sha", DEV), ("head_branch", "dev"),
            ("path", ".github/workflows/ci.yml"), ("event", "push"),
            ("repository", {"full_name": "example/other"}),
            ("head_repository", {"full_name": "example/fork"}),
            ("repository", []), ("head_repository", "example/project"),
        ]
        invalid.extend((key, None) for key in self.run_data)
        for key, value in invalid:
            with self.subTest(field=key, value=value):
                self.setUp()
                self.run_data[key] = value
                with self.assertRaises((RuntimeError, self.control.ControlPreservationError)):
                    self.execute()
        for key in list(self.run_data):
            with self.subTest(missing=key):
                self.setUp()
                del self.run_data[key]
                with self.assertRaises(RuntimeError):
                    self.execute()

    def test_branch_repository_base_and_api_errors_cannot_supply_an_owning_run(self) -> None:
        for key, value in [
            ("HEAD_REF", "release/promote-unknown"), ("HEAD_REF", "release/promote-0"),
            ("HEAD_REF", "release/promote-0123"), ("HEAD_REF", "release/promote-123-extra"),
            ("HEAD_REPOSITORY", "example/fork"), ("BASE_SHA", "unknown"),
        ]:
            with self.subTest(field=key, value=value):
                self.setUp()
                self.env[key] = value
                with self.assertRaises(RuntimeError):
                    self.execute()
                self.assertFalse(self.api_calls)
        self.setUp()
        self.api_error = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute()
        self.assertEqual(len(self.api_calls), 1)
        self.assertFalse(self.tree_reads)

    def test_main_ancestry_and_regular_workflow_blobs_are_required(self) -> None:
        self.unrelated = True
        with self.assertRaises(self.control.ControlPreservationError):
            self.execute()
        self.assertFalse(self.tree_reads)
        for side in ("source", "current"):
            for path in CONTROLS:
                for mode in (None, "100755", "120000", "160000"):
                    with self.subTest(side=side, path=path, mode=mode):
                        self.setUp()
                        tree = getattr(self, side)
                        if mode is None:
                            tree.pop(path)
                        else:
                            tree[path] = (mode, "commit" if mode == "160000" else "blob", tree[path][2])
                        with self.assertRaises((RuntimeError, self.control.ControlPreservationError)):
                            self.execute()
        self.setUp()
        del self.blobs[self.source[CONTROLS[0]][2]]
        with self.assertRaises(self.control.ControlPreservationError):
            self.execute()
        self.setUp()
        self.source[CONTROLS[0]] = self.blob(b"x" * ((1 << 20) + 1))
        with self.assertRaises(self.control.ControlPreservationError):
            self.execute()


def main(argv: list[str] | None = None) -> int:
    global WORKFLOW, BASE_WORKFLOW, EXPLICIT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path)
    parser.add_argument("--base-branch-workflow", type=Path)
    options, remaining = parser.parse_known_args(argv)
    EXPLICIT = options.workflow is not None or options.base_branch_workflow is not None
    WORKFLOW = options.workflow or WORKFLOW
    BASE_WORKFLOW = options.base_branch_workflow
    digest = hashlib.sha256(WORKFLOW.read_bytes()).hexdigest()
    print(f"Controller {'explicit' if EXPLICIT else 'default'}: {WORKFLOW}; sha256={digest}", flush=True)
    base = BASE_WORKFLOW or WORKFLOW.parent / "base-branch-reminder.yml"
    print(f"Admission: {base}; sha256={hashlib.sha256(base.read_bytes()).hexdigest()}", flush=True)
    result = unittest.main(module=__name__, argv=[sys.argv[0], *remaining], exit=False).result
    return int(not result.wasSuccessful())


if __name__ == "__main__":
    raise SystemExit(main())
