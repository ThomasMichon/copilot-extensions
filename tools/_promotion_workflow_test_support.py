"""Shared assertions and Git fixture data for legacy and source-owned controllers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

def workflow(root: Path) -> dict[str, Any]:
    import yaml

    return yaml.safe_load((root / ".github/workflows/validate-and-promote.yml").read_text("utf-8"))


def source_owned(jobs: dict[str, Any]) -> bool:
    return "qualify-candidate" in jobs


def unit_profile(jobs: dict) -> str:
    return "candidate-units-v3" if "compose-candidate" in jobs else "source-units-v2"


def source_workflow(root: Path, name: str) -> dict[str, Any]:
    import yaml

    return yaml.safe_load(source_workflow_text(root, name))


def source_owned_controller(root: Path) -> bool:
    controller = (root / ".github/workflows/validate-and-promote.yml").read_text("utf-8")
    return (
        (root / ".github/promotion-control.json").is_file()
        and re.search(r"^  qualify-candidate:\s*$", controller, re.M) is not None
    )


def source_workflow_text(root: Path, name: str) -> str:
    """Keep source contracts separate from a generated checkout's preserved controller."""
    if not source_owned_controller(root):
        return (root / ".github/workflows" / name).read_text("utf-8")
    state = json.loads((root / ".github/release-pipeline-state.json").read_text("utf-8"))
    dev = state["last_promotion"]["dev_head"]
    assert isinstance(dev, str) and re.fullmatch(r"[0-9a-f]{40}", dev), "invalid recorded Dev source"
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "--no-replace-objects", *args], cwd=root, env=env,
            check=True, capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ).stdout

    assert git("rev-parse", "--verify", f"{dev}^{{commit}}").strip() == dev
    return git("show", f"{dev}:.github/workflows/{name}")


def heredoc(script: str) -> str:
    bodies = re.findall(r"<<'PY'\s*\n(.*?)^PY\s*$", script, re.M | re.S)
    assert len(bodies) == 1, "expected one complete literal workflow Python block"
    return bodies[0]


def qualification_response() -> dict[str, Any]:
    if os.environ.get("QUALIFICATION_PROFILE") in ("source-units-v2", "candidate-units-v3"):
        return {
            "id": int(os.environ["QUALIFICATION_RUN"]),
            "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"]),
            "event": "workflow_dispatch", "head_sha": os.environ["CONTROL_SHA"],
            "head_branch": "main", "path": ".github/workflows/validate-and-promote.yml",
            "status": "in_progress", "conclusion": None,
        }
    return {
        "id": int(os.environ["QUALIFICATION_RUN"]),
        "run_attempt": int(os.environ["QUALIFICATION_ATTEMPT"]),
        "event": "workflow_dispatch", "head_sha": os.environ["CANDIDATE_SHA"],
        "head_branch": os.environ["CANDIDATE_BRANCH"], "path": ".github/workflows/ci.yml",
        "status": "completed", "conclusion": "success",
        "display_title": (
            f"CI promotion {os.environ['GITHUB_RUN_ID']}-{os.environ['GITHUB_RUN_ATTEMPT']} "
            f"at {os.environ['CANDIDATE_SHA']}"
        ),
    }


def api_response(argv: list[str], rules: list[dict[str, Any]]) -> str:
    if argv[-1].endswith("/rules/branches/main"):
        return json.dumps(rules)
    if os.environ.get("QUALIFICATION_PROFILE") in ("source-units-v2", "candidate-units-v3"):
        if argv[-1].endswith("/actions/jobs/" + os.environ["QUALIFICATION_UNIT_JOB"]):
            return json.dumps({
                "id": int(os.environ["QUALIFICATION_UNIT_JOB"]),
                "run_id": int(os.environ["QUALIFICATION_RUN"]),
                "run_attempt": int(os.environ["QUALIFICATION_ATTEMPT"]),
                "head_sha": os.environ["CONTROL_SHA"], "name": "Eligible source units with coverage",
                "status": "completed", "conclusion": "success", "steps": [{
                    "name": "Upload promotion unit evidence", "status": "completed", "conclusion": "success",
                }],
            })
        if argv[-1].endswith("/actions/artifacts/" + os.environ["QUALIFICATION_UNIT_ARTIFACT"]):
            return json.dumps({
                "id": int(os.environ["QUALIFICATION_UNIT_ARTIFACT"]),
                "name": "promotion-unit-evidence", "expired": False,
                "digest": os.environ["QUALIFICATION_UNIT_ARTIFACT_DIGEST"],
                "workflow_run": {
                    "id": int(os.environ["QUALIFICATION_RUN"]),
                    "head_branch": "main", "head_sha": os.environ["CONTROL_SHA"],
                },
            })
    assert argv[-1] == (
        f"repos/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['QUALIFICATION_RUN']}"
    ), "unexpected API operation"
    return json.dumps(qualification_response())


