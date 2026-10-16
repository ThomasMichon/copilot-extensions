"""Immutable post-merge dispatch identity, coverage and failure contracts."""
from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from _promotion_workflow_test_support import assert_source_owned_boundary, source_owned

import verify_promotion_workflows as verification

SHA = "a" * 40
TAG = "promote-20260102030405-aaaaaaaa"
RUN = "123-1"
CREATED_AT = "2026-01-02T00:00:00Z"
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def backend(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "synthetic-actions-authority")
    monkeypatch.setattr(verification.time, "sleep", lambda _: None)
    state = {
        "calls": [], "dispatched": [], "runs": {}, "main": "ahead",
        "tag_sha": SHA, "annotation": True, "conclusion": "success", "missing": set(),
        "control_sha": SHA, "ancestry": {},
        "promotion": {"id": 123, "created_at": CREATED_AT, "run_started_at": "2026-01-03T00:00:00Z"},
    }

    def api(path, *, body=None, timeout=60):
        state["calls"].append((path, body))
        assert 0 < timeout <= 60
        if "/git/ref/" in path:
            return {"object": {"type": "tag" if state["annotation"] else "commit",
                               "sha": "b" * 40 if state["annotation"] else state["tag_sha"]}}
        if "/git/tags/" in path:
            return {"object": {"type": "commit", "sha": state["tag_sha"]}}
        if "/compare/" in path:
            comparison = path.split("/compare/")[1]
            return {"status": state["ancestry"].get(comparison, state["main"])}
        if path == "repos/example/project/actions/runs/123":
            return state["promotion"]
        if "/actions/workflows/" in path:
            filename = path.split("/actions/workflows/")[1].split("/")[0].split("?")[0]
            name, required = verification.WORKFLOWS[filename]
            workflow_id = list(verification.WORKFLOWS).index(filename) + 10
            if "/dispatches" in path:
                branch = "main" if filename == "workflow-lockdown-guard.yml" else TAG
                assert body == {"ref": branch, "inputs": {"promotion_sha": SHA, "promotion_run": RUN}}
                state["dispatched"].append(filename)
                state["runs"][filename] = [{
                    "id": workflow_id + 100, "workflow_id": workflow_id,
                    "event": "workflow_dispatch",
                    "head_sha": state["control_sha"] if branch == "main" else SHA,
                    "head_branch": branch,
                    "display_title": f"{name} promotion {RUN} at {SHA}",
                    "status": "completed", "conclusion": state["conclusion"],
                }]
                return None
            if "/runs?" in path:
                assert parse_qs(urlsplit(path).query)["created"] == [">=" + CREATED_AT]
                return {"workflow_runs": state["runs"].get(filename, [])}
            return {"id": workflow_id, "state": "active", "path": f".github/workflows/{filename}",
                    "name": name}
        if "/jobs?" in path:
            run_id = int(path.split("/runs/")[1].split("/")[0])
            filename = list(verification.WORKFLOWS)[run_id - 110]
            required = verification.WORKFLOWS[filename][1]
            return {"jobs": [
                {"name": name, "conclusion": "skipped" if name in state["missing"] else "success"}
                for name in required
            ]}
        pytest.fail(f"unexpected backend call: {path}")

    monkeypatch.setattr(verification, "_api", api)
    return state


def verify():
    return verification.verify("example/project", SHA, TAG, RUN)


@pytest.mark.parametrize("annotation", [True, False])
def test_dispatches_both_consumers_and_accepts_main_control_ahead_of_release(backend, annotation):
    backend["annotation"] = annotation
    backend["control_sha"] = "c" * 40
    assert verify() == {"ci.yml": 110, "workflow-lockdown-guard.yml": 111}
    assert backend["dispatched"] == list(verification.WORKFLOWS)
    calls = [path for path, _ in backend["calls"]]
    assert sum("/git/ref/" in path for path in calls) == 2
    assert any(f"/compare/{SHA}...{'c' * 40}" in path for path in calls)
    assert any(f"/compare/{'c' * 40}...main" in path for path in calls)
    assert calls.index(next(path for path in calls if "/jobs?" in path)) > max(
        i for i, path in enumerate(calls) if "/dispatches" in path
    )


