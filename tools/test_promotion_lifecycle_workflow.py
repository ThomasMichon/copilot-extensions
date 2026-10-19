"""Exercise qualified-before-PR control data without network or release writes.

Use ``--workflow PATH`` for a prepared Main controller. The legacy Dev controller
is not a lifecycle qualification; generated source-owned controllers must comply.
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

import test_release_publication_boundary as boundary
from _promotion_workflow_test_support import source_workflow


def lifecycle_contract(workflow: dict[str, Any]) -> None:
    jobs = workflow["jobs"]
    prepare, qualify, publish, merge = (jobs[name] for name in (
        "promote", "qualify-candidate", "publish-python-artifacts", "merge-promotion",
    ))
    assert "pr_number" not in prepare["outputs"]
    assert "gh pr create" not in json.dumps(prepare)
    assert qualify["if"] == "needs.promote.outputs.candidate_sha != ''"
    assert qualify["outputs"]["qualification_promotion_run"] == (
        "${{ steps.qualify.outputs.qualification_promotion_run }}"
    )
    guard = next(
        step for step in jobs["gate"]["steps"]
        if "promotion_lifecycle.py" in step.get("run", "")
    )
    assert "--require-idle" in guard["run"] and "--candidate-" not in guard["run"]
    assert guard["env"]["GH_TOKEN"] == "${{ github.token }}"
    gate_steps = jobs["gate"]["steps"]
    check_index = next(index for index, step in enumerate(gate_steps) if step.get("id") == "check")
    assert gate_steps.index(guard) < check_index
    retirement = next(step for step in gate_steps if "--automatic-source" in step.get("run", ""))
    assert gate_steps.index(retirement) > check_index
    assert retirement["if"] == (
        "github.event_name == 'repository_dispatch' && steps.check.outputs.is_dev == 'true'"
    )
    assert retirement["env"] == {
        "GH_TOKEN": "${{ github.token }}", "AUTOMATIC_SOURCE": "${{ steps.check.outputs.sha }}",
        "AUTOMATIC_MAIN": "${{ steps.pause.outputs.main_base }}",
    }
    assert "--require-idle" in retirement["run"] and "--automatic-main" in retirement["run"]
    record = next(step for step in gate_steps if step.get("name") == "Record immutable admitted source")
    upload = next(step for step in gate_steps if step.get("name") == "Upload immutable admitted source")
    assert gate_steps.index(retirement) < gate_steps.index(record) < gate_steps.index(upload)
    assert record["if"] == upload["if"] == "steps.check.outputs.is_dev == 'true'"
    assert record["env"] == {
        "ADMITTED_SOURCE": "${{ steps.check.outputs.sha }}",
        "ADMITTED_MAIN": "${{ steps.pause.outputs.main_base }}",
        "ADMISSION_PATH": "${{ runner.temp }}/promotion-admission.json",
    }
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"] == {
        "name": "promotion-admission-${{ steps.check.outputs.sha }}-${{ steps.pause.outputs.main_base }}",
        "path": "${{ runner.temp }}/promotion-admission.json", "if-no-files-found": "error",
    }
    assert gate_steps[check_index]["env"]["RELEASED_SOURCE"] == "${{ steps.pause.outputs.released_source }}"
    assert "released_source=" in next(step for step in gate_steps if step.get("id") == "pause")["run"]
    assert publish["env"]["QUALIFICATION_PROMOTION_RUN"] == (
        "${{ needs.qualify-candidate.outputs.qualification_promotion_run }}"
    )
    assert publish["outputs"]["pr_number"] == "${{ steps.land.outputs.pr_number }}"
    positions = {step.get("id"): index for index, step in enumerate(publish["steps"])}
    opening = publish["steps"][positions["land"]]
    assert positions["publish"] < positions["land"]
    assert any(
        "--require-idle" in step.get("run", "") and "--candidate-branch" in step["run"]
        for step in publish["steps"][positions["publish"] + 1:positions["land"]]
    )
    assert "gh pr create" in opening["run"]
    assert opening["env"]["GH_TOKEN"] == "${{ secrets.APERTURE_RELEASE_TOKEN }}"
    assert "needs.publish-python-artifacts.outputs.pr_number != ''" in merge["if"]
    assert "needs.promote.outputs.pr_number" not in merge["if"]
    assert "secrets." not in json.dumps(merge)
    assert len(merge["steps"]) == 1 and "uses" not in merge["steps"][0]
    waiter = merge["steps"][0]["env"]
    assert waiter["PROMOTION_PR"] == "${{ needs.publish-python-artifacts.outputs.pr_number }}"
    assert waiter["QUALIFICATION_PROMOTION_RUN"] == (
        "${{ needs.qualify-candidate.outputs.qualification_promotion_run }}"
    )


def minimal_contract() -> dict[str, Any]:
    """Small contract data, not a second executable controller."""
    owner = "${{ needs.qualify-candidate.outputs.qualification_promotion_run }}"
    return {"jobs": {
        "gate": {"steps": [{
            "run": "python promotion_lifecycle.py --require-idle",
            "env": {"GH_TOKEN": "${{ github.token }}"},
        }, {"id": "pause", "run": "released_source=trusted"},
            {"id": "check", "run": "confirm admitted source",
             "env": {"RELEASED_SOURCE": "${{ steps.pause.outputs.released_source }}"}},
            {"run": "python promotion_lifecycle.py --require-idle --automatic-source sha --automatic-main main",
             "if": "github.event_name == 'repository_dispatch' && steps.check.outputs.is_dev == 'true'",
             "env": {"GH_TOKEN": "${{ github.token }}",
                     "AUTOMATIC_SOURCE": "${{ steps.check.outputs.sha }}",
                     "AUTOMATIC_MAIN": "${{ steps.pause.outputs.main_base }}"}},
            {"name": "Record immutable admitted source", "if": "steps.check.outputs.is_dev == 'true'",
             "env": {"ADMITTED_SOURCE": "${{ steps.check.outputs.sha }}",
                     "ADMITTED_MAIN": "${{ steps.pause.outputs.main_base }}",
                     "ADMISSION_PATH": "${{ runner.temp }}/promotion-admission.json"}},
            {"name": "Upload immutable admitted source", "if": "steps.check.outputs.is_dev == 'true'",
             "uses": "actions/upload-artifact@v4", "with": {
                 "name": "promotion-admission-${{ steps.check.outputs.sha }}-${{ steps.pause.outputs.main_base }}",
                 "path": "${{ runner.temp }}/promotion-admission.json", "if-no-files-found": "error",
             }}]},
        "promote": {"outputs": {"candidate_sha": "candidate"}, "steps": []},
        "qualify-candidate": {
            "if": "needs.promote.outputs.candidate_sha != ''",
            "outputs": {"qualification_promotion_run": "${{ steps.qualify.outputs.qualification_promotion_run }}"},
        },
        "publish-python-artifacts": {
            "env": {"QUALIFICATION_PROMOTION_RUN": owner},
            "outputs": {"pr_number": "${{ steps.land.outputs.pr_number }}"},
            "steps": [
                {"id": "publish", "run": "raw publication/readback"},
                {"run": "python promotion_lifecycle.py --require-idle --candidate-branch branch"},
                {"id": "land", "run": "gh pr create",
                 "env": {"GH_TOKEN": "${{ secrets.APERTURE_RELEASE_TOKEN }}"}},
            ],
        },
        "merge-promotion": {
            "if": "needs.publish-python-artifacts.outputs.pr_number != ''",
            "steps": [{"env": {
                "PROMOTION_PR": "${{ needs.publish-python-artifacts.outputs.pr_number }}",
                "QUALIFICATION_PROMOTION_RUN": owner,
            }}],
        },
    }}


class ContractFixtureTests(unittest.TestCase):
    def test_positive_and_mutated_contract_data(self) -> None:
        original = minimal_contract()
        lifecycle_contract(original)
        for mutation in (
            "early-pr", "pr-dependent-qualification", "no-origin", "no-admission",
            "no-final-admission", "early-open", "wrong-pr", "merge-secret", "no-retirement",
            "no-admission-record",
        ):
            with self.subTest(mutation=mutation):
                workflow = copy.deepcopy(original)
                jobs = workflow["jobs"]
                if mutation == "early-pr":
                    jobs["promote"]["outputs"]["pr_number"] = "early"
                elif mutation == "pr-dependent-qualification":
                    jobs["qualify-candidate"]["if"] = "needs.promote.outputs.pr_number != ''"
                elif mutation == "no-origin":
                    jobs["qualify-candidate"]["outputs"].clear()
                elif mutation == "no-admission":
                    jobs["gate"]["steps"][0]["run"] = "python other.py"
                elif mutation == "no-final-admission":
                    jobs["publish-python-artifacts"]["steps"].pop(1)
                elif mutation == "early-open":
                    jobs["publish-python-artifacts"]["steps"].reverse()
                elif mutation == "wrong-pr":
                    jobs["merge-promotion"]["if"] = "needs.promote.outputs.pr_number != ''"
                elif mutation == "no-retirement":
                    jobs["gate"]["steps"][3]["run"] = "no automatic retirement check"
                elif mutation == "no-admission-record":
                    jobs["gate"]["steps"].pop()
                else:
                    jobs["merge-promotion"]["steps"][0]["env"]["GH_TOKEN"] = "${{ secrets.UNEXPECTED }}"
                with self.assertRaises((AssertionError, KeyError, StopIteration)):
                    lifecycle_contract(workflow)


class TriggerLifecycleTests(boundary._ShellFixture):
    def test_all_deferred_wake_events_share_the_trusted_existing_dispatcher(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = source_workflow(root, "promote-trigger.yml")
        events = workflow.get("on", workflow.get(True))
        self.assertEqual(set(events["workflow_run"]["workflows"]), {
            "CI", "Validate and promote dev to main",
        })
        self.assertEqual(events["pull_request_target"], {"branches": ["main"], "types": ["closed"]})
        job = workflow["jobs"]["filter-and-dispatch"]
        self.assertEqual(job["permissions"], {
            "contents": "write", "pull-requests": "read", "actions": "read",
        })
        self.assertEqual(job["steps"][0]["with"]["ref"], "${{ github.workflow_sha }}")
        resolver = next(step for step in job["steps"] if step.get("id") == "source")
        for argument in (
            "--source-ci-run", "--completed-promotion-run", "--closed-main-pr", "--github-output",
        ):
            self.assertIn(argument, resolver["run"])
        dispatch = job["steps"][-1]
        self.assertEqual(dispatch["if"], "steps.source.outputs.dispatch_sha != ''")
        self.assertEqual(dispatch["env"]["SHA"], "${{ steps.source.outputs.dispatch_sha }}")
        self.assertLess(dispatch["run"].index("git merge-base --is-ancestor"),
                        dispatch["run"].index('gh api "repos/'))

    def test_real_admission_cli_controls_ci_completion_merge_recovery_and_resume_events(self) -> None:
        root = Path(__file__).resolve().parents[1]
        workflow = source_workflow(root, "promote-trigger.yml")
        resolver = next(step for step in workflow["jobs"]["filter-and-dispatch"]["steps"]
                        if step.get("id") == "source")
        with tempfile.TemporaryDirectory(prefix="promotion-trigger-policy-") as directory:
            transport = Path(directory) / "transport.py"
            transport.write_text(
                """import os,runpy,sys
