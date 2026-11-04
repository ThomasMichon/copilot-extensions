"""Native producer proof for a stable, explicitly nonsecret feed identity."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time

import pytest

from runtime_profile import ProfileError
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


@pytest.mark.parametrize("change", ["credentials", "unselected-feed"])
def test_nonsecret_feed_is_checked_before_guarded_requests(tmp_path, monkeypatch, tls_source, change):  # noqa: F811
    import governed_feed_trust

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    original_identity = governed_feed_trust._opaque_index_identity
    _configure_native_tls(tmp_path, monkeypatch, state)
    home_key = "LOCALAPPDATA" if os.name == "nt" else "XDG_STATE_HOME"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(home_key, str(home))
    monkeypatch.setattr(governed_feed_trust, "_opaque_index_identity", original_identity)
    url = state["origin"] + "/simple"
    if change == "credentials":
        config = Path(os.environ["UV_CONFIG_FILE"])
        config.write_text(
            config.read_text(encoding="utf-8").replace('authenticate = "never"', 'authenticate = "auto"'),
            encoding="utf-8",
        )
        monkeypatch.setenv("UV_INDEX_FIXTURE_USERNAME", "fixture-user")
        monkeypatch.setenv("UV_INDEX_FIXTURE_PASSWORD", "fixture-value")
    else:
        url = "https://other.invalid/simple/"
    destination = tmp_path / "refused.zip"
    deadline = time.monotonic() + 180
    with pytest.raises(ProfileError, match="public feed"):
        producer._build_from_snapshot(
            tmp_path, {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40},
            json.dumps(manifest).encode(), wheels, _offline_target(producer, deadline),
            uv=uv, download_origins=(), materials={}, material_paths={}, destination=destination,
            deadline=deadline, max_bytes=4 << 20, guarded_feed=True, nonsecret_feed_url=url,
        )
    assert not destination.exists()
    count = len(state["requests"])
    assert count == 0, f"nonsecret preflight sent {count} feed requests"
