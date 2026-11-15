"""Native generated-launcher contracts, without touching an installed Manager."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import time
import venv
import zipfile
from contextlib import ExitStack, contextmanager
from email import message_from_bytes
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

from worktree_manager import self_install as si
from _manager_process_fixture import synthetic_runtime


def _run(argv, *, env, cwd, timeout=20):
    return subprocess.run(
        argv, env=env, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", timeout=timeout, **no_window_kwargs(),
    )


def _installed_application_wheel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, nested: bool,
) -> tuple[Path, Path, Path, Path]:
    source = tmp_path / "wheel-source"
    si._copy_payload(si.running_payload_dir(), source)
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    result = _run(
        [sys.executable, "-I", "-c",
         "from setuptools.build_meta import build_wheel; import sys; build_wheel(sys.argv[1])",
         str(wheels)], env=dict(os.environ), cwd=source, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheel, = wheels.glob("*.whl")
    repository = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(repository / "tools"))
    from promotion_python_artifacts import validate_wheel_sources

    with zipfile.ZipFile(wheel) as bundle:
        metadata_name, = (name for name in bundle.namelist() if name.endswith(".dist-info/METADATA"))
        validate_wheel_sources(
            bundle, {name: hashlib.sha256(bundle.read(name)).hexdigest() for name in bundle.namelist()},
            source_dir=repository / "worktree-manager",
            prefix=metadata_name.rsplit("/", 1)[0] + "/",
            metadata=message_from_bytes(bundle.read(metadata_name)),
            component="worktree-manager", candidate_root=repository,
        )
    root = tmp_path / "Manager root with spaces"
    slot = synthetic_runtime(root, "1.2.3", nested=nested)
    prefix = slot / ".venv" if nested else slot
    python = prefix / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    assert uv
    installed = _run(
        [uv, "pip", "install", "--python", str(python), "--offline", "--no-deps", str(wheel)],
        env=dict(os.environ), cwd=tmp_path,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    shutil.rmtree(source)
    if (slot / "payload").exists():
        shutil.rmtree(slot / "payload")
    located = _run([
        str(python), "-I", "-B", "-c",
        "import json;from worktree_manager.relocated_launch import _relocated_launch_script;"
        "print(json.dumps(str(_relocated_launch_script())))",
    ], env=dict(os.environ), cwd=tmp_path)
    assert located.returncode == 0, located.stdout + located.stderr
    payload = Path(json.loads(located.stdout)).parents[1]
    assert payload.is_relative_to(prefix)
    assert not (slot / "payload").exists()
    for name in ("pane-args.ps1", "pane-wrapper.ps1", "pane-wrapper.sh", "pane-launch.ps1",
                 "session-options.ps1", "session-options.sh", "psmux-passthrough.conf"):
        assert (payload / "bin" / name).is_file()
    policy = _run([
        getattr(sys, "_base_executable", sys.executable), "-I", "-B",
        str(payload / "bin" / "check-automatic-maintenance.py"),
    ], env=dict(os.environ, WORKTREE_MANAGER_ROOT=str(root)), cwd=tmp_path)
    assert policy.returncode == 0, policy.stdout + policy.stderr
    warmed = _run([
        str(python), "-I", "-S", "-B", "-X", "utf8",
        str(slot / ".runtime-bootstrap" / "runtime_launcher.py"),
        "-m", "worktree_manager", "--version",
    ], env=dict(os.environ, WORKTREE_MANAGER_ROOT=str(root)), cwd=tmp_path)
    assert warmed.returncode == 0, warmed.stdout + warmed.stderr
    return root, slot, payload, python


@contextmanager
def _serving_installed_wheel(root: Path, slot: Path, python: Path, env: dict):
    ready = root.parent / "serving-wheel.json"
    code = """
