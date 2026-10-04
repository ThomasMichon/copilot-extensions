from __future__ import annotations

from pathlib import Path

from agent_index.index_config import IndexConfig, _default_data_dir, _default_stream_batch_size


def test_data_dir_explicit_override_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(tmp_path / "explicit"))
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    assert _default_data_dir() == tmp_path / "explicit"


def test_data_dir_state_dir_override_wins_over_home(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENT_INDEX_DATA_DIR", raising=False)
    monkeypatch.setenv("AGENT_INDEX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    assert _default_data_dir() == tmp_path / "state"


def test_data_dir_falls_back_to_agent_index_home(monkeypatch, tmp_path) -> None:
    """Regression test for a real production bug: ``IndexConfig.data_dir``'s
    default used to skip ``AGENT_INDEX_HOME`` entirely and go straight to the
    hardcoded ``~/.agent-index/data``, unlike every OTHER "where does
    agent-index keep its stuff" resolver in this package (``_default_backup_dir``
    in this same module, and ``agent_index.config``'s own ``data_dir()``/
    ``install_dir()``), all of which DO honor it. This silently broke test
    isolation (a test setting AGENT_INDEX_HOME to a tmp_path sandbox, expecting
    IndexConfig().data_dir to follow, actually kept writing to the REAL
    production data directory) and would equally silently ignore an operator's
    AGENT_INDEX_HOME override meant to relocate the whole data store."""
    monkeypatch.delenv("AGENT_INDEX_DATA_DIR", raising=False)
    monkeypatch.delenv("AGENT_INDEX_STATE_DIR", raising=False)
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    assert _default_data_dir() == tmp_path / "home" / "data"


def test_config_data_dir_follows_agent_index_home(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENT_INDEX_DATA_DIR", raising=False)
    monkeypatch.delenv("AGENT_INDEX_STATE_DIR", raising=False)
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "home"))
    assert IndexConfig().data_dir == tmp_path / "home" / "data"


def test_data_dir_hardcoded_default_when_nothing_set(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_INDEX_DATA_DIR", raising=False)
    monkeypatch.delenv("AGENT_INDEX_STATE_DIR", raising=False)
    monkeypatch.delenv("AGENT_INDEX_HOME", raising=False)
    assert _default_data_dir() == Path("~/.agent-index/data").expanduser()


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


def test_stream_batch_size_unset_env_follows_recorded_cpu_device(monkeypatch) -> None:
    # Regression (#1452): with AGENT_INDEX_DEVICE unset, the batch size must
    # follow the RESOLVED device (env -> recorded machine_device() -> cuda), not
    # a bare "cuda" env default. A CPU host whose device env is unset previously
    # got the 500 GPU batch, so its CPU /embed/batch calls exceeded the read
    # timeout and whole sources failed. It must resolve to the small CPU batch.
    monkeypatch.delenv("AGENT_INDEX_STREAM_BATCH_SIZE", raising=False)
    monkeypatch.delenv("AGENT_INDEX_DEVICE", raising=False)
    monkeypatch.setattr("agent_index.config.machine_device", lambda: "cpu")
    assert _default_stream_batch_size() == 64


def test_stream_batch_size_unset_env_follows_recorded_cuda_device(monkeypatch) -> None:
    # The mirror of the regression: a GPU host with the env unset keeps 500.
    monkeypatch.delenv("AGENT_INDEX_STREAM_BATCH_SIZE", raising=False)
    monkeypatch.delenv("AGENT_INDEX_DEVICE", raising=False)
    monkeypatch.setattr("agent_index.config.machine_device", lambda: "cuda")
    assert _default_stream_batch_size() == 500


def test_config_stream_batch_size_is_device_aware(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_INDEX_STREAM_BATCH_SIZE", raising=False)
    monkeypatch.setenv("AGENT_INDEX_DEVICE", "cpu")
    assert IndexConfig().stream_batch_size == 64
    monkeypatch.setenv("AGENT_INDEX_DEVICE", "cuda")
    assert IndexConfig().stream_batch_size == 500
