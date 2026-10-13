"""Static shape + tracked-executable checks for the stable public
install-surface proxy (install/dispatch/bootstrap.{ps1,sh}) -- see
docs/patterns/standalone-service-layout.md's "public-install-surface
indirection" and issue #6419.

These do not exercise the real fetch or the real bootstrap it proxies to
(no network, no git clone) -- they assert the proxy's structural contract:
it forwards every argument, prefers a local sibling copy of the real
bootstrap before falling back to a network fetch, and only the real path it
resolves (not the published URL) needs to change on a future move.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PROXY_DIR = REPO / "install" / "dispatch"


def test_posix_proxy_is_tracked_executable() -> None:
    path = "install/dispatch/bootstrap.sh"
    result = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--stage", "--", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        import pytest

        pytest.skip("Git index metadata is unavailable")
    if not result.stdout.strip():
        raise AssertionError(f"{path} is not tracked by git")
    mode = result.stdout.split(maxsplit=1)[0]
    assert mode == "100755"


def test_proxy_sh_forwards_args_and_resolves_local_before_network() -> None:
    text = (PROXY_DIR / "bootstrap.sh").read_text(encoding="utf-8")
    assert 'REAL_PATH="services/dispatch/bootstrap.sh"' in text
    # Local sibling copy (a cloned checkout) is preferred over a network fetch.
    assert "LOCAL_REAL" in text
    assert 'exec bash "$LOCAL_REAL" "$@"' in text
    # Network fallback forwards args too and does not reimplement any real
    # install logic -- it only fetches and execs the real script.
    assert 'exec bash "$TMP_FILE" "$@"' in text


def test_proxy_ps1_forwards_args_and_resolves_local_before_network() -> None:
    text = (PROXY_DIR / "bootstrap.ps1").read_text(encoding="utf-8")
    assert "'services', 'dispatch', 'bootstrap.ps1'" in text
    assert "$LocalReal" in text
    assert "@Args" in text


def test_proxy_scripts_default_to_canonical_repo() -> None:
    for name in ("bootstrap.sh", "bootstrap.ps1"):
        text = (PROXY_DIR / name).read_text(encoding="utf-8")
        assert "ThomasMichon" in text
        assert "copilot-extensions" in text


def test_plugin_installer_points_at_the_proxy_not_the_real_bootstrap() -> None:
    """The self-provisioning binstub in plugins/agent-dispatch must fetch the
    STABLE install/dispatch/ proxy -- never the real services/dispatch/
    bootstrap directly -- so a future services/dispatch move needs no change
    here."""
    ps1 = (REPO / "plugins" / "agent-dispatch" / "scripts" / "install.ps1").read_text(encoding="utf-8")
    sh = (REPO / "plugins" / "agent-dispatch" / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "install/dispatch/bootstrap.ps1" in ps1
    assert "install/dispatch/bootstrap.sh" in sh
