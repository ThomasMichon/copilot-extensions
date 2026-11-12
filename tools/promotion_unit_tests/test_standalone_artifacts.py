"""Pure identity, candidate inventory and build-hook refusal controls."""
from __future__ import annotations

import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import time

import pytest

import promotion_python_artifacts as artifacts
from artifact_source_build import validate_static_build
from standalone_python_artifacts import artifact_label, injected_resources, project_key


@pytest.mark.parametrize("variant", [None, "cp313d", "cp313t"])
def test_native_interpreter_request_preserves_marker_environment_and_refuses_variants(variant):
    from runtime_application_project import target_python_request

    environment = {
        "implementation_name": "cpython", "sys_platform": "win32",
        "platform_machine": "ARM64", "python_full_version": "3.13.0",
    }
    original = dict(environment)
    tags = ["cp313-cp313-win_arm64", "py3-none-any"]
    if variant:
        tags.insert(0, f"cp313-{variant}-win_arm64")
        with pytest.raises(ValueError, match="standard CPython target"):
            target_python_request(environment, tags)
    else:
        assert target_python_request(environment, tags) == "cpython-3.13.0-windows-aarch64-none"
    assert environment == original


@pytest.mark.parametrize("phase", ["complete", "profile-failed", "expired", "earlier-changed"])
def test_complete_application_portfolio_builds_once_and_publishes_only_after_readback(
    tmp_path, monkeypatch, phase,
):
    import local_install_profile as profiles
    import local_install_source as local
    import installer_engine_health as health

    deadline = time.monotonic() + 30
    clock = time.monotonic
    expired = [False]
    monkeypatch.setattr(local.time, "monotonic", lambda: deadline + 1 if expired[0] else clock())
    calls = []
    records = []
    descriptor = {"kind": "github", "canonical": "github:example/suite", "ref": ""}
    identities = {
        name: {"plugin": name, "source_commit": "a" * 40, "source_tree": "b" * 40}
        for name in ("worktree-manager", "agent-worktrees")
    }

    def admit(*args, **options):
        assert options == {"deadline": deadline}
        calls.append("admit")
        return "d" * 40, "b" * 40

    def compose(repository, commit, main, output, **options):
        permissions = local.preparation_process.procutil_module("python_cache_permissions")
        permissions.check(output.parent, private=True)
        assert (repository, commit, main) == (tmp_path, "a" * 40, "c" * 40)
        assert options == {
            "deadline": deadline, "finalized_main": False, "source_descriptor": None,
            "projects": ("standalone:worktree-manager", "agent-worktrees"),
        }
        calls.append("compose")
        output.mkdir(parents=True)
        record = {
            "source_commit": commit, "source_tree": "b" * 40,
            "composition": {"main_seed": main, "tree": "e" * 40},
            "source_descriptor": descriptor,
            "projects": {name: {"source": identity} for name, identity in identities.items()},
        }
        records.append(record)
        (output / profiles.source_inputs.MANIFEST).write_text(json.dumps(record), encoding="utf-8")
        return record

    @contextmanager
    def build(output, record, **options):
        assert options == {
            "projects": ("standalone:worktree-manager", "agent-worktrees"),
            "uv": "selected-uv", "deadline": deadline, "retain_toolchain": True,
        }
        calls.append("build")
        yield record, "retained toolchain"

    def profiles_worker(snapshot, record, output, components, extras, toolchain, **options):
        assert toolchain == "retained toolchain"
        assert options == {"uv": "selected-uv", "deadline": deadline}
        return profiles.prepare_applications(
            snapshot, output / "profiles", list(components), extras=extras, **options,
            source_record_sha256=hashlib.sha256(
                json.dumps(record, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        )

    def profile(snapshot, output, **options):
        name = options["component"]
        assert options == {
            "component": name, "uv": "selected-uv", "deadline": deadline,
            "prepare_profile": True, "extras": (),
        }
        calls.append("profile:" + name)
        if name == "agent-worktrees" and phase == "profile-failed":
            raise RuntimeError("profile actor failed")
        (output / "snapshot").mkdir(parents=True)
        (output / "snapshot/fixture.txt").write_text(name, encoding="utf-8")
        expired[0] = phase == "expired"
        return {
            "phase": "profile-prepared", "install_ready": True,
            "snapshot": str(output / "snapshot"), "source": identities[name],
        }

    @contextmanager
    def inputs(snapshot, name, **options):
        assert options == {"deadline": deadline}
        calls.append("readback:" + name)
        project = SimpleNamespace(
            identity=SimpleNamespace(
                document=lambda: identities[name], source_commit="a" * 40, source_tree="b" * 40,
            ),
            composition={"main_seed": "c" * 40, "tree": "e" * 40},
            source_descriptor=descriptor,
            project={"application": "application"}, project_key=name,
        )
        if phase == "earlier-changed" and name == "worktree-manager":
            project.composition = None
        yield project, SimpleNamespace(verify=lambda: calls.append("verify:" + name))

    monkeypatch.setattr(local.preparation, "_admit", admit)
    monkeypatch.setattr(local, "compose", compose)
    monkeypatch.setattr(local, "_built_projects", build)
    monkeypatch.setattr(local, "_prepare_profiles", profiles_worker)
    monkeypatch.setattr(local, "_reused_applications", lambda *args, **options: None)
    monkeypatch.setattr(profiles, "prepare_registry_lock", profile)
    monkeypatch.setattr(profiles.source_inputs, "project_inputs", inputs)
    lock = tmp_path / "uv.lock"
    lock.write_text("version = 1\n")
    monkeypatch.setattr(profiles.source_inputs, "_member", lambda *args: lock)
    monkeypatch.setattr(
        profiles.source_inputs, "_read",
        lambda path: records[0] if path.name == profiles.source_inputs.MANIFEST
        else {"construction": {"retained": True}},
    )
    monkeypatch.setattr(profiles.source_inputs, "application_inputs", lambda *args: {"retained": True})
    monkeypatch.setattr(
        health, "verify_application_wheels", lambda *args: None,
    )
    output = tmp_path.parent / (tmp_path.name + "-output")
    options = dict(
        components=tuple(identities), branch="dev", uv="selected-uv", deadline=deadline,
    )
    if phase == "complete":
        result = local.prepare_applications(tmp_path, "a" * 40, "c" * 40, output, **options)
        assert list(result) == list(identities)
        completion = json.loads((output / "applications.json").read_text())
        assert completion["snapshots"] == result
        assert set(completion["content"]) == set(result)
        assert calls == [
            "admit", "compose", "build", "profile:worktree-manager", "profile:agent-worktrees",
            "readback:worktree-manager", "verify:worktree-manager",
            "readback:agent-worktrees", "verify:agent-worktrees", "admit",
        ]
    else:
        with pytest.raises((ValueError, RuntimeError), match={
            "profile-failed": "profile actor failed", "expired": "deadline exceeded",
            "earlier-changed": "differs from the selected source",
        }[phase]):
            local.prepare_applications(tmp_path, "a" * 40, "c" * 40, output, **options)
        assert not (output / "applications.json").exists()
        if phase == "earlier-changed":
            assert calls[-1] == "readback:worktree-manager"
        else:
            assert not any(call.startswith("readback:") for call in calls)


@pytest.mark.parametrize("absolute_deadline", [False, True])
def test_application_cli_forwards_one_selected_portfolio(tmp_path, monkeypatch, capsys, absolute_deadline):
    import local_install_source as local

    captured = []

    def prepare(*args, **options):
        captured.append((args, options))
        print("fixture build progress")
        return {"agent-worktrees": "prepared-snapshot"}

    monkeypatch.setattr(local, "prepare_applications", prepare)
    deadline = time.monotonic() + 60
    arguments = [
        "--source-checkout", str(tmp_path), "--source-commit", "a" * 40,
        "--main-head", "c" * 40, "--output", str(tmp_path / "output"),
        "--prepare-applications", "--branch", "dev", "--uv", "selected-uv",
        *(["--deadline", str(deadline)] if absolute_deadline else ["--timeout", "60"]),
        "--project", "standalone:worktree-manager", "--project", "agent-worktrees",
        "--application-extras", '{"agent-worktrees":["fixture-extra"]}',
    ]
    before = time.monotonic()
    assert local.main(arguments) == 0
    args, options = captured[0]
    assert args == (tmp_path, "a" * 40, "c" * 40, tmp_path / "output")
    assert options["components"] == ("worktree-manager", "agent-worktrees")
    assert options["branch"] == "dev" and options["uv"] == "selected-uv"
    assert options["extras"] == {"agent-worktrees": ("fixture-extra",)}
    if absolute_deadline:
        assert options["deadline"] == deadline
    else:
        assert before + 60 <= options["deadline"] <= time.monotonic() + 60
    captured_output = capsys.readouterr()
    assert json.loads(captured_output.out) == {"agent-worktrees": "prepared-snapshot"}
    assert captured_output.err == "fixture build progress\n"
    with pytest.raises(SystemExit) as refused:
        local.main([*arguments, "--reuse-install-source", str(tmp_path / "old")])
    assert refused.value.code == 2 and len(captured) == 1


@pytest.mark.parametrize("deadline", [0, False], ids=["expired-zero", "invalid-false"])
def test_local_source_refuses_elapsed_budget_before_git_or_output(tmp_path, monkeypatch, deadline):
    import local_install_source as local

    monkeypatch.setattr(
        local.preparation, "_git",
        lambda *args, **kwargs: pytest.fail("invalid source budget reached Git"),
    )
    output = tmp_path / "output"
    with pytest.raises(local.preparation.SourcePreparationError, match="deadline"):
        local.compose(tmp_path, "a" * 40, None, output, deadline=deadline)
    assert not output.exists()


@pytest.mark.parametrize("route", ["plugin", "standalone"])
@pytest.mark.parametrize("phase", ["success", "preflight-expired", "build-expired"])
def test_shared_artifact_owner_preserves_selected_uv_and_deadline(
    tmp_path, monkeypatch, route, phase,
):
    import build_python_artifacts as builder
    import installer_wheel_inputs as inputs
    import standalone_python_artifacts as standalone

    root = tmp_path / "source"
    project = root / ("plugins/demo" if route == "plugin" else "services/pr")
    project.mkdir(parents=True)
    (project / "pyproject.toml").write_text(
        '[build-system]\nrequires=["setuptools>=68"]\nbuild-backend="setuptools.build_meta"\n'
        '[project]\nname="demo"\nversion="1.0"\n', encoding="utf-8",
    )
    for library in ("versioned-runtime", "installer-engine"):
        (root / "libs" / library).mkdir(parents=True)
    (root / "libs/agent-procutil/src/agent_procutil").mkdir(parents=True)
    monkeypatch.setattr(builder, "REPO", root)
    monkeypatch.setattr(builder, "PLUGINS_DIR", root / "plugins")
    monkeypatch.setattr(builder, "LIBS_DIR", root / "libs")
    deadline = time.monotonic() + 120
    clock = time.monotonic
    expired = [phase == "preflight-expired"]
    monkeypatch.setattr(inputs.time, "monotonic", lambda: deadline + 1 if expired[0] else clock())
    calls = []
    lock = SimpleNamespace(record={"fixture": "one-lock"}, generator="fixture", lock_id="sha256:lock")

    def resolve(path, **options):
        assert options == {"python": None, "uv": "selected-uv", "deadline": deadline}
        calls.append("resolve")
        return lock

    def build(source, output, **options):
        assert options["toolchain"] is lock
        assert options["uv"] == "selected-uv" and options["deadline"] == deadline
        calls.append("build")
        wheel = output / "demo-1.0-py3-none-any.whl"
        wheel.write_bytes(b"fixture backend output")
        expired[0] = phase == "build-expired"
        return wheel

    monkeypatch.setattr(builder, "resolve_toolchain_lock", resolve)
    monkeypatch.setattr(builder, "build_wheel", build)
    monkeypatch.setattr(builder, "read_wheel_generator", lambda _: "fixture")
    output = tmp_path / "output"
    build = (
        lambda: builder.build_plugin_artifacts(
            "demo", out_dir=output, uv="selected-uv", deadline=deadline,
        )
    ) if route == "plugin" else (
        lambda: standalone.build_standalone_artifacts(
            "pr-service", builder=builder, out_dir=output, uv="selected-uv", deadline=deadline,
        )
    )
    if phase == "success":
        record = build()
        assert record["build_toolchain"] == lock.record
        assert len(list(output.glob("*-manifest.json"))) == 1
    else:
        with pytest.raises(inputs.WheelDeadlineError):
            build()
        assert not list(output.glob("*-manifest.json"))
    assert calls == ([] if phase == "preflight-expired" else ["resolve", "build"])


@pytest.mark.parametrize("phase", ["reused", "miss", "worker-failed"])
def test_complete_output_reuse_does_not_replay_worker_failures(tmp_path, monkeypatch, phase):
    import local_install_source as local
    import build_toolchain_lock as locks

    record = {
        "schema": local.SCHEMA, "version": 1, "kind": "source",
        "source_commit": "a" * 40, "source_tree": "b" * 40,
        "projects": {"agent-worktrees": {"artifact": {"build_toolchain": {"fixture": True}}}},
    }
    permissions = local.preparation_process.procutil_module("python_cache_permissions")
    owner = local.preparation_process.procutil_module("_daemon_instance_owner")
    tmp_path = tmp_path / "private"
    permissions.mkdir(tmp_path)
    permissions.check(tmp_path, private=True)
    previous = tmp_path / "previous"
    for path in (previous, previous / "inputs", previous / "inputs/build-tools"):
        permissions.mkdir(path)
    for path, content in (
        (previous / "inputs" / local.MANIFEST, record),
        (previous / "applications.json", {
            "schema": "copilot-extensions.prepared-applications", "version": 1,
            "snapshots": {"agent-worktrees": "old"},
        }),
    ):
        with os.fdopen(owner.open_private(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY), "w") as stream:
            json.dump(content, stream)
    built = tmp_path / "incoming/inputs"
    built.mkdir(parents=True)
    calls = []

    def retained(path, **options):
        assert options["reuse_only"] is True
        return SimpleNamespace(record={"fixture": True})

    def profiles(*args, **options):
        calls.append(options["reuse_source"])
        if phase == "worker-failed":
            raise RuntimeError("worker died")
        return None if phase == "miss" else {"agent-worktrees": "retained"}

    monkeypatch.setattr(locks, "resolve_toolchain_lock", retained)
    monkeypatch.setattr(local, "_prepare_profiles", profiles)
    args = (built, record, built.parent, ("agent-worktrees",), {})
    options = dict(uv="unused", deadline=time.monotonic() + 30)
    if phase == "worker-failed":
        with pytest.raises(RuntimeError, match="worker died"):
            local._reused_applications(*args, **options)
    else:
        assert local._reused_applications(*args, **options) == (
            None if phase == "miss" else {"agent-worktrees": "retained"}
        )
    assert calls == [previous]


def test_missing_retained_toolchain_cannot_start_construction(tmp_path, monkeypatch):
    import build_toolchain_lock as locks

    target = tmp_path / "absent/retained"
    monkeypatch.setattr(locks, "_validated_trusted_index_url", lambda env: ("https://packages.example/simple", None))
    monkeypatch.setattr(locks, "_resolve_interpreter_identity", lambda *a, **kw: "selected-python")
    monkeypatch.setattr(locks, "_target_dir_for_identity", lambda *a, **kw: target)
    monkeypatch.setattr(locks, "_seasoned_install", lambda *a, **kw: pytest.fail("reuse constructed a toolchain"))
    with pytest.raises(locks.ArtifactBuildError, match="no complete retained toolchain"):
        locks.resolve_toolchain_lock(target, reuse_only=True, deadline=time.monotonic() + 30)
    assert not target.parent.exists()


@pytest.mark.parametrize("refused", ["parent", "candidate", "descendants"])
def test_automatic_reuse_checks_native_permissions_before_candidate_execution(tmp_path, monkeypatch, refused):
    import local_install_source as local
    import build_toolchain_lock as locks

    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "inputs").mkdir()
    record = {"schema": local.SCHEMA, "version": 1, "projects": {}}
    (previous / "inputs" / local.MANIFEST).write_text(json.dumps(record))
    (previous / "applications.json").write_text(json.dumps({
        "schema": "copilot-extensions.prepared-applications", "version": 1,
        "snapshots": {"demo": "candidate-controlled"},
    }))
    checks = []

    def check(path, **options):
        checks.append(path)
        if path == (tmp_path if refused == "parent" else previous) and refused != "descendants":
            raise PermissionError("untrusted preparation directory")

    def descendants(path, **options):
        checks.append(path)
        raise PermissionError("untrusted executable descendant")

    monkeypatch.setattr(local.preparation_process, "procutil_module", lambda name: SimpleNamespace(
        check=check, check_ancestors=lambda path: None, check_bytecode=descendants,
    ))
    monkeypatch.setattr(locks, "resolve_toolchain_lock", lambda *a, **kw: pytest.fail(
        "shared preparation directories cannot supply an executable toolchain",
    ))
    assert local._reused_applications(
        tmp_path / "incoming/inputs", record, tmp_path / "incoming", ("demo",), {},
        uv="unused", deadline=time.monotonic() + 10,
    ) is None
    assert checks[0] == tmp_path
    if refused != "parent":
        assert previous in checks


@pytest.mark.parametrize("outcome", ["success", "timeout", "cleanup-unconfirmed"])
def test_source_child_keeps_execution_and_cleanup_inside_the_original_deadline(monkeypatch, outcome):
    import subprocess
    import source_preparation_process as process
    from dataclasses import dataclass

    @dataclass
    class Budget:
        deadline: float
        cleanup_reserve: float

    failure = subprocess.TimeoutExpired("fixture", 8) if outcome == "timeout" else RuntimeError("unconfirmed")
    calls = []

    def run(argv, **options):
        calls.append(options)
        if outcome != "success":
            raise failure
        return subprocess.CompletedProcess(argv, 3, b"prepared\n", b"miss\n")

    monkeypatch.setattr(process.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(process, "procutil_module", lambda name: SimpleNamespace(
        run_caller_owned=run, OwnedExecutionBudget=Budget,
    ))
    if outcome == "success":
        result = process.run(["worker"], deadline=110, text=True)
        assert (result.returncode, result.stdout, result.stderr) == (3, "prepared\n", "miss\n")
    else:
        with pytest.raises(type(failure)) as caught:
            process.run(["worker"], deadline=110, text=True)
        assert caught.value is failure
    assert calls[0]["budget"] == Budget(110, 2)
    assert calls[0]["timeout"] == 10 and calls[0]["binary"] is True


def test_source_archive_copy_cannot_continue_after_the_callers_deadline(monkeypatch):
    import io
    import subprocess
    import source_preparation_process as process
    from dataclasses import dataclass

    @dataclass
    class Budget:
        deadline: float
        cleanup_reserve: float

    clock = [100.0]

    class Sink(io.BytesIO):
        def write(self, data):
            written = super().write(data)
            clock[0] = 111.0
            return written

    monkeypatch.setattr(process.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(process, "procutil_module", lambda name: SimpleNamespace(
        OwnedExecutionBudget=Budget,
        run_caller_owned=lambda argv, **kw: subprocess.CompletedProcess(
            argv, 0, b"x" * (2 << 20), b"",
        ),
    ))
    sink = Sink()
    with pytest.raises(subprocess.TimeoutExpired):
        process.run(["archive"], deadline=110, stdout=sink)
    assert len(sink.getvalue()) == 1 << 20


def test_lazy_toolchain_marker_read_keeps_original_deadline(monkeypatch, tmp_path):
    import build_toolchain_lock as locks

    deadline = time.monotonic() + 30
    reads = []
    monkeypatch.setattr(
        locks, "_query_marker_environment",
        lambda path, **options: (reads.append((path, options)) or {"fixture": True}),
    )
    selected = tmp_path / "python"
    lock = locks.ToolchainLock(selected, {}, deadline=deadline)
    assert lock.marker_environment == {"fixture": True}
    assert reads == [(selected, {"deadline": deadline})]
    monkeypatch.setattr(locks.time, "monotonic", lambda: deadline + 1)
    with pytest.raises(locks.ArtifactBuildError):
        _ = lock.marker_environment
    assert len(reads) == 1


@pytest.mark.parametrize("outcome", ["success", "miss", "failed", "wrong-snapshot"])
def test_profile_worker_only_reports_a_miss_for_explicit_reuse(tmp_path, monkeypatch, outcome):
    import local_install_source as local
    import build_toolchain_lock as locks

    deadline = time.monotonic() + 30
    previous = tmp_path / "prior"
    expected = {"agent-worktrees": str(previous / "profiles/agent-worktrees/snapshot")}
    commands = []
    monkeypatch.setattr(locks, "_query_marker_environment", lambda *a, **kw: {})

    def run(argv, **options):
        commands.append(argv)
        assert options["deadline"] == deadline
        return SimpleNamespace(
            returncode={"success": 0, "miss": 3, "failed": 1, "wrong-snapshot": 0}[outcome],
            stdout=json.dumps(expected if outcome != "wrong-snapshot" else {"agent-worktrees": "foreign"}),
            stderr="worker error",
        )

    monkeypatch.setattr(local.preparation_process, "run", run)
    args = (tmp_path, {}, tmp_path / "incoming", ("agent-worktrees",), {},
            SimpleNamespace(venv_python=Path("python"), record={}))
    options = dict(uv="uv", deadline=deadline, reuse_source=previous)
    if outcome in {"failed", "wrong-snapshot"}:
        with pytest.raises(ValueError, match="failed|another selected snapshot"):
            local._prepare_profiles(*args, **options)
    else:
        assert local._prepare_profiles(*args, **options) == (expected if outcome == "success" else None)
    assert len(commands) == 1 and "--reuse-applications" in commands[0]
    assert commands[0][0] == sys.executable
    if outcome == "miss":
        with pytest.raises(ValueError, match="preparation failed"):
            local._prepare_profiles(*args, uv="uv", deadline=deadline)


@pytest.mark.parametrize("changed", [False, True])
def test_pr_preparation_resources_refuse_substitution_without_runtime_primitives(tmp_path, changed):
    from build_toolchain_lock import ArtifactBuildError

    root = Path(__file__).resolve().parents[2]
    helper = tmp_path / "libs/versioned-runtime/standalone_build.py"
    helper.parent.mkdir(parents=True)
    helper.write_bytes((root / "libs/versioned-runtime/standalone_build.py").read_bytes())
    canonical = tmp_path / "plugins/ai-attribution/scripts"
    canonical.mkdir(parents=True)
    expected = {}
    for name in ("attribution_profile.py", "comment_preparation.py"):
        source = canonical / name
        source.write_bytes(b"# canonical preparation fixture\n")
        member = "agent_pull_requests_service/_attribution/" + name
        included = tmp_path / "services/pr/src" / member
        included.parent.mkdir(parents=True, exist_ok=True)
        included.write_bytes(b"# substituted\n" if changed else source.read_bytes())
        expected[member] = source
    if changed:
        with pytest.raises(ArtifactBuildError, match="differs from canonical"):
            injected_resources("pr-service", tmp_path)
    else:
        assert injected_resources("pr-service", tmp_path) == expected


@pytest.mark.parametrize("member", ["__init__.py", "_job_scopes.py", "_windows_job_types.py",
                                   "_output_relay.py", "streaming.py"])
@pytest.mark.parametrize("changed", [False, True])
def test_construction_pair_source_binding_refuses_each_changed_included_member(tmp_path, member, changed):
    root = Path(__file__).resolve().parents[2]
    helper = tmp_path / "libs/versioned-runtime/standalone_build.py"
    helper.parent.mkdir(parents=True)
    helper.write_bytes((root / "libs/versioned-runtime/standalone_build.py").read_bytes())
    expected = {
        "fleet_manager/_construction_procutil/" + name: (
            tmp_path / "libs/agent-procutil/src/agent_procutil" / name
        ) for name in ("__init__.py", "_job_scopes.py", "_windows_job_types.py", "_output_relay.py", "streaming.py")
    }
    for name, source in expected.items():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"# retained canonical construction fixture\n")
        included = tmp_path / "fleet-manager/src" / name
        included.parent.mkdir(parents=True, exist_ok=True)
        included.write_bytes(source.read_bytes())
    if changed:
        from build_toolchain_lock import ArtifactBuildError

        (tmp_path / "fleet-manager/src/fleet_manager/_construction_procutil" / member).write_bytes(
            b"# substituted included construction fixture\n",
        )
        with pytest.raises(ArtifactBuildError, match="differs from canonical source"):
            injected_resources("fleet-manager", tmp_path)
    else:
        actual = injected_resources("fleet-manager", tmp_path)
        assert {name: source for name, source in actual.items() if "_construction_procutil" in name} == expected


@pytest.mark.parametrize("record,label,key", [
    ({"plugin": "agent-pull-requests"}, "agent-pull-requests", "agent-pull-requests"),
    ({"component": "pr-service"}, "standalone-pr-service", "standalone:pr-service"),
    ({"component": "fleet-manager"}, "standalone-fleet-manager", "standalone:fleet-manager"),
    ({"component": "chat-service"}, "standalone-chat-service", "standalone:chat-service"),
    ({"component": "dispatch-service"}, "standalone-dispatch-service", "standalone:dispatch-service"),
    ({"component": "ssh-service"}, "standalone-ssh-service", "standalone:ssh-service"),
    ({"component": "worktree-manager"}, "standalone-worktree-manager", "standalone:worktree-manager"),
], ids=[
    "plugin", "pr-service", "fleet-manager", "chat-service", "dispatch-service",
    "ssh-service", "worktree-manager",
])
def test_explicit_standalone_identity_preserves_plugin_names(record, label, key):
    assert artifact_label(record) == label
    assert project_key(record) == key


@pytest.mark.parametrize("record", [
    {"component": "../services/pr"},
    {"component": "services/pr"},
    {"component": "unregistered-service"},
    {"component": []},
    {"component": "pr-service", "plugin": "pr-service"},
], ids=["traversal", "path", "unregistered", "invalid-type", "plugin-alias"])
def test_unknown_or_masquerading_components_refuse(record):
    with pytest.raises(artifacts.PromotionArtifactError, match="identity"):
        artifacts._artifact_label(record)


def test_candidate_inventory_keeps_same_name_plugin_and_standalone_distinct(tmp_path):
    for relative in ("plugins/pr-service", "services/pr", "fleet-manager"):
        source = tmp_path / relative
        source.mkdir(parents=True)
        (source / "pyproject.toml").write_text(
            '[project]\nname = "fixture"\nversion = "1.0"\n', encoding="utf-8",
        )
    assert artifacts.candidate_projects(tmp_path) == {
        "pr-service": "1.0", "standalone:pr-service": "1.0", "standalone:fleet-manager": "1.0",
    }


@pytest.mark.parametrize("foreign", [False, True])
def test_candidate_closure_uses_selected_root_without_mutating_reference_owner(tmp_path, monkeypatch, foreign):
    import build_python_artifacts as builder

    candidate, host = tmp_path / "candidate", tmp_path / "host"
    consumer = candidate / "fleet-manager"
    consumer.mkdir(parents=True)
    for root in (candidate, host):
        library = root / "libs" / "helper"
        (library / "src" / "helper").mkdir(parents=True)
        (library / "src" / "helper" / "__init__.py").write_text("")
        (library / "pyproject.toml").write_text(
            '[project]\nname="helper"\nversion="1"\n',
        )
    (consumer / "pyproject.toml").write_text(
        '[project]\nname="consumer"\nversion="1"\n[tool.uv.sources]\n'
        'helper={path="' + ("../../host/libs/helper" if foreign else "../libs/helper")
        + '",editable=true}\n',
    )
    monkeypatch.setattr(builder, "REPO", candidate)
    monkeypatch.setattr(builder, "LIBS_DIR", candidate / "libs")
    monkeypatch.setattr(builder.uer, "REPO", host)
    monkeypatch.setattr(builder.uer, "LIBS_DIR", host / "libs")
    if foreign:
        with pytest.raises(builder.ArtifactBuildError, match="not libs/helper"):
            builder.resolve_vendored_libs(consumer)
    else:
        assert builder.resolve_vendored_libs(consumer) == [("helper", candidate / "libs" / "helper")]
    assert builder.uer.REPO == host and builder.uer.LIBS_DIR == host / "libs"


@pytest.mark.parametrize("failure", ["missing", "malformed"])
def test_registered_candidate_metadata_cannot_be_silently_omitted(tmp_path, failure):
    source = tmp_path / "services" / "pr"
    source.mkdir(parents=True)
    if failure == "malformed":
        (source / "pyproject.toml").write_text("not TOML", encoding="utf-8")
    with pytest.raises(artifacts.PromotionArtifactError, match="invalid standalone"):
        artifacts.candidate_projects(tmp_path)


@pytest.mark.parametrize("kind", ["untrusted", "alternate", "missing-trusted", "trusted"])
def test_static_admission_allows_only_exact_selected_hook(tmp_path, kind):
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=68"]\nbuild-backend = "setuptools.build_meta"\n'
        '[project]\nname = "fixture"\nversion = "1.0"\n', encoding="utf-8",
    )
    approved = b"raise RuntimeError('not executed by admission')\n"
    if kind != "missing-trusted":
        (tmp_path / ("setup.cfg" if kind == "alternate" else "setup.py")).write_bytes(
            b"untrusted" if kind == "untrusted" else approved,
        )
    if kind == "trusted":
        assert validate_static_build(tmp_path, trusted_setup=approved)["project"]["name"] == "fixture"
    else:
        with pytest.raises(artifacts.PromotionArtifactError):
            validate_static_build(tmp_path, trusted_setup=approved)


@pytest.mark.parametrize("outcome", ["built", "reused", "reuse-miss", "failed", "invalid-admission"])
def test_export_isolates_canonical_source_from_nested_scratch(tmp_path, monkeypatch, outcome):
    scratch = tmp_path / ".promote-scratch" / "promote-fixture"
    scratch.mkdir(parents=True)
    candidate = None
    events = []
    tree, commit = "a" * 40, "b" * 40
    monkeypatch.setitem(sys.modules, "promote_release", SimpleNamespace(
        add_scratch_worktree=lambda *a, **kw: scratch,
        remove_scratch_worktree=lambda path, **kw: events.append(("remove", path)),
        _is_ancestor=lambda *a, **kw: True,
    ))
    monkeypatch.setenv("GITHUB_WORKFLOW_SHA", commit)
    monkeypatch.setenv("GITHUB_SHA", "c" * 40 if outcome == "invalid-admission" else commit)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")

    def run(argv, *, cwd):
        nonlocal candidate
        if argv[0] == "git":
            assert argv[:4] == ["git", f"--attr-source={tree}", "checkout-index", "--all"]
            assert cwd == scratch and argv[4].endswith(os.sep)
            candidate = Path(argv[4].removeprefix("--prefix="))
            assert candidate.name == "source" and candidate.parent.name == "inputs"
            assert not candidate.is_relative_to(tmp_path)
            assert not any((parent / "libs").exists() for parent in list(candidate.parents)[:2])
            (candidate / "canonical.txt").write_bytes(b"attribute-normalized\r\n")
            events.append(("checkout", candidate))
            return ""
        assert Path(argv[argv.index("--repo") + 1]) == candidate
        assert cwd == Path(artifacts.__file__).resolve().parent
        events.append(("build", candidate))
        if outcome == "failed":
            raise artifacts.PromotionArtifactError("builder failed")
        output = Path(argv[argv.index("--out-dir") + 1])
        (output / artifacts._BUNDLES_NAME).write_text("[]", encoding="utf-8")
        return ""

    def inventory(root):
        assert root == candidate
        assert (root / "canonical.txt").read_bytes() == b"attribute-normalized\r\n"
        return {}

    def reuse_source(staging, **kwargs):
        assert kwargs["candidate_root"] == candidate
        assert kwargs["content_tree"] == tree and kwargs["repo"] == tmp_path
        events.append(("reuse", candidate))
        return {"reused": True} if outcome == "reused" else None

    def validate(directory, **kwargs):
        assert kwargs["candidate_root"] == candidate and kwargs["projects"] == {}
        assert kwargs["content_tree"] == tree and kwargs["dev_head"] == commit
        assert (candidate / "canonical.txt").read_bytes() == b"attribute-normalized\r\n"
        index = json.loads((directory / artifacts.INDEX_NAME).read_text())
        assert index["content_tree"] == tree
        if outcome == "reuse-miss":
            assert index["admission"] == {
                "signer_digest": commit, "source_digest": commit, "source_ref": "refs/heads/main",
            }
        events.append(("validate", candidate))

    monkeypatch.setattr(artifacts, "_run", run)
    monkeypatch.setattr(artifacts, "candidate_projects", inventory)
    monkeypatch.setattr(artifacts, "validate_artifacts", validate)
    monkeypatch.setitem(sys.modules, "reuse_promotion_artifacts", SimpleNamespace(
        reuse_artifacts=reuse_source,
    ))
    kwargs = dict(
        repo=tmp_path, commit=commit, content_tree=tree, dev_head=commit, main_before=commit,
        out_dir=tmp_path / "export",
        reuse_repository=(
            "example/plugins" if outcome in {"reused", "reuse-miss", "invalid-admission"} else None
        ),
    )
    if outcome in {"failed", "invalid-admission"}:
        with pytest.raises(
            artifacts.PromotionArtifactError,
            match="builder failed" if outcome == "failed" else "source artifact admission",
        ):
            artifacts.export_artifacts(**kwargs)
    else:
        artifacts.export_artifacts(**kwargs)
    assert [event[0] for event in events] == {
        "built": ["checkout", "build", "validate", "remove"],
        "reused": ["checkout", "reuse", "remove"],
        "reuse-miss": ["checkout", "reuse", "build", "validate", "remove"],
        "failed": ["checkout", "build", "remove"],
        "invalid-admission": ["checkout", "reuse", "build", "remove"],
    }[outcome]
    assert candidate is not None and not candidate.parent.parent.exists()
    assert (tmp_path / "export").exists() == (outcome not in {"failed", "invalid-admission"})
    assert not list(tmp_path.glob("python-artifacts-*"))
