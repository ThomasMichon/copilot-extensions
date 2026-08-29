"""Production session-conduct assembly and wrapper parity tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

import pytest

from agent_worktrees import conduct as c


_PLUGIN = Path(__file__).resolve().parents[1]
_SCRIPTS = _PLUGIN / "scripts"


def _bash() -> str | None:
    if os.name == "nt":
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Git"
            / "bin"
            / "bash.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Programs"
            / "Git"
            / "bin"
            / "bash.exe",
        ]
        return next((str(path) for path in candidates if path.is_file()), None)
    return shutil.which("bash")


def _prepare_hook_home(
    tmp_path: Path,
    *,
    definition: str,
    related: str,
    history: str,
    windows_line_endings: bool = False,
) -> tuple[Path, Path]:
    home = tmp_path / "home"
    bindir = home / ".agent-worktrees" / "bin"
    conduct_dir = bindir / "conduct"
    conduct_dir.mkdir(parents=True)
    for name in c.KNOWN_FRAGMENTS:
        shutil.copy2(_SCRIPTS / "conduct" / name, conduct_dir / name)
    (conduct_dir / "retired-extra.md").write_text(
        "stale-extra-" + ("z" * 20_000), encoding="utf-8"
    )
    (conduct_dir / "keep.txt").write_text("unmanaged", encoding="utf-8")

    def output_text(value: str) -> str:
        if not windows_line_endings:
            return value
        placeholder = "\0"
        return (
            value.replace("\r\n", placeholder)
            .replace("\n", "\r\n")
            .replace(placeholder, "\r\n")
        )

    shim = tmp_path / "runtime-python.py"
    shim.write_text(
        f"""#!{sys.executable}
import os
import sys

args = sys.argv[1:]
def emit(value):
    sys.stdout.buffer.write(value.encode("utf-8"))

if args == ["-m", "agent_worktrees", "get", "project"]:
    print("harness")
elif "state-root" in args and "--conduct" in args:
    emit({output_text(definition)!r})
elif "related" in args and "--conduct" in args:
    emit({output_text(related)!r})
elif "history-digest" in args:
    emit({output_text(history)!r})
elif args[:2] == ["-m", "agent_worktrees.conduct"]:
    os.execv(sys.executable, [sys.executable, *args])
else:
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    if os.name == "nt":
        launcher = tmp_path / "runtime-python.cmd"
        launcher.write_text(
            f'@"{sys.executable}" "{shim}" %*\n',
            encoding="utf-8",
        )
    else:
        shim.chmod(0o755)
        launcher = shim
    (bindir / "resolve-runtime.sh").write_text(
        f"AW_PY={shlex.quote(str(launcher))}\n", encoding="utf-8"
    )
    ps_path = str(launcher).replace("'", "''")
    (bindir / "resolve-runtime.ps1").write_text(
        f"$AwPy = '{ps_path}'\n", encoding="utf-8"
    )
    return home, conduct_dir


