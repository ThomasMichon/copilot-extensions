"""Shared assertions and Git fixture data for legacy and source-owned controllers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml


def workflow(root: Path) -> dict[str, Any]:
    return yaml.safe_load((root / ".github/workflows/validate-and-promote.yml").read_text("utf-8"))


def source_owned(jobs: dict[str, Any]) -> bool:
    return "qualify-candidate" in jobs


def heredoc(script: str) -> str:
    bodies = re.findall(r"<<'PY'\s*\n(.*?)^PY\s*$", script, re.M | re.S)
    assert len(bodies) == 1, "expected one complete literal workflow Python block"
    return bodies[0]


def qualification_response() -> dict[str, Any]:
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
    assert set(qualifier["needs"]) == {"gate", "promote"}
    assert set(publisher["needs"]) == {"gate", "python-artifacts", "promote", "qualify-candidate"}
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
