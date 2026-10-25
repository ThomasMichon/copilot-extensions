"""Existing native Git caches are availability, never replacement source authority."""

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

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
    git(tmp_path, "clone", "--quiet", "--config", "core.autocrlf=false", str(origin), str(clone))
    git(clone, "checkout", "--quiet", first)
    remote = "https://example.invalid/native-fixture.git"
    git(clone, "remote", "set-url", "origin", remote)
    definition = {
        "source": {
            "source": "git",
            "url": remote,
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


def directory_link(link, target):
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


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
    "changed",
    [
        "origin",
        "revision",
        "tracked",
        "untracked",
        "ignored",
        "global",
        "assume-unchanged",
        "skip-worktree",
    ],
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
    elif changed in {"assume-unchanged", "skip-worktree"}:
        git(clone, "update-index", "--" + changed, "catalog-a/helper/marker.txt")
        (clone / "catalog-a" / "helper" / "marker.txt").write_text("hidden modification")
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


@pytest.mark.parametrize(
    "selector",
    ["github", "sha", "path", "runtime-cache", "unrelated", "source-spelling"],
)
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
    elif selector == "source-spelling":
        path = clone / folder / "marketplace.json"
        catalog = json.loads(path.read_text())
        catalog["plugins"][0]["source"] = " ./helper "
        atomic_json(path, catalog)
        git(clone, "add", ".")
        git(clone, "commit", "--quiet", "-m", "padded source fixture")
        definition["source"]["ref"] = "HEAD"
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


@pytest.mark.parametrize("failure", ["json", "closure", "cycle"])
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
            if failure == "cycle":
                raise RuntimeError("synthetic pre-3.13 asset symlink loop")
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


@pytest.mark.parametrize("component", ["source", "plugin-root"])
@pytest.mark.parametrize("spelling", ["plain", "padded"])
def test_linked_declared_source_cannot_redirect_native_cache_payload(
    native_inventory,
    monkeypatch,
    component,
    spelling,
):
    target, _home, clone, definition, environment = native_inventory
    catalog_root = clone / "catalog-a"
    selected = catalog_root / "helper"
    if component == "plugin-root":
        parent = catalog_root / "payloads"
        parent.mkdir()
        selected.rename(parent / "helper")
        path = catalog_root / "marketplace.json"
        catalog = json.loads(path.read_text())
        catalog["metadata"] = {"pluginRoot": "payloads"}
        atomic_json(path, catalog)
        selected, redirected = parent, catalog_root / "other-parent"
    else:
        redirected = catalog_root / "other-helper"
    shutil.copytree(selected, redirected)
    marker = (
        redirected / "helper" / "marker.txt"
        if component == "plugin-root"
        else redirected / "marker.txt"
    )
    marker.write_text("redirected payload")
    if spelling == "padded":
        path = catalog_root / "marketplace.json"
        catalog = json.loads(path.read_text())
        catalog["plugins"][0]["source"] = " ./helper "
        atomic_json(path, catalog)
    git(clone, "add", ".")
    git(clone, "commit", "--quiet", "-m", "source redirection fixture")
    definition["source"]["ref"] = "HEAD"
    selected.rename(selected.with_name("retained-original"))
    directory_link(selected, redirected)
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)


@pytest.mark.parametrize("style", ["uri", "path"])
def test_distinct_local_origins_with_git_suffix_do_not_share_inventory(
    native_inventory,
    git_origin,
    monkeypatch,
    style,
):
    target, _home, clone, definition, environment = native_inventory
    original = git_origin[0]
    different = original.with_name(original.name + ".git")
    init_checkout(different)
    encode = (lambda path: path.as_uri()) if style == "uri" else str
    git(clone, "remote", "set-url", "origin", encode(original))
    definition["source"] = {
        "source": "git",
        "url": encode(different),
        "path": "catalog-a",
    }
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)


@pytest.mark.parametrize("schema", ["null-entry", "scalar-entry", "owner", "plugin-root"])
def test_invalid_native_catalog_schema_falls_back(
    native_inventory,
    monkeypatch,
    schema,
):
    target, _home, clone, definition, environment = native_inventory
    path = clone / "catalog-a" / "marketplace.json"
    catalog = json.loads(path.read_text())
    if schema.endswith("entry"):
        catalog["plugins"].insert(0, None if schema == "null-entry" else "unexpected")
    elif schema == "owner":
        catalog["owner"] = {}
    else:
        catalog["metadata"] = {"pluginRoot": 42}
    atomic_json(path, catalog)
    git(clone, "add", ".")
    git(clone, "commit", "--quiet", "-m", "invalid catalog fixture")
    definition["source"]["ref"] = "HEAD"
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)


