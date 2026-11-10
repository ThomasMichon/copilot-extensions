from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs


@pytest.fixture
def driver(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    # Track both first import and replacement so teardown restores the prior module.
    monkeypatch.setitem(sys.modules, "runtime_install_governance", None)
    monkeypatch.delitem(sys.modules, "runtime_install_governance")
    spec = importlib.util.spec_from_file_location("worktrees_python_runtime_install", scripts / "runtime_install.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_installed_identity", lambda slot: {
        "package_version": module.runtime.package_version(slot.name),
        "build_info": {"version": module.runtime.package_version(slot.name),
                       "runtime_generation": "test-generation"},
    })
    return module


def test_driver_governance_uses_current_scripts(driver):
    governance = sys.modules[driver.Governance.__module__]
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    assert Path(governance.__file__).resolve().parent == scripts
    assert governance.SCRIPTS == scripts


def test_application_preparation_uses_wheel_assets_without_rewriting_wheel(
    tmp_path, monkeypatch,
):
    import agent_worktrees
    from agent_worktrees import runtime_candidate as candidate, update_stage

    slot = tmp_path / "runtime" / "versions" / "1.2.3"
    environment = slot / ".venv"
    package = environment / "Lib" / "site-packages" / "agent_worktrees"
    scripts = package / "_runtime_assets" / "scripts"
    scripts.mkdir(parents=True)
    source_scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in candidate._LIFECYCLE_FILES:
        shutil.copyfile(source_scripts / name, scripts / name)
    shutil.copytree(
        source_scripts / "installation-context", scripts / "installation-context",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (slot / "application.json").write_text("{}", encoding="utf-8")
    (slot / ".runtime-bootstrap").mkdir()
    build_info = package / "_build_info.py"
    build_info.write_bytes(b"immutable wheel member\n")
    frozen = tmp_path / "frozen-source"
    frozen.mkdir()
    (frozen / "__init__.py").write_text("source identity input\n", encoding="utf-8")
    monkeypatch.setattr(agent_worktrees, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(sys, "prefix", str(environment))
    monkeypatch.setattr(candidate, "distribution_version", lambda _name: "1.2.3")
    candidate.prepare_runtime_candidate({
        "version": "1.2.3", "frozen_plugin": str(frozen),
        "deployment_source": json.dumps({"mode": "source"}),
    })
    record = json.loads((slot / ".runtime-bootstrap" / "build-info.json").read_text())
    assert record["runtime_version"] == "1.2.3"
    assert json.loads(record["deployment_source"])["payload_fingerprint"] == (
        update_stage.fingerprint(frozen)
    )
    assert build_info.read_bytes() == b"immutable wheel member\n"
    assert (slot / ".runtime-bootstrap" / "runtime-generation").read_text().strip() == (
        record["runtime_generation"]
    )
    assert not (environment / ".runtime-bootstrap").exists()


@pytest.mark.parametrize("legacy_paths", [
    False, pytest.param(True, marks=pytest.mark.skipif(os.name != "nt", reason="Windows MAX_PATH")),
])
def test_application_preparation_retains_its_source_without_a_payload(
    tmp_path, monkeypatch, legacy_paths,
):
    import agent_worktrees
    from agent_worktrees import runtime_candidate as candidate

    slot = tmp_path / "runtime" / "versions" / ("1.2.3+" + "a" * 140 if legacy_paths else "1.2.3")
    package = slot / ".venv" / "Lib" / "site-packages" / "agent_worktrees"
    scripts = package / "_runtime_assets" / "scripts"
    scripts.mkdir(parents=True)
    source_scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in candidate._LIFECYCLE_FILES:
        shutil.copyfile(source_scripts / name, scripts / name)
    shutil.copytree(source_scripts / "installation-context", scripts / "installation-context")
    (slot / ".runtime-bootstrap").mkdir()
    (slot / "application.json").write_text("{}", encoding="utf-8")
    source = {"plugin": "agent-worktrees", "package_version": "1.2.3", "source_commit": "a" * 40}
    (slot / "runtime-source.json").write_text(json.dumps(source), encoding="utf-8")
    immutable = package / "_build_info.py"
    immutable.write_bytes(b"immutable wheel\n")
    monkeypatch.setattr(agent_worktrees, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(sys, "prefix", str(slot / ".venv"))
    monkeypatch.setattr(candidate, "distribution_version", lambda _name: "1.2.3")
    if legacy_paths:
        scan = os.scandir

        def legacy_scandir(path):
            value = os.fspath(path)
            if len(value) >= 260 and not value.startswith("\\\\?\\"):
                raise FileNotFoundError(3, "legacy Windows path limit", value)
            return scan(path)

        monkeypatch.setattr(os, "scandir", legacy_scandir)
        assert len(str(scripts / "installation-context")) > 260
    candidate.prepare_runtime_candidate()
    record = json.loads((slot / ".runtime-bootstrap" / "build-info.json").read_text())
    assert record["commit"] == source["source_commit"]
    assert json.loads(record["deployment_source"]) == source
    assert immutable.read_bytes() == b"immutable wheel\n"
    assert not (slot / "payload").exists()
    copied = slot / ".runtime-bootstrap" / "installation-context"
    for member in (source_scripts / "installation-context").glob("*.py"):
        assert (copied / member.name).read_bytes() == member.read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="Windows installation ownership")
@pytest.mark.parametrize("entry", ["install", "settle_candidate"])
def test_direct_installation_refuses_before_governance_or_construction(
    driver, tmp_path, monkeypatch, entry,
):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.delenv("UPDATE_DRIVER_WITNESS", raising=False)
    monkeypatch.setattr(driver.install_driver, "bootstrap", lambda payload: None)
    monkeypatch.setattr(
        driver, "Governance", lambda *args: pytest.fail("installation entered without driver custody"),
    )
    root = tmp_path / "absent"
    arguments = (
        ((root, driver.SCRIPTS.parent), {
            "payload": driver.SCRIPTS.parent, "uv": tmp_path / "uv", "base_python": Path(sys.executable),
        }) if entry == "install" else
        ((root, "1.2.3"), {
            "payload": driver.SCRIPTS.parent, "payload_hash": "a" * 64, "expected_governance": {},
        })
    )
    with pytest.raises(RuntimeError, match="live driver custody"):
        getattr(driver, entry)(*arguments[0], **arguments[1])
    assert not root.exists()


@pytest.mark.parametrize(("section", "field"), [
    ("state", key) for key in (
        "actualMode", "desiredMode", "status", "reason", "runtimeRoot",
        "context", "activationGeneration", "installGeneration", "allowMutation",
    )
] + [
    ("receipt", key) for key in (
        "marketplaceId", "namespaceGeneration", "generation", "pluginRoot",
    )
])
def test_governance_recheck_uses_signed_fields(driver, monkeypatch, section, field):
    governance = driver.Governance.__new__(driver.Governance)
    governance.initial = ({}, {
        key: "original" for key in (
            "marketplaceId", "namespaceGeneration", "generation", "pluginRoot",
        )
    })
    state, receipt = (dict(value) for value in governance.initial)
    monkeypatch.setattr(governance, "read", lambda: (state, receipt))
    governance.recheck()
    observed = state if section == "state" else receipt
    observed["untracked"] = "ignored"
    governance.recheck()
    observed[field] = "changed"
    diagnostic = "governance" if section == "state" else "receipt"
    with pytest.raises(driver.InstallationRefused, match=f"Installation {diagnostic} changed"):
        governance.recheck()


@pytest.fixture(scope="module")
def native_runtime_source(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[Path, str]]:
    repository = Path(__file__).resolve().parents[3]
    plugin = repository / "plugins" / "agent-worktrees"
    owned = tmp_path_factory.mktemp("native-runtime-source")
    yield from _frozen_native_runtime_source(plugin, owned)


def _frozen_native_runtime_source(plugin: Path, owned: Path) -> Iterator[tuple[Path, str]]:
    repository = Path(__file__).resolve().parents[3]
    root = owned / "repository"
    with pytest.MonkeyPatch.context() as patch:
        patch.syspath_prepend(str(repository / "plugins" / "agent-worktrees" / "scripts"))
        spec = importlib.util.spec_from_file_location(
            "backend_fixture_engine", repository / "libs/installer-engine/installer_engine.py",
        )
        assert spec and spec.loader
        engine = importlib.util.module_from_spec(spec)
        patch.setitem(sys.modules, spec.name, engine)
        spec.loader.exec_module(engine)
        frozen = engine.freeze_inputs(plugin, root / "source")
    marketplace = root / ".github" / "plugin" / "marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(json.dumps({
        "name": "runtime-fixture",
        "plugins": [{"name": "agent-worktrees", "source": frozen.plugin.relative_to(root).as_posix()}],
    }), encoding="utf-8")
    hooks = owned / "empty-hooks"
    hooks.mkdir()
    git = shutil.which("git")
    assert git is not None, "real provenance qualification requires native Git"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    options = [
        git, "-C", str(root), "-c", f"core.hooksPath={hooks}",
        "-c", "core.autocrlf=false", "-c", "core.longpaths=true",
        "-c", "commit.gpgsign=false",
        "-c", "user.name=Runtime test", "-c", "user.email=runtime-test@example.invalid",
    ]

    def run(*arguments: str) -> str:
        return subprocess.check_output(
            [*options, *arguments], env=environment, text=True, **no_window_kwargs(),
        ).strip()

    run("init", "-q", "--template", str(hooks))
    # Preserve literal copied inputs even if a package carries text/filter attributes.
    (root / ".git/info").mkdir(exist_ok=True)
    (root / ".git/info/attributes").write_text(
        "* -text -filter -ident -working-tree-encoding\n", encoding="utf-8",
    )
    run("add", "--all", "--force", ".")
    run("commit", "-qm", "Frozen runtime dependency fixture")
    head = run("rev-parse", "HEAD")
    assert run("status", "--porcelain") == ""
    assert engine.runtime.fingerprint_source(engine._source_files(frozen.packages)) == frozen.payload_hash
    yield frozen.plugin, head
    assert engine.runtime.fingerprint_source(engine._source_files(frozen.packages)) == frozen.payload_hash
    assert run("rev-parse", "HEAD") == head
    assert run("status", "--porcelain", "--untracked-files=no") == ""


@pytest.mark.parametrize("vendored", [False, True])
def test_native_runtime_source_keeps_repository_metadata_outside_frozen_packages(
    tmp_path, vendored,
):
    plugin = tmp_path / "source" / "plugins" / "fixture"
    dependency = (
        plugin / "_vendor" / "dependency" if vendored
        else tmp_path / "source" / "libs" / "dependency"
    )
    plugin.mkdir(parents=True)
    dependency.mkdir(parents=True)
    dependency_path = "_vendor/dependency" if vendored else "../../libs/dependency"
    (plugin / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "1.0.0"\n'
        '[tool.uv.sources]\n'
        f'dependency = {{ path = "{dependency_path}" }}\n',
        encoding="utf-8",
    )
    (dependency / "pyproject.toml").write_text(
        '[project]\nname = "dependency"\nversion = "1.0.0"\n', encoding="utf-8",
    )
    owned = tmp_path / "fixture"
    owned.mkdir()
    with contextmanager(_frozen_native_runtime_source)(plugin, owned) as (frozen_plugin, head):
        assert (frozen_plugin / "pyproject.toml").read_bytes() == (
            plugin / "pyproject.toml"
        ).read_bytes()
        assert head