from pathlib import Path
sys.path.insert(0,os.environ["TEST_TOOLS"])
from test_promotion_dispatch import Backend, state, ci, DEV, HEAD, REPO
import verify_promotion_workflows
backend=Backend()
mode=os.environ["TEST_MODE"]
if mode=="active":
    backend.active=[{"number":124,"state":"open","base":{"ref":"main"},
      "head":{"ref":"release/promote-501","sha":HEAD,"repo":{"full_name":REPO}},
      "user":{"login":"example"}}]
elif mode=="current":
    backend.main=state(DEV)
elif mode=="resume":
    backend.pr["head"]["ref"]="release-pipeline/state-123"
elif mode in ("failed-merged","cancelled-merged","failed-unmerged"):
    backend.promotion["conclusion"]="cancelled" if mode=="cancelled-merged" else "failure"
    backend.pr["merged"]=mode!="failed-unmerged"
elif mode=="manual-deferred":
    backend.cis=[ci(conclusion="failure"),ci(601,event="workflow_dispatch")]
def api(path,**kwargs):
    with open(os.environ["API_TRACE"],"a",encoding="utf-8") as log:
        log.write(path+"\\n")
    return backend(path,**kwargs)
verify_promotion_workflows._api=api
args=sys.argv[1:]
assert args[:2]==["-I","-B"]
script=Path(args[2])
if not script.is_absolute():
    script=Path(os.environ["TEST_TOOLS"]).parent/script
