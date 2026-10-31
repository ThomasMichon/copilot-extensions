"""Real construction resolvers and native uninstall callers with provisioned slots."""
# ruff: noqa: S101

from __future__ import annotations

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
PLUGINS = ("agent-pull-requests", "agent-ssh", "agent-codespaces")
HOSTS = (
    pytest.param(
        "pwsh", marks=pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 7 lane")
    ),
    pytest.param(
        "powershell", marks=pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5 lane")
    ),
    pytest.param(
        "bash",
        marks=pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux lane"),
    ),
)
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _host(name: str) -> str:
    """Resolve the native host before replacing the fixture child's PATH."""
    if name == "powershell":
        path = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = str(path) if path.is_file() else None
    else:
        result = shutil.which(name)
    if not result:
        pytest.skip(f"native {name} unavailable")
    return result


def _python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _quote(value: Path | str, shell: str) -> str:
    text = str(value)
    if shell == "bash":
        return "'" + text.replace("'", "'\\''") + "'"
    return "'" + text.replace("'", "''") + "'"


def _link(path: Path, target: Path) -> None:
    try:
        path.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        pytest.skip(f"fixture executable symlink unavailable: {error}")


def _path_bin(directory: Path, candidate: Path | None) -> Path:
    directory.mkdir()
    if candidate is not None:
        _link(directory / ("python3.exe" if os.name == "nt" else "python3"), candidate)
    if os.name != "nt":
        # The full native installer may use these tools, but gets no ambient Python.
        for name in (
            "basename",
            "dirname",
            "cat",
            "sed",
            "head",
            "tr",
            "grep",
            "uname",
            "date",
            "mkdir",
            "rm",
            "chmod",
            "readlink",
            "link",
            "mktemp",
            "sleep",
            "flock",
            "git",
            "find",
            "sort",
            "stat",
            "bash",
        ):
            command = shutil.which(name)
            if command:
                _link(directory / name, Path(command))
    return directory


def _environment(home: Path, paths: list[Path]) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_", "PYTHON", "UV_")) and key != "VIRTUAL_ENV"
    }
    for key, suffix in {
        "HOME": "",
        "USERPROFILE": "",
        "APPDATA": "roaming",
        "LOCALAPPDATA": "local",
        "XDG_CONFIG_HOME": "config",
        "XDG_CACHE_HOME": "cache",
        "XDG_STATE_HOME": "state",
        "XDG_DATA_HOME": "data",
        "XDG_RUNTIME_DIR": "run",
        "COPILOT_HOME": "copilot",
        "TEMP": "tmp",
        "TMP": "tmp",
        "TMPDIR": "tmp",
    }.items():
        directory = home / suffix
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    env["PATH"] = os.pathsep.join(map(str, paths))
    env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    if os.name == "nt":
        env["PATHEXT"] = ".EXE;.CMD;.BAT"
    return env


def _case(
    tmp_path: Path, plugin: str, layout: str, *, provisioned: bool = True
) -> tuple[Path, Path, Path, dict[str, bytes]]:
    """Use current parent bytes, including its actual vendored helper closure."""
    original = SOURCE / "plugins" / plugin
    fixture_source = tmp_path / "source"
    payload = fixture_source / "plugins" / plugin
    shutil.copytree(
        original / "scripts", payload / "scripts", ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copytree(
        SOURCE / "libs" / "installer-engine",
        fixture_source / "libs" / "installer-engine",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("plugin.json", "pyproject.toml", "payload-invocation.json"):
        shutil.copyfile(original / name, payload / name)
    for name in ("installer_engine_uninstall.py", "command_binding.py", "command_lifecycle.py"):
        assert (payload / "scripts" / name).read_bytes() == (
            original / "scripts" / name
        ).read_bytes()
    profile_path = payload / "scripts" / "uninstall-profile.json"
    profile = json.loads(profile_path.read_text())
    profile["module"] = "fixture_external_uninstall_" + plugin.replace("-", "_")
    profile_path.write_text(json.dumps(profile), encoding="utf-8")
    home = tmp_path / "home"
    root = home / ("." + plugin)
    root.mkdir(parents=True)
    project = (payload / "pyproject.toml").read_text()
    version_match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', project)
    assert version_match
    version = version_match.group(1)
    internal = root / "versions" / version if layout == "versions" else root / layout
    if provisioned:
        venv.EnvBuilder(with_pip=False, symlinks=False).create(internal)
    marker = root / "deploy-manifest.json"
    marker.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "service": plugin,
                "runtime": "python",
                "source": {
                    "plugin": plugin,
                    "kind": "local",
                    "path": str(payload),
                    "version": version,
                },
                "venv": str(internal),
            }
        ),
        encoding="utf-8",
    )
    if layout == "versions":
        (root / "current-version").write_text(version)
    durable = {
        "config.yaml": b"operator configuration\n",
        "auth.json": b'{"auth": "retained"}',
        "unknown/precious": b"unknown state\x00\xff",
        "watch-subscriptions.json": b'{"subscriptions": []}',
        "watch/journal.jsonl": b'{"event": "retained"}\n',
    }
    for name, value in durable.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    return payload, root, internal, durable


