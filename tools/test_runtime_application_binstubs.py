"""Real uv front-door controls; no production application or service is installed."""
from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import venv
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
from runtime_application_binstubs import global_binstubs, write_slot_binstubs
from runtime_application_project import emit
import runtime_application_install
from local_install_profile import source_inputs as install_source
from installer_wheel_snapshot import retain_wheel_inputs

engine = install_source.engine


@pytest.mark.parametrize("stage,failure", [(True, False), (False, False), (False, True)])
def test_manager_regular_install_routes_to_its_owner_before_generic_publication(
    tmp_path, monkeypatch, stage, failure,
):
    slot = tmp_path / "root" / "versions" / "1.2.3"
    (slot / ".venv").mkdir(parents=True)
    (slot / ".venv" / "pyvenv.cfg").write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(runtime_application_install.sys, "prefix", str(slot / ".venv"))
    monkeypatch.setattr(
        runtime_application_install, "_application", lambda _slot: {"name": "worktree-manager"},
    )
    calls = []

    def owner(record, command, root, selected):
        calls.append((command, root, selected))
        if failure:
            raise RuntimeError("finite installation owner refused")

    monkeypatch.setattr(runtime_application_install, "_run", owner)
    if failure:
        with pytest.raises(RuntimeError, match="owner refused"):
            runtime_application_install.install(slot, stage=stage)
    else:
        result = runtime_application_install.install(slot, stage=stage)
        assert result["state"] == ("staged" if stage else "installed")
    assert calls == ([] if stage else [(["self-install", "--apply"], slot.parent.parent, slot)])
    assert not (slot.parent.parent / "current-version").exists()


@pytest.mark.parametrize("wheel_present", [False, True])
def test_application_source_fallback_only_on_real_wheel_absence(tmp_path, wheel_present):
    source = tmp_path / "source"
    package = source / "src" / "fallback_app"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        '[project]\nname="fallback-app"\nversion="1.0.0"\n'
        '[project.scripts]\nfallback-app="fallback_app:main"\n',
        encoding="utf-8",
    )
    wheel = tmp_path / "fallback_app-1.0.0-py3-none-any.whl"
    if wheel_present:
        wheel.write_bytes(b"not a wheel")
    destination = emit(
        tmp_path / "application", distribution="fallback-app", version="1.0.0",
        requires_python=">=3.10", commands={"fallback-app": "fallback_app:main"},
        wheels={"fallback-app": wheel}, source_fallbacks={"fallback-app": source},
    )
    manifest = (destination / "pyproject.toml").read_text()
    if wheel_present:
        assert (destination / "wheels" / wheel.name).read_bytes() == b"not a wheel"
        assert not (destination / "source").exists()
    else:
        assert 'path = "source/fallback-app", editable = false' in manifest
        assert (destination / "source" / "fallback-app" / "src" / "fallback_app" /
                "__init__.py").read_text() == "VALUE = 1\n"
        assert not (destination / "wheels" / wheel.name).exists()


