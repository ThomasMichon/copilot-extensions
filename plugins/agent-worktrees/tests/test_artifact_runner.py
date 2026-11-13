"""Opt-in qualification of the real contained runner through native scratch custody."""

import importlib.util
import os
from pathlib import Path
import secrets
import sys
import subprocess

import pytest

from agent_worktrees import artifact_scratch, finalize, project_state, tracking
from test_resource_producer_finalize import _final_owner_seed as _final_owner_seed
from test_resource_producer_finalize import final_owner as final_owner

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]


@pytest.mark.timeout(90)
@pytest.mark.parametrize("fails", [False, True])
def test_real_runner_retains_only_bounded_evidence_then_finalizes_and_cleans(
    final_owner, tmp_path_factory, monkeypatch, fails,
):
    root, ledger, config = final_owner
    initial = tracking.load_record(ledger)
    initial.creation_nonce = secrets.token_hex(16)
    tracking.save_record(initial, ledger)
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    spec = importlib.util.spec_from_file_location("owned_scratch_runner", repo / "tools" / "run-plugin-tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    owner = tracking.format_claim_ref(config.machine, config.repo_name, "owner")
    selected_root = tmp_path_factory.mktemp("owned-runner-root") / "private"
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(selected_root))
    monkeypatch.setattr(project_state, "canonical_repository_id", lambda *a: "repo-fixture")
    fixture_plugin = root / "fixture-plugin"
    tests = fixture_plugin / "tests"
    tests.mkdir(parents=True)
    (tests / "test_fixture.py").write_text(
        "def test_fixture():\n    assert " + ("False" if fails else "True") + "\n",
        encoding="utf-8",
    )
    (fixture_plugin / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    cache = root / "retained-cache"
    cache.mkdir()
    sentinel = cache / "caller-cache.bin"
    sentinel.write_bytes(b"cache is not disposable scratch")
    monkeypatch.setattr(runner, "OWNED_SCRATCH_OWNER", owner)
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: fixture_plugin)
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **k: Path(sys.executable))
    monkeypatch.setattr(runner, "VENV_ROOT", cache)
    result = runner.run_plugin(
        "fixture", "unused-existing-deps", reinstall=False, kexpr=None,
        limits=runner.Limits(wall_seconds=30), plugin_timeout=60, test_timeout=15,
        max_files_per_subsuite=25,
    )
    assert result == int(fails)
    record = tracking.load_record(ledger)
    assert len(record.resources) == 1
    claim = record.resources[0]
    assert claim.producer["phase"] == ("retained" if fails else "closed")
    location = selected_root / artifact_scratch._name(claim.producer["recovery"])
    if fails:
        assert {entry.name for entry in location.iterdir()} == {
            "scratch-owner.json", "runner-diagnostic.txt",
        }
        diagnostic = (location / "runner-diagnostic.txt").read_bytes()
        assert len(diagnostic) <= 8 * 1024 * 1024
        assert b'"exit_code": 1' in diagnostic
        monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *a: None)
        monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *a: None)
        assert finalize.validate_and_finalize("owner", config)
        assert tracking.load_record(ledger).status == "finalized" and location.exists()
        preview = artifact_scratch.preview_cleanup(ledger, owner, claim.producer["token"], forbidden=())
        outcome = artifact_scratch.cleanup(
            ledger, owner, claim.producer["token"], preview=preview["preview"], forbidden=(),
        )
        assert outcome["outcome"] == "removed"
    assert not location.exists()
    assert sentinel.read_bytes() == b"cache is not disposable scratch"


def test_unknown_cleanup_is_never_pruned_or_settled(final_owner, tmp_path_factory, monkeypatch):
    root, ledger, config = final_owner
    initial = tracking.load_record(ledger)
    initial.creation_nonce = secrets.token_hex(16)
    tracking.save_record(initial, ledger)
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    from plugin_test_owned_scratch import OwnedTestSandbox
    from plugin_test_containment import UnreapedProcessError
    monkeypatch.setattr(project_state, "canonical_repository_id", lambda *a: "repo-fixture")
    selected_root = tmp_path_factory.mktemp("unknown-cleanup") / "private"
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(selected_root))
    owner = tracking.format_claim_ref(config.machine, config.repo_name, "owner")
    scope = OwnedTestSandbox(owner, repo, stage="tests")
    with pytest.raises(UnreapedProcessError):
        with scope as raw:
            location = Path(raw)
            (location / "still-in-use.bin").write_bytes(b"must remain untouched")
            raise UnreapedProcessError("fixture: descendant cleanup unknown")
    claim = tracking.load_record(ledger).resources[0]
    assert claim.producer["phase"] == "bound" and claim.is_unsettled
    assert (location / "still-in-use.bin").read_bytes() == b"must remain untouched"
    assert not (location / "runner-diagnostic.txt").exists()
    assert not finalize.validate_and_finalize("owner", config)


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction")
def test_root_admission_refuses_junctions_before_allocation(tmp_path):
    from agent_procutil import no_window_kwargs
    from agent_procutil._daemon_instance_owner import mkdir_private
    from agent_worktrees.artifact_root import RootAdmissionError, admit_root

    target, link = tmp_path / "target", tmp_path / "junction"
    mkdir_private(target)
    subprocess.run(
        [os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(target)],
        check=True, capture_output=True, timeout=10, **no_window_kwargs(),
    )
    for selected in (link, link / "scratch"):
        with pytest.raises(RootAdmissionError, match="links"):
            with admit_root(selected, forbidden=(), create=True):
                pytest.fail("junction admitted as private scratch")
    assert not (target / "scratch").exists()
