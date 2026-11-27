"""The setup feed reader and dependency installer use the same project context."""
from __future__ import annotations

from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from .test_containers_legacy_runtime_uninstall import cases
from .test_installer_bootstrap import seed as seed
from .test_uninstall_bootstrap import Removal, removal as removal

pytestmark = [
    pytest.mark.portfolio_tier("T2"), pytest.mark.effect("process"),
    pytest.mark.effect("filesystem"),
]
HEADLESS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@pytest.mark.parametrize("host", [host for host in cases.HOSTS if Path(host).stem == "bash"])
@pytest.mark.parametrize("materialized", [False, True], ids=["raw", "materialized"])
@pytest.mark.parametrize("scenario", [
    "caller-default", "payload-default", "relative-site", "absolute-site",
    "relative-config", "absolute-config",
])
def test_index_selection_uses_payload_config_and_preserves_caller_site(
    seed: Removal, host: str, scenario: str,
) -> None:
    home = Path(seed.env["HOME"])
    caller = home / "caller"
    caller.mkdir()
    environment = seed.env.copy()
    for name in tuple(environment):
        if name.startswith(("PIP_", "UV_")) and name not in (
            "UV_PYTHON_INSTALL_DIR", "UV_CACHE_DIR", "UV_OFFLINE", "UV_PYTHON_DOWNLOADS",
        ):
            environment.pop(name)
    environment.update(
        XDG_CONFIG_HOME=str(home / "user-config"),
        XDG_CONFIG_DIRS=str(home / "global-config"),
        XDG_DATA_HOME=str(home / "user-data"),
        XDG_DATA_DIRS=str(home / "global-data"),
    )
    environment.pop("VIRTUAL_ENV", None)
    environment.pop("CONDA_PREFIX", None)
    expected = "https://pip.example.invalid/simple"
    if scenario.endswith("default"):
        environment["PIP_INDEX_URL"] = expected
        configured = caller if scenario == "caller-default" else seed.payload
        (configured / "uv.toml").write_text(
            '[[index]]\nname="fixture"\nurl="https://uv.example.invalid/simple"\ndefault=true\n',
            encoding="utf-8",
        )
        if scenario == "payload-default":
            expected = "__absent__"
    elif scenario.endswith("config"):
        expected = "https://explicit.example.invalid/simple"
        explicit = caller / "pip.conf"
        explicit.write_text(f"[global]\nindex-url={expected}\n", encoding="utf-8")
        (seed.payload / "pip.conf").write_text(
            "[global]\nindex-url=https://unintended.example.invalid/simple\n", encoding="utf-8",
        )
        environment["PIP_CONFIG_FILE"] = (
            "pip.conf" if scenario == "relative-config" else str(explicit)
        )
    else:
        user = home / (
            "user-data/pip/pip.conf" if sys.platform == "darwin" else "user-config/pip/pip.conf"
        )
        user.parent.mkdir(parents=True)
        user.write_text("[global]\nindex-url=https://user.example.invalid/simple\n", encoding="utf-8")
        prefix = caller / "site"
        prefix.mkdir()
        (prefix / "pyvenv.cfg").write_text("fixture\n", encoding="utf-8")
        expected = "https://site.example.invalid/simple"
        (prefix / "pip.conf").write_text(f"[global]\nindex-url={expected}\n", encoding="utf-8")
        environment["VIRTUAL_ENV"] = "site" if scenario == "relative-site" else str(prefix)
    source = (seed.payload / "scripts/init.sh").read_text(encoding="utf-8")
    functions = []
    for name in ("_source_python", "_ensure_uv_index"):
        start = source.index(f"\n{name}() {{")
        functions.append(source[start:source.index("\n}\n", start) + 3])
    program = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(seed.payload / 'scripts/installer-bootstrap.sh'))}\n"
        "_initialize_containers_bootstrap Inspect\n"
        f"PLUGIN_DIR={shlex.quote(str(seed.payload))}\n"
        "_fail() { printf '%s\\n' \"$1\" >&2; }\n_step() { :; }\n"
        + "\n".join(functions)
        + '\n_ensure_uv_index\nprintf "%s\\n" "${UV_DEFAULT_INDEX-__absent__}"\n'
    )
    result = subprocess.run(
        [host, "--noprofile", "--norc", "-c", program],
        capture_output=True, text=True, env=environment, cwd=caller,
        timeout=40, creationflags=HEADLESS,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == expected
    assert not seed.poison.exists()