script=script.resolve()
assert script==Path(os.environ["TEST_TOOLS"])/"promotion_lifecycle.py"
sys.argv=[str(script),*args[3:]]
runpy.run_path(str(script),run_name="__main__")
""",
                encoding="utf-8", newline="\n",
            )
            prefix = r"""
python() { "$PYTHON" -I -B "$TRANSPORT_HELPER" "$@"; }
git() {
    if [ "$1" = "merge-base" ]; then return "$ANCESTOR_STATUS"; fi
    if [ "$1" = "fetch" ]; then return 0; fi
    return 74
}
"""
            for mode, event, name, eligible, ancestor in (
                ("ready", "workflow_run", "CI", True, "0"),
                ("active", "workflow_run", "CI", False, "0"),
                ("current", "workflow_run", "CI", False, "0"),
                ("ready", "workflow_run", "Validate and promote dev to main", True, "0"),
                ("failed-merged", "workflow_run", "Validate and promote dev to main", True, "0"),
                ("cancelled-merged", "workflow_run", "Validate and promote dev to main", True, "0"),
                ("failed-unmerged", "workflow_run", "Validate and promote dev to main", False, "0"),
                ("manual-deferred", "workflow_run", "Validate and promote dev to main", True, "0"),
                ("resume", "pull_request_target", "", True, "0"),
                ("ready", "workflow_run", "CI", False, "1"),
            ):
                with self.subTest(mode=mode, event=event, name=name, ancestor=ancestor):
                    trace = Path(directory) / f"trace-{mode}-{event}-{ancestor}"
                    env = {
                        "GITHUB_REPOSITORY": "example/project", "GITHUB_EVENT_NAME": event,
                        "COMPLETED_WORKFLOW": name, "COMPLETED_RUN": "600" if name == "CI" else "500",
                        "COMPLETED_SHA": "b" * 40, "CLOSED_PR": "123",
                        "PYTHON": Path(sys.executable).as_posix(), "TRANSPORT_HELPER": transport.as_posix(),
                        "TEST_TOOLS": (root / "tools").as_posix(), "TEST_MODE": mode,
                        "API_TRACE": trace.as_posix(), "ANCESTOR_STATUS": ancestor,
                    }
                    result = self.shell(prefix + resolver["run"], env)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual("dispatch_sha=" in result.output, eligible)
                    if ancestor == "1":
                        self.assertFalse(trace.exists())


class QualifiedPRLifecycleTests(boundary._ShellFixture):
    @classmethod
    def setUpClass(cls) -> None:
        text = boundary.WORKFLOW.read_text(encoding="utf-8")
        boundary.controller(text, "  qualify-candidate:")
        cls.workflow = yaml.safe_load(text)
        lifecycle_contract(cls.workflow)
        cls.jobs = boundary.jobs(text)
        cls.opening = boundary.script(boundary.step(cls.jobs["publish-python-artifacts"], "land"))
        cls.waiter = boundary.python_body(
            boundary.script(boundary.step(cls.jobs["merge-promotion"], "merged")),
        )

    def test_literal_admission_record_binds_the_actual_source_base_and_original_attempt(self) -> None:
        record = next(step for step in self.workflow["jobs"]["gate"]["steps"]
                      if step.get("name") == "Record immutable admitted source")
        code = boundary.python_body(record["run"])
        with tempfile.TemporaryDirectory(prefix="promotion-admission-record-") as directory:
            path = Path(directory) / "record.json"
            env = {
                **boundary.environment(), "ADMITTED_SOURCE": boundary.DEV,
                "ADMITTED_MAIN": boundary.BASE, "ADMISSION_PATH": str(path),
            }
            with boundary.mock.patch.dict(boundary.os.environ, env, clear=True):
                exec(compile(code, "<literal-source-admission>", "exec"), {})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {
                "schema": "copilot-extensions.promotion-source-admission", "version": 1,
                "source": boundary.DEV, "main_base": boundary.BASE, "run": 123, "attempt": 4,
            })
            for name, value in (
                ("ADMITTED_SOURCE", "invalid"), ("ADMITTED_MAIN", "invalid"),
                ("GITHUB_RUN_ID", "0"), ("GITHUB_RUN_ATTEMPT", "-1"),
            ):
                with self.subTest(name=name):
                    invalid_path = Path(directory) / f"invalid-{name}.json"
                    with (
                        boundary.mock.patch.dict(boundary.os.environ, {
                            **env, "ADMISSION_PATH": str(invalid_path), name: value,
                        }, clear=True),
                        self.assertRaises(RuntimeError),
                    ):
                        exec(compile(code, "<literal-source-admission>", "exec"), {})
                    self.assertFalse(invalid_path.exists())

    def test_literal_queued_admission_rechecks_retirement_using_the_real_cli(self) -> None:
        root = Path(__file__).resolve().parents[1]
        retirement = next(
            step for step in self.workflow["jobs"]["gate"]["steps"]
            if "--automatic-source" in step.get("run", "")
        )
        with tempfile.TemporaryDirectory(prefix="queued-promotion-admission-") as directory:
            transport = Path(directory) / "transport.py"
            transport.write_text(
                """import os,runpy,sys
