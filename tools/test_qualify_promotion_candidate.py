"""Qualification cannot publish a green wrapper around absent candidate coverage."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from _promotion_workflow_test_support import source_owned, source_workflow, workflow as controller_workflow
from test_promotion_artifact_readiness import _materializer_git
import candidate_qualification_inventory as inventory
import qualify_promotion_candidate as qualification
import verify_promotion_workflows as workflows

ROOT = Path(__file__).resolve().parent.parent
SHA, DEV, MAIN = "a" * 40, "b" * 40, "c" * 40
REF, RUN = "release/promote-123", "123-1"
IDENTITY = dict(candidate_sha=SHA, dev_sha=DEV, main_before=MAIN, tree="d" * 40)


@pytest.fixture
def backend(monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("GH_TOKEN", "synthetic-read-dispatch")
    monkeypatch.setattr(qualification, "validate", lambda *args: IDENTITY.copy())
    monkeypatch.setattr(qualification, "matrix", lambda *args: {"include": ["actual-consumer"]})
    monkeypatch.setattr(
        qualification, "required_jobs",
        lambda root: ({"guards + lint", "full - agent-worktrees", "Promotion snapshot gate"},
                      {"PR gate"}, "e" * 64),
    )
    monkeypatch.setattr(qualification.time, "sleep", lambda _: None)
    state = {
        "calls": [], "dispatched": 0, "runs": [], "base": MAIN, "candidate": SHA,
        "conclusion": "success",
        "jobs": [
            {"id": index, "name": name, "status": "completed", "conclusion": "success"}
            for index, name in enumerate(
                ["guards + lint", "full - agent-worktrees", "Promotion snapshot gate"], 1,
            )
        ],
    }

    def api(path: str, *, body: dict | None = None, timeout: float = 60) -> dict | None:
        state["calls"].append((path, body))
        if "/git/ref/heads/" in path:
            target = state["base"] if path.endswith("/main") else state["candidate"]
            return {"object": {"type": "commit", "sha": target}}
        if path == "repos/example/project/actions/runs/123":
            return {"id": 123, "created_at": "2026-01-01T00:00:00Z"}
        if "/dispatches" in path:
            assert body == {
                "ref": REF,
                "inputs": {"promotion_sha": SHA, "promotion_run": RUN, "promotion_phase": "candidate"},
            }
            state["dispatched"] += 1
            state["runs"] = [{
                "id": 456, "workflow_id": 12, "event": "workflow_dispatch",
                "head_sha": SHA, "head_branch": REF, "run_attempt": 1,
                "display_title": f"CI promotion {RUN} at {SHA}",
                "status": "completed", "conclusion": state["conclusion"],
            }]
            return None
        if "/workflows/ci.yml/runs?" in path:
            return {"workflow_runs": copy.deepcopy(state["runs"])}
        if path.endswith("/workflows/ci.yml"):
            return {"id": 12, "state": "active", "name": "CI", "path": ".github/workflows/ci.yml"}
        if "/attempts/" in path and "/jobs?" in path:
            return {"jobs": copy.deepcopy(state["jobs"])}
        if path == "repos/example/project/actions/runs/456":
            return copy.deepcopy(state["runs"][0])
        raise AssertionError(path)

    monkeypatch.setattr(qualification, "_api", api)
    monkeypatch.setattr(workflows, "_api", api)
    return state


def _qualify() -> dict:
    return qualification.qualify(
        repository="example/project", candidate_ref=REF, candidate_sha=SHA, dev_sha=DEV,
        main_before=MAIN, control_sha=MAIN, promotion_run=RUN,
        candidate_root=ROOT, dev_root=ROOT,
    )


def _verify(receipt: dict) -> str:
    return qualification.verify_receipt(
        receipt, repository="example/project", candidate_sha=SHA, dev_sha=DEV,
        main_before=MAIN, control_sha=MAIN, promotion_run=RUN, candidate_root=ROOT,
    )


def test_exact_candidate_dispatch_and_receipt_replay_reuse_the_same_successful_attempt(backend: dict) -> None:
    receipt = _qualify()
    assert receipt["candidate_sha"] == SHA
    assert receipt["dev_sha"] == DEV
    assert receipt["run_id"] == 456 and receipt["run_attempt"] == 1
    assert _verify(receipt) == qualification.receipt_digest(receipt)
    assert _qualify() == receipt
    assert backend["dispatched"] == 1
    assert any("/attempts/1/jobs?" in path for path, _ in backend["calls"])
    assert not any("/merge" in path for path, _ in backend["calls"])


def test_real_cli_writes_exact_digest_bytes_and_all_bound_workflow_outputs(
    backend: dict, tmp_path: Path,
) -> None:
    receipt, output = tmp_path / "receipt.json", tmp_path / "output"
    assert qualification.main([
        "--repository", "example/project", "--candidate-ref", REF, "--candidate-sha", SHA,
        "--dev-sha", DEV, "--main-before", MAIN, "--control-sha", MAIN, "--promotion-run", RUN,
        "--candidate-root", str(ROOT), "--dev-root", str(ROOT),
        "--receipt", str(receipt), "--github-output", str(output),
    ]) == 0
    fields = dict(line.split("=", 1) for line in output.read_text("utf-8").splitlines())
    assert fields["qualification_digest"] == hashlib.sha256(receipt.read_bytes()).hexdigest()
    assert {key: fields[key] for key in IDENTITY} == IDENTITY
    assert fields["control_sha"] == MAIN
    assert fields["qualification_run"] == "456" and fields["qualification_attempt"] == "1"
    assert fields["qualification_promotion_run"] == RUN


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "timed_out", None])
def test_a_failed_candidate_never_returns_a_release_qualification(
    backend: dict, conclusion: str | None,
) -> None:
    backend["conclusion"] = conclusion
    with pytest.raises(workflows.VerificationError, match="did not succeed"):
        _qualify()


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", None])
def test_green_workflow_with_nonexecuted_required_row_is_not_qualification(
    backend: dict, conclusion: str | None,
) -> None:
    backend["jobs"][1]["conclusion"] = conclusion
    with pytest.raises(workflows.VerificationError, match="required candidate CI row"):
        _qualify()


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "incomplete", "invalid-id"])
def test_required_portfolio_is_an_exact_executed_row_set(backend: dict, mutation: str) -> None:
    if mutation == "missing":
        backend["jobs"].pop(1)
    elif mutation == "duplicate":
        backend["jobs"].append(backend["jobs"][0].copy())
    elif mutation == "extra":
        backend["jobs"].append(dict(id=10, name="Unknown job", status="completed", conclusion="success"))
    elif mutation == "incomplete":
        backend["jobs"][1]["status"] = "queued"
    else:
        backend["jobs"][1]["id"] = True
    with pytest.raises(workflows.VerificationError):
        _qualify()


@pytest.mark.parametrize("key", ["base", "candidate"])
def test_a_changed_source_or_main_base_refuses_before_dispatch(backend: dict, key: str) -> None:
    backend[key] = "f" * 40
    with pytest.raises(workflows.VerificationError, match="source/base changed"):
        _qualify()
    assert backend["dispatched"] == 0


@pytest.mark.parametrize("mutation", ["attempt", "head", "base", "missing-row", "receipt-source"])
def test_stale_qualification_cannot_authorize_artifact_publication(backend: dict, mutation: str) -> None:
    receipt = _qualify()
    if mutation == "attempt":
        backend["runs"][0]["run_attempt"] = 2
    elif mutation == "head":
        backend["runs"][0]["head_sha"] = "f" * 40
    elif mutation == "base":
        backend["base"] = "f" * 40
    elif mutation == "missing-row":
        backend["jobs"].pop(1)
    else:
        receipt["dev_sha"] = "f" * 40
    with pytest.raises(workflows.VerificationError):
        _verify(receipt)


def test_relabelled_receipt_cannot_claim_a_different_owning_promotion_attempt(backend: dict) -> None:
    receipt = _qualify()
    receipt["promotion_run"] = "123-2"
    with pytest.raises(workflows.VerificationError, match="different owning promotion attempt"):
        qualification.verify_receipt(
            receipt, repository="example/project", candidate_sha=SHA, dev_sha=DEV,
            main_before=MAIN, control_sha=MAIN, promotion_run="123-2", candidate_root=ROOT,
        )
    assert backend["runs"][0]["display_title"] == f"CI promotion {RUN} at {SHA}"


def _supported_smoke_profile(text: str) -> str:
    smoke = yaml.safe_load(text)["jobs"]["smoke"]
    matrix = smoke["strategy"]["matrix"]
    if smoke["name"] == "${{ matrix.plugin }}" and matrix == {
        "plugin": "${{ fromJson(needs.discover.outputs.smoke) }}",
    }:
        return "unpartitioned"
    assert smoke["name"] == "${{ matrix.name }}" and matrix == {
        "include": "${{ fromJson(needs.discover.outputs.smoke_matrix) }}",
    }, "fixture requires an explicitly supported smoke matrix profile"
    return "partitioned"


def _candidate_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, profile: str = "actual",
) -> Path:
    workflow = tmp_path / "ci.yml"
    if profile == "actual":
        text = (ROOT / ".github/workflows/ci.yml").read_text("utf-8")
    elif profile == "partitioned":
        source = source_workflow(ROOT, "ci.yml")
        if source_owned(controller_workflow(ROOT)["jobs"]):
            dev = json.loads((ROOT / ".github/release-pipeline-state.json").read_text("utf-8"))[
                "last_promotion"
            ]["dev_head"]
            text = _materializer_git(ROOT, "show", f"{dev}:.github/workflows/ci.yml").decode("utf-8")
        else:
            text = (ROOT / ".github/workflows/ci.yml").read_text("utf-8")
        assert yaml.safe_load(text) == source
        assert _supported_smoke_profile(text) == "partitioned"
    else:
        assert profile == "main", "unknown test fixture profile"
        main = _materializer_git(
            ROOT, "rev-parse", "--verify", "refs/remotes/origin/main^{commit}",
        ).decode().strip()
        text = _materializer_git(ROOT, "show", f"{main}:.github/workflows/ci.yml").decode("utf-8")
        assert _supported_smoke_profile(text) == "unpartitioned"
    # Emulate the reviewed candidate aggregate while the workflow-only bootstrap is separate.
    start = text.index("  promotion-gate:\n")
    before, gate = text[:start], text[start:]
    if "      - full\n" not in gate:
        gate = gate.replace("    needs:\n", "    needs:\n      - full\n", 1)
    workflow.write_text(before + gate, encoding="utf-8")
    original = inventory.regular
    monkeypatch.setattr(
        inventory, "regular",
        lambda root, path: workflow if path == Path(".github/workflows/ci.yml") else original(root, path),
    )
    return workflow


@pytest.mark.parametrize("profile", ["actual", "partitioned", "main"])
def test_inventory_derives_all_platform_and_matrix_rows_from_the_actual_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch, profile=profile)
    required, allowed_skips, digest = inventory.required_jobs(ROOT)
    assert {"guards + lint", "full - agent-worktrees", "archive sources (windows-latest)"} <= required
    assert {"agent-bridge", "agent-index", "Promotion snapshot gate"} <= required
    partitions = {f"agent-dispatch (partition {index}/3)" for index in (1, 2, 3)}
    if _supported_smoke_profile(workflow.read_text("utf-8")) == "partitioned":
        assert partitions <= required
        assert "agent-dispatch" not in required
    else:
        assert "agent-dispatch" in required
        assert not partitions & required
    assert any(name.startswith("fleet-manager (windows-latest,") for name in required)
    assert any(name.startswith("fleet-manager (ubuntu-latest,") for name in required)
    assert len(required) > 50
    assert len(allowed_skips) == 2
    assert len(digest) == 64


@pytest.mark.parametrize("platform", ["ubuntu-latest", "windows-latest"])
@pytest.mark.parametrize("conclusion", ["missing", "failure", "cancelled", "skipped"])
def test_chat_platform_rows_are_required_not_optional_candidate_coverage(
    backend: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, conclusion: str,
) -> None:
    _candidate_workflow(tmp_path, monkeypatch)
    expected, allowed_skips, digest = inventory.required_jobs(ROOT)
    rows = {f"agent-chat-service ({os})" for os in ("ubuntu-latest", "windows-latest")}
    assert rows <= expected
    assert not rows & allowed_skips
    monkeypatch.setattr(qualification, "required_jobs", lambda root: (expected, allowed_skips, digest))
    backend["jobs"] = [
        dict(id=index, name=name, status="completed", conclusion="success")
        for index, name in enumerate(sorted(expected), 1)
    ]
    target = f"agent-chat-service ({platform})"
    if conclusion == "missing":
        backend["jobs"] = [row for row in backend["jobs"] if row["name"] != target]
    else:
        next(row for row in backend["jobs"] if row["name"] == target)["conclusion"] = conclusion
    with pytest.raises(workflows.VerificationError, match="required.*row"):
        _qualify()


@pytest.mark.parametrize("platform", ["ubuntu-latest", "windows-latest"])
def test_chat_platform_jobs_cannot_gain_an_unaccounted_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch)
    text = workflow.read_text("utf-8")
    old = f"    name: agent-chat-service ({platform})"
    assert text.count(old) == 1
    workflow.write_text(
        text.replace(old, "    name: agent-chat-service (${{ matrix.os }})"),
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError, match="unsupported canonical CI matrix"):
        inventory.required_jobs(ROOT)


@pytest.mark.parametrize("profile", ["actual", "partitioned", "main"])
def test_inventory_uses_the_same_declared_source_representation_for_lf_and_crlf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: str,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch, profile=profile)
    canonical = workflow.read_text("utf-8").replace("\r\n", "\n").encode("utf-8")
    workflow.write_bytes(canonical)
    lf = inventory.required_jobs(ROOT)
    represented = canonical.replace(b"\n", b"\r\n")
    assert represented != canonical and b"\r\njobs:\r\n" in represented
    workflow.write_bytes(represented)
    assert inventory.required_jobs(ROOT) == lf
    digest_input = canonical
    if _supported_smoke_profile(canonical.decode("utf-8")) == "partitioned":
        helper = (ROOT / "tools/ci_plugin_partitions.py").read_bytes().replace(b"\r\n", b"\n")
        digest_input += b"\0tools/ci_plugin_partitions.py\0" + helper
    assert lf[2] == hashlib.sha256(digest_input).hexdigest()


def test_inventory_rejects_unknown_lone_cr_source_representation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch)
    canonical = workflow.read_text("utf-8").encode("utf-8")
    workflow.write_bytes(canonical.replace(b"\n", b"\r", 1))
    with pytest.raises(CandidateValidationError, match="unsupported lone carriage return"):
        inventory.required_jobs(ROOT)


@pytest.mark.parametrize("mutation", ["missing-full", "unknown-job", "duplicate", "matrix", "skip-scope"])
def test_inventory_refuses_missing_or_unsupported_recipes_instead_of_silently_narrowing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch)
    text = workflow.read_text("utf-8")
    if mutation == "missing-full":
        text = text.replace("      - full\n", "")
    elif mutation == "unknown-job":
        text += "\n  unaccounted:\n    runs-on: ubuntu-latest\n"
    elif mutation == "duplicate":
        text = text.replace("      - checks\n", "      - checks\n      - checks\n", 1)
    elif mutation == "matrix":
        text = text.replace("    name: full - ${{ matrix.plugin }}", "    name: full - ${{ matrix.other }}")
    else:
        text = text.replace("inputs.promotion_run == ''", "inputs.promotion_run != ''")
    workflow.write_text(text, encoding="utf-8")
    with pytest.raises(CandidateValidationError):
        inventory.required_jobs(ROOT)


def _partition_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str,
) -> Path:
    helper = tmp_path / "ci_plugin_partitions.py"
    helper.write_text(text, encoding="utf-8")
    original = inventory.regular
    monkeypatch.setattr(
        inventory, "regular",
        lambda root, path: helper if path == Path("tools/ci_plugin_partitions.py") else original(root, path),
    )
    return helper


@pytest.mark.parametrize("text", [
    "", "_SMOKE_PARTITIONS = dict(agent_dispatch=3)",
    "_SMOKE_PARTITIONS = {'agent-dispatch': True}",
    "_SMOKE_PARTITIONS = {'agent-dispatch': 1}",
    "_SMOKE_PARTITIONS = {'agent-dispatch': 257}",
    "_SMOKE_PARTITIONS = {'unknown-plugin': 3}",
    "_SMOKE_PARTITIONS = {'agent-worktrees': 3}",
    "_SMOKE_PARTITIONS = {'agent-dispatch': 3, 'agent-dispatch': 4}",
    "_SMOKE_PARTITIONS = {'agent-dispatch': 3}\n_SMOKE_PARTITIONS = {}",
    "_SMOKE_PARTITIONS = {'agent-dispatch': 3}\n_SMOKE_PARTITIONS: dict = {}",
    "_SMOKE_PARTITIONS = {'agent-dispatch': 256}",
    "_SMOKE_PARTITIONS = {",
])
def test_inventory_refuses_malformed_ambiguous_or_oversized_partition_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str,
) -> None:
    _candidate_workflow(tmp_path, monkeypatch, profile="partitioned")
    _partition_source(tmp_path, monkeypatch, text)
    with pytest.raises(CandidateValidationError, match="smoke partition inventory"):
        inventory.required_jobs(ROOT)


def test_partition_source_is_not_executed_and_its_normalized_bytes_are_receipt_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _candidate_workflow(tmp_path, monkeypatch, profile="partitioned")
    text = "_SMOKE_PARTITIONS = {'agent-dispatch': 3}\nraise RuntimeError('must not execute')\n"
    helper = _partition_source(tmp_path, monkeypatch, text)
    original = inventory.required_jobs(ROOT)
    helper.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    assert inventory.required_jobs(ROOT) == original
    helper.write_text(text + "# changed source\n", encoding="utf-8")
    changed = inventory.required_jobs(ROOT)
    assert changed[:2] == original[:2] and changed[2] != original[2]
    helper.unlink()
    with pytest.raises(CandidateValidationError, match="smoke partition inventory"):
        inventory.required_jobs(ROOT)


@pytest.mark.parametrize("mutation", ["include", "extra-axis", "discover", "output", "name"])
def test_partition_matrix_requires_its_explicit_discovery_and_name_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch, profile="partitioned")
    text = workflow.read_text("utf-8")
    old, new = {
        "include": ("include: ${{ fromJson(needs.discover.outputs.smoke_matrix) }}",
                    "include: ${{ fromJson(needs.discover.outputs.smoke) }}"),
        "extra-axis": ("include: ${{ fromJson(needs.discover.outputs.smoke_matrix) }}",
                       "include: ${{ fromJson(needs.discover.outputs.smoke_matrix) }}\n        extra: [one]"),
        "discover": ('python tools/ci_plugin_partitions.py matrix "$SMOKE"', 'python other.py "$SMOKE"'),
        "output": ('echo "smoke_matrix=$SMOKE_MATRIX"', 'echo "other=$SMOKE_MATRIX"'),
        "name": ("    name: ${{ matrix.name }}", "    name: prefix ${{ matrix.name }}"),
    }[mutation]
    assert old in text
    workflow.write_text(text.replace(old, new), encoding="utf-8")
    with pytest.raises(CandidateValidationError, match="discovered partitions"):
        inventory.required_jobs(ROOT)


def test_candidate_only_bridge_workers_retain_exact_rows_and_bind_changed_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch, profile="partitioned")
    text = workflow.read_text("utf-8")
    assert text.count(inventory._PARTITIONED_SMOKE_WORKERS_STEP) == 1
    with_workers = inventory.required_jobs(ROOT)
    workflow.write_text(
        text.replace(inventory._PARTITIONED_SMOKE_WORKERS_STEP, inventory._PARTITIONED_SMOKE_STEP),
        encoding="utf-8",
    )
    historical = inventory.required_jobs(ROOT)
    assert historical[:2] == with_workers[:2]
    assert historical[2] != with_workers[2]


@pytest.mark.parametrize(("old", "new"), [
    ("'-n 2 --dist=worksteal'", "'-n 3 --dist=worksteal'"),
    ("matrix.plugin == 'agent-bridge'", "matrix.plugin != 'agent-bridge'"),
    ("inputs.promotion_run != '' && ", ""),
    ("'-n 2 --dist=worksteal'", "'-n 2 --dist=worksteal -k selected_case'"),
    ("          PLUGIN:", "          EXTRA: unreviewed\n          PLUGIN:"),
])
def test_candidate_bridge_worker_recipe_cannot_expand_or_narrow_qualification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old: str, new: str,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch, profile="partitioned")
    text = workflow.read_text("utf-8")
    step = inventory._PARTITIONED_SMOKE_WORKERS_STEP
    assert text.count(step) == 1 and step.count(old) == 1
    workflow.write_text(text.replace(step, step.replace(old, new)), encoding="utf-8")
    with pytest.raises(CandidateValidationError, match="discovered partitions"):
        inventory.required_jobs(ROOT)


@pytest.mark.parametrize("mutation", [
    "collect-only", "filtered", "failure-masked", "partition-env", "selection",
    "skip-candidate", "only-pr",
])
def test_partition_runner_requires_the_complete_executed_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch, profile="partitioned")
    text = workflow.read_text("utf-8")
    invocation = '          python tools/run-plugin-tests.py "$PLUGIN" "${args[@]}"'
    if mutation in ("collect-only", "filtered", "failure-masked"):
        suffix = {
            "collect-only": " --collect-only",
            "filtered": " -k selected_case",
            "failure-masked": " || true",
        }[mutation]
        old, new = invocation, invocation + suffix
    elif mutation == "partition-env":
        old, new = "PARTITION: ${{ matrix.partition }}", "PARTITION: 0"
    elif mutation in ("skip-candidate", "only-pr"):
        old = "if: matrix.plugin != 'agent-dispatch' || github.event_name != 'pull_request'"
        new = "if: false" if mutation == "skip-candidate" else "if: github.event_name == 'pull_request'"
    else:
        old, new = 'mapfile -t args <<< "$files"', "args=(--collect-only)"
    assert text.count(old) == 1
    workflow.write_text(text.replace(old, new), encoding="utf-8")
    with pytest.raises(CandidateValidationError, match="discovered partitions"):
        inventory.required_jobs(ROOT)


@pytest.mark.parametrize("partition", [1, 2, 3])
@pytest.mark.parametrize("conclusion", ["missing", "failure", "cancelled"])
def test_every_actual_dispatch_partition_must_execute_successfully(
    backend: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, partition: int, conclusion: str,
) -> None:
    _candidate_workflow(tmp_path, monkeypatch, profile="partitioned")
    expected, allowed_skips, digest = inventory.required_jobs(ROOT)
    monkeypatch.setattr(qualification, "required_jobs", lambda root: (expected, allowed_skips, digest))
    backend["jobs"] = [
        dict(id=index, name=name, status="completed", conclusion="success")
        for index, name in enumerate(sorted(expected), 1)
    ]
    target = f"agent-dispatch (partition {partition}/3)"
    assert target in expected
    if conclusion == "missing":
        backend["jobs"] = [row for row in backend["jobs"] if row["name"] != target]
    else:
        next(row for row in backend["jobs"] if row["name"] == target)["conclusion"] = conclusion
    with pytest.raises(workflows.VerificationError, match="required.*row"):
        _qualify()


@pytest.mark.parametrize("conclusion", ["missing", "failure", "cancelled"])
def test_unpartitioned_main_receipt_binds_actual_ci_not_unused_partition_source(
    backend: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conclusion: str,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch, profile="main")
    expected, allowed_skips, digest = inventory.required_jobs(ROOT)
    assert "agent-dispatch" in expected
    monkeypatch.setattr(qualification, "required_jobs", inventory.required_jobs)
    backend["jobs"] = [
        dict(id=index, name=name, status="completed", conclusion="success")
        for index, name in enumerate(sorted(expected), 1)
    ]
    receipt = _qualify()
    assert _verify(receipt) == qualification.receipt_digest(receipt)
    _partition_source(tmp_path, monkeypatch, "raise RuntimeError('unused source must not execute')\n")
    assert inventory.required_jobs(ROOT) == (expected, allowed_skips, digest)
    assert _verify(receipt) == qualification.receipt_digest(receipt)
    workflow.write_text(workflow.read_text("utf-8") + "# changed actual CI\n", encoding="utf-8")
    with pytest.raises(workflows.VerificationError):
        _verify(receipt)
    if conclusion == "missing":
        backend["jobs"] = [row for row in backend["jobs"] if row["name"] != "agent-dispatch"]
    else:
        next(row for row in backend["jobs"] if row["name"] == "agent-dispatch")["conclusion"] = conclusion
    with pytest.raises(workflows.VerificationError, match="required.*row"):
        _qualify()


CandidateValidationError = inventory.CandidateValidationError
