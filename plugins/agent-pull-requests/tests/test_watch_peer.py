"""The governed PR owner composes its validated immutable peer bootstrap."""

import json
import os
import subprocess
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs
from test_watch_installation import _installation
from test_watch_invocation import _install_slot, _probe
from test_watch_invocation import installed as installed
from test_watch_runtime import _advance_installation, _complete

from agent_pull_requests import __version__

ROOT = Path(__file__).resolve().parents[3]
_PREFIX = """
import json, sys
from pathlib import Path
from agent_pull_requests import __main__ as cli
from agent_pull_requests.watch_installation import WatchInstallation
from agent_pull_requests.watch_invocation import WatchInvocation, capture_running
installation = WatchInstallation.capture(
    receipt=Path(sys.argv[1]), plugin_root=Path(sys.argv[2]),
    payload_root=Path(sys.argv[3]), durable_home=Path(sys.argv[4]),
    os_profile=Path(sys.argv[5]), environment={},
)
runtime = capture_running(installation, environment={})
cli._WATCH_INVOCATION = WatchInvocation(installation, runtime, str(installation.receipt))
print(json.dumps(cli._agent_worktrees_prefix()))
"""


@pytest.fixture
def peer_slot(tmp_path):
    installation = _installation(tmp_path)
    source = ROOT / "plugins" / "agent-pull-requests" / "src" / "agent_pull_requests"
    package = Path("src") / "agent_pull_requests"
    files = {
        str(package / path.name): path.read_bytes()
        for path in source.glob("_installation_context*.py")
    }
    files[str(package / "_peer_launch.py")] = (
        ROOT / "libs" / "peer-launch" / "peer_launch.py"
    ).read_bytes()
    runtime = _complete(installation, version=__version__, snapshot_files=files)
    return _install_slot(installation, runtime)


@pytest.mark.timeout(90)
@pytest.mark.parametrize("receipt", ["same", "advanced"])
def test_cli_peer_prefix_uses_immutable_snapshot_not_management_payload(
    peer_slot, tmp_path, receipt,
):
    installation, runtime, python, library = peer_slot
    management = installation.payload_root / "scripts"
    management.mkdir()
    (management / "peer_launch.py").write_text(
        'raise AssertionError("replaceable management payload was executed")\n', encoding="utf-8",
    )
    if receipt == "advanced":
        installation = _advance_installation(installation, tmp_path)
    result = _probe((installation, runtime, python, library), script=_PREFIX)
    assert result.returncode == 0, result.stderr
    prefix = json.loads(result.stdout)
    assert prefix[:5] == [str(python), "-B", "-I", "-X", "utf8"]
    assert Path(prefix[5]) == (
        installation.plugin_root / "snapshots" / runtime.snapshot_id
        / "src" / "agent_pull_requests" / "_peer_launch.py"
    )
    assert prefix[6:] == [
        "agent-pull-requests", str(installation.plugin_root),
        str(installation.receipt), "agent-worktrees",
    ]
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {
            "PYTHONPATH", "PYTHONHOME", "COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
        }
    }
    if os.name == "nt":
        environment["USERPROFILE"] = str(installation.os_profile)
    refused = subprocess.run(  # noqa: S603 -- validated owned snapshot bootstrap; no peer installed
        [*prefix, "--help"], env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert refused.returncode == 126, refused.stderr
    assert "peer launch refused:" in refused.stderr
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()
    assert not list((
        installation.plugin_root / "snapshots" / runtime.snapshot_id
    ).rglob("__pycache__"))


@pytest.mark.timeout(90)
def test_missing_snapshot_peer_boundary_never_uses_management_or_ambient(installed):
    installation = installed[0]
    management = installation.payload_root / "scripts"
    management.mkdir()
    (management / "peer_launch.py").write_text(
        'raise AssertionError("replaceable management payload was executed")\n', encoding="utf-8",
    )
    result = _probe(installed, script=_PREFIX)
    assert result.returncode != 0
    assert "unavailable in its immutable snapshot" in result.stderr
    assert "replaceable management payload was executed" not in result.stderr
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()


@pytest.mark.timeout(90)
def test_changed_snapshot_peer_code_refuses_before_execution(peer_slot, tmp_path):
    installation, runtime, _, _ = peer_slot
    runtime.require_current(installation, environment={})
    marker = tmp_path / "must-not-execute"
    path = (
        installation.plugin_root / "snapshots" / runtime.snapshot_id
        / "src" / "agent_pull_requests" / "_peer_launch.py"
    )
    path.write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    result = _probe(peer_slot, script=_PREFIX)
    assert result.returncode != 0
    assert "snapshot" in result.stderr.lower()
    assert not marker.exists()
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()
