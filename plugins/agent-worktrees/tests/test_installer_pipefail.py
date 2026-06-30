"""Regression: install.sh value-extraction greps must not abort under pipefail.

`scripts/install.sh` runs under `set -euo pipefail`. Several `register_project`
helpers extract an optional value with `grep KEY file | head -1 | sed ...`.
When KEY is absent, `grep` exits 1 and -- under pipefail -- aborts the whole
`install.sh update` *before* "Update complete" and before the sibling-module
update step (which deploys agent-bridge). The result was that `<repo> update`
silently skipped every sibling runtime whenever the project config lacked a key
like `default_branch:`. The guards append `|| true` so a missing key yields an
empty string instead of aborting.

Runtime-environment handling: the two execution tests below assert a *bash*
semantic premise, so they need a shell that genuinely honors
`set -euo pipefail`. That holds on Linux, native WSL, macOS, and Git Bash. It
does **not** hold for the `bash` PATH-resolves-to on a bare Windows host: the
App-Execution-Alias stub at ``%LOCALAPPDATA%\\Microsoft\\WindowsApps\\bash.EXE``
launches the default WSL distro through a wrapper that neither propagates child
exit codes (every command appears to exit 0) nor reads Windows temp paths, so
the pipefail/errexit behaviour is unobservable. We probe the resolved bash once
and skip the semantic tests when it is non-conformant rather than fail on it.
The pure-text drift guard (:func:`test_install_sh_guards_value_greps`) needs no
bash and runs everywhere. Input is piped in (not read from a temp file) so the
tests carry no Windows-vs-POSIX path-translation assumptions either.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_INSTALL_SH = (
    Path(__file__).resolve().parents[1] / "scripts" / "install.sh"
)

_BASH = shutil.which("bash")


def _bash_honors_pipefail() -> bool:
    """True when the resolved ``bash`` aborts a command substitution whose
    pipeline fails under ``set -euo pipefail``.

    Mirrors the exact unguarded pattern install.sh used to carry: a no-match
    ``grep`` inside a ``grep | head | sed`` pipeline. A conformant POSIX bash
    aborts before the trailing ``echo`` (non-zero exit, no marker); the Windows
    WSL app-exec stub instead reports success and prints the marker, which is
    how we detect and skip it."""
    if _BASH is None:
        return False
    probe = (
        "set -euo pipefail\n"
        "_x=$(printf 'a\\n' | grep 'zzz' | head -1 | sed 's/a/b/')\n"
        "echo REACHED\n"
    )
    try:
        r = subprocess.run([_BASH, "-c", probe], capture_output=True,
                           text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode != 0 and "REACHED" not in r.stdout


_needs_conformant_bash = pytest.mark.skipif(
    not _bash_honors_pipefail(),
    reason="resolved bash does not honor `set -euo pipefail` "
           "(no bash, or the Windows WindowsApps WSL app-exec stub); "
           "install.sh's pipefail semantics are only observable under a real "
           "POSIX bash (Linux / native WSL / macOS / Git Bash)",
)


@_needs_conformant_bash
def test_value_grep_pattern_survives_missing_key():
    """The guarded extraction pattern must exit 0 (empty) when the key is
    absent, even under `set -euo pipefail`."""
    script = (
        "set -euo pipefail\n"
        "_db=$(printf 'some_other_key: value\\n' "          # no default_branch:
        "| grep 'default_branch:' | head -1 "
        "| sed 's/.*default_branch:\\s*//' || true)\n"
        'echo "db=[$_db]"\n'
    )
    r = subprocess.run([_BASH, "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, f"pipeline aborted: {r.stderr}"
    assert "db=[]" in r.stdout


@_needs_conformant_bash
def test_unguarded_pattern_would_abort():
    """Sanity check that the bug is real: the *unguarded* pattern aborts under
    pipefail when the key is absent (guards the regression's premise)."""
    script = (
        "set -euo pipefail\n"
        "_db=$(printf 'some_other_key: value\\n' "
        "| grep 'default_branch:' | head -1 "
        "| sed 's/.*default_branch:\\s*//')\n"
        'echo "reached=[$_db]"\n'
    )
    r = subprocess.run([_BASH, "-c", script], capture_output=True, text=True)
    assert r.returncode != 0
    assert "reached=" not in r.stdout


def test_install_sh_guards_value_greps():
    """Drift guard: the install.sh value-extraction greps keep their `|| true`
    so a missing config key can't abort the update before sibling modules."""
    text = _INSTALL_SH.read_text()
    for key in ("anchor:", "default_branch:"):
        line = next(
            ln for ln in text.splitlines()
            if f"grep '{key}'" in ln and "$(" in ln
        )
        assert line.rstrip().endswith("|| true)"), (
            f"unguarded value grep for {key!r}: {line.strip()}"
        )
