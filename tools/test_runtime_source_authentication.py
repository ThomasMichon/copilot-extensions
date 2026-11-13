"""Actual configured-source acquisition composed with installation-owned intent."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

import runtime_source_authentication as authentication
from runtime_profile import ProfileError, ProfileUnavailable
from test_installer_dependency_policy import source_project
from test_runtime_profile_policy_plan import adopter as adopter
from test_runtime_source_lock import plan
from test_runtime_profile_policy import owned_policy
from test_installer_wheel_acquisition import feeds as feeds
from worktree_manager import runtime_source, runtime_source_config, source_config


def git(path, *arguments):
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(
        ["git", "-c", "core.autocrlf=false", "-c", "core.longpaths=true", "-C", str(path), *arguments],
        capture_output=True, text=True, env=environment, check=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result.stdout.strip()


@pytest.fixture
def configured_source(tmp_path, monkeypatch):
    root = tmp_path / "manager"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    repository = tmp_path / "upstream"
    repository.mkdir()
    git(repository, "init", "-q", "-b", "main")
    project = source_project(repository / "plugins" / "agent-example", name="agent-example")
    manifest = project / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('version="1.0"', 'version="1.0.0"'))
    (project / "plugin.json").write_text(json.dumps({"name": "agent-example", "version": "1.0.0"}))
    git(repository, "add", ".")
    git(repository, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Synthetic source")
    commit = git(repository, "rev-parse", "HEAD")
    selection = runtime_source_config.RuntimeSource("exact", str(repository), commit)
    runtime_source_config.set_source(selection, root=root)
    return root, repository, commit, selection


def retain(adopter, **options):
    return authentication.retain_authenticated_source(
        plan(adopter), installation=adopter[1], **({"deadline": time.monotonic() + 60} | options),
    )


@pytest.mark.parametrize("with_dependency", [False, True])
def test_real_origin_discovery_plans_actual_artifact_and_only_observed_absence(
    adopter, configured_source, tmp_path, monkeypatch, feeds, with_dependency,
):
    import importlib.util
    import zipfile

    import artifact_attestation
    import artifact_release_discovery as discovery
    import build_python_artifacts as builder
    import promotion_python_artifacts as artifacts
    import runtime_profile_policy as planner
    from promotion_source_history import content_tree
    from runtime_profile_cases import _record, _wheel
    from runtime_profile_release import APPROVAL_SOURCE, SCHEMA
    from test_promotion_python_artifacts import _complete_manifest, _STATIC_BUILD, _wheel_bytes
    from packaging.markers import default_environment
    from runtime_artifact_inputs import retain_artifact_runtime_inputs
    from installer_wheel_inputs import WheelLimits
    from test_installer_wheel_acquisition import limits, worker_procutil
    from test_runtime_feed_configuration import environment
    from runtime_input_revocation import ProfileError as RevocationError

    state = feeds()
    native_environment = environment(adopter, state, tmp_path)
    dependency_bytes = _wheel_bytes("external_demo", "1.0")
    dependency = {
        **_wheel("external-demo"), "filename": "external_demo-1.0-py3-none-any.whl",
        "sha256": "sha256:" + hashlib.sha256(dependency_bytes).hexdigest(),
        "requires_python": "",
    }
    state["routes"]["/simple/external-demo/"] = (
        200, {"Content-Type": "application/vnd.pypi.simple.v1+json"}, json.dumps({
            "meta": {"api-version": "1.1"}, "name": "external-demo",
            "files": [{
                "filename": dependency["filename"], "url": "/files/" + dependency["filename"],
                "hashes": {"sha256": dependency["sha256"][7:]},
                "upload-time": "2020-01-01T00:00:00Z",
            }],
        }).encode(),
    )
    state["routes"]["/files/" + dependency["filename"]] = (200, {}, dependency_bytes)
    monkeypatch.setattr(
        "runtime_feed_configuration._opaque_index_identity",
        lambda value: hashlib.sha256(value.encode()).hexdigest(),
    )

    owner, repository, _, _ = configured_source
    plugin = repository / "plugins" / "agent-example"
    (plugin / "pyproject.toml").write_text(
        _STATIC_BUILD + '[project]\nname = "agent-example"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    if with_dependency:
        with (plugin / "pyproject.toml").open("a", encoding="utf-8") as output:
            output.write('dependencies = ["external-demo==1.0"]\n')
    module = plugin / "src" / "agent_example" / "__init__.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_bytes(b"VALUE = 'first-party fixture'\n")
    git(repository, "add", ".")
    git(repository, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Frozen first-party source")
    dev = git(repository, "rev-parse", "HEAD")
    tree = content_tree(git(repository, "rev-parse", "HEAD^{tree}"), repo=repository)
    pipeline_state = repository / ".github" / "release-pipeline-state.json"
    pipeline_state.parent.mkdir(exist_ok=True)
    pipeline_state.write_text(json.dumps({
        "schema": "copilot-extensions.release-pipeline-state", "version": 1,
        "last_promotion": {
            "dev_head": dev, "main_before": dev, "release_id": dev, "tag": "promote-dev-" + dev,
        },
    }), encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Promoted source container")
    main = git(repository, "rev-parse", "HEAD")
    runtime_source_config.set_source(
        runtime_source_config.RuntimeSource("exact", str(repository), main), root=owner,
    )
    wheels, exported = tmp_path / "wheels", tmp_path / "exported"
    wheels.mkdir()
    exported.mkdir()
    wheel = wheels / "agent_example-1.0.0-py3-none-any.whl"
    wheel.write_bytes(_wheel_bytes("agent_example", "1.0.0", members={
        "agent_example-1.0.0.dist-info/METADATA": (
            "Metadata-Version: 2.4\nName: agent-example\nVersion: 1.0.0\n"
            + ("Requires-Dist: external-demo==1.0\n" if with_dependency else "")
        ).encode(),
    }))
    spec = importlib.util.spec_from_file_location("test_frozen_artifact_builder", Path(builder.__file__))
    assert spec and spec.loader
    isolated = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(isolated)
    isolated.REPO = repository
    manifest = _complete_manifest({
        "schema": "copilot-extensions.python-artifact-manifest", "schema_version": 3,
        "plugin": "agent-example", "version": "1.0.0",
        "payload_hash": isolated.compute_payload_hash([plugin]),
        "wheels": [{
            "filename": wheel.name, "role": "plugin", "sha256": "sha256:" + artifacts._digest(wheel),
        }],
    })
    entry = artifacts.bundle_wheels(manifest, wheels, exported)
    bundle = (exported / entry["filename"]).read_bytes()
    with zipfile.ZipFile(exported / entry["filename"]) as archive:
        manifest_digest = "sha256:" + hashlib.sha256(archive.read("manifest.json")).hexdigest()
    profile = _record()
    profile.update(
        release={"identity": "agent-example:1.0.0", "sha256": manifest["payload_hash"]},
        component="agent-example", roots=["agent-example==1.0.0"], mode="artifact",
        artifact={"artifact_id": manifest["artifact_id"], "manifest_sha256": manifest_digest},
        approval_source=APPROVAL_SOURCE,
        target={"environment": default_environment(), "tags": ["py3-none-any"], "device": "cpu"},
        wheels=[{
            **_wheel("agent-example", "1.0.0"), "filename": wheel.name,
            "sha256": manifest["wheels"][0]["sha256"], "requires_python": "",
            "requires_dist": ["external-demo==1.0"] if with_dependency else [],
        }, *([dependency] if with_dependency else [])],
    )
    index = {
        "schema": discovery.INDEX_SCHEMA, "schema_version": 2,
        "dev_head": dev, "content_tree": tree, "artifacts": [entry],
        "admission": {"signer_digest": dev, "source_digest": dev, "source_ref": "refs/heads/main"},
        "runtime_profiles": {"schema": SCHEMA, "version": 1, "profiles": [profile]},
    }
    mode, calls = "available", []

    def runner(argv, **options):
        calls.append(argv)
        current = json.loads(json.dumps(index))
        if mode == "no-profile":
            del current["runtime_profiles"]
        elif mode == "forged-binding":
            current["runtime_profiles"]["profiles"][0]["artifact"]["manifest_sha256"] = "sha256:" + "f" * 64
        data = json.dumps(current).encode()
        assets = [
            {"id": 2, "name": discovery.INDEX_NAME, "state": "uploaded",
             "digest": "sha256:" + hashlib.sha256(data).hexdigest(), "size": len(data)},
            {"id": 3, "name": entry["filename"], "state": "uploaded",
             "digest": "sha256:" + entry["sha256"], "size": len(bundle)},
        ]
        if argv[0] == "attestation":
            subject = Path(argv[2])
            assert subject.read_bytes() == (bundle if subject.suffix == ".zip" else data)
            assert argv[-7:] == [
                "--signer-digest", dev, "--source-digest", dev,
                "--source-ref", "refs/heads/main", "--deny-self-hosted-runners",
            ]
            assert options == {"verification": True}
            return subprocess.CompletedProcess(argv, 0, "", "")
        assert argv[0] == "api"
        endpoint = next(value for value in argv[1:] if value.startswith("repos/"))
        if endpoint.endswith("/releases/tags/python-artifacts-" + dev):
            if mode in ("absent", "permission"):
                return subprocess.CompletedProcess(
                    argv, 1, "", f"gh: refused (HTTP {404 if mode == 'absent' else 403})",
                )
            result = {"id": 1, "tag_name": "python-artifacts-" + dev,
                      "draft": False, "prerelease": True, "assets": assets}
        elif endpoint.endswith("/git/ref/tags/python-artifacts-" + dev):
            result = {"object": {"type": "commit", "sha": dev}}
        elif endpoint.endswith("/releases/1/assets?per_page=100"):
            result = [assets]
        elif endpoint.endswith("/releases/assets/2"):
            return subprocess.CompletedProcess(argv, 0, data, b"")
        elif endpoint.endswith("/releases/assets/3"):
            return subprocess.CompletedProcess(argv, 0, bundle, b"")
        else:
            pytest.fail("unexpected discovery operation: " + repr(argv))
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    monkeypatch.setattr(artifact_attestation, "_gh", runner)
    with authentication.retain_installation_origin(
        installation=adopter[1], deadline=time.monotonic() + 90,
    ) as origin:
        for mode in ("available", "no-profile", "absent", "permission", "forged-binding"):
            directory = tmp_path / ("discovered-" + mode)
            directory.mkdir()
            arguments = dict(
                installation=adopter[1], repository="example/plugins",
                source_checkout=origin.acquired.checkout, main_commit=origin.acquired.commit,
                target=profile["target"], component="agent-example", roots=profile["roots"],
                max_subject_bytes=1 << 20, deadline=time.monotonic() + 30, runner=runner,
            )
            if mode in ("permission", "forged-binding"):
                with pytest.raises((artifacts.PromotionArtifactError, ProfileError)):
                    planner.plan_discovered_installation_profile(directory, origin, **arguments)
                continue
            selected = planner.plan_discovered_installation_profile(directory, origin, **arguments)
            if mode == "available":
                assert selected.release_selection.selected.profile.record() == profile
                assert selected.source_trigger is None
                revoked = frozenset()
                with retain_artifact_runtime_inputs(
                    selected, origin, directory, installation=adopter[1],
                    source_checkout=origin.acquired.checkout, uv="uv", python=Path(sys.executable),
                    procutil=worker_procutil(), acquisition_limits=limits(max_closure_bytes=1 << 20),
                    wheel_limits=WheelLimits(
                        1 << 20, 8 << 20, 1 << 20, 1 << 20, 1000, 1000, 1 << 20,
                    ),
                    max_snapshot_bytes=8 << 20, deadline=time.monotonic() + 60,
                    env=native_environment, revoked_inputs=lambda: revoked,
                ) as inputs:
                    inputs.verify()
                    receipt = inputs.wheels.receipt()
                    assert receipt["binding"]["profile"] == profile
                    assert set(receipt["distributions"]) == (
                        {"agent-example", "external-demo"} if with_dependency else {"agent-example"}
                    )
                    acquisition = receipt["binding"]["acquisition"]
                    if with_dependency:
                        assert [item["filename"] for item in acquisition["files"]] == [dependency["filename"]]
                    else:
                        assert acquisition is None
                    retained_paths = tuple(inputs.wheels.snapshot.paths.values())
                    revoked = frozenset({profile["wheels"][0]["sha256"]})
                    with pytest.raises(RevocationError, match="revoked"):
                        inputs.verify()
                    revoked = frozenset()
                    source = origin.acquired.checkout / "plugins" / "agent-example" / "src" / "agent_example" / "__init__.py"
                    original = source.read_bytes()
                    source.write_bytes(b"CHANGED = True\n")
                    with pytest.raises((ProfileError, artifacts.PromotionArtifactError, RuntimeError)):
                        inputs.verify()
                    source.write_bytes(original)
                    inputs.verify()
                    if with_dependency:
                        configuration = Path(native_environment["UV_CONFIG_FILE"])
                        content = configuration.read_bytes()
                        try:
                            configuration.write_bytes(content.replace(
                                b"system-certs=true", b"system-certs=false",
                            ))
                            with pytest.raises(ProfileError, match="configuration changed"):
                                inputs.verify()
                        finally:
                            configuration.write_bytes(content)
                        inputs.verify()
                with pytest.raises(ProfileError, match="custody is closed"):
                    inputs.verify()
                assert all(not path.exists() for path in retained_paths)
                assert [path for path, _ in state["requests"]] == (
                    ["/simple/external-demo/", "/files/" + dependency["filename"]]
                    if with_dependency else []
                )
            else:
                assert selected.release_selection is None
                assert selected.source_trigger == (
                    "approved-profile-unavailable" if mode == "no-profile" else "release-index-unavailable"
                )
            assert origin.record()["mode"] == "origin" and origin.record()["trigger"] is None
        assert origin.identity.source_commit == main and origin.acquired.commit == main
    assert sum(argv[0] == "attestation" for argv in calls) == 10
    assert not (adopter[1]["legacy_root"] / "current-version").exists()


def test_actual_owned_source_is_authenticated_without_an_artifact_or_self_claim(
    adopter, configured_source,
):
    root, repository, commit, selection = configured_source
    with retain(adopter) as selected:
        record = selected.record()
        assert record["source"] == selected.identity.document()
        assert selected.identity.source_commit == commit
        assert selected.identity.source_tree == git(repository, "rev-parse", f"{commit}^{{tree}}")
        assert selected.identity.plugin == plan(adopter).policy()["owner"]["pluginId"]
        assert record["owner_policy"] == plan(adopter).policy()["policyId"]
        assert record["mode"] == "source" and record["trigger"] == "release-index-unavailable"
        assert record["provider"] == "configured-runtime-source"
        assert "artifact" not in record and "approved" not in record
        assert str(repository).encode() not in selected.canonical
        assert record["selection_sha256"] == "sha256:" + hashlib.sha256(json.dumps({
            "selection": selection.document(), "owner": str(root.resolve()),
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert selected.payload != repository / "plugins" / "agent-example"
        assert not (selected.payload / ".git").exists()
        payload = selected.payload
    assert not payload.exists()
    assert not (root / "versions").exists()
    assert not (root / "current-version").exists()
    assert not git(repository, "status", "--porcelain=v1", "--ignored")


def test_source_origin_precedes_fallback_and_keeps_the_same_acquisition_owner(
    adopter, configured_source,
):
    with authentication.retain_installation_origin(
        installation=adopter[1], deadline=time.monotonic() + 60,
    ) as origin:
        assert origin.record()["mode"] == "origin"
        assert origin.record()["trigger"] is None
        assert origin.acquired.commit == configured_source[2]
        assert origin.acquired.tree == origin.identity.source_tree
        selected = origin.for_plan(plan(adopter), installation=adopter[1])
        assert selected.acquired is origin.acquired
        assert selected.payload == origin.payload
        assert selected.record()["mode"] == "source"
        assert selected.record()["trigger"] == "release-index-unavailable"
        assert origin.record()["mode"] == "origin"
        selected.verify()
        policy_path = Path(adopter[0].resolve_runtime_deployment(**adopter[1])["deployment"]["path"])
        policy_before = policy_path.read_bytes() if policy_path.exists() else None
        try:
            owned_policy(adopter[0], adopter[1], allowSourceFallback=False)
            with pytest.raises(ProfileError, match="installation authority"):
                selected.verify()
        finally:
            if policy_before is None:
                policy_path.unlink()
            else:
                policy_path.write_bytes(policy_before)


def test_source_origin_does_not_authorize_disabled_fallback(adopter, configured_source):
    owned_policy(adopter[0], adopter[1], allowSourceFallback=False)
    with authentication.retain_installation_origin(
        installation=adopter[1], deadline=time.monotonic() + 60,
    ) as origin:
        assert origin.record()["mode"] == "origin" and origin.record()["trigger"] is None
        with pytest.raises(ProfileUnavailable, match="source fallback is disabled"):
            plan(adopter)
        assert not (adopter[1]["legacy_root"] / "versions").exists()


def test_manager_self_update_preferences_do_not_authorize_runtime_source(adopter, tmp_path, monkeypatch):
    root = tmp_path / "manager"
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    source_config.set_source(repo="https://example.invalid/self-update.git", ref="dev", root=root)
    monkeypatch.setattr(runtime_source, "acquire", lambda *args, **options: pytest.fail("unselected source acquired"))
    with pytest.raises(ProfileUnavailable, match="provider is not selected"):
        with retain(adopter):
            pytest.fail("self-update policy was treated as source authentication")
    assert not (root / "runtime-sources").exists()


@pytest.mark.parametrize("phase", ["before", "consumption"])
def test_source_authentication_rechecks_actual_installation_policy(
    adopter, configured_source, phase,
):
    def change():
        owned_policy(adopter[0], adopter[1], allowSourceFallback=False)

    selected_plan = plan(adopter)
    if phase == "before":
        change()
    with pytest.raises(ProfileError, match="installation authority"):
        with authentication.retain_authenticated_source(
            selected_plan, installation=adopter[1], deadline=time.monotonic() + 60,
        ):
            change()


@pytest.mark.parametrize("phase", ["acquisition", "consumption"])
def test_source_policy_is_rechecked_without_silently_reselecting(
    adopter, configured_source, monkeypatch, phase,
):
    root = configured_source[0]
    if phase == "acquisition":
        actual = runtime_source.acquire

        @contextmanager
        def changed(*args, **options):
            with actual(*args, **options) as acquired:
                runtime_source_config.set_source(runtime_source_config.RuntimeSource(), root=root)
                yield acquired

        monkeypatch.setattr(runtime_source, "acquire", changed)
    with pytest.raises(ProfileError, match="configured runtime source changed"):
        with retain(adopter):
            runtime_source_config.set_source(runtime_source_config.RuntimeSource(), root=root)


def test_changed_prepared_source_is_not_authenticated_by_its_restored_receipt(adopter, configured_source):
    with pytest.raises(ValueError, match="bytes"):
        with retain(adopter) as selected:
            payload = selected.payload
            (payload / "fixture_backend.py").write_text("changed")
    assert not payload.exists()


def test_source_owner_root_cannot_change_during_consumption(
    adopter, configured_source, tmp_path, monkeypatch,
):
    with pytest.raises(ProfileError, match="configured runtime source changed"):
        with retain(adopter):
            monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path / "another-owner"))


@pytest.mark.parametrize("corruption", ["origin", "owner", "untracked"])
def test_corrupt_owned_checkout_does_not_fall_back_to_supplied_source(
    adopter, configured_source, corruption,
):
    root = configured_source[0]
    with retain(adopter):
        pass
    checkout = next(
        path / "checkout" for path in (root / "runtime-sources").iterdir()
        if (path / "checkout" / ".git").is_dir()
    )
    if corruption == "origin":
        git(checkout, "remote", "set-url", "origin", str(root / "foreign"))
    elif corruption == "owner":
        (checkout / ".git" / "runtime-source-owner.json").write_text("{}")
    else:
        (checkout / "foreign.py").write_text("not selected")
    with pytest.raises(runtime_source.SourceAcquisitionError):
        with retain(adopter):
            pytest.fail("corrupt source checkout was authenticated")


def test_unmerged_exact_selection_refuses_instead_of_substituting_main(adopter, configured_source):
    root, repository, _, _ = configured_source
    git(repository, "switch", "-qc", "dev")
    (repository / "extra.txt").write_text("unmerged")
    git(repository, "add", ".")
    git(repository, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Unmerged")
    selected = git(repository, "rev-parse", "HEAD")
    git(repository, "switch", "-q", "main")
    runtime_source_config.set_source(
        runtime_source_config.RuntimeSource("exact", str(repository), selected), root=root,
    )
    with pytest.raises(runtime_source.SourceAcquisitionError, match="not merged"):
        with retain(adopter):
            pytest.fail("unmerged source was substituted with main")


def test_deadline_refuses_before_source_side_effects(adopter, configured_source, monkeypatch):
    monkeypatch.setattr(runtime_source, "acquire", lambda *args, **options: pytest.fail("expired source acquired"))
    with pytest.raises(ValueError, match="deadline"):
        with authentication.retain_authenticated_source(
            plan(adopter), installation=adopter[1], deadline=time.monotonic() - 1,
        ):
            pytest.fail("expired authentication reached a consumer")
    assert not (configured_source[0] / "runtime-sources").exists()
