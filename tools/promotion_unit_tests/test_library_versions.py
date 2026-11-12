"""Changefiles share promotion's shipped-version allocation owner."""

import json
from types import SimpleNamespace

import pytest

import accumulate_bumps as acc
import promote_release as promotion


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr(acc, "REPO", tmp_path)
    monkeypatch.setattr(acc, "PLUGINS_DIR", tmp_path / "plugins")
    for relative in (
        "libs/agent-procutil",
        "plugins/example/libs/agent-procutil",
        "services/index/libs/agent-procutil",
        "services/index",
    ):
        root = tmp_path / relative
        root.mkdir(parents=True, exist_ok=True)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "agent-procutil"\nversion = "0.1.0-dev7"\n',
            encoding="utf-8",
        )
    return tmp_path


def test_library_changefile_advances_canonical_and_every_physical_copy(library):
    computed = acc.compute({"libs/agent-procutil": ["dev"]})
    assert computed == {"libs/agent-procutil": ("0.1.0-dev7", "0.1.0-dev8")}
    assert acc.apply(computed) == ["libs/agent-procutil"]
    assert len(acc.library_roots("libs/agent-procutil")) == 3
    for root in acc.library_roots("libs/agent-procutil"):
        assert acc.read_pyproject_project_version(root) == "0.1.0-dev8"


@pytest.mark.parametrize("name", [
    "libs/../services/index", "libs/agent-procutil/..", "libs/missing",
    "agent-procutil",
])
def test_library_identifiers_do_not_alias_other_projects(library, name):
    assert acc.library_roots(name) == []
    assert acc.compute({name: ["dev"]}) == {}


def test_library_seed_preserves_shipped_version_without_reapplying_old_change(library, monkeypatch):
    monkeypatch.setattr(
        promotion, "_main_version_inputs",
        lambda *args: {"libs/agent-procutil/pyproject.toml": '[project]\nversion="0.1.0-dev8"\n'},
    )
    promotion._seed_versions_from_main(library, "shipped", acc=acc, repo=library)
    for root in acc.library_roots("libs/agent-procutil"):
        assert acc.read_pyproject_project_version(root) == "0.1.0-dev8"
    assert acc.compute({"libs/agent-procutil": ["dev"]}) == {
        "libs/agent-procutil": ("0.1.0-dev8", "0.1.0-dev9"),
    }


def _packaged_projects(root):
    for name, version in (("producer", "0.1.0-dev1"), ("receiver", "0.4.0-dev1")):
        directory = root / "plugins" / name
        directory.mkdir()
        (directory / "plugin.json").write_text(json.dumps({
            "name": name, "version": version,
        }), encoding="utf-8")
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8",
        )


@pytest.mark.parametrize("receiver_bump", [None, "dev"])
def test_compute_bumps_packaged_manifest_receiver(library, receiver_bump):
    _packaged_projects(library)
    (library / "plugins/receiver/packaged-assets.json").write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets",
        "version": 1,
        "canonicalDir": "plugins",
        "payloadDir": "src/bundle",
        "files": ["producer/plugin.json"],
    }), encoding="utf-8")
    grouped = {"producer": ["patch"]}
    if receiver_bump:
        grouped["receiver"] = [receiver_bump]

    assert acc.compute(grouped) == {
        "producer": ("0.1.0-dev1", "0.1.1-dev1"),
        "receiver": ("0.4.0-dev1", "0.4.0-dev2" if receiver_bump else "0.4.1-dev1"),
    }
    assert grouped == {
        "producer": ["patch"],
        **({"receiver": ["dev"]} if receiver_bump else {}),
    }


def test_packaged_manifest_bumps_close_cycles_and_reach_standalone(library):
    _packaged_projects(library)
    manager = library / "worktree-manager"
    manager.mkdir()
    (manager / "pyproject.toml").write_text(
        '[project]\nname = "manager"\nversion = "0.7.0-dev1"\n', encoding="utf-8",
    )
    for consumer, files in (
        ("plugins/producer", ["receiver/plugin.json"]),
        ("plugins/receiver", ["producer/pyproject.toml", "receiver/plugin.json"]),
        ("worktree-manager", ["receiver/plugin.json"]),
    ):
        (library / consumer / "packaged-assets.json").write_text(json.dumps({
            "schema": "copilot-extensions.packaged-assets", "version": 2,
            "groups": [{
                "canonicalDir": "plugins", "payloadDir": "src/bundle", "files": files,
            }],
        }), encoding="utf-8")

    assert acc.compute({"producer": ["patch"]}) == {
        "producer": ("0.1.0-dev1", "0.1.1-dev1"),
        "receiver": ("0.4.0-dev1", "0.4.1-dev1"),
        "worktree-manager": ("0.7.0-dev1", "0.7.1-dev1"),
    }
    assert acc.compute({}) == {}


