"""Native producer proof for a stable, explicitly nonsecret feed identity."""
from __future__ import annotations

import json
import os
import shutil
import time

import pytest

from test_core_native_delivery import (
    _configure_native_tls,
    _offline_target,
    local_tls_feed,  # noqa: F401
    tls_source,  # noqa: F401
)


def test_guarded_public_feed_produces_the_same_candidate_in_fresh_homes(
    tmp_path, monkeypatch, tls_source,  # noqa: F811
):
    import governed_feed_trust

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    original_identity = governed_feed_trust._opaque_index_identity
    _configure_native_tls(tmp_path, monkeypatch, state)
    home_key = "LOCALAPPDATA" if os.name == "nt" else "XDG_STATE_HOME"
    monkeypatch.setattr(governed_feed_trust, "_opaque_index_identity", original_identity)
    results, local_identities = [], []
    for index in (1, 2):
        home = tmp_path / f"home-{index}"
        home.mkdir()
        monkeypatch.setenv(home_key, str(home))
        local_identities.append(original_identity("nonsecret-test-input"))
        source = {
            "source_commit": str(index) * 40, "source_tree": str(index + 2) * 40,
            "main_head": str(index + 4) * 40,
        }
        deadline = time.monotonic() + 180
        destination = tmp_path / f"candidate-{index}.zip"
        result = producer._build_from_snapshot(
            tmp_path, source, json.dumps(manifest).encode(), wheels,
            _offline_target(producer, deadline), uv=uv, download_origins=(),
            materials={}, material_paths={}, destination=destination, deadline=deadline,
            max_bytes=4 << 20, guarded_feed=True, nonsecret_feed_url=state["origin"] + "/simple",
        )
        results.append((result, destination.read_bytes()))
    assert local_identities[0] != local_identities[1]
    assert results[0][0]["source_commit"] != results[1][0]["source_commit"]
    assert results[0][0]["profile_sha256"] == results[1][0]["profile_sha256"]
    assert results[0][0]["sha256"] == results[1][0]["sha256"]
    assert results[0][1] == results[1][1]
    assert results[0][0]["approval"] == "unattested"
