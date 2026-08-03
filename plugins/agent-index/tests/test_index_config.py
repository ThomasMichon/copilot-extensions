from __future__ import annotations

from agent_index.index_config import IndexConfig, _default_stream_batch_size


def test_stream_batch_size_gpu_default(monkeypatch) -> None:
    # #115: GPU hosts keep the high-throughput 500 batch.
    monkeypatch.delenv("AGENT_INDEX_STREAM_BATCH_SIZE", raising=False)
    monkeypatch.setenv("AGENT_INDEX_DEVICE", "cuda")
    assert _default_stream_batch_size() == 500


def test_stream_batch_size_cpu_default(monkeypatch) -> None:
    # #115: only the less-capable CPU path is downgraded to a small batch so a
    # /embed/batch completes within the read timeout instead of emptying the index.
    monkeypatch.delenv("AGENT_INDEX_STREAM_BATCH_SIZE", raising=False)
    monkeypatch.setenv("AGENT_INDEX_DEVICE", "cpu")
    assert _default_stream_batch_size() == 64


def test_stream_batch_size_explicit_override_wins(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_INDEX_STREAM_BATCH_SIZE", "128")
    monkeypatch.setenv("AGENT_INDEX_DEVICE", "cpu")
    assert _default_stream_batch_size() == 128


def test_config_stream_batch_size_is_device_aware(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_INDEX_STREAM_BATCH_SIZE", raising=False)
    monkeypatch.setenv("AGENT_INDEX_DEVICE", "cpu")
    assert IndexConfig().stream_batch_size == 64
    monkeypatch.setenv("AGENT_INDEX_DEVICE", "cuda")
    assert IndexConfig().stream_batch_size == 500
