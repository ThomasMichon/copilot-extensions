from __future__ import annotations

import importlib.util
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "build_python_artifacts.py"

_SPEC = importlib.util.spec_from_file_location("build_python_artifacts", MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
bpa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bpa)

uer = bpa.uer  # the real uv_editable_ref module build_python_artifacts imports

# A fake, already-resolved toolchain lock shared by every end-to-end test
# below -- passed explicitly to `build_plugin_artifacts`/`build_wheel` so
# none of these tests ever invokes the real `resolve_toolchain_lock` (which
# would shell out to real `uv venv`/`uv pip install`).
_FAKE_TOOLCHAIN = bpa.ToolchainLock(
    Path("/fake/toolchain-venv/bin/python"),
    {"setuptools": "84.1.0", "wheel": "0.44.0"},
)


# --- parse_wheel_filename -----------------------------------------------


def test_parse_wheel_filename_pure_python():
    info = bpa.parse_wheel_filename(Path("agent_bridge-0.4.1.dev3-py3-none-any.whl"))
    assert info == {
        "name": "agent_bridge",
        "version": "0.4.1.dev3",
        "python_tag": "py3",
        "abi_tag": "none",
        "platform_tag": "any",
    }


def test_parse_wheel_filename_platform_specific():
    info = bpa.parse_wheel_filename(
        Path("pydantic_core-2.46.5-cp312-cp312-win_amd64.whl")
    )
    assert info["python_tag"] == "cp312"
    assert info["abi_tag"] == "cp312"
    assert info["platform_tag"] == "win_amd64"


def test_parse_wheel_filename_with_build_tag():
    # Regression: a wheel filename may carry an optional numeric build tag
    # between version and the compatibility tags (PEP 427) -- it must not
    # be absorbed into `version`.
    info = bpa.parse_wheel_filename(Path("demo_pkg-1.2.3-1-py3-none-any.whl"))
    assert info["name"] == "demo_pkg"
    assert info["version"] == "1.2.3"
    assert info["python_tag"] == "py3"
    assert info["abi_tag"] == "none"
    assert info["platform_tag"] == "any"


def test_parse_wheel_filename_malformed_raises():
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.parse_wheel_filename(Path("not-a-wheel.txt"))


def test_parse_wheel_filename_non_normalized_name_raises():
    # Regression: a well-formed wheel's distribution name is always
    # exactly one token (PEP 427 normalizes '-'/'_'/'.' runs to a single
    # '_' specifically so this split is unambiguous). A hyphenated,
    # non-normalized name like "demo-pkg" must not be silently accepted by
    # reinterpreting one of its own tokens as the version.
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.parse_wheel_filename(Path("demo-pkg-1.2.3-py3-none-any.whl"))


# --- overall_identity_tags -----------------------------------------------


def test_overall_identity_tags_all_universal():
    infos = [
        {"python_tag": "py3", "abi_tag": "none", "platform_tag": "any"},
        {"python_tag": "py3", "abi_tag": "none", "platform_tag": "any"},
    ]
    assert bpa.overall_identity_tags(infos) == {
        "python_tag": "py3",
        "abi_tag": "none",
        "platform_tag": "any",
    }


def test_overall_identity_tags_one_platform_specific_wins():
    infos = [
        {"python_tag": "py3", "abi_tag": "none", "platform_tag": "any"},
        {"python_tag": "cp312", "abi_tag": "cp312", "platform_tag": "win_amd64"},
    ]
    assert bpa.overall_identity_tags(infos) == {
        "python_tag": "cp312",
        "abi_tag": "cp312",
        "platform_tag": "win_amd64",
    }


def test_overall_identity_tags_conflicting_specific_tags_raise():
    infos = [
        {"python_tag": "cp311", "abi_tag": "cp311", "platform_tag": "win_amd64"},
        {"python_tag": "cp312", "abi_tag": "cp312", "platform_tag": "win_amd64"},
    ]
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.overall_identity_tags(infos)


# --- read_wheel_generator -------------------------------------------------


def _make_fake_wheel(path: Path, *, generator: str | None) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        if generator is not None:
            zf.writestr(
                "fake_pkg-1.0.dist-info/WHEEL",
                f"Wheel-Version: 1.0\nGenerator: {generator}\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            )
        else:
            zf.writestr(
                "fake_pkg-1.0.dist-info/WHEEL",
                "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
            )


def test_read_wheel_generator_present(tmp_path: Path):
    wheel = tmp_path / "fake_pkg-1.0-py3-none-any.whl"
    _make_fake_wheel(wheel, generator="setuptools (84.1.0)")
    assert bpa.read_wheel_generator(wheel) == "setuptools (84.1.0)"


def test_read_wheel_generator_absent_raises(tmp_path: Path):
    wheel = tmp_path / "fake_pkg-1.0-py3-none-any.whl"
    _make_fake_wheel(wheel, generator=None)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.read_wheel_generator(wheel)


def test_read_wheel_generator_missing_dist_info_raises(tmp_path: Path):
    wheel = tmp_path / "empty-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("not_dist_info.txt", "nothing here")
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.read_wheel_generator(wheel)


def test_read_wheel_generator_multiple_dist_info_raises(tmp_path: Path):
    # Regression: a malformed wheel with more than one matching
    # dist-info/WHEEL entry must not silently trust whichever ZIP member
    # happens to come first -- that could record the WRONG distribution's
    # generator.
    wheel = tmp_path / "fake_pkg-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr(
            "fake_pkg-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: setuptools (84.1.0)\n",
        )
        zf.writestr(
            "other_pkg-2.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nGenerator: setuptools (1.0.0)\n",
        )
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.read_wheel_generator(wheel)


def test_read_wheel_generator_invalid_utf8_raises(tmp_path: Path):
    # Regression: errors="replace" would silently accept corrupt metadata
    # and record a replacement-character Generator as if the real toolchain
    # were known -- the opposite of fail-closed.
    wheel = tmp_path / "fake_pkg-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr("fake_pkg-1.0.dist-info/WHEEL", b"Generator: \xff\xfe bad\n")
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.read_wheel_generator(wheel)


# --- sha256_file -----------------------------------------------------------


def test_sha256_file_matches_hashlib(tmp_path: Path):
    import hashlib

    f = tmp_path / "data.bin"
    f.write_bytes(b"hello world" * 1000)
    expected = f"sha256:{hashlib.sha256(f.read_bytes()).hexdigest()}"
    assert bpa.sha256_file(f) == expected


