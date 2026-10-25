"""Existing native Git caches are availability, never replacement source authority."""

import json
import os
import shutil
import subprocess
import sys

import pytest

from plugin_resolve.preparation import prepare
from plugin_resolve.reflection import build_plan, build_prepare_python, parse_prepared_context
from plugin_resolve.registration import atomic_json
from plugin_resolve.settings import read_repo_settings
from test_plugin_preparation import init_checkout
from test_source_preserving_preparation import git, git_origin as git_origin_fixture

git_origin = git_origin_fixture


@pytest.fixture
def native_inventory(tmp_path, git_origin):
    origin, first, _second = git_origin
    target, home, cache = (tmp_path / name for name in ("target", "home", "cache"))
    init_checkout(target)
    home.mkdir()
    atomic_json(home / "settings.json", {"unrelated": "preserve"})
    clone = cache / "marketplaces" / "native-clone"
    clone.parent.mkdir(parents=True)
    git(tmp_path, "clone", "--quiet", str(origin), str(clone))
    git(clone, "checkout", "--quiet", first)
    definition = {
        "source": {
            "source": "git",
            "url": origin.as_uri(),
            "ref": "first",
            "path": "catalog-a",
        }
    }
    environment = {
        **os.environ,
        "COPILOT_HOME": str(home),
        "COPILOT_CACHE_HOME": str(cache),
    }
    return target, home, clone, definition, environment


def request(target, definition):
    return {
        "repo": str(target),
        "local_mode": "reference",
        **build_plan(
            [{"source": "helper@git-fixture"}],
            [],
            marketplaces={"git-fixture": definition},
        ),
    }


def no_install(*args, **kwargs):
    raise AssertionError("cold installation was selected")


def test_native_git_cache_reuses_qualified_payload_without_global_registration(
    native_inventory,
    monkeypatch,
):
    target, home, clone, definition, environment = native_inventory
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    before = (home / "settings.json").read_bytes()
    context = {}
    expected = {"helper@git-fixture": str(clone / "catalog-a" / "helper")}
    assert (
        prepare(
            request(target, definition),
            environment=environment,
            prepared_context=context,
        )
        == expected
    )
    assert prepare(request(target, definition), environment=environment) == expected
    assert read_repo_settings(target).marketplaces["git-fixture"] == definition
    assert (home / "settings.json").read_bytes() == before
    assert not (home / "installed-plugins").exists()
    assert context == {
        "cwd": str(target),
        "logical_payloads": expected,
        "plugins": [
            {"projected_source": "helper@git-fixture", "logical_source": "helper@git-fixture"}
        ],
    }


def test_native_git_inventory_streams_to_fresh_stdlib_only_receiver(native_inventory):
    target, home, clone, definition, environment = native_inventory
    environment = {
        **environment,
        "HOME": str(home),
        "USERPROFILE": str(home),
        "COPILOT_PATH": sys.executable,
    }
    code = build_prepare_python(
        request(target, definition),
        str(target),
        include_prepared_context=True,
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-"],
        input=code,
        cwd=target,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    context = parse_prepared_context(result.stdout, expected_repo=str(target))
    assert context["logical_payloads"] == {
        "helper@git-fixture": str(clone / "catalog-a" / "helper"),
    }
    assert context["plugins"] == [
        {
            "projected_source": "helper@git-fixture",
            "logical_source": "helper@git-fixture",
        }
    ]
    assert not (home / "installed-plugins").exists()
    assert read_repo_settings(target).marketplaces["git-fixture"] == definition


@pytest.mark.parametrize(
    "changed", ["origin", "revision", "tracked", "untracked", "ignored", "global"]
)
def test_unqualified_native_git_cache_uses_ordinary_installation(
    native_inventory,
    monkeypatch,
    changed,
):
    target, home, clone, definition, environment = native_inventory
    if changed == "origin":
        git(clone, "remote", "set-url", "origin", "https://example.invalid/other.git")
    elif changed == "revision":
        definition["source"]["ref"] = "HEAD"
        definition["source"]["sha"] = git(clone, "rev-parse", "origin/HEAD")
    elif changed == "tracked":
        (clone / "catalog-a" / "helper" / "marker.txt").write_text("modified")
    elif changed in {"untracked", "ignored"}:
        (clone / "catalog-a" / "helper" / "extra.py").write_text("unexpected")
        if changed == "ignored":
            (clone / ".git" / "info" / "exclude").write_text("extra.py\n")
    else:
        atomic_json(
            home / "settings.json",
            {
                "extraKnownMarketplaces": {
                    "git-fixture": {"source": {"source": "github", "repo": "other/catalog"}},
                }
            },
        )
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)
    assert not (target / ".github" / "copilot" / "settings.local.json").exists()


def test_native_git_cache_keeps_explicit_target_disable(native_inventory, monkeypatch):
    target, _home, _clone, definition, environment = native_inventory
    path = target / ".github" / "copilot" / "settings.json"
    path.parent.mkdir(parents=True)
    atomic_json(
        path,
        {
            "extraKnownMarketplaces": {"git-fixture": definition},
            "enabledPlugins": {"helper@git-fixture": False},
        },
    )
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    assert prepare(request(target, definition), environment=environment) == {}
    assert read_repo_settings(target).enabled["helper@git-fixture"] is False
    assert json.loads(path.read_text())["extraKnownMarketplaces"]["git-fixture"] == definition


