"""Released SDK adapters are self-contained in each extension's payload."""

from pathlib import Path

import pytest

import materialize_main as materializer

_REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("plugin", [
    "agent-bridge", "agent-remote-driver", "agent-worktrees", "budget-guidance", "context-handoff",
])
def test_extension_payload_carries_its_canonical_sdk_adapter(tmp_path: Path, plugin: str) -> None:
    destination = tmp_path / "release" / "plugins" / plugin
    destination.mkdir(parents=True)
    report = materializer.materialize_launch_wrapper_assets_into(
        source_consumer_dir=_REPO / "plugins" / plugin,
        dest_consumer_dir=destination, canonical_root=_REPO, dest_root=tmp_path / "release",
    )
    assert report and all(line.startswith("OK") for line in report), report
    canonical = (
        _REPO / "libs" / "copilot-driver-core" / "src"
        / "agent_remote_driver" / "bridge_extension" / "sdk-compat.mjs"
    ).read_bytes()
    extension = destination / "extensions" / plugin
    helper = extension / "core" / "sdk-compat.mjs" if plugin == "agent-bridge" else extension / "sdk-compat.mjs"
    assert helper.read_bytes() == canonical
    assert not (tmp_path / "release" / "libs").exists()
    if plugin == "agent-bridge":
        receiver = destination / "src" / "agent_bridge" / "receiving_assets" / "plugins"
        assert (
            receiver / "agent-remote-driver" / "extensions" / "agent-remote-driver" / "sdk-compat.mjs"
        ).read_bytes() == canonical
