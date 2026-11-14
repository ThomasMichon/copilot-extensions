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


@pytest.fixture
def runner_owner(final_owner, tmp_path_factory, monkeypatch):
    root, ledger, config = final_owner
    initial = tracking.load_record(ledger)
    initial.creation_nonce = secrets.token_hex(16)
    tracking.save_record(initial, ledger)
    repo = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repo / "tools"))
    spec = importlib.util.spec_from_file_location("preparation_runner", repo / "tools" / "run-plugin-tests.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    owner = tracking.format_claim_ref(config.machine, config.repo_name, "owner")
    selected = tmp_path_factory.mktemp("runner-root-admission") / "private"
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(selected))
    monkeypatch.setattr(project_state, "canonical_repository_id", lambda *a: "repo-fixture")
    monkeypatch.setattr(runner, "OWNED_SCRATCH_OWNER", owner)
    return runner, owner, selected, root, ledger


@pytest.mark.timeout(180)
def test_preparation_uses_real_contained_worker_and_preserves_cached_environment(runner_owner, monkeypatch):
    import plugin_test_environment
    runner, _, _, root, ledger = runner_owner
    monkeypatch.setattr(runner, "VENV_ROOT", root / "cache")
    py = runner._venv_python("agent-worktrees")
    py.parent.mkdir(parents=True)
    py.write_bytes(b"warm-cache sentinel; never executed")
    venv = root / "cache" / "agent-worktrees"
    (venv / ".bootstrap-uv-cache").mkdir()
    (venv / ".dep-fingerprint").write_text(runner._dep_fingerprint("agent-worktrees"))
    monkeypatch.setattr(
        plugin_test_environment, "preparation_environment",
        lambda base, sandbox, *a, **k: runner.isolated_environment(base, Path(sandbox)),
    )
    assert runner._ensure_venv("agent-worktrees", "unused-warm-cache", reinstall=False) == py
    assert py.read_bytes() == b"warm-cache sentinel; never executed"
    claim = tracking.load_record(ledger).resources[0]
    assert claim.producer["phase"] == "closed"


def test_real_main_admits_fresh_root_before_legacy_cache_initialization(runner_owner, monkeypatch):
    from agent_worktrees.artifact_root import admit_root
    runner, owner, selected, _, ledger = runner_owner
    seen = []
    def cache_root():
        with admit_root(selected, forbidden=()):
            seen.append("private-before-cache")
        return selected / "cache"
    which = runner.shutil.which
    monkeypatch.setattr(runner.shutil, "which", lambda name: "fixture-uv" if name == "uv" else which(name))
    monkeypatch.setattr(runner, "_venv_root", cache_root)
    monkeypatch.setattr(runner, "_acquire_admission", lambda *a: None)
    monkeypatch.setattr(runner, "run_plugin", lambda *a, **k: 0)
    assert not selected.exists()
    assert runner.main(["agent-worktrees", "--owned-scratch-owner", owner]) == 0
    assert seen == ["private-before-cache"]
    assert tracking.load_record(ledger).resources == []


def test_unknown_preparation_cleanup_retains_the_original_allocation(runner_owner, monkeypatch):
    import plugin_test_environment
    import plugin_test_owned_scratch
    from plugin_test_containment import UnreapedProcessError
    runner, _, selected, root, ledger = runner_owner
    monkeypatch.setattr(runner, "VENV_ROOT", root / "cache")
    monkeypatch.setattr(
        plugin_test_environment, "preparation_environment",
        lambda base, sandbox, *a, **k: runner.isolated_environment(base, Path(sandbox)),
    )
    def unknown(*a, **k):
        raise UnreapedProcessError("fixture preparation cleanup unknown")
    monkeypatch.setattr(plugin_test_owned_scratch, "run_contained", unknown)
    with pytest.raises(UnreapedProcessError):
        runner._ensure_venv("agent-worktrees", "unused", reinstall=False)
    claim = tracking.load_record(ledger).resources[0]
    allocation = selected / artifact_scratch._name(claim.producer["recovery"])
    assert claim.producer["phase"] == "bound" and claim.is_unsettled
    assert allocation.exists() and not (allocation / "runner-diagnostic.txt").exists()


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


