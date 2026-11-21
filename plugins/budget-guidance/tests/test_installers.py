from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import ModuleType, SimpleNamespace

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
PWSH = shutil.which("pwsh")
BASE_PYTHON = str(getattr(sys, "_base_executable", sys.executable))
NO_WINDOW = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
VERSION = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]

pytestmark = pytest.mark.guard


def _copy_payload(target: Path) -> None:
    def ignore(directory: str, names: list[str]) -> set[str]:
        omitted = {"__pycache__", ".pytest_cache"}
        if Path(directory) == PLUGIN:
            omitted.add("tests")
        return omitted.intersection(names)

    pending = []
    with ThreadPoolExecutor(max_workers=16) as copies:
        def queue_copy(source: str, destination: str) -> str:
            pending.append(copies.submit(shutil.copy2, source, destination))
            return destination

        shutil.copytree(PLUGIN, target, ignore=ignore, copy_function=queue_copy)
        for future in pending:
            future.result()
    previous = sys.path.copy()
    sys.path.insert(0, str(PLUGIN.parents[1] / "tools"))
    try:
        materialize_main = importlib.import_module("materialize_main")
    finally:
        sys.path[:] = previous
    changes = materialize_main.materialize_file_pointers(target, canonical_root=PLUGIN.parents[1])
    changes.extend(materialize_main.materialize_uv_editable_ref_into(
        source_consumer_dir=PLUGIN, dest_consumer_dir=target,
        canonical_root=PLUGIN.parents[1],
    ))
    if (PLUGIN / "payload-invocation.json").is_file():
        changes.extend(materialize_main.materialize_installer_engine_ref_into(
            source_consumer_dir=PLUGIN, dest_consumer_dir=target,
            canonical_root=PLUGIN.parents[1],
        ))
        changes.extend(materialize_main.materialize_payload_invocation_into(
            source_consumer_dir=PLUGIN, dest_consumer_dir=target,
            canonical_root=PLUGIN.parents[1],
        ))
    assert not any(change.startswith("SKIP ") for change in changes), changes
    procutil = target / "libs" / "agent-procutil"
    assert (procutil / "pyproject.toml").is_file(), "Missing agent-procutil fixture manifest"
    assert (procutil / "src" / "agent_procutil" / "__init__.py").is_file(), (
        "Missing agent-procutil fixture package"
    )


def _isolate_global_pip_files(helper: Path, configuration: Path) -> None:
    """Relocate only the copied reader's platform-global file provider."""
    source = helper.read_text(encoding="utf-8")
    marker = 'if __name__ == "__main__":'
    assert source.count(marker) == 1
    override = (
        "def _global_files(_environment: object, _platform: str) -> list[Path]:\n"
        f"    return [Path({str(configuration)!r})]\n\n\n"
    )
    helper.write_text(source.replace(marker, override + marker), encoding="utf-8")


def _resolved_uv_defaults(output: str, native: ModuleType) -> list[str]:
    projection = native._projection(output, "PipInstallSettings")
    settings = native._fields(native._body(projection["settings"], "PipSettings"))
    locations = native._fields(native._body(settings["index_locations"], "IndexLocations"))
    body = native._body(locations["indexes"], "", "[", "]")
    defaults: list[str] = []
    while body.strip():
        item, end = native.dependencies._balanced(body, body.index("{"), "{", "}")
        fields = native._fields(item)
        if fields["default"] == "true":
            defaults.append(native._index_url(fields["url"])[0])
        body = body[end:].strip().removeprefix(",")
    return defaults


def test_installers_recommend_supported_version_flag() -> None:
    posix = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "Try: budget-guidance --version" in posix
    assert "Try: budget-guidance --version" in powershell
    assert "Try: budget-guidance version" not in posix
    assert "Try: budget-guidance version" not in powershell


def test_package_installers_retain_quiet_native_output_with_explicit_failure() -> None:
    posix = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    assert '"${dependencies[@]}" "$PLUGIN_DIR" --quiet' in posix
    assert "$pipSources + @('--quiet')" in powershell
    assert "installer_dependency_policy.py" in posix and "installer_dependency_policy.py" in powershell
    assert '-m pip install' not in posix and '-m pip install' not in powershell
    assert "Failed to install budget-guidance package into venv" in posix
    assert "Failed to install budget-guidance package into venv" in powershell