def test_linked_required_asset_parent_prevents_native_cache_reuse(
    native_inventory,
    monkeypatch,
    caplog,
):
    target, _home, clone, definition, environment = native_inventory
    real_support = clone / "real-support"
    real_support.mkdir()
    guide = real_support / "guide.md"
    guide.write_text("required guide")
    scripts = clone / "catalog-a" / "helper" / "scripts"
    scripts.mkdir()
    (scripts / "use.txt").write_text("../../support/guide.md")
    directory_link(clone / "support", real_support)
    link_target = clone / ".git" / "link-target"
    link_target.write_bytes(b"real-support")
    blob = git(clone, "hash-object", "-w", str(link_target))
    git(clone, "add", "real-support", "catalog-a")
    git(clone, "update-index", "--add", "--cacheinfo", "120000", blob, "support")
    git(clone, "commit", "--quiet", "-m", "linked required asset fixture")
    definition["source"]["ref"] = "HEAD"
    guide.write_text("modified required guide")
    if os.name != "nt":
        assert not git(clone, "status", "--porcelain", "--", "support/guide.md")
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)
    assert "invalid-payload" in caplog.text
    assert not (target / ".github" / "copilot" / "settings.local.json").exists()


@pytest.mark.parametrize("probe", ["rev-parse", "status"])
def test_native_cache_git_probe_timeout_uses_ordinary_installation(
    native_inventory,
    monkeypatch,
    caplog,
    probe,
):
    import plugin_resolve.inventory as inventory

    target, _home, clone, definition, environment = native_inventory
    original = inventory.subprocess.run
    timed_out = []

    def timeout(arguments, **kwargs):
        if "-C" in arguments and arguments[arguments.index("-C") + 1] == str(clone):
            if arguments[arguments.index("-C") + 2] == probe:
                assert kwargs["timeout"] == 30
                timed_out.append(probe)
                raise subprocess.TimeoutExpired(arguments, 30)
        return original(arguments, **kwargs)

    monkeypatch.setattr(inventory.subprocess, "run", timeout)
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)
    assert timed_out == [probe]
    assert "probe-timeout" in caplog.text
    assert not (target / ".github" / "copilot" / "settings.local.json").exists()


@pytest.mark.parametrize("kind", ["git", "github"])
def test_distinct_network_repository_names_do_not_share_inventory(
    native_inventory,
    monkeypatch,
    kind,
):
    target, _home, clone, definition, environment = native_inventory
    if kind == "git":
        definition["source"]["url"] = definition["source"]["url"].removesuffix(".git")
    else:
        git(clone, "remote", "set-url", "origin", "https://github.com/example/catalog.git")
        definition["source"] = {
            "source": "github",
            "repo": "example/catalog.git",
            "path": "catalog-a",
            "ref": "first",
        }
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)


def test_github_repository_name_ending_in_git_reuses_exact_repository(
    native_inventory,
    monkeypatch,
):
    target, _home, clone, definition, environment = native_inventory
    git(clone, "remote", "set-url", "origin", "git@github.com:example/catalog.git.git")
    definition["source"] = {
        "source": "github",
        "repo": "example/catalog.git",
        "path": "catalog-a",
        "ref": "first",
    }
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    assert prepare(request(target, definition), environment=environment) == {
        "helper@git-fixture": str(clone / "catalog-a" / "helper"),
    }


def test_deleted_higher_priority_catalog_prevents_native_cache_reuse(
    native_inventory,
    monkeypatch,
):
    target, _home, clone, definition, environment = native_inventory
    catalog_root = clone / "catalog-a"
    shutil.copytree(catalog_root / "helper", catalog_root / "other-helper")
    catalog = json.loads((catalog_root / "marketplace.json").read_text())
    catalog["plugins"][0]["source"] = "./other-helper"
    primary = catalog_root / ".github" / "plugin" / "marketplace.json"
    primary.parent.mkdir(parents=True)
    atomic_json(primary, catalog)
    git(clone, "add", ".")
    git(clone, "commit", "--quiet", "-m", "native-priority catalog fixture")
    definition["source"]["ref"] = "HEAD"
    primary.unlink()
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)


def test_non_utf8_git_status_uses_ordinary_installation(
    native_inventory,
    monkeypatch,
):
    import plugin_resolve.inventory as inventory

    target, _home, clone, definition, environment = native_inventory
    original = inventory.subprocess.run
    observed = []

    def raw_status(arguments, **kwargs):
        if (
            "-C" in arguments
            and arguments[arguments.index("-C") + 1] == str(clone)
            and arguments[arguments.index("-C") + 2] == "status"
        ):
            observed.append(True)
            return original(
                [sys.executable, "-c", r"import os; os.write(1, b'?? bad\xff.txt\x00')"],
                **kwargs,
            )
        return original(arguments, **kwargs)

    monkeypatch.setattr(inventory.subprocess, "run", raw_status)
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)
    assert observed == [True]


