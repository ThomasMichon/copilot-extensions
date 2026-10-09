"""Exercise the reporter's actual shell step with a network-free gh fixture."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/validate-and-promote.yml"
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Actions step runs on Linux")


def _dispatch_script() -> str:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["report-failure"]["steps"]
    step = next(
        s for s in steps
        if s.get("name") == "Fire the fix-attempt agent for any newly filed issue"
    )
    assert step["if"] == "always() && steps.watchdog.outputs.filed_issue_numbers != ''"
    assert step["env"]["REPO"] == "${{ github.repository }}"
    assert step["env"]["FILED_ISSUE_NUMBERS"] == "${{ steps.watchdog.outputs.filed_issue_numbers }}"
    assert "${{" not in step["run"]
    return step["run"]


def _run(
    tmp_path: Path,
    *,
    state: str = "active",
    second_state: str = "active",
    issues: str = "123 456 789",
    lookup_exit: int = 0,
    second_lookup_exit: int = 0,
    dispatch_exit: int = 0,
    present: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required to exercise the Actions step")
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    gh = binary_dir / "gh"
    gh.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$CALLS_FILE"
if [[ "$1" == api ]]; then
  COUNT=0
  if [[ -f "$STATE_FILE" ]]; then read -r COUNT < "$STATE_FILE"; fi
  COUNT=$((COUNT + 1))
  printf '%s\\n' "$COUNT" > "$STATE_FILE"
  if [[ "$LOOKUP_EXIT" != 0 ]]; then
    echo "metadata lookup failed" >&2
    exit "$LOOKUP_EXIT"
  fi
  if [[ "$COUNT" != 1 && "$SECOND_LOOKUP_EXIT" != 0 ]]; then
    echo "metadata recheck failed" >&2
    exit "$SECOND_LOOKUP_EXIT"
  fi
  if [[ "$COUNT" == 1 ]]; then
    printf '%s\\n' "$INITIAL_STATE"
  else
    printf '%s\\n' "$SECOND_STATE"
  fi
elif [[ "$1" == workflow && "$2" == run ]]; then
  if [[ "$DISPATCH_EXIT" != 0 ]]; then echo "dispatch failed" >&2; fi
  exit "$DISPATCH_EXIT"
else
  echo "unexpected gh invocation" >&2
  exit 98
fi
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    if present:
        compiled = tmp_path / ".github/workflows/ci-failure-fix-attempt.lock.yml"
        compiled.parent.mkdir(parents=True)
        compiled.write_text("", encoding="utf-8")
    calls_file = tmp_path / "calls"
    env = {
        **os.environ,
        "PATH": f"{binary_dir}{os.pathsep}{os.environ['PATH']}",
        "REPO": "example/project",
        "FILED_ISSUE_NUMBERS": issues,
        "INITIAL_STATE": state,
        "SECOND_STATE": second_state,
        "LOOKUP_EXIT": str(lookup_exit),
        "SECOND_LOOKUP_EXIT": str(second_lookup_exit),
        "DISPATCH_EXIT": str(dispatch_exit),
        "CALLS_FILE": str(calls_file),
        "STATE_FILE": str(tmp_path / "state-count"),
    }
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", _dispatch_script()],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    calls = calls_file.read_text(encoding="utf-8").splitlines() if calls_file.exists() else []
    return result, calls


@pytest.mark.parametrize("state", ["disabled_manually", "disabled_inactivity", "disabled_fork"])
def test_disabled_workflow_is_one_lookup_and_zero_dispatches(tmp_path: Path, state: str) -> None:
    result, calls = _run(tmp_path, state=state)
    assert result.returncode == 0
    assert len(calls) == 1
    assert calls[0] == (
        "api repos/example/project/actions/workflows/"
        "ci-failure-fix-attempt.lock.yml --jq .state"
    )
    assert "skipping all dispatches" in result.stdout


def test_active_workflow_dispatches_only_the_supplied_issue_batch(tmp_path: Path) -> None:
    result, calls = _run(tmp_path)
    assert result.returncode == 0
    assert sum(c.startswith("api ") for c in calls) == 1
    assert [c for c in calls if c.startswith("workflow run ")] == [
        f"workflow run ci-failure-fix-attempt.lock.yml --repo example/project -f item_number={number}"
        for number in (123, 456, 789)
    ]


def test_disable_race_stops_after_one_failed_request_without_retry(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, second_state="disabled_manually", dispatch_exit=22)
    assert result.returncode == 0
    assert sum(c.startswith("api ") for c in calls) == 2
    assert sum(c.startswith("workflow run ") for c in calls) == 1
    assert "skipping remaining dispatches" in result.stdout


def test_repeated_issue_numbers_are_dispatched_only_once(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, issues="123 123 456 123 456")
    assert result.returncode == 0
    assert sum(c.startswith("api ") for c in calls) == 1
    assert [c for c in calls if c.startswith("workflow run ")] == [
        f"workflow run ci-failure-fix-attempt.lock.yml --repo example/project -f item_number={number}"
        for number in (123, 456)
    ]


def test_real_dispatch_failure_stays_failed_and_does_not_retry(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, dispatch_exit=22)
    assert result.returncode == 22
    assert sum(c.startswith("workflow run ") for c in calls) == 1
    assert sum(c.startswith("api ") for c in calls) == 2
    assert "::error::" in result.stdout


def test_metadata_failure_stays_failed_without_dispatch(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, lookup_exit=7)
    assert result.returncode == 7
    assert len(calls) == 1


def test_failed_race_recheck_is_not_hidden_as_a_pause(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, dispatch_exit=22, second_lookup_exit=7)
    assert result.returncode == 7
    assert sum(c.startswith("api ") for c in calls) == 2
    assert sum(c.startswith("workflow run ") for c in calls) == 1
    assert "skipping remaining dispatches" not in result.stdout


@pytest.mark.parametrize("state", ["", "null", "deleted", "unexpected"])
def test_unknown_state_is_not_silently_treated_as_disabled(tmp_path: Path, state: str) -> None:
    result, calls = _run(tmp_path, state=state)
    assert result.returncode == 1
    assert len(calls) == 1
    assert "Unexpected fix-attempt workflow state" in result.stdout


@pytest.mark.parametrize("issues", ["", "  \n\t"])
def test_empty_batch_performs_no_api_calls(tmp_path: Path, issues: str) -> None:
    result, calls = _run(tmp_path, issues=issues)
    assert result.returncode == 0
    assert calls == []


@pytest.mark.parametrize("issues", ["123 nope", "0", "-1", "123;touch injected"])
def test_invalid_batch_is_data_and_never_dispatches(tmp_path: Path, issues: str) -> None:
    result, calls = _run(tmp_path, issues=issues)
    assert result.returncode == 1
    assert calls == []
    assert not (tmp_path / "injected").exists()


def test_bootstrap_without_compiled_workflow_remains_a_clean_skip(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, present=False)
    assert result.returncode == 0
    assert calls == []


def test_issue_wildcard_cannot_expand_into_a_numeric_file(tmp_path: Path) -> None:
    (tmp_path / "123").write_text("", encoding="utf-8")
    result, calls = _run(tmp_path, issues="123*")
    assert result.returncode == 1
    assert calls == []
