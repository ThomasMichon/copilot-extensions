"""Normal installer upgrade from a physically attested governed watch owner."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import time
from contextlib import ExitStack

import pytest
from agent_procutil import no_window_kwargs
from work_coalescing_singleton.client import request
from zdd import publication_history, routing

from agent_pull_requests import __version__, _installation_context as api
from agent_pull_requests.watch_contract import MERGED, PRSnapshot
from agent_pull_requests.watch_notification import event_payload
from agent_pull_requests.watch_registry import WatchKey, WatchRegistry
from agent_pull_requests.watch_storage import read_subscriptions_state, write_subscriptions_state
from test_cell_installer_slice import PAYLOAD
from test_watch_driver import _begin, _driver
from test_watch_invocation import installed as installed  # noqa: F401 -- pytest fixture


def _release_fixture(destination):
    spec = importlib.util.spec_from_file_location(
        "apr_upgrade_materializer", PAYLOAD / "scripts" / "cell_snapshot.py",
    )
    producer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(producer)
    producer.materialize(PAYLOAD, destination, api)
    version = "2.0.0"
    assert version != __version__
    project = destination / "pyproject.toml"
    project.write_text(re.sub(
        r'(?m)^version\s*=\s*"[^"]+"', f'version = "{version}"',
        project.read_text("utf-8"), count=1,
    ), encoding="utf-8")
    module = destination / "src" / "agent_pull_requests" / "__init__.py"
    original = module.read_text("utf-8")
    assert f'__version__ = "{__version__}"' in original
    module.write_text(
        original.replace(f'__version__ = "{__version__}"', f'__version__ = "{version}"'),
        encoding="utf-8",
    )
    manifest = destination / "plugin.json"
    document = json.loads(manifest.read_text("utf-8"))
    document["version"] = version
    manifest.write_text(json.dumps(document), encoding="utf-8")
    return version


def _advance(installation, payload, version):
    stamped = api.stamp_context(
        payload_root=payload, plugin_id="agent-pull-requests",
        durable_home=installation.durable_home,
        payload_version=version, payload_origin="explicit",
        source_descriptor={"source": "github", "repo": "example/one"},
        marketplace_key="one",
        expected_namespace_generation=installation.namespace_generation,
        expected_install_generation=installation.install_generation, environment={},
    )
    api.compare_and_swap_activation(
        context=installation.receipt, durable_home=installation.durable_home,
        expected_marketplace_id=installation.marketplace_id,
        expected_plugin_id="agent-pull-requests",
        expected_namespace_generation=stamped["namespaceGeneration"],
        expected_install_generation=stamped["generation"],
        expected_activation_generation=1, activation_mode="namespaced",
        activation_state="active", legacy_disposition="absent",
        legacy_probe={"declared": True, "result": "absent", "checkedAt": None},
        environment={}, os_profile=installation.os_profile,
    )
    return stamped


def _pending(root, python):
    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    registry.register(
        key, "upgrade-subscriber", until=(MERGED,), acknowledged=True,
        notify={"argv": [str(python), "-I", "-c", "raise SystemExit(1)"]},
    )
    registry.apply_snapshot(key, PRSnapshot(merged=True, pr_state="closed"))
    entries = registry.snapshot_state()
    entries[0]["pending"]["next_attempt"] = time.time() + 3600
    write_subscriptions_state(entries, root)
    return entries[0]


def _run(command, environment, timeout=120):
    return subprocess.run(
        command, env=environment, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=timeout, check=False, **no_window_kwargs(),
    )


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("process")
@pytest.mark.effect("network")
# This composes the measured native fixture (~44s), real package build (~96s),
# and the existing 120s cutover and 120s graceful-stop bounds.
@pytest.mark.timeout(420)
@pytest.mark.skipif(os.name != "nt", reason="Native POSIX upgrade requires an isolated OS account")
def test_normal_update_builds_different_target_and_retires_original_owner(
    installed, tmp_path,
):
    started = time.monotonic()
    installation, old_runtime, old_python, _ = installed
    payload = tmp_path / "release"
    version = _release_fixture(payload)
    saved = _pending(installation.state_root, old_python)
    with ExitStack() as children:
        with installation.transaction(timeout=1, environment={}) as guard:
            journal = _begin(installed, guard)
            driver = _driver(installed, journal, children)
            assert driver.run().ok
        old = driver.successor
        reference = old.capture_exit(timeout=30)
        children.callback(reference.close)
        original_runtime = old_runtime.evidence()
        before = routing.read_table(installation.run_root, strict=True)
        prior_archive = (
            installation.run_root / "watch-owner-history" / f"{journal.transaction_id}.json"
        ).read_bytes()
        stamped = _advance(installation, payload, version)
        environment = {
            key: value for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME", "COPILOT_EXTENSIONS_CONTEXT"}
        }
        environment.update(
            HOME=str(installation.os_profile), USERPROFILE=str(installation.os_profile),
            COPILOT_EXTENSIONS_CONTEXT=str(installation.receipt),
            AGENT_RT_ROOT=str(installation.plugin_root), COPILOT_PLUGIN_ROOT=str(payload),
        )
        print(f"upgrade fixture ready after {time.monotonic() - started:.2f}s", flush=True)
        try:
            result = _run([
                str(old_python), "-I", str(payload / "scripts" / "cell_install.py"),
                "update", "--context", str(installation.receipt),
                "--expected-marketplace-id", installation.marketplace_id,
                "--durable-home", str(installation.durable_home),
            ], environment, timeout=240)
            print(
                f"installer returned {result.returncode} after "
                f"{time.monotonic() - started:.2f}s\n{result.stdout}\n{result.stderr}",
                flush=True,
            )
            assert result.returncode == 0, result.stderr
            assert json.loads(result.stdout) == {
                "status": "ready", "activated": True, "ownerStarted": True,
            }
            assert reference.wait(0)
            assert old_runtime.evidence() == original_runtime
            after = routing.read_table(installation.run_root, strict=True)
            assert after["active"]["pid"] != old.instance.pid
            assert after["active"]["version"] == version
            assert after["active"]["generation"] > before["active"]["generation"]
            assert after["previous"] == before["active"]
            assert all(entry in after[publication_history.KEY]["entries"]
                       for entry in before[publication_history.KEY]["entries"])
            current = json.loads(
                (installation.run_root / "watch-owner-transaction.json").read_text()
            )
            assert current["phase"] == "complete"
            assert current["predecessor"] == old.instance.evidence()
            assert current["exited"] == ["predecessor"]
            assert current["installation"]["generation"] == stamped["generation"]
            assert current["installation"]["stateRoot"] == str(installation.state_root)
            assert current["installation"]["runRoot"] == str(installation.run_root)
            assert current["successor"]["runtimeVersion"] == version
            assert current["target"]["snapshotId"].startswith(version + "-")
            assert (
                installation.run_root / "watch-owner-history" / f"{journal.transaction_id}.json"
            ).read_bytes() == prior_archive
            endpoint = current["endpoints"]["successor"]
            # Read through the committed private endpoint after the installer
            # process and its construction job have both exited.
            response = request(
                endpoint["host"], endpoint["port"], endpoint["token"],
                kind="health", key="upgrade-proof", payload={}, request_deadline_s=30,
            )
            assert response["instance"] == current["successor"]
            assert all(response[field] is True
                       for field in ("writerHeld", "readReady", "effectsEnabled"))
            for name in ("current-version", "last-known-good"):
                assert (installation.plugin_root / name).read_text().strip() == version
            manifest = json.loads(
                (installation.plugin_root / "deploy-manifest.json").read_text()
            )
            assert manifest["source"]["version"] == version
            assert manifest["source"]["snapshot_id"] == current["target"]["snapshotId"]
            assert manifest["venv"] == str(installation.plugin_root / "versions" / version)
            recovered, = read_subscriptions_state(installation.state_root)
            assert recovered["registration_id"] == saved["registration_id"]
            assert recovered["pending"] == saved["pending"]
            restored = WatchRegistry()
            restored.restore_state([recovered], strict=True)
            assert event_payload(restored.pending_events()[0]) == saved["pending"]["payload"]
            assert not (installation.os_profile / ".agent-pull-requests").exists()
            assert not (installation.os_profile / ".local" / "bin").exists()
        finally:
            log = installation.run_root / "watch-owner.log"
            if log.is_file():
                print(log.read_text("utf-8"), flush=True)
            current = json.loads(
                (installation.run_root / "watch-owner-transaction.json").read_text()
            )
            print(
                f"upgrade cleanup after {time.monotonic() - started:.2f}s; "
                f"owner phase={current['phase']} exited={current['exited']}",
                flush=True,
            )
            cleanup = _run([
                str(old_python), "-I", "-c",
                "from agent_pull_requests.watch_invocation import WatchInvocation; "
                "from agent_pull_requests.watch_entry import stop; "
                "stop(WatchInvocation.capture(), timeout=120)",
            ], environment, timeout=150)
            assert cleanup.returncode == 0, cleanup.stderr
