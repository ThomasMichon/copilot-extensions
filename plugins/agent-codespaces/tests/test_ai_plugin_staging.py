"""Tests for the repo-own ``.ai`` plugin resolution lane (ai_plugin_staging)."""

from __future__ import annotations

import base64
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

from agent_codespaces import ai_plugin_staging as aps


def test_find_plugin_resolve_pkg_locates_installed_package():
    pkg = aps.find_plugin_resolve_pkg()
    assert pkg is not None
    assert (pkg / "__init__.py").is_file()


def test_tar_pkg_b64_roundtrips_with_package_arcname():
    pkg = aps.find_plugin_resolve_pkg()
    assert pkg is not None
    b64 = aps.tar_pkg_b64(pkg)
    buf = io.BytesIO(base64.b64decode(b64))
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        names = tf.getnames()
    # Extracts under a top-level ``plugin_resolve/`` dir so ``<dest>`` on sys.path
    # makes ``import plugin_resolve`` work on the target.
    assert any(n == "plugin_resolve" or n.startswith("plugin_resolve/") for n in names)
    assert any(n.endswith("plugin_resolve/__init__.py") for n in names)


def test_build_resolve_command_quotes_and_embeds_repo():
    cmd = aps.build_resolve_command("QkFTRTY0", "/workspaces/odsp-web")
    assert "base64 -d" in cmd and "tar -xzf" in cmd
    assert "/workspaces/odsp-web" in cmd
    assert "python3" in cmd and "command -v python" in cmd
    # Degrades to an empty result marker rather than erroring.
    assert aps.RESULT_MARKER in cmd


def test_parse_resolve_result_extracts_marker_line():
    payload = json.dumps({
        "resolved": {"a@m": "/workspaces/r/.ai/a", "b@m": "/workspaces/r/.ai/b"},
        "unresolved": ["c@remote"],
    })
    out = f"login banner noise\n{aps.RESULT_MARKER}{payload}\ntrailing\n"
    resolved, unresolved = aps.parse_resolve_result(out)
    assert sorted(resolved) == ["/workspaces/r/.ai/a", "/workspaces/r/.ai/b"]
    assert unresolved == ["c@remote"]


def test_parse_resolve_result_failsafe_on_garbage():
    assert aps.parse_resolve_result("no marker here") == ([], [])
    assert aps.parse_resolve_result(f"{aps.RESULT_MARKER}not-json") == ([], [])
    assert aps.parse_resolve_result("") == ([], [])


def _make_ai_repo(root: Path) -> None:
    """A synthetic repo with a `.claude/settings.json` + `.ai` local marketplace.

    One local plugin (``atomic`` -> ``atomic-dir``) and one remote-marketplace
    plugin (``flt@remote``) that must NOT resolve to a dir.
    """
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.json").write_text(json.dumps({
        "extraKnownMarketplaces": {
            "odsp-web-plugins": {"source": {"source": "directory", "path": "./.ai"}},
            "remote": {"source": {"source": "github", "repo": "org/x"}},
        },
        "enabledPlugins": {
            "atomic@odsp-web-plugins": True,
            "flt@remote": True,
        },
    }))
    ai = root / ".ai"
    (ai / ".claude-plugin").mkdir(parents=True)
    (ai / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "odsp-web-plugins",
        "plugins": [{"name": "atomic", "source": "./atomic-dir"}],
    }))
    plug = ai / "atomic-dir" / ".claude-plugin"
    plug.mkdir(parents=True)
    (plug / "plugin.json").write_text(json.dumps({"name": "atomic", "version": "1.0.0"}))


def test_end_to_end_driver_resolves_local_skips_remote(tmp_path: Path):
    """Ship-and-run the resolver driver portably: extract the shipped package and
    run the exact driver via this interpreter against a synthetic `.ai` repo. The
    local plugin dir must resolve; the remote-marketplace one must not."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_ai_repo(repo)

    pkg = aps.find_plugin_resolve_pkg()
    assert pkg is not None
    # Extract the shipped tar exactly as the remote command would.
    dest = tmp_path / "shipped"
    dest.mkdir()
    buf = io.BytesIO(base64.b64decode(aps.tar_pkg_b64(pkg)))
    with tarfile.open(fileobj=buf, mode="r:gz") as tf:
        tf.extractall(dest)
    # Run the embedded driver verbatim (portable: real paths, this interpreter).
    proc = subprocess.run(
        [sys.executable, "-c", aps._DRIVER, str(dest), str(repo)],
        capture_output=True, text=True, timeout=60,
    )
    resolved, unresolved = aps.parse_resolve_result(proc.stdout)
    assert len(resolved) == 1
    assert resolved[0].replace("\\", "/").endswith("/.ai/atomic-dir")
    assert "flt@remote" in unresolved


def test_full_bash_command_runs_on_posix(tmp_path: Path):
    """The full remote bash command (mktemp+tar+python) end-to-end -- POSIX only
    (Windows git-bash hands POSIX temp paths to a native python, a test-env-only
    path mismatch that does not occur on a Linux CodeSpace/container)."""
    import os
    import shutil

    if os.name != "posix" or shutil.which("bash") is None:  # pragma: no cover
        import pytest
        pytest.skip("full-bash command test is POSIX-only")
    repo = tmp_path / "repo"
    repo.mkdir()
    _make_ai_repo(repo)
    pkg = aps.find_plugin_resolve_pkg()
    assert pkg is not None
    command = aps.build_resolve_command(aps.tar_pkg_b64(pkg), str(repo))
    proc = subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True, timeout=60,
    )
    resolved, unresolved = aps.parse_resolve_result(proc.stdout)
    assert len(resolved) == 1
    assert resolved[0].endswith("/.ai/atomic-dir")
    assert "flt@remote" in unresolved
