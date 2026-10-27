"""Actual source acquisition, build-tool admission, build and closed health."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from dataclasses import replace

import pytest
import runtime_feed_configuration as configuration
import runtime_source_build as builds
import runtime_source_wheels as completion
from installer_wheel_acquisition import AcquisitionLimits
from installer_wheel_inputs import WheelLimits
from test_installer_wheel_acquisition import CERTIFICATE
from test_installer_wheel_acquisition import feeds as feeds  # noqa: PLC0414
from test_runtime_feed_configuration import environment
from test_runtime_source_authentication import adopter as adopter  # noqa: PLC0414
from test_runtime_source_authentication import (
    configured_source as configured_source,  # noqa: PLC0414
)
from test_runtime_source_authentication import git
from test_runtime_source_lock import plan
from test_runtime_source_lock import target as target  # noqa: PLC0414
from wheel_input_fixture import wheel_bytes


def test_native_source_configuration_rechecks_without_resolving_or_contacting_feed(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch,
):
    from runtime_source_lock import retain_source_configuration

    source_with_process_owner(configured_source, build_only=True)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    env = environment(adopter, state, tmp_path)
    selected = plan(adopter)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    actual_run = subprocess.run

    def without_resolution(argv, *args, **kwargs):
        if len(argv) > 2 and list(argv[1:3]) == ["pip", "compile"] and "--show-settings" not in argv:
            pytest.fail("settings-only source capture executed dependency resolution")
        return actual_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", without_resolution)
    records, paths = [], []
    for changes in (
        {}, {}, {"exclude_newer": "2020-01-01T00:00:00Z"},
        {"resolution": "lowest"}, {"prerelease": "disallow"}, {"fork-strategy": "fewest"},
    ):
        env = environment(adopter, state, tmp_path, **changes)
        with builds.retain_authenticated_source(
            selected, installation=adopter[1], deadline=time.monotonic() + 60,
        ) as authenticated, retain_source_configuration(
            selected, payload=authenticated.payload, expected_source=authenticated.identity,
            target=target, component="fixture", uv=shutil.which("uv"), python=Path(sys.executable),
            deadline=time.monotonic() + 60, env=env, wheel_witness=True,
            include_build_tools=True, installation=adopter[1],
        ) as captured:
            captured.verify()
            record = captured.record()
            assert record["schema"] == "copilot-extensions.source-configuration"
            assert record["source"] == authenticated.identity.document()
            assert record["build_roots"] and len(captured.projections) == 2
            assert not {"pins", "files", "native_lock_sha256"} & record.keys()
            assert not captured.output.exists()
            assert not state["requests"]
            records.append(record)
            paths.append(captured.payload)
    assert records[0] == records[1]
    assert all(record["configuration_policy"] != records[0]["configuration_policy"] for record in records[2:])
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize("denied_package", ["packaging", "fixture-build-input"])
def test_current_build_input_revocation_refuses_before_tools_installation_or_backend(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch, denied_package,
):
    project = source_with_process_owner(configured_source)
    if denied_package == "fixture-build-input":
        manifest = project / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace(
            'dependencies=["agent-procutil"]',
            'dependencies=["agent-procutil","fixture-build-input==1.0"]',
        ))
        select_source(configured_source)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    build_only_feed(state)
    selected = json.loads(state["routes"][f"/simple/{denied_package}/"][2])["files"][0]
    denied = frozenset({"sha256:" + selected["hashes"]["sha256"]})
    env = environment(adopter, state, tmp_path)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    monkeypatch.setattr(builds.engine, "numbered_candidate", lambda *args, **options: pytest.fail(
        "revoked build input reached the tools constructor",
    ))
    monkeypatch.setattr(builds, "retain_source_profile", lambda *args, **options: pytest.fail(
        "revoked build input reached a backend",
    ))
    with pytest.raises(ValueError, match="revoked"):
        with builds.retain_source_runtime_inputs(
            plan(adopter), **options(adopter, target, env), revoked_inputs=lambda: denied,
        ):
            pytest.fail("revoked inputs became a runtime")


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("src")


def source_with_process_owner(configured_source, *, build_only=False):
    repository = configured_source[1]
    project = repository / "plugins" / "agent-example"
    procutil = project / "_vendor" / "procutil"
    procutil.mkdir(parents=True)
    canonical = Path(__file__).resolve().parents[1] / "libs" / "agent-procutil" / "src"
    shutil.copytree(canonical, procutil / "src")
    backend = Path(__file__).with_name("fixtures") / "source-runtime-backend.py"
    for package, name, dependencies in (
        (project, "agent-example", '["agent-procutil"]'), (procutil, "agent-procutil", "[]"),
    ):
        (package / "pyproject.toml").write_text(
            '[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\nbackend-path=["."]\n'
            f'[project]\nname="{name}"\nversion="1.0.0"\ndependencies={dependencies}\n'
            + ('[tool.uv.sources]\nagent-procutil={path="_vendor/procutil"}\n' if package == project else ""),
        )
        shutil.copyfile(backend, package / "fixture_backend.py")
    if build_only:
        manifest = project / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace(
            "requires=[]", 'requires=["fixture-build-input==1.0"]',
        ))
        backend = project / "fixture_backend.py"
        backend.write_text(backend.read_text().replace(
            "    project = tomllib.loads",
            "    import fixture_build_input\n"
            "    assert fixture_build_input.VALUE == 1\n"
            "    project = tomllib.loads",
        ))
    module = project / "src" / "agent_example"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text('VALUE = "from-authenticated-source"\n')
    select_source(configured_source)
    return project


def select_source(configured_source):
    from worktree_manager import runtime_source_config

    root, repository, _, _ = configured_source
    git(repository, "add", ".")
    git(repository, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Source build fixture")
    commit = git(repository, "rev-parse", "HEAD")
    runtime_source_config.set_source(
        runtime_source_config.RuntimeSource("exact", str(repository), commit), root=root,
    )


def test_source_acquisition_and_preparation_share_literal_checkout_semantics(
    adopter, configured_source, monkeypatch,
):
    from test_runtime_source_authentication import retain

    project = source_with_process_owner(configured_source)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.autocrlf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "true")
    with retain(adopter) as authenticated:
        authenticated.verify()
        relative = Path("src") / "agent_example" / "__init__.py"
        assert (authenticated.payload / relative).read_bytes() == (project / relative).read_bytes()


def packaging_feed(state, *, hint=">=3.9", bad_record=False):
    distribution = importlib.metadata.distribution("packaging")
    version = distribution.version
    extra = {
        str(item).replace("\\", "/"): distribution.locate_file(item).read_bytes()
        for item in distribution.files
        if str(item).startswith("packaging/") and item.suffix in (".py", ".typed")
    }
    assert "packaging/requirements.py" in extra
    filename = f"packaging-{version}-py3-none-any.whl"
    content = wheel_bytes(
        name="packaging", prefix=f"packaging-{version}.dist-info/",
        metadata=(
            f"Metadata-Version: 2.4\nName: packaging\nVersion: {version}\nRequires-Python: >=3.9\n\n"
        ).encode(),
        extra=extra, record_mutator=(lambda rows: rows[0].__setitem__(1, "sha256=AAAA")) if bad_record else None,
    )
    state["routes"]["/simple/packaging/"] = (
        200, {"Content-Type": "application/vnd.pypi.simple.v1+json"}, json.dumps({
            "meta": {"api-version": "1.2"}, "name": "packaging", "files": [{
                "filename": filename, "url": "/files/" + filename,
                "hashes": {"sha256": hashlib.sha256(content).hexdigest()},
                "upload-time": "2000-01-01T00:00:00Z",
                "requires-python": hint,
            }],
        }).encode(),
    )
    state["routes"]["/files/" + filename] = 200, {}, content


def build_only_feed(state):
    filename = "fixture_build_input-1.0-py3-none-any.whl"
    content = wheel_bytes(
        name="fixture_build_input", prefix="fixture_build_input-1.0.dist-info/",
        metadata=b"Metadata-Version: 2.4\nName: fixture-build-input\nVersion: 1.0\n\n",
    )
    state["routes"]["/simple/fixture-build-input/"] = (
        200, {"Content-Type": "application/vnd.pypi.simple.v1+json"}, json.dumps({
            "meta": {"api-version": "1.2"}, "name": "fixture-build-input", "files": [{
                "filename": filename, "url": "/files/" + filename,
                "hashes": {"sha256": hashlib.sha256(content).hexdigest()},
                "upload-time": "2000-01-01T00:00:00Z",
            }],
        }).encode(),
    )
    state["routes"]["/files/" + filename] = 200, {}, content
    return filename


def options(adopter, target, env):
    return {
        "installation": adopter[1], "target": target, "component": "fixture", "uv": shutil.which("uv"),
        "python": Path(sys.executable), "env": env, "max_lock_bytes": 65536, "max_snapshot_bytes": 4 << 20,
        "acquisition_limits": AcquisitionLimits(
            max_packages=8, max_requests=100, max_redirects=8, max_candidates_per_project=10,
            max_index_bytes=1 << 20, max_total_metadata_bytes=1 << 20,
            max_wheel_bytes=2 << 20, max_closure_bytes=4 << 20, max_temporary_bytes=10 << 20,
        ),
        "wheel_limits": WheelLimits(
            max_archive_bytes=2 << 20, max_expanded_bytes=8 << 20, max_member_bytes=2 << 20,
            max_metadata_bytes=1 << 20, max_members=10000, max_tags=100,
            max_metadata_member_bytes=65536,
        ),
        "deadline": time.monotonic() + 120,
    }


def test_native_exact_source_wheels_reuse_across_fresh_retention_and_invalidate_body_edit(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch, record_property,
):
    project = source_with_process_owner(configured_source, build_only=True)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    build_only_feed(state)
    env = environment(adopter, state, tmp_path)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    native_build = completion.build_wheel
    built = []

    def observed_build(*args, **kwargs):
        built.append(args[0].name)
        return native_build(*args, **kwargs)

    monkeypatch.setattr(completion, "build_wheel", observed_build)
    source_paths = []
    elapsed = []
    for iteration in range(3):
        if iteration == 2:
            (project / "src" / "agent_example" / "__init__.py").write_text('VALUE = "changed-body"\n')
            select_source(configured_source)
        started = time.monotonic()
        with builds.retain_source_runtime_inputs(
            plan(adopter), **options(adopter, target, env),
        ) as selected:
            source_paths.append(selected.payload)
            assert len(built) == (2 if iteration < 2 else 4)
            filename = next(
                item["filename"] for item in selected.profile.record()["wheels"]
                if item["name"] == "agent-example"
            )
            stream = selected.wheels.snapshot.archives[filename]
            with zipfile.ZipFile(stream) as archive:
                body = archive.read("agent_example/__init__.py")
            assert (b"changed-body" in body) == (iteration == 2)
        elapsed.append(time.monotonic() - started)
    assert len(set(source_paths)) == 3
    record_property("cold_warm_changed_source_seconds", json.dumps(elapsed))


@pytest.mark.parametrize("mutation", [
    "body",
    pytest.param("mode", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX source permissions")),
])
def test_backend_source_mutation_refuses_before_cache_publication_and_retry_rebuilds(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch, mutation,
):
    source_with_process_owner(configured_source, build_only=True)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    build_only_feed(state)
    env = environment(adopter, state, tmp_path)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    native_build = completion.build_wheel
    calls = []

    def mutating_build(project, *args, **kwargs):
        calls.append(project)
        if len(calls) == 1:
            module = project / "src" / "agent_example" / "__init__.py"
            if mutation == "body":
                module.write_text('VALUE = "poisoned"\n')
            else:
                module.chmod(0o700)
        return native_build(project, *args, **kwargs)

    monkeypatch.setattr(completion, "build_wheel", mutating_build)
    with pytest.raises(builds.ProfileError, match="compact source changed"):
        with builds.retain_source_runtime_inputs(plan(adopter), **options(adopter, target, env)):
            pytest.fail("mutated compact source reached construction")
    root = Path(plan(adopter).policy()["owner"]["runtimeRoot"])
    assert not list((root / ".source-wheel-cache" / "inputs").glob("*/wheel.json"))
    with builds.retain_source_runtime_inputs(plan(adopter), **options(adopter, target, env)) as selected:
        assert len(calls) == 3
        filename = next(
            item["filename"] for item in selected.profile.record()["wheels"] if item["name"] == "agent-example"
        )
        with zipfile.ZipFile(selected.wheels.snapshot.archives[filename]) as archive:
            assert b"poisoned" not in archive.read("agent_example/__init__.py")


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable source modes require a POSIX host")
def test_native_mode_only_source_change_invalidates_cached_wheel_permissions(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch,
):
    project = source_with_process_owner(configured_source, build_only=True)
    backend = project / "fixture_backend.py"
    backend.write_text(backend.read_text().replace(
        "            info.compress_type = zipfile.ZIP_DEFLATED",
        "            info.compress_type = zipfile.ZIP_DEFLATED\n"
        "            source = Path('src') / path\n"
        "            if source.is_file(): info.external_attr = source.stat().st_mode << 16",
    ))
    module = project / "src" / "agent_example" / "__init__.py"
    module.chmod(0o644)
    select_source(configured_source)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    build_only_feed(state)
    env = environment(adopter, state, tmp_path)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    identities = []
    for executable in (False, True):
        if executable:
            module.chmod(0o755)
            select_source(configured_source)
        with builds.retain_source_runtime_inputs(plan(adopter), **options(adopter, target, env)) as selected:
            identities.append(selected.identity)
            filename = next(
                item["filename"] for item in selected.profile.record()["wheels"] if item["name"] == "agent-example"
            )
            with zipfile.ZipFile(selected.wheels.snapshot.archives[filename]) as archive:
                mode = archive.getinfo("agent_example/__init__.py").external_attr >> 16
            assert bool(mode & 0o111) == executable
    assert identities[0].assembly_hash == identities[1].assembly_hash
    assert identities[0].source_tree != identities[1].source_tree


def test_authenticated_source_builds_and_reuses_independent_closed_environments(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch,
):
    source_with_process_owner(configured_source, build_only=True)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    build_filename = build_only_feed(state)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    env = environment(adopter, state, tmp_path)
    arguments = options(adopter, target, env)
    selected = plan(adopter)
    completed = []
    actual_completion = builds.retain_source_profile

    def observe_completion(*args, **kwargs):
        completed.append(kwargs)
        return actual_completion(*args, **kwargs)

    monkeypatch.setattr(builds, "retain_source_profile", observe_completion)
    routes = dict(state["routes"])
    with builds.retain_source_runtime_inputs(selected, **arguments) as source:
        record = source.profile.record()
        assert record["mode"] == "source" and record["artifact"] is None
        assert {item["name"] for item in record["wheels"]} == {"agent-example", "agent-procutil"}
        assert {name for name, _, _, _ in source.wheels.distributions} == {"agent-example", "agent-procutil"}
        assert source.local_packages == ("agent-example", "agent-procutil")
        assert source.build_slot.parent.parent.name == ".source-build-tools"
        build_receipt = json.loads((source.build_slot / ".runtime-input-origins.json").read_text())
        assert set(build_receipt["packages"]) == {"packaging", "fixture-build-input"}
        assert build_receipt["local_packages"] == []
        source_lock = source.wheels.receipt()["binding"]["source_lock"]
        assert "fixture-build-input==1.0" in source_lock["build_roots"]
        assert any(item["filename"] == build_filename for item in source_lock["files"])
        assert any(path == "/files/" + build_filename for path, _ in state["requests"])
        build_marker = builds.engine.runtime.marker_path(source.build_slot.parent.parent, source.build_slot.name)
        before = build_marker.read_bytes(), build_marker.stat().st_mtime_ns
        first_binding = source.wheels.receipt()
        build_slot = source.build_slot
        assert "packaging" not in {item["name"] for item in record["wheels"]}
        requests = len(state["requests"])
        state["routes"].clear()
        monkeypatch.setenv("UV_INDEX_URL", state["origin"] + "/must-not-resolve")
        monkeypatch.setenv("UV_CONFIG_FILE", str(tmp_path / "missing-config"))
        runtime = Path(selected.policy()["owner"]["runtimeRoot"])
        version = source.runtime_version
        candidate_options = {
            "plugin": source.payload, "uv": Path(arguments["uv"]), "base_python": Path(sys.executable),
            "health_module": "agent_example", "wheel_inputs": source.wheels,
            "local_packages": source.local_packages,
            "expected_source_identity": builds.engine.runtime_source.manifest_fields(source.identity),
        }
        with builds.engine.numbered_candidate(runtime, version, **candidate_options) as candidate:
            assert not candidate.reused and candidate.slot != source.build_slot
            python = candidate.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            proof = subprocess.run(
                [str(python), "-I", "-c",
                 ("import agent_example, importlib.metadata as m; "
                 "assert agent_example.VALUE == 'from-authenticated-source'; "
                 "assert {d.metadata['Name'] for d in m.distributions()} == {'agent-example', 'agent-procutil'}")],
                capture_output=True, text=True, check=True, timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            assert proof.returncode == 0
            receipt = (candidate.slot / ".runtime-input-origins.json").read_bytes()
        with builds.engine.numbered_candidate(runtime, version, **candidate_options) as reused:
            assert reused.reused
            assert (reused.slot / ".runtime-input-origins.json").read_bytes() == receipt
            assert builds.engine.runtime.current_version(runtime) is None
        assert len(state["requests"]) == requests
        assert not git(configured_source[1], "status", "--porcelain=v1", "--ignored")
    state["routes"].update(routes)
    with builds.retain_source_runtime_inputs(selected, **arguments) as repeated:
        assert repeated.runtime_version == version
        assert repeated.wheels.receipt() == first_binding
        assert repeated.build_slot == build_slot
        assert (build_marker.read_bytes(), build_marker.stat().st_mtime_ns) == before
        with builds.engine.numbered_candidate(
            runtime, repeated.runtime_version,
            **(candidate_options | {"plugin": repeated.payload, "wheel_inputs": repeated.wheels}),
        ) as reused:
            assert reused.reused
            assert (reused.slot / ".runtime-input-origins.json").read_bytes() == receipt
    assert len(completed) == 2
    assert all(call["offline"] and call["build_profile"] is not None for call in completed)


@pytest.mark.parametrize("reuse", [False, True])
def test_tools_interpreter_target_drift_refuses_before_backend(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch, reuse,
):
    source_with_process_owner(configured_source)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    env = environment(adopter, state, tmp_path)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    arguments = options(adopter, target, env)
    selected = plan(adopter)
    if reuse:
        with builds.retain_source_runtime_inputs(selected, **arguments):
            pass
    actual = builds._query_marker_environment
    observed = []

    def different_target(python, **kwargs):
        assert ".source-build-tools" in python.parts
        observed.append(python)
        return {**actual(python, **kwargs), "platform_machine": "different-target"}

    monkeypatch.setattr(builds, "_query_marker_environment", different_target)
    monkeypatch.setattr(completion, "build_wheel", lambda *args, **kwargs: pytest.fail("target drift reached backend"))
    with pytest.raises(ValueError, match="installed build-tools interpreter differs"), builds.retain_source_runtime_inputs(
        selected, **arguments,
    ):
        pytest.fail("target drift reached runtime construction")
    assert len(observed) == 1


@pytest.mark.parametrize("fault", ["foreign", "packages", "file", "online"])
def test_shared_completion_refuses_changed_build_role_before_backend(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch, fault,
):
    source_with_process_owner(configured_source)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    env = environment(adopter, state, tmp_path)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    actual = builds.retain_source_profile

    def changed(*args, **kwargs):
        profile = kwargs["build_profile"]
        record = profile.record()
        if fault == "foreign":
            record["component"] = "foreign"
        elif fault == "file":
            record["wheels"][0]["sha256"] = "sha256:" + "0" * 64
        elif fault == "packages":
            kwargs["toolchain"].packages["packaging"] = "0"
        else:
            kwargs["offline"] = False
        kwargs["build_profile"] = replace(profile, canonical=json.dumps(record).encode())
        return actual(*args, **kwargs)

    monkeypatch.setattr(builds, "retain_source_profile", changed)
    monkeypatch.setattr(completion, "build_wheel", lambda *args, **kwargs: pytest.fail("changed build role executed"))
    with pytest.raises(ValueError, match="build-only"), builds.retain_source_runtime_inputs(
        plan(adopter), **options(adopter, target, env),
    ):
        pytest.fail("changed build role reached runtime construction")


@pytest.mark.parametrize("failure", ["hint", "record", "build-url"])
def test_unadmitted_source_build_inputs_never_execute_a_backend(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch, failure,
):
    project = source_with_process_owner(configured_source)
    if failure == "build-url":
        manifest = project / "pyproject.toml"
        manifest.write_text(manifest.read_text().replace(
            "requires=[]", 'requires=["untrusted @ https://untrusted.invalid/package.whl"]',
        ))
        select_source(configured_source)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state, hint=">=3.8" if failure == "hint" else ">=3.9", bad_record=failure == "record")
    env = environment(adopter, state, tmp_path)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    monkeypatch.setattr(completion, "build_wheel", lambda *args, **options: pytest.fail("unadmitted backend executed"))
    message = {"hint": "Python metadata", "record": "RECORD", "build-url": "registry requirements"}[failure]
    with pytest.raises((ValueError, RuntimeError), match=message), builds.retain_source_runtime_inputs(
        plan(adopter), **options(adopter, target, env),
    ):
        pytest.fail("unadmitted source inputs reached a runtime")
    assert not (Path(plan(adopter).policy()["owner"]["runtimeRoot"]) / ".source-build-tools").exists()


@pytest.mark.parametrize("setting,message", [
    ("implicit-public", "selected indexes"),
    ("strategy", "index strategy"),
    ("credentials", "credential provider"),
    ("tls", "certificate provider"),
    ("proxy", "proxy"),
])
def test_rejected_native_source_settings_never_execute_compiler_or_request_packages(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch, setting, message,
):
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    source_with_process_owner(configured_source)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    settings = {
        "strategy": {"index_strategy": "unsafe-best-match"},
        "credentials": {"keyring_provider": "subprocess"},
    }.get(setting, {})
    env = environment(adopter, state, tmp_path, **settings)
    config = Path(env["UV_CONFIG_FILE"])
    if setting == "implicit-public":
        config.write_text("system-certs=true\n")
    elif setting == "tls":
        config.write_text(config.read_text().replace("system-certs=true", "system-certs=false"))
    elif setting == "proxy":
        env["HTTPS_PROXY"] = "http://127.0.0.1:1"
    actual = subprocess.run

    def guarded(argv, **kwargs):
        if list(argv)[1:3] == ["pip", "compile"] and "--show-settings" not in argv:
            pytest.fail("rejected native settings reached an effectful compiler")
        return actual(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded)
    monkeypatch.setattr(completion, "build_wheel", lambda *args, **kwargs: pytest.fail("rejected settings built"))
    with pytest.raises(ValueError, match=message), builds.retain_source_runtime_inputs(
        plan(adopter), **options(adopter, target, env),
    ):
        pytest.fail("rejected settings reached admitted source inputs")
    assert state["requests"] == []


def test_built_metadata_must_match_authenticated_static_declarations(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch,
):
    project = source_with_process_owner(configured_source)
    backend = project / "fixture_backend.py"
    backend.write_text(backend.read_text().replace(
        "    entries = {", '    metadata += "Requires-Dist: undeclared>=1\\n"\n    entries = {',
    ))
    select_source(configured_source)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    env = environment(adopter, state, tmp_path)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    with pytest.raises(builds.ProfileError, match="runtime dependencies differ"), builds.retain_source_runtime_inputs(
        plan(adopter), **options(adopter, target, env),
    ):
        pytest.fail("backend metadata rewrote authenticated dependency declarations")
    runtime = Path(plan(adopter).policy()["owner"]["runtimeRoot"])
    assert not (runtime / "versions").exists()


def test_backend_timeout_settles_descendants_before_releasing_source_custody(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch,
):
    import installer_engine_process

    project = source_with_process_owner(configured_source)
    writer = tmp_path / "owned-backend-writer"
    script = (
        "import pathlib,time\n"
        f"path=pathlib.Path({str(writer)!r})\n"
        "while True:\n"
        "    with path.open('a') as stream: stream.write('x')\n"
        "    time.sleep(.05)\n"
    )
    backend = project / "fixture_backend.py"
    backend.write_text(backend.read_text().replace(
        '    project = tomllib.loads',
        "    import os, subprocess, sys, time\n"
        f"    subprocess.Popen([sys.executable, '-c', {script!r}], "
        "executable=sys.executable, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)\n"
        "    time.sleep(60)\n"
        '    project = tomllib.loads',
    ))
    select_source(configured_source)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    env = environment(adopter, state, tmp_path)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    actual = installer_engine_process.run_owned

    def bounded(argv, **kwargs):
        if argv[1:3] == ["build", "--wheel"]:
            kwargs = kwargs | {"deadline": time.monotonic() + 5}
        try:
            return actual(argv, **kwargs)
        except subprocess.CalledProcessError as error:
            pytest.fail("backend did not reach timeout:\n" + error.stderr)

    monkeypatch.setattr(installer_engine_process, "run_owned", bounded)
    with pytest.raises(subprocess.TimeoutExpired), builds.retain_source_runtime_inputs(
        plan(adopter), **options(adopter, target, env),
    ):
        pytest.fail("timed-out backend reached runtime inspection")
    assert writer.exists(), "test must reach the actual descendant writer"
    size = writer.stat().st_size
    time.sleep(.3)
    assert writer.stat().st_size == size
    assert not (Path(plan(adopter).policy()["owner"]["runtimeRoot"]) / "versions").exists()


def test_revoked_source_policy_refuses_before_returning_built_inputs(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch,
):
    from test_runtime_profile_policy import owned_policy

    source_with_process_owner(configured_source)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    env = environment(adopter, state, tmp_path)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    actual = completion.build_wheel
    calls = []

    def revoke(*args, **kwargs):
        calls.append(args[0])
        built = actual(*args, **kwargs)
        owned_policy(adopter[0], adopter[1], allowSourceFallback=False)
        return built

    monkeypatch.setattr(completion, "build_wheel", revoke)
    with pytest.raises(ValueError, match="installation authority"), builds.retain_source_runtime_inputs(
        plan(adopter), **options(adopter, target, env),
    ):
        pytest.fail("revoked source policy reached runtime construction")
    assert len(calls) == 1, "revocation must refuse before a second backend executes"
