"""Prepared POSIX cue entry: real managed selection and one owned deadline."""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import time

from agent_procutil import no_window_kwargs
from agent_procutil._linux_pidfd import open_pidfd, send_pidfd_signal
import pytest

from cue_fixture import PLUGIN, managed_cue_environment, prepare_cue_assets

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX hook execution")


def _prepare(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    payload = tmp_path / "payload"
    prepare_cue_assets(payload)
    for name in ("write_session_guidance.py", "write-session-guidance.sh"):
        shutil.copy2(PLUGIN / "scripts" / name, payload / "scripts")
    shutil.copytree(PLUGIN / "src", payload / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(PLUGIN / "plugin.json", payload / "plugin.json")
    environment = {
        **managed_cue_environment(), "HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
        "COPILOT_PLUGIN_ROOT": str(payload),
        "BUDGET_GUIDANCE_CONFIG": str(tmp_path / "missing-budget.json"),
    }
    return payload, environment


def _command(payload: Path) -> list[str]:
    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("Bash unavailable")
    return [shell, str(payload / "scripts/write-session-guidance.sh")]


def _selection_marker(payload: Path) -> None:
    provider = payload / "scripts/bootstrap-uv.sh"
    provider.write_text(
        provider.read_text() +
        "resolve_uv_bootstrap_python() {\n"
        '  printf started > "$CUE_SELECTION_MARKER"\n'
        '  printf "%s\\n" "$COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"\n'
        "}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize("bom", ["", "\ufeff"])
def test_posix_manifest_handoff_ignores_path_python_and_foreign_environment(
    tmp_path: Path, bom: str, record_property: Callable[[str, object], None],
) -> None:
    payload, environment = _prepare(tmp_path)
    poison = tmp_path / "foreign-venv/bin"
    poison.mkdir(parents=True)
    (poison.parent / "pyvenv.cfg").write_text("home = invalid-foreign-python\n")
    for name in ("python", "python3", "py"):
        program = poison / name
        program.write_text("#!/bin/sh\nexit 91\n", encoding="ascii")
        program.chmod(0o700)
    seed = environment.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON")
    seed_alias = tmp_path / "prepared \u00e9'\"\\python"
    seed_alias.symlink_to(seed)
    installed = tmp_path / ".budget-guidance"
    installed.mkdir()
    (installed / "deploy-manifest.json").write_text(bom + json.dumps({
        "service": "budget-guidance", "bootstrap_python": str(seed_alias),
    }))
    environment.update(
        PATH=str(poison) + os.pathsep + os.environ.get("PATH", ""),
        VIRTUAL_ENV=str(poison.parent), PYTHONHOME="invalid-home", PYTHONPATH="invalid-path",
        UV_PYTHON="foreign", UV_NO_MANAGED_PYTHON="1",
        UV_WORKING_DIR=str(tmp_path / "not-a-directory"),
        UV_CACHE_DIR=str(tmp_path / "private-cache"),
    )
    scripts = payload / "scripts"
    (scripts / "write_session_guidance.py").rename(scripts / "actual_cue.py")
    identity = tmp_path / "writer.json"
    (scripts / "write_session_guidance.py").write_text(
        "import json,os,runpy,sys\nfrom pathlib import Path\n"
        "assert not any(k in os.environ for k in "
        "('VIRTUAL_ENV','PYTHONHOME','PYTHONPATH','UV_PYTHON','UV_NO_MANAGED_PYTHON','UV_WORKING_DIR'))\n"
        f"Path({str(identity)!r}).write_text(json.dumps({{'version':list(sys.version_info[:3])}}))\n"
        f"runpy.run_path({str(scripts / 'actual_cue.py')!r},run_name='__main__')\n",
        encoding="utf-8",
    )
    timings: dict[str, float] = {}
    for source in ("startup", "resume"):
        started = time.monotonic()
        result = subprocess.run(
            _command(payload), input=json.dumps({
                "sessionId": "managed-posix", "cwd": str(tmp_path), "source": source,
            }), text=True, capture_output=True, env=environment, timeout=9, **no_window_kwargs(),
        )
        timings[source] = time.monotonic() - started
        record_property(source + "_seconds", timings[source])
        assert result.returncode == 0 and result.stdout == "{}", result.stderr
        assert result.stderr == ""
        target = tmp_path / ".copilot/session-state/managed-posix/instructions/budget-guidance/session-guidance.instructions.md"
        assert json.loads(target.read_text().split("\n", 1)[1])["error"] == "configuration not found"
        declared = json.loads((PLUGIN / "payload-invocation.json").read_bytes())["bootstrapPython"]
        assert json.loads(identity.read_text())["version"] == list(map(int, declared.split(".")))
    print("POSIX_CUE_TIMINGS=" + json.dumps({"deployment_bom": bool(bom), **timings}))


@pytest.mark.parametrize("missing", [
    "uv", "managed-python", "request", "bootstrap",
    "nul-bootstrap", "newline-bootstrap", "newline-service",
    "oversized-manifest", "linked-manifest",
])
def test_posix_missing_prerequisite_never_executes_the_writer(tmp_path: Path, missing: str) -> None:
    payload, environment = _prepare(tmp_path)
    marker = tmp_path / "writer"
    (payload / "scripts/write_session_guidance.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
    )
    command = _command(payload)
    if missing == "uv":
        empty = tmp_path / "empty-path"
        empty.mkdir()
        environment["PATH"] = str(empty)
    elif missing == "managed-python":
        environment["UV_PYTHON_INSTALL_DIR"] = str(tmp_path / "unprepared-managed-store")
    elif missing == "request":
        (payload / "scripts/bootstrap-python-version.txt").unlink()
    else:
        seed = environment.pop("COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON")
        if missing != "bootstrap":
            installed = tmp_path / ".budget-guidance"
            installed.mkdir()
            manifest = installed / "deploy-manifest.json"
            body = json.dumps({
                "service": "budget-guidance\n" if missing == "newline-service" else "budget-guidance",
                "bootstrap_python": (
                    seed + "\x00" if missing == "nul-bootstrap" else
                    seed + "\n" if missing == "newline-bootstrap" else seed
                ),
            })
            if missing == "linked-manifest":
                target = tmp_path / "linked-selection.json"
                target.write_text(body)
                manifest.symlink_to(target)
            else:
                manifest.write_text((" " * 65537 if missing == "oversized-manifest" else "") + body)
    result = subprocess.run(
        command, input="{}", text=True, capture_output=True, env=environment,
        timeout=9, **no_window_kwargs(),
    )
    assert result.returncode == 0 and result.stdout == "{}"
    assert result.stderr.strip() == (
        "budget-guidance: managed cue prerequisites are unavailable; "
        "run the owning scripts/install.sh install"
    )
    assert not marker.exists()
    assert not (tmp_path / "unprepared-managed-store").exists()


def test_posix_stalled_input_expires_before_any_selection(tmp_path: Path) -> None:
    payload, environment = _prepare(tmp_path)
    marker = tmp_path / "selection"
    _selection_marker(payload)
    environment["CUE_SELECTION_MARKER"] = str(marker)
    started = time.monotonic()
    process = subprocess.Popen(
        _command(payload), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=environment, start_new_session=True, **no_window_kwargs(),
    )
    try:
        assert process.stdin is not None
        process.stdin.write(b"{")
        process.stdin.flush()
        assert process.wait(timeout=8) == 0
        assert 5 <= time.monotonic() - started < 8
        assert process.stdout is not None and process.stdout.read() == b"{}"
        assert process.stderr is not None
        assert process.stderr.read().strip() == b"budget-guidance: session cue could not be written"
        assert not marker.exists()
    finally:
        if process.returncode is None:
            getattr(os, "killpg")(process.pid, getattr(signal, "SIGKILL"))
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


@pytest.mark.parametrize("length", [65536, 65537])
def test_posix_input_limit_and_stalled_writer_share_the_deadline(tmp_path: Path, length: int) -> None:
    payload, environment = _prepare(tmp_path)
    marker = tmp_path / "selection"
    _selection_marker(payload)
    environment["CUE_SELECTION_MARKER"] = str(marker)
    (payload / "scripts/write_session_guidance.py").write_text("import time\ntime.sleep(20)\n")
    started = time.monotonic()
    result = subprocess.run(
        _command(payload), input=b"x" * length, capture_output=True,
        env=environment, timeout=9, **no_window_kwargs(),
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0 and result.stdout == b"{}"
    assert result.stderr.strip() == b"budget-guidance: session cue could not be written"
    assert marker.exists() == (length == 65536)
    assert elapsed < 9
    if length == 65536:
        assert elapsed >= 5


def test_posix_selection_and_writer_do_not_receive_separate_budgets(tmp_path: Path) -> None:
    payload, environment = _prepare(tmp_path)
    provider = payload / "scripts/bootstrap-uv.sh"
    provider.write_text(
        provider.read_text() +
        "resolve_uv_bootstrap_python() {\n"
        "  sleep 3\n"
        '  printf "%s\\n" "$COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"\n'
        "}\n",
    )
    started_marker, completed_marker = tmp_path / "started", tmp_path / "completed"
    (payload / "scripts/write_session_guidance.py").write_text(
        "import time\nfrom pathlib import Path\n"
        f"Path({str(started_marker)!r}).touch()\ntime.sleep(3)\n"
        f"Path({str(completed_marker)!r}).touch()\n",
    )
    started = time.monotonic()
    result = subprocess.run(
        _command(payload), input=b"{}", capture_output=True, env=environment,
        timeout=9, **no_window_kwargs(),
    )
    assert 5 <= time.monotonic() - started < 9
    assert result.returncode == 0 and result.stdout == b"{}"
    assert result.stderr.strip() == b"budget-guidance: session cue could not be written"
    assert started_marker.exists() and not completed_marker.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux pidfd cold-query witness")
def test_posix_real_cold_query_exits_before_fixture_cleanup(tmp_path: Path) -> None:
    payload, environment = _prepare(tmp_path)
    uv = shutil.which("uv")
    assert uv is not None
    seed = Path(environment["COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON"]).resolve()
    store = Path(environment["UV_PYTHON_INSTALL_DIR"]).resolve()
    relative = seed.relative_to(store)
    source = store / relative.parts[0]
    managed = tmp_path / "private-managed \u00e9'quote"
    private = managed / source.name
    shutil.copytree(source, private)
    query = tmp_path / "query.json"
    version = json.loads((PLUGIN / "payload-invocation.json").read_bytes())["bootstrapPython"]
    packages = private / "lib" / ("python" + ".".join(version.split(".")[:2])) / "site-packages"
    packages.mkdir(parents=True, exist_ok=True)
    (packages / "sitecustomize.py").write_text(
        "import json,os,time\nfrom pathlib import Path\n"
        "parts=Path('/proc/self/stat').read_text().rpartition(') ')[2].split()\n"
        f"target=Path({str(query)!r})\ntemporary=target.with_suffix('.tmp')\n"
        "temporary.write_text(json.dumps({'pid':os.getpid(),"
        "'parent':os.getppid(),'birth':parts[19]}))\n"
        "temporary.replace(target)\ntime.sleep(20)\n",
    )
    environment.update(
        UV_PYTHON_INSTALL_DIR=str(managed), UV_CACHE_DIR=str(tmp_path / "cold-cache"),
        COPILOT_EXTENSIONS_BOOTSTRAP_PYTHON=str(managed / relative),
    )
    started = time.monotonic()
    process = subprocess.Popen(
        _command(payload), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=environment, start_new_session=True, **no_window_kwargs(),
    )
    pinned: list[int] = []
    try:
        assert process.stdin is not None
        process.stdin.write(b"{}")
        process.stdin.close()
        process.stdin = None
        while not query.exists() and time.monotonic() - started < 4 and process.poll() is None:
            time.sleep(0.01)
        assert query.exists(), "a real cold uv metadata query was not observed"
        observed = json.loads(query.read_text())
        for pid, image in ((observed["pid"], managed / relative), (observed["parent"], Path(uv))):
            descriptor = open_pidfd(pid)
            pinned.append(descriptor)
            assert Path(f"/proc/{pid}/exe").resolve() == image.resolve()
        actual = Path(f"/proc/{observed['pid']}/stat").read_text().rpartition(") ")[2].split()
        assert actual[19] == observed["birth"]
        stdout, stderr = process.communicate(timeout=max(1, started + 9 - time.monotonic()))
        assert process.returncode == 0 and stdout == b"{}"
        assert stderr.strip() == b"budget-guidance: session cue could not be written"
        assert all(select.select([descriptor], [], [], 0)[0] for descriptor in pinned), (
            "uv/query survived the cue's own completion", observed,
        )
    finally:
        cleanup_deadline = time.monotonic() + 5
        if process.returncode is None:
            getattr(os, "killpg")(process.pid, getattr(signal, "SIGKILL"))
            process.wait(timeout=max(0.01, cleanup_deadline - time.monotonic()))
        for descriptor in pinned:
            if not select.select([descriptor], [], [], 0)[0]:
                send_pidfd_signal(descriptor, getattr(signal, "SIGKILL"))
            remaining = max(0, cleanup_deadline - time.monotonic())
            assert select.select([descriptor], [], [], remaining)[0], "owned fixture process did not settle"
            os.close(descriptor)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()