def test_repeat_verification_uses_the_exact_existing_run_instead_of_dispatching_again(backend):
    assert verify()
    assert verify()
    assert len(backend["dispatched"]) == 2


@pytest.mark.parametrize("duplicates", [False, True])
def test_large_unrelated_history_is_excluded_without_hiding_retries_or_duplicates(
    backend, monkeypatch, duplicates,
):
    verify()
    backend["promotion"]["run_started_at"] = "2026-02-01T00:00:00Z"
    if duplicates:
        backend["runs"]["workflow-lockdown-guard.yml"] *= 2
    api = verification._api
    old_runs = [{"display_title": "unrelated", "created_at": "2026-01-01T00:00:00Z"}] * 1500
    queried = []

    def history(path, **kwargs):
        if "/workflow-lockdown-guard.yml/runs?" not in path:
            return api(path, **kwargs)
        query = parse_qs(urlsplit(path).query)
        queried.append(query)
        rows = old_runs + backend["runs"]["workflow-lockdown-guard.yml"]
        if query.get("created") == [">=" + CREATED_AT]:
            rows = backend["runs"]["workflow-lockdown-guard.yml"]
        page = int(query["page"][0])
        return {"workflow_runs": rows[(page - 1) * 100:page * 100]}

    monkeypatch.setattr(verification, "_api", history)
    if duplicates:
        with pytest.raises(verification.VerificationError, match="ambiguous"):
            verify()
    else:
        assert verify() == {"ci.yml": 110, "workflow-lockdown-guard.yml": 111}
    assert queried and all(query["created"] == [">=" + CREATED_AT] for query in queried)
    assert len(backend["dispatched"]) == 2


@pytest.mark.parametrize("field,value", [
    ("id", 124), ("id", True), ("created_at", None),
    ("created_at", "2026-02-30T00:00:00Z"), ("created_at", "2026-01-02"),
])
def test_invalid_owning_promotion_clock_never_dispatches(backend, field, value):
    backend["promotion"][field] = value
    with pytest.raises(verification.VerificationError, match="creation time"):
        verify()
    assert not backend["dispatched"]


@pytest.mark.parametrize("key,value", [("tag_sha", "b" * 40), ("main", "diverged"), ("main", "behind")])
def test_unbound_or_non_main_snapshot_never_dispatches(backend, key, value):
    backend[key] = value
    with pytest.raises(verification.VerificationError):
        verify()
    assert not backend["dispatched"]


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "timed_out", None])
def test_failed_or_skipped_workflow_is_not_completion(backend, conclusion):
    backend["conclusion"] = conclusion
    with pytest.raises(verification.VerificationError, match="failed"):
        verify()


@pytest.mark.parametrize("job", ["guards + lint", "Promotion snapshot gate", "refresh-source-attribution"])
def test_required_job_skip_is_not_a_clean_promotion(backend, job):
    backend["missing"].add(job)
    with pytest.raises(verification.VerificationError, match="skipped required jobs"):
        verify()


@pytest.mark.parametrize("key,value", [
    ("workflow_id", 20), ("head_sha", "c" * 40), ("head_branch", "main"),
    ("event", "push"), ("id", True),
])
def test_wrong_run_identity_is_never_adopted(backend, key, value):
    verify()
    backend["runs"]["ci.yml"][0][key] = value
    with pytest.raises(verification.VerificationError, match="does not match"):
        verify()
    assert len(backend["dispatched"]) == 2


def test_ambiguous_same_identity_runs_refuse(backend):
    verify()
    backend["runs"]["ci.yml"] *= 2
    with pytest.raises(verification.VerificationError, match="ambiguous"):
        verify()


def test_check_publishing_workflow_never_adopts_a_matching_direct_tag_dispatch(backend):
    verify()
    backend["runs"]["workflow-lockdown-guard.yml"][0]["head_branch"] = TAG
    with pytest.raises(verification.VerificationError, match="does not match"):
        verify()


