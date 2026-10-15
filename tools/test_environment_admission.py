"""Real process boundaries for environment exclusion, not host serialization."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent
_previous_path = sys.path.copy()
sys.path.insert(0, str(TOOLS))
try:
    import _admission_protocol as admission
finally:
    sys.path[:] = _previous_path

CHILD = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from _admission_protocol import acquire, AlreadyRunningError
try:
    lease = acquire(float(sys.argv[3]), Path(sys.argv[2]))
except AlreadyRunningError:
    raise SystemExit(3)
try:
    if sys.argv[4] == 'hold':
        Path(sys.argv[5]).touch()
        sys.stdin.readline()
finally:
    lease.release()
"""


def child_command(root: Path, wait: float = 0, mode: str = "probe", ready: Path | None = None):
    return [sys.executable, "-I", "-c", CHILD, str(TOOLS), str(root), str(wait), mode, str(ready)]


def launch_options():
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


@pytest.fixture
def cache(tmp_path, monkeypatch):
    cache_root = tmp_path / "cache"
    monkeypatch.setenv("LOCALAPPDATA", str(cache_root))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))
    return tmp_path


def probe(root: Path, wait: float = 0) -> int:
    root.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        child_command(root, wait), timeout=10, check=False,
        stdin=subprocess.DEVNULL, capture_output=True, **launch_options(),
    ).returncode


EXHAUSTIVE = pytest.mark.runner_exhaustive


