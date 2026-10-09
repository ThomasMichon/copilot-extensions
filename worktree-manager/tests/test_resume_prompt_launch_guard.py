"""Execute the launchers' real cold-start guard functions across live races."""

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from worktree_manager import relocated_launch

BIN = Path(__file__).resolve().parents[1] / "bin"
PWSH = shutil.which("pwsh")
BASH = (
    shutil.which("bash", path=r"C:\Program Files\Git\usr\bin")
    or shutil.which("bash", path=r"C:\Program Files\Git\bin")
    or (shutil.which("bash") if os.name != "nt" else None)
)


def _function(text: str, name: str, *, powershell: bool) -> str:
    prefix = f"function {name} {{" if powershell else f"{name}() {{"
    start = text.index(prefix)
    end = text.index("\n}", start) + 2
    return text[start:end]


def _bundle(case: str) -> str:
    return json.dumps({
        "worktree_id": "wt-a",
        "facts": {"liveness": {
            "confirmed": case != "unknown",
            "value": {
                "active": "False" if case == "malformed" else case in ("mux", "bare"),
                "mux_live": case == "mux",
                "bare": case == "bare",
            },
        }},
    })


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("seeded,case,no_mux", [
    (True, "mux", False), (True, "bare", True),
    (True, "cold", False), (True, "cold", True),
    (True, "unknown", True), (True, "malformed", True),
    (False, "bare", False),
])
def test_real_powershell_guard_rejects_live_even_without_mux(tmp_path, seeded, case, no_mux):
    text = (BIN / "launch-session.ps1").read_text(encoding="utf-8")
    guard = _function(text, "Assert-AwColdResume", powershell=True)
    boolean = lambda value: "$true" if value else "$false"
    engine = tmp_path / "engine.ps1"
    engine.write_text(
        "if ($args -notcontains '--force-refresh') { exit 99 }\n"
        f"Write-Output '{_bundle(case)}'\nexit 0\n",
        encoding="utf-8",
    )
    script = tmp_path / "guard.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$plan = [pscustomobject]@{{worktree_id='wt-a'; seed_claimed={boolean(seeded)}; no_mux={boolean(no_mux)}}}\n"
        "$CopilotArgs = @()\n"
        f"$VenvPython = '{engine}'\n$script:LaunchProject = 'demo'\n"
        "function Write-SetupLog { param($Message,$Level) }\n"
        f"{guard}\nAssert-AwColdResume\nWrite-Output 'LAUNCH'\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)], capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        timeout=20,
    )
    rejected = bool(seeded and case != "cold")
    assert proc.returncode == (3 if rejected else 0), proc.stderr
    assert ("LAUNCH" in proc.stdout) is not rejected
    if rejected:
        assert "seed remains staged" in proc.stderr


@pytest.mark.skipif(BASH is None, reason="Native POSIX bash unavailable")
@pytest.mark.parametrize("seeded,case", [
    (1, "mux"), (1, "bare"), (1, "cold"), (1, "unknown"),
    (1, "malformed"), (0, "bare"),
])
def test_real_bash_guard_rejects_live_and_uncertain(tmp_path, seeded, case):
    text = (BIN / "launch-session.sh").read_text(encoding="utf-8")
    guard = _function(text, "aw_assert_cold_resume", powershell=False)
    script = (
        f"_SEEDED_LAUNCH={seeded}\n"
        "_SEEDED_WORKTREE_ID=wt-a\nLAUNCH_PROJECT=demo\nPYTHON=aw_test_python\n"
        "setup_log() { :; }\n"
        "aw_test_python() {\n"
        "if [[ \"$1\" == -m ]]; then\n"
        "[[ \"$*\" == *--force-refresh* ]] || return 99\n"
        f"printf '%s' '{_bundle(case)}'\n"
        "else\n"
        f"'{sys.executable}' \"$@\"\n"
        "fi\n}\n"
        f"{guard}\naw_assert_cold_resume\necho LAUNCH\n"
    )
    proc = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    rejected = bool(seeded and case != "cold")
    assert proc.returncode == (3 if rejected else 0), proc.stderr
    assert ("LAUNCH" in proc.stdout) is not rejected