@pytest.mark.parametrize("comparison", [f"{SHA}...{'c' * 40}", f"{'c' * 40}...main"])
def test_guard_release_and_control_ancestry_are_independently_required(backend, comparison):
    backend["control_sha"] = "c" * 40
    backend["ancestry"][comparison] = "diverged"
    with pytest.raises(verification.VerificationError, match="ancestry"):
        verify()


def test_tag_moving_after_successful_jobs_still_fails(backend, monkeypatch):
    api = verification._api
    reads = 0

    def moved(path, **kwargs):
        nonlocal reads
        if "/git/ref/" in path:
            reads += 1
            if reads == 2:
                backend["tag_sha"] = "b" * 40
        return api(path, **kwargs)

    monkeypatch.setattr(verification, "_api", moved)
    with pytest.raises(verification.VerificationError, match="tag does not point"):
        verify()


def test_workflow_registration_timeout_is_bounded(backend, monkeypatch):
    clock = iter(range(10000))
    monkeypatch.setattr(verification.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(verification, "_runs", lambda *a, **kw: [])
    with pytest.raises(verification.VerificationError, match="deadline expired"):
        verification.verify("example/project", SHA, TAG, RUN, wait_seconds=1)


def test_job_pagination_does_not_hide_the_required_refresh(monkeypatch):
    calls = []

    def api(path, **kwargs):
        calls.append(path)
        return {"jobs": [{"name": "earlier"}] * 100 if path.endswith("&page=1") else [
            {"name": "refresh-source-attribution", "conclusion": "success"},
        ]}

    monkeypatch.setattr(verification, "_api", api)
    jobs = verification._pages(
        "repos/example/project/actions/runs/1/jobs?filter=latest", "jobs",
        deadline=verification.time.monotonic() + 60,
    )
    assert len(jobs) == 101
    assert calls[-1].endswith("&per_page=100&page=2")


@pytest.mark.parametrize("field,value", [
    ("repository", "../project"), ("merge_sha", "a" * 39), ("tag", "main"),
    ("tag", "promote-20260102030405-bbbbbbbb"), ("promotion_run", "other"),
])
def test_invalid_identity_never_reaches_backend(backend, field, value):
    args = {"repository": "example/project", "merge_sha": SHA, "tag": TAG, "promotion_run": RUN}
    args[field] = value
    with pytest.raises(verification.VerificationError, match="invalid immutable"):
        verification.verify(**args)
    assert not backend["calls"]


def test_missing_explicit_authority_never_uses_ambient_auth(backend, monkeypatch):
    monkeypatch.delenv("GH_TOKEN")
    with pytest.raises(verification.VerificationError, match="authority is unavailable"):
        verify()
    assert not backend["calls"]


def test_api_transport_sends_exact_json_without_a_secret_on_argv(monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "")

    monkeypatch.setattr(subprocess, "run", run)
    body = {"ref": TAG, "inputs": {"promotion_sha": SHA}}
    assert verification._api("repos/example/project/dispatches", body=body) is None
    argv, kwargs = calls[0]
    assert argv[-4:] == ["--method", "POST", "--input", "-"]
    assert json.loads(kwargs["input"]) == body
    assert kwargs["check"] is True and kwargs["timeout"] == 60


def test_provider_reads_use_consumer_authority_without_mutating_publication_or_dispatch_scope(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "synthetic-publisher")
    monkeypatch.setenv("ARTIFACT_VERIFY_TOKEN", "synthetic-consumer")
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, "{}")

    monkeypatch.setattr(subprocess, "run", run)
    verification._api("repos/example/project/actions/runs/1")
    verification._api("repos/example/project/dispatches", body={"ref": "main"})
    assert calls[0][1]["env"]["GH_TOKEN"] == "synthetic-consumer"
    assert calls[1][1]["env"]["GH_TOKEN"] == "synthetic-publisher"
    assert all("ARTIFACT_VERIFY_TOKEN" not in kwargs["env"] for _, kwargs in calls)
    assert os.environ["GH_TOKEN"] == "synthetic-publisher"
    assert all("synthetic-" not in " ".join(argv) for argv, _ in calls)


