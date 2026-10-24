"""Library changefiles share promotion's shipped-version allocation owner."""

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
    monkeypatch.setattr(promotion, "_git", lambda *args, **kwargs: "")
    monkeypatch.setattr(
        acc, "_project_version_at",
        lambda ref, path: "0.1.0-dev8" if path == "libs/agent-procutil/pyproject.toml" else None,
    )
    promotion._seed_versions_from_main(library, "shipped", acc=acc, repo=library)
    for root in acc.library_roots("libs/agent-procutil"):
        assert acc.read_pyproject_project_version(root) == "0.1.0-dev8"
    assert acc.compute({"libs/agent-procutil": ["dev"]}) == {
        "libs/agent-procutil": ("0.1.0-dev8", "0.1.0-dev9"),
    }


def test_library_apply_failure_is_not_accepted(library):
    copy = library / "plugins/example/libs/agent-procutil/pyproject.toml"
    copy.write_text("[project]\nname='agent-procutil'\n", encoding="utf-8")
    assert acc.apply({"libs/agent-procutil": ("0.1.0-dev7", "0.1.0-dev8")}) == []


def test_shipped_version_seed_failure_refuses(monkeypatch, tmp_path):
    fake = SimpleNamespace(
        iter_standalone_consumer_names=lambda: [],
        iter_library_consumer_names=lambda: ["libs/agent-procutil"],
        _project_version_at=lambda *args: "0.1.0-dev8",
        read_consumer_version=lambda *args: "0.1.0-dev7",
        apply=lambda *args: [],
    )
    with pytest.raises(promotion.PromotionError, match="cannot seed shipped versions"):
        promotion._seed_versions_from_main(tmp_path, "shipped", acc=fake, repo=tmp_path)


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
