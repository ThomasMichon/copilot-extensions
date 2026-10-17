"""Qualification cannot publish a green wrapper around absent candidate coverage."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest

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


def _candidate_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    workflow = tmp_path / "ci.yml"
    runner = tmp_path / "run-plugin-tests.py"
    runner.write_bytes((ROOT / "tools/run-plugin-tests.py").read_bytes())
    text = (ROOT / ".github/workflows/ci.yml").read_text("utf-8")
    # Emulate the reviewed candidate aggregate while the workflow-only bootstrap is separate.
    start = text.index("  promotion-gate:\n")
    before, gate = text[:start], text[start:]
    if "      - full\n" not in gate:
        gate = gate.replace("    needs:\n", "    needs:\n      - full\n", 1)
    workflow.write_text(before + gate, encoding="utf-8")
    regular = inventory.regular
    sources = {
        Path(".github/workflows/ci.yml"): workflow,
        Path("tools/run-plugin-tests.py"): runner,
    }
    monkeypatch.setattr(
        inventory, "regular", lambda root, path: sources.get(path) or regular(root, path),
    )
    return workflow


def test_inventory_derives_all_platform_and_matrix_rows_from_the_actual_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _candidate_workflow(tmp_path, monkeypatch)
    required, allowed_skips, digest = inventory.required_jobs(ROOT)
    assert {"guards + lint", "full - agent-worktrees", "archive sources (windows-latest)"} <= required
    assert {"agent-bridge", "agent-index", "Promotion snapshot gate"} <= required
    assert {f"agent-dispatch ({index}/4)" for index in range(1, 5)} <= required
    assert "agent-dispatch" not in required
    assert any(name.startswith("fleet-manager (windows-latest,") for name in required)
    assert any(name.startswith("fleet-manager (ubuntu-latest,") for name in required)
    assert len(required) > 50
    assert len(allowed_skips) == 2
    assert len(digest) == 64


@pytest.mark.parametrize("count", [1, 2, 4, 5])
def test_inventory_derives_every_shard_from_the_declared_runner_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch)
    runner = workflow.with_name("run-plugin-tests.py")
    text = runner.read_text("utf-8")
    declaration = '_CI_SHARD_COUNTS: dict[str, int] = {"agent-dispatch": 4}'
    assert text.count(declaration) == 1
    runner.write_text(
        text.replace(declaration, f'_CI_SHARD_COUNTS: dict[str, int] = {{"agent-dispatch": {count}}}'),
        encoding="utf-8",
    )
    required, _skips, _digest = inventory.required_jobs(ROOT)
    actual = {
        name for name in required
        if name == "agent-dispatch" or name.startswith("agent-dispatch (")
    }
    assert actual == (
        {"agent-dispatch"} if count == 1
        else {f"agent-dispatch ({index}/{count})" for index in range(1, count + 1)}
    )
    assert {"agent-bridge", "agent-index", "full - agent-worktrees"} <= required


def test_inventory_ignores_shard_policy_reads_outside_the_matrix_function(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch)
    runner = workflow.with_name("run-plugin-tests.py")
    expected = inventory.required_jobs(ROOT)
    with runner.open("a", encoding="utf-8") as stream:
        stream.write("\nunrelated = _CI_SHARD_COUNTS.get('agent-dispatch', 99)\n")
        stream.write("\ndef unrelated_reader():\n    return _CI_SHARD_COUNTS.get('other', 98)\n")
    assert inventory.required_jobs(ROOT) == expected


@pytest.mark.parametrize("mutation", ["missing-function", "duplicate-function", "missing-default", "duplicate-default"])
def test_inventory_refuses_an_absent_or_ambiguous_matrix_shard_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch)
    runner = workflow.with_name("run-plugin-tests.py")
    text = runner.read_text("utf-8")
    if mutation == "missing-function":
        text = text.replace("def _ci_matrix(", "def unrelated_matrix(", 1)
    elif mutation == "duplicate-function":
        text += "\ndef _ci_matrix(plugins):\n    return _CI_SHARD_COUNTS.get('other', 1)\n"
    elif mutation == "missing-default":
        text = text.replace("_CI_SHARD_COUNTS.get(name, 1)", "1")
    else:
        text = text.replace(
            "_CI_SHARD_COUNTS.get(name, 1)",
            "_CI_SHARD_COUNTS.get(name, _CI_SHARD_COUNTS.get(name, 1))",
        )
    runner.write_text(text, encoding="utf-8")
    with pytest.raises(CandidateValidationError, match="shard policy"):
        inventory.required_jobs(ROOT)


@pytest.mark.parametrize("counts", [
    {"agent-dispatch": 0}, {"agent-dispatch": True}, {"agent-dispatch": 1.5},
    {"agent-dispatch": 257}, {"bad/name": 4}, {"missing-qualification-consumer": 4},
])
def test_inventory_refuses_invalid_file_shard_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, counts: dict,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch)
    runner = workflow.with_name("run-plugin-tests.py")
    text = runner.read_text("utf-8")
    runner.write_text(
        text.replace('{"agent-dispatch": 4}', repr(counts), 1), encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError, match="shard policy"):
        inventory.required_jobs(ROOT)


@pytest.mark.parametrize("before,after", [
    ("fromJson(needs.discover.outputs.smoke_matrix)", "fromJson(needs.discover.outputs.other)"),
    ("--list --ci-matrix", "--list"),
    ("--shard-index ${{ matrix.shard_index }}", "--unaccounted-index"),
    ("--shard-count ${{ matrix.shard_count }}", "--unaccounted-count"),
])
def test_inventory_refuses_an_unaccounted_shard_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, before: str, after: str,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch)
    text = workflow.read_text("utf-8")
    assert before in text
    workflow.write_text(text.replace(before, after, 1), encoding="utf-8")
    with pytest.raises(CandidateValidationError, match="sharded smoke recipe"):
        inventory.required_jobs(ROOT)


@pytest.mark.parametrize("index", [1, 2, 3, 4])
@pytest.mark.parametrize("conclusion", ["missing", "failure", "cancelled", "skipped"])
def test_actual_shard_inventory_requires_every_successful_executed_row(
    backend: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    index: int, conclusion: str,
) -> None:
    _candidate_workflow(tmp_path, monkeypatch)
    portfolio = inventory.required_jobs(ROOT)
    monkeypatch.setattr(qualification, "required_jobs", lambda _root: portfolio)
    backend["jobs"] = [
        dict(id=number, name=name, status="completed", conclusion="success")
        for number, name in enumerate(sorted(portfolio[0]), 1)
    ]
    receipt = _qualify()
    assert _verify(receipt) == qualification.receipt_digest(receipt)
    target = f"agent-dispatch ({index}/4)"
    if conclusion == "missing":
        backend["jobs"] = [job for job in backend["jobs"] if job["name"] != target]
    else:
        next(job for job in backend["jobs"] if job["name"] == target)["conclusion"] = conclusion
    with pytest.raises(workflows.VerificationError):
        _verify(receipt)


def test_legacy_unsharded_inventory_does_not_require_new_runner_declarations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch)
    text = workflow.read_text("utf-8")
    text = text.replace(inventory._SHARDED_SMOKE_NAME, "${{ matrix.plugin }}")
    text = text.replace(
        "      matrix: ${{ fromJson(needs.discover.outputs.smoke_matrix) }}",
        "      matrix:\n        plugin: ${{ fromJson(needs.discover.outputs.smoke) }}",
    )
    text = text.replace(
        "          --shard-index ${{ matrix.shard_index }} --shard-count ${{ matrix.shard_count }}\n",
        "",
    )
    workflow.write_text(text, encoding="utf-8")
    workflow.with_name("run-plugin-tests.py").write_text("# Legacy runner\n", encoding="utf-8")
    required, _skips, _digest = inventory.required_jobs(ROOT)
    assert "agent-dispatch" in required
    assert not any(name.startswith("agent-dispatch (") for name in required)


def test_inventory_refuses_a_shard_policy_exceeding_the_hosted_matrix_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch)
    runner = workflow.with_name("run-plugin-tests.py")
    text = runner.read_text("utf-8")
    assert "_CI_SHARD_COUNTS.get(name, 1)" in text
    runner.write_text(
        text.replace("_CI_SHARD_COUNTS.get(name, 1)", "_CI_SHARD_COUNTS.get(name, 256)"),
        encoding="utf-8",
    )
    with pytest.raises(CandidateValidationError, match="matrix limit"):
        inventory.required_jobs(ROOT)


def test_inventory_uses_the_same_declared_source_representation_for_lf_and_crlf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = _candidate_workflow(tmp_path, monkeypatch)
    canonical = workflow.read_text("utf-8").replace("\r\n", "\n").encode("utf-8")
    workflow.write_bytes(canonical)
    lf = inventory.required_jobs(ROOT)
    represented = canonical.replace(b"\n", b"\r\n")
    assert represented != canonical and b"\r\njobs:\r\n" in represented
    workflow.write_bytes(represented)
    assert inventory.required_jobs(ROOT) == lf
    assert lf[2] == hashlib.sha256(canonical).hexdigest()


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


CandidateValidationError = inventory.CandidateValidationError
