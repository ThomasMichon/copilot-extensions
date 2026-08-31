"""Behavior tests for read-only companions to mixed session-start hooks."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]


def _write_version(home: Path, version: str = "1.2.3") -> None:
    root = home / ".agent-worktrees"
    root.mkdir(parents=True, exist_ok=True)
    (root / "current-version").write_text(version, encoding="utf-8")


def _payload(
    session_id: str,
    cwd: Path,
    source: str = "new",
    timestamp: object = 1_000,
) -> str:
    value = {"sessionId": session_id, "cwd": str(cwd), "source": source}
    if timestamp is not None:
        value["timestamp"] = timestamp
    return json.dumps(value)


def _bash() -> str:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Bash is not available")
    if os.name == "nt" and "WindowsApps" in Path(bash).parts:
        pytest.skip("WSL Bash cannot execute Windows test paths directly")
    return bash


def _powershell() -> str:
    powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("PowerShell is not available")
    return powershell


def _run(
    script: str,
    payload: str,
    *,
    home: Path,
    cwd: Path,
    context_only: bool = False,
    side_effect_only: bool = False,
    await_context: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [_bash(), str(PLUGIN / "scripts" / script)]
    if context_only:
        command.append("--context-only")
    elif side_effect_only:
        command.append("--side-effect-only")
    elif await_context:
        command.append("--await-context")
    environment = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    return subprocess.run(
        command,
        input=payload,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _run_powershell(
    script: str,
    payload: str,
    *,
    home: Path,
    cwd: Path,
    context_only: bool = False,
    side_effect_only: bool = False,
    await_context: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        _powershell(),
        "-NoLogo",
        "-NoProfile",
        "-File",
        str(PLUGIN / "scripts" / script),
    ]
    if context_only:
        command.append("--context-only")
    elif side_effect_only:
        command.append("--side-effect-only")
    elif await_context:
        command.append("--await-context")
    environment = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    return subprocess.run(
        command,
        input=payload,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_register_nudge_context_only_replays_without_mutating(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "example"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    command = bin_dir / "agent-worktrees"
    command.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    command.chmod(0o755)
    _write_version(home)
    payload = _payload("session-nudge", repo)

    direct = _run("register-nudge.sh", payload, home=home, cwd=repo)
    assert direct.returncode == 0, direct.stderr
    direct_payload = json.loads(direct.stdout)
    assert "not a registered agent-worktrees project" in direct_payload[
        "additionalContext"
    ]
    state_file = next(
        (home / ".agent-worktrees" / ".session-context").glob(
            "register-nudge-*"
        )
    )
    state_mtime = state_file.stat().st_mtime_ns

    replay = _run(
        "register-nudge.sh",
        payload,
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(replay.stdout) == direct_payload
    assert state_file.stat().st_mtime_ns == state_mtime

    alias = tmp_path / "example-link"
    alias.symlink_to(repo, target_is_directory=True)
    canonical_alias = _run(
        "register-nudge.sh",
        _payload("session-nudge", alias),
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(canonical_alias.stdout) == direct_payload

    other = _run(
        "register-nudge.sh",
        _payload("other-session", repo),
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(other.stdout) == {}

    other_cwd = tmp_path / "other"
    other_cwd.mkdir()
    changed_cwd = _run(
        "register-nudge.sh",
        _payload("session-nudge", other_cwd),
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(changed_cwd.stdout) == {}

    changed_source = _run(
        "register-nudge.sh",
        _payload("session-nudge", repo, source="resume"),
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(changed_source.stdout) == {}

    for stale_payload in (
        _payload("session-nudge", repo, timestamp=1_001),
        _payload("session-nudge", repo, timestamp=None),
        _payload("session-nudge", repo, timestamp="later"),
    ):
        stale_timestamp = _run(
            "register-nudge.sh",
            stale_payload,
            home=home,
            cwd=repo,
            context_only=True,
        )
        assert json.loads(stale_timestamp.stdout) == {}

    _write_version(home, "1.2.4")
    changed_version = _run(
        "register-nudge.sh",
        payload,
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(changed_version.stdout) == {}


def test_register_nudge_side_effect_mode_suppresses_but_preserves_context(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "example"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    command = bin_dir / "agent-worktrees"
    command.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    command.chmod(0o755)
    _write_version(home)
    payload = _payload("session-nudge-side-effect", repo)

    direct = _run(
        "register-nudge.sh",
        payload,
        home=home,
        cwd=repo,
        side_effect_only=True,
    )
    replay = _run(
        "register-nudge.sh",
        payload,
        home=home,
        cwd=repo,
        context_only=True,
    )

    assert json.loads(direct.stdout) == {}
    assert "not a registered agent-worktrees project" in json.loads(
        replay.stdout
    )["additionalContext"]


def test_register_nudge_awaits_direct_completion_receipt(tmp_path: Path) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "example"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    command = bin_dir / "agent-worktrees"
    command.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    command.chmod(0o755)
    _write_version(home)
    payload = _payload("session-nudge-await", repo)
    environment = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    waiter = subprocess.Popen(
        [
            _bash(),
            str(PLUGIN / "scripts" / "register-nudge.sh"),
            "--await-context",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=repo,
        env=environment,
        text=True,
    )
    assert waiter.stdin is not None
    waiter.stdin.write(payload)
    waiter.stdin.close()
    time.sleep(0.1)

    direct = _run(
        "register-nudge.sh",
        payload,
        home=home,
        cwd=repo,
        side_effect_only=True,
    )
    assert waiter.stdout is not None
    replay_output = waiter.stdout.read()
    waiter.wait(timeout=5)

    assert json.loads(direct.stdout) == {}
    assert "not a registered agent-worktrees project" in json.loads(
        replay_output
    )["additionalContext"]


def test_marketplace_context_only_replays_without_reconciling(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    bin_dir = home / ".agent-worktrees" / "bin"
    bin_dir.mkdir(parents=True)
    repo.mkdir()
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/usr/bin/env bash\n"
        "cat >/dev/null\n"
        "printf '%s' "
        + shlex.quote(
            json.dumps(
                {
                    "additionalContext": (
                        "Local marketplace sources changed; restart the session."
                    )
                }
            )
        )
        + "\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (bin_dir / "resolve-runtime.sh").write_text(
        f"AW_PY={shlex.quote(str(fake_python))}\n",
        encoding="utf-8",
    )
    _write_version(home)
    payload = _payload("session-marketplace", repo)

    direct = _run("marketplace-overrides.sh", payload, home=home, cwd=repo)
    assert direct.returncode == 0, direct.stderr
    direct_payload = json.loads(direct.stdout)
    state_file = next(
        (home / ".agent-worktrees" / ".session-context").glob(
            "marketplace-overrides-*"
        )
    )
    state_mtime = state_file.stat().st_mtime_ns

    fake_python.write_text(
        "#!/usr/bin/env bash\nexit 97\n",
        encoding="utf-8",
    )
    replay = _run(
        "marketplace-overrides.sh",
        payload,
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == direct_payload
    assert state_file.stat().st_mtime_ns == state_mtime

    changed_cwd = _run(
        "marketplace-overrides.sh",
        _payload("session-marketplace", tmp_path),
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(changed_cwd.stdout) == {}
    changed_source = _run(
        "marketplace-overrides.sh",
        _payload("session-marketplace", repo, source="resume"),
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(changed_source.stdout) == {}
    for stale_payload in (
        _payload("session-marketplace", repo, timestamp=1_001),
        _payload("session-marketplace", repo, timestamp=None),
        _payload("session-marketplace", repo, timestamp="later"),
    ):
        stale_timestamp = _run(
            "marketplace-overrides.sh",
            stale_payload,
            home=home,
            cwd=repo,
            context_only=True,
        )
        assert json.loads(stale_timestamp.stdout) == {}
    _write_version(home, "1.2.4")
    changed_version = _run(
        "marketplace-overrides.sh",
        payload,
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(changed_version.stdout) == {}


def test_marketplace_side_effect_mode_suppresses_but_preserves_context(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    bin_dir = home / ".agent-worktrees" / "bin"
    bin_dir.mkdir(parents=True)
    repo.mkdir()
    fake_python = tmp_path / "fake-python"
    expected = {
        "additionalContext": "Local marketplace sources changed; restart."
    }
    fake_python.write_text(
        "#!/usr/bin/env bash\ncat >/dev/null\nprintf '%s' "
        + shlex.quote(json.dumps(expected))
        + "\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    (bin_dir / "resolve-runtime.sh").write_text(
        f"AW_PY={shlex.quote(str(fake_python))}\n",
        encoding="utf-8",
    )
    _write_version(home)
    payload = _payload("session-marketplace-side-effect", repo)

    direct = _run(
        "marketplace-overrides.sh",
        payload,
        home=home,
        cwd=repo,
        side_effect_only=True,
    )
    replay = _run(
        "marketplace-overrides.sh",
        payload,
        home=home,
        cwd=repo,
        context_only=True,
    )

    assert json.loads(direct.stdout) == {}
    assert json.loads(replay.stdout) == expected


def test_register_nudge_powershell_context_only_replays(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "example"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    command = bin_dir / "agent-worktrees"
    command.write_text("", encoding="utf-8")
    _write_version(home)
    payload = _payload("session-nudge-ps", repo)

    direct = _run_powershell(
        "register-nudge.ps1",
        payload,
        home=home,
        cwd=repo,
    )
    assert direct.returncode == 0, direct.stderr
    direct_payload = json.loads(direct.stdout)

    replay = _run_powershell(
        "register-nudge.ps1",
        payload,
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == direct_payload

    alias = tmp_path / "example-link"
    try:
        alias.symlink_to(repo, target_is_directory=True)
    except OSError:
        alias = None
    if alias is not None:
        canonical_alias = _run_powershell(
            "register-nudge.ps1",
            _payload("session-nudge-ps", alias),
            home=home,
            cwd=repo,
            context_only=True,
        )
        assert json.loads(canonical_alias.stdout) == direct_payload

    changed_source = _run_powershell(
        "register-nudge.ps1",
        _payload("session-nudge-ps", repo, source="resume"),
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(changed_source.stdout) == {}
    for stale_payload in (
        _payload("session-nudge-ps", repo, timestamp=1_001),
        _payload("session-nudge-ps", repo, timestamp=None),
        _payload("session-nudge-ps", repo, timestamp="later"),
    ):
        stale_timestamp = _run_powershell(
            "register-nudge.ps1",
            stale_payload,
            home=home,
            cwd=repo,
            context_only=True,
        )
        assert json.loads(stale_timestamp.stdout) == {}
    _write_version(home, "1.2.4")
    changed_version = _run_powershell(
        "register-nudge.ps1",
        payload,
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(changed_version.stdout) == {}


def test_marketplace_powershell_context_only_replays(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    bin_dir = home / ".agent-worktrees" / "bin"
    bin_dir.mkdir(parents=True)
    repo.mkdir()
    fake_python = tmp_path / "fake-python.ps1"
    expected = {
        "additionalContext": "Local marketplace sources changed; restart."
    }
    escaped = json.dumps(expected).replace("'", "''")
    fake_python.write_text(
        f"Write-Output '{escaped}'\n",
        encoding="utf-8",
    )
    escaped_python = str(fake_python).replace("'", "''")
    (bin_dir / "resolve-runtime.ps1").write_text(
        f"$AwPy = '{escaped_python}'\n",
        encoding="utf-8",
    )
    _write_version(home)
    payload = _payload("session-marketplace-ps", repo)

    direct = _run_powershell(
        "marketplace-overrides.ps1",
        payload,
        home=home,
        cwd=repo,
    )
    assert direct.returncode == 0, direct.stderr
    assert json.loads(direct.stdout) == expected

    fake_python.write_text("exit 97\n", encoding="utf-8")
    replay = _run_powershell(
        "marketplace-overrides.ps1",
        payload,
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert replay.returncode == 0, replay.stderr
    assert json.loads(replay.stdout) == expected

    changed_cwd = _run_powershell(
        "marketplace-overrides.ps1",
        _payload("session-marketplace-ps", tmp_path),
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(changed_cwd.stdout) == {}
    changed_source = _run_powershell(
        "marketplace-overrides.ps1",
        _payload("session-marketplace-ps", repo, source="resume"),
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(changed_source.stdout) == {}
    for stale_payload in (
        _payload("session-marketplace-ps", repo, timestamp=1_001),
        _payload("session-marketplace-ps", repo, timestamp=None),
        _payload("session-marketplace-ps", repo, timestamp="later"),
    ):
        stale_timestamp = _run_powershell(
            "marketplace-overrides.ps1",
            stale_payload,
            home=home,
            cwd=repo,
            context_only=True,
        )
        assert json.loads(stale_timestamp.stdout) == {}
    _write_version(home, "1.2.4")
    changed_version = _run_powershell(
        "marketplace-overrides.ps1",
        payload,
        home=home,
        cwd=repo,
        context_only=True,
    )
    assert json.loads(changed_version.stdout) == {}
