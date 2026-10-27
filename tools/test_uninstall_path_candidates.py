"""Activated installed venvs must not shadow later external uninstall interpreters."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import venv
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
BASH = str(GIT_BASH) if GIT_BASH.is_file() else shutil.which("bash")
PWSH = shutil.which("pwsh")
SITES = (
    ("agent-bridge", "install.sh", "_bridge_uninstall_tool"),
    ("agent-bridge", "uninstall-runtime.sh", "_finite_runtime_uninstall"),
    ("agent-dispatch", "uninstall-runtime.sh", "_finite_runtime_uninstall"),
    ("agent-dispatch", "uninstall-runtime.sh", "_finite_stop_tool"),
    ("agent-vault", "uninstall-runtime.sh", "_finite_runtime_uninstall"),
    ("agent-vault", "uninstall-runtime.sh", "_finite_stop_tool"),
    ("budget-guidance", "uninstall-runtime.sh", "_finite_runtime_uninstall"),
    ("agent-index", "install.sh", "_finite_index_uninstall"),
    ("agent-machines", "legacy-uninstall.sh", "legacy"),
    ("agent-containers", "legacy-uninstall.sh", "legacy"),
    ("agent-mcp", "init.sh", "mcp"),
    ("agent-logger", "install.sh", "logger-resolve"),
    ("agent-logger", "install.sh", "logger-check"),
)


def _posix(path: Path) -> str:
    value = path.as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


def _function(source: Path, name: str, powershell: bool = False) -> str:
    text = source.read_text(encoding="utf-8")
    start = text.index(f"function {name} {{" if powershell else f"{name}() {{")
    return text[start:text.index("\n}\n", start) + 2]


def _run(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed native hosts and fixture-owned argument vectors
        command, cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.fixture(scope="module")
def interpreters(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path, Path]:
    base = tmp_path_factory.mktemp("uninstall-path")
    root = base / "installed"
    installed = root / "versions" / "1.0.0"
    external = base / "external"
    for path in (installed, external):
        venv.EnvBuilder(with_pip=False, symlinks=False).create(path)
        if os.name == "nt":
            shutil.copyfile(path / "Scripts" / "python.exe", path / "Scripts" / "python3.exe")
        executable = path / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        probe = _run(
            [str(executable), "-I", "-B", "-c", "import sys; print(sys.prefix)"],
            base, dict(os.environ),
        )
        assert probe.returncode == 0 and Path(probe.stdout.strip()).resolve() == path.resolve()
    alias = base / "outside-alias"
    if os.name == "nt":
        if not PWSH:
            pytest.skip("PowerShell required for the native Windows metadata host")
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"  # noqa: E731
        result = _run(
            [PWSH, "-NoProfile", "-NonInteractive", "-Command",
             f"New-Item -ItemType Junction -Path {quote(alias)} -Target {quote(installed)} | Out-Null"],
            base, dict(os.environ),
        )
        assert result.returncode == 0, result.stderr
    else:
        alias.symlink_to(installed, target_is_directory=True)
    broken = base / "broken"
    broken.mkdir()
    for name in ("python", "python3"):
        path = broken / (name + (".exe" if os.name == "nt" else ""))
        path.write_bytes(b"not an executable interpreter\n")
        path.chmod(0o755)
    for relative in ("config.json", "tasks.db", "auth/custom", "unknown-state"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"retained operator bytes")
    return root, external, broken, alias


def _payload(tmp_path: Path, plugin: str, root: Path) -> tuple[Path, Path, dict[str, str]]:
    payload = tmp_path / "p"
    scripts = payload / "scripts"
    shutil.copytree(REPO / "plugins" / plugin / "scripts", scripts,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copyfile(REPO / "plugins" / plugin / "pyproject.toml", payload / "pyproject.toml")
    (root / "deploy-manifest.json").write_text(json.dumps({
        "schema_version": 3, "service": plugin,
        "source": {"kind": "local", "plugin": plugin},
    }), encoding="utf-8")
    receipt = tmp_path / "metadata-reached.json"
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_", "BUDGET_GUIDANCE_"))
        and key not in {"BASH_ENV", "ENV"}
    }
    home = tmp_path / "home"
    home.mkdir()
    env.update(HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1",
               SHADOW_ROOT=str(root), SHADOW_RECEIPT=str(receipt))
    for name, actual in (
        ("installer_engine_uninstall.py", REPO / "libs" / "installer-engine" / "installer_engine_uninstall.py"),
        ("installer_engine_stop.py", REPO / "libs" / "installer-engine" / "installer_engine_stop.py"),
        ("uninstall_runtime.py", REPO / "plugins" / plugin / "scripts" / "uninstall_runtime.py"),
        ("uninstall_guard.py", REPO / "plugins" / plugin / "scripts" / "uninstall_guard.py"),
    ):
        if not actual.is_file():
            continue
        (scripts / name).write_text(
            "import importlib.util,json,os,pathlib,sys\n"
            "root=pathlib.Path(os.environ['SHADOW_ROOT']).resolve()\n"
            "for value in (sys.executable,sys.prefix,sys.base_prefix):\n"
            " assert not pathlib.Path(value).resolve().is_relative_to(root)\n"
            f"spec=importlib.util.spec_from_file_location('actual_uninstall_metadata',{str(actual)!r})\n"
            "module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module\n"
            "spec.loader.exec_module(module)\n"
            "status=module.main()\n"
            "if status==0:\n"
            " pathlib.Path(os.environ['SHADOW_RECEIPT']).write_text(json.dumps(dict("
            "executable=sys.executable,prefix=sys.prefix,base_prefix=sys.base_prefix)))\n"
            "raise SystemExit(status)\n",
            encoding="utf-8",
        )
    return scripts, receipt, env


def _state(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        name: ((root / name).read_bytes(), (root / name).stat().st_mtime_ns)
        for name in ("config.json", "tasks.db", "auth/custom", "unknown-state", "deploy-manifest.json")
    }


def _body(source: Path, kind: str) -> str:
    text = source.read_text(encoding="utf-8")
    if kind == "legacy":
        start = text.index('python="$(_uninstall_external_python "$root")"')
        end = text.index('"$python" "${arguments[@]}" --check', start)
        return text[start:end] + '"$python" "${arguments[@]}" --check'
    if kind == "mcp":
        start = text.index('UNINSTALL_PYTHON="$(_uninstall_external_python "$INSTALL_DIR")"')
        end = text.index('if [[ "$uninstall_status" == absent ]]', start)
        return text[start:end]
    if kind.startswith("logger-"):
        if kind == "logger-resolve":
            start = text.index('py="$(_uninstall_external_python "$INSTALL_DIR")"')
            end = text.index('--resolve-root)" || exit 1', start) + len('--resolve-root)" || exit 1')
        else:
            start = text.index('py="$(_uninstall_external_python "$INSTALL_DIR")"', text.index("  uninstall)"))
            end = text.index('"$py" -I -B "$SCRIPT_DIR/uninstall_guard.py" --root "$INSTALL_DIR" || exit 1', start)
            end += len('"$py" -I -B "$SCRIPT_DIR/uninstall_guard.py" --root "$INSTALL_DIR" || exit 1')
        return text[start:end]
    call = (
        f'{kind} check-unit --unit "$UNIT"' if kind.endswith("_tool") else
        f'{kind} --check "$INSTALL_DIR"' if kind == "_finite_index_uninstall" else
        f"{kind} --check"
    )
    return _function(source, kind) + "\n" + call


@pytest.mark.parametrize("site", SITES, ids=[f"{p}-{kind}" for p, _, kind in SITES])
@pytest.mark.parametrize("availability", ("external", "broken-then-external", "alias-then-external", "none"))
def test_activated_runtime_does_not_shadow_native_uninstall_metadata(
    tmp_path: Path, interpreters: tuple[Path, Path, Path, Path],
    site: tuple[str, str, str], availability: str,
) -> None:
    if not BASH:
        pytest.skip("Bash unavailable")
    plugin, filename, kind = site
    root, external, broken, alias = interpreters
    scripts, receipt, env = _payload(tmp_path, plugin, root)
    binary = "Scripts" if os.name == "nt" else "bin"
    directories = [root / "versions" / "1.0.0" / binary]
    if availability == "broken-then-external":
        directories.append(broken)
    if availability == "alias-then-external":
        directories.append(alias / binary)
    if availability != "none":
        directories.append(external / binary)
    # Keep only non-Python utility directories after the explicitly controlled
    # candidate sequence; the no-external case must really have no fallback.
    utility = Path(BASH).parent.parent / "usr" / "bin" if os.name == "nt" else None
    if utility is None:
        utility = tmp_path / "utilities"
        utility.mkdir()
        for name in ("dirname", "bash"):
            executable = shutil.which(name)
            assert executable is not None, f"Required native fixture utility missing: {name}"
            (utility / name).symlink_to(executable)
    if utility is not None:
        directories.append(utility)
    if os.name == "nt" and PWSH:
        directories.append(Path(PWSH).parent)
    before = _state(root)
    unit = tmp_path / "fixture.service"
    module = plugin.replace("-", "_") + (".service" if plugin == "agent-vault" else "")
    tail = "--foreground --persistent --handoff-stdin" if plugin == "agent-vault" else "start" if plugin == "agent-bridge" else "serve"
    unit.write_text(
        f"[Service]\nExecStart={_posix(root)}/versions/1.0.0/bin/python -m {module} {tail}\n"
        f"WorkingDirectory={_posix(root)}\n", encoding="utf-8",
    )
    source = REPO / "plugins" / plugin / "scripts" / filename
    script = tmp_path / "invoke.sh"
    script.write_text("\n".join((
        "set -euo pipefail",
        f"PATH={shlex.quote(':'.join(_posix(path) for path in directories))}",
        f"INSTALL_DIR={shlex.quote(_posix(root))};root=$INSTALL_DIR",
        f"SCRIPT_DIR={shlex.quote(_posix(scripts))};PLUGIN_DIR=${{SCRIPT_DIR%/*}}",
        f"UNIT={shlex.quote(_posix(unit))};helper=$SCRIPT_DIR/installer_engine_uninstall.py",
        '_fail() { printf "%s\\n" "$1" >&2; }',
        _function(source, "_uninstall_external_python"),
        _body(source, kind),
        "echo metadata-complete",
    )), encoding="utf-8", newline="\n")
    result = _run([BASH, "--noprofile", "--norc", _posix(script)], tmp_path, env)
    assert (result.returncode == 0) is (availability != "none"), result.stdout + result.stderr
    assert receipt.exists() is (availability != "none"), result.stdout + result.stderr
    assert _state(root) == before
    if receipt.exists():
        observed = json.loads(receipt.read_text())
        assert all(not Path(value).resolve().is_relative_to(root.resolve()) for value in observed.values())


@pytest.mark.skipif(os.name != "nt" or PWSH is None, reason="Native PowerShell selector controls")
@pytest.mark.parametrize("plugin", ("agent-machines", "agent-containers"))
@pytest.mark.parametrize("availability", ("external", "broken-then-external", "alias-then-external", "none"))
def test_windows_legacy_uninstall_enumerates_all_external_candidates(
    tmp_path: Path, interpreters: tuple[Path, Path, Path, Path], plugin: str, availability: str,
) -> None:
    root, external, broken, alias = interpreters
    scripts, receipt, env = _payload(tmp_path, plugin, root)
    directories = [root / "versions" / "1.0.0" / "Scripts"]
    if availability == "broken-then-external":
        directories.append(broken)
    if availability == "alias-then-external":
        directories.append(alias / "Scripts")
    if availability != "none":
        directories.append(external / "Scripts")
    before = _state(root)
    source = REPO / "plugins" / plugin / "scripts" / "legacy-uninstall.ps1"
    helper = scripts / "installer_engine_uninstall.py"
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"  # noqa: E731
    script = tmp_path / "invoke.ps1"
    script.write_text("\n".join((
        "$ErrorActionPreference='Stop'",
        f"$env:PATH={quote(os.pathsep.join(str(path) for path in directories))}",
        f"$InstallDir={quote(root)}",
        f". {quote(scripts / 'versioned_runtime_native.ps1')}",
        _function(source, "Get-LegacyUninstallPython", powershell=True),
        "$python=Get-LegacyUninstallPython",
        "if (-not $python) { throw 'No existing external Python' }",
        f"$output=Invoke-Hidden -FilePath $python -ArgList @('-I','-B',{quote(helper)},"
        f"'--payload-root',{quote(scripts.parent)},'--plugin-id',{quote(plugin)},"
        f"'--root',{quote(root)},'--shell',{quote(PWSH)},"
        f"'--profile',{quote(scripts / 'uninstall-profile.json')},'--check')",
        "if ($LASTEXITCODE -ne 0) { throw 'Actual uninstall metadata refused' }",
    )), encoding="utf-8")
    result = _run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(script)], tmp_path, env)
    assert (result.returncode == 0) is (availability != "none"), result.stdout + result.stderr
    assert receipt.exists() is (availability != "none"), result.stdout + result.stderr
    assert _state(root) == before
