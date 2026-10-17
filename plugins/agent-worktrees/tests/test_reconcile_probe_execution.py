"""The fast probe retains the owning payload's authority and native verdicts."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent_worktrees import reconcile, update_cli
from test_reconcile import env as env


def test_probe_prefers_owned_portable_helper(tmp_path):
    helpers = tmp_path / "scripts" / "installation-context"
    helpers.mkdir(parents=True)
    portable = helpers / "installation_context.py"
    native = helpers / "installation-context.ps1"
    portable.touch()
    native.touch()
    assert reconcile._installation_context_helper("agent-worktrees", tmp_path) == portable
    if os.name == "nt":
        portable.unlink()
        assert reconcile._installation_context_helper("agent-worktrees", tmp_path) == native
        portable.touch()
        native.unlink()
    else:
        portable.unlink()
    with pytest.raises(ValueError, match="platform installation-context helper is missing"):
        reconcile._installation_context_helper("agent-worktrees", tmp_path)
    for helper in helpers.iterdir():
        helper.unlink()
    assert reconcile._installation_context_helper("agent-worktrees", tmp_path) is None


def test_probe_uses_isolated_current_python_without_native_shell(tmp_path, monkeypatch):
    helper = tmp_path / "installation_context.py"
    context = tmp_path / "install.json"
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 15
        assert kwargs["encoding"] == "utf-8"
        assert not set(reconcile._RUNTIME_ENV_UNSET) & kwargs["env"].keys()
        return subprocess.CompletedProcess(command, 0, '{"allowMutation":true}', "")

    monkeypatch.setattr(reconcile.subprocess, "run", run)
    monkeypatch.setattr(reconcile.shutil, "which", lambda name: pytest.fail(name))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "foreign"))
    reconcile._run_installation_mode_helper(
        "agent-worktrees", tmp_path, helper, tmp_path / "legacy", context=context,
    )
    assert calls[0][:6] == [
        sys.executable, "-I", "-X", "utf8", str(helper), "probe-legacy",
    ]
    assert calls[0][-2:] == ["--context", str(context)]


def test_prelaunch_omits_only_foreign_pr_overlays(tmp_path, monkeypatch):
    from types import SimpleNamespace

    calls = []

    def config(*, include_control_plane_related_pr):
        calls.append(include_control_plane_related_pr)
        assert include_control_plane_related_pr is False
        return SimpleNamespace(default_repo=SimpleNamespace(service_paths=None))

    monkeypatch.setattr(update_cli.worktree_identity, "_find_repo_dir", lambda: tmp_path)
    monkeypatch.setattr(update_cli.cfg, "load_config", config)
    monkeypatch.setattr(update_cli, "_resolve_environment", lambda config: "test")
    monkeypatch.setattr(update_cli.svc, "discover_services", lambda *args, **kwargs: [])
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: None)
    monkeypatch.setattr(reconcile, "_explicit_context_target", lambda: None)
    assert update_cli.plan_pre_launch() == {"action": "continue"}
    assert calls == [False]


@pytest.mark.skipif(os.name != "nt", reason="native Windows adapter parity")
@pytest.mark.parametrize(
    "scenario",
    ["legacy", "unicode-legacy", "active", "foreign-environment",
     "stale-generation", "invalid-policy"],
)
def test_windows_portable_probe_matches_native_authority(env, monkeypatch, scenario):
    payload = env.install_payload("agent-worktrees", "2.0.0", scope="universal")
    if scenario == "unicode-legacy":
        unicode_root = env.home / "caf\u00e9-\u6d4b\u8bd5"
        unicode_root.mkdir()
        relocated = unicode_root / "payload"
        payload.rename(relocated)
        payload = relocated
        env.home = unicode_root
        monkeypatch.setattr(reconcile, "_home", lambda: env.home)
        monkeypatch.setattr(reconcile.subprocess, "_text_encoding", lambda: "cp1252")
        monkeypatch.setenv("PYTHONUTF8", "0")
        monkeypatch.setenv("PYTHONIOENCODING", "ascii")
    monkeypatch.setenv("USERPROFILE", str(env.home))
    monkeypatch.setenv("HOME", str(env.home))
    monkeypatch.setenv("DOTNET_MultiCoreJitNoProfileGather", "1")
    monkeypatch.setenv("COMPlus_MultiCoreJitNoProfileGather", "1")
    monkeypatch.setenv("POWERSHELL_TELEMETRY_OPTOUT", "1")
    (env.home / "AppData" / "Local" / "Microsoft" / "PowerShell").mkdir(parents=True)
    legacy = env.home / ".agent-worktrees"
    legacy.mkdir()
    context = None
    if scenario in {"active", "foreign-environment", "stale-generation"}:
        context, root = env.select_context("agent-worktrees", payload, "2.0.0", "2.0.0")
        receipt = json.loads(context.read_text(encoding="utf-8"))
        namespace = json.loads(Path(receipt["namespaceReceipt"]).read_text(encoding="utf-8"))
        activation = {
            "schema": "copilot-extensions.installation-activation",
            "version": 1,
            "marketplaceId": reconcile.CORE_MARKETPLACE_ID,
            "pluginId": "agent-worktrees",
            "mode": "namespaced",
            "state": "active",
            "environment": {
                "platform": "windows",
                "homeRealPath": str(
                    env.home if scenario != "foreign-environment" else env.home / "foreign"
                ),
                "wslDistro": None,
            },
            "context": str(context),
            "namespaceGeneration": namespace["generation"],
            "installGeneration": receipt["generation"] + (scenario == "stale-generation"),
            "generation": 1,
            "legacy": {
                "disposition": "absent",
                "probe": {
                    "declared": True, "result": "absent",
                    "checkedAt": "2026-01-01T00:00:00Z",
                },
            },
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        (root / "installation-activation.json").write_text(
            json.dumps(activation), encoding="utf-8",
        )
    policy = env.home / ".copilot-extensions" / "installation-mode.json"
    if scenario not in {"legacy", "unicode-legacy"}:
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text(
            "invalid json" if scenario == "invalid-policy" else json.dumps({
                "schema": "copilot-extensions.installation-mode",
                "version": 1, "installationMode": {"enabled": True},
            }),
            encoding="utf-8",
        )

    def snapshot():
        return {
            path.relative_to(env.home).as_posix(): (
                path.is_dir(), path.stat().st_mtime_ns,
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
            )
            for path in env.home.rglob("*")
        }

    helper = reconcile._installation_context_helper("agent-worktrees", payload)
    assert helper is not None and helper.suffix == ".py"
    before = snapshot()
    portable = reconcile._run_installation_mode_helper(
        "agent-worktrees", payload, helper, legacy, context=context,
    )
    assert snapshot() == before
    native = reconcile._run_installation_mode_helper(
        "agent-worktrees", payload, helper.with_name("installation-context.ps1"),
        legacy, context=context,
    )
    assert snapshot() == before
    if scenario == "stale-generation":
        # The adapters diagnose the same refusal at different probe stages.
        assert portable["probeReason"] == "namespaced-active"
        assert native["probeReason"] == "revalidation-required"
        assert {**portable, "probeReason": native["probeReason"]} == native
    else:
        assert portable == native
    expected_status = {
        "legacy": "ready", "unicode-legacy": "ready", "active": "ready",
        "foreign-environment": "foreign-environment",
        "stale-generation": "revalidation-required", "invalid-policy": "invalid",
    }[scenario]
    assert portable["status"] == expected_status
    assert portable["allowMutation"] is (scenario in {"legacy", "unicode-legacy"})
    expected_mode = (
        None if scenario == "foreign-environment"
        else "namespaced" if context else "legacy"
    )
    assert portable["actualMode"] == expected_mode
    if scenario in {"foreign-environment", "stale-generation", "invalid-policy"}:
        for result in (portable, native):
            with pytest.raises(ValueError, match=f"status={expected_status}, reason="):
                reconcile._select_runtime_installation(
                    "agent-worktrees", result, home=env.home,
                )
    else:
        assert reconcile._select_runtime_installation(
            "agent-worktrees", portable, home=env.home,
        ) == reconcile._select_runtime_installation(
            "agent-worktrees", native, home=env.home,
        )