def assert_source_owned_boundary(jobs: dict[str, Any]) -> None:
    merge = jobs["merge-promotion"]
    assert set(merge["needs"]) == {"gate", "promote", "qualify-candidate", "publish-python-artifacts"}
    for name in ("qualify-candidate", "publish-python-artifacts"):
        assert f"needs.{name}.result == 'success'" in merge["if"]
    for expression in (
        "needs.qualify-candidate.outputs.qualification_run",
        "needs.qualify-candidate.outputs.qualification_attempt",
        "needs.qualify-candidate.outputs.qualification_digest",
        "needs.publish-python-artifacts.outputs.publication_digest",
        "needs.publish-python-artifacts.outputs.source_release",
    ):
        assert f"{expression} != ''" in merge["if"]
    qualifier, publisher, record = (jobs[name] for name in (
        "qualify-candidate", "publish-python-artifacts", "record-promotion",
    ))
    units = {"source-units"} if "source-units" in jobs else set()
    assert set(qualifier["needs"]) == {"gate", "promote"} | units
    assert set(publisher["needs"]) == {"gate", "python-artifacts", "promote", "qualify-candidate"} | units
    if units:
        frozen = "compose-candidate" in jobs
        if frozen:
            assert jobs["compose-candidate"]["needs"] == "gate"
            assert set(jobs["source-units"]["needs"]) == {"gate", "compose-candidate"}
        else:
            assert jobs["source-units"]["needs"] == "gate"
        assert set(jobs["build-python-artifacts"]["needs"]) == {"gate"} | (
            {"compose-candidate"} if frozen else set()
        )
        assert "source-units" in jobs["python-artifacts"]["needs"]
        assert unit_profile(jobs) in merge["if"]
        qualify_step = next(step for step in qualifier["steps"] if step.get("id") == "qualify")
        assert "--profile " + unit_profile(jobs) in qualify_step["run"]
        assert qualify_step["env"]["UNIT_PRODUCER_ATTEMPT"] == "${{ needs.source-units.outputs.attempt }}"
        assert '--promotion-run "$GITHUB_RUN_ID-$UNIT_PRODUCER_ATTEMPT"' in qualify_step["run"]
        assert not any(name in jobs for name in (
            "full", "worktree-manager", "agent-index-service-linux", "agent-index-service-windows",
            "guards-full-sweep", "postmerge-workflows",
        ))
    assert "needs.qualify-candidate.result == 'success'" in publisher["if"]
    assert set(record["needs"]) == {"merge-promotion", "publish-python-artifacts"}
    assert record["continue-on-error"] is True
    assert "postmerge-workflows" not in jobs
    for name in ("qualify-candidate", "publish-python-artifacts", "merge-promotion"):
        assert name in jobs["report-failure"]["needs"]
        assert f"needs.{name}.result == 'failure'" in jobs["report-failure"]["if"]
    for name in ("build-python-artifacts", "python-artifacts", "promote",
                 "qualify-candidate", "publish-python-artifacts", "record-promotion"):
        checkouts = [step["with"] for step in jobs[name]["steps"]
                     if step.get("with", {}).get("path") == "reviewed-helpers"]
        assert len(checkouts) == 1
        assert checkouts[0]["ref"] == "${{ env.RELEASE_HELPERS_SHA }}"
        assert checkouts[0]["persist-credentials"] is False
    publication = next(step for step in publisher["steps"] if step.get("id") == "publish")
    for argument in (
        "publish-candidate", "--repo", "--candidate-root candidate", "--promotion-run",
        "--qualification-receipt", "--main-before", "--github-output",
    ):
        assert argument in publication["run"]
    assert "--merged-commit" not in publication["run"]
    if units:
        assert "--qualification-profile " + unit_profile(jobs) in publication["run"]
    assert not any("publish_python_artifacts.py" in step.get("run", "") for step in record["steps"])
    assert "verify_promotion_workflows.py" not in json.dumps(record)


def reviewed_helpers(repo: Path, source: Path) -> tuple[Path, str]:
    """Create a distinct real Git helper checkout inside a disposable test repo."""
    helpers = repo / "reviewed-helpers"
    if not helpers.exists():
        shutil.copytree(repo / "tools", helpers / "tools")
        for name in ("promote_release.py", "promotion_python_artifacts.py",
                     "promotion_control.py", "promotion_candidate.py"):
            shutil.copyfile(source / "tools" / name, helpers / "tools" / name)
        inspector = Path("libs") / "installer-engine" / "installer_wheel_inputs.py"
        (helpers / inspector).parent.mkdir(parents=True)
        shutil.copyfile(source / inspector, helpers / inspector)
        for argv in (
            ["init", "-q", "-b", "main"], ["add", "-A"],
            ["-c", "user.name=Test", "-c", "user.email=test@example.com",
             "commit", "-q", "-m", "reviewed helper fixture"],
        ):
            subprocess.run(
                ["git", *argv], cwd=helpers, check=True, capture_output=True, timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=helpers, check=True,
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return helpers, result.stdout.strip()
