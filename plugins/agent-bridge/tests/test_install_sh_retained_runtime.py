"""A fresh shell-installed Bridge slot can serve a retained native caller."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from tests.test_install_completed_slot_admission import _extract_sh

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None, reason="native POSIX shell")
@pytest.mark.timeout(60)
def test_shell_completion_retains_launchable_build_without_restamping(tmp_path: Path) -> None:
    root = tmp_path / "bridge"
    slot = root / "versions/1.2.3"
    text = (SCRIPTS / "install.sh").read_text(encoding="utf-8")
    functions = "\n".join(
        _extract_sh(name, text) for name in ("_versioned_slot_clean", "_versioned_mark_complete")
    )
    script = tmp_path / "construct.sh"
    script.write_text(
        "set -euo pipefail\n"
        + f"SCRIPT_DIR={shlex.quote(str(SCRIPTS))}\n"
        + f"INSTALL_DIR={shlex.quote(str(root))}\n"
        + 'LINK_DIR="$INSTALL_DIR/venv"\nSRC_VERSION=1.2.3\nVERSIONED_RUNTIME=1\n'
        + f"_bootstrap_python() {{ printf '%s\\n' {shlex.quote(sys.executable)}; }}\n"
        + "_payload_hash() { printf '%064d\\n' 0; }\n"
        + functions
        + "\n_versioned_slot_clean\n"
        + 'if [[ "$_RUNTIME_CONSTRUCTION_REUSE" != 1 ]]; then\n'
        + f'  {shlex.quote(sys.executable)} -m venv --without-pip "$INSTALL_DIR/versions/$SRC_VERSION"\n'
        + "fi\n_versioned_mark_complete\n",
        encoding="utf-8",
    )

    def construct() -> None:
        result = subprocess.run(
            ["bash", str(script)], capture_output=True, text=True, timeout=30, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    construct()
    launcher = slot / ".runtime-bootstrap/runtime_launcher.py"
    spec = importlib.util.spec_from_file_location("_shell_retained_owner", launcher)
    assert spec is not None and spec.loader is not None
    owner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(owner)
    identity = owner.runtime_identity(slot)
    marker = (slot / ".install-complete.json").read_bytes()
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import pathlib,sys\nprint(pathlib.Path(sys.prefix).samefile(sys.argv[1]))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [str(slot / "bin/python"), "-I", "-S", "-B", str(launcher),
         "--runtime-identity", identity, str(probe), str(slot)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "True"
    construct()
    assert owner.runtime_identity(slot) == identity
    assert (slot / ".install-complete.json").read_bytes() == marker