@pytest.fixture
def application(tmp_path, monkeypatch, request):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("a real uv executable is required")
    root = tmp_path / "runtime root"
    artifact = tmp_path / "artifacts"
    artifact.mkdir()
    wheel = artifact / "fixture_app-1.0.0-py3-none-any.whl"
    files = {
        "fixture_app.py": (
            "import json, os, sys\nfrom pathlib import Path\n"
            "def cli():\n"
            "    if '--setup' in sys.argv:\n"
            "        Path(os.environ['FIXTURE_APP_HOME'], 'setup.json').write_text('setup')\n"
            "    if '--start' in sys.argv:\n"
            "        root = Path(os.environ['FIXTURE_APP_HOME'])\n"
            "        assert (root / 'current-version').is_file()\n"
            "        (root / 'started.json').write_text('started')\n"
            "    if '--unicode-output' in sys.argv:\n"
            "        print('\\u2192\\u03b1')\n"
            "    print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(), 'prefix': sys.prefix}))\n"
            "    return 17 if '--fail' in sys.argv else 0\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(cli())\n"
        ).encode(),
        "fixture_app-1.0.0.dist-info/METADATA": (
            "Metadata-Version: 2.1\nName: fixture-app\nVersion: 1.0.0\n"
            "Requires-Python: >=3.10\n"
        ).encode(),
        "fixture_app-1.0.0.dist-info/WHEEL": (
            "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ).encode(),
    }
    if getattr(request, "param", None) == "long-asset":
        member = "/".join(["fixture_assets", *["nested-resources" * 2] * 5, "operational.txt"])
        files[member] = b"retained operational asset\n"
    rows = [
        (name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode(),
         len(raw))
        for name, raw in files.items()
    ]
    record = "fixture_app-1.0.0.dist-info/RECORD"
    output = io.StringIO()
    csv.writer(output, lineterminator="\n").writerows([*rows, (record, "", "")])
    files[record] = output.getvalue().encode()
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    source = tmp_path / "actual source"
    source.mkdir()
    (source / "fixture_app.py").write_bytes(files["fixture_app.py"])
    (source / "pyproject.toml").write_text(
        '[project]\nname="fixture-app"\nversion="1.0.0"\n'
        '[project.scripts]\nfixture-app="fixture_app:cli"\n', encoding="utf-8",
    )
    source_hash = engine.runtime_source.assembly_hash(source)
    identity = engine.runtime_source.SourceIdentity(
        "fixture-app", "1.0.0", "a" * 40, "b" * 40, source_hash,
        engine.runtime_source.runtime_version("1.0.0", "a" * 40, source_hash),
    )
    (source / "runtime-source.json").write_text(json.dumps(identity.document()), encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest()
    profile = {
        "component": "fixture-app",
        "release": {"identity": "source:" + identity.source_commit,
                    "sha256": "sha256:" + identity.assembly_hash},
        "wheels": [{
            "name": "fixture-app", "version": "1.0.0", "filename": wheel.name,
            "sha256": digest, "requires_python": ">=3.10", "requires_dist": [],
            "provides_extras": [], "selected_extras": [],
        }],
    }
    selected = install_source.ProjectSource(source, identity, {
        "projects": {"fixture-app": {"artifact": {"wheels": [
            {"name": "fixture-app", "filename": wheel.name, "sha256": digest},
        ]}}},
    }, "fixture-app")
    with retain_wheel_inputs(
        {wheel.name: wheel}, {wheel.name: digest}, max_wheels=1,
        max_archive_bytes=1 << 20, max_total_bytes=1 << 20, max_snapshot_bytes=1 << 20,
        deadline=time.monotonic() + 60,
    ) as retained:
        inputs = engine.WheelInstallInputs(
            retained, json.dumps(profile, sort_keys=True, separators=(",", ":")),
            (("fixture-app", "1.0.0", wheel.name, digest),),
        )
        construction = install_source.application_inputs(selected, inputs)
    slot = root / "versions" / construction["runtime_version"]
    emit(
        slot, distribution="fixture-app", version="1.0.0",
        requires_python=f">={sys.version_info.major}.{sys.version_info.minor}",
        commands={"fixture-app": "fixture_app:cli", "fixture-alias": "fixture_app:cli"},
        wheels={"fixture-app": wheel},
        setup=["--setup"], start=["--start"],
        construction=construction, runtime_source=identity.document(),
    )
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("UV_") and key not in {"VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"}
    }
    environment["UV_CACHE_DIR"] = str(root / "cache" / "uv")
    home = tmp_path / "home"
    home.mkdir()
    environment.update(HOME=str(home), USERPROFILE=str(home))
    if os.name == "nt":
        environment.update(HOMEDRIVE=home.drive, HOMEPATH=str(home)[len(home.drive):])
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    result = subprocess.run(
        [uv, "lock", "--offline", "--python", sys.executable],
        cwd=slot, env=environment, capture_output=True, text=True, timeout=30, creationflags=flags,
    )
    assert result.returncode == 0, result.stderr
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "fixture_app.py").write_text("raise RuntimeError('caller hijack')", encoding="utf-8")
    monkeypatch.setenv("UV_PYTHON", sys.executable)
    return root, slot, caller, environment


