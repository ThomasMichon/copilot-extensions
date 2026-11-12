"""Revalidate real prepared wheel/profile bytes without rebuilding or installing."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import local_install_source as local


@pytest.fixture
def application_portfolio(tmp_path, monkeypatch):
    import build_toolchain_lock as locks

    permissions = local.preparation_process.procutil_module("python_cache_permissions")
    owner = local.preparation_process.procutil_module("_daemon_instance_owner")
    parent = tmp_path / "portfolios"
    owner.mkdir_private(parent)
    repository = tmp_path / "repository"
    repository.mkdir()
    state = SimpleNamespace(parent=parent, repository=repository, builds=0, compositions=0)
    toolchain = SimpleNamespace(record={"fixture": True})

    def write(path, content):
        with os.fdopen(owner.open_private(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY), "w") as stream:
            stream.write(content)

    def compose(repository, commit, main, output, **options):
        permissions.check(output.parent, private=True)
        state.compositions += 1
        owner.mkdir_private(output)
        record = {
            "schema": local.SCHEMA, "version": 1, "kind": "source",
            "source_commit": commit, "source_tree": "b" * 40, "main_head": main,
            "projects": {"demo": {"artifact": {"build_toolchain": toolchain.record}}},
        }
        write(output / local.MANIFEST, json.dumps(record))
        return record

    @contextmanager
    def build(output, record, **options):
        state.builds += 1
        owner.mkdir_private(output / "build-tools")
        yield record, toolchain

    def profiles(built, record, output, components, extras, toolchain, **options):
        previous = options.get("reuse_source")
        if previous is not None:
            return json.loads((previous / "applications.json").read_text())["snapshots"]
        snapshot = output / "profiles/demo/snapshot"
        for path in (output / "profiles", snapshot.parent, snapshot):
            owner.mkdir_private(path)
        write(snapshot / "fixture.txt", "prepared")
        write(output / "applications.json", "")
        return {"demo": str(snapshot)}

    monkeypatch.setattr(local.preparation, "_admit", lambda *a, **kw: ("d" * 40, "b" * 40))
    monkeypatch.setattr(local, "compose", compose)
    monkeypatch.setattr(local, "_built_projects", build)
    monkeypatch.setattr(local, "_prepare_profiles", profiles)
    monkeypatch.setattr(locks, "resolve_toolchain_lock", lambda *a, **kw: toolchain)

    def prepare(output):
        return local.prepare_applications(
            repository, "a" * 40, "c" * 40, output, components=("demo",),
            branch="dev", uv="unused", deadline=time.monotonic() + 30,
        )

    state.prepare = prepare
    return state


def test_fresh_application_root_is_private_and_complete_repeat_reuses(application_portfolio):
    state = application_portfolio
    first = state.parent / "first"
    old_umask = os.umask(0o022)
    try:
        prepared = state.prepare(first)
    finally:
        os.umask(old_umask)
    permissions = local.preparation_process.procutil_module("python_cache_permissions")
    permissions.check_bytecode(first, max_entries=100, seconds=10)
    assert state.prepare(state.parent / "second") == prepared
    assert (first / "applications.json").is_file()
    assert state.builds == 1 and state.compositions == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX sticky directory semantics")
def test_fresh_application_accepts_trusted_sticky_parent_without_reusing_siblings(application_portfolio):
    state = application_portfolio
    state.parent.chmod(0o1777)
    assert state.prepare(state.parent / "first")
    assert state.prepare(state.parent / "second")
    assert state.builds == 2


@pytest.mark.parametrize("where", ["parent", "ancestor", "foreign-parent"])
@pytest.mark.skipif(os.name == "nt", reason="POSIX access bits and ownership")
def test_fresh_application_refuses_replaceable_parent_chain(application_portfolio, monkeypatch, where):
    state = application_portfolio
    target = state.parent.parent if where == "ancestor" else state.parent
    original = target.stat().st_mode
    if where == "foreign-parent":
        lstat = Path.lstat

        def foreign(path, *args, **options):
            info = lstat(path, *args, **options)
            if path == target:
                values = list(info)
                values[4] = os.getuid() + 1
                return os.stat_result(values)
            return info

        monkeypatch.setattr(Path, "lstat", foreign)
    else:
        target.chmod(0o777)
    try:
        with pytest.raises(PermissionError):
            state.prepare(state.parent / "refused")
        assert state.compositions == 0
        assert not (state.parent / "refused").exists()
    finally:
        if where != "foreign-parent":
            target.chmod(original)


@pytest.mark.parametrize("where", ["leaf", "parent", "ancestor"])
def test_fresh_application_refuses_linked_output_chain(application_portfolio, where):
    state = application_portfolio
    link = state.parent / "linked"
    target = state.parent / "target"
    target.mkdir()
    if os.name == "nt":
        subprocess.run(
            [os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(target)],
            check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        link.symlink_to(target, target_is_directory=True)
    if where == "ancestor":
        (target / "nested").mkdir()
    output = link if where == "leaf" else link / (
        "nested/refused" if where == "ancestor" else "refused"
    )
    with pytest.raises((ValueError, PermissionError)):
        state.prepare(output)
    assert state.compositions == 0
    assert not (target / "applications.json").exists()
    assert not (target / "refused").exists()


@pytest.mark.parametrize("previous", ["ordinary", "shared", "foreign", "partial"])
def test_fresh_application_never_repairs_or_reenters_existing_root(
    application_portfolio, monkeypatch, previous,
):
    state = application_portfolio
    output = state.parent / "existing"
    output.mkdir()
    marker = output / "retained"
    marker.write_bytes(b"previous attempt")
    if previous == "shared" and os.name != "nt":
        output.chmod(0o777)
    if previous == "foreign":
        permissions = local.preparation_process.procutil_module("python_cache_permissions")
        check = permissions.check

        def foreign(path, **options):
            if path == output:
                raise PermissionError("foreign root")
            return check(path, **options)

        monkeypatch.setattr(permissions, "check", foreign)
    if previous == "partial":
        (output / "inputs").mkdir()
    mode = output.stat().st_mode
    with pytest.raises(ValueError, match="must be new"):
        state.prepare(output)
    assert marker.read_bytes() == b"previous attempt"
    assert output.stat().st_mode == mode and state.compositions == 0


def test_fresh_application_claim_is_exclusive_before_composition(application_portfolio, monkeypatch):
    state = application_portfolio
    output = state.parent / "racing"
    owner = local.preparation_process.procutil_module("_daemon_instance_owner")
    mkdir = owner.mkdir_private
    claimed = []

    def competitor(path, **options):
        mkdir(path)
        (path / "competitor").write_text("retained")
        claimed.append(path)
        mkdir(path, **options)

    monkeypatch.setattr(owner, "mkdir_private", competitor)
    with pytest.raises(FileExistsError):
        state.prepare(output)
    assert claimed and (output / "competitor").read_text() == "retained"
    assert state.compositions == 0


def test_concurrent_fresh_preparers_have_one_owner(application_portfolio, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, local as thread_local

    state = application_portfolio
    barrier, entered = Barrier(2), thread_local()

    def admit(*args, **options):
        if not getattr(entered, "first", False):
            entered.first = True
            barrier.wait(timeout=5)
        return "d" * 40, "b" * 40

    def prepare():
        try:
            return state.prepare(state.parent / "concurrent")
        except FileExistsError as error:
            return error

    monkeypatch.setattr(local.preparation, "_admit", admit)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(prepare) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, FileExistsError) for result in results) == 1
    assert state.compositions == 1 and state.builds == 1


def test_failed_fresh_preparation_retains_private_partial_root_without_reusing_it(
    application_portfolio, monkeypatch,
):
    state = application_portfolio
    profiles = local._prepare_profiles
    first = state.parent / "partial"

    def fail(*args, **options):
        raise RuntimeError("profile failed")

    monkeypatch.setattr(local, "_prepare_profiles", fail)
    with pytest.raises(RuntimeError, match="profile failed"):
        state.prepare(first)
    permissions = local.preparation_process.procutil_module("python_cache_permissions")
    permissions.check(first, private=True)
    assert (first / "inputs").is_dir() and not (first / "applications.json").exists()
    with pytest.raises(ValueError, match="must be new"):
        state.prepare(first)
    monkeypatch.setattr(local, "_prepare_profiles", profiles)
    assert state.prepare(state.parent / "next") != {}
    assert state.builds == 2 and state.compositions == 2


@pytest.fixture
def reusable_source(tmp_path, monkeypatch):
    import build_python_artifacts as builder
    import build_toolchain_lock
    import promotion_python_artifacts as artifacts
    from test_promotion_python_artifacts import _complete_manifest, _wheel_bytes
    from versioned_runtime_source import SOURCE_FILE, SourceIdentity, assembly_hash, runtime_version

    previous = tmp_path / "previous"
    source = previous / "source"
    payload = source / "plugins/demo"
    package = payload / "src/demo"
    package.mkdir(parents=True)
    body = b"VALUE = 'first-party fixture'\n"
    (package / "__init__.py").write_bytes(body)
    (payload / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools>=68"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="demo"\nversion="1.0.0"\n',
    )
    (payload / "plugin.json").write_text('{"name":"demo","version":"1.0.0"}\n')
    digest = assembly_hash(payload)
    identity = SourceIdentity(
        "demo", "1.0.0", "a" * 40, "b" * 40, digest,
        runtime_version("1.0.0", "a" * 40, digest),
    )
    (payload / SOURCE_FILE).write_text(json.dumps(identity.document(), sort_keys=True) + "\n")
    wheel = previous / "wheels/demo/demo-1.0.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(_wheel_bytes("demo", "1.0.0", payload=body))
    trusted = local._load_module(Path(builder.__file__), "_local_reuse_fixture_builder")
    trusted.REPO, trusted.PLUGINS_DIR, trusted.LIBS_DIR = source, source / "plugins", source / "libs"
    manifest = _complete_manifest({
        "schema": "copilot-extensions.python-artifact-manifest", "schema_version": 3,
        "plugin": "demo", "version": "1.0.0", "payload_hash": trusted.compute_payload_hash([payload]),
        "wheels": [{
            "filename": wheel.name, "role": "plugin",
            "sha256": "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest(),
        }],
    })
    manifest["wheels"][0]["source"] = "plugins/demo"
    record = {
        "schema": local.SCHEMA, "version": 1, "kind": "source",
        "source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40,
        "composition": {"main_seed": "c" * 40, "tree": "d" * 40},
        "projects": {"demo": {
            "payload": "source/plugins/demo", "wheels": "wheels/demo",
            "source": identity.document(), "artifact": manifest,
        }},
    }
    (previous / local.MANIFEST).write_text(json.dumps(record))
    output = tmp_path / "next"
    shutil.copytree(source, output / "source")
    monkeypatch.setattr(build_toolchain_lock, "resolve_toolchain_lock", lambda *a, **kw: pytest.fail(
        "reusing unchanged inputs must not provision a build toolchain",
    ))
    monkeypatch.setattr(builder, "build_wheel", lambda *a, **kw: pytest.fail(
        "reusing unchanged inputs must not build first-party wheels",
    ))
    monkeypatch.setattr(artifacts, "candidate_projects", lambda root: {"demo": "1.0.0"})
    return previous, output, {**record, "projects": {}}, record


@pytest.mark.parametrize("changed", [None, "source", "composition", "wheel", "policy"])
def test_prepared_wheel_reuse_revalidates_inputs_without_build_fallback(reusable_source, changed):
    previous, output, record, original = reusable_source
    if changed == "source":
        (output / "source/plugins/demo/src/demo/__init__.py").write_text("CHANGED = True\n")
    elif changed == "composition":
        record["composition"] = {**record["composition"], "tree": "e" * 40}
    elif changed == "wheel":
        (previous / "wheels/demo/demo-1.0.0-py3-none-any.whl").write_bytes(b"damaged")
    options = dict(
        projects=("demo",), reuse_source=previous,
        source_policy="seasoned-public" if changed == "policy" else "governed",
    )
    if changed:
        with pytest.raises((ValueError, RuntimeError)):
            local.build_projects(output, record, **options)
        assert record["projects"] == {}
        assert not (output / local.MANIFEST).exists()
    else:
        assert local.build_projects(output, record, **options)["projects"] == original["projects"]
        assert (output / "wheels/demo/demo-1.0.0-py3-none-any.whl").read_bytes() == (
            previous / "wheels/demo/demo-1.0.0-py3-none-any.whl"
        ).read_bytes()
    assert not (output / "build-tools").exists()


@pytest.mark.parametrize("fault", [
    None, "completion", "wheel", "application", "extras", "target", "policy",
    "toolchain", "selected-source", "missing-copy", "damaged-copy",
])
def test_complete_application_reuse_revalidates_real_outputs_without_constructing(
    reusable_source, tmp_path, monkeypatch, fault,
):
    import local_install_profile as native
    from packaging.markers import default_environment
    from packaging.tags import sys_tags
    from runtime_profile import ProfileUnavailable

    previous, output, record, original = reusable_source
    completed = tmp_path / "completed"
    shutil.copytree(previous, completed / "inputs")
    selected = native.source_inputs.select_project(completed / "inputs", "demo")
    artifact = selected.project["artifact"]
    archives = {
        row["filename"]: completed / "inputs" / selected.project["wheels"] / row["filename"]
        for row in artifact["wheels"]
    }
    expected = {row["filename"]: row["sha256"] for row in artifact["wheels"]}
    deadline = time.monotonic() + 60
    target = {"environment": default_environment(), "tags": [str(tag) for tag in sys_tags()], "device": "cpu"}
    monkeypatch.setattr(native, "_query_marker_environment", lambda *a, **kw: target["environment"])
    policy = {"policyId": "fixture-policy"}
    monkeypatch.setattr(native, "_profile_policy", lambda *args: (("fixture", None), policy))
    with native.retain_wheel_inputs(
        archives, expected, max_wheels=256, max_archive_bytes=128 << 20,
        max_total_bytes=512 << 20, max_snapshot_bytes=1 << 30, deadline=deadline,
    ) as retained:
        profile = native.complete_profile(
            selected, target, ["demo==1.0.0"], retained,
            policy_id=policy["policyId"], acquisition_id="fixture-acquisition", deadline=deadline,
        )
        snapshot = native._write_snapshot(
            selected, completed / "profiles/demo", profile, retained, deadline=deadline,
        )
    snapshots = {"demo": str(snapshot)}
    with native.source_inputs.project_inputs(snapshot, "demo", deadline=deadline) as (observed, inputs):
        construction = native.source_inputs.application_inputs(observed, inputs)
    application = snapshot / "applications/demo"
    application.mkdir(parents=True)
    (application / "wheels").mkdir()
    for filename, archive in archives.items():
        shutil.copyfile(archive, application / "wheels" / filename)
    (application / "application.json").write_text(json.dumps({"construction": construction}))
    (application / "uv.lock").write_text("version = 1\n")
    snapshot_record = native.source_inputs._read(snapshot / local.MANIFEST)
    snapshot_record["projects"]["demo"]["application"] = "applications/demo"
    (snapshot / local.MANIFEST).write_text(json.dumps(snapshot_record))
    completion = local._application_completion(snapshots, deadline=deadline)
    if fault == "completion":
        completion["content"]["demo"] = "0" * 64
    elif fault == "wheel":
        (snapshot / "wheels/demo" / next(iter(archives))).write_bytes(b"damaged")
    elif fault == "application":
        (application / "application.json").write_text('{"construction": {}}\n')
    elif fault == "missing-copy":
        (application / "wheels" / next(iter(archives))).unlink()
    elif fault == "damaged-copy":
        (application / "wheels" / next(iter(archives))).write_bytes(b"damaged")
    if fault in {"wheel", "application", "missing-copy", "damaged-copy"}:
        completion = local._application_completion(snapshots, deadline=deadline)
    (completed / "applications.json").write_text(json.dumps(completion))
    (output / local.MANIFEST).write_text(json.dumps(record))
    digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
    toolchain_record = json.loads(json.dumps(artifact["build_toolchain"]))
    if fault == "toolchain":
        toolchain_record["lock_id"] = "sha256:" + "0" * 64
    if fault == "target":
        monkeypatch.setattr(native, "_query_marker_environment", lambda *a, **kw: {
            **target["environment"], "platform_machine": "different",
        })
    if fault == "policy":
        policy["policyId"] = "changed-policy"
    if fault == "selected-source":
        reuse = local._reuse_projects

        def changed(*args, **kwargs):
            result = reuse(*args, **kwargs)
            (output / "source/plugins/demo/src/demo/__init__.py").write_text("CHANGED = True\n")
            return result

        monkeypatch.setattr(local, "_reuse_projects", changed)
    monkeypatch.setattr(native, "prepare_registry_lock", lambda *a, **kw: pytest.fail(
        "complete reuse must not prepare another native profile",
    ))
    options = dict(
        extras={"demo": ["bootstrap"]} if fault == "extras" else {}, deadline=deadline,
        source_record_sha256=digest, toolchain_record=toolchain_record,
    )
    if fault:
        with pytest.raises(native.ProfileError if fault == "selected-source" else ProfileUnavailable):
            native.reuse_applications(output, completed, ["demo"], **options)
    else:
        assert native.reuse_applications(output, completed, ["demo"], **options) == snapshots
        assert json.loads((completed / "applications.json").read_text()) == completion
        assert native.source_inputs.select_project(snapshot, "demo").identity == selected.identity
    assert not (output / "wheels").exists()
    assert not (output / "build-tools").exists()
    assert record["projects"] == {}
    assert original["projects"]["demo"]["artifact"] == artifact