def test_byte_transformed_checkout_uses_ordinary_installation(
    native_inventory,
    monkeypatch,
):
    target, _home, clone, definition, environment = native_inventory
    readme = clone / "catalog-a" / "helper" / "README.md"
    readme.write_bytes(b"first line\nsecond line\n")
    git(clone, "add", ".")
    git(clone, "commit", "--quiet", "-m", "exact byte fixture")
    definition["source"]["ref"] = "HEAD"
    git(clone, "config", "core.autocrlf", "true")
    readme.write_bytes(b"first line\r\nsecond line\r\n")
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)


def test_unselected_lower_priority_catalog_changes_do_not_prevent_reuse(
    native_inventory,
    monkeypatch,
):
    target, _home, clone, definition, environment = native_inventory
    catalog_root = clone / "catalog-a"
    fallback = catalog_root / "marketplace.json"
    primary = catalog_root / ".github" / "plugin" / "marketplace.json"
    primary.parent.mkdir(parents=True)
    atomic_json(primary, json.loads(fallback.read_text()))
    git(clone, "add", ".")
    git(clone, "commit", "--quiet", "-m", "unselected catalog fixture")
    definition["source"]["ref"] = "HEAD"
    fallback.write_text("unrelated malformed lower-priority catalog")
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    assert prepare(request(target, definition), environment=environment) == {
        "helper@git-fixture": str(catalog_root / "helper"),
    }


@pytest.mark.parametrize("modified", ["clean", "modified", "deleted"])
@pytest.mark.parametrize("layout", ["direct", "nested"])
def test_sibling_submodule_asset_cannot_escape_committed_byte_proof(
    native_inventory,
    monkeypatch,
    modified,
    layout,
):
    target, _home, clone, definition, environment = native_inventory
    relative = Path("support") if layout == "direct" else Path("deps") / "support"
    support = clone / relative
    init_checkout(support)
    (support / "guide.md").write_bytes(b"required submodule guide")
    git(support, "add", ".")
    git(support, "commit", "--quiet", "-m", "submodule fixture")
    scripts = clone / "catalog-a" / "helper" / "scripts"
    scripts.mkdir()
    (scripts / "use.txt").write_text("../../" + relative.as_posix() + "/guide.md")
    git(clone, "add", "catalog-a", str(relative))
    git(clone, "commit", "--quiet", "-m", "required submodule asset fixture")
    definition["source"]["ref"] = "HEAD"
    assert git(clone, "ls-files", "--stage", "--", str(relative)).startswith("160000 ")
    if modified == "modified":
        (support / "guide.md").write_text("modified submodule bytes")
    elif modified == "deleted":
        (support / "guide.md").unlink()
    assert not git(clone, "status", "--porcelain", "--", str(relative / "guide.md"))
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)


def test_unreadable_cache_directory_uses_ordinary_installation(
    native_inventory,
    monkeypatch,
    caplog,
):
    target, _home, clone, definition, environment = native_inventory
    original = Path.iterdir

    def unreadable(path):
        if path == clone.parent:
            raise PermissionError("synthetic cache enumeration denied")
        return original(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)
    assert "unreadable-cache" in caplog.text


@pytest.mark.parametrize("probe", ["lstat", "resolve"])
def test_candidate_path_permission_error_preserves_installation_fallback(
    native_inventory,
    monkeypatch,
    caplog,
    probe,
):
    target, _home, clone, definition, environment = native_inventory
    original = getattr(Path, probe)
    denied = clone if probe == "lstat" else clone / "catalog-a"

    def inaccessible(path, *args, **kwargs):
        if path == denied:
            raise PermissionError("synthetic candidate path denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, probe, inaccessible)
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)
    assert "invalid-catalog" in caplog.text


