"""Finite removal uses real canonical leases and preserves unknown/state bytes."""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
from pathlib import Path
import shutil
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "libs" / "installer-engine" / "installer_engine_uninstall.py"
SPEC = importlib.util.spec_from_file_location("finite_uninstall_test_subject", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
HOST = shutil.which("pwsh") if os.name == "nt" else shutil.which("bash")

@pytest.mark.parametrize("plugin", ("agent-bridge", "agent-dispatch", "agent-vault",
                                  "budget-guidance", "agent-mcp", "agent-logger"))
def test_non_index_global_comments_do_not_authorize_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plugin: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    root = home / ("." + plugin)
    payload = REPO / "plugins" / plugin
    profile = MODULE.profile_for(payload, plugin)
    directory = home / ".local" / "bin"
    directory.mkdir(parents=True)
    stub = directory / plugin
    content = f'# old resolve-runtime {root} {profile.module}\nexec operator-tool "$@"\n'
    stub.write_text(content, encoding="utf-8")
    assert MODULE._local_bindings(payload, root, profile) == (directory, ())
    assert stub.read_text(encoding="utf-8") == content


@pytest.mark.parametrize("plugin", ("agent-bridge", "agent-dispatch", "agent-containers"))
def test_local_bindings_use_installed_artifact_path_without_executing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plugin: str,
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    payload = REPO / "plugins" / plugin
    root = home / ("." + plugin)
    _seed(root, plugin)
    profile_path = payload / "scripts/uninstall-profile.json"
    profile = MODULE.profile_for(payload, plugin, profile_path if profile_path.is_file() else None)
    publisher = tmp_path / "unavailable-original-publisher"
    record = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    record["source"]["path"] = str(publisher)
    (root / "deploy-manifest.json").write_text(json.dumps(record), encoding="utf-8")
    commands = MODULE._load(REPO / "libs/installer-engine/installer_engine_commands.py")
    templates = commands._templates(payload, root, plugin, publisher_root=publisher)
    directory = home / ".local/bin"
    directory.mkdir(parents=True)
    for name in (plugin, plugin + "-board") if plugin == "agent-dispatch" else (plugin,):
        (directory / name).write_text(templates[name][0], encoding="utf-8")
    expected = {path.name for path in directory.iterdir()}
    assert set(MODULE._local_bindings(payload, root, profile)[1]) == expected
    record["source"]["path"] = str(tmp_path / "other-publisher")
    (root / "deploy-manifest.json").write_text(json.dumps(record), encoding="utf-8")
    assert MODULE._local_bindings(payload, root, profile)[1] == ()
    assert not publisher.exists()


@pytest.mark.parametrize("mutation", ("none", "custom-root", "foreign-payload", "owned-stamp", "modified-shell",
                                     "modified-ps1", "comment-proxy"))
def test_index_bindings_require_exact_owned_launchers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    payload = REPO / "plugins" / "agent-index"
    root = home / (".custom-index" if mutation == "custom-root" else ".agent-index")
    profile = MODULE.profile_for(payload, "agent-index")
    commands = home / ".local" / "bin"
    commands.mkdir(parents=True)
    templates = MODULE._index_launchers(payload)
    if mutation == "owned-stamp":
        stamp = tmp_path / "old-index-payload"
        stamp.mkdir()
        (stamp / "pyproject.toml").write_text('[project]\nname="agent-index"\n', encoding="utf-8")
        root.mkdir()
        (root / "payload-dir").write_text(str(stamp), encoding="utf-8")
        templates = MODULE._index_launchers(payload, stamp)
    for name, values in templates.items():
        content = values[1] if mutation in {"foreign-payload", "owned-stamp"} else values[0]
        if mutation == "foreign-payload":
            content = content.replace(str(payload), str(tmp_path / "foreign-payload"))
        if mutation == "modified-shell" and name == "agent-index":
            content += "\n# operator customization"
        if mutation == "modified-ps1" and name == "agent-index.ps1":
            content += "\n# operator customization"
        if mutation == "comment-proxy":
            content = f"# resolve-runtime {root} agent_index"
        (commands / name).write_text(content, encoding="utf-8")
    _, selected = MODULE._local_bindings(payload, root, profile)
    expected = {
        "none": set(templates), "owned-stamp": set(templates),
        "custom-root": set(), "foreign-payload": set(),
        "modified-shell": {"agent-index.ps1", "agent-index.cmd"},
        "modified-ps1": {"agent-index"}, "comment-proxy": set(),
    }
    assert set(selected) == expected[mutation]


@pytest.mark.parametrize("plugin", ("agent-machines", "agent-containers"))
@pytest.mark.parametrize("mutation", ("none", "other-root", "extra-argument", "other-script"))
def test_legacy_adapter_controller_requires_exact_script_and_root(
    tmp_path: Path, plugin: str, mutation: str,
) -> None:
    payload = tmp_path / plugin
    root = tmp_path / "runtime"
    script = payload / "scripts" / (
        "other.sh" if mutation == "other-script" else "legacy-uninstall.sh"
    )
    selected = root.parent / "other" if mutation == "other-root" else root
    arguments = ["bash", str(script), str(selected)]
    if mutation == "extra-argument":
        arguments.append("other")
    command = (subprocess.list2cmdline(arguments) if os.name == "nt" else
               " ".join(shlex.quote(value) for value in arguments))
    assert MODULE._parent(
        123, "bash", command, payload, root, plugin, 123,
    ) is (mutation == "none")


@pytest.mark.parametrize("canonical_matches", (True, False))
def test_copied_source_helper_resolves_only_matching_canonical_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, canonical_matches: bool,
) -> None:
    payload = tmp_path / "plugins" / "agent-bridge"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    copied = scripts / SOURCE.name
    copied.write_bytes(SOURCE.read_bytes())
    canonical = tmp_path / "libs" / "installer-engine"
    canonical.mkdir(parents=True)
    (canonical / SOURCE.name).write_bytes(
        copied.read_bytes() if canonical_matches else b"foreign helper",
    )
    dependency = canonical / "installer-engine.ps1"
    dependency.write_text("# canonical engine\n", encoding="utf-8")
    monkeypatch.setattr(MODULE, "__file__", str(copied))
    if canonical_matches:
        assert MODULE._dependency(payload, dependency.name) == dependency
    else:
        with pytest.raises(MODULE.UninstallRefused, match="dependency missing"):
            MODULE._dependency(payload, dependency.name)


