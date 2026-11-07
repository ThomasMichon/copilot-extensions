"""Real retained dispatch in disposable native venvs, without installation."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv

import pytest
from agent_procutil import no_window_kwargs
from update_driver import InvalidRequestError, runtime_command, runtime_script_command


@pytest.fixture
def retained_runtime(area, monkeypatch):
    libraries = Path(__file__).resolve().parents[2]
    root = area / "runtime"
    slot = root / "versions" / "1.2.3"
    venv.EnvBuilder(with_pip=False).create(slot)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    locations = subprocess.run(
        [str(python), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"],
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert locations.returncode == 0, locations.stderr
    package = Path(locations.stdout.strip())
    code = (
        "import json,sys\nfrom pathlib import Path\n"
        "Path(sys.argv[1]).write_text('executed',encoding='utf-8')\n"
        "print(json.dumps({'argv':sys.argv,'prefix':sys.prefix}))\n"
        "raise SystemExit(int(sys.argv[2]))\n"
    )
    (package / "retained_fixture.py").write_text(code, encoding="utf-8")
    scripts = area / "external scripts \u03b1"
    scripts.mkdir()
    script = scripts / "payload \u00fc.py"
    script.write_text(code, encoding="utf-8")
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    for source in (libraries / "versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copyfile(source, bootstrap / source.name)
    shutil.copyfile(
        libraries / "installer-engine" / "runtime_launcher.py", bootstrap / "runtime_launcher.py",
    )
    spec = importlib.util.spec_from_file_location("_retained_launch_fixture", bootstrap / "versioned_runtime.py")
    runtime = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, runtime)
    spec.loader.exec_module(runtime)
    runtime.mark_complete(root, slot.name, payload_hash="a" * 64)
    return slot, python, script, package / "retained_fixture.py"


def command(monkeypatch, retained_runtime, entry, args, *, module=False):
    slot, python, _script, _module = retained_runtime
    with monkeypatch.context() as selected:
        selected.setattr(sys, "prefix", str(slot))
        selected.setattr(sys, "executable", str(python))
        return (runtime_command if module else runtime_script_command)(entry, args)


def invoke(argv, environment, cwd):
    return subprocess.run(
        argv, cwd=cwd, env={**os.environ, **environment}, capture_output=True,
        text=True, encoding="utf-8", timeout=30, **no_window_kwargs(),
    )


@pytest.mark.parametrize("module", [False, True])
def test_retained_launch_preserves_native_dispatch_argv_and_exit(
    retained_runtime, area, monkeypatch, module,
):
    slot, _python, script, module_file = retained_runtime
    marker = area / "executed"
    args = [str(marker), "17", "", "two words", "\u03b1 \u00fc", "100% ready!", "--literal"]
    argv, env = command(
        monkeypatch, retained_runtime, "retained_fixture" if module else script,
        args, module=module,
    )
    result = invoke(argv, env, script.parent)
    assert result.returncode == 17, result.stderr
    assert marker.read_text(encoding="utf-8") == "executed"
    assert json.loads(result.stdout) == {
        "argv": [str(module_file if module else script), *args], "prefix": str(slot),
    }


@pytest.mark.parametrize("entry", ["relative", "missing", "directory"])
def test_retained_launch_refuses_invalid_script_before_payload(
    retained_runtime, area, monkeypatch, entry,
):
    _slot, _python, script, _module = retained_runtime
    marker = area / "executed"
    argv, env = command(monkeypatch, retained_runtime, script, [str(marker), "0"])
    argv = list(argv)
    argv[argv.index(str(script))] = {
        "relative": script.name,
        "missing": str(script.with_name("absent.py")),
        "directory": str(script.parent),
    }[entry]
    result = invoke(argv, env, script.parent)
    assert result.returncode != 0
    assert "absolute script file" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("refusal", ["incomplete", "foreign-interpreter"])
def test_retained_launch_admission_precedes_external_payload(
    retained_runtime, area, monkeypatch, refusal,
):
    slot, _python, script, _module = retained_runtime
    marker = area / "executed"
    argv, env = command(monkeypatch, retained_runtime, script, [str(marker), "0"])
    argv, env = list(argv), dict(env)
    if refusal == "incomplete":
        (slot / ".install-complete.json").unlink()
        expected = "requires attributable completion"
    else:
        argv[0] = getattr(sys, "_base_executable", sys.executable)
        env.pop("__PYVENV_LAUNCHER__", None)
        expected = "interpreter resolved outside its slot"
    result = invoke(argv, env, script.parent)
    assert result.returncode != 0
    assert expected in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize("path", ["relative.py", "", "line\nbreak.py", b"bytes.py", None])
def test_runtime_script_command_refuses_invalid_path(path):
    with pytest.raises(InvalidRequestError, match="absolute path"):
        runtime_script_command(path)


@pytest.mark.parametrize("kind", ["directory", "missing"])
def test_runtime_script_command_source_refuses_nonfile_target(area, monkeypatch, kind):
    marker = area / "executed"
    target = area / kind
    if kind == "directory":
        target.mkdir()
        (target / "__main__.py").write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(sys, "prefix", str(area))
    with pytest.raises(InvalidRequestError, match="absolute script file"):
        argv, environment = runtime_script_command(target)
        invoke(argv, environment, area)
    assert not marker.exists()