def _slot(driver, root, version):
    slot = root / "versions" / version
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"interpreter")
    driver.runtime.mark_complete(root, version, payload_hash="previous")
    return slot


@pytest.mark.parametrize("tampered", [False, True])
@pytest.mark.parametrize("deployment", ["source", "closed-wheels", "repair"])
def test_source_deployment_uses_exact_verified_snapshot_before_construction(
    driver, tmp_path, monkeypatch, tampered, deployment,
):
    from types import SimpleNamespace

    root, plugin = tmp_path / "runtime", tmp_path / "payload"
    plugin.mkdir()
    (plugin / "plugin.json").write_text(
        '{"name":"agent-worktrees","version":"1.0.0"}', encoding="utf-8",
    )
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="agent-worktrees"\nversion="1.0.0"\n', encoding="utf-8",
    )
    source = driver.runtime_source
    digest = source.assembly_hash(plugin)
    identity = source.SourceIdentity(
        "agent-worktrees", "1.0.0", "a" * 40, "b" * 40, digest,
        source.runtime_version("1.0.0", "a" * 40, digest),
    )
    (plugin / source.SOURCE_FILE).write_text(json.dumps(identity.document()), encoding="utf-8")
    if tampered:
        (plugin / "changed.py").write_text("VALUE = 2\n", encoding="utf-8")

    @contextmanager
    def held(**_kwargs):
        yield

    monkeypatch.setattr(driver, "Governance", lambda requested, payload: SimpleNamespace(
        root=requested, payload=payload, held=held,
    ))
    provenance = {}

    def attribute(payload, version, selected):
        assert payload == plugin and version == "1.0.0" and selected == identity
        return provenance

    class ConstructionReached(Exception):
        pass

    wheels = None

    def construct(requested, version, **kwargs):
        assert not tampered
        expected = identity.runtime_version
        if wheels is not None:
            expected += ".profile." + wheels.receipt()["lock_id"][7:23]
            assert kwargs["wheel_inputs"] is wheels
            assert kwargs["local_packages"] == ("agent-worktrees",)
        if deployment == "repair":
            assert version.startswith(expected + ".repair.") and version != expected
        else:
            assert version == expected
        assert requested == root
        snapshot = root / "snapshots" / identity.runtime_version
        assert kwargs["plugin"] == snapshot
        assert source.read_source_identity(snapshot, "1.0.0") == identity
        assert kwargs["reuse_generations"] is False
        assert kwargs["construction_data"] is provenance
        raise ConstructionReached

    monkeypatch.setattr(driver, "_source_provenance", attribute)
    monkeypatch.setattr(driver.engine, "numbered_candidate", construct)
    error = source.SourceIdentityError if tampered else ConstructionReached
    with ExitStack() as custody:
        options = {}
        if deployment != "source":
            from installer_wheel_snapshot import retain_wheel_inputs

            archive = tmp_path / "agent_worktrees-1.0.0-py3-none-any.whl"
            archive.write_bytes(b"retained upstream-inspected wheel fixture")
            digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
            retained = custody.enter_context(retain_wheel_inputs(
                {archive.name: archive}, {archive.name: digest},
                max_wheels=2, max_archive_bytes=1024, max_total_bytes=1024,
                max_snapshot_bytes=4096, deadline=time.monotonic() + 30,
            ))
            profile = {
                "component": "agent-worktrees", "mode": "source",
                "release": {
                    "identity": "source:" + identity.source_commit,
                    "sha256": "sha256:" + identity.assembly_hash,
                },
            }
            wheels = driver.engine.WheelInstallInputs(
                retained, json.dumps(profile, sort_keys=True, separators=(",", ":")),
                (("agent-worktrees", "1.0.0", archive.name, digest),),
            )
            options = {"wheel_inputs": wheels, "repair": deployment == "repair"}
        with pytest.raises(error):
            driver.install(
                root, plugin, payload=plugin, uv=tmp_path / "unused",
                base_python=Path(sys.executable), **options,
            )
    if tampered:
        assert not root.exists()


@pytest.mark.parametrize("mode", ["ordinary", "repair", "dev"])
@pytest.mark.parametrize("input_timeout", [None, 1200])
@pytest.mark.parametrize("expired", [False, True])
def test_install_source_cli_retains_inputs_through_native_owner_return(
    driver, tmp_path, monkeypatch, mode, input_timeout, expired,
):
    from types import SimpleNamespace
    import install_source
    import installer_wheel_inputs as guards

    snapshot = tmp_path / "snapshot"
    plugin = tmp_path / "plugin"
    identity = SimpleNamespace(package_version="1.0.0")
    selected = SimpleNamespace(payload=plugin, identity=identity)
    wheels = object()
    held = []
    now = [10.0]
    clock_reads = []
    budget = 600 if input_timeout is None else input_timeout

    def clock():
        clock_reads.append(now[0])
        return now[0]

    monkeypatch.setattr(driver, "time", SimpleNamespace(monotonic=clock))
    monkeypatch.setattr(guards, "time", SimpleNamespace(monotonic=lambda: now[0]))

    @contextmanager
    def inputs(path, component, *, deadline):
        assert path == snapshot and component == "agent-worktrees" and mode != "dev"
        assert deadline == 10.0 + budget
        guards._check_deadline(deadline)
        held.append(True)
        try:
            yield selected, wheels
        finally:
            held.pop()
        guards._check_deadline(deadline)

    def install(root, selected_plugin, **options):
        assert held == [True] and selected_plugin == plugin
        assert options["wheel_inputs"] is wheels and options["repair"] == (mode == "repair")
        if expired:
            now[0] = 10.0 + budget
        return {"runtime_version": "selected-generation"}

    monkeypatch.setattr(install_source, "project_inputs", inputs)
    monkeypatch.setattr(driver.runtime_source, "read_source_identity", lambda *a, **kw: identity)
    monkeypatch.setattr(driver, "install", install)
    result = tmp_path / "result.json"
    arguments = [
        "--root", str(tmp_path / "root"), "--plugin", str(plugin),
        "--payload", str(plugin), "--uv", str(tmp_path / "uv"),
        "--base-python", sys.executable, "--result", str(result),
        "--install-source", str(snapshot), "--mode", mode,
    ]
    if input_timeout is not None:
        arguments += ["--input-timeout", str(input_timeout)]
    if mode == "dev":
        with pytest.raises(SystemExit) as refusal:
            driver.main(arguments)
        assert refusal.value.code == 2 and not result.exists()
    elif expired:
        with pytest.raises(guards.WheelDeadlineError):
            driver.main(arguments)
        assert not result.exists()
    else:
        assert driver.main(arguments) == 0
        assert json.loads(result.read_text()) == {"runtime_version": "selected-generation"}
    assert clock_reads == ([] if mode == "dev" else [10.0])
    assert not held


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "-inf"])
def test_install_source_cli_refuses_invalid_input_timeout(driver, tmp_path, value, capsys):
    with pytest.raises(SystemExit) as failure:
        driver.main(["--input-timeout=" + value])
    assert failure.value.code == 2
    assert "positive and finite" in capsys.readouterr().err
    assert not (tmp_path / "runtime").exists()


def test_install_source_cli_rejects_input_timeout_without_source(
    driver, tmp_path, monkeypatch, capsys,
):
    from unittest.mock import Mock

    install = Mock(return_value={})
    monkeypatch.setattr(driver, "install", install)
    result = tmp_path / "result.json"
    with pytest.raises(SystemExit) as failure:
        driver.main([
            "--root", str(tmp_path / "runtime"), "--plugin", str(tmp_path / "plugin"),
            "--payload", str(tmp_path / "payload"), "--uv", str(tmp_path / "uv"),
            "--base-python", sys.executable, "--result", str(result),
            "--input-timeout", "1200",
        ])
    assert failure.value.code == 2
    assert "--input-timeout requires --install-source" in capsys.readouterr().err
    install.assert_not_called()
    assert not result.exists()


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell parameter validation")
@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_native_frontend_refuses_nonfinite_input_timeout_before_admission(tmp_path, value):
    shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if shell is None:
        pytest.skip("native PowerShell is unavailable")
    root = tmp_path / "runtime"
    script = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(script), "repair",
         "-InstallDir", str(root), "-InstallSource", str(tmp_path / "snapshot"),
         "-InputTimeout", value],
        capture_output=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode != 0 and b"InputTimeout" in result.stderr
    assert not root.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows installer argument admission")
@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
def test_native_frontend_refuses_source_inputs_for_status_before_touching_installation(
    tmp_path, shell,
):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} is unavailable")
    root = tmp_path / "untouched-installation"
    script = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(script), "status",
         "-InstallDir", str(root), "-InstallSource", str(tmp_path / "absent-source")],
        capture_output=True, text=True, timeout=20, check=False, **no_window_kwargs(),
    )
    assert result.returncode != 0 and "independent repair" in result.stderr
    assert not root.exists()


@pytest.mark.parametrize("source_backed", [False, True])
@pytest.mark.parametrize("recover_manager", [False, True])
def test_source_health_failure_routes_to_canonical_distinct_repair_after_custody(
    driver, tmp_path, monkeypatch, source_backed, recover_manager,
):
    from types import SimpleNamespace

    root, plugin, owner = (tmp_path / name for name in ("runtime", "payload", "owner"))
    plugin.mkdir()
    (plugin / "pyproject.toml").write_text('[project]\nname="agent-worktrees"\nversion="1.0.0"\n')
    source = driver.runtime_source
    identity = source.SourceIdentity(
        "agent-worktrees", "1.0.0", "a" * 40, "b" * 40, "c" * 64,
        source.runtime_version("1.0.0", "a" * 40, "c" * 64),
    ) if source_backed else None
    calls = []

    @contextmanager
    def held(**kwargs):
        calls.append("held")
        try:
            yield
        finally:
            calls.append("released")

    monkeypatch.setattr(driver, "Governance", lambda *a: SimpleNamespace(
        root=root, payload=owner, held=held,
    ))
    monkeypatch.setattr(source, "read_source_identity", lambda *a, **k: identity)
    monkeypatch.setattr(source, "publish_source_snapshot", lambda *a: plugin)
    monkeypatch.setattr(driver, "_source_provenance", lambda *a: {})
    monkeypatch.setattr(driver, "_selected", lambda *a: None)

    def construct(*args, **kwargs):
        raise driver.engine.HealthRepairRequired("controlled installed-health failure")

    def repair(requested, selected, **kwargs):
        assert calls == ["held", "released"]
        assert requested == root and selected == plugin
        assert kwargs["payload"] == owner and kwargs["mode"] == "repair"
        assert kwargs["expected_source_identity"] == source.manifest_fields(identity)
        assert kwargs["recover_adopted_manager"] is recover_manager
        return {"runtime_version": "distinct-repair"}

    monkeypatch.setattr(driver.engine, "numbered_candidate", construct)
    monkeypatch.setitem(sys.modules, "runtime_install_modes", SimpleNamespace(run=repair))
    arguments = dict(
        payload=owner, uv=tmp_path / "uv", base_python=Path(sys.executable),
        recover_adopted_manager=recover_manager,
    )
    if source_backed:
        assert driver.install(root, plugin, **arguments) == {"runtime_version": "distinct-repair"}
    else:
        with pytest.raises(driver.engine.HealthRepairRequired):
            driver.install(root, plugin, **arguments)


