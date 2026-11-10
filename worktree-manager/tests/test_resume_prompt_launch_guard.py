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
    if case == "error":
        return json.dumps({"error": "Required launch payload is missing"})
    return json.dumps({
        "worktree_id": "wt-a",
        **({"availability": {"status": "pending", "reason": "project cleanup pending"}}
           if case == "pending" else {}),
        "facts": {"liveness": {
            "confirmed": case not in ("unknown", "pending"),
            "value": None if case == "pending" else {
                "active": "False" if case == "malformed" else case in ("mux", "bare"),
                "mux_live": case == "mux",
                "bare": case == "bare",
            },
        }},
    })


@pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")
@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("seeded,case,no_mux", [
    (True, "mux", False), (True, "bare", True),
    (True, "cold", False), (True, "cold", True), (True, "warning", False),
    (True, "unknown", True), (True, "malformed", True),
    (True, "pending", False), (True, "error", False),
    (True, "failed", False), (True, "known", False),
    (False, "bare", False),
])
def test_real_powershell_guard_rejects_live_even_without_mux(tmp_path, seeded, case, no_mux, pending):
    text = (BIN / "launch-session.ps1").read_text(encoding="utf-8")
    guard = _function(text, "Assert-AwColdResume", powershell=True)
    boolean = lambda value: "$true" if value else "$false"
    engine = tmp_path / "engine.ps1"
    engine.write_text(
        "if ($args -notcontains '--force-refresh') { exit 99 }\n"
        + ("[Console]::Error.WriteLine('Nonfatal status warning')\n" if case == "warning" else "")
        + ("[Console]::Error.WriteLine('Git status timed out'); exit 7\n"
           if case == "failed" else f"Write-Output '{_bundle(case)}'\nexit 0\n"),
        encoding="utf-8",
    )
    script = tmp_path / "guard.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$plan = [pscustomobject]@{{worktree_id='wt-a'; seed_claimed={boolean(seeded and not pending)}; "
        f"seed_pending={boolean(seeded and pending)}; no_mux={boolean(no_mux)}}}\n"
        "$CopilotArgs = @()\n"
        f"$VenvPython = '{engine}'\n$script:LaunchProject = 'demo'\n"
        "function Write-SetupLog { param($Message,$Level) }\n"
        f"{guard}\nAssert-AwColdResume {'-LiveSessionKnown' if case == 'known' else ''}\nWrite-Output 'LAUNCH'\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)], capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        timeout=20,
    )
    rejected = bool(seeded and case not in ("cold", "warning"))
    assert proc.returncode == (3 if rejected else 0), proc.stderr
    assert ("LAUNCH" in proc.stdout) is not rejected
    if rejected:
        assert "seed remains staged" in proc.stderr
        assert "confirmed cold target" not in proc.stderr
        expected = {
            "mux": "a live session appeared", "bare": "a live session appeared",
            "known": "a live session appeared", "error": "Required launch payload is missing",
            "failed": "Git status timed out", "pending": "project cleanup pending",
        }.get(case, "Could not verify cold launch target")
        assert expected in proc.stderr
    if case == "warning":
        assert "Nonfatal status warning" in proc.stderr


