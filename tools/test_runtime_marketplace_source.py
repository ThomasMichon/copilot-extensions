"""Registered Git cache, independent acquisition and real source preparation."""
from __future__ import annotations

import json
import os
import shutil
import time

import pytest

import runtime_marketplace_source as marketplace
from runtime_profile import ProfileError, ProfileUnavailable
from test_runtime_source_authentication import configured_source as configured_source, git
from test_runtime_profile_policy_plan import adopter as adopter
from test_runtime_source_lock import plan
from worktree_manager import runtime_source, runtime_source_config


def write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def tmp_path(tmp_path_factory):
    return tmp_path_factory.mktemp("mp")


@pytest.fixture
def registered_source(adopter, configured_source):
    root, upstream, _, _ = configured_source
    installation = adopter[1]
    home = installation["copilot_home"]
    payload = installation["payload_root"]
    key = payload.parent.name
    catalog = {
        "name": key, "owner": {"name": "Fixture owner"},
        "plugins": [{"name": "agent-example", "source": "./plugins/agent-example"}],
    }
    write(upstream / ".github" / "plugin" / "marketplace.json", catalog)
    git(upstream, "add", ".")
    git(upstream, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Registered marketplace")
    commit = git(upstream, "rev-parse", "HEAD")
    remote = "https://example.invalid/registered-fixture.git"
    definition = {"source": {"source": "git", "url": remote, "ref": "main"}}
    settings = {
        "extraKnownMarketplaces": {key: definition},
        "enabledPlugins": {f"agent-example@{key}": True},
    }
    write(home / "settings.json", settings)
    write(adopter[2], settings)
    cache = home.parent / "cache"
    clone = cache / "marketplaces" / "fixture"
    clone.parent.mkdir(parents=True)
    git(upstream, "clone", "-q", "--config", "core.autocrlf=false", str(upstream), str(clone))
    git(clone, "remote", "set-url", "origin", remote)
    shutil.copytree(clone / "plugins" / "agent-example", payload, dirs_exist_ok=True)
    ledger = {
        "name": "agent-example", "marketplace": key,
        "cache_path": str(payload), "version": "1.0.0",
    }
    write(home / "config.json", {"installedPlugins": [ledger]})
    transport = home.parent / "gitconfig"
    # Test-owned Git transport maps a declared remote to an independent local
    # repository. Acquisition still clones/fetches and validates its real history.
    transport.write_text(
        f'[url "{upstream.as_uri()}"]\n\tinsteadOf = {remote}\n', encoding="utf-8",
    )
    installation["environment"] = {
        **os.environ, **installation.get("environment", {}),
        "COPILOT_HOME": str(home), "COPILOT_CACHE_HOME": str(cache),
        "GIT_CONFIG_GLOBAL": str(transport), "GIT_CONFIG_NOSYSTEM": "1",
    }
    runtime_source_config.set_source(runtime_source_config.RuntimeSource(), root=root)
    return {
        "adopter": adopter, "root": root, "upstream": upstream, "commit": commit,
        "home": home, "payload": payload, "cache": cache, "clone": clone,
        "key": key, "definition": definition, "ledger": ledger,
    }


def retain(fixture, selected_plan=None, **options):
    adopter = fixture["adopter"]
    return marketplace.retain_authenticated_marketplace_source(
        selected_plan or plan(adopter), installation=adopter[1],
        **({"deadline": time.monotonic() + 90} | options),
    )


def test_actual_registered_source_binds_independent_commit_tree_and_payload(registered_source):
    fixture = registered_source
    before = {
        name: (fixture["home"] / name).read_bytes()
        for name in ("settings.json", "config.json")
    }
    with retain(fixture) as selected:
        assert selected.identity.source_commit == fixture["commit"]
        assert selected.identity.source_tree == git(
            fixture["upstream"], "rev-parse", f"{fixture['commit']}^{{tree}}",
        )
        assert selected.payload != fixture["payload"]
        assert selected.record()["provider"] == "registered-marketplace-source"
        assert selected.record()["owner_policy"] == plan(fixture["adopter"]).policy()["policyId"]
        assert selected.record()["mode"] == "source"
        assert selected.record()["trigger"] == "release-index-unavailable"
        assert "artifact" not in selected.record()
        assert str(fixture["upstream"]).encode() not in selected.canonical
        selected.verify()
        prepared = selected.payload
    assert not prepared.exists()
    assert all((fixture["home"] / name).read_bytes() == data for name, data in before.items())
    assert not (fixture["root"] / "versions").exists()
    assert not (fixture["root"] / "current-version").exists()


def test_newer_remote_tip_is_not_substituted_for_installed_source(registered_source):
    fixture = registered_source
    (fixture["upstream"] / "later.txt").write_text("later release")
    git(fixture["upstream"], "add", ".")
    git(fixture["upstream"], "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Later main")
    assert git(fixture["upstream"], "rev-parse", "HEAD") != fixture["commit"]
    with retain(fixture) as selected:
        assert selected.identity.source_commit == fixture["commit"]


def test_explicit_copilot_home_does_not_require_an_environment_duplicate(registered_source):
    fixture = registered_source
    home = fixture["home"].with_name("custom-copilot")
    fixture["home"].rename(home)
    payload = home / "installed-plugins" / fixture["key"] / "agent-example"
    installation = fixture["adopter"][1]
    installation["copilot_home"] = home
    installation["payload_root"] = payload
    installation["environment"].pop("COPILOT_HOME")
    write(home / "config.json", {"installedPlugins": [
        {**fixture["ledger"], "cache_path": str(payload)},
    ]})
    with retain(fixture) as selected:
        assert selected.identity.source_commit == fixture["commit"]


def test_explicit_source_provenance_is_unavailable_without_an_installed_locator(
    registered_source, monkeypatch,
):
    fixture = registered_source
    fixture["adopter"][1]["source_descriptor"] = fixture["definition"]["source"]
    monkeypatch.setattr(runtime_source, "acquire", lambda *a, **k: pytest.fail("explicit origin acquired"))
    with pytest.raises(ProfileUnavailable, match="installed native origin"):
        with retain(fixture):
            pytest.fail("explicit source provenance was promoted to installed authority")


def test_empty_environment_does_not_inherit_ambient_cache_or_git_configuration(
    registered_source, monkeypatch,
):
    fixture = registered_source
    fixture["adopter"][1]["environment"] = {}
    monkeypatch.setenv("COPILOT_CACHE_HOME", str(fixture["cache"]))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fixture["home"].parent / "gitconfig"))
    monkeypatch.setattr(
        runtime_source, "acquire", lambda *a, **k: pytest.fail("ambient source configuration acquired"),
    )
    with pytest.raises(ProfileUnavailable, match="native Git source evidence is unavailable"):
        with retain(fixture):
            pytest.fail("explicitly empty environment inherited an ambient cache")