@pytest.mark.parametrize("rollback,interrupted,activation_failed,special", [
    (False, False, False, None), (True, False, False, None), (False, True, False, None),
    (True, True, False, None), (False, False, True, None),
    (False, False, False, "deferred"), (True, False, False, "committed-rollback"),
    (False, False, False, "outgoing-identity-unavailable"),
    (False, False, False, "outgoing-identity-unavailable-already-serving"),
    (False, False, False, "prepublication-interrupted"),
])
def test_one_owner_retains_candidate_and_rollback_until_actual_cutover(
    driver, tmp_path, monkeypatch, rollback, interrupted, activation_failed, special,
):
    from agent_worktrees import config as cfg, status_monitor_cutover as cutover

    root = tmp_path / "runtime"
    previous = _slot(driver, root, "1.0.0")
    driver.runtime.activate(root, "1.0.0", link_free=True)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "scripts").mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(driver.SCRIPTS / name, plugin / "scripts" / name)
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="agent-worktrees"\nversion="2.0.0"\n', encoding="utf-8",
    )
    authority = []

    class Governance:
        def __init__(self, requested, payload):
            self.root, self.payload = requested, payload
            self.manifest_publication = nullcontext

        def signature(self):
            return {}

        def recheck(self):
            pass

        @contextmanager
        def held(self, *, timeout=180):
            authority.append("enter")
            try:
                yield
            finally:
                authority.append("leave")

    class Guard:
        def release(self):
            authority.append("cutover-released")

    def construct(inputs, slot, *_args):
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True)
        python.write_bytes(b"interpreter")
        return python

    def actual_transition(**arguments):
        assert arguments["cutover_guard"] is guard
        assert arguments["asynchronous_retirement"] is True
        assert authority == ["enter", "leave", "enter"]
        assert driver.runtime.current_version(root) == "2.0.0"
        assert driver.runtime.gc(root) == []
        for version in ("1.0.0", "2.0.0"):
            with pytest.raises(driver.runtime.RetentionTimeout):
                with driver.runtime._kernel_lease(root, version, kind="retention", shared=False, timeout=0):
                    pytest.fail("rollback/candidate retention was released")
        with driver.runtime.construction_lease(root, "2.0.0", timeout=0):
            pass
        if interrupted or special == "prepublication-interrupted":
            raise RuntimeError("cutover interrupted")
        if special == "committed-rollback":
            from zdd import breadcrumb

            breadcrumb.write_breadcrumb(root / "status-monitor-routing", state="committed", self_retirement=True)
            return {"action": "cutover", "result": {"ok": False, "publication_uncertain": True}}
        return {"action": "cutover", "result": {
            "ok": not rollback and not activation_failed,
            "committed": not rollback, "rolled_back": rollback,
            "error": "promotion response failed" if activation_failed else None,
        }}

    guard = Guard()

    class Process:
        returncode = 0

        def __init__(self, argv, **_kwargs):
            def argument(name):
                return argv[argv.index(name) + 1]

            try:
                result = driver.settle_candidate(
                    root, argument("--settle-slot"), payload=plugin,
                    payload_hash=argument("--payload-hash"), expected_governance={},
                    previous=argument("--previous-version"),
                )
            except RuntimeError:
                self.returncode = 1
            else:
                Path(argument("--result")).write_text(json.dumps(result), encoding="utf-8")

        def communicate(self):
            return "", ""

    monkeypatch.setattr(driver, "Governance", Governance)
    monkeypatch.setattr(driver, "_source_provenance", lambda *_args: {})
    monkeypatch.setattr(driver.engine, "_construct", construct)
    monkeypatch.setattr(
        driver.engine, "_retained_inputs", lambda inputs, *_args, **_kwargs: nullcontext(inputs),
    )
    monkeypatch.setattr(
        driver.engine, "retained_source_wheels", lambda inputs, *_args, **_kwargs: nullcontext(inputs),
    )
    monkeypatch.setattr(driver.engine, "_prepare_installed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(driver.engine, "_verify_installed", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(driver, "_load_installed", lambda _slot: None)
    def adopt(requested, slot):
        pytest.fail("normal construction must not adopt a Task")

    monkeypatch.setattr(driver.install_driver, "adopt_broker", adopt)
    if special in {"outgoing-identity-unavailable", "outgoing-identity-unavailable-already-serving"}:
        def unavailable(*_args, **_kwargs):
            raise ValueError("Routed monitor process identity is unconfirmed")

        monkeypatch.setattr(cutover, "routed_monitor_identity", unavailable)
    else:
        monkeypatch.setattr(driver, "_monitor_identity", lambda _root: None)
    monkeypatch.setattr(
        driver, "_candidate_serving",
        lambda *_args: special == "outgoing-identity-unavailable-already-serving",
    )
    monkeypatch.setattr(cfg, "install_dir", lambda: root)
    monkeypatch.setattr(cutover, "_acquire_cutover_lock", lambda _root: guard)
    monkeypatch.setattr(cutover, "activate_after_update", actual_transition)
    if special == "deferred":
        from agent_worktrees.runtime_monitor_settlement import PublicationOutcomePending

        def deferred(*args):
            raise PublicationOutcomePending("retained unknown publication")

        monkeypatch.setattr(driver, "_settle_monitor", deferred)
    elif special == "committed-rollback":
        from agent_worktrees import runtime_monitor_recovery
        from zdd import breadcrumb

        def rolled_back(*args):
            breadcrumb.clear_breadcrumb(root / "status-monitor-routing")
            return "rolled_back"

        monkeypatch.setattr(runtime_monitor_recovery, "recover_owned_publication", rolled_back)
    elif special == "prepublication-interrupted":
        settlement_actions = []

        def prepublication(*args):
            settlement_actions.append(args[2]["action"])
            return False

        monkeypatch.setattr(driver, "_settle_monitor", prepublication)
    else:
        monkeypatch.setattr(driver, "_settle_monitor", lambda *_args: not rollback)
    monkeypatch.setattr(driver.subprocess, "Popen", Process)
    if rollback or interrupted or activation_failed or special in {"deferred", "prepublication-interrupted"}:
        with pytest.raises(subprocess.CalledProcessError):
            driver.install(root, plugin, payload=plugin, uv=tmp_path / "unused", base_python=Path(sys.executable))
        expected = "1.0.0" if rollback or special == "prepublication-interrupted" else "2.0.0"
        assert driver.runtime.current_version(root) == expected
        assert driver.runtime.read_last_known_good(root) == expected
        if special == "deferred":
            from agent_worktrees import runtime_deployment

            assert runtime_deployment.read_manifest(root) is None
    else:
        result = driver.install(root, plugin, payload=plugin, uv=tmp_path / "unused", base_python=Path(sys.executable))
        assert result["runtime_version"] == "2.0.0"
        assert driver.runtime.current_version(root) == "2.0.0"
        if special in {"outgoing-identity-unavailable", "outgoing-identity-unavailable-already-serving"}:
            assert result["retirement_pending"] is True
    assert previous.is_dir()
    if special == "prepublication-interrupted":
        assert settlement_actions == ["preparing"]
    assert authority == ["enter", "leave", "enter", "cutover-released", "leave"]
    for version in ("1.0.0", "2.0.0"):
        with driver.runtime._kernel_lease(root, version, kind="retention", shared=False, timeout=0):
            pass


def test_failed_passive_exit_is_observed_before_returning(driver, tmp_path, monkeypatch):
    events = []

    class Handle:
        pid = 123

        def poll(self):
            events.append("poll")
            return None if len(events) == 1 else 0

    monkeypatch.setattr(driver.time, "sleep", lambda _duration: events.append("wait"))
    assert driver._settle_monitor(
        tmp_path, tmp_path / "candidate",
        {"action": "cutover", "result": {"rolled_back": True}}, None, [Handle()],
    ) is False
    assert events == ["poll", "wait", "poll"]

def test_ready_successor_does_not_wait_for_predecessor_exit(driver, tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "_candidate_serving", lambda *_args: True)
    monkeypatch.setattr(
        driver.time, "sleep", lambda *_args: pytest.fail("installer waited for drain"),
    )
    assert driver._settle_monitor(
        tmp_path, tmp_path / "candidate",
        {"action": "cutover", "result": {"committed": True, "retirement_pending": True}},
        (123, "original-birth"), [],
    )


def test_failed_committed_activation_settles_without_waiting_for_readiness(
    driver, tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        driver, "_candidate_serving", lambda *_args: pytest.fail("failed commitment waited for readiness"),
    )
    assert driver._settle_monitor(
        tmp_path, tmp_path / "candidate",
        {"action": "cutover", "result": {
            "committed": True, "ok": False, "error": "roster activation failed",
        }},
        (123, "1001"), [],
    )


def test_committed_successor_death_never_rolls_back_retirement_custody(driver, tmp_path, monkeypatch):
    monkeypatch.setattr(driver, "_candidate_serving", lambda *a: False)
    summary = {"action": "cutover", "result": {"committed": True, "ok": True}}
    assert driver._settle_monitor(tmp_path, tmp_path / "candidate", summary, None, []) is True
    assert summary["result"]["committed"] and not summary["result"]["ok"]
    assert "stopped serving" in summary["result"]["error"]


def test_predecessor_qualification_prefers_route_and_refuses_unrouted_live_owner(
    driver, tmp_path, monkeypatch,
):
    from agent_worktrees import locks, status_monitor_cutover as cutover

    monkeypatch.setattr(locks, "read_lock", lambda _path: {"pid": 101})
    monkeypatch.setattr(locks, "lock_is_live", lambda _record: True)

    def routed(root, *, require_async_retirement=False):
        assert root == tmp_path and require_async_retirement
        return (202, "2002", "current")

    monkeypatch.setattr(cutover, "routed_monitor_identity", routed)
    assert driver._monitor_identity(tmp_path) == (202, "2002")
    monkeypatch.setattr(cutover, "routed_monitor_identity", lambda *args, **kwargs: None)
    with pytest.raises(driver.InstallationRefused, match="unrouted"):
        driver._monitor_identity(tmp_path)


@pytest.mark.parametrize("exit_confirmed", [False, True])
def test_serving_incoming_monitor_does_not_wait_for_failed_predecessor_recovery(
    driver, tmp_path, monkeypatch, capsys, exit_confirmed,
):
    monkeypatch.setattr(driver, "_candidate_serving", lambda *_args: True)
    monkeypatch.setattr(driver, "_monitor_identity", lambda *_args: (222, "2020"))
    from agent_worktrees import runtime_monitor_recovery

    monkeypatch.setattr(
        runtime_monitor_recovery, "recover_owned_publication",
        lambda *a, **k: pytest.fail("recovered predecessor after successor commit"),
    )
    assert driver._settle_monitor(
        tmp_path, tmp_path / "candidate",
        {"action": "cutover", "result": {
            "committed": True, "predecessor_cleanup": {"exit_confirmed": exit_confirmed},
        }}, (111, "1010"), [],
    ) is True
    assert ("cleanup remains unconfirmed" in capsys.readouterr().err) is not exit_confirmed


@pytest.mark.parametrize("wire", ["legacy", "timeout"])
def test_async_predecessor_admission_retains_slot_without_old_wire_contract(
    driver, tmp_path, monkeypatch, wire,
):
    from types import SimpleNamespace
    from agent_worktrees import locks, status_monitor_cutover as cutover
    from zdd import diagnostics, process_exit, routing

    slot = tmp_path / "versions" / "legacy"
    (slot / ".venv").mkdir(parents=True)
    monkeypatch.setattr(locks, "process_start_time", lambda pid: "1001")
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "1001")
    monkeypatch.setattr(diagnostics, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(process_exit, "observe_original_exit", lambda pid, start: False)
    monkeypatch.setattr(locks, "read_lock", lambda path: {
        "pid": 101, "start_time": "1001", "prefix": str(slot / ".venv"),
    })
    monkeypatch.setattr(locks, "lock_is_live", lambda record: True)
    monkeypatch.setattr(cutover, "load_or_create_control_token", lambda root: "fixture-token")
    routing.publish_active(
        tmp_path / "status-monitor-routing", bind="127.0.0.1", port=61001, pid=101,
        process_start_time="1001",
    )

    def health():
        if wire == "timeout":
            raise TimeoutError("old control unavailable")
        return {"pid": 101, "status": "ready", "capabilities": []}

    monkeypatch.setattr(cutover, "ControlClient", lambda *a, **k: SimpleNamespace(health=health))
    predecessor = driver._monitor_identity(tmp_path)
    assert predecessor == (101, "1001")
    assert driver._monitor_slot(tmp_path, predecessor) == "legacy"


@pytest.mark.parametrize("failure_point", ["before", "current", "both"])
@pytest.mark.parametrize("monitor_scope,committed", [(False, False), (True, False), (True, True)])
def test_publication_failure_retains_custody_through_observed_outcome(
    driver, tmp_path, monkeypatch, failure_point, monitor_scope, committed,
):
    from agent_worktrees import config as cfg, status_monitor_cutover as cutover

    root = tmp_path / "runtime"
    _slot(driver, root, "1.0.0")
    _slot(driver, root, "2.0.0")
    _slot(driver, root, "0.9.0")
    driver.runtime.activate(root, "1.0.0", link_free=True)
    snapshot = driver._selector_snapshot(root)
    events = []
    failure = OSError("publication storage failure")
    activate = driver.runtime.activate
    restore = driver._restore_selectors

    class Governance:
        def __init__(self, requested, payload):
            self.root, self.payload = requested, payload
            self.manifest_publication = nullcontext

        def signature(self):
            return {}

        def recheck(self):
            pass

        @contextmanager
        def held(self, *, timeout=180):
            events.append("governance-held")
            try:
                yield
            finally:
                events.append("governance-released")

    class Guard:
        def release(self):
            events.append("cutover-released")

    def assert_custody():
        assert events[0] == "governance-held"
        assert "governance-released" not in events
        assert "cutover-released" not in events
        versions = ("0.9.0", "1.0.0", "2.0.0") if monitor_scope else ("1.0.0", "2.0.0")
        for version in versions:
            with pytest.raises(driver.runtime.RetentionTimeout):
                with driver.runtime._kernel_lease(root, version, kind="retention", shared=False, timeout=0):
                    pytest.fail("publication failure released runtime custody")

    def fail_publication(*args, **kwargs):
        assert_custody()
        if "publication-failed" in events:
            return activate(*args, **kwargs)
        events.append("publication-failed")
        if failure_point == "current":
            (root / "current-version").write_text("2.0.0\n", encoding="utf-8")
        elif failure_point == "both":
            activate(*args, **kwargs)
        raise failure

    def settle(*_args):
        assert_custody()
        events.append("outcome-observed")
        return committed

    def retry_restore(*args):
        assert_custody()
        events.append("restore")
        if events.count("restore") == 1:
            raise OSError("transient rollback storage failure")
        restore(*args)

    monkeypatch.setattr(driver, "Governance", Governance)
    monkeypatch.setattr(driver, "_load_installed", lambda _slot: None)
    monkeypatch.setattr(cfg, "install_dir", lambda: root if monitor_scope else tmp_path / "other")
    monkeypatch.setattr(cutover, "_acquire_cutover_lock", lambda _root: Guard())
    monkeypatch.setattr(driver, "_monitor_identity", lambda _root: (123, "1001"))
    monkeypatch.setattr(driver, "_monitor_slot", lambda *_args: "0.9.0")
    monkeypatch.setattr(driver, "_candidate_serving", lambda *_args: False)
    monkeypatch.setattr(driver, "_settle_monitor", settle)
    monkeypatch.setattr(driver.runtime, "activate", fail_publication)
    monkeypatch.setattr(driver, "_restore_selectors", retry_restore)
    monkeypatch.setattr(driver.time, "sleep", lambda _duration: None)
    with pytest.raises(OSError) as raised:
        driver.settle_candidate(
            root, "2.0.0", payload=tmp_path, payload_hash="previous",
            expected_governance={}, previous="1.0.0",
        )
    assert raised.value is failure
    assert events[-2:] == ["cutover-released", "governance-released"]
    if committed:
        assert events.count("outcome-observed") == 1
        assert "restore" not in events
        assert driver.runtime.current_version(root) == "2.0.0"
        assert driver.runtime.read_last_known_good(root) == "2.0.0"
    else:
        assert events.count("restore") == 2
        assert driver._selector_snapshot(root) == snapshot
    for version in ("0.9.0", "1.0.0", "2.0.0"):
        with driver.runtime._kernel_lease(root, version, kind="retention", shared=False, timeout=0):
            pass


@pytest.mark.parametrize("summary", [
    {"action": "cutover", "result": {"committed": True}},
    {"action": "restart", "restart": {"spawned": True}},
])
def test_reported_commit_or_restart_cannot_hide_actual_child_failure(
    driver, tmp_path, monkeypatch, summary,
):
    class Handle:
        def poll(self):
            return 1

    monkeypatch.setattr(driver, "_candidate_serving", lambda *_args: False)
    committed = summary["action"] == "cutover"
    assert driver._settle_monitor(tmp_path, tmp_path / "candidate", summary, None, [Handle()]) is committed
    if committed:
        assert summary["result"]["ok"] is False
        assert "stopped serving" in summary["result"]["error"]


def test_uncertain_publication_requires_terminal_owner_witness(driver, tmp_path, monkeypatch, capsys):
    from zdd import breadcrumb

    records = iter([None, {"state": "unrecognized"}, {"state": "rolled_back"}])
    waits = []
    monkeypatch.setattr(breadcrumb, "read_breadcrumb", lambda _root: next(records))
    monkeypatch.setattr(driver.time, "sleep", waits.append)
    monkeypatch.setattr(driver, "_candidate_serving", lambda *_args: False)
    assert driver._settle_monitor(
        tmp_path, tmp_path / "candidate",
        {"action": "cutover", "result": {"publication_uncertain": True}}, None, [],
    ) is False
    assert waits == [1, 1]
    assert capsys.readouterr().err.count("publication is uncertain") == 1


@pytest.mark.parametrize("outcome", ["committed", "rolled_back"])
def test_owned_terminal_outcome_is_consumed_without_rereading_a_cleared_witness(
    driver, tmp_path, monkeypatch, outcome,
):
    from zdd import breadcrumb
    from agent_worktrees import runtime_monitor_recovery

    record = {"state": "started", "self_retirement": True}
    reads = []
    def witness(_root):
        reads.append(True)
        assert len(reads) == 1, "settlement reread a witness consumed by owned recovery"
        return record

    monkeypatch.setattr(breadcrumb, "read_breadcrumb", witness)
    monkeypatch.setattr(runtime_monitor_recovery, "recover_owned_publication", lambda *a: outcome)
    monkeypatch.setattr(driver, "_candidate_serving", lambda *a: True)
    summary = {"action": "cutover", "result": {"publication_uncertain": True, "ok": False, "error": "uncertain"}}
    assert driver._settle_monitor(tmp_path, tmp_path / "candidate", summary, None, []) is (
        outcome == "committed"
    )
    assert summary["result"]["publication_uncertain"] is False
    if outcome == "committed":
        assert summary["result"]["ok"] is True and summary["result"]["error"] is None


@pytest.mark.parametrize("ready", [True, False])
@pytest.mark.parametrize("grant_error", [False, True])
def test_interrupted_marked_committed_witness_is_never_rolled_back(
    driver, tmp_path, monkeypatch, ready, grant_error,
):
    from zdd import breadcrumb
    from agent_worktrees import runtime_monitor_recovery

    record = {"state": "committed", "self_retirement": True}
    monkeypatch.setattr(breadcrumb, "read_breadcrumb", lambda path: record)
    calls = []
    def recover(*args):
        calls.append(True)
        if grant_error:
            raise RuntimeError("retirement grant changed")
        return "committed"

    monkeypatch.setattr(runtime_monitor_recovery, "recover_owned_publication", recover)
    monkeypatch.setattr(driver, "_candidate_serving", lambda *args: ready)
    monkeypatch.setattr(driver.time, "sleep", lambda delay: pytest.fail("committed recovery must not retry under installer guards"))
    summary = {"action": "interrupted"}
    assert driver._settle_monitor(tmp_path, tmp_path / "candidate", summary, None, []) is True
    assert calls == [True]
    assert summary["result"]["committed"] is True
    assert summary["result"]["ok"] is (ready and not grant_error)
    if grant_error:
        assert "retirement grant changed" in summary["result"]["error"]
        assert breadcrumb.read_breadcrumb(tmp_path) is record


def test_restart_spawn_returns_the_real_process_handle_to_its_owner(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from agent_worktrees import status_monitor_cutover, status_monitor_runtime as smr

    handle = object()
    handles = []
    monkeypatch.setattr(smr, "_core_helper", lambda _name, local: local)
    monkeypatch.setattr(smr, "_core", lambda: SimpleNamespace(windowless_daemon_kwargs=lambda **_kwargs: {}))
    monkeypatch.setattr(smr, "_daemon_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(smr, "_daemon_environment", lambda: {})
    monkeypatch.setattr(smr, "direct_monitor_python", lambda python: (python, {}))
    monkeypatch.setattr(status_monitor_cutover, "spawn_monitor", lambda *_args, **_kwargs: handle)
    assert smr._spawn_detached(["candidate-python", "-m", "agent_worktrees"], spawned_handles=handles)
    assert handles == [handle]


@pytest.mark.parametrize("legacy_lock", ["absent", "stale"])
@pytest.mark.parametrize("slot", ["serving", None, "..", "missing"])
@pytest.mark.parametrize("nested", [False, True])
def test_routed_predecessor_is_captured_before_runtime_retention(driver, tmp_path, legacy_lock, slot, nested):
    from agent_worktrees import locks, status_monitor_cutover as cutover
    from agent_worktrees.runtime_monitor_retirements import CAPABILITY
    from agent_worktrees.status_monitor_recovery_contract import CAPABILITY as RECOVERY
    from zdd import routing

    root = tmp_path / "runtime"
    (root / "versions" / "serving").mkdir(parents=True)
    if nested:
        (root / "versions" / "serving" / ".venv").mkdir()
    (root / "status-monitor-control.token").write_text("fixture-token", encoding="utf-8")
    if legacy_lock == "stale":
        (root / "status-monitor.lock").write_text(json.dumps({"pid": 0}), encoding="utf-8")
    pid = os.getpid()
    prefix = root / "versions" / slot if isinstance(slot, str) else None
    if nested and prefix is not None:
        prefix = prefix / ".venv"
    server = cutover.ControlServer(
        lambda *_args: {
            "pid": pid, "status": "ready", "runtime_slot": slot,
            "runtime_prefix": str(prefix) if prefix is not None else None,
            "capabilities": [CAPABILITY, RECOVERY], "runtime_generation": "a" * 32,
        },
        token="fixture-token",
    )
    server.start()
    try:
        routing.publish_active(
            root / "status-monitor-routing", bind="127.0.0.1", port=server.port, pid=pid,
        )
        if slot == "serving":
            identity = driver._monitor_identity(root)
            assert identity == (pid, locks.process_start_time(pid))
            assert driver._monitor_slot(root, identity) == "serving"
        else:
            with pytest.raises(driver.InstallationRefused):
                identity = driver._monitor_identity(root)
                driver._monitor_slot(root, identity)
    finally:
        server.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows retained process object semantics")
def test_native_exit_evidence_does_not_mistake_a_retained_handle_for_a_live_process():
    from zdd import diagnostics

    process = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", "import time; time.sleep(2)"],
        stdin=subprocess.DEVNULL, **no_window_kwargs(),
    )
    try:
        assert diagnostics._pid_alive(process.pid)
        assert process.wait(timeout=10) == 0
        assert not diagnostics._pid_alive(process.pid)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


@pytest.mark.parametrize("handle,wait,error,alive", [
    (None, 0, 87, False), (None, 0, 5, True),
    (123, 0, 0, False), (123, 0x102, 0, True), (123, 0xFFFFFFFF, 0, True),
])
def test_native_exit_signal_preserves_ambiguous_process_liveness(
    monkeypatch, handle, wait, error, alive,
):
    import ctypes
    from types import SimpleNamespace
    from zdd import diagnostics

    closed = []

    def open_process(access, inherit, pid):
        assert access & 0x00100000
        assert pid == 202 and inherit is False
        return handle

    def wait_process(observed, timeout):
        assert observed == handle and timeout == 0
        return wait

    kernel = SimpleNamespace(
        OpenProcess=open_process, CloseHandle=lambda value: closed.append(value),
        WaitForSingleObject=wait_process,
    )
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    assert diagnostics._pid_alive(202) is alive
    assert closed == ([] if handle is None else [handle])


@pytest.mark.timeout(900)
@pytest.mark.parametrize(("legacy_lock", "legacy_protocol"), [
    ("live", False), ("absent", False), ("stale", False),
    pytest.param("live", True, id="legacy-protocol", marks=pytest.mark.skipif(
        sys.platform == "darwin", reason="shared pinned external retirement supports Windows and Linux",
    )),
])
def test_real_installer_returns_before_resident_owned_monitor_drain(
    driver, tmp_path, native_runtime_source, legacy_lock, legacy_protocol,
):
    from agent_worktrees import classify_daemon, locks
    from agent_worktrees.status_monitor_cutover import ControlClient
    from agent_worktrees.status_monitor_python import direct_monitor_python
    from test_status_monitor_cutover_process import _wait_for
    from zdd import breadcrumb, diagnostics, routing

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("real construction requires the configured uv executable")
    plugin, expected_commit = native_runtime_source
    home = tmp_path / "home"
    root = home / ".agent-worktrees"
    home.mkdir()
    result = tmp_path / "result.json"
    environment = dict(os.environ, HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home))
    environment["AGENT_WORKTREES_STATUS_MONITOR"] = "1"
    for name in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "COPILOT_PLUGIN_STAGED_FROM",
        "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_WORKTREES_STATUS_MONITOR_ROUTING_DIR",
    ):
        environment.pop(name, None)
    argv = [
        sys.executable, "-I", "-B", str(plugin / "scripts" / "runtime_install.py"),
        "--root", str(root), "--plugin", str(plugin), "--payload", str(plugin),
        "--base-python", sys.executable, "--result", str(result), "--uv", uv,
    ]
    log = tmp_path / "installer.log"
    with log.open("w", encoding="utf-8") as stream:
        try:
            built = subprocess.run(
                argv, env=environment, stdout=stream, stderr=subprocess.STDOUT,
                timeout=300, **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired:
            pytest.fail("Construction did not settle:\n" + log.read_text(encoding="utf-8", errors="replace"))
    assert built.returncode == 0, log.read_text(encoding="utf-8", errors="replace")
    installed = json.loads(result.read_text(encoding="utf-8"))
    candidate = root / "versions" / installed["runtime_version"]
    info = json.loads(subprocess.check_output(
        [str(driver.runtime.slot_python(root, candidate.name)), "-I", "-B", "-c",
         "import json; from agent_worktrees._build_info import BUILD_INFO; print(json.dumps(BUILD_INFO))"],
        env=environment, text=True, timeout=30, **no_window_kwargs(),
    ))
    assert info["commit"] == expected_commit
    assert info["source"] == plugin.as_posix()
    old = root / "versions" / "serving-baseline"
    shutil.copytree(candidate, old)
    driver.runtime.marker_path(root, old.name).unlink()
    driver.runtime.mark_complete(root, old.name, payload_hash=installed["payload_hash"])
    driver.runtime.activate(root, old.name, link_free=True)
    python, extra = direct_monitor_python(str(driver.runtime.slot_python(root, old.name)))
    monitor_environment = {**environment, **extra}
    lock_path = root / "status-monitor.lock"
    route_root = root / "status-monitor-routing"
    token = root / "status-monitor-control.token"
    processes = []
    identities = {}
    monitor_waiter = None
    monitor_exit = None
    hook_thread = None
    demand_thread = None
    demand_stop = threading.Event()
    demand_endpoint = None
    request_started = tmp_path / "request-started"
    request_release = tmp_path / "request-release"
    monitor_probe = """
import runpy, sys, threading
from pathlib import Path
from agent_worktrees import classify_daemon, status_monitor_health
started, release = map(Path, sys.argv[1:3])
if sys.argv[3] == "legacy":
    original_health = status_monitor_health.response
    def legacy_health(**kwargs):
        health = original_health(**kwargs)
        health["capabilities"] = [
            capability for capability in health["capabilities"]
            if capability != status_monitor_health.CAPABILITY
        ]
        return health
    status_monitor_health.response = legacy_health
start_server = classify_daemon.start_server
def controlled_server(compute, **kwargs):
    def controlled_compute(kind, payload):
        if payload.get("retention_probe") is True:
            started.touch()
            wait = threading.Event()
            for _ in range(1800):
                if release.is_file():
                    return {"delayed": True}
                wait.wait(0.1)
            raise TimeoutError("retention probe was not released")
        return compute(kind, payload)
    return start_server(controlled_compute, **kwargs)
classify_daemon.start_server = controlled_server
sys.argv = ["agent_worktrees", "status-monitor", "--interval", "1"]
runpy.run_module("agent_worktrees", run_name="__main__", alter_sys=True)
"""
    try:
        with (tmp_path / "monitor.log").open("w", encoding="utf-8") as monitor_log:
            monitor = subprocess.Popen(
                [python, "-I", "-B", "-c", monitor_probe, str(request_started), str(request_release),
                 "legacy" if legacy_protocol else "supported"],
                env=monitor_environment, cwd=tmp_path, stdin=subprocess.DEVNULL,
                stdout=monitor_log, stderr=subprocess.STDOUT, **no_window_kwargs(),
            )
            processes.append(monitor)
            monitor_waiter = ThreadPoolExecutor(max_workers=1, thread_name_prefix="baseline-reaper")
            monitor_exit = monitor_waiter.submit(monitor.wait)
            try:
                old_record = _wait_for(
                    lambda: (
                        record if "classify_endpoint" in (record := locks.read_lock(lock_path) or {})
                        else None
                    ),
                    message="isolated baseline never published its lock",
                )
            except AssertionError:
                pytest.fail(
                    "Baseline startup failed:\n"
                    + (tmp_path / "monitor.log").read_text(encoding="utf-8", errors="replace")
                    + f"\nlock={lock_path.read_text() if lock_path.exists() else 'absent'}"
                )
            old_pid = old_record["pid"]
            identities[old_pid] = diagnostics.process_start_time(old_pid)
            _wait_for(
                lambda: routing.read_active_endpoint(route_root, verify_listener=False),
                message="isolated baseline never published its route",
            )
            demand_host, demand_port = old_record["worktree_status_endpoint"].split(":")
            demand_endpoint = (demand_host, int(demand_port), old_record["worktree_status_token"])
            classify_daemon.wcs_client.subscribe(*demand_endpoint, "retained-cutover-demand")
            demand_errors = []

            def keep_demand():
                while not demand_stop.wait(1):
                    try:
                        classify_daemon.wcs_client.subscribe(
                            *demand_endpoint, "retained-cutover-demand", timeout=2,
                        )
                    except classify_daemon.wcs_client.DaemonUnavailable as error:
                        demand_errors.append(str(error))
                        return

            demand_thread = threading.Thread(target=keep_demand, daemon=True)
            demand_thread.start()
            driver.runtime.activate(root, installed["runtime_version"], link_free=True)
            hook_result = {}
            host, port, classify_token = classify_daemon.endpoint_from_rendezvous(old_record)
            def request():
                try:
                    hook_result.update(classify_daemon.wcs_client.request(
                        host, port, classify_token, kind=classify_daemon.KIND,
                        key="retained-cutover-request", payload={"retention_probe": True},
                        request_deadline_s=180,
                    ))
                except classify_daemon.wcs_client.DaemonUnavailable as error:
                    hook_result["error"] = str(error)

            hook_thread = threading.Thread(target=request, daemon=True)
            hook_thread.start()
            _wait_for(
                request_started.is_file, timeout=10,
                message="baseline did not begin its controlled request",
            )
            assert hook_thread.is_alive(), "baseline did not retain the delayed request"
            if legacy_lock == "absent":
                lock_path.unlink()
            elif legacy_lock == "stale":
                lock_path.write_text(json.dumps({"pid": 0}), encoding="utf-8")
            argv[-1] = str(tmp_path / "uv-must-not-run")
            with log.open("w", encoding="utf-8") as stream:
                transition = subprocess.Popen(
                    argv, env=environment, stdout=stream, stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL, **no_window_kwargs(),
                )
                processes.append(transition)
                successor = _wait_for(
                    lambda: (
                        endpoint if (endpoint := routing.read_active_endpoint(
                            route_root, verify_listener=False,
                        )) is not None and endpoint.pid != old_pid else None
                    ),
                    timeout=120,
                    message="installer never published the successor route",
                )
                identities[successor.pid] = diagnostics.process_start_time(successor.pid)
                assert transition.wait(timeout=60) == 0, log.read_text(encoding="utf-8", errors="replace")
                demand_stop.set()
                demand_thread.join(timeout=5)
                if legacy_protocol:
                    from agent_worktrees import runtime_monitor_grace as grace
                    from agent_worktrees import runtime_monitor_retirements as retirements

                    assert monitor.poll() is None, "installer bypassed the resident-owned grace"
                    assert hook_thread.is_alive(), "predecessor request ended before grace expiry"
                    grace_path = grace._path(root, {"pid": old_pid, "start": identities[old_pid]})
                    grace_record = _wait_for(
                        lambda: grace._read(grace_path),
                        message="ready successor never armed its retirement grace",
                    )
                    assert 0 <= time.time() - grace_record["ready_at"] < grace.GRACE_SECONDS
                    assert grace_record["old"]["pid"] == old_pid
                    assert grace_record["successor"] == successor.to_dict()
                    def old_slot_retained():
                        return old.name in {
                            version
                            for versions in driver.runtime._leases.read_runtime_references(root).values()
                            for version in versions
                        }

                    assert old_slot_retained()
                    grace_record["ready_at"] = time.time() - grace.GRACE_SECONDS - 1
                    retirements._write_record(grace_path, grace_record)
                    assert ControlClient(
                        successor.base_url, token=token.read_text().strip(),
                    ).promote()["adopted"] is True
                    assert monitor_exit.result(timeout=5) != 0
                    hook_thread.join(timeout=15)
                    assert not hook_thread.is_alive() and "error" in hook_result
                    assert "bounded legacy takeover" not in log.read_text(encoding="utf-8")
                    _wait_for(
                        lambda: not old_slot_retained(),
                        message="resident did not settle the exited predecessor's runtime custody",
                    )
                    new_lock = _wait_for(
                        lambda: locks.read_lock(lock_path),
                        message="successor monitor lock was never readable",
                    )
                    assert new_lock["pid"] == successor.pid
                    health = ControlClient(successor.base_url, token=token.read_text().strip()).health()
                    assert health["published"] is True and health["status"] == "ready"
                    assert routing.read_active_endpoint(route_root) == successor
                    assert "previous" not in routing.read_table(route_root)
                    return
                classify_daemon.wcs_client.release(*demand_endpoint, "retained-cutover-demand")
                assert not demand_errors, demand_errors
                assert hook_thread.is_alive(), "predecessor request finished before the retention probe"
                assert monitor.poll() is None, "installation waited for predecessor retirement"
                with driver.runtime._kernel_lease(
                    root, old.name, kind="retention", shared=False, timeout=0,
                ):
                    pass
                assert old.name in {
                    version for versions in driver.runtime._leases.read_runtime_references(root).values()
                    for version in versions
                }
                assert driver.runtime.gc(root, min_age_days=0) == []
                request_release.touch()
            hook_thread.join(timeout=15)
            assert not hook_thread.is_alive()
            assert hook_result.get("delayed") is True
            from agent_worktrees.status_monitor_cli import _SELF_RETIRE_MAX_GRACE_S

            assert monitor_exit.result(timeout=_SELF_RETIRE_MAX_GRACE_S + 15) == 0
            assert not diagnostics._pid_alive(old_pid), "predecessor did not independently retire"
            with driver.runtime._kernel_lease(
                root, old.name, kind="retention", shared=False, timeout=0,
            ):
                pass
            new_lock = _wait_for(
                lambda: locks.read_lock(lock_path),
                message="successor monitor lock was never readable",
            )
            assert new_lock["pid"] == successor.pid
            assert Path(new_lock["prefix"]).resolve() == candidate.resolve()
            health = ControlClient(successor.base_url, token=token.read_text().strip()).health()
            assert health["published"] is True and health["status"] == "ready"
    except (AssertionError, subprocess.TimeoutExpired) as error:
        pytest.fail(
            f"{error}\nInstaller:\n{log.read_text(encoding='utf-8', errors='replace')}"
            f"\nMonitor:\n{(tmp_path / 'monitor.log').read_text(encoding='utf-8', errors='replace')}"
            f"\nRoute:\n{routing.routing_table_path(route_root).read_text()}"
            f"\nBreadcrumb:\n{breadcrumb.read_breadcrumb(route_root)}"
            f"\nInstaller-process={processes[-1].poll()} request={hook_result}"
        )
    finally:
        try:
            request_release.touch()
            demand_stop.set()
            if demand_thread is not None:
                demand_thread.join(timeout=5)
            if demand_endpoint is not None and not legacy_protocol:
                classify_daemon.wcs_client.release(*demand_endpoint, "retained-cutover-demand")
            from zdd import process_exit

            for pid, identity in identities.items():
                if identity is not None:
                    cleanup = diagnostics.terminate_pid_if_identity(pid, identity, exit_timeout=10)
                    assert cleanup.get("exit_confirmed") is True or process_exit.observe_original_exit(pid, identity)
        finally:
            try:
                for process in processes:
                    if process.poll() is None:
                        process.terminate()
                    process.wait(timeout=15)
                if hook_thread is not None:
                    hook_thread.join(timeout=15)
            finally:
                if monitor_waiter is not None:
                    monitor_waiter.shutdown(wait=True)
                    assert monitor_exit.done(), "baseline reaper did not join"
                    assert monitor_exit.result() == monitor.returncode
                    assert monitor.returncode is not None, "baseline child was not reaped"


def test_preparation_stamps_installed_metadata_and_preserves_native_scripts(tmp_path, monkeypatch):
    import ast
    import agent_worktrees
    from agent_worktrees import runtime_candidate as installer

    package = tmp_path / "site-packages" / "agent_worktrees"
    package.mkdir(parents=True)
    (tmp_path / ".runtime-bootstrap").mkdir()
    scripts = tmp_path / ("Scripts" if os.name == "nt" else "bin")
    scripts.mkdir()
    native_script = scripts / "agent-worktrees"
    native_script.write_text("native console script")
    trampoline = scripts / "agent-worktrees.exe"
    trampoline.write_bytes(b"unsigned")
    python = scripts / "python.exe"
    python.write_bytes(b"signed interpreter")
    monkeypatch.setattr(agent_worktrees, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(installer.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(installer, "distribution_version", lambda _name: "2.0.0.dev1")
    provenance = {
        "version": "2.0.0-dev1", "commit": "abc", "branch": 'quoted"branch',
        "source": str(tmp_path / "source"),
        "deployment_source": json.dumps({"path": str(tmp_path / "source")}),
        "frozen_plugin": str(tmp_path / "source"),
    }
    frozen_scripts = Path(provenance["frozen_plugin"]) / "scripts"
    frozen_scripts.mkdir(parents=True)
    assert {
        "command_binding.py", "command_lifecycle.py", "runtime_install_governance.py",
    } <= set(installer._LIFECYCLE_FILES)
    for name in installer._LIFECYCLE_FILES:
        (frozen_scripts / name).write_text("frozen lifecycle", encoding="utf-8")
    (frozen_scripts / "installation-context").mkdir()
    (frozen_scripts / "installation-context" / "installation_context.py").write_text(
        "frozen validator", encoding="utf-8",
    )
    from agent_worktrees import update_stage

    monkeypatch.setattr(update_stage, "fingerprint", lambda _path: "frozen-fingerprint")
    cached_info = tmp_path / "cached-build-info.py"
    cached_info.write_text("unchanged cached build provenance\n", encoding="utf-8")
    os.link(cached_info, package / "_build_info.py")
    installer.prepare_runtime_candidate(provenance)
    assert cached_info.read_text(encoding="utf-8") == "unchanged cached build provenance\n"
    assert not os.path.samefile(cached_info, package / "_build_info.py")
    content = (package / "_build_info.py").read_text(encoding="utf-8")
    info = ast.literal_eval(ast.parse(content).body[-1].value)
    assert all(info[key] == value for key, value in provenance.items()
               if key not in ("deployment_source", "frozen_plugin"))
    assert json.loads(info["deployment_source"])["payload_fingerprint"] == "frozen-fingerprint"
    assert "frozen_plugin" not in info
    for name in installer._LIFECYCLE_FILES:
        assert (tmp_path / ".runtime-bootstrap" / name).read_text() == "frozen lifecycle"
    assert (
        tmp_path / ".runtime-bootstrap" / "installation-context" / "installation_context.py"
    ).read_text() == "frozen validator"
    assert len(info["runtime_generation"]) == 32
    assert info["build_timestamp"].endswith("Z")
    assert native_script.is_file() and python.is_file()
    assert trampoline.exists() == (os.name != "nt")
    with pytest.raises(RuntimeError, match="Frozen package version"):
        installer.prepare_runtime_candidate({**provenance, "version": "1.0.0"})
    assert (package / "_build_info.py").read_text(encoding="utf-8") == content


@pytest.mark.parametrize("missing", [
    None, "installer_wheel_snapshot.py", "installer_wheel_inputs.py",
    "installer_retained_content.py",
    "installer_dependency_arguments.py",
])
def test_prepared_bootstrap_loads_actual_installer_without_source(tmp_path, monkeypatch, missing):
    import agent_worktrees
    from agent_worktrees import runtime_candidate as installer, update_stage

    plugin = Path(__file__).resolve().parents[1]
    frozen = tmp_path / "frozen-plugin"
    shutil.copytree(plugin / "scripts", frozen / "scripts")
    package = tmp_path / "site-packages" / "agent_worktrees"
    package.mkdir(parents=True)
    bootstrap = tmp_path / ".runtime-bootstrap"
    bootstrap.mkdir()
    for name in (
        "runtime_launcher.py", "versioned_runtime.py", "versioned_runtime_leases.py",
        "versioned_runtime_source.py", "versioned_runtime_construction.py",
        "versioned_runtime_publication.py",
    ):
        shutil.copyfile(frozen / "scripts" / name, bootstrap / name)
    monkeypatch.setattr(agent_worktrees, "__file__", str(package / "__init__.py"))
    monkeypatch.setattr(installer.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(installer, "distribution_version", lambda _name: "2.0.0.dev1")
    monkeypatch.setattr(update_stage, "fingerprint", lambda _path: "frozen-fingerprint")
    installer.prepare_runtime_candidate({
        "version": "2.0.0-dev1", "commit": "abc", "branch": "test",
        "source": str(frozen), "deployment_source": json.dumps({"path": str(frozen)}),
        "frozen_plugin": str(frozen),
    })
    shutil.rmtree(frozen)
    if missing is not None:
        (bootstrap / missing).unlink(missing_ok=True)
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(bootstrap / "runtime_install.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    if missing is not None:
        assert result.returncode != 0
        if missing == "installer_retained_content.py":
            assert missing in result.stderr
        elif missing == "installer_dependency_arguments.py":
            assert "FileNotFoundError" in result.stderr and missing in result.stderr
        else:
            assert f"No module named '{Path(missing).stem}'" in result.stderr
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "--settle-slot" in result.stdout


def test_bash_bootstrap_skips_unusable_alias_and_searches_all_candidates(tmp_path):
    bash = shutil.which("bash") if os.name != "nt" else None
    if os.name == "nt" and (git := shutil.which("git")) is not None:
        candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if candidate.is_file():
            bash = str(candidate)
    if bash is None:
        pytest.skip("native Bash is unavailable")
    text = (Path(__file__).resolve().parents[1] / "scripts" / "install.sh").read_text(encoding="utf-8")
    start = text.index("\n_bootstrap_python() {\n")
    end = text.index("\n}\n", start) + 3
    actual = Path(sys.executable).as_posix()
    program = (
        text[start:end] + "\n"
        "LINK_DIR=/absent; VENV_DIR=/absent\n"
        "type() { printf '%s\\n' '/c/Users/example/Microsoft/WindowsApps/python3' "
        f"'/absent/python' '{actual}'; }}\n"
        'selected="$(_bootstrap_python exclude-venv-dir)" || exit 1\n'
        "\"$selected\" -I -B -c 'import sys; print(sys.executable)'\n"
    )
    result = subprocess.run(
        [bash, "-c", program], capture_output=True, text=True,
        timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == Path(sys.executable).resolve()


@pytest.mark.timeout(900)
def test_real_python_lifecycle_constructs_then_reuses_without_uv(
    driver, tmp_path, native_runtime_source,
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("real construction requires the configured uv executable")
    plugin, expected_commit = native_runtime_source
    root, result = tmp_path / "runtime", tmp_path / "result.json"
    home = tmp_path / "home"
    home.mkdir()
    environment = dict(os.environ)
    environment.update(HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home))
    for name in (
        "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
        "COPILOT_PLUGIN_STAGED_FROM", "AGENT_WORKTREES_PAYLOAD_ROOT",
    ):
        environment.pop(name, None)
    argv = [
        sys.executable, "-I", str(plugin / "scripts" / "runtime_install.py"),
        "--root", str(root), "--plugin", str(plugin), "--payload", str(plugin),
        "--base-python", sys.executable, "--result", str(result), "--uv", uv,
    ]
    source_files = driver.engine._source_files(driver.engine._package_roots(plugin))
    source_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_files}

    def run(command, label, timeout):
        log = tmp_path / f"{label}.log"
        started = time.monotonic()
        with log.open("w", encoding="utf-8") as stream:
            try:
                completed = subprocess.run(
                    command, stdout=stream, stderr=subprocess.STDOUT, env=environment,
                    timeout=timeout, **no_window_kwargs(),
                )
            except subprocess.TimeoutExpired:
                pytest.fail(
                    f"Lifecycle did not settle ({label}): {command}\n"
                    + log.read_text(encoding="utf-8", errors="replace")
                )
        elapsed = time.monotonic() - started
        changed = [
            str(path) for path, digest in source_hashes.items()
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ]
        assert not changed, f"{label} modified source inputs: {changed}"
        assert driver.engine._source_files(driver.engine._package_roots(plugin)) == source_files
        assert completed.returncode == 0, log.read_text(encoding="utf-8", errors="replace")
        diagnostics = log.read_text(encoding="utf-8", errors="replace")
        print(f"{label}: {elapsed:.3f}s", flush=True)
        for line in diagnostics.splitlines():
            if line.startswith("Source wheel assembly:"):
                print(line, flush=True)
        return diagnostics

    construction = run(argv, "construct", 300)
    installed = json.loads(result.read_text(encoding="utf-8"))
    assert installed["reused"] is False
    assert (root / "current-version").read_text().strip() == installed["runtime_version"]
    python = Path(installed["runtime_python"])
    origins = json.loads((python.parent.parent / ".runtime-input-origins.json").read_text())
    assert "agent-worktrees:non-static-setuptools-contract" in construction
    assert "retaining isolated source builds" in construction
    assert "source_wheels" not in origins
    assert set(origins["packages"]) == {
        driver.engine._package_name(package) for package in driver.engine._package_roots(plugin)
    }
    info_probe = [
        str(python), "-I", "-B", "-c",
        "import json; from agent_worktrees._build_info import BUILD_INFO; print(json.dumps(BUILD_INFO))",
    ]
    info = json.loads(subprocess.check_output(
        info_probe, env=environment, text=True, timeout=30, **no_window_kwargs(),
    ))
    assert info["version"] == installed["package_version"]
    assert info["source"] == plugin.as_posix()
    assert len(info["runtime_generation"]) == 32
    assert info["commit"] == expected_commit
    if os.name == "nt":
        assert not list(python.parent.glob("agent-*.exe"))
    fresh_version = installed["package_version"] + ".repair." + "c" * 32
    fresh = [
        sys.executable, "-I", "-B", "-c",
        "import os,sys;from pathlib import Path;"
        "plugin,root,uv,version=Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4];"
        "sys.path.insert(0,str(plugin/'scripts'));"
        "os.environ['AGENT_WORKTREES_PAYLOAD_ROOT']=str(plugin);"
        "import installer_engine as engine;"
        "\nwith engine.numbered_candidate(root,version,plugin=plugin,uv=uv,"
        "base_python=Path(sys.executable),health_module='agent_worktrees') as candidate:"
        "\n assert not candidate.reused"
        "\n assert engine.runtime.is_complete(root,version,expect_hash=candidate.payload_hash)",
        str(plugin), str(root), uv, fresh_version,
    ]
    diagnostics = run(fresh, "fresh-venv-from-retained-source", 180)
    assert "agent-worktrees:non-static-setuptools-contract" in diagnostics
    assert (root / "current-version").read_text().strip() == installed["runtime_version"]
    argv[-1] = str(tmp_path / "uv-must-not-run")
    run(argv, "reuse-without-uv", 180)
    reused = json.loads(result.read_text(encoding="utf-8"))
    assert reused["reused"] is True
    assert reused["payload_hash"] == installed["payload_hash"]
    assert reused["runtime_python"] == installed["runtime_python"]
    assert json.loads(subprocess.check_output(
        info_probe, env=environment, text=True, timeout=30, **no_window_kwargs(),
    )) == info
    native = []
    if os.name == "nt":
        for name in ("powershell.exe", "pwsh.exe"):
            executable = shutil.which(name)
            if executable is not None:
                native.append([
                    executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                    str(plugin / "scripts" / "install.ps1"), "provision", "-InstallDir", str(root),
                ])
    bash = shutil.which("bash") if os.name != "nt" else None
    if os.name == "nt" and (git := shutil.which("git")) is not None:
        git_bash = Path(git).resolve().parent.parent / "bin" / "bash.exe"
        if git_bash.is_file():
            bash = str(git_bash)
    if bash is not None:
        probe = subprocess.run(
            [bash, "-c", "printf '%s' \"$OSTYPE\""], capture_output=True, text=True,
            timeout=10, **no_window_kwargs(),
        )
        if os.name != "nt" or probe.stdout.startswith(("msys", "cygwin")):
            if os.name == "nt":
                native.append([
                    bash, "-c", 'exec bash "$1" provision --install-dir "$(cygpath -u "$2")"',
                    "--", (plugin / "scripts" / "install.sh").as_posix(), str(root),
                ])
            else:
                native.append([
                    bash, str(plugin / "scripts" / "install.sh"), "provision",
                    "--install-dir", str(root),
                ])
    assert native, "real native entrypoint proof needs an available supported shell"
    for index, command in enumerate(native):
        run(command, f"native-{index}", 180)
        assert (root / "current-version").read_text().strip() == installed["runtime_version"]
        manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
        assert manifest["source"]["version"] == installed["package_version"]
        assert Path(manifest["source"]["path"]).resolve() == plugin.resolve()
        assert Path(manifest["venv"]).resolve() == Path(installed["runtime_python"]).parent.parent.resolve()
        assert json.loads(subprocess.check_output(
            info_probe, env=environment, text=True, timeout=30, **no_window_kwargs(),
        )) == info


@pytest.mark.parametrize("discovery", [
    "missing", "unreadable", "reuse-timeout", "reuse-malformed", "unknown-slot", "retry-references",
])
@pytest.mark.parametrize("release", [False, True])
def test_previous_only_installation_starts_and_settles_the_incoming_monitor(
    driver, tmp_path, monkeypatch, discovery, release,
):
    from types import SimpleNamespace
    from zdd import diagnostics, process_exit, routing
    from agent_worktrees import (
        config as cfg, locks, runtime_monitor_retirements as retirements,
        status_monitor_cutover as cutover, status_monitor_health,
        status_monitor_runtime as monitor,
    )

    root = tmp_path / "runtime"
    for version in ("1.0.0", "2.0.0"):
        _slot(driver, root, version)
    driver.runtime.activate(root, "1.0.0", link_free=True)
    owner = str(tmp_path / "owner")
    claim = None
    selected = "1.0.0"
    if release:
        driver.runtime.claim_dev(root, owner, previous_version=selected)
        _slot(driver, root, "dev")
        driver.runtime.activate(root, "dev", link_free=True, dev_owner=owner)
        claim = driver.runtime.read_dev_claim(root, strict=True)
        selected = "dev"
    directory = root / "status-monitor-routing"
    directory.mkdir()
    previous = routing.Endpoint("127.0.0.1", 61001, 202, "old", 1, "2002")
    raw_active = previous.to_dict() if discovery.startswith("reuse-") or discovery in (
        "unknown-slot", "retry-references",
    ) else None
    routing.routing_table_path(directory).write_text(json.dumps({
        "version": 1, "active": raw_active, "previous": previous.to_dict() if raw_active is None else None,
    }))
    if discovery == "unknown-slot":
        (root / "status-monitor.lock").write_text(json.dumps({
            "pid": 202, "start_time": "2002", "prefix": str(tmp_path),
        }))
    spawned = []
    class Governance:
        def __init__(self, requested, payload):
            self.root, self.payload = requested, payload
            self.manifest_publication = nullcontext
        def signature(self):
            return {}
        def recheck(self):
            pass
        def held(self, **kwargs):
            return nullcontext()
    class Handle:
        pid = 303
        process_start_time = "3003"
        def poll(self):
            return None
        def terminate(self):
            pytest.fail("replacement must not terminate a daemon")
    def spawn(python, *, port):
        assert routing.read_table(directory)["active"] == raw_active
        spawned.append((python, port))
        return Handle()
    read_text = Path.read_text
    def read_discovery(path, *args, **kwargs):
        if discovery == "unreadable" and path == root / "status-monitor.lock":
            raise PermissionError("fixture discovery unavailable")
        return read_text(path, *args, **kwargs)
    def health():
        return {
            "status": "ready", "published": True, "pid": 303,
            "runtime_slot": "2.0.0", "runtime_prefix": str(root / "versions" / "2.0.0"),
        }
    monkeypatch.setattr(driver, "Governance", Governance)
    monkeypatch.setattr(driver, "_load_installed", lambda slot: None)
    monkeypatch.setattr(cfg, "install_dir", lambda: root)
    monkeypatch.setattr(monitor, "_aw_runtime_home", lambda: root)
    monkeypatch.setattr(monitor, "_monitor_lock_path", lambda: root / "status-monitor.lock")
    monkeypatch.setattr(monitor, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(cutover, "routing_dir", lambda *a: directory)
    monkeypatch.setattr(cutover, "load_or_create_control_token", lambda *a: "fixture")
    def client(url, **kwargs):
        def observed_health():
            if url.endswith(":61001"):
                if discovery == "reuse-timeout":
                    raise TimeoutError("fixture outgoing timeout")
                if discovery == "reuse-malformed":
                    raise ValueError("fixture malformed outgoing health")
                if discovery == "unknown-slot":
                    return {"status": "ready", "pid": 202, "runtime_prefix": str(tmp_path)}
            return health()
        return SimpleNamespace(health=observed_health, promote=lambda: {"adopted": True})
    monkeypatch.setattr(cutover, "ControlClient", client)
    monkeypatch.setattr(cutover, "spawn_passive", spawn)
    monkeypatch.setattr(cutover, "pick_free_port", lambda: 61002)
    monkeypatch.setattr(status_monitor_health, "prepared_check", lambda *a, **k: lambda *a: True)
    monkeypatch.setattr(cutover.guarded_recovery, "successor_healthy", lambda *a, **k: (
        routing.read_table(directory)["active"]["pid"] == 303
    ))
    monkeypatch.setattr(retirements, "_runtime", lambda: driver.runtime)
    exited = set()
    monkeypatch.setattr(process_exit, "observe_original_exit", lambda pid, start: pid in exited)
    monkeypatch.setattr(diagnostics, "_pid_alive", lambda pid: pid in (202, 303))
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: {202: "2002", 303: "3003"}.get(pid))
    monkeypatch.setattr(locks, "process_start_time", diagnostics.process_start_time)
    monkeypatch.setattr(locks, "_pid_alive", lambda pid: pid in (202, 303))
    monkeypatch.setattr(Path, "read_text", read_discovery)
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    if discovery == "retry-references":
        from agent_worktrees import runtime_monitor_identity as identity

        def unavailable(_root):
            raise driver.InstallationRefused("fixture outgoing identity unavailable")
        monkeypatch.setattr(driver, "_monitor_identity", unavailable)
        commit = driver.runtime._leases.commit_runtime_reference_transaction
        def interrupted(*args, **kwargs):
            raise OSError("fixture reference commit interrupted")
        monkeypatch.setattr(driver.runtime._leases, "commit_runtime_reference_transaction", interrupted)
        with pytest.raises(OSError, match="commit interrupted"):
            driver.settle_candidate(
                root, "2.0.0", payload=tmp_path, payload_hash="previous",
                expected_governance={}, previous=selected, release_dev_claim=claim,
                dev_owner=owner if release else None,
            )
        monkeypatch.setattr(driver.runtime._leases, "commit_runtime_reference_transaction", commit)
        monkeypatch.setattr(driver, "_monitor_identity", lambda root: (202, "2002"))
        monkeypatch.setattr(driver, "_monitor_slot", lambda *a: "1.0.0")
        monkeypatch.setattr(cutover, "routed_monitor_identity", lambda *a, **k: (202, "2002", "1.0.0"))
        monkeypatch.setattr(identity, "async_predecessor", lambda root, predecessor, **k: (
            predecessor, {"expected_old": previous},
        ))
        monkeypatch.setattr(driver.time, "sleep", lambda seconds: pytest.fail(
            "installer retry entered permanent settlement after a pending reference transaction",
        ))
    result = driver.settle_candidate(
        root, "2.0.0", payload=tmp_path, payload_hash="previous",
        expected_governance={}, previous=selected, release_dev_claim=claim,
        dev_owner=owner if release else None,
    )
    assert len(spawned) == 1
    assert result["runtime_version"] == "2.0.0" and result["retirement_pending"]
    assert routing.read_table(directory)["active"]["pid"] == 303
    assert (root / "deploy-manifest.json").is_file()
    assert list((root / "status-monitor-observations").glob("*.json"))
    if release:
        assert result["dev_claim_release_pending"]
        assert driver.runtime.read_dev_claim(root, strict=True) == claim
        assert retirements.reconcile(root, runtime=driver.runtime)
        assert driver.runtime.read_dev_claim(root, strict=True) == claim
        exited.add(202)
        assert not retirements.reconcile(root, runtime=driver.runtime)
        assert driver.runtime.read_dev_claim(root, strict=True) is None


@pytest.mark.parametrize("serving_slot", ["1.0.0", "dev"])
@pytest.mark.parametrize("activation_fails", [False, True])
def test_legacy_installer_replaces_unsupported_predecessor_and_keeps_failures_truthful(
    driver, tmp_path, monkeypatch, activation_fails, serving_slot,
):
    from agent_worktrees import (
        config as cfg, runtime_monitor_identity as monitor,
        runtime_monitor_retirements as retirements, status_monitor_cutover as cutover,
    )

    from types import SimpleNamespace
    from zdd import process_exit, routing
    from agent_worktrees import status_monitor_runtime as runtime

    root = tmp_path / "runtime"
    _slot(driver, root, "1.0.0")
    _slot(driver, root, "2.0.0")
    driver.runtime.activate(root, "1.0.0", link_free=True)
    owner = str(tmp_path / "owner")
    claim = None
    if serving_slot == "dev":
        driver.runtime.claim_dev(root, owner, previous_version="1.0.0")
        _slot(driver, root, "dev")
        claim = driver.runtime.read_dev_claim(root)
    events = []
    class Governance:
        def __init__(self, requested, payload):
            self.root, self.payload = requested, payload
            self.manifest_publication = nullcontext
        def signature(self):
            return {}
        def recheck(self):
            pass
        def held(self, **kwargs):
            return nullcontext()
    class Guard:
        held = True
        lock_path = root / "status-monitor-routing" / "cutover.lock"
        def release(self):
            events.append("released")
    guard = Guard()
    predecessor = (202, "2002", serving_slot)
    transition = cutover.activate_after_update
    def unsupported(_root):
        raise monitor.AsyncRetirementUnsupported("legacy fixture", predecessor)
    def retire(requested, expected, *, guard):
        pytest.fail("predecessor retired before successor startup")
    def activate(**kwargs):
        assert events == ["reserve"]
        assert driver.runtime.current_version(root) == "2.0.0"
        assert kwargs["monitor_was_live"] is True
        assert "retired_predecessor" not in kwargs
        assert kwargs["asynchronous_retirement"] is True
        assert kwargs["retirement_predecessor"] == {
            "pid": predecessor[0], "start": predecessor[1], "slot": serving_slot,
            "release_claim": claim,
        }
        assert kwargs["cutover_guard"] is guard
        events.append("activation")
        return transition(**kwargs)
    class Orchestrator:
        def __init__(self, *args, **kwargs):
            pass
        def run(self, **kwargs):
            result = SimpleNamespace(
                ok=not activation_fails, committed=not activation_fails,
                publication_uncertain=False, retirement_pending=True,
                rolled_back=activation_fails, error="fixture health failure" if activation_fails else None,
            )
            result.to_dict = lambda: {key: value for key, value in vars(result).items() if key != "to_dict"}
            return result
    monkeypatch.setattr(cutover, "CutoverOrchestrator", Orchestrator)
    monkeypatch.setattr(cutover, "SingleInstance", Guard)
    monkeypatch.setattr(runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(runtime, "_monitor_lock_path", lambda: root / "status-monitor.lock")
    monkeypatch.setattr(runtime, "_aw_runtime_home", lambda: root)
    monkeypatch.setattr(cutover, "routing_dir", lambda *a: root / "status-monitor-routing")
    monkeypatch.setattr(cutover, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr(cutover.locks, "read_lock", lambda path: {
        "pid": predecessor[0], "start_time": predecessor[1],
    })
    monkeypatch.setattr(process_exit, "observe_original_exit", lambda *args: False)
    monkeypatch.setattr(cutover, "routed_monitor_identity", lambda *a, **k: None)
    monkeypatch.setattr(routing, "read_active_endpoint", lambda *a, **k: routing.Endpoint(
        "127.0.0.1", 61001, predecessor[0], process_start_time=predecessor[1],
    ))
    monkeypatch.setattr(cutover, "_reap_abandoned_passive", lambda record: {})
    monkeypatch.setattr(driver, "Governance", Governance)
    monkeypatch.setattr(driver, "_load_installed", lambda slot: None)
    monkeypatch.setattr(driver, "_monitor_identity", unsupported)
    monkeypatch.setattr(driver, "_monitor_slot", lambda *a: serving_slot)
    monkeypatch.setattr(driver, "_candidate_serving", lambda *a: (
        "activation" in events and not activation_fails
    ))
    monkeypatch.setattr(monitor, "retire_legacy", retire)
    monkeypatch.setattr(monitor, "async_predecessor", lambda root, previous, **kwargs: (previous, {}))
    monkeypatch.setattr(retirements, "reserve", lambda *a, **k: events.append("reserve") or True)
    monkeypatch.setattr(retirements, "cancel", lambda *a, **k: events.append("cancel") or True)
    monkeypatch.setattr(cfg, "install_dir", lambda: root)
    monkeypatch.setattr(cutover, "_acquire_cutover_lock", lambda directory: guard)
    monkeypatch.setattr(cutover, "activate_after_update", activate)
    kwargs = dict(
        payload=tmp_path, payload_hash="previous", expected_governance={}, previous="1.0.0",
        release_dev_claim=claim, dev_owner=owner if claim is not None else None,
    )
    if activation_fails:
        with pytest.raises(driver.InstallationRefused, match="rolled back"):
            driver.settle_candidate(root, "2.0.0", **kwargs)
        assert driver.runtime.current_version(root) == "1.0.0"
    else:
        result = driver.settle_candidate(root, "2.0.0", **kwargs)
        assert result["runtime_version"] == "2.0.0" and result["retirement_pending"] is True
        assert result["dev_claim_release_pending"] is (claim is not None)
    assert events[:3] == ["reserve", "activation", "reserve"] and events[-1] == "released"
