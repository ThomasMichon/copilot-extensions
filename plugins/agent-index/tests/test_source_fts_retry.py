"""Selected FTS conflicts cross an actual child boundary without exposing payloads.

Only store retry sleeps are recorded; shared native-library clocks stay real.
"""

import subprocess
import time
from types import SimpleNamespace

import numpy as np
import pytest
from test_source_aware_store import chunk, cold as cold

from agent_index.store import content_store as content_module
from agent_index.store.source_layout import SourceLayoutError, validate_source_store


@pytest.mark.timeout(120)
@pytest.mark.parametrize("existing", [False, True], ids=["first", "incremental"])
@pytest.mark.parametrize("exhausted", [False, True], ids=["retry-success", "exhausted"])
def test_selected_fts_real_child_conflict_retry(cold, monkeypatch, caplog, existing, exhausted):
    _config, store = cold
    store.upsert("small", [chunk()], np.array([[1, 0]], dtype=np.float32))
    if existing:
        assert store.ensure_fts_index()
        store.upsert("small", [chunk(text="needle new content", path="new.md")],
                     np.array([[0, 1]], dtype=np.float32))
    content = store.content_store
    native_run = subprocess.run
    native_sleep = time.sleep
    attempts, delays, diagnostics = [], [], []

    def actual_child(argv, **kwargs):
        time.sleep(0.001)
        args = list(argv)
        assert "child_fts_owner" in args[4]
        attempts.append(args[-1])
        if exhausted or len(attempts) == 1:
            args[4] = (
                "import sys; from agent_index.store.source_layout import child_fts_owner;"
                "\nwith child_fts_owner(sys.argv[1],sys.argv[3]):"
                "\n raise RuntimeError('Retryable commit conflict; SYNTHETIC_BODY_MUST_NOT_ESCAPE')"
            )
        result = native_run(args, **kwargs)
        if result.returncode:
            diagnostics.append(result.stderr)
        return result

    monkeypatch.setattr(content_module.subprocess, "run", actual_child)
    monkeypatch.setattr(content_module, "time", SimpleNamespace(
        time=time.time, monotonic=time.monotonic,
        sleep=lambda delay: delays.append(delay),
    ))
    assert time.sleep is native_sleep
    if exhausted:
        with pytest.raises(SourceLayoutError, match="FTS preparation failed") as failure:
            content.ensure_fts_index(max_retries=2)
        assert "SYNTHETIC_BODY" not in str(failure.value)
        receipt = store._source_domain.receipt()
        assert receipt["state"] == "dirty" and receipt["uncertain_effect"] is True
        assert "fts_pending" not in receipt
        with pytest.raises(SourceLayoutError, match="uncertain effect"):
            validate_source_store(store)
        with pytest.raises(SourceLayoutError):
            store.stats()
    else:
        assert content.ensure_fts_index(max_retries=2)
        receipt = store._source_domain.receipt()
        assert receipt["state"] == "clean" and not receipt.get("uncertain_effect")
        assert receipt["fts_coverage"]["chunks"] == receipt["versions"]["chunks"]
        assert content.fts_search("needle")
    assert attempts == (["0", "0"] if existing else ["1", "1"])
    assert len(delays) == 1 and delays[0] >= 2
    assert diagnostics and "Retryable commit conflict" in diagnostics[0]
    assert "SYNTHETIC_BODY" not in caplog.text