@pytest.mark.parametrize("codes,guards,overrun,expected", [
    ((5, 0), False, False, 0), ((0, 5), False, False, 0), ((5, 5), False, False, 5),
    ((1, 0), False, False, 1), ((5,), True, False, 0), ((0,), False, True, 124),
])
def test_disposition_uses_the_normalized_partition_outcome(
    runner_owner, monkeypatch, codes, guards, overrun, expected,
):
    from contextlib import contextmanager
    from types import SimpleNamespace
    runner, _, selected, root, ledger = runner_owner
    plugin = root / "fixture-plugin"
    (plugin / "tests").mkdir(parents=True)
    test = plugin / "tests/test_fixture.py"
    test.write_text("def test_fixture(): pass\n")
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: plugin)
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **k: Path(sys.executable))
    monkeypatch.setattr(runner, "VENV_ROOT", root / "cache")
    monkeypatch.setattr(runner, "_test_group_plan", lambda *a: [([test], "fixture") for _ in codes])
    results = iter(codes)
    monkeypatch.setattr(runner, "run_contained", lambda *a, **k: next(results))
    if overrun:
        clock = [0.0]
        cleanup_worker = runner._group_cleanup_worker

        @contextmanager
        def late_cleanup():
            with cleanup_worker() as state:
                yield state
            clock[0] = 100.0

        monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock[0]))
        monkeypatch.setattr(runner, "_group_cleanup_worker", late_cleanup)
    outcome = runner.run_plugin(
        "fixture", "unused", reinstall=False, kexpr=None if guards else "fixture", guards=guards,
        limits=runner.Limits(), plugin_timeout=60, test_timeout=15, max_files_per_subsuite=25,
    )
    assert outcome == expected
    claim = tracking.load_record(ledger).resources[0]
    assert claim.producer["phase"] == ("retained" if expected else "closed")
    location = selected / artifact_scratch._name(claim.producer["recovery"])
    assert location.exists() is bool(expected)
    if overrun:
        assert b'"exit_code": 124' in (location / "runner-diagnostic.txt").read_bytes()


@pytest.mark.parametrize("cleanup", ["held", "error"])
def test_ordinary_daemon_cleanup_failure_preserves_active_scratch(runner_owner, monkeypatch, cleanup):
    from contextlib import contextmanager
    from types import SimpleNamespace
    import plugin_test_containment as contained
    from plugin_test_owned_scratch import OwnedTestSandbox
    runner, owner, _, _, ledger = runner_owner

    @contextmanager
    def lifecycle(**kwargs):
        yield {}
        if cleanup == "error":
            raise OSError("fixture daemon registry cleanup failed")
        kwargs["cleanup_results"].append(SimpleNamespace(status="held"))

    monkeypatch.setattr(contained, "daemon_test_lifecycle", lifecycle)
    monkeypatch.setattr(contained, "_run_contained_process", lambda *a, **k: 0)
    monkeypatch.setattr(contained, "_read_registry_environment", lambda: None)
    monkeypatch.setattr(contained, "_registry_environment_drift", lambda _: [])
    with pytest.raises(contained.UnreapedProcessError, match="unsettled"):
        with OwnedTestSandbox(owner, runner.REPO, stage="tests") as raw:
            location = Path(raw)
            record = location / "daemon-record"
            record.write_bytes(b"unresolved daemon custody")
            contained.run_contained(
                ["unused"], cwd=runner.REPO, env={}, sandbox=location, limits=contained.Limits(),
            )
    claim = tracking.load_record(ledger).resources[0]
    assert claim.producer["phase"] == "bound" and claim.is_unsettled
    assert record.read_bytes() == b"unresolved daemon custody"
