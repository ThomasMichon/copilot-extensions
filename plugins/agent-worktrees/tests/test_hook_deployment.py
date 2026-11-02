"""Session fallback never installs and retains independent non-install hooks."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def test_legacy_session_never_enters_installation_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "deployment_hook_client", PLUGIN / "scripts/hook_client.py",
    )
    assert spec and spec.loader
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setattr(client, "__file__", str(scripts / "hook_client.py"))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    suffix = "ps1" if os.name == "nt" else "sh"
    if not shutil.which("pwsh" if os.name == "nt" else "bash"):
        pytest.skip("Native hook shell unavailable")
    refusal = (
        "[Console]::Error.WriteLine('deployment-managed: use explicit installation management')\n"
        "[Console]::Out.Write('{}')\nexit 3\n"
        if os.name == "nt" else
        "printf '%s\\n' 'deployment-managed: use explicit installation management' >&2\n"
        "printf '{}'\nexit 3\n"
    )
    (scripts / f"bootstrap-check.{suffix}").write_text(refusal, encoding="utf-8")
    retained = ("project-hooks", "register-nudge", "register-session", "anchor-hygiene-check")
    for name in retained:
        marker = tmp_path / name
        body = (
            f"[IO.File]::WriteAllText('{marker}', 'ran')\n"
            f"Write-Output '{{\"additionalContext\":\"{name}\"}}'\n"
            if os.name == "nt" else
            f"printf ran > '{marker}'\n"
            f"printf '%s' '{{\"additionalContext\":\"{name}\"}}'\n"
        )
        (scripts / f"{name}.{suffix}").write_text(body, encoding="utf-8")
    # Only the test's containment changes: production sibling selection,
    # concurrency, refusal handling and response folding execute unchanged.
    popen = subprocess.Popen

    def contained_popen(*args, **kwargs):
        if os.name == "nt":
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | subprocess.CREATE_NO_WINDOW
        return popen(*args, **kwargs)

    monkeypatch.setattr(client, "subprocess", SimpleNamespace(
        Popen=contained_popen, PIPE=subprocess.PIPE, TimeoutExpired=subprocess.TimeoutExpired,
        CREATE_NEW_PROCESS_GROUP=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    ))
    result = client._fallback_legacy_session_start({"sessionId": "retained-hooks"})
    assert {name for name in retained if (tmp_path / name).read_text() == "ran"} == set(retained)
    assert result["additionalContext"] == "anchor-hygiene-check"
    assert not result.get("_stderr")


@pytest.mark.parametrize("held", [False, True])
@pytest.mark.parametrize("runtime_state", ["missing", "stale"])
@pytest.mark.parametrize("status_state", ["absent", "failed", "unreadable"])
def test_native_fallback_preserves_guidance_without_installing(
    tmp_path, held, runtime_state, status_state,
):
    if os.name != "nt":
        pytest.skip("native Windows fallback and legacy hook consumers")
    home = tmp_path / "home"
    (home / "AppData/Roaming").mkdir(parents=True)
    scripts = tmp_path / "payload/scripts"
    scripts.mkdir(parents=True)
    for name in ("hook_client.py", "hook_runtime.py", "hook_lifecycle_runtime.py", "provision-check.ps1"):
        shutil.copyfile(PLUGIN / "scripts" / name, scripts / name)
    runtime = home / ".agent-worktrees"
    if runtime_state == "stale":
        slot = runtime / "versions/0.0.1"
        interpreter = slot / "Scripts/python.exe"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b"fixture interpreter must never execute")
        (slot / ".install-complete.json").write_text('{"version":"0.0.1"}')
        (runtime / "current-version").write_text("0.0.1")
        (runtime / "deploy-manifest.json").write_text('{"source":{"version":"0.0.1"}}')
        (runtime / "reconcile.lock").write_text(str(os.getpid()))
    marker = tmp_path / "installer-called"
    quoted_marker = str(marker).replace("'", "''")
    trap = f"[IO.File]::WriteAllText('{quoted_marker}', 'called')\nexit 97\n"
    for name in ("bootstrap-check.ps1", "install.ps1"):
        (scripts / name).write_text(trap, encoding="utf-8")
    (scripts / "command_binding.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('called')\n",
        encoding="utf-8",
    )
    retained = ("project-hooks", "register-nudge", "register-session", "anchor-hygiene-check")
    for name in retained:
        target = str(tmp_path / name).replace("'", "''")
        (scripts / f"{name}.ps1").write_text(
            "if ($env:WORKTREE_NO_RECONCILE -ne 'caller-reconcile' -or "
            "$env:WORKTREE_NO_PROVISION -ne 'caller-provision') { throw 'caller environment changed' }\n"
            f"[IO.File]::WriteAllText('{target}', 'called')\n"
            f"Write-Output '{{\"additionalContext\":\"{name}\"}}'\n",
            encoding="utf-8",
        )
    hold = home / "hold.json"
    hold.write_text(json.dumps({"active": held}), encoding="utf-8")
    hold_bytes = hold.read_bytes()
    if status_state != "absent":
        status = runtime / "logs/provision-status.json"
        status.parent.mkdir(parents=True, exist_ok=True)
        status.write_text(
            "{" if status_state == "unreadable" else json.dumps({
                "ok": False, "repo": str(tmp_path), "failed": [{"service": "fixture-service"}],
            }), encoding="utf-8",
        )
    before = {str(path.relative_to(runtime)): path.read_bytes()
              for path in runtime.rglob("*") if path.is_file()}
    environment = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
                   "BOOTSTRAP_KILLSWITCH_STATE_FILE": str(hold),
                   "WORKTREE_NO_RECONCILE": "caller-reconcile",
                   "WORKTREE_NO_PROVISION": "caller-provision"}
    environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    code = """
