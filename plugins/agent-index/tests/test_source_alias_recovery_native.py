"""Separate native workers retain the canonical-family stale-alias repair rule."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import numpy as np
import pytest
from agent_procutil import no_window_kwargs
from numpy.typing import NDArray

from agent_index.index_config import IndexConfig
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.state import IndexState
from agent_index.store.source_layout import LAYOUT, initialize_source_store

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]

FAMILY = "git:repo"
ORIGINAL = "# Needle\n\noriginal first document\n"
MIDDLE = "# Needle\n\ntemporary first document\n"
LAST = "# Needle\n\nlatest second document\n"


def _fixture_environment() -> dict[str, str]:
    return {
        **{name: value for name, value in os.environ.items() if not name.startswith("GIT_")},
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
    }


def _git(repo: Path, *args: str) -> str:
    executable = shutil.which("git")
    assert executable is not None
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [executable, "-C", str(repo), *args], capture_output=True, text=True,
        timeout=10, check=True, env=_fixture_environment(), **no_window_kwargs(),
    )
    return result.stdout.strip()


def _child(data: Path, repo: Path, alias: str, mode: str) -> None:
    from agent_index.engine.client import EngineClient
    from agent_index.indexing.task_store import TaskStore
    from agent_index.indexing.worker import run_worker
    from agent_index.sources.git_repo import GitRepoConnector

    inline = {"role": "host", "corpus": {"sources": [
        {"name": name, "type": "git", "_repo_path": str(repo)}
        for name in ("git:left", "git:right")
    ]}}
    environment = {
        "AGENT_INDEX_STORE_LAYOUT": LAYOUT, "AGENT_INDEX_DATA_DIR": str(data),
        "AGENT_INDEX_HOME": str(data.parent / "home"), "AGENT_INDEX_SOURCE_MODE": "explicit",
        "AGENT_INDEX_INDEX_ENSURE_ENGINES": "0", "AGENT_INDEX_INDEXER_NICE": "0",
        "AGENT_INDEX_GIT_FETCH": "0",
        "AGENT_INDEX_CONFIG_DATA_B64": base64.urlsafe_b64encode(
            json.dumps(inline).encode(),
        ).decode(),
    }

    def inference(_client: EngineClient, texts: list[str]) -> NDArray[np.float32]:
        vectors = np.zeros((len(texts), 768), dtype=np.float32)
        vectors[:, 0] = 1
        return vectors

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("stale alias reached provider acquisition")

    def forbid_http(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("synthetic worker attempted HTTP")

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, environment))
        stack.enter_context(patch.object(EngineClient, "embed_texts", inference))
        stack.enter_context(patch.object(httpx.Client, "send", forbid_http))
        if mode == "refuse":
            for name in (
                "discover_delta", "discover_delta_page", "discover_changed",
                "discover", "list_paths", "current_commit",
            ):
                stack.enter_context(patch.object(GitRepoConnector, name, forbidden, create=True))
        config = IndexConfig(cluster_enabled=False)
        queue = TaskStore(data / "tasks.db")
        task = queue.enqueue(alias, full=mode == "full", trigger_source="fixture")
        claimed = queue.dequeue_next()
        assert claimed is not None and claimed.id == task.id
        assert run_worker(task.id, config=config) == 0
        finished = queue.get_task(task.id)
        assert finished is not None
        assert finished.result_stats is not None
        counts = finished.result_stats.get("work_counts")
        assert isinstance(counts, dict) and counts["cluster_pass_attempts"] == 0
        print("ALIAS-RESULT " + json.dumps({
            "status": finished.status, "failures": finished.result_stats.get("sources_failed", []),
        }), flush=True)


def _worker(data: Path, repo: Path, alias: str, mode: str) -> dict[str, Any]:
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).resolve()), "--worker",
         str(data), str(repo), alias, mode],
        capture_output=True, text=True, timeout=30, check=False,
        env=_fixture_environment(), **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    lines = [line.removeprefix("ALIAS-RESULT ") for line in result.stdout.splitlines()
             if line.startswith("ALIAS-RESULT ")]
    assert len(lines) == 1, result.stdout + result.stderr
    value = json.loads(lines[0])
    assert isinstance(value, dict)
    return value


@pytest.mark.timeout(120)
def test_native_stale_alias_refuses_reverted_file_omission_then_repairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    redirected = {
        "GIT_DIR": tmp_path / "unrelated.git",
        "GIT_WORK_TREE": tmp_path / "unrelated-worktree",
        "GIT_INDEX_FILE": tmp_path / "unrelated-index",
    }
    for name, target in redirected.items():
        monkeypatch.setenv(name, str(target))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "invalid-inherited-configuration")
    repo, data = tmp_path / "repo", tmp_path / "data"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    _git(repo, "config", "user.name", "Synthetic Fixture")
    _git(repo, "config", "user.email", "fixture@example.invalid")
    first, second = repo / "first.md", repo / "second.md"
    first.write_text(ORIGINAL, encoding="utf-8")
    second.write_text("# Needle\n\noriginal second document\n", encoding="utf-8")
    _git(repo, "add", "--", "first.md", "second.md")
    _git(repo, "commit", "--quiet", "-m", "fixture A")
    a = _git(repo, "rev-parse", "HEAD")
    assert _worker(data, repo, "git:left", "full")["status"] == "complete"
    assert _worker(data, repo, "git:right", "full")["status"] == "complete"
    config = IndexConfig(data_dir=data, cluster_enabled=False)
    state = IndexState.load(config.state_file)
    assert state.sources["git:left"].last_commit == state.sources["git:right"].last_commit == a

    first.write_text(MIDDLE, encoding="utf-8")
    _git(repo, "add", "--", "first.md")
    _git(repo, "commit", "--quiet", "-m", "fixture B")
    b = _git(repo, "rev-parse", "HEAD")
    assert _worker(data, repo, "git:left", "incremental")["status"] == "complete"
    state = IndexState.load(config.state_file)
    assert state.sources["git:left"].last_commit == state.completed_family_cursors[FAMILY] == b
    assert state.sources["git:right"].last_commit == a
    store = initialize_source_store(config)
    middle = store.content_store.get_chunks_for_file(FAMILY, "first.md")
    assert middle and any("temporary first document" in row.content for row in middle)
    middle_ids = [row.chunk_id for row in middle]
    vectors = store.get_vector_table("code").get_vectors(middle_ids)
    assert set(vectors) == set(middle_ids)
    before_versions = (
        store.content_store._get_or_create_table().version,
        store.get_vector_table("code")._get_or_create_table().version,
    )
    paths = PathIndex(data / "path_index.db")
    before_paths = paths.get_all_entries()
    before_state = config.state_file.read_bytes()

    first.write_text(ORIGINAL, encoding="utf-8")
    second.write_text(LAST, encoding="utf-8")
    _git(repo, "add", "--", "first.md", "second.md")
    _git(repo, "commit", "--quiet", "-m", "fixture C")
    c = _git(repo, "rev-parse", "HEAD")
    assert _git(repo, "diff", "--name-only", a, c) == "second.md"
    refused = _worker(data, repo, "git:right", "refuse")
    assert refused["status"] == "partial"
    assert len(refused["failures"]) == 1
    assert "family" in refused["failures"][0]["error"]
    assert "explicit full reindex required" in refused["failures"][0]["error"]
    assert config.state_file.read_bytes() == before_state
    assert PathIndex(data / "path_index.db").get_all_entries() == before_paths
    store = initialize_source_store(config)
    assert (
        store.content_store._get_or_create_table().version,
        store.get_vector_table("code")._get_or_create_table().version,
    ) == before_versions
    assert store.content_store.get_chunks_for_file(FAMILY, "first.md") == middle
    after_vectors = store.get_vector_table("code").get_vectors(middle_ids)
    assert after_vectors.keys() == vectors.keys()
    for identity, vector in vectors.items():
        np.testing.assert_array_equal(after_vectors[identity], vector)

    assert _worker(data, repo, "git:right", "full")["status"] == "complete"
    store = initialize_source_store(config)
    first_rows = store.content_store.get_chunks_for_file(FAMILY, "first.md")
    second_rows = store.content_store.get_chunks_for_file(FAMILY, "second.md")
    assert first_rows and any("original first document" in row.content for row in first_rows)
    assert not any("temporary first document" in row.content for row in first_rows)
    assert second_rows and any("latest second document" in row.content for row in second_rows)
    assert not store.get_vector_table("code").get_vectors(middle_ids)
    repaired_ids = [row.chunk_id for row in first_rows + second_rows]
    assert set(store.get_vector_table("code").get_vectors(repaired_ids)) == set(repaired_ids)
    state = IndexState.load(config.state_file)
    assert state.sources["git:right"].last_commit == state.completed_family_cursors[FAMILY] == c
    assert state.sources["git:left"].last_commit == b
    assert not any(target.exists() for target in redirected.values())


if __name__ == "__main__":
    assert len(sys.argv) == 6 and sys.argv[1] == "--worker"
    _child(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5])
