"""Real construction resolvers and native uninstall callers with provisioned slots."""
# ruff: noqa: S101

from __future__ import annotations

import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import venv
from collections.abc import Callable
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[4]
PLUGINS = ("agent-pull-requests", "agent-ssh")
HOSTS = (
    pytest.param(
        "pwsh", marks=pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 7 lane")
    ),
    pytest.param(
        "powershell", marks=pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5 lane")
    ),
    pytest.param(
        "bash",
        marks=pytest.mark.skipif(os.name == "nt", reason="native POSIX lane"),
    ),
)
FLAGS = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


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
            "awk",
            "wc",
            "mkfifo",
            "rmdir",
            "cut",
            "cksum",
            "ps",
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
    paths = [_path_bin(tmp_path / "path", None), Path(str(getattr(sys, "_base_executable"))).parent]
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
        paths.append(Path(str(getattr(sys, "_base_executable"))).parent)
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
    base = Path(str(getattr(sys, "_base_executable")))
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


@pytest.fixture(scope="module")
def prepared_uninstall_python() -> tuple[Path, Path, Path]:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("This acquisition-free control requires prepared uv")
    result = subprocess.run(
        [uv, "python", "find", "--no-project", "--system", "--managed-python",
         "--no-python-downloads", "cpython@3.11.18"],
        capture_output=True, text=True, timeout=15, creationflags=FLAGS, check=False,
    )
    if result.returncode:
        pytest.skip("This acquisition-free control requires prepared CPython 3.11.18")
    store = subprocess.check_output(
        [uv, "python", "dir"], text=True, timeout=15, creationflags=FLAGS,
    ).strip()
    return Path(uv), Path(result.stdout.strip()).resolve(), Path(store)


def _prepared_payload(payload: Path, materialized: bool, shell: str) -> Path:
    previous = sys.path[:]
    try:
        owner = runpy.run_path(str(SOURCE / "tools/materialize_installer_engine.py"))
        for original, target in owner["derived_file_pairs"](
            SOURCE, payload.parents[1],
            copied_consumer=(SOURCE / "plugins/agent-codespaces", payload),
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, target)
        if materialized:
            changes = owner["materialize_ref_into"](
                source_consumer_dir=SOURCE / "plugins/agent-codespaces",
                dest_consumer_dir=payload, canonical_root=SOURCE,
                dest_root=payload.parents[1],
            )
            assert changes and not any(line.startswith("SKIP") for line in changes), changes
    finally:
        sys.path[:] = previous
    scripts = payload / "scripts"
    support = scripts if materialized else payload.parents[1] / "libs/installer-engine"
    if not materialized:
        (scripts / "installer-instance-python-version.txt").unlink(missing_ok=True)
        assert (scripts / "bootstrap-uv.sh").is_file()
        (scripts / "native_capture.sh").unlink(missing_ok=True)
        capture = payload.parents[1] / "libs/agent-procutil/src/agent_procutil/native_capture.sh"
        capture.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE / "libs/agent-procutil/src/agent_procutil/native_capture.sh", capture)
    suffix = "sh" if shell == "bash" else "ps1"
    provider = support / f"bootstrap-uv.{suffix}"
    invocation = '    exec "$@"\n' if shell == "bash" else "    $result = & $InvokeCaptured @options\n"
    observation = (
        '    printf "%s\\n" "$*" >> "$CODESPACES_UNINSTALL_UV_LOG"\n' if shell == "bash" else
        '    [IO.File]::AppendAllText($env:CODESPACES_UNINSTALL_UV_LOG, ($Arguments -join " ") + "`n")\n'
    )
    text = provider.read_text(encoding="utf-8")
    assert text.count(invocation) == 1
    provider.write_text(text.replace(invocation, observation + invocation), encoding="utf-8")
    # The fixture module has no host consumers; keep filesystem qualification
    # independent of unrelated privileged processes in the host's POSIX census.
    helper = scripts / "installer_engine_uninstall.py"
    text = helper.read_text(encoding="utf-8")
    needle = '    """Observe current-user consumers; observation never grants stop authority."""\n'
    assert text.count(needle) == 1
    observed = text.replace(
        needle, needle + "    if os.name != 'nt':\n        return []\n",
    )
    helper.write_text(observed, encoding="utf-8")
    (payload.parents[1] / "libs/installer-engine/installer_engine_uninstall.py").write_text(
        observed, encoding="utf-8",
    )
    return support


def _internal_store(root: Path, bootstrap: Path, store: Path) -> Path:
    relative = bootstrap.relative_to(store)
    private = root / "prepared-store"
    copied = private / relative
    copied.parent.mkdir(parents=True)
    shutil.copy2(bootstrap, copied)
    prefix = private / relative.parts[0]
    original_prefix = store / relative.parts[0]
    if os.name == "nt":
        for dll in bootstrap.parent.glob("*.dll"):
            shutil.copy2(dll, copied.parent / dll.name)
        _link(prefix / "Lib", original_prefix / "Lib")
        if (original_prefix / "DLLs").is_dir():
            _link(prefix / "DLLs", original_prefix / "DLLs")
    else:
        (prefix / "lib").mkdir()
        _link(prefix / "lib/python3.11", original_prefix / "lib/python3.11")
    return private


