"""Guard: bootstrap-check's background reconcile no longer gates on a
per-plugin opt-in (agent-bridge-unified-zdd-cutover Phase 0). The opt-in
existed because a raw reconcile could race a live daemon/session; now that
every reconcile-capable plugin's update path is always-ZDD (safe to run
unattended), the gate was removed rather than kept as a redundant consent
checkbox. This test proves:

* neither hook script still contains opt-in-gate text or a
  ``background_reconcile_<plugin>`` key reference, and
* a version drift reconciles unconditionally -- no
  ``.copilot-extensions/config.yaml`` required, and a stale
  ``background_reconcile_<plugin>`` key left over from before this change
  (e.g. set to ``false``) does not resurrect the old skip behavior.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_PLUGIN_NAME = _PLUGIN_ROOT.name
_PS1 = _PLUGIN_ROOT / "scripts" / "bootstrap-check.ps1"
_SH = _PLUGIN_ROOT / "scripts" / "bootstrap-check.sh"
# The legacy opt-in key, resolved to this plugin's literal name -- kept
# only to prove a stale leftover key from before this gate's removal is
# now inert (see test_sh_reconciles_even_with_stale_opt_in_key_present).
_OPT_IN_KEY = f"background_reconcile_{_PLUGIN_NAME}"


def _resolve_bash() -> str | None:
    """Resolve a REAL bash, not Windows' WSL-launcher `bash.exe` shim.

    On Windows, ``shutil.which("bash")`` can resolve to a WSL launcher stub
    under ``WindowsApps`` or the classic ``C:\\Windows\\System32\\bash.exe``
    -- neither is a real POSIX bash for this test's purposes (the System32
    launcher invokes an actual WSL distro, which runs this script in a
    different environment than the one under test). Prefer the real Git
    Bash location when present, then fall back to a PATH-resolved bash with
    both known WSL-launcher locations excluded.
    """
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    path = os.environ.get("PATH")
    if path:
        filtered = os.pathsep.join(
            part for part in path.split(os.pathsep)
            if "WindowsApps" not in part
            and part.rstrip("\\").lower() != r"c:\windows\system32"
        )
        bash = shutil.which("bash", path=filtered)
        if bash:
            return bash
    bash = shutil.which("bash")
    if bash and "WindowsApps" not in bash and "\\system32\\" not in bash.lower():
        return bash
    return None


_BASH = _resolve_bash()


def test_hook_scripts_exist():
    assert _PS1.is_file()
    assert _SH.is_file()


def test_ps1_has_no_opt_in_gate():
    text = _PS1.read_text(encoding="utf-8")
    assert "optInKey" not in text
    assert "optedIn" not in text
    assert "background reconcile SKIPPED" not in text


def test_sh_has_no_opt_in_gate():
    text = _SH.read_text(encoding="utf-8")
    assert "optInKey" not in text
    assert "optedIn" not in text
    assert "background reconcile SKIPPED" not in text


@pytest.mark.skipif(
    os.name != "nt" and _BASH is None, reason="native shell not available",
)
def test_sh_reconciles_without_any_opt_in_config(tmp_path):
    """No .copilot-extensions/config.yaml at all -- reconcile must still
    proceed unconditionally now that the opt-in gate is gone."""
    from .test_bootstrap_driver import bootstrap_fixture, run_hook

    plugin_dir, _, project, _, env = bootstrap_fixture(tmp_path, phase="drift")
    result = run_hook(plugin_dir, project, env)
    assert result.returncode == 0, result.stderr
    assert "SKIPPED" not in result.stderr, result.stderr
    assert "bootstrap provision supervision launched" in result.stderr, result.stderr
    assert (project / "handoff").is_file()


@pytest.mark.skipif(
    os.name != "nt" and _BASH is None, reason="native shell not available",
)
def test_sh_reconciles_even_with_stale_opt_in_key_present(tmp_path):
    """A leftover `background_reconcile_<plugin>: false` from before this
    gate's removal must not resurrect the old skip behavior -- the key is
    now inert."""
    from .test_bootstrap_driver import bootstrap_fixture, run_hook

    plugin_dir, _, project, _, env = bootstrap_fixture(tmp_path, phase="drift")
    (project / ".copilot-extensions").mkdir(parents=True)
    (project / ".copilot-extensions" / "config.yaml").write_text(
        f"{_OPT_IN_KEY}: false\n", encoding="utf-8"
    )
    result = run_hook(plugin_dir, project, env)
    assert result.returncode == 0, result.stderr
    assert "SKIPPED" not in result.stderr, result.stderr
    assert "bootstrap provision supervision launched" in result.stderr, result.stderr
    assert (project / "handoff").is_file()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
