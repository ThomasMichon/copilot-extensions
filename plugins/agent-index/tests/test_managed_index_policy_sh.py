"""Index's POSIX installer reads effective index policy without ambient pip."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest

from test_install_engine_adoption import ENGINE, function
from test_managed_bootstrap_reader_sh import _environment, prepared_helper as prepared_helper

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Native POSIX index policy")


@pytest.mark.parametrize("layout", ["raw", "packaged"])
@pytest.mark.parametrize("scenario", [
    "pip-environment", "pip-files", "uv-environment", "uv-config", "venv", "conda",
    "disabled", "malformed", "missing-reader", "unprepared",
])
def test_effective_index_policy_never_launches_ambient_pip(
    tmp_path: Path, layout: str, scenario: str,
    prepared_helper: tuple[Path, Path, Path],
) -> None:
    scripts = tmp_path / "scripts"
    shutil.copytree(ENGINE, scripts, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    engine = ENGINE if layout == "raw" else scripts
    if scenario == "missing-reader":
        # A missing packaged dependency must not fall through to a checkout copy.
        engine = scripts
        (scripts / "installer_pip_index.py").rename(scripts / "reader.unavailable")
    env = _environment(
        tmp_path, prepared_helper, "unprepared" if scenario == "unprepared" else "managed",
    )
    commands = Path(env["PATH"])
    for name in ("pip", "pip3"):
        command = commands / name
        command.write_text(
            '#!/bin/sh\nprintf pip-invoked > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        command.chmod(0o700)
    home = Path(env["HOME"])
    home.mkdir(parents=True, exist_ok=True)
    env.update(
        UV_NO_CONFIG="1", UV_CONFIG_FILE="", UV_NO_SYSTEM_CONFIG="1",
        XDG_CONFIG_HOME=str(tmp_path / "configuration"),
        XDG_CONFIG_DIRS=str(tmp_path / "system"), PIP_CONFIG_FILE=os.devnull,
    )
    for name in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "PIP_INDEX_URL", "UV_INDEX",
                 "UV_WORKING_DIR", "UV_WORKING_DIRECTORY", "UV_PROJECT", "UV_ISOLATED"):
        env.pop(name, None)
    expected = ""
    chosen = "https://selected.example/a%2Fb/simple"
    if scenario == "pip-environment":
        env["PIP_INDEX_URL"] = chosen
        expected = chosen
    elif scenario == "uv-environment":
        env["UV_DEFAULT_INDEX"] = chosen
        env["PIP_INDEX_URL"] = "https://not-selected.example/simple"
        expected = chosen
    elif scenario == "uv-config":
        project = tmp_path / "selected project"
        project.mkdir()
        (project / "uv.toml").write_text(
            f'[[index]]\nurl = "{chosen}"\ndefault = true\n', encoding="utf-8",
        )
        env.update(UV_NO_CONFIG="false", UV_WORKING_DIR=str(project))
        env["PIP_INDEX_URL"] = "https://not-selected.example/simple"
    elif scenario in {"venv", "conda"}:
        prefix = Path(env["VIRTUAL_ENV"] if scenario == "venv" else env["CONDA_PREFIX"])
        prefix.mkdir()
        if scenario == "venv":
            (prefix / "pyvenv.cfg").write_text("fixture\n", encoding="utf-8")
        else:
            (prefix / "conda-meta").mkdir()
        (prefix / "pip.conf").write_text(f"[global]\nindex-url = {chosen}\n", encoding="utf-8")
        env.pop("PIP_CONFIG_FILE")
        expected = chosen
    elif scenario in {"pip-files", "malformed"}:
        user = Path(env["XDG_CONFIG_HOME"]) / "pip/pip.conf"
        user.parent.mkdir(parents=True)
        user.write_text(
            "[global]\nindex-url = https://not-selected.example/simple\n", encoding="utf-8",
        )
        explicit = tmp_path / "explicit pip.conf"
        explicit.write_text(
            "not a configuration\n" if scenario == "malformed"
            else f"[global]\nindex-url = {chosen}\n",
            encoding="utf-8",
        )
        env["PIP_CONFIG_FILE"] = str(explicit)
        expected = chosen
    script = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(engine / 'installer-engine.sh'))}\n"
        + function("_bootstrap_python", "sh") + "\n"
        + function("_ensure_uv_index", "sh") + "\n"
        '_fail() { printf "%s\\n" "$*" >&2; }\n_step() { :; }\n'
        f'_ensure_uv_index {shlex.quote(str(prepared_helper[0]))} || exit $?\n'
        'printf "%s" "${UV_DEFAULT_INDEX:-}"\n'
    )
    result = subprocess.run(
        ["/bin/bash", "-c", script], cwd=tmp_path, env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
    )
    assert not Path(env["AMBIENT_MARKER"]).exists(), result.stdout + result.stderr
    assert not list((tmp_path / "unprepared-python").iterdir())
    if scenario in {"malformed", "missing-reader", "unprepared"}:
        assert result.returncode != 0
        assert not result.stdout
        diagnostic = {
            "malformed": "Cannot read package index configuration.",
            "missing-reader": "The index reader is not prepared",
            "unprepared": "requires prepared uv-managed Python",
        }[scenario]
        assert diagnostic in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout == expected


@pytest.mark.parametrize("entry", ["_ensure_runtime", "_install_engine"])
def test_index_policy_failure_stops_construction(
    tmp_path: Path, entry: str, prepared_helper: tuple[Path, Path, Path],
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "versioned_runtime.py").write_text(
        "import sys\n"
        "assert 'construction-shell' in sys.argv\n"
        "print('_runtime_construction_begin() { _RUNTIME_CONSTRUCTION_REUSE=0; };')\n",
        encoding="utf-8",
    )
    env = _environment(tmp_path, prepared_helper, "managed")
    mkdir = shutil.which("mkdir")
    assert mkdir
    (Path(env["PATH"]) / "mkdir").symlink_to(mkdir)
    marker = tmp_path / "construction-ran"
    definitions = "\n".join(function(name, "sh") for name in (
        "_bootstrap_python", "_find_python", "_ensure_uv_index", entry,
    ))
    script = f"""