# --- resolve_vendored_libs -------------------------------------------------
#
# `resolve_vendored_libs` validates every discovered consumer directory with
# the REAL `uv_editable_ref.uv_editable_problems` -- the same acceptance
# check `materialize_main.py` applies -- so these fixtures must satisfy it:
# `editable = true`, the referenced `libs/<lib>` resolved exactly under
# `uer.LIBS_DIR` (patched to the fake repo root below), a real directory
# with both `src/` and `pyproject.toml` present, and no symlinks anywhere.


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(uer, "REPO", tmp_path)
    monkeypatch.setattr(uer, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(uer, "LIBS_DIR", tmp_path / "libs")
    monkeypatch.setattr(bpa, "REPO", tmp_path)
    monkeypatch.setattr(bpa, "PLUGINS_DIR", tmp_path / "plugins")
    monkeypatch.setattr(bpa, "LIBS_DIR", tmp_path / "libs")
    return tmp_path


def _write_pyproject(path: Path, *, sources: dict[str, str] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    lines = ['[project]', 'name = "whatever"', 'version = "0.1.0"']
    if sources:
        lines.append("")
        lines.append("[tool.uv.sources]")
        for name, rel in sources.items():
            lines.append(f'{name} = {{ path = "{rel}", editable = true }}')
    (path / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_valid_lib(repo: Path, lib: str) -> Path:
    """A `libs/<lib>` directory that passes `uv_editable_problems` on its own
    (real directory, `src/` present, `pyproject.toml` present, no symlinks)."""
    lib_dir = repo / "libs" / lib
    pkg = lib.replace("-", "_")
    (lib_dir / "src" / pkg).mkdir(parents=True, exist_ok=True)
    (lib_dir / "src" / pkg / "__init__.py").write_text("", encoding="utf-8")
    _write_pyproject(lib_dir)
    return lib_dir


def test_resolve_vendored_libs_direct(fake_repo: Path):
    plugin_dir = fake_repo / "plugins" / "demo"
    lib_dir = _seed_valid_lib(fake_repo, "widget")
    _write_pyproject(plugin_dir, sources={"demo-widget": "../../libs/widget"})

    libs = bpa.resolve_vendored_libs(plugin_dir)

    assert libs == [("widget", lib_dir.resolve())]


def test_resolve_vendored_libs_recurses_nested(fake_repo: Path):
    plugin_dir = fake_repo / "plugins" / "demo"
    lib_a = _seed_valid_lib(fake_repo, "a")
    lib_b = _seed_valid_lib(fake_repo, "b")
    _write_pyproject(plugin_dir, sources={"demo-a": "../../libs/a"})
    # Overwrite lib_a's pyproject with its own nested reference to lib_b,
    # keeping the src/ fixture _seed_valid_lib already created.
    (lib_a / "pyproject.toml").write_text(
        '[project]\nname = "a"\nversion = "0.1.0"\n\n'
        '[tool.uv.sources]\ndemo-b = { path = "../b", editable = true }\n',
        encoding="utf-8",
    )

    libs = dict(bpa.resolve_vendored_libs(plugin_dir))

    assert set(libs) == {"a", "b"}
    assert libs["b"] == lib_b.resolve()


def test_resolve_vendored_libs_nested_editable_ref_missing_src_raises(
    fake_repo: Path,
):
    # Regression: a NESTED editable reference (one level deeper than the
    # originally requested top-level plugin) was queued and would be built
    # completely unvalidated -- only the top-level consumer's own
    # references ever went through uv_editable_problems. Here lib_a's own
    # reference to "b" resolves to a real canonical libs/b location, but
    # that directory is missing src/ -- must now fail the same way a
    # top-level reference to it would.
    plugin_dir = fake_repo / "plugins" / "demo"
    lib_a = _seed_valid_lib(fake_repo, "a")
    incomplete_lib_b = fake_repo / "libs" / "b"
    incomplete_lib_b.mkdir(parents=True)
    _write_pyproject(incomplete_lib_b)  # no src/ directory
    _write_pyproject(plugin_dir, sources={"demo-a": "../../libs/a"})
    (lib_a / "pyproject.toml").write_text(
        '[project]\nname = "a"\nversion = "0.1.0"\n\n'
        '[tool.uv.sources]\ndemo-b = { path = "../b", editable = true }\n',
        encoding="utf-8",
    )

    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_vendored_libs(plugin_dir)


def test_resolve_vendored_libs_no_sources_is_empty(fake_repo: Path):
    plugin_dir = fake_repo / "plugins" / "demo"
    _write_pyproject(plugin_dir)

    assert bpa.resolve_vendored_libs(plugin_dir) == []


def test_read_sources_table_rejects_malformed_tool_table(tmp_path: Path):
    # Invariant: every intermediate table ([tool], [tool.uv]) must be
    # validated before `.get()` is called on it -- a structurally valid
    # TOML document whose `tool` key is not itself a table (e.g. an array)
    # must raise the documented ArtifactBuildError, not an uncaught
    # AttributeError, especially since no top-level guard runs while
    # recursively inspecting a vendored lib.
    d = tmp_path / "demo"
    d.mkdir()
    (d / "pyproject.toml").write_text(
        'tool = []\n\n[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.find_in_tree_lib_sources(d)


def test_read_sources_table_rejects_malformed_uv_table(tmp_path: Path):
    d = tmp_path / "demo"
    d.mkdir()
    (d / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n[tool]\nuv = []\n',
        encoding="utf-8",
    )
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.find_in_tree_lib_sources(d)


def test_resolve_vendored_libs_unsafe_name_raises(fake_repo: Path):
    plugin_dir = fake_repo / "plugins" / "demo"
    # A `path` whose final component is ".." resolves outside the consumer
    # root (so `find_uv_editable_refs` includes it) but its derived `lib`
    # name (`Path(raw_path).name`) is literally "..", which
    # `is_safe_lib_name` (invoked inside `uv_editable_problems`) must
    # reject rather than let reach `libs/<lib>`.
    _write_pyproject(plugin_dir, sources={"demo-evil": "../../.."})

    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_vendored_libs(plugin_dir)


def test_resolve_vendored_libs_non_editable_raises(fake_repo: Path):
    # Missing `editable = true` would resolve to a frozen, non-live copy on
    # `dev` -- `uv_editable_problems` rejects it, and so must this script.
    plugin_dir = fake_repo / "plugins" / "demo"
    _seed_valid_lib(fake_repo, "widget")
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[tool.uv.sources]\ndemo-widget = { path = "../../libs/widget" }\n',
        encoding="utf-8",
    )

    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_vendored_libs(plugin_dir)


def test_resolve_vendored_libs_missing_canonical_dir_raises(fake_repo: Path):
    # References a lib that was never seeded under libs/ at all.
    plugin_dir = fake_repo / "plugins" / "demo"
    _write_pyproject(plugin_dir, sources={"demo-widget": "../../libs/widget"})

    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_vendored_libs(plugin_dir)


def test_resolve_vendored_libs_in_tree_editable_combination_raises(fake_repo: Path):
    # Regression: an `editable = true` entry whose path does NOT escape its
    # own consumer root falls through BOTH discovery functions
    # (find_uv_editable_refs only returns escaping entries;
    # find_in_tree_lib_sources skips every editable=true entry) and would
    # otherwise be silently omitted from the manifest entirely.
    plugin_dir = fake_repo / "plugins" / "demo"
    _seed_in_tree_lib(plugin_dir, "widget")
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[tool.uv.sources]\ndemo-widget = { path = "libs/widget", editable = true }\n',
        encoding="utf-8",
    )

    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_vendored_libs(plugin_dir)


def test_resolve_vendored_libs_name_collision_different_canonical_raises(
    fake_repo: Path,
):
    # Regression: deduplicating solely by the final directory name would
    # silently drop one of two genuinely distinct sources that happen to
    # share a name (here, two different "widget" libs nested under two
    # different parents).
    plugin_dir = fake_repo / "plugins" / "demo"
    lib_a = _seed_in_tree_lib(plugin_dir, "a")
    _seed_in_tree_lib(lib_a, "widget")
    _write_in_tree_pyproject(lib_a, sources={"demo-widget": "libs/widget"})
    lib_b = _seed_in_tree_lib(plugin_dir, "b")
    _seed_in_tree_lib(lib_b, "widget")
    _write_in_tree_pyproject(lib_b, sources={"demo-widget": "libs/widget"})
    _write_in_tree_pyproject(plugin_dir, sources={"demo-a": "libs/a", "demo-b": "libs/b"})

    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_vendored_libs(plugin_dir)


# --- find_in_tree_lib_sources / in-tree vendored-copy discovery -----------
#
# Regression: the ONLY shape discoverable before this fix was the escaping
# dev-branch `uv`-editable form. An ordinary in-tree vendored copy (no
# `editable` marker, path resolves WITHIN the consumer's own `libs/`) is a
# separate, real, already-shipped shape some plugins use permanently (e.g.
# agent-worktrees' own `libs/plugin-resolve`), and is the ONLY shape left
# once `materialize_main.py` has rewritten every escaping reference into
# exactly this local form -- the real state promotion actually builds
# against.


def _write_in_tree_pyproject(path: Path, *, sources: dict[str, str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    lines = ['[project]', 'name = "whatever"', 'version = "0.1.0"', "", "[tool.uv.sources]"]
    for name, rel in sources.items():
        lines.append(f'{name} = {{ path = "{rel}" }}')
    (path / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _seed_in_tree_lib(consumer_dir: Path, lib: str) -> Path:
    """A `consumer_dir/libs/<lib>` in-tree vendored copy that passes
    `_validate_in_tree_lib_dir` on its own."""
    lib_dir = consumer_dir / "libs" / lib
    pkg = lib.replace("-", "_")
    (lib_dir / "src" / pkg).mkdir(parents=True, exist_ok=True)
    (lib_dir / "src" / pkg / "__init__.py").write_text("", encoding="utf-8")
    _write_pyproject(lib_dir)
    return lib_dir


def test_find_in_tree_lib_sources_direct(fake_repo: Path):
    plugin_dir = fake_repo / "plugins" / "demo"
    lib_dir = _seed_in_tree_lib(plugin_dir, "widget")
    _write_in_tree_pyproject(plugin_dir, sources={"demo-widget": "libs/widget"})

    found = bpa.find_in_tree_lib_sources(plugin_dir)

    assert found == [("demo-widget", "libs/widget", "widget")]
    assert (plugin_dir / "libs" / "widget").resolve() == lib_dir.resolve()


def test_find_in_tree_lib_sources_ignores_escaping_entries(fake_repo: Path):
    # An escaping entry is find_uv_editable_refs's own job -- this function
    # must not also report it, or resolve_vendored_libs would validate it
    # twice under two different (incompatible) acceptance rules.
    plugin_dir = fake_repo / "plugins" / "demo"
    _seed_valid_lib(fake_repo, "widget")
    _write_pyproject(plugin_dir, sources={"demo-widget": "../../libs/widget"})

    assert bpa.find_in_tree_lib_sources(plugin_dir) == []


def test_resolve_vendored_libs_discovers_in_tree_only_consumer(fake_repo: Path):
    # Simulates a plugin that vendors its libs in-tree by design (e.g.
    # agent-worktrees), with no escaping `uv`-editable reference at all.
    plugin_dir = fake_repo / "plugins" / "demo"
    lib_dir = _seed_in_tree_lib(plugin_dir, "widget")
    _write_in_tree_pyproject(plugin_dir, sources={"demo-widget": "libs/widget"})

    libs = bpa.resolve_vendored_libs(plugin_dir)

    assert libs == [("widget", lib_dir.resolve())]


def test_resolve_vendored_libs_discovers_post_materialization_form(fake_repo: Path):
    # Simulates the REAL state build_python_artifacts actually runs against
    # during promotion: materialize_main.py has already rewritten the
    # escaping dev-branch reference into the local, non-editable
    # `{ path = "libs/<lib>" }` form. Before this fix, resolve_vendored_libs
    # would silently discover NOTHING here.
    plugin_dir = fake_repo / "plugins" / "demo"
    lib_dir = _seed_in_tree_lib(plugin_dir, "widget")
    _write_in_tree_pyproject(plugin_dir, sources={"demo-widget": "libs/widget"})

    libs = bpa.resolve_vendored_libs(plugin_dir)

    assert libs == [("widget", lib_dir.resolve())]


def test_resolve_vendored_libs_in_tree_recurses_nested(fake_repo: Path):
    plugin_dir = fake_repo / "plugins" / "demo"
    lib_a = _seed_in_tree_lib(plugin_dir, "a")
    _write_in_tree_pyproject(plugin_dir, sources={"demo-a": "libs/a"})
    lib_b = _seed_in_tree_lib(lib_a, "b")
    _write_in_tree_pyproject(lib_a, sources={"demo-b": "libs/b"})

    libs = dict(bpa.resolve_vendored_libs(plugin_dir))

    assert set(libs) == {"a", "b"}
    assert libs["b"] == lib_b.resolve()


def test_resolve_vendored_libs_sibling_cross_reference(fake_repo: Path):
    # Regression (found via a real smoke test against agent-worktrees):
    # plugins/agent-worktrees/libs/plugin-activation depends on its SIBLING
    # plugins/agent-worktrees/libs/dropin-registry via a plain
    # `{ path = "../dropin-registry" }` entry -- no `editable` marker, and
    # escaping plugin-activation's OWN root (though not the plugin's).
    # Before this fix, uv_editable_problems (applied to every recursed-into
    # node) wrongly rejected this as a broken top-level canonical
    # reference missing `editable = true`.
    plugin_dir = fake_repo / "plugins" / "demo"
    lib_a = _seed_in_tree_lib(plugin_dir, "a")
    _write_in_tree_pyproject(plugin_dir, sources={"demo-a": "libs/a"})
    lib_b = _seed_in_tree_lib(plugin_dir, "b")  # sibling of lib_a, same libs/ level
    _write_in_tree_pyproject(lib_a, sources={"demo-b": "../b"})

    libs = dict(bpa.resolve_vendored_libs(plugin_dir))

    assert set(libs) == {"a", "b"}
    assert libs["b"] == lib_b.resolve()


def test_find_in_tree_lib_sources_rejects_cross_plugin_escape(fake_repo: Path):
    # Regression: a non-editable path resolving to an UNRELATED plugin's
    # libs/ directory (not the consumer's own, nor a sibling in the same
    # parent libs/ folder) must not be accepted as a legitimate in-tree
    # vendored copy -- `materialize_nested_uv_editable_refs` enforces this
    # identical "expected sibling location" constraint.
    other_plugin_dir = fake_repo / "plugins" / "other"
    _seed_in_tree_lib(other_plugin_dir, "widget")
    plugin_dir = fake_repo / "plugins" / "demo"
    _write_in_tree_pyproject(
        plugin_dir, sources={"demo-widget": "../other/libs/widget"}
    )

    assert bpa.find_in_tree_lib_sources(plugin_dir) == []


def test_resolve_vendored_libs_cross_plugin_escape_raises_not_silently_omitted(
    fake_repo: Path,
):
    # Invariant: a non-editable, escaping reference that doesn't resolve
    # to an allowed in-tree vendored-lib location must fail the build
    # closed, matching what `materialize_nested_uv_editable_refs` would do
    # with this same reference -- never silently omit the dependency from
    # the artifact set.
    other_plugin_dir = fake_repo / "plugins" / "other"
    _seed_in_tree_lib(other_plugin_dir, "widget")
    plugin_dir = fake_repo / "plugins" / "demo"
    _write_in_tree_pyproject(
        plugin_dir, sources={"demo-widget": "../other/libs/widget"}
    )

    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_vendored_libs(plugin_dir)


def test_resolve_vendored_libs_in_tree_missing_src_raises(fake_repo: Path):
    plugin_dir = fake_repo / "plugins" / "demo"
    lib_dir = plugin_dir / "libs" / "widget"
    lib_dir.mkdir(parents=True)
    _write_pyproject(lib_dir)  # no src/ directory
    _write_in_tree_pyproject(plugin_dir, sources={"demo-widget": "libs/widget"})

    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_vendored_libs(plugin_dir)


def test_resolve_vendored_libs_in_tree_symlink_raises(fake_repo: Path):
    plugin_dir = fake_repo / "plugins" / "demo"
    real_lib = _seed_in_tree_lib(fake_repo, "widget")  # elsewhere, irrelevant path
    plugin_dir.mkdir(parents=True, exist_ok=True)
    link = plugin_dir / "libs" / "widget"
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(real_lib, target_is_directory=True)
        link.resolve(strict=True)
    except OSError:
        # Either symlink creation isn't permitted in this environment, or
        # (some sandboxed/locked-down hosts) path resolution THROUGH a
        # freshly created symlink is itself blocked -- both are
        # environment limitations unrelated to the behavior under test.
        pytest.skip("symlinks are not fully usable in this environment")
    _write_in_tree_pyproject(plugin_dir, sources={"demo-widget": "libs/widget"})

    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_vendored_libs(plugin_dir)


def test_resolve_vendored_libs_in_tree_nested_symlink_raises(fake_repo: Path):
    # Regression: the lib directory itself is a real directory (not a
    # symlink), but a file WITHIN it is a symlink -- the shallow
    # `unresolved.is_symlink()` check alone would miss this, letting
    # hashing/building silently follow it outside the vendored tree.
    plugin_dir = fake_repo / "plugins" / "demo"
    lib_dir = _seed_in_tree_lib(plugin_dir, "widget")
    outside = fake_repo / "outside.txt"
    outside.write_text("not part of the vendored tree", encoding="utf-8")
    link = lib_dir / "src" / "widget" / "escape.py"
    try:
        link.symlink_to(outside)
        link.resolve(strict=True)
    except OSError:
        pytest.skip("symlinks are not fully usable in this environment")
    _write_in_tree_pyproject(plugin_dir, sources={"demo-widget": "libs/widget"})

    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_vendored_libs(plugin_dir)


# --- read_project_version / version correspondence -------------------------


def test_read_project_version(fake_repo: Path):
    plugin_dir = fake_repo / "plugins" / "demo"
    _write_pyproject(plugin_dir)
    assert bpa.read_project_version(plugin_dir) == "0.1.0"


def test_read_project_version_missing_raises(tmp_path: Path):
    d = tmp_path / "demo"
    d.mkdir()
    (d / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.read_project_version(d)


def test_read_project_version_malformed_project_table_raises(tmp_path: Path):
    # Invariant: [project] itself must be validated as a table before
    # .get() is called on it -- a TOML-valid manifest where `project` is
    # not a table (e.g. an array) must raise the documented
    # ArtifactBuildError, not an uncaught AttributeError.
    d = tmp_path / "demo"
    d.mkdir()
    (d / "pyproject.toml").write_text("project = []\n", encoding="utf-8")
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.read_project_version(d)


def test_assert_version_corresponds_accepts_hyphen_normalization():
    # No exception means acceptance.
    bpa._assert_version_corresponds(
        raw_version="0.4.1-dev3", wheel_version="0.4.1.dev3", label="demo"
    )


def test_assert_version_corresponds_rejects_real_mismatch():
    with pytest.raises(bpa.ArtifactBuildError):
        bpa._assert_version_corresponds(
            raw_version="0.4.1-dev3", wheel_version="9.9.9", label="demo"
        )


# --- directory_content_hash / compute_payload_hash -------------------------


def test_directory_content_hash_stable_for_unchanged_content(tmp_path: Path):
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    assert bpa.directory_content_hash(d) == bpa.directory_content_hash(d)


def test_hash_fields_no_concatenation_ambiguity():
    # Regression: naively joining fields with a plain separator lets two
    # DIFFERENT field sequences serialize identically (e.g. "ab"+"c" ==
    # "a"+"bc" == "abc"); length-prefixing must keep them distinct.
    assert bpa._hash_fields("ab", "c") != bpa._hash_fields("a", "bc")


def test_directory_content_hash_changes_with_content(tmp_path: Path):
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    before = bpa.directory_content_hash(d)
    (d / "a.py").write_text("x = 2\n", encoding="utf-8")
    after = bpa.directory_content_hash(d)
    assert before != after


def test_directory_content_hash_ignores_pycache_and_build_dirs(tmp_path: Path):
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    before = bpa.directory_content_hash(d)
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "a.cpython-312.pyc").write_bytes(b"\x00\x01")
    (d / "build").mkdir()
    (d / "build" / "stuff.txt").write_text("noise", encoding="utf-8")
    after = bpa.directory_content_hash(d)
    assert before == after


def test_directory_content_hash_ignores_egg_info_across_repeated_calls(
    tmp_path: Path,
):
    # Regression: *.egg-info has a per-package-varying name (unlike the
    # fixed names in _PAYLOAD_IGNORE_DIR_NAMES), and a build backend can
    # leave one behind inside the source tree. Without ignoring it by
    # suffix, a SECOND invocation's "pre-build" hash would differ from the
    # first just because the first build's residue is still on disk --
    # even though no real source changed.
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "a.py").write_text("x = 1\n", encoding="utf-8")
    before = bpa.directory_content_hash(d)
    (d / "demo.egg-info").mkdir()
    (d / "demo.egg-info" / "PKG-INFO").write_text("generated", encoding="utf-8")
    after = bpa.directory_content_hash(d)
    assert before == after


def test_directory_content_hash_reflects_uncommitted_working_tree_mutation(
    tmp_path: Path,
):
    # The whole point of hashing the working tree instead of git HEAD:
    # a promotion-time mutation (e.g. a version bump) that was never
    # committed must still change the payload hash.
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "pyproject.toml").write_text('version = "0.1.0"\n', encoding="utf-8")
    before = bpa.directory_content_hash(d)
    (d / "pyproject.toml").write_text('version = "0.1.0.dev99"\n', encoding="utf-8")
    after = bpa.directory_content_hash(d)
    assert before != after


def test_compute_payload_hash_deterministic_regardless_of_order(
    fake_repo: Path,
):
    plugin_dir = fake_repo / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "f.py").write_text("x\n", encoding="utf-8")
    lib_dir = fake_repo / "libs" / "widget"
    lib_dir.mkdir(parents=True)
    (lib_dir / "f.py").write_text("y\n", encoding="utf-8")

    dirs = [plugin_dir, lib_dir]
    h1 = bpa.compute_payload_hash(dirs)
    h2 = bpa.compute_payload_hash(list(reversed(dirs)))
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_compute_payload_hash_changes_when_either_dir_changes(fake_repo: Path):
    plugin_dir = fake_repo / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "f.py").write_text("x\n", encoding="utf-8")
    lib_dir = fake_repo / "libs" / "widget"
    lib_dir.mkdir(parents=True)
    (lib_dir / "f.py").write_text("y\n", encoding="utf-8")
    dirs = [plugin_dir, lib_dir]
    before = bpa.compute_payload_hash(dirs)

    (lib_dir / "f.py").write_text("z\n", encoding="utf-8")
    after = bpa.compute_payload_hash(dirs)

    assert before != after


# --- build_wheel (mocked subprocess) ---------------------------------------


def _staging_dir_from_cmd(cmd: list[str]) -> Path:
    return Path(cmd[cmd.index("-o") + 1])


def test_build_wheel_moves_output_into_out_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    out_dir = tmp_path / "dist"

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        (_staging_dir_from_cmd(cmd) / "new_pkg-2.0-py3-none-any.whl").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    wheel = bpa.build_wheel(tmp_path / "src", out_dir)
    assert wheel == out_dir / "new_pkg-2.0-py3-none-any.whl"
    assert wheel.is_file()


def test_build_wheel_overwrites_existing_same_name_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a second invocation that rebuilds the exact same wheel
    # filename (identical version rebuilt again, or a retry after a later
    # manifest step failed and left a same-named wheel behind) must still
    # be detected as a successful build, not silently seen as "0 new
    # wheels" because the before/after filename set didn't change.
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    existing = out_dir / "demo-1.0-py3-none-any.whl"
    existing.write_bytes(b"old-bytes")

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        (_staging_dir_from_cmd(cmd) / "demo-1.0-py3-none-any.whl").write_bytes(
            b"new-bytes"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    wheel = bpa.build_wheel(tmp_path / "src", out_dir)
    assert wheel == existing
    assert wheel.read_bytes() == b"new-bytes"


def test_build_wheel_nonzero_exit_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_wheel(tmp_path / "src", tmp_path / "dist")


def test_build_wheel_ambiguous_output_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        staging = _staging_dir_from_cmd(cmd)
        (staging / "a-1.0-py3-none-any.whl").write_bytes(b"")
        (staging / "b-1.0-py3-none-any.whl").write_bytes(b"")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_wheel(tmp_path / "src", tmp_path / "dist")


# --- build_plugin_artifacts (end-to-end, mocked build_wheel) ---------------


def test_build_plugin_artifacts_end_to_end(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    plugin_dir = fake_repo / "plugins" / "demo"
    _seed_valid_lib(fake_repo, "widget")
    _write_pyproject(plugin_dir, sources={"demo-widget": "../../libs/widget"})

    out_dir = fake_repo / "dist"

    def fake_build_wheel(source_dir: Path, out: Path, *, toolchain=None, reserved_names=None):  # noqa: ARG001
        out.mkdir(parents=True, exist_ok=True)
        name = source_dir.name.replace("-", "_")
        wheel = out / f"{name}-0.1.0-py3-none-any.whl"
        _make_fake_wheel(wheel, generator="setuptools (84.1.0)")
        return wheel

    monkeypatch.setattr(bpa, "build_wheel", fake_build_wheel)

    manifest = bpa.build_plugin_artifacts("demo", out_dir=out_dir, toolchain=_FAKE_TOOLCHAIN)

    assert manifest["schema"] == bpa.MANIFEST_SCHEMA
    assert manifest["plugin"] == "demo"
    assert manifest["build_toolchain"] == {
        "packages": {"setuptools": "84.1.0", "wheel": "0.44.0"},
        "lock_id": _FAKE_TOOLCHAIN.lock_id,
    }
    assert manifest["python_tag"] == "py3"
    assert {e["role"] for e in manifest["wheels"]} == {"plugin", "vendored-lib"}
    assert len(manifest["wheels"]) == 2
    for wheel_entry in manifest["wheels"]:
        # Regression: each wheel's OWN parsed tags must be recorded, not
        # just the artifact set's aggregate ones.
        assert wheel_entry["python_tag"] == "py3"
        assert wheel_entry["abi_tag"] == "none"
        assert wheel_entry["platform_tag"] == "any"
    manifest_path = out_dir / "demo-0.1.0-manifest.json"
    assert manifest_path.is_file()
    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest


def test_build_plugin_artifacts_rejects_nested_symlink_in_plugin_dir(
    fake_repo: Path,
):
    plugin_dir = fake_repo / "plugins" / "demo"
    _write_pyproject(plugin_dir)
    outside = fake_repo / "outside.txt"
    outside.write_text("not part of the plugin tree", encoding="utf-8")
    link = plugin_dir / "escape.py"
    try:
        link.symlink_to(outside)
        link.resolve(strict=True)
    except OSError:
        pytest.skip("symlinks are not fully usable in this environment")

    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_plugin_artifacts("demo", out_dir=fake_repo / "dist")


def test_build_plugin_artifacts_payload_hash_excludes_build_residue(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: payload_hash must reflect the PRE-build source tree, not
    # whatever a build backend happens to leave behind inside it (e.g.
    # setuptools' build_meta creating a *.egg-info directory alongside the
    # sources even for an isolated wheel build).
    plugin_dir = fake_repo / "plugins" / "demo"
    _write_pyproject(plugin_dir)
    expected_hash = bpa.compute_payload_hash([plugin_dir])
    out_dir = fake_repo / "dist"

    def fake_build_wheel(source_dir: Path, out: Path, *, toolchain=None, reserved_names=None):  # noqa: ARG001
        (source_dir / "demo.egg-info").mkdir(exist_ok=True)
        (source_dir / "demo.egg-info" / "PKG-INFO").write_text(
            "build residue", encoding="utf-8"
        )
        out.mkdir(parents=True, exist_ok=True)
        wheel = out / "demo-0.1.0-py3-none-any.whl"
        _make_fake_wheel(wheel, generator="setuptools (84.1.0)")
        return wheel

    monkeypatch.setattr(bpa, "build_wheel", fake_build_wheel)
    manifest = bpa.build_plugin_artifacts("demo", out_dir=out_dir, toolchain=_FAKE_TOOLCHAIN)

    assert manifest["payload_hash"] == expected_hash

    # Invariant: residue left behind by THIS invocation must not change
    # the NEXT invocation's "pre-build" hash either -- the egg-info
    # directory is still on disk when build_plugin_artifacts runs again.
    manifest2 = bpa.build_plugin_artifacts("demo", out_dir=out_dir, toolchain=_FAKE_TOOLCHAIN)
    assert manifest2["payload_hash"] == expected_hash


def test_build_plugin_artifacts_artifact_id_changes_with_wheel_bytes(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: artifact_id must fold in the wheels' own digests, not just
    # payload hash/tags/toolchain -- two byte-distinct wheel sets built from
    # identical source must not collide on the same artifact_id.
    plugin_dir = fake_repo / "plugins" / "demo"
    _write_pyproject(plugin_dir)
    out_dir = fake_repo / "dist"

    def make_builder(payload: bytes):
        def fake_build_wheel(source_dir: Path, out: Path, *, toolchain=None, reserved_names=None):  # noqa: ARG001
            out.mkdir(parents=True, exist_ok=True)
            wheel = out / "demo-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as zf:
                zf.writestr(
                    "demo-0.1.0.dist-info/WHEEL",
                    "Wheel-Version: 1.0\nGenerator: setuptools (84.1.0)\n",
                )
                zf.writestr("demo/payload.bin", payload)
            return wheel

        return fake_build_wheel

    monkeypatch.setattr(bpa, "build_wheel", make_builder(b"aaa"))
    manifest1 = bpa.build_plugin_artifacts("demo", out_dir=out_dir, toolchain=_FAKE_TOOLCHAIN)
    (out_dir / "demo-0.1.0-py3-none-any.whl").unlink()
    monkeypatch.setattr(bpa, "build_wheel", make_builder(b"bbb"))
    manifest2 = bpa.build_plugin_artifacts("demo", out_dir=out_dir, toolchain=_FAKE_TOOLCHAIN)

    assert manifest1["payload_hash"] == manifest2["payload_hash"]
    assert manifest1["wheels"][0]["sha256"] != manifest2["wheels"][0]["sha256"]
    assert manifest1["artifact_id"] != manifest2["artifact_id"]


def test_build_plugin_artifacts_rejects_out_dir_nested_in_source(
    fake_repo: Path,
):
    # Regression: an out_dir nested under a hashed source directory would
    # be created and populated with wheels/manifest BEFORE a retry's own
    # payload_hash computation, folding a prior run's own output into its
    # own identity.
    plugin_dir = fake_repo / "plugins" / "demo"
    _write_pyproject(plugin_dir)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_plugin_artifacts("demo", out_dir=plugin_dir / "artifacts")


def test_build_plugin_artifacts_preserves_raw_version(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: the manifest's version (and filename) must be the raw
    # declared version ("0.4.1-dev3"-shaped), not the wheel's PEP 440
    # normalization ("0.4.1.dev3"), so it exactly matches the
    # <plugin>-v<version> release-tag identity this effort documents.
    plugin_dir = fake_repo / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.4.1-dev3"\n', encoding="utf-8"
    )
    out_dir = fake_repo / "dist"

    def fake_build_wheel(source_dir: Path, out: Path, *, toolchain=None, reserved_names=None):  # noqa: ARG001
        out.mkdir(parents=True, exist_ok=True)
        wheel = out / "demo-0.4.1.dev3-py3-none-any.whl"
        _make_fake_wheel(wheel, generator="setuptools (84.1.0)")
        return wheel

    monkeypatch.setattr(bpa, "build_wheel", fake_build_wheel)
    manifest = bpa.build_plugin_artifacts("demo", out_dir=out_dir, toolchain=_FAKE_TOOLCHAIN)

    assert manifest["version"] == "0.4.1-dev3"
    assert (out_dir / "demo-0.4.1-dev3-manifest.json").is_file()


def test_build_plugin_artifacts_rejects_real_version_mismatch(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    plugin_dir = fake_repo / "plugins" / "demo"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.4.1-dev3"\n', encoding="utf-8"
    )
    out_dir = fake_repo / "dist"

    def fake_build_wheel(source_dir: Path, out: Path, *, toolchain=None, reserved_names=None):  # noqa: ARG001
        out.mkdir(parents=True, exist_ok=True)
        wheel = out / "demo-9.9.9-py3-none-any.whl"
        _make_fake_wheel(wheel, generator="setuptools (84.1.0)")
        return wheel

    monkeypatch.setattr(bpa, "build_wheel", fake_build_wheel)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_plugin_artifacts("demo", out_dir=out_dir, toolchain=_FAKE_TOOLCHAIN)


def test_build_plugin_artifacts_unsafe_plugin_name_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: `plugin` becomes a path component (PLUGINS_DIR / plugin,
    # and the manifest filename) -- an absolute value or `../` traversal
    # must be rejected before either is constructed.
    monkeypatch.setattr(bpa, "PLUGINS_DIR", tmp_path / "plugins")
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_plugin_artifacts("../../etc", out_dir=tmp_path / "dist")


def test_build_plugin_artifacts_rejects_duplicate_wheel_filename(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: two DIFFERENT sources (here, the plugin and its one
    # vendored lib) producing the identical wheel filename within the SAME
    # invocation must fail closed -- silently overwriting would leave an
    # earlier manifest entry's sha256 describing bytes no longer on disk.
    plugin_dir = fake_repo / "plugins" / "demo"
    _seed_valid_lib(fake_repo, "widget")
    _write_pyproject(plugin_dir, sources={"demo-widget": "../../libs/widget"})
    out_dir = fake_repo / "dist"

    def fake_build_wheel(source_dir: Path, out: Path, *, toolchain=None, reserved_names=None):  # noqa: ARG001
        out.mkdir(parents=True, exist_ok=True)
        # Every source produces the SAME filename, regardless of identity.
        wheel = out / "collision-0.1.0-py3-none-any.whl"
        _make_fake_wheel(wheel, generator="setuptools (84.1.0)")
        if reserved_names is not None:
            if wheel.name in reserved_names:
                raise bpa.ArtifactBuildError(f"{wheel.name} collides within this invocation")
            reserved_names.add(wheel.name)
        return wheel

    monkeypatch.setattr(bpa, "build_wheel", fake_build_wheel)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_plugin_artifacts("demo", out_dir=out_dir, toolchain=_FAKE_TOOLCHAIN)


def test_build_wheel_rejects_filename_already_reserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    out_dir = tmp_path / "dist"

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        (_staging_dir_from_cmd(cmd) / "demo-1.0-py3-none-any.whl").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    reserved = {"demo-1.0-py3-none-any.whl"}
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_wheel(tmp_path / "src", out_dir, reserved_names=reserved)


def test_build_plugin_artifacts_unknown_plugin_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(bpa, "PLUGINS_DIR", tmp_path / "plugins")
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_plugin_artifacts("nope", out_dir=tmp_path / "dist")


# --- ToolchainLock -----------------------------------------------------


def test_toolchain_lock_generator():
    lock = bpa.ToolchainLock(Path("/fake/python"), {"setuptools": "84.1.0", "wheel": "0.44.0"})
    assert lock.generator == "setuptools (84.1.0)"


def test_toolchain_lock_id_stable_for_same_packages():
    lock_a = bpa.ToolchainLock(Path("/fake/python"), {"setuptools": "84.1.0", "wheel": "0.44.0"})
    lock_b = bpa.ToolchainLock(Path("/other/python"), {"wheel": "0.44.0", "setuptools": "84.1.0"})
    assert lock_a.lock_id == lock_b.lock_id


def test_toolchain_lock_id_changes_with_different_packages():
    lock_a = bpa.ToolchainLock(Path("/fake/python"), {"setuptools": "84.1.0", "wheel": "0.44.0"})
    lock_b = bpa.ToolchainLock(Path("/fake/python"), {"setuptools": "84.2.0", "wheel": "0.44.0"})
    assert lock_a.lock_id != lock_b.lock_id


# --- resolve_toolchain_lock (mocked subprocess) ------------------------


def _toolchain_query_stdout(packages: dict[str, str]) -> str:
    return json.dumps(packages)


def _assume_governed_feed_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most `resolve_toolchain_lock` tests below exercise something OTHER
    than the governed-feed gate itself -- bypass it so they aren't coupled
    to this machine's/CI runner's real environment."""
    monkeypatch.setattr(bpa, "_governed_feed_configured", lambda **_: True)


def test_resolve_toolchain_lock_creates_venv_and_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        calls.append(cmd)
        if cmd[:2] == ["uv", "venv"]:
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        # the `<venv_python> -c <script> setuptools wheel` version query
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    lock = bpa.resolve_toolchain_lock(venv_dir)

    assert lock.packages == {"setuptools": "84.1.0", "wheel": "0.44.0"}
    assert lock.venv_python == venv_python
    assert any(cmd[:2] == ["uv", "venv"] for cmd in calls)
    assert any(cmd[:3] == ["uv", "pip", "install"] for cmd in calls)


def test_resolve_toolchain_lock_reuses_existing_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)
    # Regression: a second call with the SAME venv_dir (the mechanism a
    # caller uses to share one toolchain lock across several plugins in
    # one promotion run) must not re-create or re-install -- only read
    # back the already-installed versions.
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        calls.append(cmd)
        assert cmd[:2] != ["uv", "venv"]
        assert cmd[:3] != ["uv", "pip", "install"]
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    lock = bpa.resolve_toolchain_lock(venv_dir)

    assert lock.packages == {"setuptools": "84.1.0", "wheel": "0.44.0"}
    assert len(calls) == 1


def test_resolve_toolchain_lock_venv_creation_failure_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no governed feed")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(tmp_path / "toolchain-venv")


def test_resolve_toolchain_lock_install_failure_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            venv_python.parent.mkdir(parents=True, exist_ok=True)
            venv_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="install failed")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(venv_dir)


def test_resolve_toolchain_lock_query_nonzero_exit_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(venv_dir)


def test_resolve_toolchain_lock_query_malformed_json_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(venv_dir)


def test_resolve_toolchain_lock_missing_package_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)
    # Regression: a locked venv genuinely missing one of the two required
    # packages (e.g. a previous partial/failed install) must fail closed
    # rather than record an incomplete toolchain lock.
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        return subprocess.CompletedProcess(
            cmd, 0, stdout=_toolchain_query_stdout({"setuptools": "84.1.0"}), stderr=""
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(venv_dir)


# --- governed-feed enforcement ------------------------------------------


def test_governed_feed_configured_via_env_var():
    assert bpa._governed_feed_configured(env={"UV_INDEX_URL": "https://example.internal/simple/"})
    assert bpa._governed_feed_configured(env={"UV_DEFAULT_INDEX": "https://example.internal/simple/"})
    assert bpa._governed_feed_configured(env={"UV_INDEX": "https://example.internal/simple/"})


def test_governed_feed_not_configured_with_empty_env():
    assert not bpa._governed_feed_configured(env={})


def test_governed_feed_configured_via_user_uv_toml_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    uv_toml = tmp_path / "uv" / "uv.toml"
    uv_toml.parent.mkdir(parents=True)
    uv_toml.write_text('index-url = "https://example.internal/simple/"\n', encoding="utf-8")
    assert bpa._governed_feed_configured(env={"APPDATA": str(tmp_path)})


def test_governed_feed_not_configured_when_uv_toml_has_no_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    uv_toml = tmp_path / "uv" / "uv.toml"
    uv_toml.parent.mkdir(parents=True)
    uv_toml.write_text('# no index configured here\n', encoding="utf-8")
    assert not bpa._governed_feed_configured(env={"APPDATA": str(tmp_path)})


def test_governed_feed_configured_via_user_uv_toml_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bpa.sys, "platform", "linux")
    uv_toml = tmp_path / "uv" / "uv.toml"
    uv_toml.parent.mkdir(parents=True)
    uv_toml.write_text('index-url = "https://example.internal/simple/"\n', encoding="utf-8")
    assert bpa._governed_feed_configured(env={"XDG_CONFIG_HOME": str(tmp_path)})


def test_governed_feed_not_configured_ignores_malformed_uv_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # A malformed uv.toml must never be silently treated as "configured" --
    # fail closed the same as "absent".
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    uv_toml = tmp_path / "uv" / "uv.toml"
    uv_toml.parent.mkdir(parents=True)
    uv_toml.write_text("not = [valid toml", encoding="utf-8")
    assert not bpa._governed_feed_configured(env={"APPDATA": str(tmp_path)})


def test_resolve_toolchain_lock_refuses_when_no_governed_feed_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: with no configured governed feed at all, resolving the
    # toolchain must fail closed rather than let `uv pip install` silently
    # resolve setuptools/wheel from public PyPI.
    monkeypatch.setattr(bpa, "_governed_feed_configured", lambda **_: False)
    calls: list[list[str]] = []

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(tmp_path / "toolchain-venv")
    assert calls == []  # never even attempted `uv venv`/`uv pip install`


# --- build_wheel with a locked toolchain --------------------------------


def test_build_wheel_with_toolchain_uses_no_build_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    out_dir = tmp_path / "dist"
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )
    seen_cmd: list[str] = []

    def fake_run(cmd, capture_output, text):  # noqa: ARG001
        seen_cmd.extend(cmd)
        (_staging_dir_from_cmd(cmd) / "demo-1.0-py3-none-any.whl").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    # A `python` argument must be ignored once a toolchain is given -- the
    # toolchain's own venv_python is the only interpreter used.
    bpa.build_wheel(tmp_path / "src", out_dir, python="/some/other/python", toolchain=toolchain)

    assert "--no-build-isolation" in seen_cmd
    assert str(toolchain.venv_python) in seen_cmd
    assert "/some/other/python" not in seen_cmd


# --- build_plugin_artifacts hermeticity verification --------------------


def test_build_plugin_artifacts_rejects_generator_mismatch_with_toolchain(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a wheel whose actual Generator: does not match the
    # locked toolchain means --no-build-isolation did not really use the
    # pinned venv -- this must fail closed, not silently record a drifted
    # toolchain.
    plugin_dir = fake_repo / "plugins" / "demo"
    _write_pyproject(plugin_dir)
    out_dir = fake_repo / "dist"

    def fake_build_wheel(source_dir: Path, out: Path, *, toolchain=None, reserved_names=None):  # noqa: ARG001
        out.mkdir(parents=True, exist_ok=True)
        wheel = out / "demo-0.1.0-py3-none-any.whl"
        _make_fake_wheel(wheel, generator="setuptools (60.0.0)")
        return wheel

    monkeypatch.setattr(bpa, "build_wheel", fake_build_wheel)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_plugin_artifacts("demo", out_dir=out_dir, toolchain=_FAKE_TOOLCHAIN)


def test_build_plugin_artifacts_resolves_own_toolchain_when_none_given(
    fake_repo: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a caller that doesn't pass a toolchain (today's CLI
    # default) must still get a real, pinned, non-isolated build -- this
    # verifies build_plugin_artifacts resolves (and cleans up) its own
    # disposable lock rather than silently reverting to an isolated build.
    plugin_dir = fake_repo / "plugins" / "demo"
    _write_pyproject(plugin_dir)
    out_dir = fake_repo / "dist"
    resolved_dirs: list[Path] = []

    def fake_resolve_toolchain_lock(venv_dir: Path, *, python=None):  # noqa: ARG001
        resolved_dirs.append(venv_dir)
        return _FAKE_TOOLCHAIN

    def fake_build_wheel(source_dir: Path, out: Path, *, toolchain=None, reserved_names=None):  # noqa: ARG001
        assert toolchain is _FAKE_TOOLCHAIN
        out.mkdir(parents=True, exist_ok=True)
        wheel = out / "demo-0.1.0-py3-none-any.whl"
        _make_fake_wheel(wheel, generator="setuptools (84.1.0)")
        return wheel

    monkeypatch.setattr(bpa, "resolve_toolchain_lock", fake_resolve_toolchain_lock)
    monkeypatch.setattr(bpa, "build_wheel", fake_build_wheel)

    manifest = bpa.build_plugin_artifacts("demo", out_dir=out_dir)

    assert len(resolved_dirs) == 1
    assert not resolved_dirs[0].exists()  # the disposable toolchain dir is cleaned up
    assert manifest["build_toolchain"]["lock_id"] == _FAKE_TOOLCHAIN.lock_id