def _run(script: Path, home: Path, shell: str, *args: str) -> str:
    env = os.environ.copy()
    env.update({"HOME": str(home), "USERPROFILE": str(home)})
    is_bash = Path(shell).name.lower() in {"bash", "bash.exe"}
    command = [shell, str(script), *args] if is_bash else [
        shell, "-NoProfile", "-File", str(script), *args
    ]
    result = subprocess.run(
        command,
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return result.stdout.strip()


def _require_shell(shell: str | None, name: str) -> str:
    if shell is None:
        pytest.skip(f"{name} is unavailable")
    return shell


def _stress_hook_fixture(tmp_path):
    long_path = "/state/" + ("deep-unicode-深/" * 40)
    definition = (
        f"**The user's state repo** is `{long_path}`. STATE-ROOT-REQUIRED."
    )
    related = (
        "## Related-repository guidance\n\n"
        "- `agent-worktrees related resolve <repo>`\n"
        "- `agent-worktrees related show <repo>`\n"
        "- `agent-worktrees related doctor`\n"
        "- 42 directional related entries: `agent-worktrees related list`.\n"
        + ("Full directional guidance. " * 10)
    ).rstrip()
    history = "Oversized history:\n" + "\n".join(
        f"- history-{i}-" + ("🙂" * 180) for i in range(20)
    )
    home, conduct_dir = _prepare_hook_home(
        tmp_path, definition=definition, related=related, history=history
    )
    return definition, related, history, home, conduct_dir


@pytest.mark.skipif(
    os.name == "nt" or _bash() is None,
    reason="POSIX conduct fixtures require a POSIX host",
)
def test_posix_hook_enforces_budget_priority(tmp_path):
    definition, related, _history, home, conduct_dir = _stress_hook_fixture(tmp_path)

    bash_output = _run(
        _SCRIPTS / "session-conduct.sh",
        home,
        _require_shell(_bash(), "Bash"),
    )
    data = json.loads(bash_output)
    context = data["additionalContext"]
    assert c.runtime_units(bash_output) <= c.MAX_OUTPUT_CHARS
    assert definition in context
    for name in c.KNOWN_FRAGMENTS:
        required = (conduct_dir / name).read_text(encoding="utf-8").strip()
        assert required in context
    assert related in context
    assert c.UNKNOWN_OMITTED in context
    assert "stale-extra-" not in context
    assert c.HISTORY_TRUNCATED in context
    assert "🙂" in context


def _powershell() -> str | None:
    return shutil.which("pwsh") or (
        shutil.which("powershell") if os.name == "nt" else None
    )


@pytest.mark.skipif(_powershell() is None, reason="PowerShell is unavailable")
def test_powershell_hook_enforces_budget_and_normalizes_windows_newlines(tmp_path):
    definition = "definition-one\rdefinition-two\nSTATE-ROOT-REQUIRED"
    related = "related-one\r\nrelated-two"
    history = (
        "This worktree's recent history (most recent last):\r\n"
        "- newest-history\r\n\r\n"
        "Worktree succession: retain this complete instruction."
    )
    home, _ = _prepare_hook_home(
        tmp_path,
        definition=definition,
        related=related,
        history=history,
        windows_line_endings=True,
    )

    output = _run(
        _SCRIPTS / "session-conduct.ps1",
        home,
        _require_shell(_powershell(), "PowerShell"),
    )
    context = json.loads(output)["additionalContext"]
    assert c.runtime_units(output) <= c.MAX_OUTPUT_CHARS
    assert "\r" not in context
    assert "definition-one\ndefinition-two" in context
    assert "related-one\nrelated-two" in context
    assert "- newest-history" in context
    assert "Worktree succession: retain this complete instruction." in context


@pytest.mark.skipif(
    os.name == "nt" or _bash() is None or _powershell() is None,
    reason="Bash/PowerShell parity requires a POSIX host",
)
def test_bash_and_powershell_hooks_have_identical_payloads(tmp_path):
    _definition, _related, _history, home, _ = _stress_hook_fixture(tmp_path)
    bash_output = _run(
        _SCRIPTS / "session-conduct.sh",
        home,
        _require_shell(_bash(), "Bash"),
    )
    ps_output = _run(
        _SCRIPTS / "session-conduct.ps1",
        home,
        _require_shell(_powershell(), "PowerShell"),
    )
    assert ps_output == bash_output


@pytest.mark.skipif(
    os.name == "nt" or _bash() is None,
    reason="POSIX conduct fixtures require a POSIX host",
)
def test_aggregate_mode_is_compact_and_keeps_binding_invariants(tmp_path):
    definition, related, history, _home, _ = _stress_hook_fixture(tmp_path)
    history += (
        "\n\nActive effort: `efforts/active/example/README.md`; participant "
        "`Driver`; slice `Phase 2`. Load that effort first."
        "\n\nWorktree succession: the current head session is abc123 (active). "
        "If that is not you, coordinate rather than starting parallel work."
    )
    home, _ = _prepare_hook_home(
        tmp_path / "aggregate",
        definition=definition,
        related=related,
        history=history,
    )
    output = _run(
        _SCRIPTS / "session-conduct.sh",
        home,
        _require_shell(_bash(), "Bash"),
        "--aggregate",
    )
    context = json.loads(output)["additionalContext"]

    assert context.startswith("[owner: agent-worktrees@")
    assert definition[:80] in context
    assert "active-effort assignment" in context
    assert "succession head as authoritative" in context
    assert "`agent-worktrees status` is the disposition source" in context
    assert "`agent-worktrees:worktree`" in context
    assert "Active effort:" in context
    assert "Worktree succession:" in context
    assert len(context.encode("utf-8")) <= c.AGGREGATE_MAX_CONTEXT_BYTES

    powershell = _powershell()
    if powershell:
        assert _run(
            _SCRIPTS / "session-conduct.ps1",
            home,
            powershell,
            "--aggregate",
        ) == output


def test_related_is_omitted_only_after_history(tmp_path):
    conduct_dir = tmp_path / "conduct"
    conduct_dir.mkdir()
    for name in c.KNOWN_FRAGMENTS:
        shutil.copy2(_SCRIPTS / "conduct" / name, conduct_dir / name)
    related = "RELATED-FULL-" + ("r" * 10_000)
    history = "HISTORY-FULL-" + ("h" * 10_000)

    payload = c.assemble_payload(
        conduct_dir,
        "STATE-ROOT-REQUIRED",
        related,
        history,
    )
    context = json.loads(payload)["additionalContext"]
    assert c.runtime_units(payload) <= c.MAX_OUTPUT_CHARS
    assert "STATE-ROOT-REQUIRED" in context
    assert related not in context
    assert c.RELATED_OMITTED in context
    assert "HISTORY-FULL-" in context or c.HISTORY_TRUNCATED in context


def test_history_truncation_keeps_complete_succession_and_newest_lines(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(c, "_installed_package_version", lambda: "1.2.3")
    history = (
        "This worktree's recent history (most recent last):\n"
        + "\n".join(f"- history-{i}-" + ("x" * 100) for i in range(12))
        + "\n\nWorktree succession: this complete instruction must survive."
    )
    payload = c.assemble_payload(
        tmp_path / "missing-conduct",
        "mandatory",
        "",
        history,
        max_chars=390,
    )
    context = json.loads(payload)["additionalContext"]

    assert c.runtime_units(payload) <= 390
    assert c.HISTORY_TRUNCATED in context
    assert "Worktree succession: this complete instruction must survive." in context
    assert "history-11-" in context
    assert "history-0-" not in context
    assert context.count("Worktree succession:") == 1
    assert context.split("Worktree succession:", 1)[1] == (
        " this complete instruction must survive."
    )


def test_history_truncation_keeps_complete_effort_and_succession_orientation(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(c, "_installed_package_version", lambda: "1.2.3")
    history = (
        "This worktree's recent history (most recent last):\n"
        + "\n".join(f"- history-{i}-" + ("x" * 100) for i in range(12))
        + "\n\nActive effort: `efforts/active/example/README.md`; participant "
        "`Driver`; slice `Phase 2`. Load that effort first."
        + "\n\nWorktree succession: this complete instruction must survive."
    )
    payload = c.assemble_payload(
        tmp_path / "missing-conduct",
        "mandatory",
        "",
        history,
        max_chars=540,
    )
    context = json.loads(payload)["additionalContext"]

    assert c.runtime_units(payload) <= 540
    assert "Active effort: `efforts/active/example/README.md`" in context
    assert "Worktree succession: this complete instruction must survive." in context
    assert "history-11-" in context
    assert "history-0-" not in context


def test_custom_omission_marker_wins_over_related_marker(tmp_path):
    conduct_dir = tmp_path / "conduct"
    conduct_dir.mkdir()
    (conduct_dir / "account-conduct.md").write_text(
        "mandatory-" + ("m" * 160), encoding="utf-8"
    )
    (conduct_dir / "custom.md").write_text("custom", encoding="utf-8")

    payload = c.assemble_payload(
        conduct_dir,
        "",
        "related-" + ("r" * 1_000),
        "",
        max_chars=320,
    )
    context = json.loads(payload)["additionalContext"]
    assert c.runtime_units(payload) <= 320
    assert c.UNKNOWN_OMITTED in context
    assert c.RELATED_OMITTED not in context


def test_nonempty_payload_begins_with_exact_installed_owner_marker(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(c, "_installed_package_version", lambda: "1.2.3-dev4")
    payload = c.assemble_payload(
        tmp_path / "missing-conduct",
        "definition",
        "",
        "",
    )
    context = json.loads(payload)["additionalContext"]
    assert context.splitlines()[0] == "[owner: agent-worktrees@1.2.3-dev4]"
    assert context == "[owner: agent-worktrees@1.2.3-dev4]\n\ndefinition"


def test_owner_version_fallback_is_safe_and_well_formed(monkeypatch):
    def fail(_name):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(c.metadata, "version", fail)
    assert c._owner_marker() == "[owner: agent-worktrees@unknown]"
    monkeypatch.setattr(c.metadata, "version", lambda _name: "bad]\nvalue")
    assert c._owner_marker() == "[owner: agent-worktrees@unknown]"


def test_related_skill_distinguishes_conduct_count_from_list_enumeration():
    skill = (
        _PLUGIN / "skills" / "agent-worktrees-related" / "SKILL.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(skill.split())
    assert "conduct output itself shows the directional-entry count" in normalized
    assert (
        "`<agent-worktrees catalog argv[0]> related list` enumerates those entries"
        in normalized
    )
    assert "list shows" not in normalized


def test_worktree_skill_distinguishes_session_relay_from_objective_completion():
    skill = (
        _PLUGIN / "skills" / "worktree" / "SKILL.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(skill.split())

    assert "The Worktree Owns the Objective; Sessions Are Relay Legs" in skill
    assert "Consuming a handoff starts a new relay leg" in normalized
    assert "A single session may consume a handoff" in normalized
    assert (
        "A consumed or completed handoff task, a completed phase, a clean git "
        "status, or a merged PR is not proof" in normalized
    )
    assert (
        "Handoff consumed or phase/PR landed, but the parent objective has "
        "actionable work" in normalized
    )


def test_assembler_forces_utf8_stdout(tmp_path):
    env = os.environ.copy()
    env.update({
        "AW_CONDUCT_DEFINITION": "Unicode conduct 🙂",
        "PYTHONIOENCODING": "cp1252",
    })
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent_worktrees.conduct",
            str(tmp_path / "missing-conduct"),
        ],
        env=env,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    payload = json.loads(result.stdout)
    context = payload["additionalContext"]
    assert re.fullmatch(
        r"\[owner: agent-worktrees@[A-Za-z0-9][A-Za-z0-9._+-]*\]",
        context.splitlines()[0],
    )
    assert context.endswith("Unicode conduct 🙂")
