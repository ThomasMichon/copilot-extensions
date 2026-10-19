"""Pure impact and real pinned-Git contracts for pre-runner CI admission."""

from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_job_admission as admission  # noqa: E402


@pytest.mark.parametrize("path,expected", [
    ("efforts/active/example/README.md", (False, False, False)),
    ("visions/example/README.md", (False, False, False)),
    ("docs/pipelines.md", (False, False, False)),
    ("README.md", (False, False, False)),
    ("fleet-manager/src/fleet_manager/planning.py", (True, False, False)),
    ("worktree-manager/src/worktree_manager/picker.py", (False, True, False)),
    ("services/dispatch/pyproject.toml", (False, False, True)),
    ("plugins/agent-dispatch/src/agent_dispatch/serve.py", (False, True, True)),
    ("plugins/agent-dispatch/pivots/workers-config.json", (False, True, True)),
    ("plugins/agent-worktrees/src/agent_worktrees/picker.py", (False, True, False)),
    ("plugins/agent-bridge/src/agent_bridge/client.py", (False, True, False)),
    ("plugins/future-plugin/pivots/manifest.json", (False, True, False)),
    ("services/index/src/service.py", (False, False, False)),
    ("libs/agent-procutil/src/agent_procutil/__init__.py", (True, True, True)),
    ("libs/new-transitive-dependency/pyproject.toml", (True, True, True)),
    ("tools/run-standalone-tests.py", (True, True, True)),
    ("tools/materialize_main.py", (True, True, True)),
    ("tools/ci_job_admission.py", (True, True, True)),
    (".github/workflows/ci.yml", (True, True, True)),
    (".github/actions/future-policy/action.yml", (True, True, True)),
    ("TESTING.md", (True, True, True)),
    ("pyproject.toml", (True, True, True)),
    ("conftest.py", (True, True, True)),
    ("efforts/active/example/artifacts/probe.py", (True, True, True)),
    ("docs/fixture.json", (True, True, True)),
    ("new-consumer/src/main.py", (True, True, True)),
    ("unrelated/fleet-manager/src/main.py", (True, True, True)),
])
def test_dependency_and_unknown_paths(path: str, expected: tuple[bool, bool, bool]) -> None:
    result = admission.select_jobs([path], event="pull_request")
    assert (result.fleet, result.manager, result.dispatch) == expected
    assert result.outputs().splitlines()[0] == "admission_validated=true"
    assert result.chat is all(expected)


@pytest.mark.parametrize("path", [
    "services/chat/pyproject.toml",
    "services/chat/src/agent_chat_service/store.py",
    "services/chat/tests/test_store.py",
])
def test_chat_core_selects_only_its_own_standalone_family(path: str) -> None:
    result = admission.select_jobs([path], event="pull_request")
    assert (result.fleet, result.manager, result.dispatch, result.chat) == (False, False, False, True)
    assert "chat_contracts=true" in result.outputs()


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "repository_dispatch", "future-event"])
def test_non_pr_events_keep_full_coverage(event: str) -> None:
    assert admission.select_jobs([], event=event) == admission.Admission(
        True, True, True, "full-event", chat=True,
    )


@pytest.mark.parametrize("path", ["", "../outside", "/outside", "a//b", "a/./b", "a\\b", "a\nb", "C:/x"])
def test_ambiguous_paths_fail_instead_of_emitting_empty_success(path: str) -> None:
    with pytest.raises(ValueError, match="repository-relative"):
        admission.select_jobs([path], event="pull_request")


@pytest.mark.parametrize("widening", ["libs/shared/source.py", "new-consumer/source.py", "TESTING.md"])
@pytest.mark.parametrize("invalid_first", [False, True])
def test_widening_never_short_circuits_validation(widening: str, invalid_first: bool) -> None:
    paths = ["../invalid.py", widening] if invalid_first else [widening, "../invalid.py"]
    with pytest.raises(ValueError, match="repository-relative"):
        admission.select_jobs(iter(paths), event="pull_request")


