"""Regression guard for ThomasMichon/copilot-extensions#2863.

A deployed `0.1.2-dev111` venv on lambda-core was missing
`agent_dispatch.procutil.agent_worktrees_environment`, even though every
verified copy of the actual dev111 *source* -- git HEAD and the staged
marketplace snapshot alike -- has always defined it correctly. The
mismatch traced to two compounding gaps in `install.sh`/`install.ps1`:

1. `uv pip install` on a local PATH source caches its build by source path,
   not source content, so a stale wheel built under a reused path/version
   can silently be served instead of a fresh build.
2. The post-install health gate only ran a bare `import agent_dispatch`,
   which never touches `agent_dispatch.embody` -- that module is imported
   lazily, only inside `spawn_factories.make_headless_spawn`, so a broken
   import there was invisible to `agent-dispatch health`/`daemon-status`
   and only surfaced on the first real spawn attempt.

These tests are static-text guards (following the precedent in
`test_install_sh_version_ordering.py`) ensuring both fixes stay in place:
the local-path install always forces a fresh build for agent-dispatch AND
its own local `[tool.uv.sources]` workspace path deps (equally vulnerable
local PATH sources -- #2863 separately flagged a same-class ImportError
against one of them, `agent-procutil`, in the self-update fallback path),
and the health gate actually exercises the lazily-imported `embody` chain
before a slot is activated or reported healthy.
"""

from __future__ import annotations

from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = (_PLUGIN_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
_INSTALL_PS1 = (_PLUGIN_ROOT / "scripts" / "install.ps1").read_text(encoding="utf-8")

_LOCAL_PATH_PACKAGES = (
    "agent-dispatch",
    "agent-procutil",
    "agent-zdd",
    "agent-dropin-registry",
    "agent-plugin-activation",
    "agent-plugin-resolve",
)


def test_sh_pip_install_forces_fresh_build():
    assert "_STALE_CACHE_REFRESH_PACKAGES" in _INSTALL_SH
    assert "--reinstall-package \"$pkg\" --refresh-package \"$pkg\"" in _INSTALL_SH
    array_start = _INSTALL_SH.index("_STALE_CACHE_REFRESH_PACKAGES=(")
    array_end = _INSTALL_SH.index(")", array_start)
    array_body = _INSTALL_SH[array_start:array_end]
    for pkg in _LOCAL_PATH_PACKAGES:
        assert pkg in array_body


def test_sh_health_gate_imports_embody():
    assert "import agent_dispatch, agent_dispatch.embody" in _INSTALL_SH
    # Both the pre-activation slot gate and the final post-swap verification
    # must exercise the lazily-imported chain, not just the top-level package.
    assert _INSTALL_SH.count("import agent_dispatch, agent_dispatch.embody") >= 2


def test_ps1_pip_install_forces_fresh_build():
    for pkg in _LOCAL_PATH_PACKAGES:
        assert f"'{pkg}'" in _INSTALL_PS1
    assert "StaleCacheRefreshPackages" in _INSTALL_PS1
    assert "--reinstall-package', $pkg, '--refresh-package', $pkg" in _INSTALL_PS1


def test_ps1_health_gate_imports_embody():
    assert "import agent_dispatch, agent_dispatch.embody" in _INSTALL_PS1
    assert _INSTALL_PS1.count("import agent_dispatch, agent_dispatch.embody") >= 2