def _fake_uv_environment(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    driver = tmp_path / "fake_uv.py"
    driver.write_text(
        """
import os
import sys

args = sys.argv[1:]
base = os.environ["TEST_BASE_PYTHON"]
if os.environ.get("TEST_ASSERT_CLEAN_UV") == "1" and args and args[0] in ("python", "venv", "pip"):
    hints = ("UV_PYTHON", "UV_PYTHON_PREFERENCE", "UV_MANAGED_PYTHON", "UV_NO_MANAGED_PYTHON",
             "VIRTUAL_ENV", "CONDA_PREFIX", "UV_WORKING_DIR", "UV_WORKING_DIRECTORY",
             "UV_ISOLATED", "UV_ENV_FILE", "UV_PROJECT", "UV_FROZEN", "UV_LOCKED", "UV_NO_SYNC")
    inherited = [name for name in hints if name in os.environ]
    if inherited:
        print("unexpected inherited uv hints: " + ", ".join(inherited), file=sys.stderr)
        raise SystemExit(94)
if args == ["--version"]:
    print("uv 0.12.5")
    raise SystemExit(0)
if args[:2] == ["python", "install"]:
    raise SystemExit(0)
if args and args[0] == "run":
    selected = args.index("--python") + 1
    assert args[selected + 1] == "python"
    os.execv(args[selected], [args[selected], *args[selected + 2:]])
if args[:2] == ["python", "find"]:
    print(base)
    raise SystemExit(0)
if "--show-settings" in args:
    if os.environ.get("TEST_EXPECT_UV_DEFAULT_INDEX"):
        assert os.environ.get("UV_DEFAULT_INDEX") == os.environ["TEST_EXPECT_UV_DEFAULT_INDEX"]
    if os.environ.get("TEST_EXPECT_UV_CONFIG_INDEX"):
        assert "UV_DEFAULT_INDEX" not in os.environ
        with open(os.environ["UV_CONFIG_FILE"], encoding="utf-8") as config:
            assert os.environ["TEST_EXPECT_UV_CONFIG_INDEX"] in config.read()
    if os.environ.get("TEST_EXPECT_UV_DEFAULT_INDEX") or os.environ.get("TEST_EXPECT_UV_CONFIG_INDEX"):
        with open(os.environ["TEST_UV_INDEX_LOG"], "a", encoding="utf-8") as log:
            log.write("configured-index-observed\\n")
    print("PipInstallSettings {\\n settings: PipSettings {\\n"
          " index_locations: IndexLocations { indexes: [], flat_index: [], },\\n"
          " exclude_newer: ExcludeNewer {\\n"
          " global: None, package: ExcludeNewerPackage({}),\\n },\\n },\\n}")
    raise SystemExit(0)
from pathlib import Path
import shutil
import site
import subprocess
import json

if args and args[0] == "venv":
    target = Path(args[1])
    log = os.environ.get("TEST_UV_VENV_LOG")
    if log:
        with Path(log).open("a", encoding="utf-8") as stream:
            stream.write(str(target) + "\\n")
    rc = subprocess.run(
        [base, "-I", "-S", "-m", "venv", "--without-pip", str(target)],
        check=False,
    ).returncode
    if rc == 0 and os.environ.get("TEST_BUILD_ARTIFACT_PROBE"):
        python = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        site_dir = subprocess.check_output(
            [str(python), "-c", "import site; print(site.getsitepackages()[0])"], text=True,
        ).strip()
        pip = Path(site_dir) / "pip"
        pip.mkdir()
        (pip / "__init__.py").write_text("", encoding="utf-8")
        (pip / "__main__.py").write_text(
            "import os, runpy\\n"
            "runpy.run_path(os.environ['TEST_UV_DRIVER'], run_name='__main__')\\n",
            encoding="utf-8",
        )
    raise SystemExit(rc)
is_pip = args[:1] == ["install"]
if is_pip:
    args = ["pip", "install", "--python", sys.executable, *args[1:]]
if args[:2] == ["pip", "install"]:
    python = args[args.index("--python") + 1]
    plugin = next(Path(value) for value in args[2:]
                  if (Path(value) / "src" / "budget_guidance").is_dir())
    if os.environ.get("TEST_BUILD_ARTIFACT_PROBE"):
        sources = [Path(arg) for arg in args if (Path(arg) / "pyproject.toml").is_file()]
        artifacts = []
        for source in sources:
            artifacts.extend(source.glob("*.egg-info"))
            artifacts.extend((source / "src").glob("*.egg-info"))
            if (source / "build").exists():
                artifacts.append(source / "build")
        assert not artifacts, f"stale artifacts reached build: {artifacts}"
        with Path(os.environ["TEST_INSTALL_ATTEMPTS_LOG"]).open("a", encoding="utf-8") as log:
            log.write(json.dumps({"engine": "pip" if is_pip else "uv", "sources": len(sources)}) + "\\n")
        for source in sources:
            (source / "build" / "lib").mkdir(parents=True)
            (source / "stale.egg-info").mkdir()
            (source / "src" / "stale.egg-info").mkdir()
        failure = int(os.environ.get("TEST_INSTALL_EXIT_CODE", "9"))
        if failure:
            raise SystemExit(failure)
    assert "--exclude-newer" in args and "--reinstall" in args
    site_dir = subprocess.check_output(
        [python, "-c", "import site; print(site.getsitepackages()[0])"],
        text=True,
    ).strip()
    procutil = next(Path(arg) for arg in args if Path(arg).name == "agent-procutil")
    from concurrent.futures import ThreadPoolExecutor
    pending = []
    with ThreadPoolExecutor(max_workers=16) as copies:
        def queue_copy(source, destination):
            pending.append(copies.submit(shutil.copy2, source, destination))
            return destination
        for source, package in (
            (plugin, "budget_guidance"), (procutil, "agent_procutil"),
        ):
            shutil.copytree(
                source / "src" / package, Path(site_dir) / package,
                dirs_exist_ok=True, copy_function=queue_copy,
            )
        for future in pending:
            future.result()
    raise SystemExit(0)
raise SystemExit(2)
""".lstrip(),
        encoding="utf-8",
    )
    uv = fake_bin / "uv"
    uv.write_text(
        f"#!/bin/sh\nexec '{BASE_PYTHON}' '{driver}' \"$@\"\n",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    for name in ("python", "python3", "py"):
        command = fake_bin / name
        command.write_text("#!/bin/sh\nexit 127\n", encoding="utf-8")
        command.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "TEST_BASE_PYTHON": BASE_PYTHON,
        "TEST_UV_DRIVER": str(driver),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
        "POWERSHELL_TELEMETRY_OPTOUT": "1",
        "COMPUTERNAME": "TEST-HOST",
    }
    env.pop("COPILOT_PLUGIN_INSTALL_STAGED", None)
    env.pop("COPILOT_PLUGIN_STAGED_FROM", None)
    Path(env["HOME"]).mkdir()
    return fake_bin, env


def _poison_uv_environment(env: dict[str, str], root: Path) -> None:
    foreign = root / "foreign directory"
    foreign.mkdir()
    env.update({
        "TEST_ASSERT_CLEAN_UV": "1", "UV_PYTHON": "must-not-be-selected",
        "UV_PYTHON_PREFERENCE": "only-system", "UV_MANAGED_PYTHON": "1",
        "UV_NO_MANAGED_PYTHON": "1", "VIRTUAL_ENV": str(foreign),
        "CONDA_PREFIX": str(foreign), "UV_WORKING_DIR": str(foreign),
        "UV_WORKING_DIRECTORY": str(foreign),
        "UV_ISOLATED": "1", "UV_ENV_FILE": str(root / "missing.env"),
        "UV_PROJECT": str(root / "missing-project"),
        "UV_FROZEN": "1", "UV_LOCKED": "1", "UV_NO_SYNC": "1",
    })


@pytest.fixture(scope="module", params=[False, True], ids=["fresh", "retained"])
def native_failed_install_state(
    tmp_path_factory: pytest.TempPathFactory, request: pytest.FixtureRequest,
) -> SimpleNamespace:
    if os.name != "nt" or PWSH is None:
        pytest.skip("native Windows installer")
    tmp_path = tmp_path_factory.mktemp("budget-failure")
    previous = request.param
    fake_bin, env = _fake_uv_environment(tmp_path)
    _poison_uv_environment(env, tmp_path)
    powershell_home = Path(PWSH).resolve().parent
    git = shutil.which("git")
    assert git is not None, "native installer fixture requires Git"
    system = Path(os.environ["SystemRoot"])
    env["PATH"] = os.pathsep.join(str(path) for path in (
        fake_bin, powershell_home, Path(git).parent, system / "System32", system,
    ))
    env["PSModulePath"] = str(powershell_home / "Modules")
    (fake_bin / "uv").unlink()
    for name in ("python", "python3", "py"):
        (fake_bin / name).unlink()
        prefix = (
            '@echo off\nsetlocal EnableDelayedExpansion\nif "%~1"=="-3" (\n'
            f'  "{BASE_PYTHON}" %2 %3 %4 %5 %6 %7 %8 %9\n'
            '  exit /b !errorlevel!\n)\n'
        ) if name == "py" else "@echo off\n"
        (fake_bin / f"{name}.cmd").write_text(
            prefix + f'"{BASE_PYTHON}" %*\nexit /b %errorlevel%\n',
            encoding="utf-8",
        )
    driver = tmp_path / "fake_uv.py"
    source = driver.read_text(encoding="utf-8")
    source = source.replace(
        'if args and args[0] == "venv":',
        'if args == ["--version"]:\n    print("uv 0.12.5")\n    raise SystemExit(0)\n'
        'if args and args[0] == "venv":',
    )
    source = source.replace(
        'if args[:2] == ["pip", "install"]:\n',
        'if args[:2] == ["pip", "install"]:\n'
        '    if os.environ.get("TEST_DEPENDENCY_FAIL") == "1":\n'
        '        python = Path(args[args.index("--python") + 1])\n'
        '        (python.parent.parent / "partial-install").write_text("partial")\n'
        '        print("controlled dependency failure 29", file=sys.stderr)\n'
        '        raise SystemExit(29)\n',
    )
    driver.write_text(source, encoding="utf-8")
    (fake_bin / "uv.cmd").write_text(
        f'@echo off\n"{BASE_PYTHON}" -I -S "{driver}" %*\nexit /b %errorlevel%\n',
        encoding="utf-8",
    )
    root = tmp_path / "runtime with spaces"
    payload = tmp_path / "payload" / "budget-guidance"
    _copy_payload(payload)
    old = root / "versions" / "0.0.1"
    if previous:
        old.mkdir(parents=True)
        (old / "live").write_text("untouched")
        (old / ".install-complete.json").write_text(json.dumps({
            "version": "0.0.1", "completed_at": "2026-01-01T00:00:00Z",
            "pid": 1, "payload_hash": "0" * 64,
        }))
        (root / "current-version").write_text("0.0.1")
    command = [
        PWSH, "-NoProfile", "-NonInteractive", "-File",
        str(payload / "scripts" / "install.ps1"),
        "install", "-InstallDir", str(root),
    ]
    before = (root / "current-version").read_bytes() if previous else None
    failed = subprocess.run(
        command, env={**env, "TEST_DEPENDENCY_FAIL": "1"}, cwd=tmp_path,
        capture_output=True, text=True, timeout=45,
        creationflags=NO_WINDOW,
    )
    selected = root / "current-version"
    return SimpleNamespace(
        root=root, old=old, command=command, env=env, tmp_path=tmp_path,
        previous=previous, before=before, failed=failed,
        failed_slot_exists=(root / "versions" / VERSION).exists(),
        failed_manifest_exists=(root / "deploy-manifest.json").exists(),
        selected_after_failure=selected.read_bytes() if selected.exists() else None,
        retained_after_failure=(old / "live").read_text() if previous else None,
    )


def _assert_failed_install(state: SimpleNamespace) -> None:
    assert state.failed.returncode != 0, state.failed.stdout + state.failed.stderr
    assert "Canonical dependency installation exited 29" in state.failed.stdout, (
        state.failed.stdout + state.failed.stderr
    )
    assert not state.failed_slot_exists and not state.failed_manifest_exists
    assert state.selected_after_failure == state.before
    if state.previous:
        assert state.retained_after_failure == "untouched"


def test_native_failed_install_preserves_selection(native_failed_install_state: SimpleNamespace) -> None:
    _assert_failed_install(native_failed_install_state)


def test_native_retry_after_failure_publishes_clean_slot(native_failed_install_state: SimpleNamespace) -> None:
    state = native_failed_install_state
    _assert_failed_install(state)
    retried = subprocess.run(
        state.command, env=state.env, cwd=state.tmp_path, capture_output=True, text=True,
        timeout=45, creationflags=NO_WINDOW,
    )
    assert retried.returncode == 0, retried.stdout + retried.stderr
    assert not (state.root / "versions" / VERSION / "partial-install").exists()
    assert (state.root / "current-version").read_text().strip() == VERSION
    if state.previous:
        assert (state.old / "live").read_text() == "untouched"


def test_posix_installer_is_uv_first_when_ambient_python_is_absent() -> None:
    source = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert source.index('bootstrap-python-version.txt" Prepare') < source.index(
        "    _initialize_source_runtime || exit 1"
    )
    assert 'venv "$VENV_DIR" --python "$PYTHON_CMD" --managed-python' in source
    assert "Python fallback" not in source


def test_powershell_installer_is_uv_first_when_ambient_python_is_absent() -> None:
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    assert source.index("Get-BootstrapPython -Prepare") < source.index(
        "if ($Action -in @('install', 'update', 'stamp', 'provision')) { Initialize-SourceRuntime }"
    )
    assert "'--python', $pythonCmd, '--managed-python'" in source
    assert "Get-AuthenticodeSignature" not in source
    assert "Python fallback" not in source


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="portable pwsh self-stage coverage")
def test_powershell_self_stage_preserves_spaced_paths_and_force(tmp_path: Path) -> None:
    assert PWSH
    _, env = _fake_uv_environment(tmp_path)
    profile = tmp_path / "profile with spaces"
    profile.mkdir()
    env["HOME"] = str(profile)
    env["USERPROFILE"] = str(profile)
    installed = (
        profile
        / ".copilot"
        / "installed-plugins"
        / "market with spaces"
        / "budget-guidance"
    )
    _copy_payload(installed)
    custom_root = tmp_path / "custom runtime with spaces"
    venv_log = tmp_path / "powershell uv venv.log"
    env["TEST_UV_VENV_LOG"] = str(venv_log)

    stamp = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(installed / "scripts" / "install.ps1"),
            "stamp",
            "-InstallDir",
            str(custom_root),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stdout
    snapshot = Path((custom_root / "payload-dir").read_text(encoding="utf-8"))
    assert snapshot == custom_root / "snapshots" / VERSION
    assert (snapshot / "scripts" / "install.ps1").is_file()

    install = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(installed / "scripts" / "install.ps1"),
            "install",
            "-InstallDir",
            str(custom_root),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert install.returncode == 0, install.stdout
    assert venv_log.read_text(encoding="utf-8").splitlines() == [
        str(custom_root / "versions" / VERSION)
    ]

    capture = tmp_path / "forwarded arguments.json"
    forced = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(installed / "scripts" / "install.ps1"),
            "install",
            "-InstallDir",
            str(custom_root),
            "-Force",
        ],
        cwd=tmp_path,
        env={
            **env,
            "BUDGET_GUIDANCE_TEST_ARGUMENT_CAPTURE": str(capture),
        },
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert forced.returncode == 0, forced.stdout
    assert json.loads(capture.read_text(encoding="utf-8-sig")) == {
        "action": "install",
        "installDir": str(custom_root),
        "force": True,
        "staged": True,
    }


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX installer coverage")
@pytest.mark.parametrize("feed_policy", [
    "pip-config", "uv-config", "active-venv", "active-conda", "global-config",
])
def test_posix_installer_bootstraps_with_uv_and_no_ambient_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, feed_policy: str,
) -> None:
    assert BASH
    monkeypatch.syspath_prepend(str(PLUGIN.parents[1] / "tools"))
    registry = importlib.import_module("installer_engine_ref")
    monkeypatch.setitem(registry.STANDALONE_NATIVE_EXTRA_FILES, "plugins/budget-guidance", (
        "bootstrap-uv.ps1", "bootstrap-uv.sh",
    ))
    fake_bin, env = _fake_uv_environment(tmp_path)
    for variable in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_CONFIG_FILE", "UV_NO_CONFIG",
                     "PIP_INDEX_URL", "VIRTUAL_ENV", "CONDA_PREFIX"):
        env.pop(variable, None)
    pip_log, index_log = tmp_path / "ambient-pip.log", tmp_path / "uv-index.log"
    index = "https://configured.example/a%2Fb/simple"
    config = tmp_path / "pip.conf"
    config.write_text(f"[global]\nindex-url = {index}\n", encoding="utf-8")
    uv_config = tmp_path / "uv.toml"
    uv_config.write_text("", encoding="utf-8")
    env.update({
        "PIP_CONFIG_FILE": str(config), "TEST_PIP_LOG": str(pip_log),
        "TEST_EXPECT_UV_DEFAULT_INDEX": index, "TEST_UV_INDEX_LOG": str(index_log),
        "UV_CONFIG_FILE": str(uv_config),
    })
    global_config = tmp_path / "global-pip.conf"
    global_index = index if feed_policy == "global-config" else "https://global.example/simple"
    global_config.write_text(f"[global]\nindex-url = {global_index}\n", encoding="utf-8")
    if feed_policy == "uv-config":
        uv_config.write_text(f'[[index]]\nurl = "{index}"\ndefault = true\n', encoding="utf-8")
        env.pop("TEST_EXPECT_UV_DEFAULT_INDEX")
        env.update({
            "UV_CONFIG_FILE": str(uv_config), "TEST_EXPECT_UV_CONFIG_INDEX": index,
            "PIP_INDEX_URL": "https://not-selected.example/simple",
        })
        actual_uv = shutil.which("uv")
        assert actual_uv, "The configured-index control requires uv"
        resolved = subprocess.run(
            [actual_uv, "pip", "install", "--python", BASE_PYTHON,
             "--show-settings", "budget-guidance"],
            cwd=tmp_path, env={
                "PATH": env["PATH"], "HOME": env["HOME"], "USERPROFILE": env["USERPROFILE"],
                "UV_CONFIG_FILE": str(uv_config), "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
                "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never",
                "PIP_INDEX_URL": "https://not-selected.example/simple",
            },
            capture_output=True, text=True, timeout=30,
        )
        assert resolved.returncode == 0, resolved.stdout + resolved.stderr
        monkeypatch.syspath_prepend(str(PLUGIN.parents[1] / "libs/installer-engine"))
        native = importlib.import_module("installer_native_feed")
        assert _resolved_uv_defaults(resolved.stdout + resolved.stderr, native) == [index]
    else:
        active = tmp_path / "active-environment"
        active.mkdir()
        if feed_policy != "active-conda":
            (active / "pyvenv.cfg").write_text("home = /unusable-python\n", encoding="utf-8")
            env["VIRTUAL_ENV"] = str(active)
        else:
            (active / "conda-meta").mkdir()
            env["CONDA_PREFIX"] = str(active)
        if feed_policy in ("active-venv", "active-conda"):
            (active / "pip.conf").write_text(f"[global]\nindex-url = {index}\n", encoding="utf-8")
        if feed_policy != "pip-config":
            env["PIP_CONFIG_FILE"] = str(tmp_path / "missing-pip.conf")
        env["TEST_ASSERT_CLEAN_UV"] = "1"
    for name in ("pip", "pip3"):
        command = fake_bin / name
        command.write_text('#!/bin/sh\nprintf touched >> "$TEST_PIP_LOG"\nexit 91\n', encoding="utf-8")
        command.chmod(0o755)
    install_dir = Path(env["HOME"]) / ".budget-guidance"
    payload = tmp_path / "budget-guidance"
    _copy_payload(payload)
    assert (payload / "scripts/pip-index-config").read_bytes() == (
        PLUGIN.parents[1] / "libs/installer-engine/installer_pip_index.py"
    ).read_bytes()
    _isolate_global_pip_files(payload / "scripts/pip-index-config", global_config)

    result = subprocess.run(
        [BASH, str(payload / "scripts" / "install.sh"), "install", "--install-dir", str(install_dir)],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    assert (install_dir / "current-version").is_file()
    assert not pip_log.exists(), "The installer launched an unrelated PATH pip"
    observations = index_log.read_text(encoding="utf-8").splitlines()
    assert observations and set(observations) == {"configured-index-observed"}
    if feed_policy != "uv-config":
        selected = (install_dir / "current-version").read_bytes()
        completion = install_dir / "versions" / selected.decode().strip() / ".install-complete.json"
        completed_before = completion.read_bytes()
        index_before = index_log.read_bytes()
        invalid = tmp_path / "invalid-pip.conf"
        invalid.write_text("invalid configuration\n", encoding="utf-8")
        reused = subprocess.run(
            [BASH, str(payload / "scripts/install.sh"), "install", "--install-dir", str(install_dir)],
            cwd=tmp_path, env={**env, "PIP_CONFIG_FILE": str(invalid)},
            capture_output=True, text=True, timeout=120,
        )
        assert reused.returncode == 0, reused.stdout + reused.stderr
        assert (install_dir / "current-version").read_bytes() == selected
        assert completion.read_bytes() == completed_before
        assert index_log.read_bytes() == index_before
        assert not pip_log.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX real uv system-config coverage")
def test_real_uv_system_config_disable_is_preserved_in_retained_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    uv = shutil.which("uv")
    assert uv, "The retained-settings control requires uv"
    project = tmp_path / "project"
    project.mkdir()
    (project / "uv.toml").write_text("", encoding="utf-8")
    user = tmp_path / "user/uv/uv.toml"
    user.parent.mkdir(parents=True)
    user.write_text("", encoding="utf-8")
    system = tmp_path / "system/uv/uv.toml"
    system.parent.mkdir(parents=True)
    index = "https://system.example/simple"
    system.write_text(f'[[index]]\nurl = "{index}"\ndefault = true\n', encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"), "XDG_CONFIG_HOME": str(tmp_path / "user"),
        "XDG_CONFIG_DIRS": str(tmp_path / "system"), "UV_OFFLINE": "1",
        "UV_CACHE_DIR": str(tmp_path / "cache"), "UV_PYTHON_DOWNLOADS": "never",
    }
    original = subprocess.run(
        [uv, "pip", "install", "--python", BASE_PYTHON, "--show-settings", "example-package>=1"],
        cwd=project, env=env, capture_output=True, text=True, timeout=30,
    )
    assert original.returncode == 0, original.stdout + original.stderr
    monkeypatch.syspath_prepend(str(PLUGIN.parents[1] / "libs/installer-engine"))
    native = importlib.import_module("installer_native_feed")
    assert _resolved_uv_defaults(original.stdout + original.stderr, native) == [index]
    with native.dependencies.retain_uv_install(
        uv, ["--python", BASE_PYTHON, "example-package>=1"],
        env={**env, "UV_NO_SYSTEM_CONFIG": "1"}, cwd=project,
    ) as retained:
        configuration = Path(retained.environment["UV_CONFIG_FILE"]).read_text(encoding="utf-8")
        assert index not in configuration


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX clean-room snapshot coverage")
def test_posix_cleanroom_snapshot_provisions_without_index_environment(tmp_path: Path) -> None:
    assert BASH
    _, env = _fake_uv_environment(tmp_path)
    for variable in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "PIP_INDEX_URL", "VIRTUAL_ENV", "CONDA_PREFIX"):
        env.pop(variable, None)
    uv_config = tmp_path / "uv.toml"
    uv_config.write_text("", encoding="utf-8")
    env.update(UV_CONFIG_FILE=str(uv_config), PIP_CONFIG_FILE=os.devnull)
    scenario = PLUGIN.parents[1] / "tools/clean-room/scenarios/budget-guidance-solo/scenario.sh"
    source = scenario.read_text(encoding="utf-8")
    marker = "<<'PY'\n"
    start = source.index(marker, source.index('capture "copy-candidate"')) + len(marker)
    recipe = source[start:source.index("\nPY\nthen", start)]
    fresh = tmp_path / "candidate"
    fresh.mkdir()
    copied = subprocess.run(
        [BASE_PYTHON, "-I", "-B", "-", str(PLUGIN.parents[1]), str(fresh)],
        input=recipe, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert copied.returncode == 0, copied.stdout + copied.stderr
    payload = fresh / "plugins/budget-guidance"
    root = Path(env["HOME"]) / ".budget-guidance"
    stamped = subprocess.run(
        [BASH, str(payload / "scripts/install.sh"), "stamp"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    snapshot = Path((root / "payload-dir").read_text(encoding="utf-8").strip())
    assert (snapshot / "scripts/pip-index-config").read_bytes() == (
        PLUGIN.parents[1] / "libs/installer-engine/installer_pip_index.py"
    ).read_bytes()
    shutil.rmtree(fresh)
    provisioned = subprocess.run(
        [BASH, str(snapshot / "scripts/install.sh"), "provision"],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    assert provisioned.returncode == 0, provisioned.stdout + provisioned.stderr
    assert (root / "current-version").read_text().strip() == VERSION


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX checkout reference coverage")
@pytest.mark.parametrize("operation", ["install", "stamp", "missing-reader"])
def test_posix_checkout_reader_reference_and_stamped_closure(
    tmp_path: Path, operation: str,
) -> None:
    assert BASH
    _, env = _fake_uv_environment(tmp_path)
    for variable in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "PIP_INDEX_URL", "VIRTUAL_ENV", "CONDA_PREFIX"):
        env.pop(variable, None)
    uv_config = tmp_path / "uv.toml"
    uv_config.write_text("", encoding="utf-8")
    env.update(UV_CONFIG_FILE=str(uv_config), PIP_CONFIG_FILE=os.devnull)
    checkout = tmp_path / "checkout"
    files = subprocess.check_output(
        ["git", "-C", str(PLUGIN.parents[1]), "ls-files", "-z", "--",
         "plugins/budget-guidance", "libs", "tools", ".github/plugin/marketplace.json"],
        timeout=30,
    )
    for raw in files.split(b"\0"):
        if not raw:
            continue
        relative = Path(os.fsdecode(raw))
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PLUGIN.parents[1] / relative, target)
    roster = checkout / ".github/plugin/marketplace.json"
    catalog = json.loads(roster.read_bytes())
    catalog["plugins"] = [entry for entry in catalog["plugins"] if entry["name"] == "budget-guidance"]
    roster.write_text(json.dumps(catalog), encoding="utf-8")
    payload = checkout / "plugins/budget-guidance"
    staged = tmp_path / "bootstrap-ready"
    shutil.copytree(payload, staged)
    previous = sys.path.copy()
    sys.path.insert(0, str(PLUGIN.parents[1] / "tools"))
    try:
        materializer = importlib.import_module("materialize_main")
    finally:
        sys.path[:] = previous
    for prepare in (
        materializer.materialize_uv_editable_ref_into,
        materializer.materialize_installer_engine_ref_into,
        materializer.materialize_payload_invocation_into,
    ):
        changes = prepare(
            source_consumer_dir=payload, dest_consumer_dir=staged, canonical_root=checkout,
        )
        assert not any(line.startswith("SKIP ") for line in changes), changes
    assert (staged / "scripts/pip-index-config").read_text().startswith("<!-- VENDOR_POINTER:")
    shutil.rmtree(payload)
    staged.rename(payload)
    if operation == "missing-reader":
        (payload / "scripts/pip-index-config").unlink()
    root = Path(env["HOME"]) / ".budget-guidance"
    result = subprocess.run(
        [BASH, str(payload / "scripts/install.sh"), "install" if operation == "missing-reader" else operation],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
    )
    if operation == "missing-reader":
        assert result.returncode != 0
        assert "index reader is not prepared" in result.stdout + result.stderr
        assert not (root / "current-version").exists()
        return
    assert result.returncode == 0, result.stdout + result.stderr
    if operation == "stamp":
        snapshot = Path((root / "payload-dir").read_text().strip())
        assert (snapshot / "scripts/pip-index-config").read_bytes() == (
            checkout / "libs/installer-engine/installer_pip_index.py"
        ).read_bytes()
        assert (snapshot / "scripts/installer_dependency_config.py").read_bytes() == (
            checkout / "libs/installer-engine/installer_dependency_config.py"
        ).read_bytes()
        shutil.rmtree(checkout)
        provisioned = subprocess.run(
            [BASH, str(snapshot / "scripts/install.sh"), "provision"],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120,
        )
        assert provisioned.returncode == 0, provisioned.stdout + provisioned.stderr
    assert (root / "current-version").read_text().strip() == VERSION


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX installer coverage")
@pytest.mark.parametrize("manifest_layout", ["canonical", "materialized"])
def test_posix_stamp_uses_owned_versioned_snapshot_without_marketplace_fallback(
    tmp_path: Path, manifest_layout: str,
) -> None:
    assert BASH
    _, env = _fake_uv_environment(tmp_path)
    install_dir = Path(env["HOME"]) / ".budget-guidance"
    source = tmp_path / "source" / "plugins" / "budget-guidance"
    _copy_payload(source)
    manifest = source / "pyproject.toml"
    local_ref = b'agent-procutil = { path = "libs/agent-procutil" }'
    canonical_ref = b'agent-procutil = { path = "../../libs/agent-procutil", editable = true }'
    original_manifest = manifest.read_bytes()
    assert local_ref in original_manifest
    if manifest_layout == "canonical":
        library = source / "libs" / "agent-procutil"
        shutil.copytree(library, source.parents[1] / "libs" / "agent-procutil")
        shutil.rmtree(library)
        original_manifest = original_manifest.replace(local_ref, canonical_ref)
        manifest.write_bytes(original_manifest)
        assert canonical_ref in original_manifest
    expected_manifest = original_manifest.replace(canonical_ref, local_ref)
    assert (expected_manifest != original_manifest) is (manifest_layout == "canonical")

    result = subprocess.run(
        [
            BASH,
            str(source / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(install_dir),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    snapshot = Path((install_dir / "payload-dir").read_text(encoding="utf-8").strip())
    assert snapshot == install_dir / "snapshots" / VERSION
    assert snapshot != source
    assert (snapshot / "scripts" / "install.sh").is_file()
    assert (snapshot / "libs" / "agent-procutil" / "pyproject.toml").is_file()
    assert (snapshot / "pyproject.toml").read_bytes() == expected_manifest
    assert (snapshot / ".payload-source").read_text(encoding="utf-8").strip() == str(
        source
    )

    wrapper = Path(env["HOME"]) / ".local" / "bin" / "budget-guidance"
    wrapper_source = wrapper.read_text(encoding="utf-8")
    assert "installed-plugins/*" not in wrapper_source
    (snapshot / "scripts" / "install.sh").unlink()
    unavailable = subprocess.run(
        [BASH, str(wrapper), "status"],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    assert unavailable.returncode == 1
    assert "runtime is not prepared" in unavailable.stdout


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX installer coverage")
def test_posix_self_stage_preserves_custom_root_and_completed_runtime_on_force(tmp_path: Path) -> None:
    assert BASH
    _, env = _fake_uv_environment(tmp_path)
    installed = (
        Path(env["HOME"])
        / ".copilot"
        / "installed-plugins"
        / "example"
        / "budget-guidance"
    )
    _copy_payload(installed)
    custom_root = tmp_path / "custom-runtime"
    venv_log = tmp_path / "uv-venv.log"
    env["TEST_UV_VENV_LOG"] = str(venv_log)

    stamp = subprocess.run(
        [
            BASH,
            str(installed / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(custom_root),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        check=False,
    )
    assert stamp.returncode == 0, stamp.stdout
    assert (custom_root / "payload-dir").is_file()
    default_root = Path(env["HOME"]) / ".budget-guidance"
    assert not (default_root / "payload-dir").exists()
    assert not (default_root / "current-version").exists()

    install = subprocess.run(
        [
            BASH,
            str(installed / "scripts" / "install.sh"),
            "install",
            "--install-dir",
            str(custom_root),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert install.returncode == 0, install.stdout
    initial_calls = venv_log.read_text(encoding="utf-8").splitlines()
    assert len(initial_calls) == 1
    assert all(str(custom_root) in call for call in initial_calls)
    marker = next((custom_root / "versions").glob("*/.install-complete.json"))
    before = marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns

    forced = subprocess.run(
        [
            BASH,
            str(installed / "scripts" / "install.sh"),
            "install",
            "--install-dir",
            str(custom_root),
            "--force",
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )
    assert forced.returncode == 0, forced.stdout
    forced_calls = venv_log.read_text(encoding="utf-8").splitlines()
    assert forced_calls == initial_calls
    assert all(str(custom_root) in call for call in forced_calls)
    assert (marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns) == before


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="portable pwsh installer coverage")
def test_powershell_installer_bootstraps_with_uv_and_no_ambient_python(tmp_path: Path) -> None:
    assert PWSH
    _, env = _fake_uv_environment(tmp_path)
    install_dir = Path(env["HOME"]) / ".budget-guidance"
    payload = tmp_path / "budget-guidance"
    _copy_payload(payload)

    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "install",
            "-InstallDir",
            str(install_dir),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout
    assert (install_dir / "current-version").is_file()


@pytest.mark.parametrize("kind", ["posix", "powershell"])
@pytest.mark.skipif(os.name == "nt", reason="Native Windows uses the failure/retry fixture")
def test_full_setup_isolates_inherited_uv_selection_hints(tmp_path: Path, kind: str) -> None:
    host = BASH if kind == "posix" else PWSH
    if not host:
        pytest.skip(f"{kind} shell is unavailable")
    _, env = _fake_uv_environment(tmp_path)
    _poison_uv_environment(env, tmp_path)
    env.pop("UV_WORKING_DIR")
    env.pop("UV_WORKING_DIRECTORY")
    payload = tmp_path / "budget-guidance"
    _copy_payload(payload)
    arguments = (
        [host, str(payload / "scripts/install.sh"), "install"] if kind == "posix" else
        [host, "-NoProfile", "-File", str(payload / "scripts/install.ps1"), "install"]
    )
    result = subprocess.run(
        arguments, env=env, cwd=tmp_path, capture_output=True, text=True,
        timeout=120, creationflags=NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (Path(env["HOME"]) / ".budget-guidance/current-version").is_file()


def _compatibility_powershell_wrapper() -> str:
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    start_marker = "$ps1Content = @'\n"
    end_marker = "\n'@\n    [System.IO.File]::WriteAllText($ps1Path"
    start = source.index(start_marker) + len(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


@pytest.mark.skipif(PWSH is None, reason="PowerShell host unavailable")
@pytest.mark.parametrize("published", ["user", "machine", "neither", "bootstrap-process"])
def test_command_path_publication_does_not_confuse_bootstrap_with_persistence(
    tmp_path: Path, published: str,
) -> None:
    assert PWSH
    source = (PLUGIN / "scripts/install.ps1").read_text(encoding="utf-8")
    function = source.split("function Publish-BudgetCommandPath {", 1)[1].split(
        "\nfunction Invoke-Stamp {", 1,
    )[0]
    local_bin = str(tmp_path / ".local/bin")
    bootstrap = ""
    if published == "bootstrap-process":
        uv = shutil.which("uv")
        assert uv, "The native bootstrap control requires uv"
        Path(local_bin).mkdir(parents=True)
        shutil.copy2(uv, Path(local_bin) / "uv.exe")
        engine = PLUGIN.parents[1] / "libs/installer-engine/installer-engine.ps1"
        bootstrap = (
            f". '{str(engine).replace(chr(39), chr(39) * 2)}'\n"
            "if (Get-Command uv -CommandType Application -ErrorAction SilentlyContinue) {\n"
            "    throw 'uv was unexpectedly available before bootstrap'\n}\n"
            f"$selected = Ensure-Uv -InstallRoot '{str(tmp_path).replace(chr(39), chr(39) * 2)}' "
            "-ToolDirectory '.local/bin' -AcquireIfMissing:$false\n"
            "if (-not $selected -or ($env:PATH -split ';')[0] -cne $LocalBin) {\n"
            "    throw 'Real uv bootstrap did not prepare temporary command discovery'\n}\n"
        )
    script = tmp_path / "publish-path.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$LocalBin = '{local_bin.replace(chr(39), chr(39) * 2)}'\n"
        "$store = @{ User='user-before;user-after'; Machine='machine-before;machine-after' }\n"
        f"$published = '{published}'\n"
        "if ($published -eq 'user') { $store.User += ';' + $LocalBin }\n"
        "if ($published -eq 'machine') { $store.Machine += ';' + $LocalBin }\n"
        "$machineBefore = $store.Machine\n"
        "$env:PATH = 'process-before;process-after'\n"
        + bootstrap +
        "$writes = [Collections.Generic.List[string]]::new()\n"
        "function Get-CopilotPersistentEnvironmentVariable {\n"
        "    param($Name,$Target) $store[$Target]\n}\n"
        "function Set-CopilotPersistentEnvironmentVariable { param($Name,$Value,$Target)\n"
        "    $store[$Target]=$Value; $writes.Add($Target)\n}\n"
        "function Write-Ok { param($Message) }\n"
        "function Publish-BudgetCommandPath {" + function +
        "\nPublish-BudgetCommandPath\nPublish-BudgetCommandPath\n"
        "if ($store.Machine -cne $machineBefore) { throw 'Machine PATH changed' }\n"
        "@{ user=$store.User; process=$env:PATH; writes=@($writes.ToArray()) } | "
        "ConvertTo-Json -Compress\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)], capture_output=True, text=True,
        timeout=20, creationflags=NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads(result.stdout)
    expected_user = "user-before;user-after"
    if published == "user":
        expected_user += ";" + local_bin
    elif published != "machine":
        expected_user = local_bin + ";" + expected_user
    assert output["user"] == expected_user
    assert output["writes"] == ([] if published in ("user", "machine") else ["User"])
    assert output["process"] == local_bin + ";process-before;process-after"


@pytest.mark.skipif(PWSH is None, reason="PowerShell host unavailable")
def test_closed_wheel_uv_off_path_refuses_before_construction(tmp_path: Path) -> None:
    assert PWSH
    uv = shutil.which("uv")
    assert uv, "The closed-wheel command-prerequisite control requires uv"
    payload = tmp_path / "budget-guidance"
    _copy_payload(payload)
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".budget-guidance"
    root.mkdir()
    selected = root / "current-version"
    selected.write_text("retained-generation")
    configuration = root / "config.yaml"
    configuration.write_text("retained: true\n")
    tools = tmp_path / "command-tools"
    tools.mkdir()
    if os.name != "nt":
        mkdir = shutil.which("mkdir")
        assert mkdir
        shutil.copy2(mkdir, tools / "mkdir")
    off_path = tmp_path / "construction-tools"
    off_path.mkdir()
    wheel_uv = off_path / Path(uv).name
    shutil.copy2(uv, wheel_uv)
    profile = tmp_path / "profile.json"
    profile.write_text("{}")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(payload / "scripts/install.ps1"),
         "-Action", "update", "-InstallDir", str(root), "-WheelProfile", str(profile),
         "-WheelDirectory", str(tmp_path), "-WheelPython", sys.executable,
         "-WheelUv", str(wheel_uv)],
        cwd=home, capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home),
             "PATH": str(tools), "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
             "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never"},
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Closed-wheel command publication requires prepared uv" in result.stdout + result.stderr
    assert selected.read_text() == "retained-generation"
    assert configuration.read_text() == "retained: true\n"
    assert not (root / "versions").exists()
    assert not (root / ".install.lock").exists()
    assert not (home / ".local/bin").exists()


def test_windows_compatibility_wrapper_does_not_provision_on_launch() -> None:
    wrapper = _compatibility_powershell_wrapper()
    assert "runtime is not prepared" in wrapper
    assert " provision" not in wrapper
    assert "installed-plugins" not in wrapper


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="portable pwsh concurrency coverage")
def test_windows_compatibility_wrapper_concurrent_launch_does_not_prepare(tmp_path: Path) -> None:
    assert PWSH
    home = tmp_path / "home"
    root = home / ".budget-guidance"
    snapshot = root / "snapshots" / "test"
    (root / "bin").mkdir(parents=True)
    (snapshot / "scripts").mkdir(parents=True)
    (root / "payload-dir").write_text(str(snapshot), encoding="utf-8")

    marker = root / "runtime-ready"
    count = root / "provision-count"
    runtime = root / "runtime"
    runtime.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    runtime.chmod(0o755)
    (root / "bin" / "resolve-runtime.ps1").write_text(
        "\n".join(
            [
                "$AgentRtPy = $null",
                f"if (Test-Path -LiteralPath '{marker}') {{",
                f"    $AgentRtPy = '{runtime}'",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    (snapshot / "scripts" / "install.ps1").write_text(
        "\n".join(
            [
                "param([string]$Action)",
                "Start-Sleep -Milliseconds 500",
                f"Add-Content -LiteralPath '{count}' -Value provision",
                f"New-Item -ItemType File -Path '{marker}' -Force | Out-Null",
            ]
        ),
        encoding="utf-8",
    )
    wrapper = tmp_path / "budget-guidance.ps1"
    wrapper.write_text(_compatibility_powershell_wrapper(), encoding="utf-8")
    env = {
        **os.environ,
        "USERPROFILE": str(home),
        "HOME": str(home),
    }

    processes = [
        subprocess.Popen(
            [PWSH, "-NoProfile", "-File", str(wrapper), "status"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for _ in range(2)
    ]
    outputs = [process.communicate(timeout=30) for process in processes]

    assert [process.returncode for process in processes] == [1, 1], outputs
    assert not count.exists()
    assert not marker.exists()
