"""Production session-conduct assembly and wrapper parity tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from agent_worktrees import conduct as c


_PLUGIN = Path(__file__).resolve().parents[1]
_SCRIPTS = _PLUGIN / "scripts"


def _prepare_hook_home(
    tmp_path: Path,
    *,
    definition: str,
    related: str,
    history: str,
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

    shim = tmp_path / "runtime-python"
    shim.write_text(
        f"""#!{sys.executable}
import os
import sys

args = sys.argv[1:]
if args == ["-m", "agent_worktrees", "get", "project"]:
    print("harness")
elif "state-root" in args and "--conduct" in args:
    print({definition!r})
elif "related" in args and "--conduct" in args:
    print({related!r})
elif "history-digest" in args:
    print({history!r})
elif args[:2] == ["-m", "agent_worktrees.conduct"]:
    os.execv(sys.executable, [sys.executable, *args])
else:
    raise SystemExit(2)
""",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    (bindir / "resolve-runtime.sh").write_text(
        f"AW_PY={shlex.quote(str(shim))}\n", encoding="utf-8"
    )
    ps_path = str(shim).replace("'", "''")
    (bindir / "resolve-runtime.ps1").write_text(
        f"$AwPy = '{ps_path}'\n", encoding="utf-8"
    )
    return home, conduct_dir


def _run(script: Path, home: Path, shell: str) -> str:
    env = os.environ.copy()
    env.update({"HOME": str(home), "USERPROFILE": str(home)})
    command = ["bash", str(script)] if shell == "bash" else [
        shell, "-NoProfile", "-File", str(script)
    ]
    result = subprocess.run(
        command,
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_real_hooks_enforce_budget_priority_and_json_parity(tmp_path):
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

    bash_output = _run(_SCRIPTS / "session-conduct.sh", home, "bash")
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

    pwsh = shutil.which("pwsh")
    if pwsh:
        ps_output = _run(_SCRIPTS / "session-conduct.ps1", home, pwsh)
        assert ps_output == bash_output


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
        max_chars=260,
    )
    context = json.loads(payload)["additionalContext"]
    assert c.runtime_units(payload) <= 260
    assert c.UNKNOWN_OMITTED in context
    assert c.RELATED_OMITTED not in context


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
        check=True,
    )
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["additionalContext"] == "Unicode conduct 🙂"