@pytest.fixture
def installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    for key in tuple(os.environ):
        if key.startswith(("COPILOT_PLUGIN_", "AGENT_INDEX_")):
            monkeypatch.delenv(key, raising=False)
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    origin = REPO / "plugins" / "agent-bridge" / "scripts"
    shutil.copytree(origin / "installation-context", scripts / "installation-context")
    for name in (*MODULE.RUNTIME_MODULES, "command_binding.py", "command_lifecycle.py"):
        shutil.copyfile(origin / name, scripts / name)
    (payload / "pyproject.toml").write_text(
        '[project]\nname="agent-bridge"\nversion="1.0.0"\n', encoding="utf-8",
    )
    root = tmp_path / "runtime"
    _seed(root, "agent-bridge")
    return payload, root


def _seed(root: Path, plugin: str) -> None:
    values = {
        "versions/1.0.0/runtime.py": b"runtime code",
        "snapshots/1.0.0/code.py": b"snapshot code",
        "bin/resolve-runtime.sh": b"generated helper",
        "scripts/versioned_runtime.py": b"generated resolver",
        "current-version": b"1.0.0",
        "last-known-good": b"1.0.0",
        "stamped-version": b"1.0.0",
        "config.yaml": b"configuration",
        "tasks.db": b"database",
        "auth/custom": b"fixture authentication",
        "models/model.bin": b"model cache",
        "bin/custom": b"unknown helper",
        "snapshots/custom-note": b"unknown snapshot file",
        "versions/notes/custom": b"unknown non-generation",
        "unknown": b"\x00\xff",
        ".install.lock": b"installer lock",
        ".install-stage/custom": b"staged source",
    }
    for name, value in values.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    (root / "deploy-manifest.json").write_text(json.dumps({
        "schema_version": 3, "service": plugin, "source": {"plugin": plugin, "kind": "local"},
    }), encoding="utf-8-sig")


def _remove(_payload: Path, _shell: str, root: Path, names: tuple[str, ...]) -> None:
    for name in names:
        path = root / name
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def _cleanup(payload: Path, root: Path, **arguments: Any) -> dict[str, Any]:
    if not HOST:
        pytest.skip("Native host unavailable")
    return MODULE.cleanup(payload, "agent-bridge", root, HOST, **arguments)


@pytest.mark.skipif(os.name == "nt", reason="Actual packaged POSIX removal")
@pytest.mark.parametrize(("plugin", "entrypoint"), (
    ("agent-dispatch", "helper"),
    ("agent-index", "helper"),
    ("agent-dispatch", "native"),
))
def test_materialized_shell_adopter_removes_runtime_without_repository_libraries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, plugin: str, entrypoint: str,
) -> None:
    if not HOST:
        pytest.skip("Native shell unavailable")
    monkeypatch.syspath_prepend(str(REPO / "tools"))
    spec = importlib.util.spec_from_file_location(
        "packaged_removal_materializer", REPO / "tools" / "materialize_installer_engine.py",
    )
    assert spec is not None and spec.loader is not None
    materializer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(materializer)
    candidate = tmp_path / "release"
    payload = candidate / "plugins" / plugin
    shutil.copytree(
        REPO / "plugins" / plugin, payload,
        ignore=shutil.ignore_patterns("__pycache__", ".venv", "tests"),
    )
    (payload / "scripts" / "installer_engine_process.py").unlink(missing_ok=True)
    materializer.materialize_refs(candidate, canonical_root=REPO)
    assert not (candidate / "libs").exists()
    home = tmp_path / "home"
    root = home / ("." + plugin)
    _seed(root, plugin)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("AGENT_", "COPILOT_", "BUDGET_GUIDANCE_", "INDEX_"))
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_EXTENSIONS_TEST_CONTAINED="1",
    )
    command = (
        [HOST, str(payload / "scripts/install.sh"), "uninstall", "--no-service", "--no-supervisor"]
        if entrypoint == "native" else
        [sys.executable, "-I", "-B", str(payload / "scripts" / SOURCE.name),
         "--payload-root", str(payload), "--plugin-id", plugin, "--root", str(root),
         "--shell", HOST, "--remove-runtime"]
    )
    result = subprocess.run(
        command,
        cwd=home, env=environment, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    if entrypoint == "helper":
        assert json.loads(result.stdout)["status"] == "removed"
    else:
        assert "Venv removed (config + DB kept" in result.stdout
        repeated = subprocess.run(
            command, cwd=home, env=environment, capture_output=True, text=True, timeout=60, check=False,
        )
        assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert not (root / "versions" / "1.0.0").exists()
    assert not (root / "snapshots" / "1.0.0").exists()
    assert not (root / "deploy-manifest.json").exists()
    assert (root / "config.yaml").read_bytes() == b"configuration"
    assert (root / "unknown").read_bytes() == b"\x00\xff"
    assert (root / ".install.lock").read_bytes() == b"installer lock"
    assert (payload / "scripts" / "installer_engine_process.py").read_bytes() == (
        REPO / "libs" / "installer-engine" / "installer_engine_process.py"
    ).read_bytes()


@pytest.mark.parametrize(
    "mutation", ("broken-json", "non-object", "binding", "lifecycle", "source"),
)
@pytest.mark.parametrize("remove_runtime", (False, True))
def test_malformed_ownership_preserves_recovery_evidence_before_native_cleanup(
    installation: tuple[Path, Path], mutation: str, remove_runtime: bool,
) -> None:
    payload, root = installation
    manifest = root / "deploy-manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8-sig"))
    if mutation == "broken-json":
        manifest.write_bytes(b"\xef\xbb\xbf{")
    elif mutation == "non-object":
        manifest.write_text("[]", encoding="utf-8")
    else:
        if mutation == "binding":
            record["commandBinding"] = {"version": "invalid"}
        elif mutation == "lifecycle":
            record["commandLifecycle"] = {"version": 1, "state": "complete", "owner": None}
        else:
            record["source"] = []
        manifest.write_text(json.dumps(record), encoding="utf-8")
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    operations: list[tuple[Path, tuple[str, ...]]] = []

    def observe(_payload: Path, _shell: str, directory: Path, names: tuple[str, ...]) -> None:
        operations.append((directory, names))

    with pytest.raises((OSError, RuntimeError, ValueError)):
        _cleanup(payload, root, remove_runtime=remove_runtime, remover=observe)
    assert not operations, "Malformed ownership must refuse before any native cleanup"
    assert {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    } == before