@pytest.mark.parametrize("selector", ["github", "sha", "path", "runtime-cache", "unrelated"])
def test_native_git_cache_preserves_supported_source_selectors(
    native_inventory,
    monkeypatch,
    selector,
):
    target, _home, clone, definition, environment = native_inventory
    folder = "catalog-a"
    if selector == "github":
        git(clone, "remote", "set-url", "origin", "git@github.com:example/catalog.git")
        definition["source"] = {
            **definition["source"],
            "source": "github",
            "repo": "example/catalog",
        }
        del definition["source"]["url"]
    elif selector == "sha":
        definition["source"]["sha"] = git(clone, "rev-parse", "HEAD")
        definition["source"]["ref"] = "unresolved-ref-overridden-by-sha"
    elif selector == "path":
        definition["source"]["path"] = folder = "catalog-b"
    elif selector == "runtime-cache":
        generated = clone / folder / "helper" / ".venv"
        generated.mkdir()
        (generated / "generated.txt").write_text("runtime cache is not source")
    else:
        (clone / "catalog-b" / "helper" / "marker.txt").write_text("unrelated change")
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    assert prepare(request(target, definition), environment=environment) == {
        "helper@git-fixture": str(clone / folder / "helper"),
    }
    assert read_repo_settings(target).marketplaces["git-fixture"] == definition


def test_ambiguous_native_git_clones_refuse_instead_of_selecting_first(
    native_inventory,
    monkeypatch,
):
    target, _home, clone, definition, environment = native_inventory
    shutil.copytree(clone, clone.with_name("second-clone"))
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(ValueError, match="inventory is ambiguous"):
        prepare(request(target, definition), environment=environment)
    assert not (target / ".github" / "copilot" / "settings.local.json").exists()


@pytest.mark.parametrize("mode", ["copy", "unsupported-selector"])
def test_native_git_cache_does_not_expand_preparation_authority(
    native_inventory,
    monkeypatch,
    mode,
):
    target, _home, _clone, definition, environment = native_inventory
    value = request(target, definition)
    if mode == "copy":
        value["local_mode"] = "copy"
    else:
        definition["source"]["unknown-selector"] = "cannot-qualify"
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(value, environment=environment)


@pytest.mark.parametrize("boundary", ["publication", "effective-resolution"])
@pytest.mark.parametrize("drift", ["origin", "floating-revision"])
def test_native_git_inventory_revalidates_before_settings_publication(
    native_inventory,
    monkeypatch,
    boundary,
    drift,
):
    import plugin_resolve.inventory as inventory
    import plugin_resolve.preparation as preparation

    target, _home, clone, definition, environment = native_inventory
    if drift == "floating-revision":
        del definition["source"]["ref"]

    def change():
        if drift == "origin":
            git(clone, "remote", "set-url", "origin", "https://example.invalid/changed.git")
        else:
            git(clone, "checkout", "--quiet", "origin/HEAD")

    monkeypatch.setattr(preparation, "_install", no_install)
    real_register = preparation.register_prepared
    if boundary == "publication":

        def changed(*args, **kwargs):
            change()
            return real_register(*args, **kwargs)

        monkeypatch.setattr(preparation, "register_prepared", changed)
    else:
        real_inventory = inventory.inventory_item

        def changed(*args, **kwargs):
            verified = real_inventory(*args, **kwargs)
            change()
            return verified

        monkeypatch.setattr(inventory, "inventory_item", changed)
    with pytest.raises(ValueError, match="inventory changed|catalog was not prepared"):
        prepare(request(target, definition), environment=environment)
    assert not (target / ".github" / "copilot" / "settings.local.json").exists()


@pytest.mark.parametrize("failure", ["json", "unreadable"])
def test_bad_unrelated_cache_does_not_block_valid_native_reuse(
    native_inventory,
    monkeypatch,
    failure,
):
    import plugin_resolve.inventory as inventory

    target, _home, clone, definition, environment = native_inventory
    other = clone.with_name("aaa-unrelated") / "catalog-a"
    other.mkdir(parents=True)
    catalog = other / "marketplace.json"
    if failure == "json":
        catalog.write_text("{")
    else:
        atomic_json(catalog, {"name": "unrelated"})
        original = inventory.strict_json

        def unreadable(path, **kwargs):
            if path == catalog:
                raise PermissionError("synthetic unreadable cached catalog")
            return original(path, **kwargs)

        monkeypatch.setattr(inventory, "strict_json", unreadable)
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    assert prepare(request(target, definition), environment=environment) == {
        "helper@git-fixture": str(clone / "catalog-a" / "helper"),
    }


@pytest.mark.parametrize("failure", ["json", "closure"])
def test_invalid_selected_cache_falls_back_to_ordinary_installation(
    native_inventory,
    monkeypatch,
    caplog,
    failure,
):
    target, _home, clone, definition, environment = native_inventory
    if failure == "json":
        (clone / "catalog-a" / "marketplace.json").write_text("{")
    else:

        def linked_payload(*args, **kwargs):
            raise ValueError("Linked payload paths cannot be reflected")

        monkeypatch.setattr("plugin_resolve.reflection.subset", linked_payload)
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)
    assert "invalid-catalog" in caplog.text or "invalid-payload" in caplog.text


@pytest.mark.parametrize("reference", ["file", "directory"])
def test_deleted_required_sibling_asset_prevents_native_cache_reuse(
    native_inventory,
    monkeypatch,
    reference,
):
    target, _home, clone, definition, environment = native_inventory
    support = clone / "support"
    support.mkdir()
    (support / "guide.md").write_text("required guide")
    (support / "retained.md").write_text("directory still exists")
    scripts = clone / "catalog-a" / "helper" / "scripts"
    scripts.mkdir()
    (scripts / "use.txt").write_text(
        "../../support/guide.md" if reference == "file" else "../../support",
    )
    git(clone, "add", ".")
    git(clone, "commit", "--quiet", "-m", "required sibling fixture")
    definition["source"]["ref"] = "HEAD"
    (support / "guide.md").unlink()
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)
    assert not (target / ".github" / "copilot" / "settings.local.json").exists()
