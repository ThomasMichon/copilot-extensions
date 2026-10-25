"""Exercise the Bash uninstall arm without provisioning or host integration."""

import os
import shlex
from pathlib import Path

import pytest

from native_shell_support import run_headless


@pytest.mark.skipif(os.name == "nt", reason="Linux/POSIX uninstall regression")
def test_uninstall_branch_removes_runtime_preserves_state(tmp_path, native_bash):
    plugin = Path(__file__).resolve().parents[1]
    source = (plugin / "scripts/install.sh").read_text(encoding="utf-8")
    marker = "\n    uninstall)\n"
    assert source.count(marker) == 1
    body = source.split(marker, 1)[1].split("\n        ;;", 1)[0]

    home = tmp_path / "home"
    runtime = home / ".agent-worktrees"
    project = home / ".baseline"
    local_bin = home / ".local/bin"
    preserved = {
        project / "config.yaml": "project: baseline\n",
        project / "worktrees/witness/session.json": '{"sentinel": true}\n',
        runtime / "repos.yaml": "repos: []\n",
        home / ".tmux.conf": "# user-owned\n",
    }
    removed = [
        local_bin / "agent-worktrees",
        local_bin / "mark-session-complete",
        runtime / "deploy-manifest.json",
        runtime / "versions/witness/runtime",
        runtime / ".venv/runtime",
        runtime / "lib/package",
        runtime / "bin/pane-wrapper.sh",
    ]
    for path, content in preserved.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for path in removed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("owned fixture\n", encoding="utf-8")

    variables = {
        "HOME": home,
        "INSTALL_DIR": runtime,
        "LOCAL_BIN": local_bin,
        "BIN_DIR": runtime / "bin",
        "LIB_DIR": runtime / "lib",
        "VENV_DIR": runtime / ".venv",
        "LINK_DIR": runtime / ".venv",
        "VENV_PYTHON": tmp_path / "absent-python",
        "PLUGIN_DIR": tmp_path / "payload",
        "SCRIPT_DIR": tmp_path / "payload/scripts",
        "PROJECT_DIR": project,
        "PROJECT_NAME": "baseline",
        "SERVICE_NAME": "fixture",
        "HAS_PROJECT": "true",
        "REMOVE_CONFIG": "false",
        "VERSIONED_RUNTIME": "1",
    }
    setup = "\n".join(
        f"{name}={shlex.quote(str(value))}"
        for name, value in variables.items()
    )
    helpers = "\n".join(
        f"{name}() {{ :; }}"
        for name in (
            "header", "changed", "skipped", "ok", "warn",
            "remove_tabby_profile", "remove_legacy_binstubs",
            "remove_legacy_scripts",
        )
    )
    result = run_headless(
        [native_bash, "-c", "set -euo pipefail\n" + setup
         + "\n" + helpers + "\n" + body],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(not path.exists() for path in removed)
    assert not (runtime / "versions").exists()
    assert not (runtime / ".venv").exists()
    assert not (runtime / "lib").exists()
    for path, content in preserved.items():
        assert path.read_text(encoding="utf-8") == content
