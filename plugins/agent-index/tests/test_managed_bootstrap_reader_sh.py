"""Cold Index readers select prepared Python before application/runtime code."""
from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

import pytest

from test_install_engine_adoption import (
    ENGINE, PLUGIN, environment, function, stage_index_installer,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Native POSIX bootstrap readers")


def find_prepared_helper() -> tuple[Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This acquisition-free control requires existing uv")
    probe_env = dict(os.environ)
    if "UV_PYTHON_INSTALL_DIR" not in probe_env:
        # The contained runner redirects HOME after preparing its interpreter.
        base = Path(str(getattr(sys, "_base_executable", sys.executable))).resolve()
        probe_env["UV_PYTHON_INSTALL_DIR"] = str(base.parents[1 if os.name == "nt" else 2])
    result = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        capture_output=True, text=True, timeout=15, env=probe_env,
    )
    if result.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.11.18")
    root = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, env=probe_env,
    ).strip()
    return Path(uv), Path(result.stdout.strip()).resolve(), Path(root)


@pytest.fixture(scope="module")
def prepared_helper() -> tuple[Path, Path, Path]:
    return find_prepared_helper()


def _environment(
    tmp_path: Path, prepared: tuple[Path, Path, Path], scenario: str,
) -> dict[str, str]:
    uv, _python, managed_root = prepared
    env = environment(tmp_path)
    commands = tmp_path / "commands"
    commands.mkdir()
    for name in ("bash", "dirname", "wc", "cat", "sed", "head", "git", "id",
                 "cut", "awk", "grep", "tr", "date", "basename", "getent"):
        executable = shutil.which(name)
        assert executable, name
        (commands / name).symlink_to(executable)
    for name in ("python", "python3", "py"):
        command = commands / name
        command.write_text(
            '#!/bin/sh\nprintf invoked > "$AMBIENT_MARKER"\nexit 91\n',
            encoding="utf-8",
        )
        command.chmod(0o700)
    if scenario != "missing-uv":
        wrapper = commands / "uv"
        wrapper.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$*" >> "$UV_PROBE_LOG"\n'
            f'exec {shlex.quote(str(uv))} "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o700)
    empty_root = tmp_path / "unprepared-python"
    empty_root.mkdir()
    env.update(
        PATH=str(commands), AMBIENT_MARKER=str(tmp_path / "ambient-ran"),
        UV_PROBE_LOG=str(tmp_path / "uv-calls"), UV_OFFLINE="1",
        UV_PYTHON_INSTALL_DIR=str(empty_root if scenario == "unprepared" else managed_root),
        UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        UV_CONFIG_FILE="missing-relative.toml", UV_NO_CONFIG="false",
        UV_PYTHON=str(commands / "python"), UV_NO_MANAGED_PYTHON="1",
        VIRTUAL_ENV=str(tmp_path / "incumbent"), CONDA_PREFIX=str(tmp_path / "incumbent"),
        PYTHONHOME=str(tmp_path / "invalid-home"), PYTHONPATH=str(tmp_path / "invalid-path"),
    )
    return env