def workflows():
    return {
        name: yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
        for name in ("ci.yml", "workflow-lockdown-guard.yml", "validate-and-promote.yml")
    }


def test_native_consumer_smoke_budgets_preserve_other_plugin_limits():
    smoke = workflows()["ci.yml"]["jobs"]["smoke"]
    assert smoke["timeout-minutes"] == (
        "${{ (matrix.plugin == 'agent-dispatch' || matrix.plugin == 'agent-index' || matrix.plugin == 'agent-bridge') && 12 || 8 }}"
    )
    assert smoke["strategy"]["fail-fast"] is False


def test_dispatch_mode_preserves_main_push_coverage_without_manual_exhaustive_tiers():
    ci = workflows()["ci.yml"]
    jobs = ci["jobs"]
    assert "inputs.promotion_run != ''" in jobs["discover"]["if"]
    assert "inputs.promotion_run == ''" in jobs["full"]["if"]
    assert "inputs.promotion_run == ''" in jobs["coverage-guided-selection-integration"]["if"]
    assert set(jobs["pr-gate"]["needs"]) <= set(jobs["promotion-gate"]["needs"])
    assert "bootstrap-killswitch-powershell-5-1" in jobs["promotion-gate"]["needs"]
    for job in ("test-runner-linux", "test-runner-windows"):
        tiers = [step["env"]["PYTEST_ADDOPTS"] for step in jobs[job]["steps"]
                 if "github.event_name" in step.get("env", {}).get("PYTEST_ADDOPTS", "")]
        assert tiers and all("inputs.promotion_run != ''" in value for value in tiers)
    refresh = workflows()["workflow-lockdown-guard.yml"]["jobs"]["refresh-source-attribution"]
    assert "refs/tags/" not in refresh["if"]
    assert "github.event.repository.default_branch" in refresh["if"]
    for name, workflow in workflows().items():
        if name == "validate-and-promote.yml":
            continue
        assert "inputs.promotion_run" in workflow["run-name"]
        assert "inputs.promotion_sha" in workflow["run-name"]
        job = workflow["jobs"]["checks" if name == "ci.yml" else "refresh-source-attribution"]
        assert "Verify the dispatched promotion snapshot" in job["steps"][0]["name"]
        guard = job["steps"][0]["run"]
        if name == "ci.yml":
            assert '"$RUN_SHA" = "$EXPECTED_SHA"' in guard
            assert '"$WORKFLOW_SHA" = "$EXPECTED_SHA"' in guard
            assert "^refs/tags/promote-" in guard
        else:
            assert '"$RUN_REF" = "$DEFAULT_REF"' in guard
            assert '"$RUN_SHA" = "$WORKFLOW_SHA"' in guard
            assert "/compare/$EXPECTED_SHA...$WORKFLOW_SHA" in guard
            assert job["steps"][1]["with"]["ref"] == "${{ inputs.promotion_sha || github.workflow_sha }}"


