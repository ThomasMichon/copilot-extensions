"""Real Windows candidate launches and finite metadata removal, without service STOP."""

# ruff: noqa: S101
from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[4]
FILES = {
    "agent-bridge": "uninstall-runtime.ps1",
    "agent-dispatch": "uninstall-runtime.ps1",
    "agent-index": "install.ps1",
    "agent-vault": "uninstall-runtime.ps1",
    "budget-guidance": "uninstall-runtime.ps1",
    "agent-logger": "install.ps1",
    "agent-mcp": "init.ps1",
}
HOSTS = ("pwsh", "powershell")
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
pytestmark = pytest.mark.skipif(os.name != "nt", reason="real native Windows PE/candidate lane")


def _host(name: str) -> str:
    if name == "powershell":
        path = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = str(path) if path.is_file() else None
    else:
        result = shutil.which(name)
    if not result:
        pytest.skip(f"native {name} unavailable")
    return result


def _q(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _definition(plugin: str, name: str) -> str:
    text = (SOURCE / "plugins" / plugin / "scripts" / FILES[plugin]).read_text(encoding="utf-8")
    match = re.search(rf"^function {re.escape(name)} \{{.*?^\}}", text, re.M | re.S)
    assert match, f"real source function missing: {plugin}/{name}"
    return match.group()


def _env(home: Path, paths: list[Path]) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_", "PYTHON", "UV_")) and key != "VIRTUAL_ENV"
    }
    for key, suffix in {
        "HOME": "",
        "USERPROFILE": "",
        "APPDATA": "roaming",
        "LOCALAPPDATA": "local",
        "COPILOT_HOME": "copilot",
        "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache",
        "XDG_DATA_HOME": "data",
        "XDG_STATE_HOME": "state",
        "XDG_RUNTIME_DIR": "run",
        "TEMP": "tmp",
        "TMP": "tmp",
        "TMPDIR": "tmp",
    }.items():
        directory = home / suffix
        directory.mkdir(parents=True, exist_ok=True)
        environment[key] = str(directory)
    environment.update(
        PATH=os.pathsep.join(map(str, paths)),
        PATHEXT=".EXE;.CMD;.BAT",
        COPILOT_EXTENSIONS_TEST_CONTAINED="1",
    )
    return environment


def _run(
    command: list[str], home: Path, paths: list[Path], cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=_env(home, paths),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
        creationflags=FLAGS,
    )


def _python(directory: Path) -> Path:
    return directory / "Scripts/python.exe"


def _bin(directory: Path, executable: Path | None = None, *, broken: bool = False) -> Path:
    directory.mkdir()
    for name in ("python.exe", "python3.exe"):
        candidate = directory / name
        if broken:
            candidate.write_bytes(b"MZ\x00not a loadable PE image")
        elif executable is not None:
            try:
                candidate.symlink_to(executable)
            except OSError as error:
                pytest.skip(f"fixture executable alias unavailable: {error}")
    return directory