from pathlib import Path
sys.path.insert(0,os.environ["TEST_TOOLS"])
from test_promotion_dispatch import Backend, state, admit, DEV
import verify_promotion_workflows
backend=Backend()
backend.pr["merged"]=False
if os.environ["TEST_MODE"]=="retired":
    backend.candidate=state(DEV)
elif os.environ["TEST_MODE"]=="owned":
    admit(backend,500)
verify_promotion_workflows._api=backend
assert sys.argv[1:3]==["-I","-B"]
script=Path(os.environ["TEST_TOOLS"])/"promotion_lifecycle.py"
sys.argv=[str(script),*sys.argv[4:]]
runpy.run_path(str(script),run_name="__main__")
""",
                encoding="utf-8", newline="\n",
            )
            prefix = 'python() { "$PYTHON" -I -B "$TRANSPORT_HELPER" "$@"; }\n'
            for mode in ("ready", "retired", "owned"):
                with self.subTest(mode=mode):
                    result = self.shell(prefix + retirement["run"], {
                        "GITHUB_REPOSITORY": "example/project", "AUTOMATIC_SOURCE": "b" * 40,
                        "AUTOMATIC_MAIN": "e" * 40,
                        "PYTHON": Path(sys.executable).as_posix(),
                        "TRANSPORT_HELPER": transport.as_posix(),
                        "TEST_TOOLS": (root / "tools").as_posix(), "TEST_MODE": mode,
                    })
                    self.assertEqual(result.returncode == 0, mode == "ready", result.stderr)
                    if mode == "retired":
                        self.assertIn("retired source is unchanged", result.stderr)
                        self.assertEqual(result.output, "")
                    elif mode == "owned":
                        self.assertIn("already owns this source and Main base", result.stderr)
                        self.assertEqual(result.output, "")

    def execute_waiter(self, provider: boundary.Provider, env: dict[str, str]) -> None:
        harness = boundary.BoundaryTests("test_pending_checks_then_exact_nonadmin_merge")
        harness.merge = self.waiter
        harness.execute(provider, env)

    def test_job_only_merge_retry_reuses_original_complete_qualification(self) -> None:
        env, provider = boundary.environment(), boundary.Provider()
        env["GITHUB_RUN_ATTEMPT"] = "5"
        self.execute_waiter(provider, env)
        self.assertTrue(provider.merged)
        self.assertEqual(provider.output.getvalue(), f"merge_sha={boundary.MERGED}\n")
        self.assertFalse(any("dispatches" in argument for call in provider.calls for argument in call))

    def test_foreign_future_or_missing_qualification_origin_cannot_merge(self) -> None:
        for owner in (None, "", "124-4", "123-5", "invalid"):
            with self.subTest(owner=owner):
                env, provider = boundary.environment(), boundary.Provider()
                if owner is None:
                    env.pop("QUALIFICATION_PROMOTION_RUN")
                else:
                    env["QUALIFICATION_PROMOTION_RUN"] = owner
                with self.assertRaises((RuntimeError, KeyError, ValueError)):
                    self.execute_waiter(provider, env)
                self.assertFalse(provider.merged)
                self.assertEqual(provider.calls, [])

    def test_uncertain_successful_merge_response_is_reconciled_without_another_write(self) -> None:
        class UncertainProvider(boundary.Provider):
            def run(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                result = super().run(argv, **kwargs)
                if argv[1:3] == ["pr", "merge"]:
                    raise subprocess.TimeoutExpired(argv, 60, stderr=b"response lost")
                return result

        provider = UncertainProvider()
        self.execute_waiter(provider, boundary.environment())
        self.assertTrue(provider.merged)
        self.assertEqual(sum(call[1:3] == ["pr", "merge"] for call in provider.calls), 1)
        self.assertEqual(provider.output.getvalue(), f"merge_sha={boundary.MERGED}\n")

    def test_confirmed_policy_refusal_preserves_the_error_without_repeating_a_write(self) -> None:
        error = subprocess.CalledProcessError(1, ["gh", "pr", "merge"], stderr="policy refused")

        class RefusingProvider(boundary.Provider):
            def run(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                if argv[1:3] == ["pr", "merge"]:
                    self.calls.append(argv)
                    raise error
                return super().run(argv, **kwargs)

        provider = RefusingProvider()
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            self.execute_waiter(provider, boundary.environment())
        self.assertIs(raised.exception, error)
        self.assertEqual(sum(call[1:3] == ["pr", "merge"] for call in provider.calls), 1)
        self.assertFalse(provider.merged)
        self.assertEqual(provider.output.getvalue(), "")

    def test_already_released_source_does_not_repeat_validation_but_manual_qualification_can(self) -> None:
        check = boundary.script(boundary.step(self.jobs["gate"], "check"))
        check = check.replace("${{ github.sha }}", boundary.DEV)
        prefix = r"""
