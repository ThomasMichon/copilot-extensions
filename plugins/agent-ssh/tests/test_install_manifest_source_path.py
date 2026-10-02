"""Guard: the deploy-manifest's ``source.path`` must track the STABLE,
live installed-plugins payload -- never the throwaway per-invocation
``.install-stage/<ts>-<pid>/`` copy the self-stage prologue (install-contract
v4) creates and later reaps.

``bootstrap-check.ps1``/``.sh`` (the session-start version-drift reconcile)
read ``source.path``'s own ``pyproject.toml`` to decide whether the deployed
runtime is stale. If that path is the ephemeral stage copy, its version is
frozen at whatever it was staged with and can never reflect a later
``copilot plugin update`` -- so reconcile silently stops detecting drift
forever the moment a background/unattended reconcile happens to run staged
(empirically observed on a live machine: a watchdog's `agent-ssh restore-host`
kept failing with "cannot resolve the active agent-ssh payload" for over a
week because of exactly this). ``Get-SourceKind``/``_source_kind`` and the
``payload-dir`` marker already resolve the ORIGINAL marketplace path via
``COPILOT_PLUGIN_STAGED_FROM`` for the same reason (see their own comments);
the manifest's own ``source.path`` must use that same stable value.

See ``agent-worktrees``' own ``Write-V3Manifest`` for the reference pattern
this mirrors.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_SH = _PLUGIN_ROOT / "scripts" / "install.sh"


def test_install_scripts_exist():
    assert _PS1.is_file()
    assert _SH.is_file()


def test_ps1_manifest_source_path_prefers_staged_from():
    text = _PS1.read_text(encoding="utf-8")
    # The manifest's `source.path` must be built from a variable that itself
    # was resolved via `$env:COPILOT_PLUGIN_STAGED_FROM` -- not directly from
    # `$PluginDir`, which is the ephemeral stage copy when self-staged.
    assert re.search(
        r'\$sourcePath\s*=\s*if\s*\(\$env:COPILOT_PLUGIN_STAGED_FROM\)',
        text,
    ), "install.ps1 must resolve source.path via COPILOT_PLUGIN_STAGED_FROM"
    assert re.search(r"path\s*=\s*\(\$sourcePath\s*-replace", text), (
        "install.ps1's manifest must write source.path from $sourcePath, "
        "not directly from $PluginDir (the ephemeral stage copy)"
    )
    # Guard the regression directly: the manifest block must not read path
    # straight off $PluginDir.
    manifest_block_start = text.index("$manifest = [ordered]@{")
    manifest_block = text[manifest_block_start : manifest_block_start + 400]
    assert "path    = ($PluginDir" not in manifest_block


def test_sh_manifest_source_path_prefers_staged_from():
    text = _SH.read_text(encoding="utf-8")
    assert re.search(
        r'SOURCE_PATH="\$\{COPILOT_PLUGIN_STAGED_FROM:-\$PLUGIN_DIR\}"',
        text,
    ), "install.sh must resolve SOURCE_PATH via COPILOT_PLUGIN_STAGED_FROM"
    heredoc_start = text.index('cat > "$TMP" << EOF')
    heredoc = text[heredoc_start : heredoc_start + 400]
    assert '"path": "$SOURCE_PATH"' in heredoc
    assert '"path": "$PLUGIN_DIR"' not in heredoc


if __name__ == "__main__":
    import sys

    import pytest

    raise SystemExit(pytest.main([__file__, "-q", *sys.argv[1:]]))
