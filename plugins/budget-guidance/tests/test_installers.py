from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
PWSH = shutil.which("pwsh")
VERSION = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))[
    "project"
]["version"]

pytestmark = pytest.mark.guard


def _copy_payload(target: Path) -> None:
    def ignore(directory, names):
        omitted = {"__pycache__", ".pytest_cache"}
        if Path(directory) == PLUGIN:
            omitted.add("tests")
        return omitted.intersection(names)

    pending = []
    with ThreadPoolExecutor(max_workers=16) as copies:
        def queue_copy(source, destination):
            pending.append(copies.submit(shutil.copy2, source, destination))
            return destination

        shutil.copytree(PLUGIN, target, ignore=ignore, copy_function=queue_copy)
        for future in pending:
            future.result()
    previous = sys.path.copy()
    sys.path.insert(0, str(PLUGIN.parents[1] / "tools"))
    try:
        import materialize_main
    finally:
        sys.path[:] = previous
    changes = materialize_main.materialize_uv_editable_ref_into(
        source_consumer_dir=PLUGIN, dest_consumer_dir=target,
        canonical_root=PLUGIN.parents[1],
    )
    assert not any(change.startswith("SKIP ") for change in changes), changes
    procutil = target / "libs" / "agent-procutil"
    assert (procutil / "pyproject.toml").is_file(), "Missing agent-procutil fixture manifest"
    assert (procutil / "src" / "agent_procutil" / "__init__.py").is_file(), (
        "Missing agent-procutil fixture package"
    )


def test_installers_recommend_supported_version_flag():
    posix = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "Try: budget-guidance --version" in posix
    assert "Try: budget-guidance --version" in powershell
    assert "Try: budget-guidance version" not in posix
    assert "Try: budget-guidance version" not in powershell


def test_package_installers_retain_quiet_native_output_with_explicit_failure():
    posix = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    assert '"${dependencies[@]}" "$PLUGIN_DIR" --quiet' in posix
    assert '@pipSources --quiet 2>&1' in powershell
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
if args[:2] == ["python", "find"] or (args and args[0] == "run"):
    print(base)
    raise SystemExit(0)
if "--show-settings" in args:
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
        f"#!/bin/sh\nexec '{sys._base_executable}' '{driver}' \"$@\"\n",
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
        "TEST_BASE_PYTHON": sys._base_executable,
        "TEST_UV_DRIVER": str(driver),
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
        "POWERSHELL_TELEMETRY_OPTOUT": "1",
        "COMPUTERNAME": "TEST-HOST",
    }
    env.pop("COPILOT_PLUGIN_INSTALL_STAGED", None)
    env.pop("COPILOT_PLUGIN_STAGED_FROM", None)
    Path(env["HOME"]).mkdir()
    return fake_bin, env