import json, os, sys
from pathlib import Path
from worktree_manager import mux_daemon as mux, mux_daemon_cutover as cutover
root, slot, ready = map(Path, sys.argv[1:])
lease = mux._acquire_daemon_lease(root)
assert lease is not None, "fixture incumbent could not acquire its serving lease"
runtime = None
try:
    runtime = mux.MuxDaemonRuntime(mux.registry_path(root), root=root, passive=False)
    runtime.start()
    assert runtime.port is not None
    cutover.publish_route(runtime.port, root=root, pid=os.getpid(), version=slot.name)
    assert mux.write_lock_data(mux.lock_path(root), runtime.lock_extra())
    assert runtime.registry.register({
        "project": "stage-fixture", "worktree_id": "incumbent",
        "mux_session": "fixture-session", "mux_bin": sys.executable,
        "mapping_revision": 1, "session_incarnation": "fixture:1", "live": True,
    })["applied"]
    temporary = ready.with_suffix(".tmp")
    temporary.write_text(json.dumps({"pid": os.getpid(), "port": runtime.port}))
    temporary.replace(ready)
    sys.stdin.buffer.read()
finally:
    if runtime is not None:
        runtime.shutdown()
    cutover.clear_route_if_owner(os.getpid(), root=root)
    mux._release_daemon_lease(lease)
