"""The native setup adapter retains the canonical governed pip configuration reader."""
from __future__ import annotations

import shlex
import shutil
import subprocess
import sys

import pytest

from .managed_command_fixture import HEADLESS, PLUGIN, REPO
from .test_cell_lifecycle_bootstrap import LifecycleFixture
from .test_installer_bootstrap import (
    bootstrap as bootstrap,
    installer_python as installer_python,
    lifecycle as lifecycle,
    pytestmark as pytestmark,
)


@pytest.mark.parametrize("host", ["bash"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("site_override", [False, True], ids=["user", "site"])
def test_native_index_adapter_retains_user_and_site_precedence(
    bootstrap: LifecycleFixture, site_override: bool,
) -> None:
    if bootstrap.support == bootstrap.payload / "scripts":
        helper = bootstrap.support / "installer_pip_index.py"
        assert helper.read_bytes() == (REPO / "libs/installer-engine" / helper.name).read_bytes()
    else:
        for helper in (REPO / "libs/installer-engine").glob("installer_*.py"):
            shutil.copy2(helper, bootstrap.support / helper.name)
    home = bootstrap.home
    environment = bootstrap.env.copy()
    for name in tuple(environment):
        if name.startswith(("PIP_", "UV_")) and name not in (
            "UV_PYTHON_INSTALL_DIR", "UV_CACHE_DIR", "UV_OFFLINE", "UV_PYTHON_DOWNLOADS",
        ):
            environment.pop(name)
    environment["UV_NO_CONFIG"] = "1"
    environment["XDG_CONFIG_HOME"] = str(home / "user-config")
    environment["XDG_CONFIG_DIRS"] = str(home / "global-config")
    environment["XDG_DATA_HOME"] = str(home / "user-data")
    environment["XDG_DATA_DIRS"] = str(home / "global-data")
    user_config = home / ("user-data/pip/pip.conf" if sys.platform == "darwin" else
                          "user-config/pip/pip.conf")
    user_config.parent.mkdir(parents=True)
    user_config.write_text("[global]\nindex-url = https://user.example.invalid/simple\n")
    prefix = home / "site"
    prefix.mkdir()
    (prefix / "pyvenv.cfg").write_text("fixture\n")
    environment["VIRTUAL_ENV"] = str(prefix)
    environment.pop("CONDA_PREFIX", None)
    expected = "https://user.example.invalid/simple"
    if site_override:
        expected = "https://site.example.invalid/simple"
        (prefix / "pip.conf").write_text(f"[global]\nindex-url = {expected}\n")
    source = (PLUGIN / "scripts/init.sh").read_text()
    selector_start = source.index("\n_source_python() {")
    selector_end = source.index("\n}\n", selector_start) + 3
    start = source.index("\n_ensure_uv_index() {")
    end = source.index("\n}\n", start) + 3
    program = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(bootstrap.payload / 'scripts/installer-bootstrap.sh'))}\n"
        "_initialize_machines_bootstrap Inspect\n"
        "_fail() { printf '%s\\n' \"$1\" >&2; }\n_step() { :; }\n"
        + source[selector_start:selector_end]
        + source[start:end]
        + "\n_ensure_uv_index\nprintf '%s\\n' \"$UV_DEFAULT_INDEX\"\n"
    )
    result = subprocess.run(
        [bootstrap.shell, "--noprofile", "--norc", "-c", program],
        capture_output=True, text=True, env=environment, cwd=home, timeout=40,
        creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == expected
    assert not bootstrap.poison.exists()