def _bootstrap(payload: Path, shell: str) -> str:
    suffix = "sh" if shell == "bash" else "ps1"
    text = (payload / "scripts" / ("install." + suffix)).read_text(encoding="utf-8")
    expression = (
        r"^_bootstrap_python\(\) \{.*?^\}"
        if shell == "bash"
        else r"^function Get-BootstrapPython \{.*?^\}"
    )
    match = re.search(expression, text, flags=re.M | re.S)
    assert match, "the real construction bootstrap must be present"
    return match.group()


def _selector_harness(
    tmp_path: Path,
    payload: Path,
    root: Path,
    internal: Path,
    shell: str,
    *,
    invoke: bool,
) -> list[str]:
    host = _host(shell)
    scripts = payload / "scripts"

    def q(value: Path | str) -> str:
        return _quote(value, shell)

    if shell == "bash":
        action = (
            "uninstall_state_preserving_runtime validate"
            if payload.name == "agent-pull-requests"
            else "uninstall_state_preserving_runtime 1 validate"
        )
        operation = (
            action if invoke else 'printf "%s\\n" "$(_bootstrap_python)" "$(_uninstall_python)"'
        )
        text = f"""
set -euo pipefail
INSTALL_DIR={q(root)}
VENV_DIR={q(internal)}
LINK_DIR={q(internal)}
SCRIPT_DIR={q(scripts)}
UV_CMD=""
{_bootstrap(payload, shell)}
source "$SCRIPT_DIR/uninstall-runtime.sh"
{operation}
"""
        path = tmp_path / "selector.sh"
        args = [host, str(path)]
    else:
        operation = (
            "Invoke-StatePreservingUninstall -AdmissionOnly"
            if invoke
            else "Write-Output (Get-BootstrapPython); Write-Output (Get-UninstallPython)"
        )
        text = f"""
$ErrorActionPreference='Stop'
$InstallDir={q(root)}
$VenvDir={q(internal)}
$LinkDir={q(internal)}
$script:uvPath=$null
$OwnerTaskName='fixture-owner'
function Get-ScheduledTask {{ param($ErrorAction) }}
. {q(scripts / "versioned_runtime_native.ps1")}
{_bootstrap(payload, shell)}
. {q(scripts / "uninstall-runtime.ps1")}
{operation}
"""
        path = tmp_path / "selector.ps1"
        args = [host, "-NoProfile", "-NonInteractive", "-File", str(path)]
    path.write_text(text, encoding="utf-8")
    return args


def _run(
    command: list[str], home: Path, paths: list[Path], cwd: Path
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=_environment(home, paths),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
        creationflags=FLAGS,
    )


def _retained(root: Path, durable: dict[str, bytes]) -> None:
    for name, value in durable.items():
        assert (root / name).read_bytes() == value, name