def test_guards_cover_final_join_and_pre_create_without_live_delivery():
    ps = (BIN / "launch-session.ps1").read_text(encoding="utf-8")
    sh = (BIN / "launch-session.sh").read_text(encoding="utf-8")
    assert "Assert-AwColdResume -LiveSessionKnown" in ps
    assert "aw_assert_cold_resume live-known" in sh
    assert re.search(r"for \(\$attempt.*?\{\s+Assert-AwColdResume", ps)
    assert re.search(r"TMUX_CREATE_ATTEMPT\+\+\)\); do\s+aw_assert_cold_resume", sh)
    assert ps.index("Assert-AwColdResume\nif ($noMux)") < ps.index("if ($noMux) {")
    assert sh.index('aw_assert_cold_resume\n    if [[ "$NO_MUX"') < sh.index('if [[ "$NO_MUX" == "1" ]]')


def test_explicit_request_refuses_missing_staged_identity(monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(relocated_launch._core(), "_is_windows", lambda: True)
    monkeypatch.setattr(
        relocated_launch.subprocess, "Popen",
        lambda argv: (calls.append(argv) or SimpleNamespace(wait=lambda: 0)),
    )
    req = SimpleNamespace(
        project="demo", mode="resume", worktree_id="wt-a", no_mux=False,
        new_window=False, seed_prompt="quotes ' and \" plus\nsecond line",
    )
    plan = SimpleNamespace(worktree_id="wt-a", cmd=["copilot", "--interactive", "configured"])
    assert relocated_launch._run_relocated_mux_launch(req, plan, Path("launch-session.ps1")) == 1
    assert calls == []


def test_new_creation_prompt_is_not_reclassified_as_resume(monkeypatch):
    from types import SimpleNamespace

    calls = []
    monkeypatch.setattr(relocated_launch._core(), "_is_windows", lambda: True)
    monkeypatch.setattr(
        relocated_launch.subprocess, "Popen",
        lambda argv: calls.append(argv) or SimpleNamespace(wait=lambda: 3),
    )
    req = SimpleNamespace(
        project="demo", mode="new", worktree_id=None, no_mux=False,
        new_window=False, seed_prompt="creation prompt",
    )
    plan = SimpleNamespace(
        worktree_id="wt-a", cmd=["copilot"], seed_claimed=False,
        raw={"seed_pending": True, "seed_id": "intent-a", "seed_kind": "new"},
    )
    assert relocated_launch._run_relocated_mux_launch(req, plan, Path("launch-session.ps1")) == 3
    assert "--seed" not in calls[0]
    assert calls[0][-3:] == ["--stage-launch-seed", "--seed-id", "intent-a"]


@pytest.mark.parametrize("selector", [
    {"worktree_id": "wt-a"}, {"base": True}, {"new": True},
])
def test_ordinary_launches_do_not_require_new_flag_on_older_engine(monkeypatch, selector):
    from worktree_manager import engine_client

    seen = []
    def older_engine(project, args, **kwargs):
        seen.append(args)
        assert "--stage-launch-seed" not in args
        return {"action": "exec", "cmd": ["copilot"]}
    monkeypatch.setattr(engine_client, "run_json", older_engine)
    assert engine_client.resolve_launch_plan("demo", **selector).is_exec
    assert len(seen) == 1


@pytest.mark.parametrize("selector", [{"new": True}, {"worktree_id": "wt-a"}])
def test_prompted_new_and_resume_require_staged_owner_on_older_engine(monkeypatch, selector):
    from worktree_manager import engine_client

    seen = []
    def older_engine(project, args, **kwargs):
        seen.append(args)
        assert "--stage-launch-seed" in args
        raise engine_client.EngineError("unrecognized arguments: --stage-launch-seed")
    monkeypatch.setattr(engine_client, "run_json", older_engine)
    with pytest.raises(engine_client.EngineFeatureUnavailable, match="staged launch-prompt ownership"):
        engine_client.resolve_launch_plan("demo", seed="task", **selector)
    assert len(seen) == 1