def test_none_environment_keeps_normal_ambient_selection(registered_source, monkeypatch):
    fixture = registered_source
    fixture["adopter"][1]["environment"] = None
    monkeypatch.setenv("COPILOT_CACHE_HOME", str(fixture["cache"]))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(fixture["home"].parent / "gitconfig"))
    with retain(fixture) as selected:
        assert selected.identity.source_commit == fixture["commit"]


def test_preparation_preserves_acquisitions_long_path_support(registered_source):
    fixture = registered_source
    name = "long-" + "x" * 230 + ".py"
    member = fixture["upstream"] / "plugins" / "agent-example" / name
    member.write_text("value = 1\n")
    if os.name == "nt":
        assert len(str(member)) > 260
    git(fixture["upstream"], "add", ".")
    git(fixture["upstream"], "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Long source member")
    commit = git(fixture["upstream"], "rev-parse", "HEAD")
    git(fixture["clone"], "fetch", "-q", str(fixture["upstream"]), "main")
    git(fixture["clone"], "merge", "--ff-only", "FETCH_HEAD")
    shutil.copy2(member, fixture["payload"] / name)
    with retain(fixture) as selected:
        assert selected.identity.source_commit == commit
        assert (selected.payload / name).read_bytes() == member.read_bytes()


@pytest.mark.parametrize("changed", [
    "origin", "tracked", "untracked", "assume-unchanged", "ignored", "catalog", "ambiguous",
])
def test_corrupt_native_evidence_refuses_before_independent_acquisition(
    registered_source, monkeypatch, changed,
):
    fixture = registered_source
    clone = fixture["clone"]
    if changed == "origin":
        git(clone, "remote", "set-url", "origin", "https://example.invalid/foreign.git")
    elif changed == "catalog":
        (clone / ".github" / "plugin" / "marketplace.json").write_text("{")
    elif changed == "ambiguous":
        shutil.copytree(clone, clone.with_name("duplicate"))
    else:
        member = clone / "plugins" / "agent-example" / "fixture_backend.py"
        if changed == "assume-unchanged":
            git(clone, "update-index", "--assume-unchanged", "plugins/agent-example/fixture_backend.py")
        elif changed in ("untracked", "ignored"):
            member = member.with_name("unexpected.py")
            if changed == "ignored":
                (clone / ".git" / "info" / "exclude").write_text("unexpected.py\n")
        member.write_text("changed source")
    monkeypatch.setattr(runtime_source, "acquire", lambda *a, **k: pytest.fail("invalid cache acquired"))
    with pytest.raises(ValueError, match="corrupt|ambiguous"):
        with retain(fixture):
            pytest.fail("invalid cache reached consumer")