def test_union_and_verified_empty_diff() -> None:
    selected = admission.select_jobs(
        ["worktree-manager/pyproject.toml", "fleet-manager/tests/test_plan.py"], event="pull_request",
    )
    assert (selected.fleet, selected.manager, selected.dispatch) == (True, True, False)
    empty = admission.select_jobs([], event="pull_request")
    assert (empty.fleet, empty.manager, empty.dispatch) == (False, False, False)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "-c", "core.hooksPath=", *args],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_actual_cli_uses_pinned_diff_and_refuses_unavailable_evidence(tmp_path: Path) -> None:
    git(tmp_path, "-c", "init.templateDir=", "init", "-q")
    git(tmp_path, "config", "user.name", "CI fixture")
    git(tmp_path, "config", "user.email", "ci@example.com")
    doc = tmp_path / "efforts" / "example.md"
    doc.parent.mkdir()
    doc.write_text("initial\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    doc.write_text("proposal\n", encoding="utf-8")
    git(tmp_path, "commit", "-qam", "docs only")
    head = git(tmp_path, "rev-parse", "HEAD")
    source = tmp_path / "fleet-manager" / "source.py"
    source.parent.mkdir()
    source.write_text("pass\n", encoding="utf-8")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "later unrelated checkout")
    selected = admission.select_jobs(admission.changed_paths(tmp_path, base, head), event="pull_request")
    assert not selected.fleet  # the current checkout is not the requested PR head
    result = subprocess.run(
        [sys.executable, str(Path(admission.__file__)), "--event", "pull_request",
         "--base", "0" * 40, "--head", head, "--repository", str(tmp_path)],
        capture_output=True, text=True, timeout=40,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 1
    assert "cannot establish changed-path admission" in result.stderr
    assert "admission_validated" not in result.stdout
    moved = tmp_path / "worktree-manager" / "source.py"
    moved.parent.mkdir()
    before_move = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "mv", str(source), str(moved))
    git(tmp_path, "commit", "-qm", "move across consumers")
    paths = admission.changed_paths(tmp_path, before_move, git(tmp_path, "rev-parse", "HEAD"))
    assert set(paths) == {"fleet-manager/source.py", "worktree-manager/source.py"}
    selected = admission.select_jobs(paths, event="pull_request")
    assert selected.fleet and selected.manager and not selected.dispatch


def test_incomplete_commit_ids_are_not_accepted(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="full base and head"):
        admission.changed_paths(tmp_path, "HEAD", "0" * 40)


