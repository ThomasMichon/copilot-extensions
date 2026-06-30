"""Regression: install.sh value-extraction greps must not abort under pipefail.

`scripts/install.sh` runs under `set -euo pipefail`. Several `register_project`
helpers extract an optional value with `grep KEY file | head -1 | sed ...`.
When KEY is absent, `grep` exits 1 and -- under pipefail -- aborts the whole
`install.sh update` *before* "Update complete" and before the sibling-module
update step (which deploys agent-bridge). The result was that `<repo> update`
silently skipped every sibling runtime whenever the project config lacked a key
like `default_branch:`. The guards append `|| true` so a missing key yields an
empty string instead of aborting.
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


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_value_grep_pattern_survives_missing_key(tmp_path: Path):
    """The guarded extraction pattern must exit 0 (empty) when the key is
    absent, even under `set -euo pipefail`."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("some_other_key: value\n")  # no default_branch:

    script = (
        "set -euo pipefail\n"
        f"_db=$(grep 'default_branch:' {cfg} 2>/dev/null "
        "| head -1 | sed 's/.*default_branch:\\s*//' || true)\n"
        'echo "db=[$_db]"\n'
    )
    r = subprocess.run([_BASH, "-c", script], capture_output=True, text=True)
    assert r.returncode == 0, f"pipeline aborted: {r.stderr}"
    assert "db=[]" in r.stdout


@pytest.mark.skipif(_BASH is None, reason="bash not available")
def test_unguarded_pattern_would_abort(tmp_path: Path):
    """Sanity check that the bug is real: the *unguarded* pattern aborts under
    pipefail when the key is absent (guards the regression's premise)."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("some_other_key: value\n")

    script = (
        "set -euo pipefail\n"
        f"_db=$(grep 'default_branch:' {cfg} 2>/dev/null "
        "| head -1 | sed 's/.*default_branch:\\s*//')\n"
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
