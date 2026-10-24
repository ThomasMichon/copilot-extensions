"""Real Git and CLI proof of bottom-up paired-worktree conclusion."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest

from agent_worktrees import config as cfg, finalize, tracking, worktree_creation
from conftest import _published_seed_environment, _seed_directory


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        check=True, timeout=15,
    ).stdout.strip()


@pytest.fixture(scope="session")
def published_origin(tmp_path_factory):
    with _seed_directory(tmp_path_factory, "paired-published") as (root, build):
        origin, seed = root / "origin.git", root / "seed"
        if build:
            with _published_seed_environment(tmp_path_factory):
                _git("init", "--template=", "--bare", "-b", "main", str(origin), cwd=root)
                _git("clone", str(origin), str(seed), cwd=root)
                (seed / "base.txt").write_text("published\n", encoding="utf-8")
                _git("add", "base.txt", cwd=seed)
                _git(
                    "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "commit", "-m", "published", cwd=seed,
                )
                _git("push", "origin", "main", cwd=seed)
        return origin


@pytest.fixture(scope="session")
def checkout_seed(tmp_path_factory, published_origin):
    with _seed_directory(tmp_path_factory, "paired-checkouts") as (seed, build):
        if build:
            with _published_seed_environment(tmp_path_factory):
                for project in ("harness", "knowledge"):
                    anchor = seed / project
                    _git(
                        "clone", "--no-hardlinks", "--config", "user.name=Test",
                        "--config", "user.email=test@example.com",
                        str(published_origin), str(anchor), cwd=seed,
                    )
                    root = seed / f"{project}.worktrees"
                    worktree_id = f"pair-{project}"
                    checkout = root / worktree_id
                    _git(
                        "worktree", "add", "-b", f"worktree/{worktree_id}",
                        str(checkout), "origin/main", cwd=anchor,
                    )
        return seed


@pytest.fixture
def environment(tmp_path, monkeypatch, git_only_mux, checkout_seed):
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *_: None)
    monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *_: None)
    shutil.copytree(checkout_seed, tmp_path, dirs_exist_ok=True, copy_function=shutil.copyfile)
    records, configurations = {}, {}
    for project in ("harness", "knowledge"):
        anchor = tmp_path / project
        root = tmp_path / f"{project}.worktrees"
        worktree_id = f"pair-{project}"
        checkout = root / worktree_id
        _git("worktree", "repair", str(checkout), cwd=anchor)
        directory = cfg.project_dir(project)
        directory.mkdir(parents=True)
        document = {
            "repo_name": project, "machine": "host", "platform": cfg.detect_platform(),
            "repos": {project: {
                "anchor": str(anchor), "worktree_root": str(root),
                "default_branch": "main", "remote": "origin",
            }},
        }
        (directory / "config.yaml").write_text(json.dumps(document), encoding="utf-8")
        configurations[project] = cfg.load_project_config(project)
        records[project] = tracking.create_new_record(
            worktree_id, f"worktree/{worktree_id}", str(checkout), project,
            "host", cfg.detect_platform(), directory / "worktrees",
        )
    # These fixture configurations never change; CLI children still load them
    # independently. Keep in-process lifecycle checks on the same admitted inputs.
    monkeypatch.setattr(
        cfg, "load_project_config", lambda project, **_kwargs: configurations[project],
    )
    harness, knowledge = records["harness"], records["knowledge"]
    knowledge.pair_id, knowledge.pair_role, knowledge.pair_kind = "pair", "knowledge", "worktree"
    knowledge.pair_ref = f"host/harness/{harness.worktree_id}"
    tracking.save_record(knowledge)
    from agent_worktrees import knowledge_plugins

    monkeypatch.setattr(knowledge_plugins, "compose_from_pair", lambda **kwargs: {})
    worktree_creation._stamp_and_compose_paired_knowledge(
        configurations["harness"], harness, harness.worktree_path,
        {
            "pair_id": "pair", "pair_role": "harness", "pair_kind": "worktree",
            "pair_ref": f"host/knowledge/{knowledge.worktree_id}",
        },
    )
    return records, configurations


def _finalize(record, configurations, monkeypatch):
    monkeypatch.setattr(cfg, "tracking_dir", lambda: record.yaml_path.parent)
    return finalize.validate_and_finalize(record.worktree_id, configurations[record.repo])


@pytest.mark.parametrize("operation", ["settle", "remove", "finalize"])
def test_paired_claim_blocks_the_real_cli(environment, operation):
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    verb = {
        "settle": ["claims", "settle", harness.pair_ref],
        "remove": ["claims", "release", harness.pair_ref, "--remove"],
        "finalize": ["finalize", harness.worktree_id],
    }[operation]
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "-p", "harness", *verb, "--json"],
        cwd=harness.worktree_path,
        env={**os.environ, "AGENT_WORKTREES_STATUS_MONITOR": "0"},
        capture_output=True, text=True, timeout=25,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "paired knowledge worktree" in (result.stdout + result.stderr).lower()
    assert tracking.load_record(harness.yaml_path).resources[0].is_unsettled
    assert Path(knowledge.worktree_path).is_dir()


def test_paired_claim_settles_bottom_up(environment, monkeypatch):
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    assert not _finalize(harness, configurations, monkeypatch)

    assert _finalize(knowledge, configurations, monkeypatch)
    assert Path(knowledge.worktree_path).is_dir()
    claim, = tracking.load_record(harness.yaml_path).resources
    assert claim.is_at_rest


def _concluded_knowledge(harness, knowledge):
    tracking.update_status(knowledge, "finalized")
    tracking.settle_resource_claim(harness, harness.pair_ref)


def test_harness_finalizes_after_verified_knowledge_conclusion(environment, monkeypatch):
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    _concluded_knowledge(harness, knowledge)
    assert _finalize(harness, configurations, monkeypatch)
    assert tracking.load_record(harness.yaml_path).status == "finalized"


@pytest.mark.parametrize("creator_link", [False, True])
def test_repeated_knowledge_finalization_keeps_the_parent_pair_released(
    environment, monkeypatch, creator_link,
):
    from agent_worktrees import retirement_safety

    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    if creator_link:
        knowledge.owner_ref = knowledge.pair_ref
        tracking.save_record(knowledge)
    _concluded_knowledge(harness, knowledge)
    tracking.release_resource_claim(harness, harness.pair_ref)
    tracking.update_status(harness, "finalized")
    before = harness.yaml_path.read_bytes()
    assert _finalize(knowledge, configurations, monkeypatch)
    parent = tracking.load_record(harness.yaml_path)
    assert parent.yaml_path.read_bytes() == before
    claim, = parent.resources
    assert claim.state == "released"
    assert retirement_safety.retirement_blocker(parent) is None


@pytest.mark.parametrize("kind,status", [("bridge", "complete"), ("system", "completed")])
@pytest.mark.parametrize("missing", [False, True])
def test_completed_managed_harness_can_finalize_its_durable_pair(
    environment, monkeypatch, kind, status, missing,
):
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    _concluded_knowledge(harness, knowledge)
    tracking.release_resource_claim(harness, harness.pair_ref)
    if missing:
        harness.resources = []
        tracking.save_record(harness)
    harness.kind = kind
    tracking.update_status(harness, status)
    monkeypatch.chdir(harness.worktree_path)
    assert _finalize(harness, configurations, monkeypatch)
    parent = tracking.load_record(harness.yaml_path)
    assert parent.status == "finalized"
    assert not parent.live_resources
    assert Path(harness.worktree_path).is_dir()


def test_reopened_sibling_between_freeze_and_cleanup_restores_mutable_harness(
    environment, monkeypatch,
):
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    _concluded_knowledge(harness, knowledge)
    tracking.release_resource_claim(harness, harness.pair_ref)
    tracking.update_status(harness, "finalized")
    completed_at = harness.completed_at
    acquire = finalize.FinalizeLock.acquire
    draft = Path(knowledge.worktree_path) / "reopened.txt"

    def reopen_after_freeze(lock):
        result = acquire(lock)
        assert tracking.load_record(harness.yaml_path).status == "finalizing"
        draft.write_text("preserved reopened work\n", encoding="utf-8")
        tracking.update_status(knowledge, "active")
        return result

    monkeypatch.setattr(finalize.FinalizeLock, "acquire", reopen_after_freeze)
    assert not _finalize(harness, configurations, monkeypatch)
    saved = tracking.load_record(harness.yaml_path)
    assert saved.status == "active"
    assert saved.completed_at is None
    assert saved.last_finalized_at == completed_at
    claim, = saved.resources
    assert claim.is_unsettled
    assert Path(harness.worktree_path).is_dir()
    assert draft.read_text(encoding="utf-8") == "preserved reopened work\n"
    assert not _finalize(saved, configurations, monkeypatch)
    assert tracking.load_record(harness.yaml_path).status == "active"


@pytest.mark.parametrize("work", ["untracked", "tracked", "committed"])
def test_unfinished_paired_sibling_cannot_finalize_and_clear_parent(
    environment, monkeypatch, work,
):
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    draft = Path(knowledge.worktree_path) / ("base.txt" if work == "tracked" else "unpublished.txt")
    draft.write_text("unfinished knowledge\n", encoding="utf-8")
    if work == "committed":
        _git("add", draft.name, cwd=knowledge.worktree_path)
        _git("commit", "-m", "unfinished", cwd=knowledge.worktree_path)
    before = draft.read_bytes()
    assert not _finalize(knowledge, configurations, monkeypatch)
    assert draft.read_bytes() == before
    assert tracking.load_record(knowledge.yaml_path).status == "active"
    assert tracking.load_record(harness.yaml_path).resources[0].is_unsettled


def test_finalize_fences_sibling_reopen_through_removal_and_terminal_save(
    environment, monkeypatch,
):
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    _concluded_knowledge(harness, knowledge)
    from agent_worktrees import git_ops, prune

    observed = []

    def attempt_reopen(phase):
        def reopen():
            try:
                with tracking._RecordLock(
                    knowledge.yaml_path, timeout=0.05, require_sidecar=True,
                ):
                    sibling = tracking.load_record(knowledge.yaml_path)
                    tracking.update_status(sibling, "active")
                observed.append((phase, "reopened"))
            except TimeoutError:
                observed.append((phase, "fenced"))
        worker = threading.Thread(target=reopen)
        worker.start()
        worker.join(2)
        assert not worker.is_alive()

    probe, remove, update = (
        prune.default_paired_sibling_final, git_ops.remove_worktree, tracking.update_status,
    )

    def probe_then_reopen(record):
        result = probe(record)
        attempt_reopen("after-probe")
        return result

    def remove_under_fence(*args, **kwargs):
        attempt_reopen("before-removal")
        return remove(*args, **kwargs)

    def terminal_under_fence(record, status, *args, **kwargs):
        if record.worktree_id == harness.worktree_id and status == "finalized":
            attempt_reopen("terminal-save")
        return update(record, status, *args, **kwargs)

    monkeypatch.setattr(prune, "default_paired_sibling_final", probe_then_reopen)
    monkeypatch.setattr(git_ops, "remove_worktree", remove_under_fence)
    monkeypatch.setattr(tracking, "update_status", terminal_under_fence)
    assert _finalize(harness, configurations, monkeypatch)
    assert observed == [
        ("after-probe", "fenced"), ("before-removal", "fenced"),
        ("terminal-save", "fenced"),
    ]
    assert tracking.load_record(knowledge.yaml_path).status == "finalized"
    with tracking._RecordLock(knowledge.yaml_path, timeout=0.1, require_sidecar=True):
        pass