@pytest.mark.skipif(BASH is None, reason="Native POSIX bash unavailable")
@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("seeded,case", [
    (1, "mux"), (1, "bare"), (1, "cold"), (1, "warning"), (1, "unknown"),
    (1, "malformed"), (1, "pending"), (1, "error"), (1, "failed"),
    (1, "known"), (0, "bare"),
])
def test_real_bash_guard_rejects_live_and_uncertain(tmp_path, seeded, case, pending):
    text = (BIN / "launch-session.sh").read_text(encoding="utf-8")
    guard = _function(text, "aw_assert_cold_resume", powershell=False)
    script = (
        f"_SEEDED_LAUNCH={seeded}\n"
        f"_PENDING_NEW_SEED={int(pending)}\n"
        "_SEEDED_WORKTREE_ID=wt-a\nLAUNCH_PROJECT=demo\nPYTHON=aw_test_python\n"
        "setup_log() { :; }\n"
        "aw_test_python() {\n"
        "if [[ \"$1\" == -m ]]; then\n"
        "[[ \"$*\" == *--force-refresh* ]] || return 99\n"
        + ("echo 'Nonfatal status warning' >&2\n" if case == "warning" else "")
        + ("echo 'Git status timed out' >&2; return 7\n"
           if case == "failed" else f"printf '%s' '{_bundle(case)}'\n")
        + "else\n"
        f"'{sys.executable}' \"$@\"\n"
        "fi\n}\n"
        f"{guard}\naw_assert_cold_resume {'live-known' if case == 'known' else ''}\necho LAUNCH\n"
    )
    proc = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    rejected = bool(seeded and case not in ("cold", "warning"))
    assert proc.returncode == (3 if rejected else 0), proc.stderr
    assert ("LAUNCH" in proc.stdout) is not rejected
    if rejected:
        assert "confirmed cold target" not in proc.stderr
        expected = {
            "mux": "a live session appeared", "bare": "a live session appeared",
            "known": "a live session appeared", "error": "Required launch payload is missing",
            "failed": "Git status timed out", "pending": "project cleanup pending",
        }.get(case, "Could not verify cold launch target")
        assert expected in proc.stderr
    if case == "warning":
        assert "Nonfatal status warning" in proc.stderr


def test_guards_cover_final_join_and_pre_create_without_live_delivery():
    ps = (BIN / "launch-session.ps1").read_text(encoding="utf-8")
    sh = (BIN / "launch-session.sh").read_text(encoding="utf-8")
    assert "Assert-AwColdResume -LiveSessionKnown" in ps
    assert "aw_assert_cold_resume live-known" in sh
    assert re.search(r"for \(\$attempt.*?\{\s+Assert-AwColdResume", ps)
    assert re.search(r"TMUX_CREATE_ATTEMPT\+\+\)\); do\s+aw_assert_cold_resume", sh)
    assert ps.index("Assert-AwColdResume\nif ($noMux)") < ps.index("if ($noMux) {")
    assert sh.index('aw_assert_cold_resume\n    if [[ "$NO_MUX"') < sh.index('if [[ "$NO_MUX" == "1" ]]')
    assert re.search(
        r"if \(-not \$plan.seed_pending -and -not \$plan.seed_preserved_id\) \{\s+Invoke-SeedDeliverySafe", ps,
    )
    assert re.search(
        r'if \[\[ "\$_PENDING_NEW_SEED" != "1" && "\$_PRESERVED_LAUNCH_SEED" != "1" \]\]; then\s+_aw_deliver_pending_seed', sh,
    )


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
def test_ordinary_launches_do_not_require_new_flag_on_older_engine(monkeypatch, tmp_path, selector):
    from worktree_manager import engine_client

    seen = []
    monkeypatch.delenv("AGENT_WORKTREES_RETAINED_SEED_PLANS", raising=False)
    def older_engine(project, args, **kwargs):
        if args[:2] == ["agent-policy", "check"]:
            return {
                "version": 1, "repository": str(tmp_path), "expose_agent": True,
                "sources": [], "denied_by": [], "allowed": True,
            }
        seen.append(args)
        assert "--stage-launch-seed" not in args
        assert "--preserve-seed-id" not in args
        environment = kwargs.get("environment") or {}
        assert (environment.get("AGENT_WORKTREES_RETAINED_SEED_PLANS") == "1") == bool(selector.get("worktree_id"))
        return {"action": "exec", "cmd": ["copilot"]}
    monkeypatch.setattr(engine_client, "run_json", older_engine)
    assert engine_client.resolve_launch_plan("demo", **selector).is_exec
    assert len(seen) == 1
    assert "AGENT_WORKTREES_RETAINED_SEED_PLANS" not in os.environ


