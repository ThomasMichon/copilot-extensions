"""Released SDK adapters are self-contained in each extension's payload."""

from pathlib import Path
import shutil
import subprocess
import sys

from agent_procutil import no_window_kwargs
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


def test_budget_clean_room_source_candidate_keeps_its_declared_sdk_asset(tmp_path: Path) -> None:
    scenario = (
        _REPO / "tools" / "clean-room" / "scenarios" / "budget-guidance-solo" / "scenario.sh"
    ).read_text(encoding="utf-8")
    start = 'capture "copy-candidate" -- python3 -I - "$SOURCE" "$FRESH" <<\'PY\'\n'
    program = scenario.split(start, 1)[1].split("\nPY\n", 1)[0]
    fresh = tmp_path / "fresh"
    result = subprocess.run(
        [sys.executable, "-I", "-", str(_REPO), str(fresh)], input=program,
        text=True, capture_output=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    candidate = fresh / "plugins" / "budget-guidance"
    helper = candidate / "extensions" / "budget-guidance" / "sdk-compat.mjs"
    assert helper.read_bytes() == (
        _REPO / "libs" / "copilot-driver-core" / "src"
        / "agent_remote_driver" / "bridge_extension" / "sdk-compat.mjs"
    ).read_bytes()
    assert not (fresh / "libs" / "copilot-driver-core").exists()
    node = shutil.which("node")
    assert node, "clean-room extension controls require Node"
    result = subprocess.run(
        [node, "--test", str(candidate / "tests" / "host-quota.test.mjs")],
        cwd=candidate, text=True, capture_output=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