@pytest.mark.parametrize("mutation", ("none", "cell", "managed", "foreign-service",
                                     "foreign-source", "incomplete"))
def test_nested_index_inventory_revalidates_ownership(
    installation: tuple[Path, Path], mutation: str,
) -> None:
    payload, root = installation
    engine = root / "engine"
    _seed(engine, "agent-index-engine")
    runtime = MODULE._load(payload / "scripts" / "versioned_runtime.py")
    profile = MODULE.profile_for(REPO / "plugins" / "agent-index", "agent-index")
    before = MODULE._targets(payload, "agent-index", root, profile, runtime)
    record = json.loads((engine / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    if mutation in {"cell", "managed"}:
        (engine / ("install.json" if mutation == "cell" else "runtime-deployment.json")).write_text("{}")
    elif mutation == "foreign-service":
        record["service"] = "foreign"
    elif mutation == "foreign-source":
        record["source"]["plugin"] = "foreign"
    elif mutation == "incomplete":
        record["commandLifecycle"] = {"state": "pending"}
    (engine / "deploy-manifest.json").write_text(json.dumps(record), encoding="utf-8")
    if mutation == "none":
        assert MODULE._targets(payload, "agent-index", root, profile, runtime) == before
    else:
        with pytest.raises(MODULE.UninstallRefused):
            MODULE._targets(payload, "agent-index", root, profile, runtime)
    assert (engine / "versions/1.0.0/runtime.py").read_bytes() == b"runtime code"


def test_released_real_leases_are_retained_not_treated_as_busy(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, root = installation
    runtime = MODULE._load(payload / "scripts" / "versioned_runtime.py")
    with runtime._leases.batch_removal_custody(root, ["1.0.0"]):
        pass
    with runtime._leases.construction_lease(root / "snapshots", "1.0.0"):
        pass
    retained = [
        path for path in root.rglob("*") if path.is_file() and
        (".lock" in path.name or path.name in {
            "config.yaml", "tasks.db", "custom", "unknown", "custom-note",
        })
    ]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
              for path in retained}
    monkeypatch.setattr(MODULE, "refuse_consumers", lambda *_: None)
    assert _cleanup(payload, root, remove_runtime=True, remover=_remove)["status"] == "removed"
    assert not (root / "versions" / "1.0.0").exists()
    assert not (root / "snapshots" / "1.0.0").exists()
    assert not (root / "bin" / "resolve-runtime.sh").exists()
    assert not (root / "deploy-manifest.json").exists()
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino)
            for path in retained} == before
    assert _cleanup(payload, root)["status"] == "absent"


@pytest.mark.parametrize(
    "kind", ("construction", "retention", "snapshot", "snapshot-retention", "redirects"),
)
@pytest.mark.parametrize("admission_only", (False, True))
def test_real_held_custody_blocks_all_mutation(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, kind: str,
    admission_only: bool,
) -> None:
    payload, root = installation
    runtime = MODULE._load(payload / "scripts" / "versioned_runtime.py")
    leases = runtime._leases
    context = (
        leases.construction_lease(root / "snapshots", "1.0.0")
        if kind == "snapshot" else
        leases._kernel_lease(root / "snapshots", "1.0.0", kind="retention", shared=True, timeout=0)
        if kind == "snapshot-retention" else
        leases._kernel_lease(root, "redirects" if kind == "redirects" else "1.0.0",
                             kind="publication" if kind == "redirects" else kind,
                             shared=kind == "retention", timeout=0)
    )
    monkeypatch.setattr(MODULE, "refuse_consumers", lambda *_: None)
    calls = []
    with context:
        before = (root / "deploy-manifest.json").read_bytes()
        with pytest.raises((RuntimeError, OSError)):
            _cleanup(payload, root, remove_runtime=not admission_only,
                     check_consumers=admission_only, remover=lambda *args: calls.append(args))
        assert not calls
        assert (root / "versions" / "1.0.0" / "runtime.py").is_file()
        assert (root / "deploy-manifest.json").read_bytes() == before


