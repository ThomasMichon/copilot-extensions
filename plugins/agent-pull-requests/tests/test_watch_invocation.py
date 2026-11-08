"""Physical slot checks execute in disposable, non-editable native venvs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_pull_requests import __version__, _installation_context as api
from agent_pull_requests import watch_invocation
from agent_pull_requests.watch_install import _manifest
from test_watch_installation import _installation, _policy
from test_watch_runtime import _complete

_ROOT = Path(__file__).resolve().parents[3]
_PROBE = """
import json, os, sys
from pathlib import Path
from agent_pull_requests.watch_installation import WatchInstallation
from agent_pull_requests.watch_invocation import capture_running
from agent_pull_requests.watch_runtime import WatchInstance
from zdd.diagnostics import process_start_time
installation = WatchInstallation.capture(
    receipt=Path(sys.argv[1]), plugin_root=Path(sys.argv[2]),
    payload_root=Path(sys.argv[3]), durable_home=Path(sys.argv[4]),
    os_profile=Path(sys.argv[5]), environment={},
)
runtime = capture_running(installation, environment={})
print(json.dumps({
    "runtime": runtime.evidence(), "pid": os.getpid(),
    "birth": process_start_time(os.getpid()),
    "prefix": sys.prefix, "executable": sys.executable,
}))
"""


@pytest.fixture
def installed(tmp_path, monkeypatch):
    installation = _installation(tmp_path)
    failures = []
    fail = api._fail

    def record_refusal(message):
        failures.append(message)
        fail(message)

    # Canonical mode resolution sanitizes activation errors. Preserve their
    # test-owned cause without suppressing or retrying any failed validation.
    with monkeypatch.context() as trace:
        trace.setattr(api, "_fail", record_refusal)
        try:
            runtime = _complete(installation, version=__version__)
        except PermissionError as error:
            if failures:
                raise AssertionError(f"fixture canonical refusal: {failures}") from error
            raise
    return _install_slot(installation, runtime)


def _install_slot(installation, runtime):
    venv.EnvBuilder(with_pip=False).create(runtime.runtime_root)
    python = runtime.runtime_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    libraries = subprocess.run(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True, text=True, check=True, timeout=15, **no_window_kwargs(),
    )
    destination = Path(libraries.stdout.strip())
    sources = [
        _ROOT / "plugins" / "agent-pull-requests" / "src" / "agent_pull_requests",
        *(
            _ROOT / "libs" / library / "src" / package for library, package in (
                ("zdd", "zdd"), ("single-instance-lease", "single_instance_lease"),
                ("work-coalescing-singleton", "work_coalescing_singleton"),
                ("agent-procutil", "agent_procutil"),
            )
        ),
    ]
    for source in sources:
        shutil.copytree(source, destination / source.name, ignore=shutil.ignore_patterns("__pycache__"))
    return installation, runtime, python, destination


def _probe(installed, *, script=_PROBE, overrides=None):
    installation, _, python, _ = installed
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "COPILOT_EXTENSIONS_CONTEXT"}
    }
    environment.update(overrides or {})
    return subprocess.run(
        [str(python), "-I", "-c", script, *(
            str(getattr(installation, field)) for field in (
                "receipt", "plugin_root", "payload_root", "durable_home", "os_profile",
            )
        )],
        env=environment, capture_output=True, text=True, check=False, timeout=60,
        **no_window_kwargs(),
    )


@pytest.mark.timeout(90)
def test_actual_running_completed_slot_and_native_birth(installed):
    result = _probe(installed)
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert value["runtime"] == installed[1].evidence()
    assert Path(value["prefix"]) == installed[1].runtime_root
    assert Path(value["executable"]).parent == installed[2].parent
    if sys.platform == "win32" or sys.platform.startswith("linux"):
        assert value["birth"].isdecimal()
    assert not installed[0].state_root.exists()
    assert not installed[0].run_root.exists()


@pytest.mark.timeout(90)
def test_matching_receipt_cannot_attest_base_interpreter(installed):
    result = _probe(installed, script=_PROBE.replace(
        "runtime = capture_running", "sys.prefix = sys.base_prefix\nruntime = capture_running",
    ))
    assert result.returncode != 0
    assert "interpreter is not" in result.stderr


@pytest.mark.timeout(90)
def test_running_slot_link_refuses_before_reading_ownership(installed):
    _, runtime, python, _ = installed
    alias = runtime.runtime_root.with_name("linked")
    if os.name == "nt":
        subprocess.run(
            [os.environ["ComSpec"], "/c", "mklink", "/J", str(alias), str(runtime.runtime_root)],
            capture_output=True, check=True, timeout=10, **no_window_kwargs(),
        )
    else:
        alias.symlink_to(runtime.runtime_root, target_is_directory=True)
    alias_python = alias / python.relative_to(runtime.runtime_root)
    result = _probe(installed, script=_PROBE.replace(
        "runtime = capture_running",
        f"sys.prefix = {str(alias)!r}\nsys.executable = {str(alias_python)!r}\n"
        "runtime = capture_running",
    ))
    assert result.returncode != 0
    assert "interpreter is not" in result.stderr


@pytest.mark.timeout(90)
@pytest.mark.parametrize("root", ["state", "run"])
def test_fresh_governance_cannot_rebind_running_owner_roots(installed, root):
    result = _probe(installed, script=_PROBE.replace(
        "print(json.dumps({",
        f"""
