"""Guard: vendored libs shared across plugins stay in sync (dotfiles #929).

Enforces ``tools/check-vendored-libs-sync.py`` in CI: every lib vendored in
>=2 plugins must have a byte-identical ``src/`` tree and matching version across
its copies -- so a sibling-plugin install can't silently downgrade a shared
package (the ImportError-on-CodeSpace-dispatch outage, #929). Also verifies the
checker actually catches drift (so the guard can't rot into a no-op).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_TOOL = _REPO / "tools" / "check-vendored-libs-sync.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("check_vendored_libs_sync", _TOOL)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tool_exists():
    assert _TOOL.exists(), f"missing {_TOOL}"


def test_real_repo_is_in_sync():
    mod = _load_tool()
    problems = mod.verify()
    assert problems == [], "vendored-lib drift:\n" + "\n".join(problems)


def _make_lib(root: Path, plugin: str, lib: str, *, body: str, version: str) -> None:
    d = root / plugin / "libs" / lib / "src" / lib.replace("-", "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / "__init__.py").write_text(body, encoding="utf-8")
    (root / plugin / "libs" / lib / "pyproject.toml").write_text(
        f'[project]\nname = "{lib}"\nversion = "{version}"\n', encoding="utf-8"
    )


def test_checker_detects_source_drift(tmp_path, monkeypatch):
    mod = _load_tool()
    plugins = tmp_path / "plugins"
    _make_lib(plugins, "plugin-a", "shared-lib", body="x = 1\n", version="0.1.0")
    _make_lib(plugins, "plugin-b", "shared-lib", body="x = 2\n", version="0.1.0")
    monkeypatch.setattr(mod, "PLUGINS_DIR", plugins)
    problems = mod.verify()
    assert any("DIFFERS" in p for p in problems), problems


def test_checker_detects_version_skew(tmp_path, monkeypatch):
    mod = _load_tool()
    plugins = tmp_path / "plugins"
    _make_lib(plugins, "plugin-a", "shared-lib", body="x = 1\n", version="0.1.0")
    _make_lib(plugins, "plugin-b", "shared-lib", body="x = 1\n", version="0.2.0")
    monkeypatch.setattr(mod, "PLUGINS_DIR", plugins)
    problems = mod.verify()
    assert any("version skew" in p for p in problems), problems


def test_checker_passes_identical_copies(tmp_path, monkeypatch):
    mod = _load_tool()
    plugins = tmp_path / "plugins"
    _make_lib(plugins, "plugin-a", "shared-lib", body="x = 1\n", version="0.1.0")
    _make_lib(plugins, "plugin-b", "shared-lib", body="x = 1\n", version="0.1.0")
    monkeypatch.setattr(mod, "PLUGINS_DIR", plugins)
    assert mod.verify() == []


def test_checker_ignores_singleton_libs(tmp_path, monkeypatch):
    mod = _load_tool()
    plugins = tmp_path / "plugins"
    _make_lib(plugins, "plugin-a", "lonely-lib", body="x = 1\n", version="0.1.0")
    monkeypatch.setattr(mod, "PLUGINS_DIR", plugins)
    assert mod.verify() == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