@pytest.mark.parametrize("windows", [False, True])
def test_delegation_carries_preserved_identity_after_intent_replacement(monkeypatch, windows):
    from types import SimpleNamespace

    calls, deliveries = [], []
    monkeypatch.setattr(relocated_launch._core(), "_is_windows", lambda: windows)
    monkeypatch.setattr(os, "environ", dict(os.environ))
    current_identity = "replacement-b"

    def boundary(argv):
        calls.append(argv)
        expected = argv[argv.index("--preserve-seed-id") + 1]
        if expected != current_identity:
            return 3
        deliveries.append(current_identity)
        return 0

    monkeypatch.setattr(
        relocated_launch.subprocess, "Popen",
        lambda argv: SimpleNamespace(wait=lambda: boundary(argv)),
    )
    class ExecResult(Exception):
        pass
    def execvp(_file, argv):
        raise ExecResult(boundary(argv))
    monkeypatch.setattr(relocated_launch.os, "execvp", execvp)
    req = SimpleNamespace(
        project="demo", mode="resume", worktree_id="wt-a", no_mux=True,
        new_window=False, seed_prompt=None,
    )
    plan = SimpleNamespace(
        worktree_id="wt-a", raw={
            "seed_pending": False, "seed_preserved_id": "original-a", "seed_preservation_version": 1,
        },
    )
    if windows:
        assert relocated_launch._run_relocated_mux_launch(req, plan, Path("launch-session.ps1")) == 3
    else:
        with pytest.raises(ExecResult) as result:
            relocated_launch._run_relocated_mux_launch(req, plan, Path("launch-session.sh"))
        assert result.value.args == (3,)
    assert len(calls) == 1 and deliveries == []
    assert "--stage-launch-seed" not in calls[0] and "--seed-id" not in calls[0]


@pytest.mark.parametrize("raw", [
    {"seed_preserved_id": ""},
    {"seed_preserved_id": True},
    {"seed_preserved_id": "retained", "seed_pending": True, "seed_id": "delivery"},
    {"seed_preserved_id": "retained", "seed_preservation_version": 2},
])
def test_delegation_rejects_invalid_or_conflicting_retained_identity(monkeypatch, raw):
    from types import SimpleNamespace

    monkeypatch.setattr(
        relocated_launch.subprocess, "Popen", lambda *a, **k: pytest.fail("invalid ownership reached spawn"),
    )
    req = SimpleNamespace(project="demo", mode="resume", worktree_id="wt-a", seed_prompt=None)
    assert relocated_launch._run_relocated_mux_launch(
        req, SimpleNamespace(worktree_id="wt-a", raw={"seed_preservation_version": 1, **raw}),
        Path("launch-session.ps1"),
    ) == 1


@pytest.mark.parametrize("selector", [{"new": True}, {"worktree_id": "wt-a"}])
@pytest.mark.parametrize("missing", ["--stage-launch-seed", "--require-native-seed"])
def test_prompted_new_and_resume_require_staged_owner_on_older_engine(
    monkeypatch, tmp_path, selector, missing,
):
    from worktree_manager import engine_client

    seen = []
    def older_engine(project, args, **kwargs):
        if args[:2] == ["agent-policy", "check"]:
            return {
                "version": 1, "repository": str(tmp_path), "expose_agent": True,
                "sources": [], "denied_by": [], "allowed": True,
            }
        seen.append(args)
        assert "--stage-launch-seed" in args
        assert "--require-native-seed" in args
        raise engine_client.EngineError("unrecognized arguments: " + missing)
    monkeypatch.setattr(engine_client, "run_json", older_engine)
    with pytest.raises(engine_client.EngineFeatureUnavailable, match="staged launch-prompt ownership"):
        engine_client.resolve_launch_plan("demo", seed="task", **selector)
    assert len(seen) == 1