def _case(tmp_path: Path, plugin: str) -> tuple[Path, Path, dict[str, bytes]]:
    original = SOURCE / "plugins" / plugin
    payload = tmp_path / "source" / "plugins" / plugin
    shutil.copytree(
        original / "scripts", payload / "scripts", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copytree(
        SOURCE / "libs/installer-engine",
        tmp_path / "source/libs/installer-engine",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("plugin.json", "pyproject.toml", "payload-invocation.json"):
        source = original / name
        if source.is_file():
            shutil.copyfile(source, payload / name)
    helper = payload / "scripts/installer_engine_uninstall.py"
    spec = importlib.util.spec_from_file_location("candidate_fixture_engine", helper)
    assert spec and spec.loader
    engine = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = engine
    spec.loader.exec_module(engine)
    # Keep real removal entries; isolate only the fixture's live-module identity.
    record = dataclasses.asdict(engine.profile_for(payload, plugin))
    for key in ("root_entries", "bin_entries", "script_entries", "local_bin_entries"):
        record[key] = list(record[key])
    record["version"] = 1
    record["module"] = "fixture_candidate_" + plugin.replace("-", "_")
    (payload / "scripts/uninstall-profile.json").write_text(json.dumps(record), encoding="utf-8")
    root = tmp_path / "home" / ("." + plugin)
    root.mkdir(parents=True)
    venv.EnvBuilder(with_pip=False, symlinks=False).create(root / ".venv")
    version = json.loads((payload / "plugin.json").read_text())["version"]
    (root / "deploy-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "service": plugin,
                "runtime": "python",
                "source": {
                    "plugin": plugin,
                    "path": str(payload),
                    "kind": "local",
                    "version": version,
                },
                "venv": str(root / ".venv"),
            }
        ),
        encoding="utf-8",
    )
    durable = {
        "config.yaml": b"operator config\n",
        "auth.json": b'{"identity": "retained"}',
        "queue.db": b"\x00queued work\xff",
        "unknown/precious": b"unknown state",
    }
    for name, value in durable.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    return payload, root, durable