@pytest.mark.parametrize("filename", ["ci.yml", "workflow-lockdown-guard.yml"])
@pytest.mark.parametrize("changed", [None, "RUN_SHA", "WORKFLOW_SHA", "RUN_REF", "PROMOTION_RUN"])
def test_native_snapshot_guard_checks_the_executed_workflow_not_only_its_checkout(filename, changed):
    bash = shutil.which("bash")
    git = shutil.which("git")
    if os.name == "nt" and git:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    if bash is None:
        pytest.skip("native workflow guard validation requires Bash")
    workflow = workflows()[filename]
    job = workflow["jobs"]["checks" if filename == "ci.yml" else "refresh-source-attribution"]
    environment = {
        **os.environ, "EXPECTED_SHA": SHA, "PROMOTION_RUN": RUN,
        "PROMOTION_PHASE": "release",
        "RUN_SHA": SHA, "WORKFLOW_SHA": SHA,
        "RUN_REF": "refs/heads/main" if filename != "ci.yml" else f"refs/tags/{TAG}",
        "DEFAULT_REF": "refs/heads/main", "GITHUB_REPOSITORY": "example/project",
        "ANCESTRY_STATUS": "identical",
    }
    if changed:
        environment[changed] = "moved"
    script = "gh() { printf '%s\\n' \"$ANCESTRY_STATUS\"; }\n" + job["steps"][0]["run"]
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e"], input=script,
        text=True, capture_output=True, env=environment, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == (1 if changed else 0), result.stdout + result.stderr
    if changed:
        message = (
            "does not execute the exact qualification snapshot" if filename == "ci.yml"
            else "requires a trusted main control snapshot"
        )
        assert message in result.stdout


@pytest.mark.parametrize("ref,status,accepted", [
    ("refs/heads/main", "identical", True),
    ("refs/heads/main", "ahead", True),
    ("refs/heads/main", "diverged", False),
    ("refs/heads/main", "behind", False),
    (f"refs/tags/{TAG}", "identical", False),
])
def test_native_guard_rejects_self_consistent_off_main_target_before_checkout(ref, status, accepted):
    bash, git = shutil.which("bash"), shutil.which("git")
    if os.name == "nt" and git:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        bash = str(candidate) if candidate.is_file() else None
    if not bash:
        pytest.skip("native workflow guard validation requires Bash")
    job = workflows()["workflow-lockdown-guard.yml"]["jobs"]["refresh-source-attribution"]
    environment = {
        **os.environ, "EXPECTED_SHA": SHA, "PROMOTION_RUN": RUN,
        "RUN_SHA": "c" * 40, "WORKFLOW_SHA": "c" * 40,
        "RUN_REF": ref, "DEFAULT_REF": "refs/heads/main",
        "GITHUB_REPOSITORY": "example/project", "ANCESTRY_STATUS": status,
    }
    script = """
    gh() {
      [ "$*" = "api repos/example/project/compare/$EXPECTED_SHA...$WORKFLOW_SHA --jq .status" ] || return 91
      printf '%s\\n' "$ANCESTRY_STATUS"
    }
    """ + job["steps"][0]["run"]
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e"], input=script,
        text=True, capture_output=True, env=environment, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == (0 if accepted else 1), result.stdout + result.stderr
    if not accepted:
        assert "::error::" in result.stdout


@pytest.mark.parametrize("result", ["success", "skipped", "failure", "cancelled"])
def test_real_promotion_coverage_aggregate_refuses_missing_coverage(monkeypatch, result):
    job = workflows()["ci.yml"]["jobs"]["promotion-gate"]
    script = job["steps"][0]["run"]
    code = re.search(r"python3 - <<'PY'\n(.*?)^PY$", script, re.M | re.S).group(1)
    monkeypatch.setenv("NEEDS_JSON", json.dumps({name: {"result": result} for name in job["needs"]}))
    monkeypatch.setenv("PROMOTION_PHASE", "candidate")
    if result == "success":
        exec(compile(code, "promotion-coverage-aggregate", "exec"), {})
    else:
        with pytest.raises(SystemExit, match="coverage did not succeed"):
            exec(compile(code, "promotion-coverage-aggregate", "exec"), {})


def test_legacy_release_aggregate_permits_only_the_explicitly_omitted_full_tier(monkeypatch):
    job = workflows()["ci.yml"]["jobs"]["promotion-gate"]
    code = re.search(r"python3 - <<'PY'\n(.*?)^PY$", job["steps"][0]["run"], re.M | re.S).group(1)
    results = {name: {"result": "success"} for name in job["needs"]}
    results["full"]["result"] = "skipped"
    monkeypatch.setenv("PROMOTION_PHASE", "release")
    monkeypatch.setenv("NEEDS_JSON", json.dumps(results))
    exec(compile(code, "promotion-coverage-aggregate", "exec"), {})
    results["checks"]["result"] = "skipped"
    monkeypatch.setenv("NEEDS_JSON", json.dumps(results))
    with pytest.raises(SystemExit, match="coverage did not succeed"):
        exec(compile(code, "promotion-coverage-aggregate", "exec"), {})