"""
    # Serve control requests without the foreground loop's discovery/picker pumps.
    process, job = spawn_sync_in_kill_on_close_job(
        [str(python), "-I", "-B", "-c", code, str(root), str(slot), str(ready)],
        require_job=True, allow_breakaway=False, env=env, cwd=root,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "installed fixture incumbent did not become ready"
        assert process.poll() is None, "installed fixture incumbent exited before staging"
        yield process, json.loads(ready.read_text())
    finally:
        try:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, (stdout + stderr).decode("utf-8", "replace")
        finally:
            if job is not None:
                job.close()


@pytest.mark.parametrize("serving", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_installed_application_wheel_passive_readiness_without_source_or_uv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nested: bool, serving: bool,
) -> None:
    root, slot, _payload, python = _installed_application_wheel(
        tmp_path, monkeypatch, nested=nested,
    )
    home = tmp_path / "readiness-home"
    home.mkdir()
    providers = home / ".agent-worktrees" / "control-plane-providers.d"
    from worktree_manager.mux_mapping_registry import registry_path

    monitored = (
        root / "current-version", root / "last-known-good", root / "mux-daemon.lock",
        registry_path(root), slot / ".install-complete.json", slot / ".runtime-input-origins.json",
    )
    directories = (root / "mux-daemon-routing", home / ".local" / "bin", providers)

    def publication_state() -> dict:
        paths = {
            *monitored,
            *(path for directory in directories for path in directory.rglob("*") if path.is_file()),
        }
        return {
            path: (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
            if path.exists() else None for path in paths
        }

    environment = dict(
        os.environ, HOME=str(home), USERPROFILE=str(home), WORKTREE_MANAGER_ROOT=str(root),
        AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR=str(providers), PATH="", UV_OFFLINE="1",
    )
    with ExitStack() as lifetime:
        if serving:
            from worktree_manager.mux_daemon_cutover import ControlClient, routed_endpoint

            for name in ("current-version", "last-known-good"):
                (root / name).write_text(slot.name + "\n", encoding="utf-8")
            commands = home / ".local" / "bin"
            commands.mkdir(parents=True)
            (commands / "worktree-manager").write_bytes(b"fixture incumbent command\n")
            providers.mkdir(parents=True)
            (providers / "fixture.json").write_bytes(b'{"provider":"fixture-incumbent"}\n')
            incumbent, endpoint = lifetime.enter_context(
                _serving_installed_wheel(root, slot, python, environment),
            )
            client = ControlClient(f"http://127.0.0.1:{endpoint['port']}", root=root)
            health = client.health()
            assert health["status"] == "ready" and health["active"] is True
            assert health["runtime_generation"] == slot.name
            route = routed_endpoint(root)
            assert route is not None and route.pid == endpoint["pid"] and route.port == endpoint["port"]
        before = publication_state()
        readiness = _run([
            str(python), "-I", "-B", "-m", "worktree_manager.runtime_stage", "--root", str(root),
        ], env=environment, cwd=tmp_path)
        assert readiness.returncode == 0, readiness.stdout + readiness.stderr
        assert json.loads(readiness.stdout) == {
            "schema": "copilot-extensions.manager-stage-readiness", "version": 1,
            "generation": slot.name, "ready": True,
        }
        assert publication_state() == before
        if serving:
            assert incumbent.poll() is None
            health = client.health()
            assert health["status"] == "ready" and health["active"] is True
            assert health["runtime_generation"] == slot.name
            assert routed_endpoint(root) == route


@pytest.mark.parametrize("shell", ["ps1", "sh"] if os.name == "nt" else ["sh"])
@pytest.mark.parametrize("nested", [False, True])
def test_installed_application_wheel_launch_callbacks_without_source_or_uv(
    tmp_path, monkeypatch, shell, nested,
):
    root, slot, payload, _python = _installed_application_wheel(
        tmp_path, monkeypatch, nested=nested,
    )
    (root / "current-version").write_text(slot.name + "\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "native"))
    from test_manager_mux_callbacks import exercise_retained_callbacks

    exercise_retained_callbacks({
        "directory": str(tmp_path), "root": str(root), "slot": str(slot), "payload": str(payload),
    }, tmp_path, monkeypatch, shell)


@pytest.fixture(params=["cmd", "ps1", "sh"] if os.name == "nt" else ["sh"])
def launcher(request, tmp_path):
    kind = request.param
    path = tmp_path / f"manager.{kind}"
    path.write_text(getattr(si, f"_{kind}_binstub")(), encoding="utf-8", newline="")
    if kind == "cmd":
        (tmp_path / "worktree-manager.ps1").write_text(
            si._ps1_binstub(), encoding="utf-8",
        )
        argv = [os.environ["COMSPEC"], "/d", "/c", str(path)]
    elif kind == "ps1":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        assert shell, "native PowerShell is required on Windows"
        argv = [shell, "-NoProfile", "-NonInteractive", "-File", str(path)]
    else:
        shell = "bash"
        if os.name == "nt":
            # Never invoke Windows' bash.exe alias (which launches WSL).
            git = shutil.which("git")
            bash = Path(git).parent.parent / "bin" / "bash.exe" if git else None
            if bash is None or not bash.is_file():
                pytest.skip("native Git Bash is unavailable")
            shell = str(bash)
        argv = [shell, str(path)]
    root = tmp_path / "manager root"
    slot = root / "versions" / "1.2.3"
    slot.mkdir(parents=True)
    (root / "current-version").write_text("1.2.3\n", encoding="utf-8")
    env = dict(os.environ, WORKTREE_MANAGER_ROOT=str(root))
    for key in ("PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
        env.pop(key, None)
    return argv, env, slot


def _payload(slot, version="1.2.3"):
    pkg = slot / "src" / "worktree_manager"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (pkg / "__main__.py").write_text(
        "import json, sys, worktree_manager\n"
        "print(json.dumps(dict(version=worktree_manager.__version__, "
        "file=worktree_manager.__file__, cached=worktree_manager.__cached__, "
        "isolated=sys.flags.isolated, no_bytecode=sys.dont_write_bytecode, args=sys.argv[1:])))\n"
        "raise SystemExit(7 if '--fail' in sys.argv else 0)\n",
        encoding="utf-8",
    )
    (slot / ".install-complete").touch()
    (slot / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[project]\nname = "copilot-extensions-worktree-manager"\n'
        f'version = "{version}"\nrequires-python = ">=3.10"\n'
        '[tool.setuptools.packages.find]\nwhere = ["src"]\n'
        '[tool.uv]\nexclude-newer = "7 days"\n',
        encoding="utf-8",
    )


@pytest.mark.skipif(os.name != "nt", reason="Native CMD compatibility")
@pytest.mark.parametrize("launcher", ["cmd"], indirect=True)
def test_cmd_without_powershell_keeps_the_legacy_picker_entry(launcher, tmp_path):
    argv, env, slot = launcher
    _payload(slot)
    runtime = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / "Scripts" / "python.exe"
    uv = shutil.which("uv")
    assert uv, "the contained runner requires uv"
    installed = _run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", str(slot)],
        env=env, cwd=slot,
    )
    assert installed.returncode == 0, installed.stderr
    env["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32")
    assert shutil.which("pwsh.exe", path=env["PATH"]) is None

    result = _run(argv, env=env, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["args"] == []


def test_constructed_slot_runs_installed_package_without_resolution(launcher, tmp_path):
    argv, env, slot = launcher
    _payload(slot)
    runtime = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    assert uv, "the contained runner requires uv"
    # Install the actual src-layout distribution, not a PYTHONPATH injection.
    installed = _run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", str(slot)],
        env=env, cwd=slot,
    )
    assert installed.returncode == 0, installed.stderr
    # Any project resolution must now refuse, including offline resolution.
    # No such dependency belongs to the already-built environment.
    with (slot / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '[dependency-groups]\n'
            'dev = ["manager-launcher-resolution-must-not-run==0.0.0"]\n'
        )
    env["UV_OFFLINE"] = "1"
    env["ERRORLEVEL"] = "0"
    _payload(tmp_path)
    env["PYTHONPATH"] = str(tmp_path / "src")
    env["UV_PROJECT_ENVIRONMENT"] = str(tmp_path / "foreign-env")
    before = {
        str(path.relative_to(slot)): (
            path.read_bytes() if path.is_file() else None,
            path.stat().st_mtime_ns, path.stat().st_ctime_ns, path.stat().st_ino,
        )
        for path in (slot, *slot.rglob("*"))
    }
    result = _run(argv + ["argument with spaces", "\u2713"], env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["version"] == "1.2.3"
    assert Path(output["file"]).is_relative_to(runtime)
    assert output["args"] == ["argument with spaces", "\u2713"]
    assert output["isolated"] == 1 and output["no_bytecode"] is True
    cache = Path(output["cached"])
    cache_root = Path(env["WORKTREE_MANAGER_ROOT"]) / "cache" / "python"
    assert not cache.exists()
    assert not cache_root.exists()
    assert not Path(env["UV_PROJECT_ENVIRONMENT"]).exists()
    failed = _run(argv + ["--fail"], env=env, cwd=tmp_path)
    assert failed.returncode == 7, failed.stderr
    assert not cache_root.exists()
    assert {
        str(path.relative_to(slot)): (
            path.read_bytes() if path.is_file() else None,
            path.stat().st_mtime_ns, path.stat().st_ctime_ns, path.stat().st_ino,
        )
        for path in (slot, *slot.rglob("*"))
    } == before
    installed_module = Path(output["file"])
    module_stat = installed_module.stat()
    installed_module.write_bytes(
        installed_module.read_bytes().replace(b'"1.2.3"', b'"1.2.4"')
    )
    os.utime(installed_module, ns=(module_stat.st_atime_ns, module_stat.st_mtime_ns))
    (slot / "src" / "worktree_manager" / "__init__.py").write_text(
        '__version__ = "1.2.4"\n', encoding="utf-8",
    )
    replaced = _run(argv, env=env, cwd=tmp_path)
    assert replaced.returncode == 0, replaced.stderr
    replacement = json.loads(replaced.stdout)
    assert replacement["version"] == "1.2.4"
    assert replacement["file"] == output["file"]
    assert Path(replacement["cached"]) == cache
    assert not cache.exists()
    assert not cache_root.exists()


@pytest.mark.skipif(os.name != "nt", reason="Native CMD compatibility")
@pytest.mark.parametrize("launcher", ["cmd"], indirect=True)
def test_native_cmd_unicode_root_fallback_never_allocates_cache(launcher, tmp_path):
    argv, env, slot = launcher
    root = Path(env["WORKTREE_MANAGER_ROOT"])
    unicode_root = root.with_name("manager \u03bb root")
    root.rename(unicode_root)
    env["WORKTREE_MANAGER_ROOT"] = str(unicode_root)
    env["CE_TERMINAL_LAUNCH"] = "1"
    slot = unicode_root / slot.relative_to(root)
    _payload(slot)
    runtime = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / "Scripts" / "python.exe"
    uv = shutil.which("uv")
    assert uv, "the contained runner requires uv"
    installed = _run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", str(slot)],
        env=env, cwd=slot,
    )
    assert installed.returncode == 0, installed.stderr

    result = _run(argv, env=env, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["no_bytecode"] is True
    assert not Path(output["cached"]).exists()
    assert not (unicode_root / "cache").exists()


def test_installed_evidence_reuses_sealed_cache(launcher, tmp_path):
    argv, env, slot = launcher
    _payload(slot)
    runtime = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    assert uv, "the contained runner requires uv"
    installed = _run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", str(slot)],
        env=env, cwd=slot,
    )
    assert installed.returncode == 0, installed.stderr
    site = Path(_run([str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_paths()[\"purelib\"])"],
                     env=env, cwd=tmp_path).stdout.strip())
    procutil = site / "agent_procutil"
    shutil.copytree(
        Path(__file__).resolve().parents[2] / "libs" / "agent-procutil" / "src" / "agent_procutil",
        procutil, ignore=shutil.ignore_patterns("__pycache__"),
    )
    (slot / ".install-complete.json").write_text(json.dumps({
        "version": slot.name, "completed_at": "fixture", "pid": 0, "payload_hash": "a" * 64,
    }), encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    cache_base = home / "Library" / "Caches" if sys.platform == "darwin" else tmp_path / "user cache"
    env.update(HOME=str(home), USERPROFILE=str(home),
               LOCALAPPDATA=str(cache_base), XDG_CACHE_HOME=str(cache_base))
    cache_root = cache_base / "copilot-extensions" / "python" / "v1"
    caches = []
    # CE_TERMINAL_LAUNCH forces the CMD launcher's native branch instead of
    # delegating to PowerShell.
    native = [{"CE_TERMINAL_LAUNCH": "1"}] if argv[0] == os.environ.get("COMSPEC") else []
    for overlay in [{}, *native]:
        for extra in ([], ["--fail"]):
            result = _run(argv + extra, env={**env, **overlay}, cwd=tmp_path)
            assert result.returncode == (7 if extra else 0), result.stderr
            cache = Path(json.loads(result.stdout)["cached"])
            caches.append(cache)
            assert cache.is_file()
            assert cache.is_relative_to(cache_root) or (
                os.name == "nt" and cache.is_relative_to(Path("\\\\?\\" + str(cache_root)))
            )
            assert not (Path(env["WORKTREE_MANAGER_ROOT"]) / "cache").exists()
    assert len(set(caches)) == 1


def test_selected_payload_only_slot_is_not_an_installed_runtime(launcher, tmp_path, monkeypatch):
    """Failed canonical construction cannot bless an interpreter-only slot."""
    argv, env, previous_slot = launcher
    root = previous_slot.parents[1]
    monkeypatch.setattr(si, "local_bin", lambda: tmp_path / "local-bin")
    monkeypatch.setattr(si, "control_plane_providers_dir", lambda: tmp_path / "providers")
    payload = si.running_payload_dir()
    version = si.payload_version(payload)
    slot = si.version_slot(version, root)
    si._copy_payload(payload, slot)
    (root / si.MARKER).write_text(version, encoding="utf-8")
    before = (root / si.MARKER).read_bytes()
    assert not si._slot_is_complete(slot)
    assert not (slot / ".venv").exists()

    # A failed resolving first use can leave only the interpreter in this
    # already-selected payload. It is not the constructed-runtime fast path.
    venv.EnvBuilder(with_pip=False).create(slot / ".venv")
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    imported = _run(
        [str(python), "-I", "-c", "import worktree_manager"],
        env=env, cwd=tmp_path,
    )
    assert imported.returncode != 0
    assert "No module named 'worktree_manager'" in imported.stderr
    @contextmanager
    def refused(*args, **kwargs):
        raise RuntimeError("fixture construction refused")
        yield
    monkeypatch.setattr(si._runtime_installer(), "candidate", refused)
    result = si.self_install(payload, root=root, dry_run=False)
    assert result.action == "error" and "construction refused" in result.reason
    assert (root / si.MARKER).read_bytes() == before
    env["UV_OFFLINE"] = "1"
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert "invalid constructed worktree-manager runtime" in result.stderr
    assert not result.stdout.strip()


def test_marker_identity_is_not_a_normalized_distribution_version(launcher, tmp_path):
    argv, env, previous_slot = launcher
    slot = previous_slot.with_name("1.2.3.dev1")
    slot.mkdir()
    (slot.parents[1] / si.MARKER).write_text(slot.name, encoding="utf-8")
    _payload(slot, version="1.2.3-dev1")
    runtime = slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(runtime)
    python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    uv = shutil.which("uv")
    assert uv
    installed = _run(
        [uv, "pip", "install", "--python", str(python), "--no-deps", str(slot)],
        env=env, cwd=slot,
    )
    assert installed.returncode == 0, installed.stderr
    metadata = _run(
        [
            str(python), "-I", "-c",
            "import importlib.metadata as m; print(m.version('copilot-extensions-worktree-manager'))",
        ],
        env=env, cwd=tmp_path,
    )
    assert metadata.returncode == 0, metadata.stderr
    assert metadata.stdout.strip() == "1.2.3.dev1"
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["version"] == "1.2.3-dev1"
    assert Path(output["file"]).is_relative_to(runtime)


def test_missing_runtime_does_not_attempt_project_resolution(launcher, tmp_path):
    argv, env, slot = launcher
    _payload(slot)
    with (slot / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            '[dependency-groups]\n'
            'dev = ["manager-launcher-resolution-must-not-run==0.0.0"]\n'
        )
    env["UV_OFFLINE"] = "1"
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert "invalid constructed worktree-manager runtime" in result.stderr
    assert not result.stdout.strip()


@pytest.mark.parametrize("damage", ["missing", "unexecutable"])
def test_retained_launcher_cannot_report_success_without_starting_interpreter(launcher, tmp_path, damage):
    argv, env, slot = launcher
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.write_text("raise SystemExit(0)\n", encoding="utf-8")
    if damage == "unexecutable":
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir()
        python.write_bytes(b"not an executable")
        python.chmod(0o755)
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert result.stderr.strip()


@pytest.mark.parametrize("exit_code", [0, 7])
def test_retained_launcher_preserves_executed_child_status(launcher, tmp_path, exit_code):
    argv, env, slot = launcher
    venv.EnvBuilder(with_pip=False).create(slot)
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.write_text(
        f"import sys\nsys.stderr.write('fixture diagnostic\\n')\nraise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert "fixture diagnostic" in result.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell 5.1 native stderr semantics")
@pytest.mark.parametrize("exit_code", [0, 7])
def test_retained_powershell51_forwards_native_diagnostic_and_status(tmp_path, exit_code):
    host = shutil.which("powershell")
    assert host, "Windows PowerShell 5.1 is required for this native contract"
    root = tmp_path / "manager"
    slot = root / "versions" / "1.2.3"
    venv.EnvBuilder(with_pip=False).create(slot)
    (root / "current-version").write_text(slot.name, encoding="utf-8")
    bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
    bootstrap.parent.mkdir()
    bootstrap.write_text(
        f"import sys\nsys.stderr.write('fixture diagnostic\\n')\nraise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    launcher = tmp_path / "manager.ps1"
    launcher.write_text(si._ps1_binstub(), encoding="utf-8")
    result = _run(
        [host, "-NoProfile", "-NonInteractive", "-File", str(launcher)],
        env={**os.environ, "WORKTREE_MANAGER_ROOT": str(root)}, cwd=tmp_path,
    )
    assert result.returncode == exit_code, result.stdout + result.stderr
    assert "fixture diagnostic" in result.stderr


def test_unconstructed_launcher_requires_explicit_canonical_bootstrap(launcher, tmp_path):
    argv, env, slot = launcher
    _payload(slot)
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert "invalid constructed worktree-manager runtime" in result.stderr
    assert not result.stdout.strip()
    assert not (slot / ".venv").exists()
    assert (slot.parents[1] / si.MARKER).read_text().strip() == "1.2.3"


@pytest.mark.parametrize(
    "damage", ["uninstalled", "missing-metadata", "missing-completion", "missing-entrypoint"],
)
def test_incomplete_environment_does_not_use_foreign_package(launcher, tmp_path, damage):
    argv, env, slot = launcher
    _payload(slot)
    venv.EnvBuilder(with_pip=False).create(slot / ".venv")
    if damage != "uninstalled":
        python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        uv = shutil.which("uv")
        assert uv
        installed = _run(
            [uv, "pip", "install", "--python", str(python), "--no-deps", str(slot)],
            env=env, cwd=slot,
        )
        assert installed.returncode == 0, installed.stderr
        if damage == "missing-metadata":
            metadata = list((slot / ".venv").rglob("copilot_extensions_worktree_manager-*.dist-info"))
            assert len(metadata) == 1
            shutil.rmtree(metadata[0])
        elif damage == "missing-completion":
            (slot / ".install-complete").unlink()
        else:
            (slot / "src" / "worktree_manager" / "__main__.py").unlink()
    _payload(tmp_path)
    # A broken existing environment must not silently import a caller's package
    # or trigger acquisition to turn an incomplete install into success.
    env["UV_OFFLINE"] = "1"
    env["PYTHONPATH"] = str(tmp_path / "src")
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert "invalid constructed worktree-manager runtime" in result.stderr
    assert not result.stdout.strip()


def test_missing_selected_slot_is_not_another_project(launcher, tmp_path):
    argv, env, slot = launcher
    shutil.rmtree(slot)
    _payload(tmp_path)
    env["UV_OFFLINE"] = "1"
    result = _run(argv, env=env, cwd=tmp_path)
    assert result.returncode != 0
    assert not result.stdout.strip()


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe batch-file resume contract")
@pytest.mark.parametrize("exit_code", [0, 7])
@pytest.mark.parametrize("runtime_kind", ["retained", "legacy"])
def test_cmd_does_not_resume_replaced_launcher(tmp_path, exit_code, runtime_kind):
    launcher = tmp_path / "manager.cmd"
    launcher.write_text(si._cmd_binstub(), encoding="utf-8", newline="")
    (tmp_path / "worktree-manager.ps1").write_text(si._ps1_binstub(), encoding="utf-8")
    root = tmp_path / "manager root"
    slot = root / "versions" / "1.2.3"
    runtime = slot if runtime_kind == "retained" else slot / ".venv"
    venv.EnvBuilder(with_pip=False).create(runtime)
    (root / "current-version").write_text("1.2.3\n", encoding="utf-8")
    child_source = (
        "import os, pathlib\n"
        "pathlib.Path(os.environ['FIXTURE_LAUNCHER']).write_text("
        "'@echo off\\n' + 'echo replaced-launcher-must-not-run\\n' * 300, "
        "encoding='utf-8')\n"
        "print('fixture session exited')\n"
        f"raise SystemExit({exit_code})\n"
    )
    env = dict(
        os.environ, WORKTREE_MANAGER_ROOT=str(root), FIXTURE_LAUNCHER=str(launcher),
        ERRORLEVEL="0",
    )
    if runtime_kind == "retained":
        bootstrap = slot / ".runtime-bootstrap" / "runtime_launcher.py"
        bootstrap.parent.mkdir()
        bootstrap.write_text(child_source, encoding="utf-8")
    else:
        _payload(slot)
        (slot / "src" / "worktree_manager" / "__main__.py").write_text(
            child_source, encoding="utf-8",
        )
        uv = shutil.which("uv")
        assert uv, "the contained runner requires uv"
        installed = _run(
            [uv, "pip", "install", "--python", str(runtime / "Scripts" / "python.exe"),
             "--no-deps", str(slot)],
            env=env, cwd=tmp_path,
        )
        assert installed.returncode == 0, installed.stderr
    result = _run(
        [os.environ["COMSPEC"], "/d", "/c", str(launcher)],
        env=env,
        cwd=tmp_path,
    )
    assert result.returncode == exit_code, result.stderr
    assert result.stdout.strip() == "fixture session exited"
    assert not result.stderr.strip()