@pytest.mark.parametrize("kind", ["root", "version", "assets"])
def test_indirect_packaged_version_receiver_refuses(library, monkeypatch, kind):
    _packaged_projects(library)
    receiver = library / "plugins/receiver"
    assets = receiver / "packaged-assets.json"
    assets.write_text(json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 1,
        "canonicalDir": "plugins", "payloadDir": "src/bundle",
        "files": ["producer/plugin.json"],
    }), encoding="utf-8")
    linked = {"root": receiver, "version": receiver / "plugin.json", "assets": assets}[kind]
    original = type(linked).is_symlink
    monkeypatch.setattr(type(linked), "is_symlink", lambda path: path == linked or original(path))

    with pytest.raises(acc.VersionError, match="unsafe library version"):
        acc.compute({"producer": ["patch"]})


def test_library_apply_failure_is_not_accepted(library):
    copy = library / "plugins/example/libs/agent-procutil/pyproject.toml"
    copy.write_text("[project]\nname='agent-procutil'\n", encoding="utf-8")
    assert acc.apply({"libs/agent-procutil": ("0.1.0-dev7", "0.1.0-dev8")}) == []


def test_shipped_version_seed_failure_refuses(monkeypatch, tmp_path):
    monkeypatch.setattr(promotion, "_main_version_inputs", lambda *args: {})
    fake = SimpleNamespace(
        iter_standalone_consumer_names=lambda: [],
        iter_library_consumer_names=lambda: ["libs/agent-procutil"],
        _project_version_at=lambda *args: "0.1.0-dev8",
        read_consumer_version=lambda *args: "0.1.0-dev7",
        apply=lambda *args: [],
    )
    with pytest.raises(promotion.PromotionError, match="cannot seed shipped versions"):
        promotion._seed_versions_from_main(tmp_path, "shipped", acc=fake, repo=tmp_path)


@pytest.mark.parametrize("target", [
    "plugin.json", "pyproject.toml", "src", "src/package",
    "src/package/__init__.py", "src/package/_build_info.py",
    "instruction-projections.json", "template.md", "marketplace.json",
])
def test_all_version_write_targets_are_admitted_before_any_change(library, monkeypatch, target):
    _packaged_projects(library)
    marketplace = library / ".github/plugin/marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(json.dumps({"plugins": [
        {"name": name, "version": version}
        for name, version in (("producer", "0.1.0-dev1"), ("receiver", "0.4.0-dev1"))
    ]}), encoding="utf-8")
    monkeypatch.setattr(acc, "MARKETPLACE", marketplace)
    root = library / "plugins/receiver"
    (root / "src/package").mkdir(parents=True)
    (root / "src/package/__init__.py").write_text(
        '__version__ = "0.4.0-dev1"\n', encoding="utf-8",
    )
    (root / "instruction-projections.json").write_text(json.dumps({
        "projections": [{"template": "template.md"}],
    }), encoding="utf-8")
    (root / "template.md").write_text("[owner: receiver@0.4.0-dev1]\n", encoding="utf-8")
    linked = marketplace if target == "marketplace.json" else root / target
    before = {path.relative_to(library): path.read_bytes()
              for path in library.rglob("*") if path.is_file()}
    original = type(linked).is_symlink
    monkeypatch.setattr(type(linked), "is_symlink", lambda path: path == linked or original(path))

    with pytest.raises(acc.VersionError, match="unsafe library version"):
        acc.apply({
            "producer": ("0.1.0-dev1", "0.1.1-dev1"),
            "receiver": ("0.4.0-dev1", "0.4.1-dev1"),
        })

    assert {path.relative_to(library): path.read_bytes()
            for path in library.rglob("*") if path.is_file()} == before


@pytest.mark.parametrize("kind", ["manifest", "ancestor", "source-directory", "fallback"])
def test_linked_library_targets_refuse_before_any_version_write(library, kind):
    external = library.parent / (library.name + "-external")
    external.mkdir()
    manifest = external / "pyproject.toml"
    manifest.write_text('[project]\nversion="0.1.0-dev7"\n', encoding="utf-8")
    fallback = external / "__init__.py"
    fallback.write_text('__version__ = "0.1.0-dev7"\n', encoding="utf-8")
    canonical = library / "libs/agent-procutil/pyproject.toml"
    before = (canonical.read_bytes(), manifest.read_bytes(), fallback.read_bytes())
    copy = library / "plugins/example/libs/agent-procutil"
    source = copy / "src"
    source.mkdir()
    if kind == "manifest":
        link, target = copy / "pyproject.toml", manifest
        link.unlink()
    elif kind == "ancestor":
        (copy / "pyproject.toml").unlink()
        source.rmdir()
        copy.rmdir()
        link, target = copy, external
    elif kind == "source-directory":
        link, target = source / "package", external
    else:
        (source / "package").mkdir()
        link, target = source / "package/__init__.py", fallback
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("native symlink creation requires Windows privilege")
        raise
    with pytest.raises(acc.VersionError, match="unsafe library version"):
        acc.apply({"libs/agent-procutil": ("0.1.0-dev7", "0.1.0-dev8")})
    assert (canonical.read_bytes(), manifest.read_bytes(), fallback.read_bytes()) == before
