"""Actual configured-source acquisition composed with installation-owned intent."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import subprocess
import time

import pytest

import runtime_source_authentication as authentication
from runtime_profile import ProfileError, ProfileUnavailable
from test_installer_dependency_policy import source_project
from test_runtime_profile_policy_plan import adopter as adopter
from test_runtime_source_lock import plan
from test_runtime_profile_policy import owned_policy
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
