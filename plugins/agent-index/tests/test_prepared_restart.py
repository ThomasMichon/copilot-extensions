"""Opt-in real-process death/restart of subordinate prepared work."""

from __future__ import annotations

import json
import subprocess
import sys
from contextlib import closing

import pytest

from agent_index.index_config import IndexConfig
from agent_index.indexing.path_index import PathIndex
from agent_index.store.source_layout import LAYOUT

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]

DRIVER = r"""
import json
import os
import sys
from pathlib import Path

import numpy as np

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.engine import _index_source
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.state import IndexState
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import SourceDelta
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import initialize_source_store, selected_layout

root, mode = Path(sys.argv[1]), sys.argv[2]
profiles = {
    model: ModelProfile(model, "fixture", dim=2, table_name=f"vectors_{model}")
    for model in ("a", "b")
}
config = IndexConfig(data_dir=root, model_profiles=profiles, stream_batch_size=1)
store = initialize_source_store(config) if selected_layout() else MultiModelStore(config.lance_dir)
for profile in profiles.values():
    store.register_model(profile)
paths = PathIndex(root / "paths.db")
state = IndexState.load(config.state_file)

class Connector:
    source_name = "sample:repo"
    recovery_identity = ("fixture", "account")

    def discover_delta(self, last_commit, **kwargs):
        if mode == "resume":
            assert last_commit == "pinned", last_commit
            return SourceDelta(commit="newer")
        return SourceDelta(
            upserts=[
                FileEntry(f"{name}.md", f"Useful {name} text.", "markdown", self.source_name)
                for name in ("first", "second")
            ],
            commit="pinned",
        )

class Client:
    def __init__(self, model):
        self.model = model

    def embed_texts(self, texts):
        assert store._source_domain is None or not store._source_domain.writing
        print(json.dumps({"model": self.model, "texts": texts}), flush=True)
        if mode == "interrupt" and "second" in texts[0]:
            os._exit(73)
        assert mode != "resume" or "first" not in texts[0], "prepared pair was lost"
        return np.array([[len(text), 1] for text in texts], dtype=np.float32)

_index_source(
    "sample:repo", config=config, state=state, path_index=paths,
    multi_store=store, multi_clients={model: Client(model) for model in profiles},
    full=False, connector=Connector(),
)
print(json.dumps({
    "commit": IndexState.load(config.state_file).sources["sample:repo"].last_commit,
    "rows": store.source_counts(),
}), flush=True)
"""


@pytest.mark.parametrize("selected", [False, True])
def test_abrupt_preparation_death_reuses_committed_pair_bytes_in_a_fresh_process(
    tmp_path, monkeypatch, selected,
):
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT if selected else "")
    command = [sys.executable, "-c", DRIVER, str(tmp_path)]
    options = {
        "capture_output": True, "text": True, "timeout": 30,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    interrupted = subprocess.run([*command, "interrupt"], **options)
    assert interrupted.returncode == 73, interrupted.stderr
    before = [json.loads(line) for line in interrupted.stdout.splitlines() if line.startswith("{")]
    assert len(before) == 3
    assert not IndexConfig(data_dir=tmp_path).state_file.exists()
    paths = PathIndex(tmp_path / "paths.db")
    assert paths.get_all_entries() == {}
    with closing(paths._connect()) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM prepared_crawl_rows WHERE kind = 'vector'",
        ).fetchone()[0] == 2
    resumed = subprocess.run([*command, "resume"], **options)
    assert resumed.returncode == 0, resumed.stderr
    after = [json.loads(line) for line in resumed.stdout.splitlines() if line.startswith("{")]
    assert [row["model"] for row in after[:-1]] == ["a", "b"]
    assert all("second" in row["texts"][0] for row in after[:-1])
    assert after[-1] == {"commit": "newer", "rows": {"sample:repo": 2}}
    with closing(paths._connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM prepared_crawl_rows").fetchone()[0] == 0
