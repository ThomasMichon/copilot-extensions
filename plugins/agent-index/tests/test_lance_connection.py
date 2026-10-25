from datetime import timedelta
import sys
from types import SimpleNamespace

import pytest

from agent_index.store.lance_connection import connect_content_store


@pytest.mark.parametrize("source_aware", [False, True])
def test_connection_policy_preserves_source_aware_fence(tmp_path, monkeypatch, source_aware):
    calls = []
    marker = object()

    def connect(path, **options):
        calls.append((path, options))
        return marker

    monkeypatch.setitem(sys.modules, "lancedb", SimpleNamespace(connect=connect))
    assert connect_content_store(tmp_path, source_aware=source_aware) is marker
    assert calls == [(str(tmp_path), {} if source_aware else {"read_consistency_interval": timedelta(0)})]
