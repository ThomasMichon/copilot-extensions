"""Installer activation uses real completed targets without cold-start effects."""

import json
import os
import subprocess
from contextlib import ExitStack

import pytest
from agent_procutil import no_window_kwargs
from test_watch_control import _receiver
from test_watch_driver import _begin, _driver
from test_watch_installation import _policy
from test_watch_invocation import _PROBE, _probe, installed as installed  # noqa: F401

pytestmark = pytest.mark.timeout(180)

_ACTIVATE = _PROBE[:_PROBE.index("print(json.dumps({")] + """
from agent_pull_requests.watch_install import activate
from agent_pull_requests.watch_invocation import WatchInvocation
target = WatchInvocation(installation, runtime, str(installation.receipt))
print(json.dumps(activate(target)))
"""


def test_installer_cold_activation_selects_actual_target_without_starting(installed):
    installation, runtime, _, _ = installed
    result = _probe(installed, script=_ACTIVATE)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "status": "ready", "activated": True, "ownerStarted": False,
    }
    assert not installation.state_root.exists()
    assert not (installation.run_root / "active.json").exists()
    for name in ("current-version", "last-known-good"):
        assert (installation.plugin_root / name).read_text().strip() == runtime.runtime_version
    manifest = json.loads((installation.plugin_root / "deploy-manifest.json").read_text())
    assert manifest["source"]["version"] == runtime.payload_version
    assert manifest["venv"] == str(runtime.runtime_root)
    transaction = json.loads((installation.run_root / "watch-owner-transaction.json").read_text())
    assert transaction["phase"] == "complete" and transaction["successor"] is None
    assert (installation.run_root / "watch-owner-history" / f'{transaction["transactionId"]}.json').is_file()


def test_installer_refuses_actual_unattributed_writer_without_cold_selection(installed):
    installation = installed[0]
    with _receiver(installed, active=True):
        result = _probe(installed, script=_ACTIVATE)
        assert result.returncode != 0
        assert "AlreadyRunningError" in result.stderr
        assert not (installation.plugin_root / "current-version").exists()
        assert not (installation.run_root / "watch-owner-transaction.json").exists()


def test_installer_replay_keeps_actual_known_owner_and_terminal_custody(installed):
    installation = installed[0]
    with ExitStack() as children:
        with installation.transaction(timeout=1, environment={}) as guard:
            journal = _begin(installed, guard)
            driver = _driver(installed, journal, children)
            assert driver.run().ok
        before = (installation.plugin_root / "deploy-manifest.json").read_bytes()
        result = _probe(installed, script=_ACTIVATE)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["ownerStarted"] is False
        assert (installation.plugin_root / "deploy-manifest.json").read_bytes() == before
        assert driver.successor.health()["effectsEnabled"] is True
        current = json.loads((installation.run_root / "watch-owner-transaction.json").read_text())
        assert current["transactionId"] == journal.transaction_id
        assert current["successor"] == driver.successor.instance.evidence()


def test_installer_governance_refusal_never_selects_or_starts(installed):
    installation = installed[0]
    _policy(installation.os_profile, enabled=False)
    result = _probe(installed, script=_ACTIVATE)
    assert result.returncode != 0
    assert not (installation.plugin_root / "current-version").exists()
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()


@pytest.mark.skipif(os.name != "nt", reason="POSIX physical invocation needs isolated OS account")
@pytest.mark.parametrize("identity", ["exact", "snapshot", "version"])
def test_actual_installer_module_requires_exact_running_target(installed, identity):
    installation, runtime, python, _ = installed
    environment = dict(os.environ, USERPROFILE=str(installation.os_profile))
    result = subprocess.run(
        [
            str(python), "-I", "-m", "agent_pull_requests.watch_install",
            "--context", str(installation.receipt),
            "--payload-root", str(installation.payload_root),
            "--snapshot-id", runtime.snapshot_id if identity != "snapshot" else "foreign",
            "--runtime-version", runtime.runtime_version if identity != "version" else "foreign",
        ],
        env=environment, capture_output=True, text=True, timeout=60, **no_window_kwargs(),
    )
    if identity == "exact":
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["activated"] is True
    else:
        assert result.returncode != 0 and "actual running slot" in result.stderr
        assert not (installation.plugin_root / "current-version").exists()
    assert not installation.state_root.exists()
    assert not (installation.run_root / "active.json").exists()