def _install_command(
    tmp_path: Path, payload: Path, root: Path, shell: str, *, dry_run: bool = False
) -> list[str]:
    if shell == "bash":
        return [
            _host(shell),
            str(payload / "scripts" / "install.sh"),
            "uninstall",
            "--install-dir",
            str(root),
            *(["--dry-run"] if dry_run else []),
        ]
    command = [
        _host(shell),
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(payload / "scripts" / "install.ps1"),
        "-Action",
        "uninstall",
        "-InstallDir",
        str(root),
        *(["-DryRun"] if dry_run else []),
    ]
    if payload.name == "agent-codespaces":
        wrapper = tmp_path / "native-install-caller.ps1"
        wrapper.write_text(
            "function Get-ScheduledTask { param($ErrorAction) }\n"
            f"& {_quote(payload / 'scripts' / 'install.ps1', shell)} "
            f"-Action uninstall -InstallDir {_quote(root, shell)} "
            + ("-DryRun\n" if dry_run else "\n")
            + "exit $LASTEXITCODE\n",
            encoding="utf-8",
        )
        return [*command[:4], str(wrapper)]
    return command


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", HOSTS)
@pytest.mark.parametrize("layout", (".venv", "versions"))
def test_real_native_installer_removes_internal_runtime_using_external_python(
    tmp_path: Path,
    plugin: str,
    shell: str,
    layout: str,
) -> None:
    _host(shell)
    payload, root, internal, durable = _case(tmp_path, plugin, layout)
    paths = [_path_bin(tmp_path / "path", None), Path(sys._base_executable).parent]
    control = _run(
        _selector_harness(tmp_path, payload, root, internal, shell, invoke=False),
        root.parent,
        paths,
        tmp_path,
    )
    assert control.returncode == 0, control.stdout + control.stderr
    selected = control.stdout.strip().splitlines()
    assert len(selected) == 2, control.stdout + control.stderr
    if plugin != "agent-codespaces":
        assert Path(selected[0]).resolve() == _python(internal).resolve()
    else:
        assert _python(internal).is_file()
    assert not Path(selected[1]).resolve().is_relative_to(root.resolve())
    command = _install_command(tmp_path, payload, root, shell)
    result = _run(command, root.parent, paths, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not internal.exists()
    assert root.is_dir()
    _retained(root, durable)


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", HOSTS)
@pytest.mark.parametrize(
    "mode",
    ("missing", "internal-only", "linked-internal", "external-venv", "internal-before-external"),
)
def test_real_selector_enforces_executable_and_prefix_closure(
    tmp_path: Path,
    plugin: str,
    shell: str,
    mode: str,
) -> None:
    _host(shell)
    payload, root, internal, durable = _case(tmp_path, plugin, ".venv")
    candidate = {
        "missing": None,
        "internal-only": _python(internal),
        "linked-internal": _python(internal),
        "external-venv": Path(sys.executable),
        "internal-before-external": _python(internal),
    }[mode]
    paths = [_path_bin(tmp_path / "path", candidate)]
    if mode == "internal-only":
        paths = [
            internal / ("Scripts" if os.name == "nt" else "bin"),
            _path_bin(tmp_path / "utilities", None),
        ]
    if mode == "internal-before-external":
        paths.append(Path(sys._base_executable).parent)
    negative = mode in ("missing", "internal-only", "linked-internal")
    command = _selector_harness(tmp_path, payload, root, internal, shell, invoke=negative)
    result = _run(command, root.parent, paths, tmp_path)
    if negative:
        assert result.returncode != 0, result.stdout + result.stderr
        assert "existing external Python" in result.stdout + result.stderr
        assert _python(internal).is_file()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        selected = result.stdout.strip().splitlines()
        assert len(selected) == 2, result.stdout
        if plugin != "agent-codespaces":
            assert Path(selected[0]).resolve() == _python(internal).resolve()
        else:
            assert _python(internal).is_file()
        assert not Path(selected[1]).resolve().is_relative_to(root.resolve())
    _retained(root, durable)


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", HOSTS)
def test_external_venv_with_internal_base_prefix_is_refused(
    tmp_path: Path,
    plugin: str,
    shell: str,
) -> None:
    _host(shell)
    payload, root, internal, durable = _case(tmp_path, plugin, ".venv")
    base = Path(sys._base_executable)
    internal_base = root / "base"
    internal_base.mkdir()
    copied_base = internal_base / base.name
    shutil.copyfile(base, copied_base)
    if os.name == "nt":
        for library in base.parent.glob("*.dll"):
            shutil.copyfile(library, internal_base / library.name)
        _link(internal_base / "Lib", Path(sys.base_prefix) / "Lib")
        dlls = Path(sys.base_prefix) / "DLLs"
        if dlls.is_dir():
            _link(internal_base / "DLLs", dlls)
    else:
        import sysconfig

        version = f"python{sys.version_info.major}.{sys.version_info.minor}"
        (internal_base / "lib").mkdir()
        _link(internal_base / "lib" / version, Path(sysconfig.get_path("stdlib")))
        copied_base.chmod(0o755)
    external = tmp_path / "external-venv"
    venv.EnvBuilder(with_pip=False, symlinks=False).create(external)
    configuration = external / "pyvenv.cfg"
    text = configuration.read_text()
    text = re.sub(r"(?m)^home = .*", f"home = {internal_base.as_posix()}", text)
    text = re.sub(r"(?m)^executable = .*", f"executable = {copied_base.as_posix()}", text)
    configuration.write_text(text)
    paths = [
        external / ("Scripts" if os.name == "nt" else "bin"),
        _path_bin(tmp_path / "utilities", None),
    ]
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
    result = _run(
        _selector_harness(tmp_path, payload, root, internal, shell, invoke=True),
        root.parent,
        paths,
        tmp_path,
    )
    assert result.returncode != 0
    assert "existing external Python" in result.stdout + result.stderr
    assert copied_base.is_file()
    _retained(root, durable)


def _uv_only(directory: Path, events: Path) -> Path:
    """Make every attempted uv invocation observable without running uv."""
    directory = _path_bin(directory, None)
    if os.name == "nt":
        (directory / "uv.cmd").write_text(
            f'@echo off\n>>"{events}" echo invoked\nexit /b 99\n', encoding="utf-8"
        )
    else:
        stub = directory / "uv"
        stub.write_text(
            "#!/bin/sh\nprintf '%s\\n' invoked >> " + _quote(events, "bash") + "\nexit 99\n",
            encoding="utf-8",
        )
        stub.chmod(0o755)
    return directory


@pytest.mark.parametrize("shell", HOSTS)
@pytest.mark.parametrize(
    "mode",
    ("admission", "preview", "native-dry-run", "native-remove", "foreign", "foreign-external"),
)
def test_codespaces_uv_only_never_provisions_before_admission(
    tmp_path: Path,
    shell: str,
    mode: str,
) -> None:
    _host(shell)
    payload, root, internal, durable = _case(
        tmp_path, "agent-codespaces", ".venv", provisioned=False
    )
    if mode.startswith("foreign"):
        marker = root / "deploy-manifest.json"
        data = json.loads(marker.read_text())
        data["source"]["plugin"] = "foreign-plugin"
        marker.write_text(json.dumps(data), encoding="utf-8")
    if shell != "bash":
        # A real construction-resolver control proves the uv tripwire catches
        # the provisioning fallback which uninstall must never reach.
        control_events = tmp_path / "construction-uv-events"
        control_path = _uv_only(tmp_path / "construction-path", control_events)
        _run(
            _selector_harness(tmp_path, payload, root, internal, shell, invoke=False),
            root.parent,
            [control_path],
            tmp_path,
        )
        assert control_events.read_text().splitlines(), "real bootstrap did not hit uv tripwire"
    events = tmp_path / "uninstall-uv-events"
    paths = [_uv_only(tmp_path / "uninstall-path", events)]
    if mode == "foreign-external":
        paths.append(Path(sys._base_executable).parent)
    if mode in ("admission", "preview"):
        command = _selector_harness(tmp_path, payload, root, internal, shell, invoke=True)
        if mode == "preview":
            script = Path(command[-1])
            text = script.read_text()
            text = text.replace("-AdmissionOnly", "-Preview")
            text = text.replace(
                "uninstall_state_preserving_runtime 1 validate",
                "uninstall_state_preserving_runtime 1",
            )
            script.write_text(text, encoding="utf-8")
    else:
        command = _install_command(tmp_path, payload, root, shell, dry_run=mode != "native-remove")
    before = {
        str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
        for path in root.rglob("*")
    }
    result = _run(command, root.parent, paths, tmp_path)
    assert result.returncode != 0, result.stdout + result.stderr
    detail = result.stdout + result.stderr
    if mode == "foreign-external":
        assert any(word in detail.lower() for word in ("ownership", "foreign", "belongs")), detail
    else:
        assert "existing external Python" in detail
    assert not events.exists(), "uninstall invoked uv before admission"
    assert before == {
        str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
        for path in root.rglob("*")
    }
    _retained(root, durable)
