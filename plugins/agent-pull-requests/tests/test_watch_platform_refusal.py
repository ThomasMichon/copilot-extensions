"""Unsupported native ownership evidence never licenses governed watch effects."""

import json
import sys

import pytest
from test_watch_invocation import _probe
from test_watch_invocation import installed as installed

_PLATFORM = """
import json, os, sys, time
from pathlib import Path
from uuid import uuid4
from zdd import diagnostics, process_exit
from agent_pull_requests import _installation_context as api, watch_entry, watch_install
from agent_pull_requests.watch_installation import WatchInstallation
from agent_pull_requests.watch_invocation import WatchInvocation, capture_running
from agent_pull_requests.watch_runtime import WatchInstance
installation = WatchInstallation.capture(
    receipt=Path(sys.argv[1]), plugin_root=Path(sys.argv[2]),
    payload_root=Path(sys.argv[3]), durable_home=Path(sys.argv[4]),
    os_profile=Path(sys.argv[5]), environment={},
)
runtime = capture_running(installation, environment={})
invocation = WatchInvocation(installation, runtime, str(installation.receipt))
SIMULATION
def forbidden(*_args, **_kwargs):
    raise AssertionError("unavailable native evidence reached control, signal or spawn")
watch_entry.spawn_daemon = forbidden
watch_entry.WatchControlClient.control = forbidden
os.kill = forbidden
try:
    watch_install.activate(invocation, timeout=30)
except NotImplementedError as error:
    assert "native process birth evidence" in str(error)
else:
    raise AssertionError("unsupported controller identity authorized activation")
try:
    invocation.instance()
except ProcessLookupError as error:
    assert "native birth evidence" in str(error)
else:
    raise AssertionError("unsupported native birth authorized an owner")
started = time.monotonic()
assert watch_entry.stop(invocation, timeout=10) is False
assert time.monotonic() - started < 10
candidate = WatchInstance(runtime, os.getpid(), "1", str(uuid4()))
try:
    candidate.capture_exit(installation, read_health=forbidden, environment={})
except NotImplementedError:
    pass
else:
    raise AssertionError("unsupported native cleanup returned a process reference")
try:
    process_exit.observe_original_exit(os.getpid(), "1")
except NotImplementedError:
    pass
else:
    raise AssertionError("unsupported recovery guessed that the original exited")
for root, names in (
    (installation.plugin_root, (
        api.CURRENT_VERSION_FILE, api.LAST_KNOWN_GOOD_FILE, "deploy-manifest.json",
    )),
    (installation.run_root, ("watch-owner-transaction.json", "active.json")),
):
    assert all(not (root / name).exists() for name in names)
assert not installation.state_root.exists()
print(json.dumps({"startup": "refused", "idleStop": "bounded", "cleanup": "refused",
                  "exitObservation": "refused", "effects": False}))
"""

_SIMULATION = """
def unavailable(*_args, **_kwargs):
    raise NotImplementedError("fixture-owned unsupported native exit evidence")
diagnostics.process_start_time = lambda _pid: None
process_exit.open_process = unavailable
process_exit.observe_original_exit = unavailable
"""


def _assert_refusal(result):
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "startup": "refused", "idleStop": "bounded", "cleanup": "refused",
        "exitObservation": "refused", "effects": False,
    }


@pytest.mark.timeout(90)
def test_unsupported_native_watch_evidence_refuses_before_control(installed):
    _assert_refusal(_probe(installed, script=_PLATFORM.replace("SIMULATION", _SIMULATION)))


@pytest.mark.portfolio_tier("T3")
@pytest.mark.timeout(90)
@pytest.mark.skipif(sys.platform != "darwin", reason="requires the native macOS kernel")
def test_native_macos_governed_watch_refuses_unprovable_identity(installed):
    _assert_refusal(_probe(
        installed, script=_PLATFORM.replace("SIMULATION", "assert sys.platform == 'darwin'"),
    ))