git() {
    case "$1" in
        fetch|merge-base) return 0 ;;
        rev-parse) printf '%s\n' "$DEV_SHA" ;;
        *) return 73 ;;
    esac
}
"""
        for mode, released, expected in (
            ("false", boundary.DEV, "false"), ("true", boundary.DEV, "true"),
            ("false", boundary.BASE, "true"),
        ):
            with self.subTest(mode=mode, released=released):
                env = {
                    **boundary.environment(), "QUALIFICATION_ONLY": mode, "RELEASED_SOURCE": released,
                    "GITHUB_EVENT_NAME": "repository_dispatch", "GITHUB_REF": "refs/heads/main",
                    "DEFAULT_REF": "refs/heads/main",
                }
                result = self.shell(prefix + check, env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"is_dev={expected}\n", result.output)

    def open_pr(self, rows: list[dict[str, Any]], changes: dict[str, str] | None = None) -> tuple[boundary._ShellResult, bool]:
        with tempfile.TemporaryDirectory(prefix="qualified-pr-opening-") as directory:
            trace = Path(directory) / "created"
            env = {
                **boundary.environment(), "GITHUB_REPOSITORY_OWNER": "example",
                "PYTHON": Path(sys.executable).as_posix(), "LIVE_MAIN": boundary.BASE,
                "ACTING_USER": "example",
                "EXISTING_PRS": json.dumps(rows), "CREATE_TRACE": trace.as_posix(),
                **(changes or {}),
            }
            prefix = r"""
