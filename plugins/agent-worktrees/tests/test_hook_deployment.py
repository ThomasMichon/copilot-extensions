"""A refused deployment component does not suppress independent session hooks."""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def test_legacy_session_retains_independent_hooks_after_publication_refusal(
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
    assert "deployment-managed" in result["_stderr"]
