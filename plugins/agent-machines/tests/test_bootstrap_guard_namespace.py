"""Native producer/reader parity for the central and legacy bootstrap paths."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
              pytest.mark.effect("filesystem")]
PLUGIN = Path(__file__).resolve().parents[1]


@pytest.fixture
def guard(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env.pop("BOOTSTRAP_KILLSWITCH_STATE_FILE", None)
    env.update(HOME=str(home), USERPROFILE=str(home),
               BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT=str(home))
    if os.name == "nt":
        shell = shutil.which(os.environ.get("BOOTSTRAP_KILLSWITCH_TEST_SHELL", "powershell.exe"))
        script = PLUGIN / "scripts/bootstrap-killswitch-guard.ps1"
    else:
        shell = shutil.which("bash")
        script = PLUGIN / "scripts/bootstrap-killswitch-guard.sh"
    if shell is None:
        pytest.skip("native guard interpreter unavailable")
    def run(*args, override=None):
        child = env.copy()
        if override is not None:
            child["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(override)
        if os.name == "nt":
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            command = (f"Set-Variable -Name HOME -Scope Global -Force -Value {quote(home)}; "
                       f"if ($HOME -ne {quote(home)}) {{ throw 'fixture home not selected' }}; "
                       f"& {quote(script)} " + " ".join(quote(arg) for arg in args))
            argv = [shell, "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            argv = [shell, str(script), *args]
        return subprocess.run(argv, capture_output=True, text=True, env=child, timeout=15,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return home, run


def test_native_on_uses_central_namespace_and_off_clears_both(guard):
    home, run = guard
    root = home / ".copilot-extensions"
    primary = root / "killSwitches/bootstrap-killswitch.json"
    legacy = root / "bootstrap-killswitch.json"
    result = run("on", "native namespace")
    assert result.returncode == 0, result.stderr
    assert json.loads(primary.read_text(encoding="utf-8"))["active"] is True
    assert not legacy.exists()
    legacy.write_text('{"active":true,"reason":"retained old reader"}', encoding="utf-8")
    assert run("check").returncode == 0
    assert json.loads(run("status").stdout)["reason"] == "native namespace"
    result = run("off")
    assert result.returncode == 0, result.stderr
    assert not primary.exists() and not legacy.exists()
    assert run("check").returncode == 1


def test_native_legacy_fallback_but_central_is_authoritative(guard):
    home, run = guard
    root = home / ".copilot-extensions"
    root.mkdir()
    legacy = root / "bootstrap-killswitch.json"
    legacy.write_text('{"active":true,"reason":"legacy pause"}', encoding="utf-8")
    assert run("check").returncode == 0
    assert json.loads(run("status").stdout)["reason"] == "legacy pause"
    primary = root / "killSwitches/bootstrap-killswitch.json"
    primary.parent.mkdir()
    primary.write_text('{"active":false}', encoding="utf-8")
    assert run("check").returncode == 1
    primary.write_text("malformed", encoding="utf-8")
    assert run("check").returncode == 1


def test_native_override_isolation(guard):
    home, run = guard
    root = home / ".copilot-extensions"
    (root / "killSwitches").mkdir(parents=True)
    defaults = [root / "bootstrap-killswitch.json", root / "killSwitches/bootstrap-killswitch.json"]
    for path in defaults:
        path.write_text('{"active":true}', encoding="utf-8")
    explicit = home / "explicit.json"
    assert run("check", override=explicit).returncode == 1
    assert run("on", "isolated", override=explicit).returncode == 0
    assert run("off", override=explicit).returncode == 0
    assert not explicit.exists()
    assert all(path.exists() for path in defaults)
