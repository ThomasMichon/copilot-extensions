"""Shared installer-engine manifest-kind regressions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ENGINE = Path(__file__).resolve().parents[3] / "libs" / "installer-engine" / "installer-engine.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.guard


def _shell_function_source(source: str, name: str, next_marker: str) -> str:
    start_marker = f"{name}() {{"
    assert source.count(start_marker) == 1, f"missing unique {start_marker!r}"
    start = source.index(start_marker)
    end = source.index(next_marker, start + len(start_marker))
    assert end > start, f"{next_marker!r} does not follow {start_marker!r}"
    return source[start:end]


def _engine_functions() -> str:
    source = ENGINE.read_text(encoding="utf-8")
    return "\n\n".join(
        [
            _shell_function_source(source, "source_kind_for_path", "\nwrite_deploy_manifest()"),
            _shell_function_source(source, "write_deploy_manifest", "\nwrite_simple_binstub()"),
        ]
    )


def _run_manifest_harness(tmp_path: Path, *, plugin_path: str, source_override: str | None) -> dict:
    if BASH is None:
        pytest.skip("bash unavailable")
    install_path = tmp_path / "install"
    install_path.mkdir()
    script = tmp_path / "harness.sh"
    override = source_override or ""
    script.write_text(
        "\n".join(
            [
                "set -euo pipefail",
                "_ok() { :; }",
                "_source_kind() { if [[ -n \"${COPILOT_PLUGIN_STAGED_FROM:-}\" ]]; then printf 'marketplace'; else printf 'local'; fi; }",
                "_git_info() { printf 'abc123 dev false\\n'; }",
                _engine_functions(),
                f"write_deploy_manifest svc plugin '{install_path}' '{plugin_path}' '/venv' '' '{override}' '1.2.3'",
            ]
        ),
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["COPILOT_PLUGIN_STAGED_FROM"] = "/home/example/.copilot/installed-plugins/copilot-extensions/plugin"
    proc = subprocess.run(
        [BASH, str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads((install_path / "deploy-manifest.json").read_text(encoding="utf-8"))


def test_manifest_kind_uses_staged_source_when_no_override(tmp_path: Path) -> None:
    manifest = _run_manifest_harness(
        tmp_path,
        plugin_path="/tmp/stage/plugin",
        source_override=None,
    )
    assert manifest["source"]["kind"] == "marketplace"


def test_manifest_kind_uses_override_path_classification(tmp_path: Path) -> None:
    manifest = _run_manifest_harness(
        tmp_path,
        plugin_path="/tmp/stage/plugin",
        source_override="/tmp/local/plugin",
    )
    assert manifest["source"]["kind"] == "local"
    assert manifest["source"]["path"] == "/tmp/local/plugin"
