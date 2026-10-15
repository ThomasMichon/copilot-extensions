"""The updater's installer runs are judged by the runtime receipt.

An installer that exits 0 without publishing the payload version in its
runtime's ``deploy-manifest.json`` is a failure for the registered-runtime
reconcile, the module update and agent-worktrees' own runtime installer.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from agent_worktrees import reconcile, update_events
from agent_worktrees import update_runtime as runtime

pytestmark = pytest.mark.installer_receipts


@pytest.fixture
def example(tmp_path, monkeypatch):
    plugin = tmp_path / "agent-worktrees"
    plugin.mkdir()
    (plugin / "modules.json").write_text(json.dumps({
        "modules": [{"name": "agent-example", "source": "agent-example"}],
    }))
    payload = tmp_path / "agent-example"
    (payload / "scripts").mkdir(parents=True)
    (payload / "scripts" / "install.sh").write_text("#!/bin/sh\n")
    (payload / "plugin.json").write_text(json.dumps({"name": "agent-example"}))
    environment = {k: v for k, v in os.environ.items() if k != "COPILOT_EXTENSIONS_CONTEXT"}
    deployed = {"version": "1.0.0"}
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: payload)
    monkeypatch.setattr(reconcile, "manifest_runtime_scope", lambda path: "universal")
    monkeypatch.setattr(reconcile, "payload_version", lambda path: "2.0.0")
    monkeypatch.setattr(reconcile, "runtime_deployed_version",
                        lambda *a, **k: deployed["version"])
    monkeypatch.setattr(reconcile, "runtime_installer_environment",
                        lambda *a, **k: (environment, tmp_path / "runtime"))
    monkeypatch.setattr(runtime.subprocess, "run",
                        lambda argv, **k: subprocess.CompletedProcess(argv, 0))
    return plugin, deployed


def test_runtime_reconcile_fails_an_exit_0_that_left_the_old_receipt(example):
    status = runtime._reconcile_one_runtime("agent-example", "linux", force=True)
    assert status == ("runtime not at target after a successful installer exit "
                      "(deployed 1.0.0, expected 2.0.0)")
    assert update_events.classify_status(status)[0] == "command_failed"


def test_runtime_reconcile_accepts_the_published_receipt(example, monkeypatch):
    _plugin, deployed = example

    def install(argv, **_k):
        deployed["version"] = "2.0.0"
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(runtime.subprocess, "run", install)
    assert runtime._reconcile_one_runtime("agent-example", "linux", force=True) == "OK"


def test_module_update_fails_an_exit_0_that_left_the_old_receipt(example):
    plugin, _deployed = example
    targets = {"agent-example": runtime._RegisteredPluginTarget(
        context=None, activation=runtime._PluginActivation.ACTIVE,
    )}
    assert not runtime._update_modules(plugin, "linux", None, force=True, targets=targets)


def test_engine_installer_fails_an_exit_0_that_left_the_old_receipt(
    example, tmp_path, monkeypatch,
):
    plugin, _deployed = example
    errors: list[str] = []
    monkeypatch.setattr(runtime.output, "err", errors.append)
    rc = runtime._run_engine_installer(
        ["bash", "install.sh", "update"], plugin, 60, dict(os.environ),
        runtime_root=tmp_path / "aw-runtime",
    )
    assert rc == 1
    # The installer's own output looked successful, so the reason must be shown.
    assert errors == ["agent-worktrees installer failed: runtime not at target after a "
                      "successful installer exit (deployed 1.0.0, expected 2.0.0)"]