def _select(
    tmp_path: Path,
    plugin: str,
    payload: Path,
    root: Path,
    shell: str,
    paths: list[Path],
    *,
    broken: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    control = ""
    if broken is not None:
        control = f"""
$actualLaunchThrew=$false
try {{ $null=Invoke-Hidden -FilePath {_q(broken)} -ArgList @('-I','-c','pass') }} catch {{
    $actualLaunchThrew=$true
}}
if (-not $actualLaunchThrew) {{ throw 'Fixture did not exercise Process.Start failure' }}
"""
    script = tmp_path / "select.ps1"
    script.write_text(
        f"""
$ErrorActionPreference='Stop'
. {_q(payload / "scripts/versioned_runtime_native.ps1")}
{_definition(plugin, "Get-FiniteUninstallPython")}
{control}
$selected=Get-FiniteUninstallPython -InstallRoot {_q(root)}
if (-not $selected) {{ throw 'No existing supported external Python 3.10+ candidate' }}
Write-Output ("SELECTED:" + $selected)
""",
        encoding="utf-8",
    )
    return _run(
        [_host(shell), "-NoProfile", "-NonInteractive", "-File", str(script)],
        root.parent,
        paths,
        tmp_path,
    )


@pytest.mark.parametrize("shell", HOSTS)
@pytest.mark.parametrize("plugin", tuple(FILES))
@pytest.mark.parametrize(
    "mode", ("broken-first", "reported38-first", "missing", "internal", "physical-alias")
)
def test_real_candidate_failures_do_not_abort_or_accept_unsupported_python(
    tmp_path: Path,
    plugin: str,
    shell: str,
    mode: str,
) -> None:
    _host(shell)
    payload, root, durable = _case(tmp_path, plugin)
    broken = None
    if mode == "broken-first":
        first = _bin(tmp_path / "first", broken=True)
        broken = first / "python3.exe"
        paths = [first, Path(sys.executable).parent]
    elif mode == "reported38-first":
        old = tmp_path / "version-reporting-venv"
        venv.EnvBuilder(with_pip=False, symlinks=False).create(old)
        (old / "Lib/site-packages/sitecustomize.py").write_text(
            "import sys\nsys.version_info=(3,8,20,'final',0)\n",
            encoding="utf-8",
        )
        shutil.copyfile(_python(old), old / "Scripts/python3.exe")
        paths = [old / "Scripts", Path(sys.executable).parent]
        # -I retains isolated-venv sitecustomize; -S suppresses it. This is
        # version-reporting branch coverage, not Python 3.8 interoperability.
        for flags, expected in (
            (["-I", "-B"], "3.8"),
            (["-I", "-S"], f"{sys.version_info.major}.{sys.version_info.minor}"),
        ):
            evidence = _run(
                [
                    str(paths[0] / "python3.exe"),
                    *flags,
                    "-c",
                    "import sys;print(str(sys.version_info[0])+'.'+str(sys.version_info[1]))",
                ],
                root.parent,
                paths,
                tmp_path,
            )
            assert evidence.returncode == 0, evidence.stdout + evidence.stderr
            assert evidence.stdout.strip() == expected
    elif mode == "missing":
        paths = [_bin(tmp_path / "empty")]
    elif mode == "internal":
        paths = [root / ".venv/Scripts"]
    else:
        alias = tmp_path / "physical-alias"
        try:
            alias.symlink_to(root / ".venv", target_is_directory=True)
        except OSError as error:
            pytest.skip(f"fixture directory alias unavailable: {error}")
        paths = [alias / "Scripts"]
        evidence = _run(
            [str(paths[0] / "python.exe"), "-I", "-B", "-c", "import sys;print(sys.prefix)"],
            root.parent,
            paths,
            tmp_path,
        )
        assert evidence.returncode == 0, evidence.stdout + evidence.stderr
        assert Path(evidence.stdout.strip()).resolve().is_relative_to(root.resolve())
    result = _select(tmp_path, plugin, payload, root, shell, paths, broken=broken)
    if mode in ("broken-first", "reported38-first"):
        assert result.returncode == 0, result.stdout + result.stderr
        selected = Path(result.stdout.strip().split("SELECTED:", 1)[1])
        assert selected.resolve() == Path(sys.executable).resolve()
        assert not selected.resolve().is_relative_to(root.resolve())
    else:
        assert result.returncode != 0
        assert "No existing supported external Python 3.10+" in result.stdout + result.stderr
    for name, value in durable.items():
        assert (root / name).read_bytes() == value
    assert _python(root / ".venv").is_file()


@pytest.mark.parametrize("shell", HOSTS)
@pytest.mark.parametrize("plugin", tuple(FILES))
def test_supported_selection_runs_real_finite_metadata_cleanup_and_preserves_state(
    tmp_path: Path,
    plugin: str,
    shell: str,
) -> None:
    _host(shell)
    payload, root, durable = _case(tmp_path, plugin)
    paths = [_bin(tmp_path / "supported", Path(sys.executable))]
    result = _select(tmp_path, plugin, payload, root, shell, paths)
    assert result.returncode == 0, result.stdout + result.stderr
    selected = result.stdout.strip().split("SELECTED:", 1)[1]
    helper = payload / "scripts/installer_engine_uninstall.py"
    arguments = [
        selected,
        "-I",
        "-B",
        str(helper),
        "--payload-root",
        str(payload),
        "--plugin-id",
        plugin,
        "--root",
        str(root),
        "--shell",
        _host(shell),
        "--profile",
        str(payload / "scripts/uninstall-profile.json"),
    ]
    for action in ("--check", "--remove-runtime"):
        removed = _run([*arguments, action], root.parent, paths, tmp_path)
        assert removed.returncode == 0, removed.stdout + removed.stderr
    assert not (root / ".venv").exists()
    for name, value in durable.items():
        assert (root / name).read_bytes() == value


@pytest.mark.parametrize("shell", HOSTS)
@pytest.mark.parametrize(
    "plugin", ("agent-bridge", "agent-dispatch", "agent-index", "agent-vault", "budget-guidance")
)
def test_actual_finite_admission_invoker_recovers_from_process_start_failure(
    tmp_path: Path,
    plugin: str,
    shell: str,
) -> None:
    _host(shell)
    payload, root, durable = _case(tmp_path, plugin)
    paths = [
        _bin(tmp_path / "broken", broken=True),
        Path(sys.executable).parent,
    ]
    scripts = payload / "scripts"
    if plugin == "agent-index":
        definitions = (
            _definition(plugin, "Get-FiniteUninstallPython")
            + "\n"
            + (
                _definition(plugin, "Invoke-IndexFiniteUninstall").replace(
                    "$PSScriptRoot", "$Scripts"
                )
            )
        )
        invocation = f"Invoke-IndexFiniteUninstall -Root {_q(root)} -Mode '--check'"
    else:
        definitions = (
            f". {_q(scripts / 'versioned_runtime_native.ps1')}\n"
            f". {_q(scripts / 'uninstall-runtime.ps1')}"
        )
        invocation = f"Invoke-FiniteRuntimeUninstall -InstallRoot {_q(root)} -Check"
    script = tmp_path / "actual-admission.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        + f"$Scripts={_q(scripts)}\n"
        + definitions
        + "\n"
        + invocation
        + "\n",
        encoding="utf-8",
    )
    result = _run(
        [_host(shell), "-NoProfile", "-NonInteractive", "-File", str(script)],
        root.parent,
        paths,
        tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for name, value in durable.items():
        assert (root / name).read_bytes() == value
    assert _python(root / ".venv").is_file()


@pytest.mark.parametrize("shell", HOSTS)
@pytest.mark.parametrize("plugin", tuple(FILES))
def test_real_external_venv_with_internal_base_prefix_is_not_selected(
    tmp_path: Path,
    plugin: str,
    shell: str,
) -> None:
    _host(shell)
    payload, root, durable = _case(tmp_path, plugin)
    base = Path(sys._base_executable)
    internal_base = root / "base"
    internal_base.mkdir()
    copied = internal_base / base.name
    shutil.copyfile(base, copied)
    for library in base.parent.glob("*.dll"):
        shutil.copyfile(library, internal_base / library.name)
    try:
        (internal_base / "Lib").symlink_to(Path(sys.base_prefix) / "Lib", target_is_directory=True)
        if (Path(sys.base_prefix) / "DLLs").is_dir():
            (internal_base / "DLLs").symlink_to(
                Path(sys.base_prefix) / "DLLs", target_is_directory=True
            )
    except OSError as error:
        pytest.skip(f"fixture standard-library directory alias unavailable: {error}")
    external = tmp_path / "external"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(external)
    marker = external / "pyvenv.cfg"
    text = re.sub(r"(?m)^home = .*", f"home = {internal_base.as_posix()}", marker.read_text())
    text = re.sub(r"(?m)^executable = .*", f"executable = {copied.as_posix()}", text)
    marker.write_text(text)
    paths = [external / "Scripts"]
    evidence = _run(
        [
            str(_python(external)),
            "-I",
            "-B",
            "-c",
            "import json,sys;print(json.dumps([sys.executable,sys.prefix,sys.base_prefix]))",
        ],
        root.parent,
        paths,
        tmp_path,
    )
    assert evidence.returncode == 0, evidence.stdout + evidence.stderr
    executable, prefix, base_prefix = map(Path, json.loads(evidence.stdout))
    assert not executable.resolve().is_relative_to(root.resolve())
    assert not prefix.resolve().is_relative_to(root.resolve())
    assert base_prefix.resolve().is_relative_to(root.resolve())
    result = _select(tmp_path, plugin, payload, root, shell, paths)
    assert result.returncode != 0
    assert "No existing supported external Python 3.10+" in result.stdout + result.stderr
    for name, value in durable.items():
        assert (root / name).read_bytes() == value


def test_every_audited_uninstall_callsite_uses_qualified_selection() -> None:
    for plugin, filename in FILES.items():
        text = (SOURCE / "plugins" / plugin / "scripts" / filename).read_text(encoding="utf-8")
        assert "Get-FiniteUninstallPython -InstallRoot " in text
    for plugin in ("agent-machines", "agent-containers"):
        text = (SOURCE / "plugins" / plugin / "scripts/legacy-uninstall.ps1").read_text()
        assert "assert sys.version_info >= (3, 10)" in text
        assert "path.resolve(strict=True)" in text and "sys.base_prefix" in text