@pytest.mark.parametrize("changed", [None, "RUN_SHA", "WORKFLOW_SHA", "RUN_REF", "PROMOTION_RUN", "PROMOTION_PHASE"])
def test_native_candidate_guard_binds_the_owner_run_ref_and_exact_workflow(changed):
    bash = shutil.which("bash")
    git = shutil.which("git")
    if os.name == "nt" and git:
        native = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        bash = str(native) if native.is_file() else None
    if not bash:
        pytest.skip("native workflow guard validation requires Bash")
    step = workflows()["ci.yml"]["jobs"]["checks"]["steps"][0]
    environment = {
        **os.environ, "EXPECTED_SHA": SHA, "PROMOTION_RUN": RUN, "PROMOTION_PHASE": "candidate",
        "RUN_SHA": SHA, "WORKFLOW_SHA": SHA, "RUN_REF": "refs/heads/release/promote-123",
    }
    if changed:
        environment[changed] = "moved"
    result = subprocess.run(
        [bash, "--noprofile", "--norc", "-e"], input=step["run"],
        text=True, capture_output=True, env=environment, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == (1 if changed else 0), result.stdout + result.stderr
    if changed:
        assert "::error::" in result.stdout


def test_postmerge_verifier_and_failure_reporter_are_bound_to_actual_merge_and_tag():
    jobs = workflows()["validate-and-promote.yml"]["jobs"]
    if source_owned(jobs):
        assert_source_owned_boundary(jobs)
        qualifier = jobs["qualify-candidate"]
        run = next(step for step in qualifier["steps"] if step.get("id") == "qualify")
        assert qualifier["permissions"] == {"contents": "read", "actions": "write"}
        assert run["env"]["GH_TOKEN"] == "${{ github.token }}"
        assert "APERTURE_RELEASE_TOKEN" not in json.dumps(qualifier)
        for argument in ("qualify_promotion_candidate.py", "--candidate-root candidate",
                         "--dev-root validated-dev", "--control-sha", "--receipt", "--github-output"):
            assert argument in run["run"]
        assert "continue-on-error" not in run
        record = jobs["record-promotion"]
        tag = next(step for step in record["steps"] if "tag_promotion.py" in step.get("run", ""))
        assert tag["env"]["MERGE_SHA"] == "${{ needs.merge-promotion.outputs.merge_sha }}"
        assert tag["env"]["SOURCE_RELEASE"] == "${{ needs.publish-python-artifacts.outputs.source_release }}"
        assert "verify_promotion_workflows.py" not in json.dumps(record)
        return
    proof = jobs["postmerge-workflows"]
    assert set(proof["needs"]) == {"merge-promotion", "publish-python-artifacts"}
    run = proof["steps"][-1]
    assert run["env"]["MERGE_SHA"] == "${{ needs.merge-promotion.outputs.merge_sha }}"
    assert run["env"]["PROMOTION_TAG"] == "${{ needs.publish-python-artifacts.outputs.promotion_tag }}"
    assert "APERTURE_RELEASE_TOKEN" not in json.dumps(proof)
    assert all("uses" not in step for step in proof["steps"])
    assert run["env"]["TRUSTED_WORKFLOW_SHA"] == "${{ github.workflow_sha }}"
    assert "verify_promotion_workflows.py?ref=$TRUSTED_WORKFLOW_SHA" in run["run"]
    assert proof["permissions"] == {"contents": "read", "actions": "write"}
    for name in ("merge-promotion", "publish-python-artifacts", "postmerge-workflows"):
        assert name in jobs["report-failure"]["needs"]
        assert f"needs.{name}.result == 'failure'" in jobs["report-failure"]["if"]