@pytest.fixture(scope="module", params=[False, True], ids=["fresh", "retained"])
def native_failed_install_state(tmp_path_factory, request):
    if os.name != "nt" or PWSH is None:
        pytest.skip("native Windows installer")
    tmp_path = tmp_path_factory.mktemp("budget-failure")
    previous = request.param
    fake_bin, env = _fake_uv_environment(tmp_path)
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
            f'  "{sys._base_executable}" %2 %3 %4 %5 %6 %7 %8 %9\n'
            '  exit /b !errorlevel!\n)\n'
        ) if name == "py" else "@echo off\n"
        (fake_bin / f"{name}.cmd").write_text(
            prefix + f'"{sys._base_executable}" %*\nexit /b %errorlevel%\n',
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
        f'@echo off\n"{sys._base_executable}" -I -S "{driver}" %*\nexit /b %errorlevel%\n',
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
        creationflags=subprocess.CREATE_NO_WINDOW,
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


def _assert_failed_install(state):
    assert state.failed.returncode != 0, state.failed.stdout + state.failed.stderr
    assert "Canonical dependency installation exited 29" in state.failed.stdout, (
        state.failed.stdout + state.failed.stderr
    )
    assert not state.failed_slot_exists and not state.failed_manifest_exists
    assert state.selected_after_failure == state.before
    if state.previous:
        assert state.retained_after_failure == "untouched"


def test_native_failed_install_preserves_selection(native_failed_install_state):
    _assert_failed_install(native_failed_install_state)


def test_native_retry_after_failure_publishes_clean_slot(native_failed_install_state):
    state = native_failed_install_state
    _assert_failed_install(state)
    retried = subprocess.run(
        state.command, env=state.env, cwd=state.tmp_path, capture_output=True, text=True,
        timeout=45, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert retried.returncode == 0, retried.stdout + retried.stderr
    assert not (state.root / "versions" / VERSION / "partial-install").exists()
    assert (state.root / "current-version").read_text().strip() == VERSION
    if state.previous:
        assert (state.old / "live").read_text() == "untouched"


def test_posix_installer_is_uv_first_when_ambient_python_is_absent():
    source = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    provisioning = source[source.index("if _ensure_uv; then HAVE_UV=1; fi") :]

    assert provisioning.index("if _ensure_uv; then HAVE_UV=1; fi") < provisioning.index(
        'PYTHON_CMD=""'
    )
    assert 'uv venv "$VENV_DIR" --python 3.10 --allow-existing' in provisioning
    assert '[[ "$HAVE_UV" -eq 0 && -z "$PYTHON_CMD" ]]' in provisioning


def test_powershell_installer_is_uv_first_when_ambient_python_is_absent():
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    provisioning = source[source.index("$uvCommand = Get-Command uv -ErrorAction SilentlyContinue") :]

    assert provisioning.index("$uvCommand = Get-Command uv") < provisioning.index(
        "$pythonCmd = $null"
    )
    assert "venv $VenvDir --python 3.10 --allow-existing" in provisioning
    assert "if (-not $uvCommand -and -not $pythonCmd)" in provisioning


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="portable pwsh self-stage coverage")
def test_powershell_self_stage_preserves_spaced_paths_and_force(tmp_path: Path):
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
def test_posix_installer_bootstraps_with_uv_and_no_ambient_python(tmp_path: Path):
    _, env = _fake_uv_environment(tmp_path)
    install_dir = Path(env["HOME"]) / ".budget-guidance"

    result = subprocess.run(
        [BASH, str(PLUGIN / "scripts" / "install.sh"), "install", "--install-dir", str(install_dir)],
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


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX installer coverage")
@pytest.mark.parametrize("manifest_layout", ["canonical", "materialized"])
def test_posix_stamp_uses_owned_versioned_snapshot_without_marketplace_fallback(
    tmp_path: Path, manifest_layout: str,
):
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
    assert unavailable.returncode == 127
    assert "owning snapshot installer unavailable" in unavailable.stdout


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="POSIX installer coverage")
def test_posix_self_stage_preserves_custom_root_and_completed_runtime_on_force(tmp_path: Path):
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
def test_powershell_installer_bootstraps_with_uv_and_no_ambient_python(tmp_path: Path):
    _, env = _fake_uv_environment(tmp_path)
    install_dir = Path(env["HOME"]) / ".budget-guidance"

    result = subprocess.run(
        [
            PWSH,
            "-NoProfile",
            "-File",
            str(PLUGIN / "scripts" / "install.ps1"),
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


def _compatibility_powershell_wrapper() -> str:
    source = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    start_marker = "$ps1Content = @'\n"
    end_marker = "\n'@\n    [System.IO.File]::WriteAllText($ps1Path"
    start = source.index(start_marker) + len(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_windows_compatibility_wrapper_uses_runtime_lock_and_rechecks():
    wrapper = _compatibility_powershell_wrapper()

    lock = wrapper.index("$_lockPath = Join-Path $_root '.provision.lock'")
    acquired = wrapper.index("[IO.File]::Open(", lock)
    recheck = wrapper.index("$_py = _Resolve-Py", acquired)
    provision = wrapper.index("-File $_inst provision", recheck)

    assert lock < acquired < recheck < provision
    assert "[IO.FileShare]::None" in wrapper
    assert "installed-plugins" not in wrapper


@pytest.mark.skipif(PWSH is None or os.name == "nt", reason="portable pwsh concurrency coverage")
def test_windows_compatibility_wrapper_serializes_first_use(tmp_path: Path):
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

    assert [process.returncode for process in processes] == [0, 0], outputs
    assert count.read_text(encoding="utf-8").splitlines() == ["provision"]
