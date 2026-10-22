"""Disposable guarded caller; no runtime selection, feed or host policy writes."""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import _winapi


def main():
    sdk, workspace, physical_uv = map(Path, sys.argv[1:4])
    frontend, _, lookup = sys.argv[4].partition("-")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessMitigationPolicy.argtypes = [
        ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
    ]
    kernel.SetProcessMitigationPolicy.argtypes = [
        ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t,
    ]

    def flags():
        value = ctypes.c_ulong()
        if not kernel.GetProcessMitigationPolicy(
            ctypes.c_void_p(-1), 16, ctypes.byref(value), ctypes.sizeof(value),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value

    environment = workspace / "environment"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / "Scripts" / "python.exe"
    project = workspace / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\nbackend-path=["."]\n'
        '[project]\nname="fixture-uv-selector"\nversion="1.0"\ndependencies=[]\n'
        '[tool.uv]\nexclude-newer="2000-01-01T00:00:00Z"\n', encoding="utf-8",
    )
    (project / "fixture_backend.py").write_text(
        "import pathlib,zipfile\n"
        "def build_wheel(wheel_directory,config_settings=None,metadata_directory=None):\n"
        "    name='fixture_uv_selector-1.0-py3-none-any.whl'\n"
        "    with zipfile.ZipFile(pathlib.Path(wheel_directory)/name,'w') as wheel:\n"
        "        wheel.writestr('fixture_uv_selector/__init__.py',\"value='installed'\\n\")\n"
        "        root='fixture_uv_selector-1.0.dist-info/'\n"
        "        wheel.writestr(root+'METADATA','Metadata-Version: 2.1\\n"
        "Name: fixture-uv-selector\\nVersion: 1.0\\n')\n"
        "        wheel.writestr(root+'WHEEL','Wheel-Version: 1.0\\n"
        "Generator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')\n"
        "        wheel.writestr(root+'RECORD','')\n"
        "    return name\n", encoding="utf-8",
    )
    alias = workspace / "uv-link"
    _winapi.CreateJunction(str(physical_uv.parent), str(alias))
    logical_uv = str(alias / physical_uv.name)
    original_path = os.environ.get("PATH")
    if lookup == "named":
        os.environ["PATH"] = str(alias) + os.pathsep + os.environ["PATH"]
    selected_uv = "uv" if lookup in ("named", "system") else logical_uv
    before = flags()
    requested = ctypes.c_ulong(before | 1)
    if not before & 1 and not kernel.SetProcessMitigationPolicy(16, ctypes.byref(requested), ctypes.sizeof(requested)):
        print(json.dumps({"unsupported_guard": ctypes.get_last_error()}))
        return
    assert flags() == before | 1
    spec = importlib.util.spec_from_file_location(
        "guarded_candidate_policy", sdk / "installer_dependency_policy.py",
    )
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    actual = subprocess.run
    commands = []

    def observed(command, **kwargs):
        commands.append(command)
        return actual(command, **kwargs)

    policy.subprocess.run = observed
    for _ in range(2):
        try:
            os.stat(logical_uv)
        except OSError as error:
            assert error.winerror == 448
        else:
            raise AssertionError("logical UV traversal was not guarded")
        try:
            actual(
                [logical_uv, "--version"], capture_output=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError as error:
            assert error.winerror in (2, 448)
        else:
            raise AssertionError("unguarded logical UV control unexpectedly started")
        if frontend == "python":
            sys.path.insert(0, str(sdk))
            import installer_engine as engine

            packages = (project,)
            inputs = engine.FrozenInputs(
                project, packages, engine.runtime.fingerprint_source(engine._source_files(packages)),
            )
            bound_uv = Path(policy._select_uv_executable(selected_uv)) if lookup else Path(selected_uv)
            with engine._retained_inputs(inputs, bound_uv, python, refresh=False) as retained:
                transaction = retained.transaction
                assert transaction is not None
                result = observed(
                    list(transaction.argv), cwd=transaction.cwd, env=transaction.environment,
                    capture_output=True, text=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        else:
            assert frontend == "policy"
            result = policy.run_uv_install(
                selected_uv, ["--python", str(python), str(project)],
                cwd=project, env=dict(os.environ), trusted_source_roots=(project,),
                capture_output=True, text=True, timeout=15,
            )
        assert result.returncode == 0, result.stderr
        installed = actual(
            [str(python), "-I", "-B", "-c",
             "import fixture_uv_selector;assert fixture_uv_selector.value=='installed'"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert installed.returncode == 0, installed.stderr
        assert flags() == before | 1
    assert len(commands) == 6
    assert len({command[0] for command in commands}) == 1
    assert os.path.samefile(commands[0][0], physical_uv)
    if lookup == "system":
        assert os.environ.get("PATH") == original_path
    for command in (command for command in commands if "--show-settings" not in command):
        assert command[command.index("--exclude-newer") + 1] == "2000-01-01T00:00:00Z"
    print(json.dumps({
        "caller_guard": flags(), "blocked_controls": 2, "installed_cycles": 2,
        "same_physical_tool_for_all_calls": True, "stricter_cutoff_preserved": True,
    }))


if __name__ == "__main__":
    main()
