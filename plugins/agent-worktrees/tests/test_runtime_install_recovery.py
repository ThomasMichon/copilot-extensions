"""Rollback may restore only an independently revalidated predecessor."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "libs" / "payload-invocation" / "tests"))
from test_command_binding import _publish, _resolve, _write  # noqa: E402
from test_command_binding import binding_fixture as binding_fixture  # noqa: E402


def test_guarded_lifecycle_cli_loads_in_isolated_python():
    script = ROOT / "plugins" / "agent-worktrees" / "scripts" / "runtime_install_recovery.py"
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(script), "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert "--lifecycle-timeout" in result.stdout


def test_guarded_lifecycle_cli_refuses_a_foreign_payload(tmp_path):
    script = ROOT / "plugins" / "agent-worktrees" / "scripts" / "runtime_install_recovery.py"
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(script),
            "--payload-root",
            str(tmp_path / "foreign"),
            "--legacy-root",
            str(tmp_path / "runtime"),
            "--project-root",
            str(tmp_path),
            "--source-descriptor",
            "{}",
            "--lifecycle-timeout",
            "60",
            "--",
            sys.executable,
            "-c",
            "pass",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode != 0 and "does not own" in result.stderr
    assert not (tmp_path / "runtime").exists()


def test_guarded_lifecycle_cli_binds_the_installed_owner(recovery_fixture, monkeypatch):
    fixture, module, _runtime, _state, _previous = recovery_fixture
    payload = ROOT / "plugins" / "agent-worktrees"
    descriptor = {"kind": "github", "canonical": "github:example/repo", "ref": ""}
    binding = SimpleNamespace(
        _load_primitive=lambda path: fixture["primitive"] if path == payload else None
    )
    monkeypatch.setitem(sys.modules, "command_binding", binding)
    observed = []
    monkeypatch.setattr(
        module, "run_guarded", lambda *args, **kwargs: observed.append((args, kwargs)) or 7
    )
    result = module.main(
        [
            "--payload-root",
            str(payload),
            "--legacy-root",
            str(fixture["legacy"]),
            "--project-root",
            str(fixture["home"]),
            "--source-descriptor",
            json.dumps(descriptor),
            "--lifecycle-timeout",
            "60",
            "--",
            sys.executable,
            "-c",
            "pass",
        ]
    )
    assert result == 7
    assert observed[0][0] == (binding, fixture["primitive"], [sys.executable, "-c", "pass"])
    assert observed[0][1]["payload_root"] == payload
    assert observed[0][1]["mode_arguments"] == {"source_descriptor": descriptor}
    assert observed[0][1]["timeout"] == 60


@pytest.fixture
def recovery_fixture(binding_fixture, monkeypatch):
    fixture = binding_fixture
    scripts = ROOT / "plugins" / "agent-worktrees" / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "_worktrees_rollback_test",
        scripts / "runtime_install_recovery.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = fixture["legacy"]
    slot = root / "versions" / "1.0.0"
    python = slot / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    python.parent.mkdir(parents=True)
    python.touch()
    (root / "current-version").write_text("1.0.0\n")
    (root / "last-known-good").write_text("1.0.0\n")
    _publish(fixture)
    for path in (root / "bin", root / "scripts", fixture["home"] / ".local" / "bin"):
        path.mkdir(parents=True)
        (path / "old-command").write_bytes(b"old")
    (root / "bin" / "resolve-runtime.ps1").write_bytes(b"resolver")
    (fixture["home"] / ".local" / "bin" / "agent-worktrees.ps1").write_bytes(b"wrapper")
    (fixture["home"] / ".local" / "bin" / "agent-worktrees.cmd").write_bytes(b"wrapper")
    manifest_path = root / "deploy-manifest.json"
    previous = json.loads(manifest_path.read_text())
    previous.update(
        runtime_slot="1.0.0",
        runtime_version="1.0.0",
        venv=str(slot),
        payload_hash="a" * 64,
    )
    _write(manifest_path, previous)
    runtime = SimpleNamespace(
        current_version=lambda selected_root: (
            (selected_root / "current-version").read_text().strip()
        ),
        is_complete=lambda selected_root, version, **kwargs: (
            selected_root == root and version == "1.0.0" and kwargs == {"expect_hash": "a" * 64}
        ),
        slot_python=lambda selected_root, version: (
            python if (selected_root, version) == (root, "1.0.0") else None
        ),
        resolve_python=lambda _root: python,
    )
    state = {"healthy": True}
    monkeypatch.setattr(module, "_health", lambda *_args, **_kwargs: state["healthy"])
    return fixture, module, runtime, state, previous


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "selector",
        "helper",
        "new-helper",
        "monitor",
        "routing",
        "manifest",
        "health",
    ],
)
def test_only_the_unchanged_owned_predecessor_can_be_restored(recovery_fixture, change):
    fixture, module, runtime, state, previous = recovery_fixture
    binding, primitive, arguments = fixture["binding"], fixture["primitive"], fixture["arguments"]
    lifecycle = binding._load_lifecycle()
    root = fixture["legacy"]
    with lifecycle.legacy_lifecycle(
        binding._rules(), primitive, reserve=False, **arguments
    ) as lease:
        proof = module._capture(
            binding, primitive, arguments, lease, runtime, time.monotonic() + 30
        )
        assert proof is not None
        lease.reserve()
        pending = lifecycle.incumbent_manifest(binding._rules(), primitive, arguments)
        if change == "selector":
            (root / "current-version").write_text("another\n")
        elif change == "helper":
            (root / "bin" / "old-command").write_bytes(b"changed")
        elif change == "new-helper":
            (root / "bin" / "unexpected").write_bytes(b"new")
        elif change == "monitor":
            (root / "status-monitor.lock").write_text("uncertain")
        elif change == "routing":
            (root / "status-monitor-routing").mkdir()
            (root / "status-monitor-routing" / "breadcrumb").write_text("uncertain")
        elif change == "manifest":
            _write(root / "deploy-manifest.json", {**pending, "unexpected": True})
        elif change == "health":
            state["healthy"] = False
        restored = module._restore(proof, binding, primitive, arguments, lease, runtime)
        assert restored is (change == "none")
        if change == "none":
            result = json.loads((root / "deploy-manifest.json").read_text())
            assert result["commandBinding"] == previous["commandBinding"]
            assert result["commandLifecycle"]["state"] == "complete"
            assert _resolve(fixture)["status"] == "selected"
        else:
            assert (
                json.loads((root / "deploy-manifest.json").read_text())["commandLifecycle"][
                    "state"
                ]
                == "pending"
            )


@pytest.mark.parametrize("failure", ["write", "postcheck"])
def test_failed_rebinding_preserves_the_pending_reservation(
    recovery_fixture,
    monkeypatch,
    failure,
):
    fixture, module, runtime, _state, _previous = recovery_fixture
    binding, primitive, arguments = fixture["binding"], fixture["primitive"], fixture["arguments"]
    lifecycle = binding._load_lifecycle()
    root = fixture["legacy"]
    with lifecycle.legacy_lifecycle(
        binding._rules(), primitive, reserve=False, **arguments
    ) as lease:
        proof = module._capture(
            binding, primitive, arguments, lease, runtime, time.monotonic() + 30
        )
        lease.reserve()
        if failure == "write":
            actual_write = primitive._atomic_write_json

            def refuse(path, value, **options):
                if value.get("commandLifecycle", {}).get("state") == "complete":
                    raise OSError("fixture write failure")
                return actual_write(path, value, **options)

            monkeypatch.setattr(primitive, "_atomic_write_json", refuse)
        else:
            monkeypatch.setattr(
                binding, "resolve_command_binding", lambda *_args, **_kwargs: {"status": "blocked"}
            )
        assert not module._restore(proof, binding, primitive, arguments, lease, runtime)
        current = json.loads((root / "deploy-manifest.json").read_text())
        assert current["commandLifecycle"]["state"] == "pending"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows read custody denies writes")
def test_restoration_holds_command_bytes_until_manifest_commit(recovery_fixture, monkeypatch):
    fixture, module, runtime, _state, _previous = recovery_fixture
    binding, primitive, arguments = fixture["binding"], fixture["primitive"], fixture["arguments"]
    lifecycle = binding._load_lifecycle()
    root = fixture["legacy"]
    helper = root / "bin" / "old-command"
    actual_write = primitive._atomic_write_json
    observed = []

    def write(path, value, **options):
        if value.get("commandLifecycle", {}).get("state") == "complete":
            with pytest.raises(OSError):
                helper.write_bytes(b"racing writer")
            observed.append(True)
        return actual_write(path, value, **options)

    with lifecycle.legacy_lifecycle(
        binding._rules(), primitive, reserve=False, **arguments
    ) as lease:
        proof = module._capture(
            binding, primitive, arguments, lease, runtime, time.monotonic() + 30
        )
        lease.reserve()
        monkeypatch.setattr(primitive, "_atomic_write_json", write)
        assert module._restore(proof, binding, primitive, arguments, lease, runtime)
    assert observed == [True] and helper.read_bytes() == b"old"


@pytest.mark.parametrize("reason", ["monitor", "missing-wrapper", "unhealthy", "prior-pending"])
def test_unprovable_predecessor_never_starts_an_update(recovery_fixture, monkeypatch, reason):
    fixture, module, runtime, state, _previous = recovery_fixture
    binding, primitive, arguments = fixture["binding"], fixture["primitive"], fixture["arguments"]
    lifecycle = binding._load_lifecycle()
    root = fixture["legacy"]
    if reason == "monitor":
        (root / "status-monitor.lock").write_text("live or unknown")
    elif reason == "missing-wrapper":
        (fixture["home"] / ".local" / "bin" / "agent-worktrees.cmd").unlink()
    elif reason == "unhealthy":
        state["healthy"] = False
    else:
        with lifecycle.legacy_lifecycle(binding._rules(), primitive, **arguments):
            pass
    before = (root / "deploy-manifest.json").read_bytes()
    monkeypatch.setattr(module, "_supports_job", lambda: True)
    monkeypatch.setattr(module, "runtime", runtime)
    monkeypatch.setattr(
        lifecycle,
        "run_lifecycle",
        lambda *_args, **_kwargs: pytest.fail(
            "unproven predecessor reached the child",
        ),
    )
    with pytest.raises(ValueError, match="quiet, healthy selected predecessor"):
        module.run_guarded(binding, primitive, [sys.executable], timeout=10, **arguments)
    assert (root / "deploy-manifest.json").read_bytes() == before


@pytest.mark.parametrize("outcome", ["failed-settled", "uncertain", "success", "false-success"])
def test_recovery_runs_only_after_the_contained_child_settles(
    recovery_fixture, monkeypatch, outcome
):
    fixture, module, runtime, _state, _previous = recovery_fixture
    binding, primitive, arguments = fixture["binding"], fixture["primitive"], fixture["arguments"]
    lifecycle = binding._load_lifecycle()
    monkeypatch.setattr(module, "_supports_job", lambda: True)
    monkeypatch.setattr(module, "runtime", runtime)

    @contextmanager
    def retain(*_args, **_kwargs):
        yield

    runtime.retention_lease = retain
    calls = []

    def run(actual_binding, actual_primitive, command, **options):
        assert (
            actual_binding is not None
            and actual_primitive is primitive
            and command == [sys.executable]
        )
        assert 0 < options["timeout"] < 10 and options["managed_deployment"] is True
        calls.append("child")
        if outcome == "uncertain":
            raise RuntimeError("child tree did not settle")
        if outcome == "success":
            binding.write_legacy_manifest(
                primitive,
                {"schema_version": 3, "service": "agent-worktrees"},
                **arguments,
            )
            return 0
        if outcome == "false-success":
            return 0
        return 7

    monkeypatch.setattr(lifecycle, "run_lifecycle", run)
    if outcome == "uncertain":
        with pytest.raises(RuntimeError, match="did not settle"):
            module.run_guarded(binding, primitive, [sys.executable], timeout=10, **arguments)
        assert _resolve(fixture)["reason"] == "legacy-lifecycle-incomplete"
    else:
        result = module.run_guarded(binding, primitive, [sys.executable], timeout=10, **arguments)
        assert result == (0 if outcome == "success" else 1 if outcome == "false-success" else 7)
        if outcome == "false-success":
            assert _resolve(fixture)["reason"] == "legacy-lifecycle-incomplete"
        else:
            assert _resolve(fixture)["status"] == "selected"
    assert calls == ["child"]


def test_prelaunch_retention_failure_restores_without_starting_a_child(
    recovery_fixture, monkeypatch
):
    fixture, module, runtime, _state, _previous = recovery_fixture
    binding, primitive, arguments = fixture["binding"], fixture["primitive"], fixture["arguments"]
    lifecycle = binding._load_lifecycle()
    monkeypatch.setattr(module, "_supports_job", lambda: True)
    monkeypatch.setattr(module, "runtime", runtime)

    @contextmanager
    def refuse(*_args, **_kwargs):
        raise OSError("retention unavailable")
        yield

    runtime.retention_lease = refuse
    monkeypatch.setattr(
        lifecycle,
        "run_lifecycle",
        lambda *_args, **_kwargs: pytest.fail(
            "unretained predecessor reached the child",
        ),
    )
    with pytest.raises(OSError, match="retention unavailable"):
        module.run_guarded(binding, primitive, [sys.executable], timeout=10, **arguments)
    assert _resolve(fixture)["status"] == "selected"


@pytest.mark.parametrize("outcome", ["success", "failure"])
def test_guarded_lifecycle_preserves_fresh_install_behavior(binding_fixture, monkeypatch, outcome):
    fixture = binding_fixture
    binding, primitive, arguments = fixture["binding"], fixture["primitive"], fixture["arguments"]
    scripts = ROOT / "plugins" / "agent-worktrees" / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "_fresh_worktrees_recovery_test",
        scripts / "runtime_install_recovery.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_supports_job", lambda: True)
    (fixture["legacy"] / "retained-state.json").unlink()
    (fixture["legacy"] / "bin").mkdir()
    lifecycle = binding._load_lifecycle()

    def run(*_args, **_kwargs):
        if outcome == "success":
            binding.write_legacy_manifest(
                primitive,
                {"schema_version": 3, "service": "agent-worktrees"},
                **arguments,
            )
            return 0
        return 7

    monkeypatch.setattr(lifecycle, "run_lifecycle", run)
    result = module.run_guarded(binding, primitive, [sys.executable], timeout=10, **arguments)
    assert result == (0 if outcome == "success" else 7)
    assert _resolve(fixture)["status"] == ("selected" if outcome == "success" else "blocked")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job containment")
@pytest.mark.parametrize("outcome", ["unchanged", "changed", "descendant", "timeout"])
def test_real_contained_failure_restores_only_intact_command_state(
    recovery_fixture,
    monkeypatch,
    outcome,
):
    fixture, module, runtime, _state, _previous = recovery_fixture
    binding, primitive, arguments = fixture["binding"], fixture["primitive"], fixture["arguments"]
    monkeypatch.setattr(module, "runtime", runtime)

    @contextmanager
    def retain(*_args, **_kwargs):
        yield

    runtime.retention_lease = retain
    destination = fixture["payload"] / "libs" / "agent-procutil" / "src" / "agent_procutil"
    destination.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "libs" / "agent-procutil" / "src" / "agent_procutil", destination)
    helper = fixture["legacy"] / "bin" / "old-command"
    ready = fixture["legacy"] / "descendant-ready"
    if outcome == "changed":
        script = (
            "from pathlib import Path;import sys;"
            "Path(sys.argv[1]).write_bytes(b'changed');raise SystemExit(7)"
        )
    elif outcome == "descendant":
        child = (
            "import sys,time;from pathlib import Path;Path(sys.argv[2]).write_text('ready');"
            "time.sleep(5);Path(sys.argv[1]).write_bytes(b'late')"
        )
        script = (
            "import subprocess,sys,time\nfrom pathlib import Path\n"
            f"subprocess.Popen([sys.executable,'-c',{child!r},sys.argv[1],sys.argv[2]],"
            "creationflags=subprocess.CREATE_NO_WINDOW)\n"
            "deadline=time.monotonic()+5\n"
            "while not Path(sys.argv[2]).exists() and time.monotonic()<deadline: time.sleep(.02)\n"
            "assert Path(sys.argv[2]).exists()\nraise SystemExit(7)"
        )
    elif outcome == "timeout":
        script = "import time;time.sleep(30)"
    else:
        script = "raise SystemExit(7)"
    result = module.run_guarded(
        binding,
        primitive,
        [sys.executable, "-c", script, str(helper), str(ready)],
        timeout=10 if outcome == "timeout" else 15,
        **arguments,
    )
    assert result == (124 if outcome == "timeout" else 7)
    if outcome == "descendant":
        assert ready.read_text() == "ready"
    if outcome == "changed":
        assert _resolve(fixture)["reason"] == "legacy-lifecycle-incomplete"
        assert helper.read_bytes() == b"changed"
    else:
        assert _resolve(fixture)["status"] == "selected"
        assert helper.read_bytes() == b"old"
