"""Static shape checks for the Copilot-independent bootstrap scripts.

These do not exercise the real fetch (no network, no git clone) -- they
assert the scripts' structural contract: they fetch a payload with no
Copilot/marketplace dependency, then hand off to the EXISTING
`plugins/agent-dispatch/scripts/install.{sh,ps1}` installer rather than
reimplementing any of its venv/binstub/service logic.
"""

from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def test_bootstrap_sh_execs_into_existing_installer() -> None:
    text = (PACKAGE_ROOT / "bootstrap.sh").read_text(encoding="utf-8")
    assert "plugins/agent-dispatch/scripts/install.sh" in text
    assert 'exec bash "$payload_parent/plugins/agent-dispatch/scripts/install.sh" "$@"' in text
    # No Copilot/marketplace invocation (prose references to the plugin pipe
    # this script deliberately bypasses are fine; an actual `copilot plugin`
    # command invocation is not).
    assert "copilot plugin install" not in text
    assert "copilot plugin path" not in text


def test_bootstrap_ps1_execs_into_existing_installer() -> None:
    text = (PACKAGE_ROOT / "bootstrap.ps1").read_text(encoding="utf-8")
    assert "plugins\\agent-dispatch\\scripts\\install.ps1" in text
    assert "@Args" in text
    assert "copilot plugin install" not in text
    assert "copilot plugin path" not in text


def test_bootstrap_scripts_default_to_canonical_repo() -> None:
    for name in ("bootstrap.sh", "bootstrap.ps1"):
        text = (PACKAGE_ROOT / name).read_text(encoding="utf-8")
        assert "github.com/ThomasMichon/copilot-extensions" in text
