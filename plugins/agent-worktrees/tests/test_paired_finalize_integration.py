"""Real Git and CLI proof of bottom-up paired-worktree conclusion."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import NoReturn

import pytest
from agent_procutil import no_window_kwargs
from machine_transport import detect_platform

from agent_worktrees import config as cfg, finalize, tracking, worktree_creation
from agent_worktrees.finalize_lock import FinalizeLock
from conftest import _published_seed_environment, _repair_copied_worktree, _seed_directory

Environment = tuple[dict[str, tracking.WorktreeRecord], dict[str, cfg.Config]]


def _git(*args: str, cwd: str | Path) -> str:
    result: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        check=True, timeout=15, **no_window_kwargs(),
    )
    return result.stdout.strip()


@pytest.fixture(scope="session")
def published_origin(tmp_path_factory: pytest.TempPathFactory) -> Path:
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
def checkout_seed(tmp_path_factory: pytest.TempPathFactory, published_origin: Path) -> Path:
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
def environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_only_mux: None, checkout_seed: Path,
) -> Environment:
    monkeypatch.setattr(finalize, "_warn_of_codespace_claims_for_worktree", lambda *_: None)
    monkeypatch.setattr(finalize, "_warn_of_dev_slot_claims_for_worktree", lambda *_: None)
    shutil.copytree(checkout_seed, tmp_path, dirs_exist_ok=True, copy_function=shutil.copyfile)
    records: dict[str, tracking.WorktreeRecord] = {}
    configurations: dict[str, cfg.Config] = {}
    for project in ("harness", "knowledge"):
        anchor = tmp_path / project
        root = tmp_path / f"{project}.worktrees"
        worktree_id = f"pair-{project}"
        checkout = root / worktree_id
        _repair_copied_worktree(
            checkout_seed / project, checkout_seed / f"{project}.worktrees" / worktree_id,
            anchor, checkout,
        )
        directory = cfg.project_dir(project)
        directory.mkdir(parents=True)
        document = {
            "repo_name": project, "machine": "host", "platform": detect_platform(),
            "repos": {project: {
                "anchor": str(anchor), "worktree_root": str(root),
                "default_branch": "main", "remote": "origin",
            }},
        }
        (directory / "config.yaml").write_text(json.dumps(document), encoding="utf-8")
        configurations[project] = cfg.load_project_config(project)
        records[project] = tracking.create_new_record(
            worktree_id, f"worktree/{worktree_id}", str(checkout), project,
            "host", detect_platform(), directory / "worktrees",
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


def _finalize(
    record: tracking.WorktreeRecord, configurations: dict[str, cfg.Config],
    monkeypatch: pytest.MonkeyPatch,
) -> bool:
    monkeypatch.setattr(cfg, "tracking_dir", lambda: record.yaml_path.parent)
    return finalize.validate_and_finalize(record.worktree_id, configurations[record.repo])


def test_local_pair_fixture_uses_private_git_store(environment: Environment) -> None:
    records, configurations = environment
    for project, record in records.items():
        common = _git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=record.worktree_path)
        assert Path(common).resolve() == (Path(configurations[project].default_repo.anchor) / ".git").resolve()


@pytest.mark.parametrize("operation", ["settle", "remove", "finalize"])
def test_paired_claim_blocks_the_real_cli(environment: Environment, operation: str) -> None:
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    assert harness.pair_ref is not None
    verb = {
        "settle": ["claims", "settle", harness.pair_ref],
        "remove": ["claims", "release", harness.pair_ref, "--remove"],
        "finalize": ["finalize", harness.worktree_id],
    }[operation]
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "-p", "harness", *verb, "--json"],
        cwd=harness.worktree_path,
        env={**os.environ, "AGENT_WORKTREES_STATUS_MONITOR": "0"},
        capture_output=True, text=True, timeout=25, **no_window_kwargs(),
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "paired knowledge worktree" in (result.stdout + result.stderr).lower()
    assert tracking.load_record(harness.yaml_path).resources[0].is_unsettled
    assert Path(knowledge.worktree_path).is_dir()


def test_paired_claim_settles_bottom_up(
    environment: Environment, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    assert not _finalize(harness, configurations, monkeypatch)

    assert _finalize(knowledge, configurations, monkeypatch)
    assert Path(knowledge.worktree_path).is_dir()
    claim, = tracking.load_record(harness.yaml_path).resources
    assert claim.is_at_rest


@pytest.mark.parametrize("dirty", [
    pytest.param(False, marks=pytest.mark.skipif(
        sys.platform == "win32",
        reason="Successful native Windows retirement requires a qualified private PSMux observation.",
    )),
    True,
])
def test_native_cli_finalizes_remote_free_knowledge_without_weakening_pair(
    environment: Environment, dirty: bool,
) -> None:
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    _git("remote", "remove", "origin", cwd=configurations["knowledge"].default_repo.anchor)
    if dirty:
        (Path(knowledge.worktree_path) / "unfinished.txt").write_text("keep\n")
    result = subprocess.run(
        [sys.executable, "-m", "agent_worktrees", "-p", "knowledge",
         "finalize", knowledge.worktree_id, "--json"],
        cwd=configurations["harness"].default_repo.anchor,
        env={**os.environ, "AGENT_WORKTREES_STATUS_MONITOR": "0"},
        capture_output=True, text=True, timeout=25, **no_window_kwargs(),
    )
    assert (result.returncode == 0) == (not dirty), result.stdout + result.stderr
    assert tracking.load_record(knowledge.yaml_path).status == ("active" if dirty else "finalized")
    claim, = tracking.load_record(harness.yaml_path).resources
    assert claim.is_unsettled if dirty else claim.is_at_rest
    assert Path(knowledge.worktree_path).is_dir()


def _concluded_knowledge(
    harness: tracking.WorktreeRecord, knowledge: tracking.WorktreeRecord,
) -> None:
    assert harness.pair_ref is not None
    tracking.update_status(knowledge, "finalized")
    tracking.settle_resource_claim(harness, harness.pair_ref)


def test_harness_finalizes_after_verified_knowledge_conclusion(
    environment: Environment, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    _concluded_knowledge(harness, knowledge)
    assert _finalize(harness, configurations, monkeypatch)
    assert tracking.load_record(harness.yaml_path).status == "finalized"
    assert Path(harness.worktree_path).is_dir()
    assert Path(knowledge.worktree_path).is_dir()


@pytest.mark.parametrize("creator_link", [False, True])
def test_repeated_knowledge_finalization_keeps_the_parent_pair_released(
    environment: Environment, monkeypatch: pytest.MonkeyPatch, creator_link: bool,
) -> None:
    from agent_worktrees import retirement_safety

    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    assert harness.pair_ref is not None
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
    environment: Environment, monkeypatch: pytest.MonkeyPatch,
    kind: tracking.WorktreeKind, status: tracking.WorktreeStatus, missing: bool,
) -> None:
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    assert harness.pair_ref is not None
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
    environment: Environment, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    assert harness.pair_ref is not None
    _concluded_knowledge(harness, knowledge)
    tracking.release_resource_claim(harness, harness.pair_ref)
    tracking.update_status(harness, "finalized")
    completed_at = harness.completed_at
    acquire = FinalizeLock.acquire
    draft = Path(knowledge.worktree_path) / "reopened.txt"

    def reopen_after_freeze(lock: FinalizeLock) -> None:
        acquire(lock)
        assert tracking.load_record(harness.yaml_path).status == "finalizing"
        draft.write_text("preserved reopened work\n", encoding="utf-8")
        tracking.update_status(knowledge, "active")

    monkeypatch.setattr(FinalizeLock, "acquire", reopen_after_freeze)
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
    environment: Environment, monkeypatch: pytest.MonkeyPatch, work: str,
) -> None:
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


def test_finalize_fences_sibling_reopen_through_terminal_save_and_defers_removal(
    environment: Environment, monkeypatch: pytest.MonkeyPatch,
) -> None:
    records, configurations = environment
    harness, knowledge = records["harness"], records["knowledge"]
    _concluded_knowledge(harness, knowledge)
    from agent_worktrees import git_ops

    observed: list[tuple[str, str]] = []

    def attempt_reopen(phase: str) -> None:
        def reopen() -> None:
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

    update = tracking.update_status

    def remove_under_fence(*args: object, **kwargs: object) -> NoReturn:
        pytest.fail("finalize must retain paired checkouts for joint cleanup")

    def terminal_under_fence(
        record: tracking.WorktreeRecord, status: tracking.WorktreeStatus,
        *, save: bool = True,
    ) -> None:
        if record.worktree_id == harness.worktree_id and status == "finalized":
            attempt_reopen("terminal-save")
        update(record, status, save=save)

    monkeypatch.setattr(git_ops, "remove_worktree", remove_under_fence)
    monkeypatch.setattr(tracking, "update_status", terminal_under_fence)
    assert _finalize(harness, configurations, monkeypatch)
    assert observed == [("terminal-save", "fenced")]
    assert Path(harness.worktree_path).is_dir() and Path(knowledge.worktree_path).is_dir()
    assert tracking.load_record(knowledge.yaml_path).status == "finalized"
    with tracking._RecordLock(knowledge.yaml_path, timeout=0.1, require_sidecar=True):
        pass