def test_self_described_cache_commit_is_not_origin_authentication(registered_source):
    fixture = registered_source
    clone = fixture["clone"]
    (clone / "forged.txt").write_text("not in the registered upstream")
    git(clone, "add", ".")
    git(clone, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Untrusted cached commit")
    with pytest.raises(runtime_source.SourceAcquisitionError):
        with retain(fixture):
            pytest.fail("cache's remote URL authenticated a forged commit")
    assert not (fixture["root"] / "versions").exists()


@pytest.mark.parametrize("changed", ["payload", "ledger-version", "payload-version"])
def test_installed_source_must_match_actual_preparation(registered_source, changed):
    fixture = registered_source
    if changed == "payload":
        (fixture["payload"] / "fixture_backend.py").write_text("not acquired source")
    elif changed == "payload-version":
        write(fixture["payload"] / "plugin.json", {"name": "agent-example", "version": "2.0.0"})
    else:
        write(fixture["home"] / "config.json", {
            "installedPlugins": [{**fixture["ledger"], "version": "2.0.0"}],
        })
    with pytest.raises(ProfileError, match="installed marketplace payload differs"):
        with retain(fixture):
            pytest.fail("different installed source was authenticated")


@pytest.mark.parametrize("missing", ["cache", "ledger"])
def test_absent_native_evidence_is_unavailable_without_acquisition(
    registered_source, monkeypatch, missing,
):
    fixture = registered_source
    if missing == "cache":
        fixture["clone"].rename(fixture["cache"] / "held-source")
    else:
        write(fixture["home"] / "config.json", {})
    monkeypatch.setattr(runtime_source, "acquire", lambda *a, **k: pytest.fail("missing evidence acquired"))
    with pytest.raises(ProfileUnavailable, match="unavailable"):
        with retain(fixture):
            pytest.fail("absent evidence reached consumer")


@pytest.mark.parametrize("phase", ["acquisition", "consumption"])
@pytest.mark.parametrize("changed", ["registration", "ledger", "cache", "installed", "prepared"])
def test_source_and_configuration_are_rechecked_through_consumption(
    registered_source, monkeypatch, phase, changed,
):
    fixture = registered_source
    from contextlib import contextmanager

    def mutate(prepared=None):
        if changed == "registration":
            write(fixture["home"] / "settings.local.json", {
                "extraKnownMarketplaces": {
                    fixture["key"]: {"source": {**fixture["definition"]["source"], "ref": "dev"}},
                },
            })
        elif changed == "ledger":
            write(fixture["home"] / "config.json", {"installedPlugins": []})
        else:
            base = {
                "cache": fixture["clone"] / "plugins" / "agent-example",
                "installed": fixture["payload"], "prepared": prepared,
            }[changed]
            if base is not None:
                (base / "fixture_backend.py").write_text("changed during admission")

    if phase == "acquisition":
        if changed == "prepared":
            actual_prepare = marketplace.authentication.prepare_runtime_source.prepare

            def changed_prepare(*args, **kwargs):
                result = actual_prepare(*args, **kwargs)
                mutate(result.payload)
                return result

            monkeypatch.setattr(marketplace.authentication.prepare_runtime_source, "prepare", changed_prepare)
        else:
            actual_acquire = runtime_source.acquire

            @contextmanager
            def changed_acquire(*args, **kwargs):
                with actual_acquire(*args, **kwargs) as acquired:
                    mutate()
                    yield acquired

            monkeypatch.setattr(runtime_source, "acquire", changed_acquire)
    with pytest.raises((ValueError, RuntimeError)):
        with retain(fixture) as selected:
            if phase == "consumption":
                mutate(selected.payload)
            else:
                pytest.fail("changed source reached consumer")


def test_unsupported_registered_ref_never_reinterprets_the_latest_branch(
    registered_source, monkeypatch,
):
    fixture = registered_source
    definition = {"source": {**fixture["definition"]["source"], "ref": "release-tag"}}
    settings = {"extraKnownMarketplaces": {fixture["key"]: definition}}
    write(fixture["home"] / "settings.json", settings)
    write(fixture["adopter"][2], settings)
    monkeypatch.setattr(runtime_source, "acquire", lambda *a, **k: pytest.fail("unsupported ref acquired"))
    with pytest.raises(ProfileUnavailable, match="ref or layout is unsupported"):
        with retain(fixture):
            pytest.fail("registered ref was replaced")


def test_expired_provider_refuses_before_source_effects(registered_source, monkeypatch):
    monkeypatch.setattr(runtime_source, "acquire", lambda *a, **k: pytest.fail("expired source acquired"))
    with pytest.raises(ValueError, match="deadline"):
        with retain(registered_source, deadline=time.monotonic() - 1):
            pytest.fail("expired source reached consumer")


def test_two_registered_marketplaces_keep_independent_source_owners(registered_source):
    fixture = registered_source
    other_repo = fixture["upstream"].with_name("other")
    git(fixture["upstream"], "clone", "-q", str(fixture["upstream"]), str(other_repo))
    catalog_path = other_repo / ".github" / "plugin" / "marketplace.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["name"] = other_key = fixture["key"] + "-other"
    write(catalog_path, catalog)
    git(other_repo, "add", ".")
    git(other_repo, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Independent marketplace")
    other_commit = git(other_repo, "rev-parse", "HEAD")
    other_remote = "https://example.invalid/other-fixture.git"
    other_definition = {"source": {"source": "git", "url": other_remote, "ref": "main"}}
    other_clone = fixture["clone"].with_name("other")
    git(other_repo, "clone", "-q", str(other_repo), str(other_clone))
    git(other_clone, "remote", "set-url", "origin", other_remote)
    other_payload = fixture["home"] / "installed-plugins" / other_key / "agent-example"
    shutil.copytree(other_clone / "plugins" / "agent-example", other_payload)
    settings = {
        "extraKnownMarketplaces": {
            fixture["key"]: fixture["definition"], other_key: other_definition,
        },
    }
    write(fixture["home"] / "settings.json", settings)
    write(fixture["adopter"][2], settings)
    write(fixture["home"] / "config.json", {"installedPlugins": [
        fixture["ledger"],
        {**fixture["ledger"], "marketplace": other_key, "cache_path": str(other_payload)},
    ]})
    transport = fixture["home"].parent / "gitconfig"
    with transport.open("a", encoding="utf-8") as stream:
        stream.write(f'[url "{other_repo.as_uri()}"]\n\tinsteadOf = {other_remote}\n')
    with retain(fixture) as first:
        assert first.identity.source_commit == fixture["commit"]
        first_record = first.record()
    fixture["adopter"][1]["payload_root"] = other_payload
    with retain(fixture) as second:
        assert second.identity.source_commit == other_commit
        assert second.record()["owner_policy"] != first_record["owner_policy"]
        assert second.record()["selection_sha256"] != first_record["selection_sha256"]