@pytest.mark.parametrize("shell", HOSTS)
@pytest.mark.parametrize("materialized", (False, True), ids=("raw", "materialized"))
@pytest.mark.parametrize(
    "mode",
    ("remove", "no-flock", "preview", "missing-uv", "missing-python", "missing-provider",
     "internal-bootstrap", "foreign", "transport", "owner"),
)
def test_codespaces_prepared_uninstall_preserves_state_and_never_uses_ambient_python(
    tmp_path: Path,
    shell: str,
    materialized: bool,
    mode: str,
    prepared_uninstall_python: tuple[Path, Path, Path],
    record_property: Callable[[str, object], None],
) -> None:
    _host(shell)
    if mode == "no-flock" and shell != "bash":
        pytest.skip("Native PowerShell has no POSIX flock fallback")
    uv, bootstrap, store = prepared_uninstall_python
    payload, root, internal, durable = _case(
        tmp_path, "agent-codespaces", ".venv",
    )
    root.chmod(0o700)
    support = _prepared_payload(payload, materialized, shell)
    if mode == "foreign":
        marker = root / "deploy-manifest.json"
        data = json.loads(marker.read_text())
        data["source"]["plugin"] = "foreign-plugin"
        marker.write_text(json.dumps(data), encoding="utf-8")
    elif mode == "transport":
        (root / "sockets").mkdir()
        (root / "sockets/reconnect").write_bytes(b"retained transport")
    elif mode == "owner" and shell == "bash":
        unit = root.parent / ".config/systemd/user/agent-codespaces-owner.service"
        unit.parent.mkdir(parents=True)
        unit.write_bytes(b"fixture owner registration")
    elif mode == "missing-provider":
        provider = support / ("bootstrap-uv.sh" if shell == "bash" else "bootstrap-uv.ps1")
        provider.rename(provider.with_suffix(".unavailable"))
    tools = _path_bin(tmp_path / "private tools", None)
    if mode != "missing-uv":
        shutil.copy2(uv, tools / uv.name)
    for name in ("python", "python3", "py"):
        poison = tools / (name + ".cmd" if os.name == "nt" else name)
        poison.write_text(
            '@echo off\n> "%AMBIENT_MARKER%" echo called\nexit /b 91\n' if os.name == "nt" else
            '#!/bin/sh\nprintf called > "$AMBIENT_MARKER"\nexit 91\n', encoding="utf-8",
        )
        poison.chmod(0o700)
    env = _environment(root.parent, [tools])
    log = tmp_path / "uv-calls"
    empty_store = tmp_path / "empty-store"
    empty_store.mkdir()
    selected_store = empty_store if mode == "missing-python" else store
    if mode == "internal-bootstrap":
        selected_store = _internal_store(root, bootstrap, store)
    env.update(
        UV_PYTHON_INSTALL_DIR=str(selected_store), UV_CACHE_DIR=str(tmp_path / "uv-cache"),
        UV_OFFLINE="1", UV_PYTHON_DOWNLOADS="never",
        UV_PYTHON="unrelated-python", UV_NO_MANAGED_PYTHON="1",
        UV_CONFIG_FILE="missing-relative.toml", VIRTUAL_ENV=str(internal),
        PYTHONHOME="unrelated", PYTHONPATH="unrelated",
        AMBIENT_MARKER=str(tmp_path / "ambient-ran"), CODESPACES_UNINSTALL_UV_LOG=str(log),
        COPILOT_EXT_NO_FLOCK="1" if mode == "no-flock" else "0",
        COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC="2",
    )
    command = _install_command(tmp_path, payload, root, shell, dry_run=mode == "preview")
    if mode == "owner" and shell != "bash":
        wrapper = Path(command[-1])
        text = wrapper.read_text()
        text = text.replace(
            "function Get-ScheduledTask { param($ErrorAction) }",
            "function Get-ScheduledTask { param($ErrorAction) "
            "[pscustomobject]@{TaskName='agent-codespaces-owner'} }",
        )
        wrapper.write_text(text, encoding="utf-8")
    before = {
        str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
        for path in root.rglob("*")
    }
    if mode == "internal-bootstrap":
        evidence_env = {
            key: value for key, value in env.items()
            if key not in {"UV_CONFIG_FILE", "UV_PYTHON", "UV_NO_MANAGED_PYTHON",
                           "PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"}
        }
        evidence = subprocess.run(
            [str(uv), "python", "find", "--no-project", "--system", "--managed-python",
             "--no-python-downloads", "cpython@3.11.18"],
            env={**evidence_env, "UV_NO_CONFIG": "1"},
            capture_output=True, text=True, timeout=15, creationflags=FLAGS, check=False,
        )
        assert evidence.returncode == 0, evidence.stdout + evidence.stderr
        assert Path(evidence.stdout.strip()).resolve().is_relative_to(root.resolve())
    try:
        result = subprocess.run(
            command, cwd=tmp_path, env=env, capture_output=True, text=True,
            timeout=20, creationflags=FLAGS, check=False,
        )
    except subprocess.TimeoutExpired as error:
        pytest.fail(str(error.stderr))
    success = mode in ("remove", "no-flock", "preview")
    detail = result.stdout + result.stderr
    assert (result.returncode == 0) == success, detail
    assert not (tmp_path / "ambient-ran").exists(), detail
    calls = log.read_text().splitlines() if log.exists() else []
    assert not any("python install" in call or " run " in call for call in calls), calls
    if success:
        assert any("python find" in call and "--managed-python" in call
                   and "--no-python-downloads" in call for call in calls), calls
    if mode in ("remove", "no-flock"):
        assert not internal.exists(), detail
    else:
        assert before == {
            str(path.relative_to(root)): None if path.is_dir() else path.read_bytes()
            for path in root.rglob("*")
        }, detail
    if mode in ("foreign", "transport", "owner"):
        assert {"foreign": "foreign", "transport": "transport", "owner": "connection owner"}[
            mode
        ] in detail.lower(), detail
    _retained(root, durable)
    record_property("codespaces_prepared_uninstall", json.dumps({
        "shell": shell, "materialized": materialized, "mode": mode,
        "network": "disabled", "bootstrap_python": "3.11.18", "ambient_calls": 0,
        "uv_calls": calls,
    }))
