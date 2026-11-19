"""Synthetic PowerShell consumers, not native mouse-event qualification."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from agent_procutil import no_window_kwargs

_BIN = Path(__file__).resolve().parents[1] / "bin"
_PWSH = shutil.which("pwsh")
_PERSONAL = "# personal sentinel\nset -g mouse off\nset -g status-left personal\n"
_BEGIN = "# >>> agent-worktrees mux keybinds (opt-in) >>>"
_END = "# <<< agent-worktrees mux keybinds (opt-in) <<<"


@dataclass(frozen=True)
class _Run:
    config: str
    calls: list[list[str]]
    fragment: str
    output: str


def _run(
    tmp_path: Path,
    invocation: str,
    *,
    sessions: tuple[str, ...] = ("wt-one", "wt-two"),
    fragment: bool = True,
    initial: str = _PERSONAL,
) -> _Run:
    if _PWSH is None:
        pytest.skip("PowerShell is unavailable")
    scripts = tmp_path / "scripts"
    commands = tmp_path / "commands"
    home = tmp_path / "home"
    for path in (scripts, commands, home):
        path.mkdir()
    for name in ("apply-mux-keybinds.ps1", "session-options.ps1"):
        shutil.copyfile(_BIN / name, scripts / name)
    if fragment:
        shutil.copyfile(_BIN / "psmux-passthrough.conf", scripts / "psmux-passthrough.conf")
    conf = home / ".psmux.conf"
    conf.write_text(initial, encoding="utf-8")
    calls = tmp_path / "calls.jsonl"
    inventory = tmp_path / "sessions.json"
    inventory.write_text(json.dumps(sessions), encoding="utf-8")
    stub = commands / "psmux.ps1"
    stub.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "ConvertTo-Json -Compress -InputObject @($args) | "
        "Add-Content -LiteralPath $env:WHEEL_CALLS\n"
        "switch ($args[0]) {\n"
        "  'list-sessions' {\n"
        "    foreach ($name in (Get-Content -Raw -LiteralPath $env:WHEEL_SESSIONS "
        "| ConvertFrom-Json)) { Write-Output ($name + ': fixture') }\n"
        "  }\n"
        "  'source-file' { if ($args[1] -ne '-t') { throw 'missing target' } }\n"
        "  default { throw 'unexpected synthetic mux operation' }\n"
        "}\n",
        encoding="utf-8",
    )
    driver = tmp_path / "driver.ps1"
    driver.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$env:PATH = $env:WHEEL_COMMANDS + [IO.Path]::PathSeparator + $env:PATH\n"
        "if ((Get-Command psmux -ErrorAction Stop).Source -ne "
        "(Join-Path $env:WHEEL_COMMANDS 'psmux.ps1')) { throw 'non-fixture mux' }\n"
        "$helper = Join-Path $env:WHEEL_SCRIPTS 'apply-mux-keybinds.ps1'\n"
        "$options = Join-Path $env:WHEEL_SCRIPTS 'session-options.ps1'\n"
        + invocation + "\n",
        encoding="utf-8",
    )
    env = dict(
        os.environ,
        HOME=str(home),
        USERPROFILE=str(home),
        WHEEL_COMMANDS=str(commands),
        WHEEL_SCRIPTS=str(scripts),
        WHEEL_CALLS=str(calls),
        WHEEL_SESSIONS=str(inventory),
    )
    result = subprocess.run(
        [_PWSH, "-NoProfile", "-NonInteractive", "-File", str(driver)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        env=env,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return _Run(
        config=conf.read_text(encoding="utf-8-sig"),
        calls=[
            cast(list[str], json.loads(line))
            for line in calls.read_text(encoding="utf-8-sig").splitlines()
        ],
        fragment=(
            (scripts / "psmux-passthrough.conf").read_text(encoding="utf-8")
            if fragment else ""
        ),
        output=result.stdout + result.stderr,
    )


def _assert_native_configuration(text: str) -> None:
    directives = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert "unbind-key -a -T root" in directives
    assert "set -g mouse on" in directives
    assert "set -g scroll-enter-copy-mode off" in directives
    assert not any(
        "WheelUpPane" in line or "WheelDownPane" in line or "send-keys -M" in line
        for line in directives
    )
    assert not any("send-keys" in line for line in directives)


@pytest.mark.parametrize("persist", [True, False])
def test_helper_selects_native_wheel_configuration(tmp_path: Path, persist: bool) -> None:
    run = _run(tmp_path, "& $helper" + ("" if persist else " -NoPersist"))
    assert run.config.startswith(_PERSONAL)
    if persist:
        assert run.config.count(_BEGIN) == run.config.count(_END) == 1
        _assert_native_configuration(run.config)
    else:
        assert run.config == _PERSONAL
    assert [call[:3] for call in run.calls] == [
        ["list-sessions"],
        ["source-file", "-t", "wt-one"],
        ["source-file", "-t", "wt-two"],
    ]
    assert all(
        Path(call[3]).name == "psmux-passthrough.conf"
        for call in run.calls[1:]
    )
    _assert_native_configuration(run.fragment)


def test_helper_replaces_only_its_block_idempotently(tmp_path: Path) -> None:
    initial = (
        _PERSONAL + _BEGIN + "\n"
        "bind-key -T root WheelUpPane send-keys -M\n"
        + _END + "\n# personal tail\n"
    )
    run = _run(tmp_path, "& $helper\n& $helper", initial=initial)
    assert run.config.startswith(_PERSONAL + "# personal tail\n")
    assert run.config.count(_BEGIN) == run.config.count(_END) == 1
    assert "send-keys -M" not in run.config
    _assert_native_configuration(run.config)
    assert sum(call[0] == "source-file" for call in run.calls) == 4


@pytest.mark.parametrize("missing_fragment", [True, False])
def test_helper_preserves_no_apply_boundaries(
    tmp_path: Path, missing_fragment: bool,
) -> None:
    run = _run(
        tmp_path, "& $helper -NoPersist",
        sessions=("wt-one",) if missing_fragment else (),
        fragment=not missing_fragment,
    )
    assert run.config == _PERSONAL
    assert run.calls == [["list-sessions"]]
    assert (
        "passthrough fragment not found" if missing_fragment else "no running psmux server"
    ) in run.output


def test_launcher_passthrough_sources_only_the_named_session(tmp_path: Path) -> None:
    run = _run(tmp_path, ". $options\nInvoke-AwPsmuxPassthrough -Session 'wt-selected'")
    assert run.config == _PERSONAL
    assert len(run.calls) == 1
    assert run.calls[0][:3] == ["source-file", "-t", "wt-selected"]
    _assert_native_configuration(run.fragment)


def test_tmux_keeps_its_supported_bidirectional_mouse_bindings() -> None:
    text = (_BIN / "apply-mux-keybinds.sh").read_text(encoding="utf-8")
    for direction in ("WheelUpPane", "WheelDownPane"):
        assert any(
            direction in line and "send-keys -M" in line
            for line in text.splitlines()
            if line.strip().startswith("bind-key") or line.strip().startswith("tmux bind-key")
        )