import importlib.util, json, pathlib, sys
scripts, home = map(pathlib.Path, sys.argv[1:3])
spec = importlib.util.spec_from_file_location('fixture_hook_client', scripts / 'hook_client.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)
payload = {
    'sessionId': 'fixture-fallback', 'cwd': str(pathlib.Path.cwd()), 'source': 'cli', 'timestamp': 1,
    '_agentWorktrees': {
        'pluginVersion': '9.0.0',
        'commandCatalog': '## agent-worktrees session command catalog\\n\\nFixture connector guidance.',
    },
}
result = client._fallback_session_start(payload, home)
assert client._write_session_guidance(
    payload, home=home, decision_context=result.get('additionalContext', ''),
)
print(json.dumps(result))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(scripts), str(home)],
        env=environment, cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
        timeout=25, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0 and not result.stderr, result.stdout + result.stderr
    response = json.loads(result.stdout)
    diagnostic = response.pop("_stderr", "")
    if status_state == "failed":
        assert "Previous background provisioning failed: fixture-service." in diagnostic
    elif status_state == "unreadable":
        assert "Could not read provisioning status:" in diagnostic
    else:
        assert not diagnostic
    expected = {} if runtime_state == "missing" else {"additionalContext": "anchor-hygiene-check"}
    assert response == expected
    assert {str(path.relative_to(runtime)): path.read_bytes()
            for path in runtime.rglob("*") if path.is_file()} == before
    assert runtime.exists() is (runtime_state == "stale" or status_state != "absent")
    assert not (runtime / "stamped-version").exists()
    assert not (runtime / "snapshots").exists()
    assert not (home / ".local").exists()
    assert not marker.exists() and hold.read_bytes() == hold_bytes
    for name in retained:
        assert (tmp_path / name).exists() is (runtime_state == "stale")
    guidance = home / ".copilot/session-state/fixture-fallback/instructions/agent-worktrees/session-guidance.instructions.md"
    text = guidance.read_text(encoding="utf-8")
    assert "Fixture connector guidance." in text
    assert ("anchor-hygiene-check" in text) is (runtime_state == "stale")


def test_report_path_never_runs_legacy_provisioning(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("report_hook_client", PLUGIN / "scripts/hook_client.py")
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    monkeypatch.setattr(client, "__file__", str(scripts / "hook_client.py"))
    monkeypatch.setattr(client.shutil, "which", lambda name: "fixture-shell")
    suffix = "ps1" if os.name == "nt" else "sh"
    (scripts / f"provision-check.{suffix}").write_text("legacy installer must not execute")
    monkeypatch.setattr(client.subprocess, "Popen", lambda *a, **k: pytest.fail("legacy provisioning ran"))
    result = client._fallback_legacy_session_start({}, report_only=True)
    assert "Report-only provisioning status helper unavailable" in result["_stderr"]
