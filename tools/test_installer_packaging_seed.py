"""Real native compiler, controlled TLS acquisition and immutable seed health."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import venv

import pytest

pytestmark = pytest.mark.timeout(100)

import runtime_feed_configuration as configuration
import runtime_source_build as builds
import installer_engine_ref
from runtime_source_lock import retain_source_configuration
from test_installer_closed_wheels import engine as engine  # noqa: PLC0414
from test_installer_wheel_acquisition import CERTIFICATE, _running, feeds as feeds, worker_procutil  # noqa: PLC0414
from test_runtime_feed_configuration import environment
from test_runtime_source_authentication import adopter as adopter, configured_source as configured_source  # noqa: PLC0414
from test_runtime_source_build import packaging_feed, source_with_process_owner
from test_runtime_source_lock import plan, target as target  # noqa: PLC0414

import installer_packaging_seed as seed
from installer_wheel_acquisition import AcquisitionLimits
from installer_wheel_inputs import WheelLimits
from wheel_input_fixture import wheel_bytes


@pytest.mark.parametrize("failed", [False, True])
def test_tooling_identity_bounds_fresh_readers_and_settles_failure(tmp_path, monkeypatch, failed):
    installer, runtime = tmp_path / "installer", tmp_path / "runtime"
    installer.mkdir()
    runtime.mkdir()
    files = []
    for root, prefix in ((installer, "installer_"), (runtime, "versioned_runtime")):
        for index in range(6):
            path = root / (prefix + str(index) + ".py")
            path.write_bytes(("value = " + str(index) + "\n").encode())
            files.append(path)
    monkeypatch.setattr(seed, "__file__", str(installer / "seed.py"))
    monkeypatch.setattr(seed.engine.runtime, "__file__", str(runtime / "versioned_runtime.py"))
    fourth = threading.Event()
    lock = threading.Lock()
    started, settled = [], []
    active = peak = 0
    original = seed._file_identity

    def read(path, maximum, deadline):
        nonlocal active, peak
        with lock:
            started.append(path)
            position = len(started)
            active += 1
            peak = max(peak, active)
        try:
            if position == 4:
                fourth.set()
            if position == 1:
                assert fourth.wait(5), "the first read must not serialize later input"
            if failed and position == 2:
                raise OSError("selected tooling read failed")
            return original(path, maximum, deadline)
        finally:
            with lock:
                active -= 1
                settled.append(path)

    monkeypatch.setattr(seed, "_file_identity", read)
    try:
        if failed:
            with pytest.raises(OSError, match="selected tooling read failed"):
                seed._tooling_identity(1 << 20, time.monotonic() + 10)
        else:
            first = seed._tooling_identity(1 << 20, time.monotonic() + 10)
            files[-1].write_bytes(b"changed = True\n")
            assert seed._tooling_identity(1 << 20, time.monotonic() + 10) != first
    finally:
        assert fourth.is_set() and 2 <= peak <= 4
        assert active == 0 and sorted(started) == sorted(settled)
        assert not any(t.name.startswith("retained-content") for t in threading.enumerate())


@pytest.fixture
def installed_python(tmp_path):
    root = tmp_path / "installed"
    venv.EnvBuilder(with_pip=False).create(root)
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


@pytest.mark.parametrize("failed", [False, True])
def test_paired_target_probes_overlap_and_settle_before_return(monkeypatch, failed):
    entered = threading.Barrier(2)
    settled = []
    deadlines = []
    selected, installed = Path("selected"), Path("installed")
    deadline = time.monotonic() + 10
    environment, procutil = {}, object()

    def probe(target, actual_environment, actual_deadline, actual_procutil):
        assert actual_environment is environment and actual_procutil is procutil
        deadlines.append(actual_deadline)
        try:
            entered.wait(5)
            if failed and target == selected:
                raise ValueError("selected target failed")
            return {"target": str(target)}
        finally:
            settled.append(target)

    monkeypatch.setattr(seed, "_target", probe)
    try:
        if failed:
            with pytest.raises(ValueError, match="selected target failed"):
                seed._targets(selected, environment, deadline, procutil, installed=installed)
        else:
            assert seed._targets(
                selected, environment, deadline, procutil, installed=installed,
            ) == ({"target": "selected"}, {"target": "installed"})
    finally:
        assert set(settled) == {selected, installed}
        assert deadlines == [deadline, deadline]
        assert not any(t.name.startswith("retained-content") for t in threading.enumerate())


@pytest.mark.timeout(15)
def test_paired_target_probe_executes_both_interpreters_in_independent_owned_scopes(
    tmp_path, monkeypatch, installed_python,
):
    marker = tmp_path / "target-executions"
    monkeypatch.setattr(seed, "_TARGET", (
        "import os,pathlib,sys\n"
        "assert 'PYTHONPATH' not in os.environ and 'VIRTUAL_ENV' not in os.environ\n"
        f"with pathlib.Path({str(marker)!r}).open('a') as log:\n"
        "    log.write(sys.executable+'\\n')\n" + seed._TARGET
    ))
    original = seed.run_owned
    calls = []

    def recorded(argv, **options):
        calls.append(options)
        return original(argv, **options)

    monkeypatch.setattr(seed, "run_owned", recorded)
    selected, installed = seed._targets(
        Path(sys.executable), {**os.environ, "PYTHONPATH": "poison", "VIRTUAL_ENV": "poison"},
        time.monotonic() + 10, worker_procutil(), installed=installed_python,
    )
    executions = [Path(value) for value in marker.read_text().splitlines()]
    assert len(executions) == 2
    assert sum(value.samefile(sys.executable) for value in executions) == 1
    assert sum(value.samefile(installed_python) for value in executions) == 1
    assert selected == installed
    assert len(calls) == 2 and all(call["construction_fd"] is None for call in calls)
    assert calls[0]["deadline"] == calls[1]["deadline"]
    assert all(0 < call["timeout"] <= 10 for call in calls)


@pytest.mark.parametrize("failed", ["original", "installed"])
@pytest.mark.timeout(15)
def test_paired_target_probe_refuses_partial_success(monkeypatch, installed_python, failed):
    monkeypatch.setattr(seed, "_TARGET", (
        "import os,sys\n"
        f"installed=os.path.samefile(sys.executable,{str(installed_python)!r})\n"
        f"if installed == {failed == 'installed'!r}: raise SystemExit(7)\n" + seed._TARGET
    ))
    with pytest.raises(ValueError, match="target probe"):
        seed._targets(
            Path(sys.executable), dict(os.environ), time.monotonic() + 10,
            worker_procutil(), installed=installed_python,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows native interpreter path limit")
@pytest.mark.timeout(15)
def test_paired_target_probe_launches_a_real_long_path_interpreter(tmp_path):
    root = tmp_path
    while len(str(root / "Scripts" / "python.exe")) <= 280:
        root /= "interpreter-" + "x" * 30
    venv.EnvBuilder(with_pip=False).create(seed.dependencies._native_retained_path(root))
    installed = root / "Scripts" / "python.exe"
    selected, observed = seed._targets(
        Path(sys.executable), dict(os.environ), time.monotonic() + 10,
        worker_procutil(), installed=installed,
    )
    assert selected == observed


@pytest.mark.skipif(os.name != "nt", reason="Windows probe and venv-launcher descendants")
@pytest.mark.timeout(15)
def test_paired_target_probe_shares_bound_and_settles_second_target_descendants(
    tmp_path, monkeypatch, installed_python,
):
    marker = tmp_path / "paired-descendant"
    script = (
        "import os,pathlib,subprocess,sys,time\n"
        f"if os.path.samefile(sys.executable,{str(installed_python)!r}):\n"
        "    child=subprocess.Popen([sys.executable,'-I','-S','-B','-c','import time;time.sleep(60)'],"
        "creationflags=subprocess.CREATE_NO_WINDOW)\n"
        f"    pathlib.Path({str(marker)!r}).write_text(str(child.pid))\n"
        "    time.sleep(60)\n"
        "else:\n"
        "    time.sleep(0.5)\n"
        f"    exec({seed._TARGET!r})\n"
    )
    monkeypatch.setattr(seed, "_TARGET", script)
    monkeypatch.setattr(seed, "_TARGET_PROBE_TIMEOUT_SECONDS", 2)
    started = time.monotonic()
    original = seed.run_owned
    deadlines = []

    def bounded(argv, **options):
        assert options["deadline"] <= started + 2.1
        deadlines.append(options["deadline"])
        return original(argv, **options)

    monkeypatch.setattr(seed, "run_owned", bounded)
    with pytest.raises(ValueError, match="target probe"):
        seed._targets(
            Path(sys.executable), dict(os.environ), started + 60,
            worker_procutil(), installed=installed_python,
        )
    assert time.monotonic() - started < 10
    assert len(deadlines) == 2 and deadlines[0] == deadlines[1]
    assert marker.is_file(), "the second target's actual descendant must start before timeout"
    assert not _running(int(marker.read_text()))


@pytest.mark.skipif(os.name != "nt", reason="Windows probe and venv-launcher descendants")
@pytest.mark.timeout(15)
def test_target_probe_has_its_own_bound_and_settles_descendants(tmp_path, monkeypatch):
    marker = tmp_path / "probe-descendant"
    script = (
        "import os,pathlib,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-I','-S','-B','-c','import time;time.sleep(60)'],"
        "creationflags=subprocess.CREATE_NO_WINDOW)\n"
        f"pathlib.Path({str(marker)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(seed, "_TARGET", script)
    monkeypatch.setattr(seed, "_TARGET_PROBE_TIMEOUT_SECONDS", 2)
    started = time.monotonic()
    with pytest.raises(ValueError, match="target probe"):
        seed._target(Path(sys.executable), dict(os.environ), started + 60, worker_procutil())
    assert time.monotonic() - started < 10
    assert marker.is_file(), "the actual descendant must start before probe timeout"
    assert not _running(int(marker.read_text()))


@pytest.mark.skipif(
    os.environ.get("CE_RUN_NATIVE_SEED_FEED") != "1",
    reason="explicit native governed-feed witness qualification",
)
def test_native_configured_feed_packaging_witness(tmp_path, monkeypatch):
    uv = shutil.which("uv")
    assert uv is not None
    monkeypatch.setenv("UV_SYSTEM_CERTS", "true")
    project = tmp_path / "pyproject.toml"
    project.write_text(seed._PROJECT, encoding="utf-8")
    output = tmp_path / "pylock.toml"
    deadline = time.monotonic() + 60
    with seed.dependencies.retain_uv_source_compile(
        uv, [
            str(project), "--python", sys.executable, "--python-version",
            ".".join(str(value) for value in sys.version_info[:3]),
            "--only-binary", ":all:", "--no-python-downloads",
            "--output-file", str(output), "--format", "pylock.toml",
        ],
        cwd=tmp_path, env=dict(os.environ), trusted_source_roots=(tmp_path,),
        deadline=deadline, native_target=True,
    ) as transaction, project.open("rb") as custody:
        seed.run_owned(
            list(transaction.argv), env=transaction.environment, cwd=transaction.cwd,
            timeout=deadline - time.monotonic(), deadline=deadline,
            procutil=worker_procutil(), construction_fd=custody.fileno(),
        )
        content = output.read_bytes()
        try:
            selected = seed._selected_wheel(content, deadline=deadline)
        except ValueError:
            document = seed.dependencies._toml_parser().loads(content.decode("utf-8"))
            shape = {
                "document": sorted(document),
                "packages": [
                    {"keys": sorted(package), "wheels": [
                        {"keys": sorted(wheel), "hashes": sorted(wheel.get("hashes", {}))}
                        for wheel in package.get("wheels", [])
                    ]} for package in document.get("packages", [])
                ],
            }
            pytest.fail("native seed witness shape: " + json.dumps(shape, sort_keys=True))
        assert selected["name"] == "packaging"


@contextmanager
def selected_seed(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch, *,
    owner_options=(), dropped_option=None,
):
    source_with_process_owner(configured_source, build_only=True)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    env = environment(adopter, state, tmp_path)
    selected = plan(adopter)
    deadline = time.monotonic() + 100
    monkeypatch.setattr(seed, "engine", engine)
    monkeypatch.setattr(
        configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest(),
    )
    if owner_options:
        native_compile = seed.dependencies.retain_uv_source_compile

        def retained_compile(uv, arguments, **options):
            if "installer-packaging-seed" not in Path(arguments[0]).read_text():
                arguments = [*arguments, *owner_options]
            elif dropped_option is not None:
                arguments = list(arguments)
                position = arguments.index(dropped_option)
                del arguments[position:position + 2]
            return native_compile(uv, arguments, **options)

        monkeypatch.setattr(seed.dependencies, "retain_uv_source_compile", retained_compile)
    with builds.retain_authenticated_source(
        selected, installation=adopter[1], deadline=deadline,
    ) as authenticated, retain_source_configuration(
        selected, payload=authenticated.payload, expected_source=authenticated.identity,
        target=target, component="fixture", uv=shutil.which("uv"), python=Path(sys.executable),
        deadline=deadline, env=env, wheel_witness=True,
        include_build_tools=True, installation=adopter[1],
    ) as captured:
        checks = []
        rejected = set()

        def verify(wheels):
            authenticated.verify()
            captured.verify()
            checks.append(wheels)
            if any(wheel["sha256"] in rejected for wheel in wheels):
                raise ValueError("selected seed input is revoked")
            return selected.policy()

        options = {
            "payload": authenticated.payload, "source": authenticated.identity,
            "policy": selected.policy(), "uv": Path(shutil.which("uv")),
            "python": Path(sys.executable), "verify_owner": verify,
            "opaque_identity": lambda value: hashlib.sha256(value.encode()).hexdigest(),
            "limits": seed.SeedLimits(16_384, 32 << 20, 1 << 20),
            "acquisition_limits": AcquisitionLimits(
                max_packages=1, max_requests=10, max_redirects=3, max_candidates_per_project=10,
                max_index_bytes=16_384, max_total_metadata_bytes=32_768,
                max_wheel_bytes=1 << 20, max_closure_bytes=1 << 20, max_temporary_bytes=4 << 20,
            ),
            "wheel_limits": WheelLimits(1 << 20, 4 << 20, 1 << 20, 64 << 10, 100, 10, 32 << 10),
            "deadline": deadline, "procutil": worker_procutil(),
            "transaction": captured.transaction, "projections": captured.projections,
        }
        yield state, options, checks, rejected


@pytest.mark.parametrize("delegated_source", [False, True])
def test_real_seed_cold_health_reuse_and_policy_invalidation(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch, delegated_source,
):
    with selected_seed(adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch) as (
        state, options, checks, _rejected,
    ):
        if delegated_source:
            def verify_source(payload, source):
                assert payload == options["payload"] and source == options["source"]
                engine._assert_source_pin(payload, engine.runtime_source.manifest_fields(source))

            options["source_verifier"] = verify_source
        assert state["requests"] == []
        root = Path(options["policy"]["owner"]["runtimeRoot"]) / ".source-build-tools"
        assert not root.exists()
        for reused in (False, True):
            with seed.retain_packaging_seed(**options) as ready:
                assert ready.reused is reused
                assert ready.local_packages == ()
                assert ready.slot.parent.parent == root
                assert engine.runtime.current_version(root) is None
                receipt = json.loads((ready.slot / ".runtime-input-origins.json").read_text())
                wheels = receipt["wheel_inputs"]
                assert set(wheels["distributions"]) == {"packaging"}
                assert wheels["binding"]["source"] == options["source"].document()
                assert wheels["binding"]["requirement"] == seed.REQUIREMENT
                assert wheels["acquisition"]["policy_id"] == wheels["binding"]["feed_policy"]
                python = ready.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                result = subprocess.run(
                    [str(python), "-I", "-c",
                     "from packaging.requirements import Requirement;print(Requirement('sample>=1').name)"],
                    executable=seed.dependencies._native_retained_path(python),
                    capture_output=True, text=True, check=True,
                    timeout=options["deadline"] - time.monotonic(),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                assert result.stdout.strip() == "sample"
                if not reused:
                    for interpreter, message in (
                        (options["python"], "source, interpreter or selected policy changed"),
                        (python, "installed interpreter differs from selection: machine"),
                    ):
                        with monkeypatch.context() as changed:
                            changed.setattr(seed, "_TARGET", (
                                "import os,platform,sys\n"
                                f"if os.path.samefile(sys.executable,{str(interpreter)!r}):\n"
                                "    platform.machine=lambda: 'changed-target'\n" + seed._TARGET
                            ))
                            with pytest.raises(ValueError, match=message):
                                ready.verify()
            generation = ready.slot.name
            completed = engine.runtime.marker_path(root, generation).read_bytes()
        assert any(not item for item in checks) and any(item for item in checks)
        # A caller cannot swap its policy and reuse the completed generation
        # while the real installation owner still selects the previous one.
        options["policy"]["policyId"] += "-new"
        monkeypatch.setattr(seed, "_target", lambda *_a: pytest.fail("unselected interpreter probe"))
        with pytest.raises(ValueError, match="selected policy changed"):
            with seed.retain_packaging_seed(**options):
                pytest.fail("unselected policy reused completed tooling")
        assert engine.runtime.marker_path(root, generation).read_bytes() == completed
        assert engine.runtime.current_version(root) is None


@pytest.mark.parametrize("phase", ["selection", "health", "consumption"])
def test_owner_revocation_is_checked_before_build_and_through_custody(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch, phase,
):
    with selected_seed(adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch) as (
        state, options, _checks, rejected,
    ):
        item = json.loads(state["routes"]["/simple/packaging/"][2])["files"][0]
        digest = "sha256:" + item["hashes"]["sha256"]
        if phase == "selection":
            rejected.add(digest)
            monkeypatch.setattr(engine, "numbered_candidate", lambda *_a, **_k: pytest.fail("revoked build"))
        elif phase == "health":
            original = seed.inspect_seed

            def inspected(*args, **kwargs):
                facts = original(*args, **kwargs)
                rejected.add(digest)
                return facts

            monkeypatch.setattr(seed, "inspect_seed", inspected)
        with pytest.raises(ValueError, match="revoked"):
            with seed.retain_packaging_seed(**options):
                assert phase == "consumption"
                rejected.add(digest)
        root = Path(options["policy"]["owner"]["runtimeRoot"])
        assert not (root / "current-version").exists()


@pytest.mark.parametrize("phase", ["acquisition", "consumption"])
@pytest.mark.parametrize("peer", ["current", "legacy"])
def test_origin_tooling_rechecks_current_feed_without_source_configuration_callback(
    adopter, configured_source, feeds, tmp_path, monkeypatch, phase, peer,
):
    import runtime_admission_bootstrap as bootstrap
    from runtime_source_authentication import retain_installation_origin

    if peer == "legacy":
        current = seed.retain_packaging_seed

        @contextmanager
        def legacy_seed(**options):
            assert "source_verifier" not in options
            with current(**options) as ready:
                yield ready

        monkeypatch.setattr(seed, "retain_packaging_seed", legacy_seed)
    source_with_process_owner(configured_source, build_only=True)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    env = environment(adopter, state, tmp_path)
    certificate = tmp_path / "origin-ca.pem"
    certificate.write_bytes(Path(env["SSL_CERT_FILE"]).read_bytes())
    original = certificate.read_bytes()
    env["SSL_CERT_FILE"] = str(certificate)
    monkeypatch.setattr(
        bootstrap, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest(),
    )
    acquire = seed.acquire_wheel_inputs

    @contextmanager
    def changed(*args, **kwargs):
        with acquire(*args, **kwargs) as acquired:
            certificate.write_bytes(b"not a certificate")
            yield acquired

    if phase == "acquisition":
        monkeypatch.setattr(seed, "acquire_wheel_inputs", changed)
        monkeypatch.setattr(seed.engine, "numbered_candidate", lambda *args, **kwargs: pytest.fail(
            "changed origin feed reached tooling construction",
        ))
    deadline = time.monotonic() + 100
    with retain_installation_origin(installation=adopter[1], deadline=deadline) as origin:
        with pytest.raises(ValueError, match="native feed configuration|certificate"):
            with bootstrap.retain_origin_tooling(
                origin, installation=adopter[1], uv=Path(shutil.which("uv")),
                python=Path(sys.executable), env=env, deadline=deadline,
                limits=seed.SeedLimits(16_384, 32 << 20, 1 << 20),
                acquisition_limits=AcquisitionLimits(
                    max_packages=1, max_requests=10, max_redirects=3, max_candidates_per_project=10,
                    max_index_bytes=16_384, max_total_metadata_bytes=32_768,
                    max_wheel_bytes=1 << 20, max_closure_bytes=1 << 20, max_temporary_bytes=4 << 20,
                ),
                wheel_limits=WheelLimits(1 << 20, 4 << 20, 1 << 20, 64 << 10, 100, 10, 32 << 10),
                procutil=worker_procutil(),
            ) as ready:
                assert phase == "consumption"
                certificate.write_bytes(b"not a certificate")
                try:
                    ready.verify()
                finally:
                    certificate.write_bytes(original)
        if phase == "consumption":
            with pytest.raises(ValueError, match="custody is closed"):
                ready.verify()
        assert not (Path(origin.policy()["owner"]["runtimeRoot"]) / "current-version").exists()


def test_public_native_feed_is_refused_before_resolution_or_any_http(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch,
):
    with selected_seed(adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch) as (
        state, options, _checks, _rejected,
    ):
        options["projections"] = tuple(
            value.replace("127.0.0.1", "pypi.org") for value in options["projections"]
        )
        original = seed.run_owned

        def only_target_probe(argv, **settings):
            assert settings["construction_fd"] is None, "unadmitted resolver"
            assert argv[1:] == ["-I", "-S", "-B", "-c", seed._TARGET], "unadmitted resolver"
            return original(argv, **settings)

        monkeypatch.setattr(seed, "run_owned", only_target_probe)
        with pytest.raises(ValueError, match="affirmative source configuration"):
            with seed.retain_packaging_seed(**options):
                pytest.fail("public native feed admitted")
        assert state["requests"] == []


@pytest.mark.parametrize("dropped", [None, "--resolution", "--exclude-newer"])
def test_owner_cli_selection_and_frozen_cutoffs_cannot_be_dropped(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch, dropped,
):
    with selected_seed(
        adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch,
        owner_options=("--resolution", "lowest"), dropped_option=dropped,
    ) as (state, options, _checks, _rejected):
        original = seed.run_owned
        commands = []

        def resolved(argv, **settings):
            if settings["construction_fd"] is None:
                return original(argv, **settings)
            assert dropped is None, "changed owner settings reached resolution"
            position = argv.index("--resolution")
            assert argv[position + 1] == "lowest"
            commands.append(argv)
            return original(argv, **settings)

        monkeypatch.setattr(seed, "run_owned", resolved)
        if dropped is None:
            with seed.retain_packaging_seed(**options):
                assert len(commands) == 1
        else:
            with pytest.raises(ValueError, match="differ from the held installation owner"):
                with seed.retain_packaging_seed(**options):
                    pytest.fail("changed owner settings were admitted")
            assert not commands and not state["requests"]


@pytest.mark.skipif(os.name == "nt", reason="native POSIX interpreter-link contract")
def test_interpreter_link_is_resolved_and_cannot_retarget_during_custody(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch,
):
    with selected_seed(adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch) as (
        _state, options, _checks, _rejected,
    ):
        link = tmp_path / "selected-python"
        link.symlink_to(options["python"])
        options["python"] = link
        with pytest.raises(ValueError, match="interpreter or selected policy changed"):
            with seed.retain_packaging_seed(**options):
                replacement = tmp_path / "not-an-interpreter"
                replacement.write_bytes(b"never executed")
                link.unlink()
                link.symlink_to(replacement)


def test_first_touch_uses_only_shipped_stdlib_helpers_without_ambient_packaging(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shipped = tmp_path / "standalone"
    shipped.mkdir()
    for name in (*installer_engine_ref.PYTHON_FILES, *installer_engine_ref.SUPPORT_FILES):
        shutil.copy2(root / "libs" / "installer-engine" / name, shipped / name)
    for path in (root / "libs" / "versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copy2(path, shipped / path.name)
    wheel = tmp_path / "packaging-25.0-py3-none-any.whl"
    wheel.write_bytes(wheel_bytes(
        name="packaging", prefix="packaging-25.0.dist-info/",
        metadata=b"Metadata-Version: 2.4\nName: packaging\nVersion: 25.0\n\n",
    ))
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "packaging.py").write_text("raise RuntimeError('ambient Packaging executed')\n")
    script = """
import sys
sys.path.insert(0, sys.argv[1])
import installer_packaging_seed as seed
from installer_wheel_inputs import WheelLimits
import time
with open(sys.argv[2], "rb") as archive:
    facts = seed.inspect_seed(
        archive, {"version": "25.0"}, limits=WheelLimits(
            100000, 100000, 20000, 10000, 30, 100, 10000,
        ), deadline=time.monotonic() + 5,
    )
assert facts.name == "packaging"
assert "packaging" not in sys.modules
assert not any(name.startswith(("runtime_profile", "runtime_source_", "build_toolchain")) for name in sys.modules)
print("stdlib-seed-first-touch")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(shipped), str(wheel)],
        cwd=poison, env={**os.environ, "PYTHONPATH": str(poison)},
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "stdlib-seed-first-touch"
