"""External ordinary acquisition resumes through the native shared engine."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs
from test_provider_acquisition import acquisition_receiver
from test_provider_delta import ENTRY, FAMILY

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.pending_crawl import PendingCrawls
from agent_index.indexing.state import IndexState
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import LAYOUT, initialize_source_store

pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem")]

DRIVER = r"""
import json
import sys
from pathlib import Path

import numpy as np

from agent_index.index_config import IndexConfig, ModelProfile
from agent_index.indexing.engine import _index_source
from agent_index.indexing.path_index import PathIndex
from agent_index.indexing.state import IndexState
from agent_index.sources.providers import CliSourceConnector, ProviderManifest
from agent_index.store.multi_model_store import MultiModelStore
from agent_index.store.source_layout import initialize_source_store, selected_layout

root = Path(sys.argv[1])
profiles = {
    name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
    for name in ("a", "b")[:int(sys.argv[2])]
}
config = IndexConfig(data_dir=root / "data", model_profiles=profiles, cluster_enabled=False)
config.ensure_dirs()
store = initialize_source_store(config) if selected_layout() else MultiModelStore(config.lance_dir)
for profile in profiles.values():
    store.register_model(profile)

class Client:
    def __init__(self, model):
        self.model_id = model

    def embed_texts(self, texts):
        print(json.dumps({"model": self.model_id, "texts": texts}), flush=True)
        return np.array([[len(text), 1] for text in texts], dtype=np.float32)

_index_source(
    "alias", config=config, state=IndexState.load(config.state_file),
    path_index=PathIndex(root / "paths.db"), multi_store=store,
    multi_clients={name: Client(name) for name in profiles}, full=False,
    connector=CliSourceConnector("forge", ProviderManifest(
        source_name="forge", command=(sys.executable, str(root / "provider.py")),
    )),
)
"""


@pytest.mark.parametrize("selected", [False, True], ids=["legacy", "source-aware"])
@pytest.mark.parametrize("models", [1, 2])
def test_external_pages_resume_after_restart_without_reacquiring_acknowledged_items(
    tmp_path, monkeypatch, selected, models,
):
    if selected:
        monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", LAYOUT)
    else:
        monkeypatch.delenv("AGENT_INDEX_STORE_LAYOUT", raising=False)
    receiver = acquisition_receiver(tmp_path)
    receiver.config["metadata"]["source_delta"]["acquisition_page_versions"] = []
    receiver.config["delta"] = {
        "version": 1, "commit": "base", "complete": True, "deletions": [],
        "upserts": [
            {**ENTRY, "path": "keep.md"},
            {**ENTRY, "path": "gone.md"},
            {**ENTRY, "source": FAMILY + ":commits", "path": "old-history"},
        ],
    }
    profiles = {
        name: ModelProfile(name, "fixture", dim=2, table_name=f"vectors_{name}")
        for name in ("a", "b")[:models]
    }
    config = IndexConfig(data_dir=tmp_path / "data", model_profiles=profiles, cluster_enabled=False)
    config.ensure_dirs()

    def open_store():
        store = initialize_source_store(config) if selected else MultiModelStore(config.lance_dir)
        for profile in profiles.values():
            store.register_model(profile)
        return store

    def run():
        receiver.save()
        return subprocess.run(
            [sys.executable, "-c", DRIVER, str(tmp_path), str(models)],
            capture_output=True, text=True, timeout=30, **no_window_kwargs(),
        )

    seeded = run()
    assert seeded.returncode == 0, seeded.stderr
    store = open_store()
    before = store.projection_snapshot()
    receiver.config["metadata"]["source_delta"]["acquisition_page_versions"] = [1]
    receiver.config["acquisition_pages"]["history"]["deletions"] = [
        {"source": FAMILY + ":commits", "path": "old-history"},
    ]
    receiver.config["fail_acquisition"] = True
    interrupted = run()
    assert interrupted.returncode == 1, interrupted.stderr
    assert "temporarily unavailable" in interrupted.stderr
    assert not any(line.startswith("{") for line in interrupted.stdout.splitlines())
    store = open_store()
    assert store.projection_snapshot() == before
    paths = PathIndex(tmp_path / "paths.db")
    assert paths.count() == 3
    assert IndexState.load(config.state_file).sources["alias"].last_commit == "base"
    assert PendingCrawls(paths).sources() == ["alias"]
    receiver.config["fail_acquisition"] = False
    receiver.config["metadata"]["commit"] = "later-informational-tip"
    resumed = run()
    assert resumed.returncode == 0, resumed.stderr
    requests = [json.loads(line) for line in resumed.stdout.splitlines() if line.startswith("{")]
    assert {request["model"] for request in requests} == set(profiles)
    store = open_store()
    paths = PathIndex(tmp_path / "paths.db")
    assert store.get_document(FAMILY, "new.md").content == ENTRY["content"]
    assert store.get_document(FAMILY + ":commits", "history") is not None
    assert store.get_document(FAMILY, "keep.md").content == ENTRY["content"]
    assert store.get_document(FAMILY, "gone.md") is None
    assert store.get_document(FAMILY + ":commits", "old-history") is None
    assert paths.count() == 3
    assert IndexState.load(config.state_file).sources["alias"].last_commit == "delta-tip"
    assert PendingCrawls(paths).sources() == []
    page_calls = [call for call in receiver.calls() if "--acquisition-page-version" in call]
    assert len(page_calls) == 3
    assert sum(not any(arg.startswith("--cursor=") for arg in call) for call in page_calls) == 1
    assert all("--last-commit=base" in call for call in page_calls)
