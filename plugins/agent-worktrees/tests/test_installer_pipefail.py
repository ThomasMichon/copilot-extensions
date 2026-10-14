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
the pipefail/errexit behaviour is unobservable. The explicit native tier probes
Bash once per session through a lazy fixture
and skips the semantic tests when it is non-conformant.
The pure-text drift guard (:func:`test_install_sh_guards_value_greps`) needs no
bash and runs everywhere. Input is piped in (not read from a temp file) so the
tests carry no Windows-vs-POSIX path-translation assumptions either.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from native_shell_support import run_headless

_INSTALL_SH = (
    Path(__file__).resolve().parents[1] / "scripts" / "install.sh"
)

@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
def test_value_grep_pattern_survives_missing_key(native_bash: str):
    """The guarded extraction pattern must exit 0 (empty) when the key is
    absent, even under `set -euo pipefail`."""
    script = (
        "set -euo pipefail\n"
        "_db=$(printf 'some_other_key: value\\n' "          # no default_branch:
        "| grep 'default_branch:' | head -1 "
        "| sed 's/.*default_branch:\\s*//' || true)\n"
        'echo "db=[$_db]"\n'
    )
    r = run_headless([native_bash, "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, f"pipeline aborted: {r.stderr}"
    assert "db=[]" in r.stdout


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("process")
def test_unguarded_pattern_would_abort(native_bash: str):
    """Sanity check that the bug is real: the *unguarded* pattern aborts under
    pipefail when the key is absent (guards the regression's premise)."""
    script = (
        "set -euo pipefail\n"
        "_db=$(printf 'some_other_key: value\\n' "
        "| grep 'default_branch:' | head -1 "
        "| sed 's/.*default_branch:\\s*//')\n"
        'echo "reached=[$_db]"\n'
    )
    r = run_headless([native_bash, "-c", script], capture_output=True, text=True)
    assert r.returncode != 0
    assert "reached=" not in r.stdout


def test_install_sh_guards_value_greps():
    """Drift guard: the install.sh value-extraction greps keep their `|| true`
    so a missing config key can't abort the update before sibling modules.

    (``default_branch`` is no longer grep-extracted -- the projects.yaml write
    moved to the Python `register-project-entry` subcommand -- so only the
    ``anchor:`` REPO_DIR detection remains.)"""
    # Explicit UTF-8: this is a POSIX shell script, and relying on the
    # platform default text encoding (cp1252 on Windows) raises
    # UnicodeDecodeError on any non-ASCII byte the script happens to contain.
    text = _INSTALL_SH.read_text(encoding="utf-8")
    for key in ("anchor:",):
        line = next(
            ln for ln in text.splitlines()
            if f"grep '{key}'" in ln and "$(" in ln
        )
        assert line.rstrip().endswith("|| true)"), (
            f"unguarded value grep for {key!r}: {line.strip()}"
        )
