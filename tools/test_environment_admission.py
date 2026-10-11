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
    return subprocess.run(
        child_command(root, wait), timeout=10, check=False,
        stdin=subprocess.DEVNULL, capture_output=True, **launch_options(),
    ).returncode


@pytest.mark.parametrize("other", ["same", "other-plugin", "other-checkout", "dotdot", "alias", "case"])
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
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_"), TOOLS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    previous_path = sys.path.copy()
    sys.path.insert(0, str(TOOLS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = previous_path
    return module


@pytest.mark.parametrize("mode", [[], ["--guards"], ["--collect-only"], ["--prepare-only"]])
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


@pytest.mark.parametrize("failure", [RuntimeError("setup failed"), SystemExit(9), KeyboardInterrupt()])
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
    lease = admission.acquire(0, environment)
    try:
        assert runner.main(["alpha"]) == 3
    finally:
        lease.release()
    assert str(environment) in capsys.readouterr().err


@pytest.mark.parametrize("failure", [None, "prepare", "use"])
def test_standalone_managed_environment_protects_prepare_and_use(cache, monkeypatch, failure):
    runner = load_runner("run-standalone-tests.py")
    monkeypatch.setattr(runner, "REPO", cache)
    (cache / "agent-index-service" / "tests").mkdir(parents=True)
    environment = cache / ".test-venvs" / sys.platform / "agent-index-service"
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
    assert runner.main(["agent-index-service", "--prepare"]) == (1 if failure else 0)
    assert events == (["prepare"] if failure == "prepare" else ["prepare", "use"])
    assert probe(environment) == 0


def test_custom_interpreter_keys_real_prefix_not_executable_parent(cache, monkeypatch):
    runner = load_runner("run-standalone-tests.py")
    environment = cache / "custom-environment"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(environment)],
        check=True, timeout=30, capture_output=True, **launch_options(),
    )
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert runner.interpreter_environment(python) == environment.resolve()
    monkeypatch.setattr(runner, "REPO", cache)
    (cache / "agent-index-service" / "tests").mkdir(parents=True)
    seen = []

    def run(command, **kwargs):
        assert Path(command[0]) == python
        assert probe(environment) == 3
        seen.append(True)
        return 0

    monkeypatch.setattr(runner, "run_contained", run)
    assert runner.main(["agent-index-service", "--python", str(python)]) == 0
    assert seen == [True]
    assert probe(environment) == 0