python() { "$PYTHON" -I -S "$@"; }
gh() {
    if [ "$1" = "api" ]; then
        if [ "$2" = "user" ]; then printf '%s\n' "$ACTING_USER"
        else printf '%s\n' "$LIVE_MAIN"; fi
        return 0
    fi
    if [ "$1" != "pr" ]; then return 71; fi
    case "$2" in
        list) printf '%s\n' "$EXISTING_PRS" ;;
        create)
            printf 'created\n' >>"$CREATE_TRACE"
            printf 'https://github.com/example/project/pull/456\n' ;;
        *) return 72 ;;
    esac
}
"""
            result = self.shell(prefix + self.opening, env)
            return result, trace.exists()

    @staticmethod
    def existing_pr() -> dict[str, Any]:
        return {
            "number": 456, "headRefOid": boundary.CANDIDATE,
            "headRefName": "release/promote-123", "baseRefName": "main",
            "author": {"login": "example"}, "isCrossRepository": False, "state": "OPEN",
        }

    def test_literal_pr_opening_and_existing_pr_recovery_are_idempotent(self) -> None:
        for rows, expected_write in (([], True), ([self.existing_pr()], False)):
            with self.subTest(existing=bool(rows)):
                result, written = self.open_pr(rows)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.output, "pr_number=456\n")
                self.assertEqual(written, expected_write)

    def test_incomplete_publication_or_changed_generation_never_opens_a_pr(self) -> None:
        for changes in (
            {"PUBLICATION_DIGEST": ""}, {"QUALIFICATION_DIGEST": "invalid"},
            {"SOURCE_RELEASE": "python-artifacts-wrong"},
            {"QUALIFICATION_RUN": "0"}, {"QUALIFICATION_PROMOTION_RUN": "124-4"},
            {"CANDIDATE_BRANCH": "release/promote-124"}, {"LIVE_MAIN": boundary.MERGED},
            {"ACTING_USER": "unexpected-publisher"},
        ):
            with self.subTest(changes=changes):
                result, written = self.open_pr([], changes)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(written)
                self.assertEqual(result.output, "")

    def test_closed_foreign_or_changed_existing_pr_is_not_reopened_or_replaced(self) -> None:
        for change in (
            {"state": "CLOSED"}, {"headRefOid": boundary.DEV},
            {"baseRefName": "dev"}, {"author": {"login": "outside"}},
            {"isCrossRepository": True}, {"number": True},
        ):
            with self.subTest(change=change):
                result, written = self.open_pr([{**self.existing_pr(), **change}])
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(written)
                self.assertEqual(result.output, "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path)
    args = parser.parse_args(argv)
    if args.workflow is not None:
        boundary.WORKFLOW, boundary.EXPLICIT = args.workflow, True
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2, stream=sys.stdout).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