@pytest.mark.parametrize("scenario", [
    "raw", "packaged", "unprepared", "missing-uv", "missing-provider",
    "missing-request", "different-request",
])
def test_bootstrap_readers_inspect_prepared_python_without_ambient_fallback(
    tmp_path: Path, scenario: str, prepared_helper: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    scripts = tmp_path / "scripts"
    shutil.copytree(ENGINE, scripts, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    engine = ENGINE / "installer-engine.sh" if scenario == "raw" else scripts / "installer-engine.sh"
    if scenario in {"missing-provider", "missing-request"}:
        name = "bootstrap-uv.sh" if scenario == "missing-provider" else "installer-instance-python-version.txt"
        (scripts / name).rename(scripts / (name + ".unavailable"))
    elif scenario == "different-request":
        (scripts / "installer-instance-python-version.txt").write_text(
            "cpython@3.11.999\n", encoding="utf-8",
        )
    env = _environment(tmp_path, prepared_helper, scenario)
    incumbent = Path(env["VIRTUAL_ENV"]) / "bin"
    incumbent.mkdir(parents=True)
    (incumbent / "python").symlink_to(Path(env["PATH"]) / "python")
    definitions = function("_bootstrap_python", "sh") + "\n" + function("_find_python", "sh")
    samples = []
    successful = scenario in {"raw", "packaged"}
    for _cycle in range(2 if successful else 1):
        started = time.monotonic()
        result = subprocess.run(
            ["/bin/bash", "-c",
             f"set -euo pipefail\n. {shlex.quote(str(engine))}\n"
             f"LINK_DIR={shlex.quote(str(incumbent.parent))}\n{definitions}\n"
             'selected="$(_bootstrap_python)" || exit $?\n'
             '[[ "$(_find_python)" == "$selected" ]] || exit 92\n'
             '[[ "$UV_CONFIG_FILE" == missing-relative.toml && "$UV_NO_CONFIG" == false && '
             '"$UV_NO_MANAGED_PYTHON" == 1 && "$VIRTUAL_ENV" == "$LINK_DIR" ]] || exit 93\n'
             'printf "%s\\n" "$selected"\n'],
            env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=15,
        )
        samples.append(time.monotonic() - started)
        assert not Path(env["AMBIENT_MARKER"]).exists(), result.stdout + result.stderr
        if successful:
            assert result.returncode == 0, result.stdout + result.stderr
            assert result.stdout.strip() == str(prepared_helper[1])
            assert not result.stderr
        else:
            assert result.returncode != 0
            assert not result.stdout
            assert "requires prepared uv-managed Python" in result.stderr
    assert not list((tmp_path / "unprepared-python").iterdir())
    if successful:
        assert Path(env["UV_PROBE_LOG"]).read_text().splitlines() == [
            "--version",
            "python find --no-project --system --managed-python --no-python-downloads cpython@3.11.18",
        ] * 4
        record_property("index_posix_bootstrap_performance", json.dumps({
            "scenario": scenario, "status": "measured", "completed": len(samples),
            "samples_seconds": samples, "uv_calls_per_reader": 2, "network": "disabled",
            "cache": "fresh private uv cache then retained cache; preinstalled interpreter",
            "unavailable": ["full install latency", "child CPU", "tail qualification"],
        }))


@pytest.mark.parametrize("materialized", [False, True])
@pytest.mark.parametrize("scenario", ["managed", "unprepared"])
def test_actual_cell_entry_selects_before_runtime_and_preserves_bindings(
    tmp_path: Path, materialized: bool, scenario: str,
    prepared_helper: tuple[Path, Path, Path],
) -> None:
    payload = stage_index_installer(
        tmp_path / "repository/plugins/agent-index", materialized=materialized,
    )
    (payload / "pyproject.toml").write_text('[project]\nversion = "1.0.0"\n', encoding="utf-8")
    (payload / "scripts/cell-runtime.py").write_text(
        "import json, os, sys\n"
        "print(json.dumps({'python': sys.executable, 'args': sys.argv[1:],"
        " 'cwd': os.getcwd(), 'isolated': sys.flags.isolated,"
        " 'pythonpath': os.environ.get('PYTHONPATH'),"
        " 'pythonhome': os.environ.get('PYTHONHOME')}))\n"
        "raise SystemExit(23)\n", encoding="utf-8",
    )
    env = _environment(tmp_path, prepared_helper, scenario)
    context = tmp_path / "context with spaces.json"
    args = ["cell-recover", "--context", str(context), "--expected-marketplace-id", "example--1234"]
    result = subprocess.run(
        ["/bin/bash", str(payload / "scripts/install.sh"), *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15,
    )
    assert not Path(env["AMBIENT_MARKER"]).exists(), result.stdout + result.stderr
    assert not list((tmp_path / "unprepared-python").iterdir())
    if scenario == "unprepared":
        assert result.returncode != 0 and not result.stdout
        assert "requires prepared uv-managed Python" in result.stderr
    else:
        assert result.returncode == 23, result.stdout + result.stderr
        assert json.loads(result.stdout) == {
            "python": str(prepared_helper[1]), "args": args,
            "cwd": env["HOME"], "isolated": 1, "pythonpath": None, "pythonhome": None,
        }


@pytest.mark.parametrize("scenario", ["managed", "missing-selector", "missing-request", "unprepared"])
def test_actual_cold_gate_never_falls_back_when_generated_assets_are_missing(
    tmp_path: Path, scenario: str, prepared_helper: tuple[Path, Path, Path],
) -> None:
    import runpy

    from test_runtime_gate import _fixture

    gate, env = _fixture(tmp_path, "bash")
    bootstrap = runpy.run_path(str(PLUGIN.parents[1] / "libs/payload-invocation/bootstrap_python.py"))
    for path, content in bootstrap["expected_files"](
        gate.parents[1] / "payload-invocation.json",
        {"bootstrapPython": "3.11.18", "bootstrapLauncher": "uv"},
    ).items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    # A missing marker used to silently reactivate the ambient-Python branch.
    (gate.parent / "bootstrap-python-launcher.txt").unlink()
    if scenario == "missing-selector":
        (gate.parent / "_bootstrap_python/select.sh").unlink()
    elif scenario == "missing-request":
        (gate.parent / "bootstrap-python-version.txt").unlink()
    reader_env = _environment(tmp_path, prepared_helper, scenario)
    env.update(reader_env)
    env["AGENT_INDEX_REPO"] = str(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(gate), "installer-readiness"], env=env, cwd=tmp_path,
        capture_output=True, text=True, timeout=20,
    )
    assert not Path(env["AMBIENT_MARKER"]).exists(), result.stdout + result.stderr
    assert not list((tmp_path / "unprepared-python").iterdir())
    if scenario == "managed":
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["state"] == "configuration-empty"
    else:
        assert result.returncode != 0 and not result.stdout


@pytest.mark.parametrize("reader", ["role", "registry", "health"])
def test_remaining_cold_helpers_use_selected_bootstrap_not_path_or_incumbent(
    tmp_path: Path, reader: str,
) -> None:
    env = environment(tmp_path)
    commands = tmp_path / "commands"
    commands.mkdir()
    ambient = tmp_path / "ambient-ran"
    for name in ("python", "python3"):
        command = commands / name
        command.write_text(
            f"#!/bin/sh\nprintf invoked > {shlex.quote(str(ambient))}\nexit 91\n",
            encoding="utf-8",
        )
        command.chmod(0o700)
    selected = tmp_path / "selected-python"
    calls = tmp_path / "selected-calls"
    selected.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {shlex.quote(str(calls))}\n"
        f"exec {shlex.quote(str(getattr(sys, '_base_executable', sys.executable)))} -S \"$@\"\n",
        encoding="utf-8",
    )
    selected.chmod(0o700)
    env["PATH"] = str(commands) + os.pathsep + env["PATH"]
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("resolve_effective_config.py", "config_schema.py", "config_yaml.py"):
        shutil.copy2(PLUGIN / "scripts" / name, scripts / name)
    (scripts / "resolve-activation-role.py").write_text(
        "import sys\nassert sys.argv[1:] == ['--repo', sys.argv[2], '--machine', 'fixture']\n"
        "print('host')\n", encoding="utf-8",
    )
    library = tmp_path / "registry-source/libs/fixture-lib"
    library.mkdir(parents=True)
    (library / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (Path(env["HOME"]) / ".git-repos").write_text(
        "repos:\n  copilot-extensions:\n    path: " + json.dumps(str(library.parents[1])) + "\n",
        encoding="utf-8",
    )
    runtime = tmp_path / "incumbent"
    runtime.mkdir()
    (runtime / "active.json").write_text("invalid json", encoding="utf-8")
    target, invocation = {
        "role": ("_activation_role", "_activation_role"),
        "registry": ("_resolve_vendored_lib", "_resolve_vendored_lib fixture-lib"),
        "health": ("_service_healthy", "_service_healthy"),
    }[reader]
    script = (
        f"{function(target, 'sh')}\n"
        f"_bootstrap_python() {{ printf '%s\\n' {shlex.quote(str(selected))}; }}\n"
        f"SCRIPT_DIR={shlex.quote(str(scripts))}\n"
        f"PLUGIN_DIR={shlex.quote(str(tmp_path / 'absent-payload'))}\n"
        f"INSTALL_DIR={shlex.quote(str(runtime))}\n"
        f"LINK_PYTHON={shlex.quote(str(commands / 'python'))}\n"
        f"AGENT_INDEX_REPO={shlex.quote(str(tmp_path))}\n"
        "AGENT_INDEX_MACHINE=fixture\n"
        + invocation + "\n"
    )
    result = subprocess.run(
        ["/bin/bash", "-c", script], env=env, cwd=tmp_path,
        capture_output=True, text=True, timeout=15,
    )
    assert not ambient.exists(), result.stdout + result.stderr
    assert result.returncode == (1 if reader == "health" else 0), result.stdout + result.stderr
    assert result.stdout.strip() == {"role": "host", "registry": str(library), "health": ""}[reader]
    assert len(calls.read_text().splitlines()) >= 1