@pytest.mark.parametrize("other", [
    "same", "other-plugin",
    *[pytest.param(value, marks=EXHAUSTIVE) for value in ("other-checkout", "dotdot", "alias", "case")],
])
def test_separate_processes_contend_only_for_concrete_environment(cache, other):
    root = cache / "checkout-a" / "plugin-a"
    root.mkdir(parents=True)
    targets = {
        "same": root,
        "other-plugin": root.with_name("plugin-b"),
        "other-checkout": cache / "checkout-b" / "plugin-a",
        "dotdot": root / ".." / root.name,
        "case": Path(str(root).upper()) if os.name == "nt" else root,
    }
    if other == "alias":
        alias = cache / "alias"
        if os.name == "nt":
            subprocess.run(
                [os.environ["COMSPEC"], "/c", "mklink", "/J", str(alias), str(root)],
                check=True, timeout=10, capture_output=True, **launch_options(),
            )
        else:
            alias.symlink_to(root, target_is_directory=True)
        target = alias
    else:
        target = targets[other]
    ready = cache / "ready"
    holder = subprocess.Popen(
        child_command(root, mode="hold", ready=ready),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **launch_options(),
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "holder did not acquire its environment"
        assert probe(target) == (0 if other in {"other-plugin", "other-checkout"} else 3)
        if other == "same":
            started = time.monotonic()
            assert probe(target, wait=0.2) == 3
            assert 0.15 <= time.monotonic() - started < 5
    finally:
        holder.communicate("release\n", timeout=10)
    assert holder.returncode == 0
    assert probe(target) == 0


def load_runner(filename: str):
    spec = importlib.util.spec_from_file_location(Path(filename).stem.replace("-", "_"), TOOLS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    previous_path = sys.path.copy()
    sys.path.insert(0, str(TOOLS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = previous_path
    return module


@pytest.mark.parametrize("mode", [
    [], *[pytest.param(value, marks=EXHAUSTIVE) for value in (
        ["--guards"], ["--collect-only"], ["--prepare-only"],
    )],
])
def test_plugin_main_protects_preparation_and_use_in_every_mode(cache, monkeypatch, mode):
    runner = load_runner("run-plugin-tests.py")
    monkeypatch.setattr(runner, "VENV_ROOT", cache / "venvs")
    monkeypatch.setattr(runner, "PLUGINS", cache / "plugins")
    monkeypatch.setenv("TEMP", str(cache / "sandboxes"))
    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    plugin = runner.PLUGINS / "alpha"
    (plugin / "tests").mkdir(parents=True)
    (plugin / "tests" / "test_example.py").touch()
    environment = runner.VENV_ROOT / "alpha"
    events = []

    def prepare(name, uv, *, reinstall):
        assert reinstall
        assert probe(environment) == 3
        events.append("prepare")
        return Path(sys.executable)

    def run(*args, **kwargs):
        assert probe(environment) == 3
        events.append("use")
        return 0

    monkeypatch.setattr(runner, "_ensure_venv", prepare)
    monkeypatch.setattr(runner, "run_contained", run)
    assert runner.main(["alpha", "--reinstall", *mode]) == 0
    assert events == (["prepare"] if "--prepare-only" in mode else ["prepare", "use"])
    assert probe(environment) == 0


@pytest.mark.parametrize("failure", [
    RuntimeError("setup failed"),
    pytest.param(SystemExit(9), marks=EXHAUSTIVE),
    pytest.param(KeyboardInterrupt(), marks=EXHAUSTIVE),
])
def test_plugin_main_releases_environment_on_exception(cache, monkeypatch, failure):
    runner = load_runner("run-plugin-tests.py")
    monkeypatch.setattr(runner, "VENV_ROOT", cache / "venvs")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")

    def fail(*args, **kwargs):
        assert probe(runner.VENV_ROOT / "alpha") == 3
        raise failure

    monkeypatch.setattr(runner, "run_plugin", fail)
    if isinstance(failure, Exception):
        assert runner.main(["alpha"]) == 1
    else:
        with pytest.raises(type(failure)):
            runner.main(["alpha"])
    assert probe(runner.VENV_ROOT / "alpha") == 0


def test_list_never_resolves_or_acquires_an_environment(cache, monkeypatch):
    runner = load_runner("run-plugin-tests.py")
    monkeypatch.setattr(runner, "_acquire_admission", lambda *_: pytest.fail("list acquired a lease"))
    monkeypatch.setattr(runner.shutil, "which", lambda _: pytest.fail("list discovered uv"))
    monkeypatch.setattr(runner, "VENV_ROOT", cache / "must-not-exist")
    assert runner.main(["alpha", "--list"]) == 0
    assert not runner.VENV_ROOT.exists()
    assert not (cache / "cache").exists()


def test_busy_main_names_environment_and_keeps_exit_code(cache, monkeypatch, capsys):
    runner = load_runner("run-plugin-tests.py")
    monkeypatch.setattr(runner, "VENV_ROOT", cache / "venvs")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    environment = runner.VENV_ROOT / "alpha"
    environment.mkdir(parents=True)
    lease = admission.acquire(0, environment)
    try:
        assert runner.main(["alpha"]) == 3
    finally:
        lease.release()
    assert str(environment) in capsys.readouterr().err


@pytest.mark.parametrize("failure", [
    None, pytest.param("prepare", marks=EXHAUSTIVE), pytest.param("use", marks=EXHAUSTIVE),
])
def test_standalone_managed_environment_protects_prepare_and_use(cache, monkeypatch, failure):
    runner = load_runner("run-standalone-tests.py")
    monkeypatch.setattr(runner, "REPO", cache)
    (cache / "services/index" / "tests").mkdir(parents=True)
    environment = cache / ".test-venvs" / sys.platform / "services/index"
    environment.mkdir(parents=True)
    events = []

    def prepare(*args, **kwargs):
        assert probe(environment) == 3
        events.append("prepare")
        if failure == "prepare":
            raise OSError("preparation failed")

    def run(*args, **kwargs):
        assert probe(environment) == 3
        events.append("use")
        if failure == "use":
            raise OSError("execution failed")
        return 0

    monkeypatch.setattr(runner, "prepare", prepare)
    monkeypatch.setattr(runner, "run_contained", run)
    assert runner.main(["services/index", "--prepare"]) == (1 if failure else 0)
    assert events == (["prepare"] if failure == "prepare" else ["prepare", "use"])
    assert probe(environment) == 0


@EXHAUSTIVE
def test_custom_venv_discovery_does_not_follow_launcher_to_base_interpreter(cache, monkeypatch):
    runner = load_runner("run-standalone-tests.py")
    environment = cache / "custom-environment"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(environment)],
        check=True, timeout=30, capture_output=True, **launch_options(),
    )
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert runner.interpreter_environment(python) == environment.resolve()
    monkeypatch.setattr(runner, "REPO", cache)
    (cache / "services/index" / "tests").mkdir(parents=True)
    seen = []

    def run(command, **kwargs):
        assert Path(command[0]) == python
        assert probe(environment) == 3
        seen.append(True)
        return 0

    monkeypatch.setattr(runner, "run_contained", run)
    assert runner.main(["services/index", "--python", str(python)]) == 0
    assert seen == [True]
    assert probe(environment) == 0


@pytest.mark.parametrize("custom", [False, pytest.param(True, marks=EXHAUSTIVE)], ids=["managed", "custom-venv"])
@pytest.mark.parametrize("wait", [0, 3], ids=["busy", "bounded-wait"])
def test_missing_interpreter_during_rebuild_obeys_environment_lease(cache, custom, wait):
    environment = cache / ".test-venvs" / sys.platform / "services/index"
    environment.mkdir(parents=True)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    ready = cache / "holder-ready"
    waiter_ready = cache / "waiter-ready"
    holder = subprocess.Popen(
        child_command(environment, mode="hold", ready=ready),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **launch_options(),
    )
    waiter = None
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        assert not python.exists()
        (cache / "services/index" / "tests").mkdir(parents=True)
        script = """
import importlib.util, sys
from pathlib import Path
tools, repo, python, ready, custom, wait = sys.argv[1:]
sys.path.insert(0, tools)
spec = importlib.util.spec_from_file_location('standalone_waiter', Path(tools) / 'run-standalone-tests.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
runner.REPO = Path(repo)
def run(command, **kwargs):
    assert Path(command[0]) == Path(python)
    assert Path(python).is_file()
    print('protected-test-use', flush=True)
    return 0
runner.run_contained = run
runner.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(AssertionError('candidate probed'))
Path(ready).touch()
arguments = ['services/index', '--admission-wait', wait]
if custom == 'True':
    arguments += ['--python', python, '--environment-root', str(Path(python).parent.parent)]
raise SystemExit(runner.main(arguments))
"""
        waiter = subprocess.Popen(
            [
                sys.executable, "-I", "-c", script, str(TOOLS), str(cache),
                str(python), str(waiter_ready), str(custom), str(wait),
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **launch_options(),
        )
        if wait == 0:
            stdout, stderr = waiter.communicate(timeout=10)
            assert waiter.returncode == 3, (stdout, stderr)
            assert "BUSY" in stderr
            assert "protected-test-use" not in stdout
        else:
            deadline = time.monotonic() + 10
            while not waiter_ready.exists() and waiter.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert waiter_ready.exists()
            time.sleep(0.3)
            assert waiter.poll() is None, "missing executable bypassed the lease wait"
            python.parent.mkdir(parents=True)
            python.touch()
            holder.communicate("release\n", timeout=10)
            stdout, stderr = waiter.communicate(timeout=10)
            assert waiter.returncode == 0, (stdout, stderr)
            assert "protected-test-use" in stdout
    finally:
        if holder.poll() is None:
            holder.communicate("release\n", timeout=10)
        if waiter is not None and waiter.poll() is None:
            waiter.communicate(timeout=10)
    assert holder.returncode == 0


@pytest.mark.parametrize("alias_shape", ["macos-case", "linux-bind-mount"])
def test_filesystem_identity_not_path_spelling_keys_aliases(cache, monkeypatch, alias_shape):
    from types import SimpleNamespace
    import stat

    roots = (
        (cache / "Environment", cache / "environment")
        if alias_shape == "macos-case"
        else (cache / "source" / "venv", cache / "mount" / "venv")
    )
    metadata = SimpleNamespace(st_dev=71, st_ino=108, st_mode=stat.S_IFDIR)
    monkeypatch.setattr(Path, "stat", lambda self, **kwargs: metadata)
    assert admission.admission_dir(roots[0]) == admission.admission_dir(roots[1])
    monkeypatch.setattr(
        Path, "stat",
        lambda self, **kwargs: SimpleNamespace(
            st_dev=71, st_ino=108 if str(self) == str(roots[0]) else 109, st_mode=stat.S_IFDIR,
        ),
    )
    assert admission.admission_dir(roots[0]) != admission.admission_dir(roots[1])


def test_absent_or_unidentifiable_environment_never_uses_path_fallback(cache, monkeypatch):
    from types import SimpleNamespace
    import stat

    root = cache / "missing"
    with pytest.raises(FileNotFoundError):
        admission.admission_dir(root)
    assert not root.exists()
    monkeypatch.setattr(
        Path, "stat",
        lambda self, **kwargs: SimpleNamespace(st_dev=1, st_ino=0, st_mode=stat.S_IFDIR),
    )
    with pytest.raises(ValueError, match="filesystem identity"):
        admission.admission_dir(root)


@pytest.mark.parametrize("mode", [
    "reinstall",
    *[pytest.param(value, marks=EXHAUSTIVE) for value in ("fresh", "drift", "partial", "root-alias")],
])
def test_real_uv_rebuild_preserves_leased_directory_and_exclusion(cache, monkeypatch, mode):
    import shutil

    uv = shutil.which("uv")
    assert uv, "runner contract requires uv"
    runner = load_runner("run-plugin-tests.py")
    monkeypatch.setattr(runner, "VENV_ROOT", cache / "venvs")
    monkeypatch.setattr(runner, "_dep_fingerprint", lambda _: "current")
    monkeypatch.setattr(runner, "_has_pyproject", lambda _: False)
    monkeypatch.setenv("UV_VENV_CLEAR", "true")
    root = runner.VENV_ROOT / "alpha"
    if mode == "root-alias":
        actual_root = cache / "actual-root"
        actual_root.mkdir()
        root.parent.mkdir()
        if os.name == "nt":
            subprocess.run(
                [os.environ["COMSPEC"], "/c", "mklink", "/J", str(root), str(actual_root)],
                check=True, timeout=10, capture_output=True, **launch_options(),
            )
        else:
            root.symlink_to(actual_root, target_is_directory=True)
    else:
        root.mkdir(parents=True)
    python = runner._venv_python("alpha")
    real_run = subprocess.run
    if mode in {"reinstall", "drift"}:
        real_run(
            [uv, "venv", "--allow-existing", "--python", sys.executable, str(root)],
            env={k: v for k, v in os.environ.items() if k != "UV_VENV_CLEAR"},
            check=True, timeout=30, capture_output=True, **launch_options(),
        )
    if mode != "fresh":
        (root / "stale-package").mkdir()
        (root / "stale-package" / "stale.txt").touch()
        (root / ".dep-fingerprint").write_text("current" if mode == "reinstall" else "old")
    outside = cache / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.touch()
    link = root / "outside-link"
    if os.name == "nt":
        real_run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(outside)],
            check=True, timeout=10, capture_output=True, **launch_options(),
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    before = root.stat()
    before_key = admission.admission_dir(root)
    if mode == "root-alias":
        assert admission.admission_dir(actual_root) == before_key
    invocations = []

    def run(command, **kwargs):
        if command[:2] == [uv, "pip"]:
            assert python.is_file()
            return subprocess.CompletedProcess(command, 0)
        if command[:2] == [uv, "venv"]:
            assert command[2] == "--allow-existing"
            assert "UV_VENV_CLEAR" not in kwargs["env"]
            assert root.is_dir()
            assert not (root / "stale-package").exists()
            assert sentinel.exists()
            assert admission.admission_dir(root) == before_key
            # Probe without the monkeypatched subprocess.run call.
            contender = real_run(
                child_command(actual_root if mode == "root-alias" else root),
                timeout=10, capture_output=True, **launch_options(),
            )
            assert contender.returncode == 3
            invocations.append("uv")
            kwargs.update(capture_output=True, timeout=30, **launch_options())
            return real_run(command, **kwargs)
        return real_run(command, **kwargs)

    lease = admission.acquire(0, root)
    monkeypatch.setattr(runner.subprocess, "run", run)
    try:
        assert runner._ensure_venv("alpha", uv, reinstall=mode == "reinstall") == python
        after = root.stat()
        assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
        assert admission.admission_dir(root) == before_key
        assert probe(root) == 3
    finally:
        lease.release()
    assert invocations == ["uv"]
    assert sentinel.exists()
    assert probe(root) == 0


@EXHAUSTIVE
def test_standalone_real_uv_preparation_preserves_environment_identity(cache, monkeypatch):
    import shutil

    uv = shutil.which("uv")
    assert uv
    runner = load_runner("run-standalone-tests.py")
    monkeypatch.setattr(runner, "REPO", cache)
    monkeypatch.setenv("UV_VENV_CLEAR", "true")
    root = cache / ".test-venvs" / "standalone"
    root.mkdir(parents=True)
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    before = root.stat()
    key = admission.admission_dir(root)
    real_run = subprocess.run
    calls = []

    def run(command, **kwargs):
        if command[:2] == ["uv", "pip"]:
            assert python.is_file()
            return subprocess.CompletedProcess(command, 0)
        if command[:2] == ["uv", "venv"]:
            assert command == ["uv", "venv", "--allow-existing", str(root)]
            assert "UV_VENV_CLEAR" not in kwargs["env"]
            assert real_run(
                child_command(root), timeout=10, capture_output=True, **launch_options(),
            ).returncode == 3
            command[0] = uv
            kwargs["timeout"] = 30
            calls.append("uv")
        return real_run(command, **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", run)
    lease = admission.acquire(0, root)
    try:
        runner.prepare("services/index", python, smoke=True)
        after = root.stat()
        assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
        assert admission.admission_dir(root) == key
    finally:
        lease.release()
    assert calls == ["uv"]
    assert probe(root) == 0


@pytest.mark.parametrize("failure", ["file-root", "stat-error", "acquire-error"])
def test_environment_setup_failure_does_not_abort_other_targets(cache, monkeypatch, failure):
    runner = load_runner("run-plugin-tests.py")
    monkeypatch.setattr(runner, "VENV_ROOT", cache / "venvs")
    monkeypatch.setattr(runner.shutil, "which", lambda _: "uv")
    runner.VENV_ROOT.mkdir()
    if failure == "file-root":
        (runner.VENV_ROOT / "alpha").touch()
    original_acquire = runner._acquire_admission
    acquired = []
    ran = []

    def acquire(wait, root):
        if root.name == "alpha":
            if failure == "stat-error":
                raise OSError("cannot stat environment")
            if failure == "acquire-error":
                raise ValueError("unidentifiable environment")
        acquired.append(root)
        return original_acquire(wait, root)

    def run(name, *args, **kwargs):
        assert probe(runner.VENV_ROOT / name) == 3
        ran.append(name)
        return 0

    monkeypatch.setattr(runner, "_acquire_admission", acquire)
    monkeypatch.setattr(runner, "run_plugin", run)
    assert runner.main(["alpha", "beta"]) == 1
    assert ran == ["beta"]
    assert acquired == [runner.VENV_ROOT / "beta"]
    assert probe(runner.VENV_ROOT / "beta") == 0


def test_contents_cleanup_without_python312_junction_api(cache, monkeypatch):
    runner = load_runner("run-plugin-tests.py")
    monkeypatch.setattr(runner, "VENV_ROOT", cache / "venvs")
    root = runner.VENV_ROOT / "alpha"
    (root / "ordinary-directory").mkdir(parents=True)
    (root / "ordinary-directory" / "file").touch()
    (root / "ordinary-file").touch()
    outside = cache / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.touch()
    link = root / "outside-link"
    if os.name == "nt":
        subprocess.run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(outside)],
            check=True, timeout=10, capture_output=True, **launch_options(),
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    monkeypatch.delattr(Path, "is_junction", raising=False)
    monkeypatch.setattr(runner, "_dep_fingerprint", lambda _: "new")
    monkeypatch.setattr(runner, "_has_pyproject", lambda _: False)
    before = root.stat()

    def run(command, **kwargs):
        if command[1] == "venv":
            assert list(root.iterdir()) == []
            python = runner._venv_python("alpha")
            python.parent.mkdir()
            python.touch()

    monkeypatch.setattr(runner.subprocess, "run", run)
    lease = admission.acquire(0, root)
    try:
        runner._ensure_venv("alpha", "uv", reinstall=True)
        after = root.stat()
        assert (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)
        assert sentinel.exists()
    finally:
        lease.release()


def test_ci_runner_portfolio_keeps_smoke_path_gated_and_manual_exhaustive():
    import re
    import yaml

    workflow = yaml.safe_load(
        (TOOLS.parent / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"),
    )
    for platform in ("linux", "windows"):
        job = workflow["jobs"][f"test-runner-{platform}"]
        detector = next(step for step in job["steps"] if step.get("id") == "runner-contracts")
        tests = next(step for step in job["steps"] if step.get("name") == "Test runner admission and isolation")
        assert tests["run"].split().count("tools/test_environment_admission.py") == 1
        assert tests["if"] == "steps.runner-contracts.outputs.run == 'true'"
        assert tests["env"]["PYTEST_ADDOPTS"] == (
            """${{ (github.event_name != 'workflow_dispatch' || inputs.promotion_run != '') && '-m "not runner_exhaustive"' || '' }}"""
        )
        assert '"$EVENT_NAME" = "workflow_dispatch"' in detector["run"]
        assert "run=true" in detector["run"] and "run=false" in detector["run"]
        assert "github.event.pull_request.head.sha" in detector["env"]["HEAD_SHA"]
        pattern = re.search(r"'(\^\(.*)'", detector["run"]).group(1)
        for path in (
            "tools/run-plugin-tests.py", "tools/test_environment_admission.py",
            "tools/_admission_protocol.py", "tools/conftest.py",
            "libs/single-instance-lease/src/single_instance_lease/lease.py",
            ".github/workflows/ci.yml",
            ".devcontainer/test-isolation/devcontainer.json",
            ".devcontainer/test-isolation/Dockerfile",
        ):
            assert re.search(pattern, path)
        assert not re.search(pattern, "plugins/voice-announcer/README.md")