@pytest.mark.parametrize("marker", ("install.json", "runtime-deployment.json"))
@pytest.mark.parametrize("populated", (False, True))
def test_cell_or_managed_roots_never_enter_legacy_cleanup(
        installation: tuple[Path, Path], marker: str, populated: bool,
) -> None:
        payload, root = installation
        if not populated:
            root = root.parent / "empty-root"
            root.mkdir()
        (root / marker).write_text("{}", encoding="utf-8")
        before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
        with pytest.raises(MODULE.UninstallRefused, match="installation-cell or managed"):
            _cleanup(payload, root, remove_runtime=True)
        assert {path: path.read_bytes() for path in before} == before


def test_nested_index_engine_busy_blocks_main_removal(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, root = installation
    _seed(root / "engine", "agent-index-engine")
    record = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8-sig"))
    record["service"] = record["source"]["plugin"] = "agent-index"
    (root / "deploy-manifest.json").write_text(json.dumps(record), encoding="utf-8")
    runtime = MODULE._load(payload / "scripts" / "versioned_runtime.py")
    monkeypatch.setattr(MODULE, "refuse_consumers", lambda *_: None)
    with runtime._leases.construction_lease(root / "engine", "1.0.0"):
        with pytest.raises((RuntimeError, OSError)):
            MODULE.cleanup(payload, "agent-index", root, HOST or "", remove_runtime=True, remover=_remove)
    assert (root / "versions" / "1.0.0" / "runtime.py").is_file()
    assert (root / "engine" / "versions" / "1.0.0" / "runtime.py").is_file()


def test_partial_failure_retains_exact_retry_proof(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, root = installation
    before = (root / "deploy-manifest.json").read_bytes()
    monkeypatch.setattr(MODULE, "refuse_consumers", lambda *_: None)

    def fail_after_delete(payload: Path, shell: str, directory: Path, names: tuple[str, ...]) -> None:
        _remove(payload, shell, directory, names)
        if "deploy-manifest.json" in names:
            raise OSError("fixture failure after manifest unlink")

    with pytest.raises(OSError, match="after manifest unlink"):
        _cleanup(payload, root, remove_runtime=True, remover=fail_after_delete)
    assert (root / "deploy-manifest.json").read_bytes() == before
    assert (root / "tasks.db").read_bytes() == b"database"
    assert _cleanup(payload, root, remove_runtime=True, remover=_remove)["status"] == "removed"


def test_profile_guard_runs_inside_custody_before_deletion(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, root = installation
    profile = payload / "scripts" / "uninstall-profile.json"
    profile.write_text(json.dumps({
        "version": 1, "root_entries": list(MODULE.BASE), "bin_entries": list(MODULE.RESOLVERS),
        "script_entries": [], "local_bin_entries": [], "module": "agent_bridge",
        "guard_module": "policy.py",
    }), encoding="utf-8")
    (payload / "scripts" / "policy.py").write_text(
        "def check_under_custody(root, payload_root, plugin_id):\n"
        "    if (root / '.publication-leases').exists():\n"
        "        raise RuntimeError('fixture reconnect closure remains live')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(MODULE, "refuse_consumers", lambda *_: None)
    with pytest.raises(RuntimeError, match="reconnect closure"):
        _cleanup(payload, root, profile=profile, remove_runtime=True, remover=_remove)
    assert (root / "versions" / "1.0.0" / "runtime.py").is_file()


def test_check_is_byte_and_entry_nonmutating(installation: tuple[Path, Path]) -> None:
    payload, root = installation
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert _cleanup(payload, root)["status"] == "ready"
    assert {path: path.read_bytes() for path in root.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("busy", (False, True))
def test_explicit_consumer_admission_holds_custody_without_removing_code(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, busy: bool,
) -> None:
    payload, root = installation
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    inspected = []

    def inspect(*_args: Any) -> None:
        assert (root / ".publication-leases").is_dir()
        inspected.append(True)
        if busy:
            raise MODULE.UninstallRefused("fixture rooted ancestor remains live")

    monkeypatch.setattr(MODULE, "refuse_consumers", inspect)
    if busy:
        with pytest.raises(MODULE.UninstallRefused, match="rooted ancestor"):
            _cleanup(payload, root, check_consumers=True)
    else:
        assert _cleanup(payload, root, check_consumers=True)["status"] == "ready"
    assert inspected == [True]
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("ticket_state", ("valid", "other-root", "other-token", "malformed"))
def test_unattributed_native_lifecycle_ticket_is_validated_not_blindly_rejected(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, ticket_state: str,
) -> None:
    payload, root = installation
    owner = MODULE._ownership(payload, "agent-bridge", root)
    assert owner[2] is None
    binding, primitive, _, _ = owner
    lifecycle = binding._load_lifecycle()
    runtime = MODULE._load(payload / "scripts/versioned_runtime.py")
    profile = MODULE.profile_for(payload, "agent-bridge")
    targets = MODULE._targets(payload, "agent-bridge", root, profile, runtime)
    arguments = dict(payload_root=payload, plugin_id="agent-bridge", legacy_root=root,
                     project_root=Path.cwd(), environment={**os.environ, "COPILOT_PLUGIN_ROOT": str(payload)})
    before = (root / "deploy-manifest.json").read_bytes()
    with lifecycle.legacy_lifecycle(
        binding._rules(), primitive, reserve=False, teardown=True, **arguments,
    ) as lease:
        ticket = dict(lease.ticket)
        if ticket_state == "other-root":
            ticket["root"] = str(root.parent / "other")
        elif ticket_state == "other-token":
            ticket["token"] = "0" * 32
        monkeypatch.setenv(lifecycle.TICKET_ENV, "{" if ticket_state == "malformed" else json.dumps(ticket))
        token = lifecycle._CURRENT.set(None)
        try:
            if ticket_state == "valid":
                with MODULE._custody(payload, "agent-bridge", root, owner, targets, runtime):
                    lease.lock.assert_owned()
            else:
                with pytest.raises((RuntimeError, ValueError)):
                    with MODULE._custody(payload, "agent-bridge", root, owner, targets, runtime):
                        pytest.fail("A foreign or malformed native lifecycle was admitted")
        finally:
            lifecycle._CURRENT.reset(token)
    assert (root / "deploy-manifest.json").read_bytes() == before
    assert (root / "versions/1.0.0/runtime.py").is_file()


@pytest.mark.parametrize("mutation", (
    "none", "binding-script", "root", "plugin", "publish", "native-root",
    "rooted-interpreter", "owner-pid", "unrelated-parent",
))
def test_only_exact_live_lifecycle_owner_and_native_caller_are_controllers(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    payload, root = installation
    binding, primitive, _, _ = MODULE._ownership(payload, "agent-bridge", root)
    lifecycle = binding._load_lifecycle()
    arguments = dict(payload_root=payload, plugin_id="agent-bridge", legacy_root=root,
                     project_root=Path.cwd(), environment={**os.environ, "COPILOT_PLUGIN_ROOT": str(payload)})
    if not HOST:
        pytest.skip("Native host unavailable")
    native = ([HOST, "-NoProfile", "-File", str(payload / "scripts/install.ps1"),
               "uninstall", "-InstallDir", str(root)] if os.name == "nt" else
              [HOST, str(payload / "scripts/install.sh"), "uninstall", "--install-dir", str(root)])
    parent = list(native)
    owner_args = [
        sys.executable, "-I", "-S", "-B", "-X", "utf8", str(payload / "scripts/command_binding.py"),
        "--payload-root", str(payload), "--plugin-id", "agent-bridge", "--legacy-root", str(root),
        "--teardown", "--run-lifecycle", "--", *native,
    ]
    image = sys.executable
    if mutation == "binding-script":
        owner_args[6] = str(payload / "scripts/other.py")
    elif mutation in {"root", "plugin"}:
        key = "--legacy-root" if mutation == "root" else "--plugin-id"
        owner_args[owner_args.index(key) + 1] = str(root.parent / "other") if mutation == "root" else "other"
    elif mutation == "publish":
        owner_args.remove("--teardown")
    elif mutation == "native-root":
        owner_args[-1] = str(root.parent / "other")
    elif mutation == "rooted-interpreter":
        image = str(root / "versions/1.0.0/python")
    elif mutation == "unrelated-parent":
        parent[-3] = "install"
    command = lambda argv: subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)  # noqa: E731
    with lifecycle.legacy_lifecycle(
        binding._rules(), primitive, reserve=False, teardown=True, **arguments,
    ) as lease:
        monkeypatch.setenv(lifecycle.TICKET_ENV, json.dumps(lease.ticket))
        pid = lease.ticket["pid"]
        rows = [
            {"pid": pid + (100000 if mutation == "owner-pid" else 0), "ppid": 123,
             "image": image, "command": command(owner_args), "argv": owner_args},
            {"pid": 123, "ppid": 124, "image": HOST, "command": command(parent), "argv": parent},
            {"pid": 124, "ppid": 1, "image": str(root / "versions/1.0.0/python"),
             "command": "python -m agent_bridge", "argv": ["python", "-m", "agent_bridge"]},
        ]
        _, admitted = MODULE._lifecycle_controllers(rows, payload, root, "agent-bridge")
        expected = {pid, 123} if mutation == "none" else {pid} if mutation == "unrelated-parent" else set()
        assert admitted == expected
        assert 124 not in admitted, "A legacy runtime ancestor must never become a controller"


@pytest.mark.skipif(os.name != "nt", reason="Native Windows command-line decoding")
def test_controller_argv_preserves_native_quoted_program_and_arguments(tmp_path: Path) -> None:
    arguments = [
        str(tmp_path / "quoted ' payload" / "python.exe"), "-I", "-B", "-c",
        'print("embedded \\\\"quote and spaces")',
        str(tmp_path / "runtime's directory" / "runtime.py"),
    ]
    assert MODULE._argv(subprocess.list2cmdline(arguments), arguments[0]) == arguments


@pytest.mark.parametrize("mutation", (
    "literal", "bash32-continuations", "modified", "comment", "leading-space",
    "wrong-flag", "wrong-zero", "foreign-parent",
))
def test_staging_watchdog_accepts_only_exact_native_heredoc_renderings(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    original, root = installation
    plugin = "agent-bridge"
    payload = Path.home() / ("." + plugin) / ".install-stage" / "fixture" / plugin
    (payload / "scripts").mkdir(parents=True)
    shutil.copyfile(REPO / "plugins" / plugin / "scripts/install.sh", payload / "scripts/install.sh")
    commands = MODULE._load(SOURCE.parent / "installer_engine_commands.py")
    body = commands._sh(
        commands._read(REPO / "plugins" / plugin / "scripts/install.sh"), "INSTALL_WATCHDOG",
    ).rstrip("\n")
    assert "\\\n" in body
    rendered = body.replace("\\\n", "") if mutation == "bash32-continuations" else body
    if mutation == "modified":
        rendered = rendered.replace("shift 4", "shift 3")
    elif mutation == "comment":
        rendered += "\n# not the exact writer"
    elif mutation == "leading-space":
        rendered = " " + rendered
    owner_pid, native_pid, watchdog_pid = 9001, 9002, 9003
    native = ["/bin/bash", str(original / "scripts/install.sh"),
              "uninstall", "--install-dir", str(root)]
    owner_args = [
        sys.executable, "-I", "-S", "-B", str(original / "scripts/command_binding.py"),
        "--plugin-id", plugin, "--legacy-root", str(root),
        "--teardown", "--run-lifecycle", "--", *native,
    ]
    watchdog_args = [
        "/bin/bash", "-x" if mutation == "wrong-flag" else "-c", rendered,
        "other" if mutation == "wrong-zero" else "bash",
        str(payload / "scripts/install.sh"), plugin, str(payload.parent), "60",
        "uninstall", "--install-dir", str(root),
    ]
    lease = SimpleNamespace(ticket={"pid": owner_pid})
    binding = SimpleNamespace(
        _load_lifecycle=lambda: SimpleNamespace(borrowed_lease=lambda *_a, **_k: lease),
        _rules=lambda: None, _load_primitive=lambda _payload: None,
    )
    load = MODULE._load
    monkeypatch.setenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "fixture")
    monkeypatch.setattr(MODULE, "_authority_payload", lambda _payload: original)
    monkeypatch.setattr(MODULE, "_dependency", lambda _payload, name: SOURCE.parent / name)
    monkeypatch.setattr(MODULE, "_load", lambda path: binding if path.name == "command_binding.py" else load(path))
    rows = [
        {"pid": owner_pid, "ppid": native_pid, "image": sys.executable,
         "command": "", "argv": owner_args},
        {"pid": native_pid, "ppid": 1, "image": "/bin/bash", "command": "", "argv": native},
        {"pid": watchdog_pid, "ppid": 9999 if mutation == "foreign-parent" else owner_pid,
         "image": "/bin/bash", "command": "", "argv": watchdog_args},
    ]
    _, admitted = MODULE._lifecycle_controllers(rows, payload, root, plugin)
    expected = {owner_pid, native_pid}
    if mutation in {"literal", "bash32-continuations"}:
        expected.add(watchdog_pid)
    assert admitted == expected


@pytest.mark.parametrize("name", (
    "/absolute.py", "../other.py", "conduct/../other.py", "conduct//file.py",
    "conduct/./file.py", "conduct\\other.py", "conduct/tasks.db", ".install-stage/file.py",
))
def test_helper_profile_rejects_unsafe_nested_files(
    installation: tuple[Path, Path], name: str,
) -> None:
    payload, root = installation
    profile = payload / "scripts" / "uninstall-profile.json"
    profile.write_text(json.dumps({
        "version": 1, "root_entries": list(MODULE.BASE), "bin_entries": [name],
        "script_entries": [], "local_bin_entries": [], "module": "agent_bridge",
    }), encoding="utf-8")
    with pytest.raises(MODULE.UninstallRefused, match="finite reconstructible"):
        _cleanup(payload, root, profile=profile)


def test_nested_helper_files_and_own_bytecode_leave_unknown_siblings(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, root = installation
    selected = ("bin/conduct/notice.md", "scripts/nested/helper.py",
                "scripts/nested/__pycache__/helper.cpython-313.pyc")
    unknown = ("bin/conduct/custom.md", "scripts/nested/unknown.py",
               "scripts/nested/__pycache__/unknown.cpython-313.pyc")
    for name in (*selected, *unknown):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    retained = {root / name: ((root / name).read_bytes(), (root / name).stat().st_ino)
                for name in unknown}
    profile = payload / "scripts" / "uninstall-profile.json"
    profile.write_text(json.dumps({
        "version": 1, "root_entries": list(MODULE.BASE),
        "bin_entries": ["conduct/notice.md"], "script_entries": ["nested/helper.py"],
        "local_bin_entries": [], "module": "agent_bridge",
    }), encoding="utf-8")
    monkeypatch.setattr(MODULE, "refuse_consumers", lambda *_: None)
    assert _cleanup(payload, root, profile=profile, remove_runtime=True, remover=_remove)["status"] == "removed"
    assert all(not (root / name).exists() for name in selected)
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in retained} == retained
    assert _cleanup(payload, root, profile=profile)["status"] == "absent"


@pytest.mark.parametrize("kind", ("directory-link", "file-link", "hardlink"))
def test_nested_helper_links_refuse_before_native_deletion(
    installation: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    payload, root = installation
    outside = tmp_path / "outside"
    outside.mkdir()
    witness = outside / "notice.md"
    witness.write_bytes(b"outside fixture retained\n")
    target = root / "bin/conduct/notice.md"
    try:
        if kind == "directory-link":
            target.parent.symlink_to(outside, target_is_directory=True)
        else:
            target.parent.mkdir()
            if kind == "hardlink":
                os.link(witness, target)
            else:
                target.symlink_to(witness)
    except OSError as error:
        if os.name == "nt" and getattr(error, "winerror", None) == 1314:
            pytest.skip("This Windows account lacks symlink creation privilege")
        raise
    profile = payload / "scripts/uninstall-profile.json"
    profile.write_text(json.dumps({
        "version": 1, "root_entries": list(MODULE.BASE),
        "bin_entries": ["conduct/notice.md"], "script_entries": [],
        "local_bin_entries": [], "module": "agent_bridge",
    }), encoding="utf-8")
    calls = []
    monkeypatch.setattr(MODULE, "refuse_consumers", lambda *_: None)
    manifest = (root / "deploy-manifest.json").read_bytes()
    with pytest.raises(MODULE.UninstallRefused, match="Redirected|ordinary unshared"):
        _cleanup(payload, root, profile=profile, remove_runtime=True,
                 remover=lambda *args: calls.append(args))
    assert not calls
    assert witness.read_bytes() == b"outside fixture retained\n"
    assert (root / "deploy-manifest.json").read_bytes() == manifest
    assert (root / "versions/1.0.0/runtime.py").is_file()


def test_codespaces_profile_includes_root_resolvers_and_only_their_bytecode(tmp_path: Path) -> None:
    payload = REPO / "plugins" / "agent-codespaces"
    profile = MODULE.profile_for(payload, "agent-codespaces", payload / "scripts/uninstall-profile.json")
    root = tmp_path / "runtime"
    names = (*MODULE.RUNTIME_MODULES, "__pycache__/versioned_runtime.cpython-313.pyc",
             "__pycache__/operator.cpython-313.pyc")
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture code")
    operations = MODULE._plan(payload, root, profile, (MODULE.Target(root, (), ()),))
    selected = {directory / name for directory, entries in operations for name in entries}
    assert {root / name for name in names[:-1]} <= selected
    assert root / names[-1] not in selected


def test_removable_working_directory_is_not_admitted(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, root = installation
    monkeypatch.chdir(root / "snapshots" / "1.0.0")
    with pytest.raises(RuntimeError, match="working directory"):
        _cleanup(payload, root)
    assert (root / "deploy-manifest.json").is_file()


@pytest.mark.parametrize("name", ("config.yaml", "../runtime", "auth", ".publication-leases", "tasks.db"))
def test_profile_cannot_select_durable_or_traversing_entries(
    installation: tuple[Path, Path], name: str,
) -> None:
    payload, root = installation
    profile = payload / "scripts" / "uninstall-profile.json"
    profile.write_text(json.dumps({
        "version": 1, "root_entries": [name], "bin_entries": [],
        "script_entries": [], "local_bin_entries": [], "module": "agent_bridge",
    }), encoding="utf-8")
    with pytest.raises(RuntimeError):
        _cleanup(payload, root, profile=profile)


def test_real_live_module_consumer_is_not_deleted_or_signalled(
    installation: tuple[Path, Path],
) -> None:
    payload, root = installation
    if not HOST:
        pytest.skip("Native host unavailable")
    (payload / "fixture_uninstall_worker.py").write_text(
        "import time\ntime.sleep(90)\n", encoding="utf-8",
    )
    child = subprocess.Popen(  # noqa: S603 - fixed fixture interpreter/module
        [sys.executable, "-B", "-m", "fixture_uninstall_worker"], cwd=payload,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        with pytest.raises(RuntimeError, match=rf"consumer \(pid={child.pid}\)"):
            MODULE.refuse_consumers(payload, "agent-bridge", root, HOST,
                                   replace(MODULE.profile_for(payload, "agent-bridge"),
                                           module="fixture_uninstall_worker"))
        assert child.poll() is None
        assert (root / "versions" / "1.0.0" / "runtime.py").is_file()
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_idle_native_inventory_does_not_refuse_its_owned_inspection_host(
    installation: tuple[Path, Path],
) -> None:
    payload, root = installation
    if not HOST:
        pytest.skip("Native host unavailable")
    MODULE.refuse_consumers(payload, "agent-bridge", root, HOST,
                           replace(MODULE.profile_for(payload, "agent-bridge"),
                                   module="fixture_uninstall_worker"))


def test_retained_transaction_gate_requires_exact_trusted_source_and_parent(
    installation: tuple[Path, Path],
) -> None:
    payload, root = installation
    script = payload / "scripts" / "install-transaction.py"
    script.write_text("# fixture trusted transaction gate\n", encoding="utf-8")
    command = subprocess.list2cmdline([
        sys.executable, "-I", "-B", str(script), str(root), "300",
    ]) if os.name == "nt" else " ".join((
        str(sys.executable), "-I", "-B", str(script), str(root), "300",
    ))
    row = {"ppid": os.getppid(), "image": sys.executable, "command": command}
    assert MODULE._transaction_helper(row, payload, root)
    assert not MODULE._transaction_helper({**row, "ppid": -1}, payload, root)
    assert not MODULE._transaction_helper(
        {**row, "command": command.replace("install-transaction.py", "unknown.py")}, payload, root,
    )


def test_captured_argv_preserves_quoted_controller_paths(
    installation: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, root = installation
    quoted_payload = payload.with_name("payload's quoted space")
    quoted_root = root.with_name("runtime's quoted space")
    payload.rename(quoted_payload)
    root.rename(quoted_root)
    payload, root = quoted_payload, quoted_root
    if not HOST:
        pytest.skip("Native host unavailable")
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    entry = payload / "scripts/uninstall-state.py"
    entry.write_text("# fixture entrypoint\n", encoding="utf-8")
    invocation = [str(entry), "--root", str(root), "--remove-runtime"]
    monkeypatch.setattr(sys, "argv", invocation)
    native = ([HOST, "-NoProfile", "-File", str(payload / "scripts/install.ps1"),
               "uninstall", "-InstallDir", str(root)] if os.name == "nt" else
              [HOST, str(payload / "scripts/install.sh"), "uninstall", "--install-dir", str(root)])
    parent = os.getppid()
    redirect = [sys.executable, "-I", "-B", *invocation]
    rows = [
        {"pid": parent, "ppid": 9001, "image": sys.executable,
         "command": " ".join(redirect), "argv": redirect},
        {"pid": 9001, "ppid": 9002, "image": HOST, "command": " ".join(native), "argv": native},
        {"pid": 9002, "ppid": 1, "image": str(root / "versions/1.0.0/python"),
         "command": "python -m agent_bridge", "argv": ["python", "-m", "agent_bridge"]},
    ]
    controllers, redirectors = MODULE._controllers(rows, payload, root, "agent-bridge")
    assert controllers == {9001}
    assert redirectors == {parent}
    assert 9002 not in controllers | redirectors
    assert MODULE._arguments({**rows[0], "argv": []}) == []
    gate = payload / "scripts/install-transaction.py"
    gate.write_text("# fixture gate\n", encoding="utf-8")
    arguments = [sys.executable, "-I", "-B", str(gate), str(root), "30"]
    row = {"ppid": parent, "image": sys.executable, "command": " ".join(arguments), "argv": arguments}
    assert MODULE._transaction_helper(row, payload, root)
    assert not MODULE._transaction_helper({**row, "argv": []}, payload, root)


@pytest.mark.parametrize("shell", ("ps1", "sh"))
def test_real_self_staged_idle_cleanup_retains_source_and_coproc_gate(
    installation: tuple[Path, Path], shell: str,
) -> None:
    payload, root = installation
    staged = root / ".install-stage" / "source"
    shutil.move(str(payload), staged)
    scripts = staged / "scripts"
    shutil.copyfile(SOURCE, scripts / "installer_engine_uninstall.py")
    engine = Path(os.environ.get("COPILOT_UNINSTALL_TEST_ENGINE_ROOT", str(SOURCE.parent)))
    profile = scripts / "uninstall-profile.json"
    profile.write_text(json.dumps({
        "version": 1, "root_entries": list(MODULE.BASE), "bin_entries": list(MODULE.RESOLVERS),
        "script_entries": list(MODULE.RUNTIME_MODULES), "local_bin_entries": [],
        "module": "fixture_uninstall_worker",
    }), encoding="utf-8")
    driver = scripts / "uninstall-state.py"
    driver.write_text(
        "import importlib.util, pathlib, sys\n"
        "path=pathlib.Path(__file__).with_name('installer_engine_uninstall.py')\n"
        "spec=importlib.util.spec_from_file_location('staged_cleanup',path)\n"
        "module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module\n"
        "spec.loader.exec_module(module)\n"
        f"engine=pathlib.Path({str(engine)!r})\n"
        "previous=module._dependency\n"
        "module._dependency=lambda payload,name: engine/name if name in "
        "('installer-engine.ps1','installer-engine.sh','installer_engine_process.py') "
        "else previous(payload,name)\n"
        "raise SystemExit(module.main())\n",
        encoding="utf-8",
    )
    gate = scripts / "install-transaction.py"
    gate.write_text(
        "import sys\nprint('ready',flush=True)\nsys.stdin.readline()\n",
        encoding="utf-8",
    )
    native = scripts / ("install.ps1" if shell == "ps1" else "install.sh")
    if shell == "ps1":
        host = HOST
        if os.name != "nt" or not host:
            pytest.skip("Native Windows staging control")
        argv = ["-I", "-B", str(driver), "--payload-root", str(staged),
                "--plugin-id", "agent-bridge", "--root", str(root), "--shell", host,
                "--profile", str(profile), "--remove-runtime"]
        encoded = ",".join("'" + value.replace("'", "''") + "'" for value in argv)
        native.write_text(
            "param([string]$Action,[string]$InstallDir)\n"
            "$ErrorActionPreference='Stop'\n"
            "$s=[Diagnostics.ProcessStartInfo]::new()\n"
            f"$s.FileName='{sys.executable}';$s.UseShellExecute=$false;$s.CreateNoWindow=$true\n"
            "$s.RedirectStandardOutput=$true;$s.RedirectStandardError=$true\n"
            f"foreach($a in @({encoded})){{[void]$s.ArgumentList.Add($a)}}\n"
            "$p=[Diagnostics.Process]::Start($s)\n"
            "$o=$p.StandardOutput.ReadToEndAsync();$e=$p.StandardError.ReadToEndAsync()\n"
            "$p.WaitForExit();[Threading.Tasks.Task]::WaitAll($o,$e)\n"
            "[Console]::Out.Write($o.Result);[Console]::Error.Write($e.Result);exit $p.ExitCode\n",
            encoding="utf-8",
        )
        command = [host, "-NoProfile", "-NonInteractive", "-File", str(native),
                   "-Action", "uninstall", "-InstallDir", str(root)]
    else:
        git = Path(r"C:\Program Files\Git\bin\bash.exe")
        host = str(git) if git.is_file() else shutil.which("bash")
        if not host:
            pytest.skip("Native Bash staging control")
        # Git Bash converts absolute /drive arguments for the external Windows Python.
        def posix(path: Path) -> str:
            value = path.as_posix()
            return "/" + value[0].lower() + value[2:] if os.name == "nt" else value

        native.write_text(
            "#!/usr/bin/env bash\nset -uo pipefail\n"
            f"py={shlex.quote(posix(Path(sys.executable)))}\n"
            f"scripts={shlex.quote(posix(scripts))}\n"
            f"root={shlex.quote(posix(root))}\n"
            'coproc FINITE_GATE { "$py" -I -B "$scripts/install-transaction.py" "$root" 30; }\n'
            'gatepid=$FINITE_GATE_PID\nread -r ready <&"${FINITE_GATE[0]}"\n'
            '"$py" -I -B "$scripts/uninstall-state.py" --payload-root "${scripts%/*}" '
            '--plugin-id agent-bridge --root "$root" --shell "$BASH" '
            '--profile "$scripts/uninstall-profile.json" --remove-runtime\n'
            'status=$?\nprintf "exit\\n" >&"${FINITE_GATE[1]}"\nwait "$gatepid"\nexit "$status"\n',
            encoding="utf-8", newline="\n",
        )
        command = [host, posix(native), "uninstall", "--install-dir", posix(root)]
    result = subprocess.run(  # noqa: S603 - exact staged fixture and owned native processes
        command, cwd=root.parent, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (scripts / "installer_engine_uninstall.py").read_bytes() == SOURCE.read_bytes()
    assert driver.is_file() and gate.is_file()
    assert not (root / "versions" / "1.0.0").exists()
    assert not (root / "deploy-manifest.json").exists()
    assert (root / "tasks.db").read_bytes() == b"database"