@pytest.mark.parametrize("platform", ["linux", "win32"])
@pytest.mark.parametrize("committed_executable", [False, True])
def test_cache_file_mode_proof_respects_platform_semantics(
    native_inventory,
    monkeypatch,
    platform,
    committed_executable,
):
    import plugin_resolve.inventory as inventory

    target, _home, clone, definition, environment = native_inventory
    marker = clone / "catalog-a" / "helper" / "marker.txt"
    git(
        clone,
        "update-index",
        "--chmod=" + ("+x" if committed_executable else "-x"),
        "catalog-a/helper/marker.txt",
    )
    git(clone, "commit", "--quiet", "--allow-empty", "-m", "tracked mode fixture")
    git(clone, "config", "core.filemode", "false")
    definition["source"]["ref"] = "HEAD"
    original = Path.stat

    def changed_mode(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        if path != marker:
            return info
        values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
        values["st_mode"] = (info.st_mode & ~0o111) | (0 if committed_executable else stat.S_IXUSR)
        return SimpleNamespace(**values)

    monkeypatch.setattr(Path, "stat", changed_mode)
    monkeypatch.setattr(inventory, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    if platform == "win32":
        assert prepare(request(target, definition), environment=environment) == {
            "helper@git-fixture": str(clone / "catalog-a" / "helper"),
        }
    else:
        with pytest.raises(AssertionError, match="cold installation"):
            prepare(request(target, definition), environment=environment)


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable-bit filesystem semantics")
@pytest.mark.parametrize("committed_executable", [False, True])
def test_native_posix_executable_mode_drift_prevents_reuse(
    native_inventory,
    monkeypatch,
    committed_executable,
):
    target, _home, clone, definition, environment = native_inventory
    marker = clone / "catalog-a" / "helper" / "marker.txt"
    git(
        clone,
        "update-index",
        "--chmod=" + ("+x" if committed_executable else "-x"),
        "catalog-a/helper/marker.txt",
    )
    git(clone, "commit", "--quiet", "--allow-empty", "-m", "native tracked mode fixture")
    definition["source"]["ref"] = "HEAD"
    git(clone, "config", "core.filemode", "false")
    mode = marker.stat().st_mode & ~0o111
    marker.chmod(mode if committed_executable else mode | stat.S_IXUSR)
    assert not git(clone, "status", "--porcelain", "--", "catalog-a/helper/marker.txt")
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)


@pytest.mark.parametrize("modified", [False, True])
def test_excluded_payload_root_cannot_supply_unverified_inventory(
    native_inventory,
    monkeypatch,
    modified,
):
    target, _home, clone, definition, environment = native_inventory
    root = clone / "catalog-a"
    hidden = root / ".venv" / "helper"
    hidden.parent.mkdir()
    (root / "helper").rename(hidden)
    catalog = json.loads((root / "marketplace.json").read_text())
    catalog["plugins"][0]["source"] = ".venv/helper"
    atomic_json(root / "marketplace.json", catalog)
    git(clone, "add", "--force", ".")
    git(clone, "commit", "--quiet", "-m", "excluded payload root fixture")
    definition["source"]["ref"] = "HEAD"
    if modified:
        (hidden / "marker.txt").write_text("unverified hidden payload")
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)


def test_cache_root_permission_error_preserves_installation_fallback(
    native_inventory,
    monkeypatch,
    caplog,
):
    target, _home, clone, definition, environment = native_inventory
    original = Path.is_dir

    def inaccessible(path):
        if path == clone.parent:
            raise PermissionError("synthetic cache root denied")
        return original(path)

    monkeypatch.setattr(Path, "is_dir", inaccessible)
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)
    assert "unreadable-cache" in caplog.text


@pytest.mark.parametrize("path_kind", ["candidate", "catalog", "payload"])
def test_legacy_python_cyclic_path_error_preserves_installation_fallback(
    native_inventory,
    monkeypatch,
    caplog,
    path_kind,
):
    target, _home, clone, definition, environment = native_inventory
    denied = {
        "candidate": clone,
        "catalog": clone / "catalog-a",
        "payload": clone / "catalog-a" / "helper",
    }[path_kind]
    original = Path.resolve

    def cyclic(path, *args, **kwargs):
        if path == denied:
            raise RuntimeError("synthetic pre-3.13 symlink loop")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", cyclic)
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)
    assert "invalid-catalog" in caplog.text or "invalid-payload" in caplog.text


@pytest.mark.skipif(os.name == "nt", reason="POSIX unprivileged symbolic-link creation")
def test_native_cyclic_source_links_preserve_installation_fallback(
    native_inventory,
    monkeypatch,
):
    target, _home, clone, definition, environment = native_inventory
    selected = clone / "catalog-a" / "helper"
    selected.rename(selected.with_name("retained-helper"))
    loop = selected.with_name("loop")
    selected.symlink_to(loop, target_is_directory=True)
    loop.symlink_to(selected, target_is_directory=True)
    monkeypatch.setattr("plugin_resolve.preparation._install", no_install)
    with pytest.raises(AssertionError, match="cold installation"):
        prepare(request(target, definition), environment=environment)