from agent_pull_requests.watch_invocation import WatchInvocation
receipt = Path(sys.argv[1])
document = json.loads(receipt.read_text(encoding="utf-8"))
document["roots"][{root!r}] = "foreign-{root}"
receipt.write_text(json.dumps(document), encoding="utf-8")
WatchInvocation(installation, runtime, str(receipt)).fresh_installation()
print(json.dumps({{
""",
    ))
    assert result.returncode != 0
    assert "state or run root changed" in result.stderr


@pytest.mark.timeout(90)
@pytest.mark.skipif(os.name != "nt", reason="OS-profile factory fixture supplies USERPROFILE")
def test_actual_cli_context_factory_binds_receipt_roots_and_same_cell_prefix(installed):
    installation = installed[0]
    peer_boundary = _ROOT / "libs" / "peer-launch" / "peer_launch.py"
    script = _PROBE.replace(
        "runtime = capture_running(installation, environment={})",
        f"""
from agent_pull_requests.watch_invocation import WatchInvocation
invocation = WatchInvocation.capture()
runtime = invocation.runtime
instance = invocation.instance()
assert instance.pid == os.getpid()
import importlib.util
spec = importlib.util.spec_from_file_location("fixture_peer_boundary", {str(peer_boundary)!r})
boundary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundary)
prefix = invocation.peer_prefix(boundary)
assert prefix[-4:] == [
    "agent-pull-requests", str(installation.plugin_root),
    str(installation.receipt), "agent-worktrees",
]
assert prefix[0] == sys.executable
assert prefix[1:5] == ["-B", "-I", "-X", "utf8"]
""",
    )
    result = _probe(installed, script=script, overrides={
        "COPILOT_EXTENSIONS_CONTEXT": str(installation.receipt),
        "AGENT_RT_ROOT": str(installation.plugin_root),
        "COPILOT_PLUGIN_ROOT": str(installation.payload_root),
        "USERPROFILE": str(installation.os_profile),
    })
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["runtime"] == installed[1].evidence()
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()


@pytest.mark.timeout(90)
@pytest.mark.skipif(os.name != "nt", reason="OS-profile fixture; native POSIX peer coverage is separate")
@pytest.mark.parametrize("damage", [None, "current", "manifest"])
def test_actual_module_entry_requires_coherent_selected_runtime(installed, damage):
    installation, runtime, _, _ = installed
    root = installation.plugin_root
    for marker in (api.CURRENT_VERSION_FILE, api.LAST_KNOWN_GOOD_FILE):
        (root / marker).write_text(runtime.runtime_version + "\n", encoding="utf-8")
    manifest = root / "deploy-manifest.json"
    manifest.write_bytes(_manifest(
        watch_invocation.WatchInvocation(installation, runtime, str(installation.receipt)),
    ))
    if damage == "current":
        (root / api.CURRENT_VERSION_FILE).unlink()
    elif damage == "manifest":
        document = json.loads(manifest.read_text("utf-8"))
        document["source"]["content_hash"] = "0" * 64
        manifest.write_text(json.dumps(document), encoding="utf-8")
    script = _PROBE[:_PROBE.index("print(json.dumps({")] + """
from agent_pull_requests import __main__ as cli
raise SystemExit(cli.main(["version"]))
"""
    result = _probe(installed, script=script, overrides={
        "COPILOT_EXTENSIONS_CONTEXT": str(installation.receipt),
        "AGENT_RT_ROOT": str(root), "COPILOT_PLUGIN_ROOT": str(installation.payload_root),
        "USERPROFILE": str(installation.os_profile),
    })
    if damage is None:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == f"agent-pull-requests {runtime.runtime_version}"
    else:
        assert result.returncode != 0, result.stdout
        assert not result.stdout
        assert (
            "markers are incomplete" if damage == "current" else "manifest differs"
        ) in result.stderr
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()


@pytest.mark.timeout(90)
@pytest.mark.parametrize("package", ["agent_pull_requests", "zdd", "single_instance_lease"])
def test_completed_slot_refuses_loaded_modules_outside_venv(installed, tmp_path, package):
    external = tmp_path / "external"
    shutil.copytree(installed[3] / package, external / package)
    result = _probe(
        installed, script=f"import sys\nsys.path.insert(0, {str(external)!r})\n" + _PROBE,
    )
    assert result.returncode != 0
    assert "modules escape" in result.stderr


@pytest.mark.timeout(90)
@pytest.mark.parametrize("change", ["completion", "version", "governance"])
def test_running_slot_refuses_uncompleted_or_changed_build(installed, change):
    installation, runtime, _, library = installed
    if change == "completion":
        (runtime.runtime_root / watch_invocation.api.RUNTIME_SLOT_COMPLETION_FILE).unlink()
    elif change == "version":
        (library / "agent_pull_requests" / "__init__.py").write_text(
            '__version__ = "foreign"\n', encoding="utf-8",
        )
    else:
        _policy(installation.os_profile, False)
    result = _probe(installed)
    assert result.returncode != 0
    assert not installation.state_root.exists()
    assert not installation.run_root.exists()


def test_no_explicit_context_preserves_legacy_invocation(monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(
        watch_invocation, "capture_running", lambda *_: pytest.fail("not a cell invocation"),
    )
    assert watch_invocation.WatchInvocation.capture() is None


@pytest.mark.parametrize("context", ["unavailable", '{"installReceipt":"unavailable"}'])
def test_explicit_context_without_actual_roots_never_falls_back(monkeypatch, context):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", context)
    monkeypatch.delenv("AGENT_RT_ROOT", raising=False)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    with pytest.raises(PermissionError, match="canonical invocation roots"):
        watch_invocation.WatchInvocation.capture()