def invoke(path, arguments, caller, environment):
    if path.suffix == ".ps1":
        host = shutil.which("powershell.exe") or shutil.which("pwsh")
        if host is None:
            pytest.skip("PowerShell is unavailable")
        command = [host, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(path), *arguments]
    elif path.suffix == ".cmd":
        if os.name != "nt":
            pytest.skip("native CMD is unavailable")
        invocation = subprocess.list2cmdline([str(path), *arguments])
        command = f'"{os.environ["COMSPEC"]}" /d /s /c "{invocation}"'
    else:
        if os.name == "nt":
            pytest.skip("native POSIX execution is unavailable")
        command = [str(path), *arguments]
    return subprocess.run(
        command, cwd=caller, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_isolated_frontdoor_prints_unicode_without_environment_encoding(application, extension):
    _root, slot, caller, environment = application
    environment.update(PYTHONUTF8="0", PYTHONIOENCODING="ascii", UV_PYTHON=sys.executable)
    result = invoke(slot / ("fixture-app" + extension), ["--unicode-output"], caller, environment)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == "\u2192\u03b1"


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_install_stage_uses_uv_without_touching_root_selection(application, extension):
    root, slot, caller, environment = application
    environment.update(UV_PYTHON=sys.executable, UV_PROJECT_ENVIRONMENT=str(caller / "wrong-env"),
                       UV_NO_SYNC="1", PYTHONPATH=str(caller), VIRTUAL_ENV=str(caller / "wrong-env"))
    result = invoke(slot / ("install" + extension), ["--stage"],
                    caller, environment)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record == {"state": "staged", "slot": str(slot.resolve())}
    assert (slot / ".venv" / "pyvenv.cfg").is_file()
    assert not (root / "current-version").exists()
    assert not (root / ".venv").exists()
    assert not (caller / "wrong-env").exists()
    assert not (root / "setup.json").exists()
    assert not (root / "started.json").exists()
    assert not (slot / engine.runtime.COMPLETE_MARKER).exists()
    assert not (slot / ".runtime-input-origins.json").exists()
    assert not (slot / ".runtime-bootstrap").exists()
    assert not (Path(environment["HOME"]) / ".local" / "bin").exists()


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_regular_install_runs_setup_publishes_then_starts(application, extension):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    result = invoke(slot / ("install" + extension), [], caller, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["state"] == "installed"
    assert (root / "setup.json").read_text() == "setup"
    assert (root / "started.json").read_text() == "started"
    assert (root / "current-version").read_text().strip() == slot.name
    assert (slot / "install-success.json").is_file()
    completion = json.loads((slot / engine.runtime.COMPLETE_MARKER).read_text())
    declaration = json.loads((slot / "application.json").read_text())
    assert completion["payload_hash"] == declaration["construction"]["payload_hash"]
    assert not (root / ".venv").exists()
    globals_root = Path(environment["HOME"]) / ".local" / "bin"
    followup = invoke(globals_root / ("fixture-app" + extension), ["--fail", "", "\u03b1", "100% ready!"],
                      caller, environment)
    assert followup.returncode == 17, followup.stderr
    assert json.loads(followup.stdout)["args"] == ["--fail", "", "\u03b1", "100% ready!"]
    alias = invoke(globals_root / ("fixture-alias" + extension), ["--fail"], caller, environment)
    assert alias.returncode == 17, alias.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows legacy path-length behavior")
@pytest.mark.parametrize("application", ["long-asset"], indirect=True)
def test_installed_wheel_health_keeps_long_path_content_checks(application):
    _root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    result = invoke(slot / "install.cmd", [], caller, environment)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((slot / ".runtime-input-origins.json").read_text())
    wheels = engine.WheelReuseInputs(
        slot.name, json.dumps(record["wheel_inputs"], sort_keys=True, separators=(",", ":")),
        time.monotonic() + 60,
    )
    legacy_calls = """
import pathlib
def legacy(operation):
    def call(path, *args, **kwargs):
        if 'fixture_assets' in path.parts and len(str(path)) >= 260 and not str(path).startswith(chr(92) * 2 + '?' + chr(92)):
            raise FileNotFoundError('legacy Windows path exceeds MAX_PATH')
        return operation(path, *args, **kwargs)
    return call
pathlib.Path.stat = legacy(pathlib.Path.stat)
pathlib.Path.open = legacy(pathlib.Path.open)
"""

    def run(arguments):
        arguments = list(arguments)
        index = arguments.index("-c") + 1
        arguments[index] = legacy_calls + arguments[index]
        result = subprocess.run(
            arguments, executable=arguments[0], cwd=caller, env=environment,
            capture_output=True, text=True,
            timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        result.check_returncode()

    def verify():
        engine.verify_installed_inputs(
            slot, slot / ".venv" / "Scripts" / "python.exe", "fixture_app",
            origins=record["packages"], extras=tuple(record.get("extras", [])),
            local_packages=tuple(record["local_packages"]), excluded_packages=[],
            wheel_inputs=wheels, run=run,
        )

    verify()
    asset = next(Path(engine._native_retained_path(slot / ".venv")).rglob("operational.txt"))
    assert len(str(asset)) >= 260
    contents = asset.read_bytes()
    asset.write_bytes(b"X" + contents[1:])
    with pytest.raises(subprocess.CalledProcessError, match="non-zero exit status") as failure:
        verify()
    assert "installed content differs from its wheel" in failure.value.stderr


@pytest.mark.parametrize("foreign_interpreter", [False, True])
def test_retention_bootstrap_uses_the_completed_application_environment(
    application, foreign_interpreter,
):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    extension = ".cmd" if os.name == "nt" else ".sh"
    installed = invoke(slot / ("install" + extension), [], caller, environment)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    completion = (slot / engine.runtime.COMPLETE_MARKER).read_bytes()
    python = sys.executable if foreign_interpreter else str(engine.runtime.slot_python(root, slot.name))
    result = subprocess.run(
        [python, "-I", "-S", "-B", "-X", "utf8",
         str(slot / ".runtime-bootstrap" / "runtime_launcher.py"), "-m", "fixture_app", "--fail"],
        executable=python, cwd=caller, env=environment,
        capture_output=True, text=True, encoding="utf-8", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if foreign_interpreter:
        assert result.returncode != 0
        assert "interpreter resolved outside its slot" in result.stderr
        assert not result.stdout.strip()
    else:
        assert result.returncode == 17, result.stdout + result.stderr
        assert Path(json.loads(result.stdout)["prefix"]) == slot / ".venv"
    assert (slot / engine.runtime.COMPLETE_MARKER).read_bytes() == completion


def test_retention_bootstrap_preserves_the_historical_direct_slot(tmp_path):
    root = tmp_path / "runtime"
    slot = root / "versions" / "1.2.3"
    venv.EnvBuilder(with_pip=False).create(slot)
    python = engine.runtime.slot_python(root, slot.name)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    locations = subprocess.run(
        [str(python), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
        executable=str(python), capture_output=True, text=True, timeout=30, creationflags=flags,
    )
    assert locations.returncode == 0, locations.stderr
    (Path(locations.stdout.strip()) / "historical_fixture.py").write_text(
        "import sys\nprint(sys.prefix)\nraise SystemExit(7)\n", encoding="utf-8",
    )
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for path in Path(engine.runtime.__file__).parent.glob("versioned_runtime*.py"):
        shutil.copyfile(path, bootstrap / path.name)
    shutil.copyfile(
        Path(runtime_application_install.__file__).with_name("runtime_launcher.py"),
        bootstrap / "runtime_launcher.py",
    )
    engine.runtime.mark_complete(root, slot.name, payload_hash="historical-fixture")
    result = subprocess.run(
        [str(python), "-I", "-S", "-B", "-X", "utf8",
         str(bootstrap / "runtime_launcher.py"), "-m", "historical_fixture"],
        executable=str(python), cwd=tmp_path, capture_output=True,
        text=True, timeout=30, creationflags=flags,
    )
    assert result.returncode == 7, result.stdout + result.stderr
    assert Path(result.stdout.strip()) == slot


@pytest.mark.parametrize("stage", [False, True])
def test_install_forwards_explicit_application_arguments_only_when_regular(application, stage):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    forwarded = ["--config", str(caller / "config with spaces.json"), "--role", "host"]
    extension = ".cmd" if os.name == "nt" else ".sh"
    result = invoke(
        slot / ("install" + extension),
        [*(["--stage"] if stage else []), "--", *forwarded], caller, environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(line) for line in result.stdout.splitlines()]
    if stage:
        assert records == [{"state": "staged", "slot": str(slot)}]
        assert not (root / "setup.json").exists()
        assert not (root / "started.json").exists()
        assert not (root / "current-version").exists()
    else:
        assert records[0]["args"] == ["--setup", *forwarded]
        assert records[1]["args"] == ["--start", *forwarded]
        assert records[2]["state"] == "installed"


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_failed_start_does_not_leave_new_globals_or_selector(application, extension):
    root, slot, caller, environment = application
    record = json.loads((slot / "application.json").read_text())
    record["start"] = ["--fail"]
    (slot / "application.json").write_text(json.dumps(record), encoding="utf-8")
    environment["UV_PYTHON"] = sys.executable
    result = invoke(slot / ("install" + extension), [], caller, environment)
    assert result.returncode != 0
    assert not (root / "current-version").exists()
    assert not (slot / "install-success.json").exists()
    assert not (Path(environment["HOME"]) / ".local" / "bin" / "fixture-app.cmd").exists()
    assert not (Path(environment["HOME"]) / ".local" / "bin" / "fixture-alias.cmd").exists()


def test_partial_selector_failure_restores_previous_publication(application, monkeypatch):
    root, slot, caller, environment = application
    environment["UV_PYTHON"] = sys.executable
    extension = ".cmd" if os.name == "nt" else ".sh"
    staged = invoke(slot / ("install" + extension), ["--stage"], caller, environment)
    assert staged.returncode == 0, staged.stderr
    record = json.loads((slot / "application.json").read_text())
    record.update(setup=[], start=[])
    (slot / "application.json").write_text(json.dumps(record), encoding="utf-8")
    original = b"0.9.0.source.original\n"
    (root / "current-version").write_bytes(original)
    (root / "last-known-good").write_bytes(original)
    spec = importlib.util.spec_from_file_location("_fixture_install", slot / "install.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    replace = installer.os.replace

    def fail_last_good(source, destination):
        if Path(destination) == root / "last-known-good":
            raise OSError("injected publication failure")
        return replace(source, destination)

    monkeypatch.setattr(sys, "prefix", str(slot / ".venv"))
    monkeypatch.setattr(sys, "executable", str(
        slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
    ))
    monkeypatch.setattr(installer.os, "replace", fail_last_good)
    global_bin = root / "global"
    with pytest.raises(OSError, match="injected publication failure"):
        installer.install(slot, stage=False, global_bin=global_bin)
    assert (root / "current-version").read_bytes() == original
    assert (root / "last-known-good").read_bytes() == original
    assert not tuple(global_bin.iterdir())
    assert not (slot / "install-success.json").exists()


@pytest.mark.parametrize("failure", ["missing-inputs", "source-mismatch", "installed-content"])
def test_unqualified_application_does_not_publish_or_run_setup(application, failure):
    root, slot, caller, environment = application
    extension = ".cmd" if os.name == "nt" else ".sh"
    environment["UV_PYTHON"] = sys.executable
    staged = invoke(slot / ("install" + extension), ["--stage"], caller, environment)
    assert staged.returncode == 0, staged.stderr
    if failure == "installed-content":
        site = slot / ".venv" / (
            "Lib/site-packages" if os.name == "nt"
            else f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        (site / "fixture_app.py").write_text("raise RuntimeError('damaged installed content')\n")
    else:
        record = json.loads((slot / "application.json").read_text())
        if failure == "missing-inputs":
            record["construction"] = None
        else:
            record["construction"]["source_hash"] = "0" * 64
        (slot / "application.json").write_text(json.dumps(record), encoding="utf-8")
    result = invoke(slot / ("install" + extension), [], caller, environment)
    assert result.returncode != 0, result.stdout + result.stderr
    for path in (
        root / "current-version", root / "last-known-good", root / "setup.json",
        root / "started.json", slot / engine.runtime.COMPLETE_MARKER, slot / "install-success.json",
        Path(environment["HOME"]) / ".local" / "bin",
    ):
        assert not path.exists(), path


def test_regular_install_reuses_canonical_completion_without_restamping(application):
    root, slot, caller, environment = application
    extension = ".cmd" if os.name == "nt" else ".sh"
    environment["UV_PYTHON"] = sys.executable
    first = invoke(slot / ("install" + extension), [], caller, environment)
    assert first.returncode == 0, first.stdout + first.stderr
    marker = slot / engine.runtime.COMPLETE_MARKER
    before = marker.read_bytes(), marker.stat().st_mtime_ns
    second = invoke(slot / ("install" + extension), [], caller, environment)
    assert second.returncode == 0, second.stdout + second.stderr
    assert (marker.read_bytes(), marker.stat().st_mtime_ns) == before
    assert (root / "current-version").read_text().strip() == slot.name


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_global_uses_nested_home_relative_root(application, extension):
    root, slot, caller, environment = application
    nested = Path(environment["HOME"]) / ".fixture-app" / "engine"
    copied = nested / "versions" / slot.name
    shutil.copytree(slot, copied)
    (nested / "current-version").write_text(slot.name + "\n", encoding="utf-8")
    globals_root = root / "nested-global"
    globals_root.mkdir()
    for name, text in global_binstubs(
        "fixture-app", "FIXTURE_APP_HOME", ".fixture-app/engine",
    ).items():
        path = globals_root / name
        path.write_text(text, encoding="utf-8", newline="")
        if path.suffix == ".sh" or not path.suffix:
            path.chmod(0o700)
    environment.pop("FIXTURE_APP_HOME", None)
    environment["UV_PYTHON"] = sys.executable
    result = invoke(globals_root / ("fixture-app" + extension), ["--fail"], caller, environment)
    assert result.returncode == 17, result.stderr
    assert Path(json.loads(result.stdout)["prefix"]).samefile(copied / ".venv")


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
def test_global_forwards_to_slot_uv_and_preserves_failure(application, extension):
    root, slot, caller, environment = application
    binaries = root / "global"
    binaries.mkdir()
    for name, text in global_binstubs("fixture-app", "FIXTURE_APP_HOME", ".fixture-app").items():
        path = binaries / name
        path.write_text(text, encoding="utf-8", newline="")
        if path.suffix == ".sh" or not path.suffix:
            path.chmod(0o700)
    (root / "current-version").write_text(slot.name + "\n", encoding="utf-8")
    environment.update(FIXTURE_APP_HOME=str(root), UV_PYTHON=sys.executable)
    result = invoke(binaries / ("fixture-app" + extension), ["--fail", "space value", "100% ready!"],
                    caller, environment)
    assert result.returncode == 17, result.stderr
    record = json.loads(result.stdout)
    assert record["args"] == ["--fail", "space value", "100% ready!"]
    assert Path(record["cwd"]) == caller
    assert Path(record["prefix"]).samefile(slot / ".venv")


@pytest.mark.parametrize("extension", [".cmd", ".ps1", ".sh"])
@pytest.mark.parametrize("version", ["..", "1/../../outside", "1 & echo unsafe"])
def test_global_refuses_invalid_marker_without_invocation(application, extension, version):
    root, _slot, caller, environment = application
    binary = root / ("fixture-app" + extension)
    binary.write_text(
        global_binstubs("fixture-app", "FIXTURE_APP_HOME", ".fixture-app")[binary.name],
        encoding="utf-8", newline="",
    )
    if extension == ".sh":
        binary.chmod(0o700)
    (root / "current-version").write_text(version + "\n", encoding="utf-8")
    environment["FIXTURE_APP_HOME"] = str(root)
    result = invoke(binary, [], caller, environment)
    assert result.returncode != 0
    assert not result.stdout.strip()


def test_existing_launcher_is_not_partially_overwritten(tmp_path):
    (tmp_path / "install.cmd").write_text("retained", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_slot_binstubs(tmp_path, "fixture-app", "fixture_app")
    assert (tmp_path / "install.cmd").read_text(encoding="utf-8") == "retained"
    assert not (tmp_path / "fixture-app.ps1").exists()
