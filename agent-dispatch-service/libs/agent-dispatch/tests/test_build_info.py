"""Version resolution + build-info stamping (replaces the old drift guard).

`agent_dispatch.__version__` is now *derived* -- there is no hand-maintained
constant to drift. `pyproject.toml` is the single source of truth: at deploy
time `scripts/stamp_build_info.py` bakes it (plus git provenance) into
`_build_info.py`, and `_resolve_version()` prefers that, then
`importlib.metadata`, then a dev sentinel.
"""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import agent_dispatch


def _source_plugin_root() -> Path:
    try:
        payload = importlib.metadata.distribution("agent-dispatch").read_text("direct_url.json")
    except (importlib.metadata.PackageNotFoundError, FileNotFoundError):
        payload = None
    if payload:
        try:
            data = json.loads(payload)
        except (TypeError, ValueError):
            data = None
        url = str((data or {}).get("url") or "").strip()
        if url:
            candidate = _path_from_file_url(url)
            if candidate and candidate.is_dir() and (candidate / "scripts" / "stamp_build_info.py").is_file():
                return candidate.resolve()
    return Path(agent_dispatch.__file__).resolve().parent.parent.parent


def _path_from_file_url(url: str) -> Path | None:
    parsed = urlparse(url)
    if parsed.scheme != "file":
        return None
    path = url2pathname(parsed.path)
    if parsed.netloc and parsed.netloc.casefold() != "localhost":
        return Path(f"//{parsed.netloc}{path}")
    return Path(path)


def test_version_matches_packaged_metadata_when_unstamped():
    """The committed `_build_info.py` ships an empty version, so a normal
    (editable/CI) install resolves `__version__` from the packaged metadata --
    i.e. straight from pyproject. No manual bump, no drift."""
    packaged = importlib.metadata.version("agent-dispatch")
    # __version__ is resolved at import; recompute to be explicit about the path.
    assert agent_dispatch._resolve_version() == packaged
    assert agent_dispatch.__version__ == packaged


def test_build_info_placeholder_is_empty_so_it_falls_through():
    """The repo copy must not pin a literal version -- an un-stamped checkout
    has to fall through to metadata, not report a stale build-info value."""
    from agent_dispatch._build_info import BUILD_INFO

    assert BUILD_INFO["version"] == ""


def test_path_from_file_url_preserves_unc_authority():
    assert _path_from_file_url("file://server/share/agent-dispatch") == Path(
        "//server/share/agent-dispatch"
    )


def test_stamp_build_info_writes_pyproject_version(tmp_path):
    """The deploy-time stamper reads the version from pyproject.toml and writes
    a valid `_build_info.py` that `_resolve_version()` would then prefer."""
    import runpy

    # Minimal fake plugin dir with a pyproject the stamper can read.
    plugin_dir = tmp_path / "plugins" / "agent-dispatch"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "agent-dispatch"\nversion = "9.9.9-dev1"\n', encoding="utf-8"
    )
    pkg_dir = tmp_path / "site" / "agent_dispatch"
    pkg_dir.mkdir(parents=True)

    stamper = _source_plugin_root() / "scripts" / "stamp_build_info.py"
    mod = runpy.run_path(str(stamper))
    out = mod["stamp"](pkg_dir, plugin_dir, None)

    assert out.exists()
    ns: dict = {}
    exec(out.read_text(encoding="utf-8"), ns)  # noqa: S102 -- generated, trusted test artifact
    assert ns["BUILD_INFO"]["version"] == "9.9.9-dev1"
    assert ns["BUILD_INFO"]["commit"] == "unknown"  # no --git-dir given
