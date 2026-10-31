"""Read the real controller proposal; composition precedes all release evidence."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_real_candidate_workflow_freezes_once_before_units_and_artifacts():
    jobs = yaml.safe_load((ROOT / ".github/workflows/validate-and-promote.yml").read_text())["jobs"]
    assert jobs["compose-candidate"]["needs"] == "gate"
    assert {"gate", "compose-candidate"} <= set(jobs["source-units"]["needs"])
    assert {"gate", "compose-candidate"} <= set(jobs["build-python-artifacts"]["needs"])
    assert {"compose-candidate", "source-units", "build-python-artifacts"} <= set(jobs["python-artifacts"]["needs"])
    assert {"compose-candidate", "source-units", "python-artifacts"} <= set(jobs["promote"]["needs"])
    for name in ("source-units", "build-python-artifacts", "python-artifacts", "promote"):
        actions = jobs[name]["steps"]
        assert any(
            step.get("with", {}).get("name") == "frozen-promotion-candidate"
            and step.get("uses", "").startswith("actions/download-artifact@")
            for step in actions
        )
        commands = "\n".join(step.get("run", "") for step in actions)
        assert "consume_pending_changes" not in commands
        assert "--build-python-artifacts-dir" not in commands
        assert "--prepare-candidate-dir" not in commands
        expected = (
            '--expected-candidate-sha "$EXPECTED_CANDIDATE_SHA"' if name == "promote"
            else '--candidate-sha "$CANDIDATE_SHA"'
        )
        assert expected in commands
        assert any(
            step.get("env", {}).get("EXPECTED_CANDIDATE_SHA" if name == "promote" else "CANDIDATE_SHA")
            == "${{ needs.compose-candidate.outputs.candidate_sha }}"
            for step in actions
        )
    units = "\n".join(step.get("run", "") for step in jobs["source-units"]["steps"])
    assert '"$RUNNER_TEMP/candidate/tools/run-promotion-unit-tests.py"' in units
    assert '--development-root "$GITHUB_WORKSPACE"' in units and '--candidate-tree "$CANDIDATE_TREE"' in units
    assert jobs["source-units"]["timeout-minutes"] == 5
    assert "source-units" not in jobs["build-python-artifacts"]["needs"]
    assert jobs["python-artifacts"]["permissions"]["id-token"] == "write"
    assert "id-token" not in jobs["build-python-artifacts"]["permissions"]


def test_real_controller_publication_requires_explicit_candidate_profile():
    jobs = yaml.safe_load((ROOT / ".github/workflows/validate-and-promote.yml").read_text())["jobs"]
    for name, argument in (
        ("qualify-candidate", "--profile candidate-units-v3"),
        ("publish-python-artifacts", "--qualification-profile candidate-units-v3"),
    ):
        commands = "\n".join(step.get("run", "") for step in jobs[name]["steps"])
        assert argument in commands
        assert "source-units-v2" not in commands
    assert "candidate-units-v3" in jobs["merge-promotion"]["if"]
    assert "compose-candidate" in jobs["report-failure"]["needs"]
    assert "needs.compose-candidate.result == 'failure'" in jobs["report-failure"]["if"]


def test_complete_source_is_attested_and_read_back_before_the_final_main_pr():
    job = yaml.safe_load((ROOT / ".github/workflows/validate-and-promote.yml").read_text())["jobs"]["publish-python-artifacts"]
    steps = job["steps"]
    prepare = next(number for number, step in enumerate(steps) if "promotion_source_archive.py" in step.get("run", ""))
    attest = next(number for number, step in enumerate(steps) if step.get("uses", "").startswith("actions/attest-build-provenance@"))
    publish = next(number for number, step in enumerate(steps) if step.get("id") == "publish")
    land = next(number for number, step in enumerate(steps) if step.get("id") == "land")
    assert prepare < attest < publish < land
    assert steps[attest]["with"]["subject-path"] == "${{ runner.temp }}/complete-source/*"
    assert '--complete-source-dir "$RUNNER_TEMP/complete-source"' in steps[publish]["run"]
    assert "--qualification-receipt qualification/qualification.json" in steps[prepare]["run"]
    assert "--source-index python-artifacts/python-artifacts.json" in steps[prepare]["run"]
    assert job["permissions"]["id-token"] == job["permissions"]["attestations"] == "write"
    assert "GH_TOKEN" not in steps[prepare].get("env", {})
    assert "APERTURE_RELEASE_TOKEN" not in str(steps[attest])