def workflow() -> dict:
    return yaml.safe_load(
        (Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("job,key", [
    ("fleet-native-linux", "fleet_contracts"), ("fleet-native-windows", "fleet_contracts"),
    ("worktree-manager", "manager_contracts"), ("agent-dispatch-service", "dispatch_contracts"),
    ("agent-chat-service-linux", "chat_contracts"),
    ("agent-chat-service-windows", "chat_contracts"),
])
def test_actual_job_admission_and_full_manual_override(job: str, key: str) -> None:
    cfg = workflow()["jobs"][job]
    assert cfg["needs"] == "discover"
    expression = cfg["if"]
    assert "always() && !cancelled()" in expression
    for event, base, promotion, discovery, validated, selected, cancelled, expected in (
        ("pull_request", "dev", "", "success", "true", "false", False, False),
        ("pull_request", "dev", "", "success", "true", "true", False, True),
        ("pull_request", "main", "", "success", "true", "true", False, False),
        ("pull_request", "dev", "", "skipped", "true", "true", False, False),
        ("pull_request", "dev", "", "failure", "true", "true", False, False),
        ("pull_request", "dev", "", "success", "", "true", False, False),
        ("pull_request", "dev", "", "success", "true", "true", True, False),
        ("push", "", "", "success", "true", "true", False, True),
        ("push", "", "", "failure", "", "", False, True),
        ("workflow_dispatch", "", "123", "success", "true", "true", False, True),
        ("workflow_dispatch", "", "123", "failure", "", "", False, True),
        ("workflow_dispatch", "", "", "skipped", "", "", False, True),
        ("workflow_dispatch", "", "", "skipped", "", "", True, False),
    ):
        replacements = {
            "always()": "True",
            "!cancelled()": repr(not cancelled),
            "github.event_name": repr(event),
            "github.event.pull_request.base.ref": repr(base),
            "inputs.promotion_run": repr(promotion),
            "needs.discover.result": repr(discovery),
            "needs.discover.outputs.admission_validated": repr(validated),
            f"needs.discover.outputs.{key}": repr(selected),
        }
        translated = expression
        for name, value in replacements.items():
            translated = translated.replace(name, value)
        translated = translated.replace("&&", " and ").replace("||", " or ")
        assert eval(translated, {"__builtins__": {}}, {}) is expected


def gate_result(needs: dict) -> subprocess.CompletedProcess[str]:
    step = workflow()["jobs"]["pr-gate"]["steps"][0]
    code = step["run"].split("python3 - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    return subprocess.run(
        [sys.executable, "-c", code], env=dict(os.environ, NEEDS_JSON=json.dumps(needs)),
        capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def valid_needs(*, selected: bool) -> dict:
    jobs = workflow()["jobs"]
    needs = {name: {"result": "skipped"} for name in jobs["pr-gate"]["needs"]}
    outputs = {"admission_validated": "true"}
    for key in ("fleet_contracts", "manager_contracts", "dispatch_contracts", "chat_contracts"):
        outputs[key] = str(selected).lower()
    needs["discover"] = {"result": "success", "outputs": outputs}
    if selected:
        for name in (
            "fleet-native-linux", "fleet-native-windows", "worktree-manager",
            "agent-dispatch-service", "agent-chat-service-linux", "agent-chat-service-windows",
        ):
            needs[name] = {"result": "success"}
    return needs


def test_actual_discovery_exports_pinned_selection_and_required_noop() -> None:
    jobs = workflow()["jobs"]
    discovery = jobs["discover"]
    step = next(item for item in discovery["steps"] if item.get("id") == "admission")
    assert "ci_job_admission.py" in step["run"] and '>> "$GITHUB_OUTPUT"' in step["run"]
    assert "pull_request.head.sha" in step["env"]["HEAD_SHA"]
    assert "pull_request.base.sha" in step["env"]["BASE_SHA"]
    for key in (
        "admission_validated", "fleet_contracts", "manager_contracts",
        "dispatch_contracts", "chat_contracts",
    ):
        assert discovery["outputs"][key] == "${{ steps.admission.outputs." + key + " }}"
    assert jobs["pr-gate"]["name"] == "PR gate (required check)"
    assert "!cancelled()" in jobs["pr-gate"]["if"]
    assert gate_result(valid_needs(selected=False)).returncode == 0
    assert gate_result(valid_needs(selected=True)).returncode == 0


@pytest.mark.parametrize("broken", [
    "missing", "skipped", "failure", "invalid", "invalid-chat", "no-outputs",
    "selected-skipped", "selected-chat-linux-skipped", "selected-chat-windows-skipped",
])
def test_actual_aggregate_refuses_unknown_or_incomplete_admission(broken: str) -> None:
    needs = valid_needs(selected=broken in {
        "selected-skipped", "selected-chat-linux-skipped", "selected-chat-windows-skipped",
    })
    if broken == "missing":
        del needs["discover"]
    elif broken in {"skipped", "failure"}:
        needs["discover"]["result"] = broken
    elif broken == "invalid":
        needs["discover"]["outputs"]["fleet_contracts"] = ""
    elif broken == "invalid-chat":
        needs["discover"]["outputs"]["chat_contracts"] = ""
    elif broken == "no-outputs":
        del needs["discover"]["outputs"]
    elif broken == "selected-skipped":
        needs["fleet-native-windows"]["result"] = "skipped"
    else:
        platform = "linux" if broken == "selected-chat-linux-skipped" else "windows"
        needs[f"agent-chat-service-{platform}"]["result"] = "skipped"
    result = gate_result(needs)
    assert result.returncode == 1, result.stdout + result.stderr