set -euo pipefail
. {shlex.quote(str(ENGINE / "installer-engine.sh"))}
{definitions}
INSTALLER_ENGINE_DEPENDENCY_POLICY={shlex.quote(str(scripts / "missing-policy.py"))}
SCRIPT_DIR={shlex.quote(str(scripts))}
PKG_SRC_DIR="$SCRIPT_DIR"
INSTALL_DIR={shlex.quote(str(tmp_path / "runtime"))}
ENGINE_HOME={shlex.quote(str(tmp_path / "engine"))}
SRC_VERSION=1.0.0
VENV_DIR="$INSTALL_DIR/versions/$SRC_VERSION"
LOCAL_BIN={shlex.quote(str(tmp_path / "bin"))}
_bootstrap_python() {{ printf '%s' {shlex.quote(str(prepared_helper[1]))}; }}
_ensure_uv() {{ UV_COMMAND={shlex.quote(str(prepared_helper[0]))}; }}
_with_index_build_lock() {{ shift; "$@"; }}
_assert_source_runtime() {{ :; }}
_payload_hash() {{ printf '%064d' 0; }}
_ok() {{ :; }}; _step() {{ :; }}; _fail() {{ printf '%s\\n' "$*" >&2; }}
_versioned_slot_clean() {{ printf ran > {shlex.quote(str(marker))}; }}
_new_index_venv() {{ printf ran > {shlex.quote(str(marker))}; }}
{entry}
"""
    result = subprocess.run(
        ["/bin/bash", "-c", script], cwd=tmp_path, env=env,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode != 0
    assert "The index reader is not prepared" in result.stderr
    assert not marker.exists()
