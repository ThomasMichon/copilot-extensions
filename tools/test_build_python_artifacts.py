from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

#: Captured at import time, BEFORE any test's monkeypatching -- the one
#: genuine `subprocess.run`, for the single test that deliberately needs
#: it (`test_query_marker_environment_against_real_interpreter`). `
#: subprocess` is a shared singleton module object: by the time a test
#: body runs, `subprocess.run` itself may already be this file's own
#: autouse default stub, so grabbing "the real one" at THAT point would
#: just return the stub.
_REAL_SUBPROCESS_RUN = subprocess.run

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "tools" / "build_python_artifacts.py"

_SPEC = importlib.util.spec_from_file_location("build_python_artifacts", MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
bpa = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bpa)

uer = bpa.uer  # the real uv_editable_ref module build_python_artifacts imports
# The real build_toolchain_lock module -- cached in sys.modules as a side
# effect of bpa's own `from build_toolchain_lock import ...` above. Needed
# because a name like `_governed_feed_configured`, once imported into
# `bpa`'s own namespace, is just a separate binding there: monkeypatching
# `bpa._governed_feed_configured` would NOT affect `resolve_toolchain_lock`'s
# own internal call to it, since that call resolves via THIS module's own
# globals, not bpa's.
btl = sys.modules["build_toolchain_lock"]
# The real governed_feed_trust module -- same reasoning as `btl` above:
# `_opaque_index_identity`'s own internal call to `_provenance_key_dir`
# resolves via THIS module's globals, not `btl`'s re-exported binding.
gft = sys.modules["governed_feed_trust"]


@pytest.fixture(autouse=True)
def _isolated_provenance_key_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`_opaque_index_identity`'s keyed-hash machinery persists a per-
    machine key under the real LOCALAPPDATA/XDG_STATE_HOME -- no test in
    this file may read or write that real, shared location. Isolated to
    this test's own `tmp_path` for every test, automatically.

    Also stubs `subprocess.run` to a trivial always-succeeding no-op by
    default: on this machine (genuinely Windows), `_provenance_key`'s own
    call to `_restrict_file_to_owner` would otherwise shell out to REAL
    `icacls` against a path inside pytest's own tmp tree for every test
    that merely touches `_opaque_index_identity`/`_provenance_key` --
    those paths carry the SAME untrusted-mount-point quirk documented
    elsewhere in this file's own pytest-teardown workarounds, and
    `icacls` behaves unreliably against them, unrelated to this fix's
    actual correctness (verified separately via real smoke tests against
    normal paths). Any test that needs its OWN subprocess behavior
    (nearly every `resolve_toolchain_lock`/`build_wheel` test) overrides
    this default later in its own body, same as any other monkeypatch
    stacking in this file."""
    monkeypatch.setattr(gft, "_provenance_key_dir", lambda: tmp_path / "provenance-key-dir")
    monkeypatch.setattr(
        gft.subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),  # noqa: ARG005
    )



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

    def fake_run(cmd, **kwargs):  # noqa: ARG001
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

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        (_staging_dir_from_cmd(cmd) / "demo-1.0-py3-none-any.whl").write_bytes(
            b"new-bytes"
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    wheel = bpa.build_wheel(tmp_path / "src", out_dir)
    assert wheel == existing
    assert wheel.read_bytes() == b"new-bytes"


def test_build_wheel_nonzero_exit_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_wheel(tmp_path / "src", tmp_path / "dist")


def test_build_wheel_ambiguous_output_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_run(cmd, **kwargs):  # noqa: ARG001
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

    def fake_run(cmd, **kwargs):  # noqa: ARG001
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
    to this machine's/CI runner's real environment. Patches the REAL
    defining module (`btl`), not the re-exported `bpa` alias -- see `btl`'s
    own module-load comment above for why that distinction matters here.
    `resolve_toolchain_lock` itself now calls `_validated_trusted_index_url`
    directly (not `_governed_feed_configured`), so that's the function
    patched here.

    Also bypasses `_resolve_interpreter_identity` (which otherwise shells
    out to `uv python find`) with an identity passthrough -- tests
    exercising THAT specific resolution behavior override this again with
    their own dedicated mock; every other test below just wants its given
    `python` argument treated as a stable identity, unchanged."""
    monkeypatch.setattr(
        btl, "_validated_trusted_index_url",
        lambda env: ("https://example.internal/simple/", None),  # noqa: ARG005
    )
    monkeypatch.setattr(
        btl, "_resolve_interpreter_identity", lambda python, env: python or ""
    )


def test_resolve_toolchain_lock_creates_venv_and_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        calls.append(cmd)
        if cmd[:2] == ["uv", "venv"]:
            # cmd[3] is the STAGING dir -- resolve_toolchain_lock builds
            # there first and only renames into venv_dir after both steps
            # succeed.
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        # the `<venv_python> -c <script> setuptools wheel` version query
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    lock = bpa.resolve_toolchain_lock(venv_dir)

    assert lock.packages == {"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}
    assert lock.venv_python == venv_python
    assert any(cmd[:2] == ["uv", "venv"] for cmd in calls)
    assert any(cmd[:3] == ["uv", "pip", "install"] for cmd in calls)


def test_resolve_toolchain_lock_version_query_uses_isolated_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a direct-interpreter `-c` probe must use `-I` (isolated
    # mode) -- otherwise a local `json.py`/`platform.py` reachable from the
    # subprocess's current working directory could shadow the stdlib
    # module the script imports and forge its result, even with
    # PYTHONPATH/PYTHONHOME stripped (which does not close this
    # current-directory `sys.path` vector).
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    btl._write_provenance_marker(venv_dir, "https://example.internal/simple/")
    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_cmds.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.resolve_toolchain_lock(venv_dir)

    assert len(seen_cmds) == 1
    assert seen_cmds[0][0] == str(venv_python)
    assert seen_cmds[0][1] == "-I"
    assert seen_cmds[0][2] == "-c"


def test_query_marker_environment_uses_isolated_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_cmds.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"python_version": "3.12"}), stderr=""
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa._query_marker_environment(tmp_path / "python")

    assert seen_cmds[0][1] == "-I"
    assert seen_cmds[0][2] == "-c"


def test_resolve_toolchain_lock_reuses_existing_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)
    # Regression: a second call with the SAME venv_dir (the mechanism a
    # caller uses to share one toolchain lock across several plugins in
    # one promotion run) must not re-create or re-install -- only read
    # back the already-installed versions. Requires a matching provenance
    # marker (see `_write_provenance_marker`) -- without one, reuse would
    # be refused and this venv rebuilt instead (see the provenance tests
    # below).
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    btl._write_provenance_marker(venv_dir, "https://example.internal/simple/")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        calls.append(cmd)
        assert cmd[:2] != ["uv", "venv"]
        assert cmd[:3] != ["uv", "pip", "install"]
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    lock = bpa.resolve_toolchain_lock(venv_dir)

    assert lock.packages == {"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}
    assert len(calls) == 1


def test_resolve_toolchain_lock_rebuilds_when_provenance_marker_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: an interpreter existing at venv_dir is NOT itself a
    # trust signal -- a venv published before this provenance check
    # existed (no marker at all) must never be silently reused just
    # because `venv_python.is_file()` happens to be true. It must also
    # NEVER be renamed/quarantined aside: another process sharing this
    # exact path could be actively building against it right now. Instead
    # this call must resolve into its own deterministic alternate sibling,
    # leaving venv_dir completely untouched.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("stale-unmarked-interpreter", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        calls.append(cmd)
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("rebuilt", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    lock = bpa.resolve_toolchain_lock(venv_dir)

    assert any(cmd[:2] == ["uv", "venv"] for cmd in calls)
    assert any(cmd[:3] == ["uv", "pip", "install"] for cmd in calls)
    assert lock.venv_python.read_text(encoding="utf-8") == "rebuilt"
    # The stale, unmarked venv at venv_dir itself was NEVER touched --
    # no rename/quarantine of a path another process might be using.
    assert venv_python.read_text(encoding="utf-8") == "stale-unmarked-interpreter"
    assert list(venv_dir.parent.glob(f".{venv_dir.name}.untrusted-*")) == []
    # The rebuilt lock lives at the deterministic alternate sibling.
    expected_alt = btl._alternate_toolchain_dir(
        venv_dir, "https://example.internal/simple/", None
    )
    assert lock.venv_python == bpa._venv_python_path(expected_alt)


def test_resolve_toolchain_lock_rebuilds_when_provenance_marker_mismatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a venv published from a DIFFERENT index than the one
    # currently validated (e.g. built under a prior/different trust
    # policy) must never be reused just because its marker exists -- only
    # an EXACT match to the currently validated index is trusted. It must
    # also be left completely untouched (never renamed/quarantined),
    # since another process may be actively building against it.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    btl._write_provenance_marker(venv_dir, "https://different.example/simple/")

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    lock = bpa.resolve_toolchain_lock(venv_dir)

    # venv_dir itself is untouched: still carries its ORIGINAL (different)
    # provenance, never overwritten or renamed aside.
    assert btl._provenance_matches(venv_dir, "https://different.example/simple/")
    assert not btl._provenance_matches(venv_dir, "https://example.internal/simple/")
    assert list(venv_dir.parent.glob(f".{venv_dir.name}.untrusted-*")) == []
    # The new lock was built at the deterministic alternate sibling instead.
    expected_alt = btl._alternate_toolchain_dir(
        venv_dir, "https://example.internal/simple/", None
    )
    assert lock.venv_python == bpa._venv_python_path(expected_alt)
    assert btl._provenance_matches(expected_alt, "https://example.internal/simple/")


def test_resolve_toolchain_lock_reuses_alternate_sibling_on_repeat_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a SECOND call sharing the same venv_dir, with the same
    # differing identity, must reuse the alternate sibling built by the
    # first call rather than rebuilding it again -- the "shared lock"
    # promise still holds for repeat calls at one (expected-rare)
    # mismatched identity, just via a second shared slot.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    btl._write_provenance_marker(venv_dir, "https://different.example/simple/")

    build_count = {"n": 0}

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            build_count["n"] += 1
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    lock1 = bpa.resolve_toolchain_lock(venv_dir)
    lock2 = bpa.resolve_toolchain_lock(venv_dir)

    assert build_count["n"] == 1
    assert lock1.venv_python == lock2.venv_python


def test_resolve_toolchain_lock_treats_different_python_as_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: the reuse identity must not ignore the requested
    # --python -- a venv created for one interpreter must never be
    # silently returned for a later call that explicitly asked for a
    # different one.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("py310-build", encoding="utf-8")
    btl._write_provenance_marker(
        venv_dir, "https://example.internal/simple/", "/usr/bin/python3.10"
    )
    build_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            build_cmds.append(cmd)
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("py312-build", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    lock = bpa.resolve_toolchain_lock(venv_dir, python="/usr/bin/python3.12")

    # The ORIGINAL (3.10) venv at venv_dir was never touched.
    assert venv_python.read_text(encoding="utf-8") == "py310-build"
    # This call's own (3.12) lock was built fresh at the alternate sibling.
    assert lock.venv_python.read_text(encoding="utf-8") == "py312-build"
    assert "--python" in build_cmds[0]
    assert build_cmds[0][build_cmds[0].index("--python") + 1] == "/usr/bin/python3.12"


# --- _resolve_interpreter_identity: text selector vs resolved identity ----


def test_resolve_interpreter_identity_queries_uv_python_find(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    real_interpreter = tmp_path / "real-python"
    real_interpreter.write_text("", encoding="utf-8")
    seen_cmds: list[list[str]] = []
    seen_envs: list[dict] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_cmds.append(cmd)
        seen_envs.append(kwargs.get("env"))
        return subprocess.CompletedProcess(
            cmd, 0, stdout=str(real_interpreter) + "\n", stderr=""
        )

    monkeypatch.setattr(btl.subprocess, "run", fake_run)
    sentinel_env = {"SENTINEL": "1"}
    identity = btl._resolve_interpreter_identity("python3.12", sentinel_env)

    # Regression (round 21): must probe with `--no-config` plus the
    # CALLER's own already-sanitized env -- the SAME config context
    # `uv venv` itself uses -- never its own, separately-resolved one;
    # otherwise the probe and `uv venv` could silently disagree about
    # which interpreter is actually in play (e.g. a project `uv.toml`
    # pinning `python`, visible to the probe but not to `uv venv
    # --no-config`).
    assert seen_cmds == [["uv", "python", "find", "--no-config", "python3.12"]]
    assert seen_envs == [sentinel_env]
    assert identity == str(real_interpreter.resolve())


def test_resolve_interpreter_identity_fails_closed_on_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    # Regression (round 21): a failed probe (nonzero exit, empty output,
    # or `uv` itself missing) must raise, never fall back to the raw
    # selector text. A text fallback is NOT fail-closed: if the probe
    # fails for two calls whose selector text is identical but whose
    # ACTUAL interpreters differ (e.g. `python3` repointed between
    # calls), both would record the same approximate identity and
    # falsely compare equal -- exactly the false-match hazard this
    # function exists to prevent.
    monkeypatch.setattr(
        btl.subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not found"),  # noqa: ARG005
    )
    with pytest.raises(bpa.ArtifactBuildError):
        btl._resolve_interpreter_identity("python3.12", {})
    with pytest.raises(bpa.ArtifactBuildError):
        btl._resolve_interpreter_identity(None, {})

    def raise_oserror(cmd, **kwargs):  # noqa: ARG001
        raise OSError("uv not found")

    monkeypatch.setattr(btl.subprocess, "run", raise_oserror)
    with pytest.raises(bpa.ArtifactBuildError):
        btl._resolve_interpreter_identity("python3.12", {})


def test_resolve_toolchain_lock_detects_resolved_interpreter_change_despite_same_selector_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression (the core scenario motivating resolved-identity
    # tracking): the SAME selector text (e.g. a symlink, or a bare
    # command resolved via PATH) can resolve to a genuinely DIFFERENT
    # real interpreter over time. A marker keyed on resolved identity
    # (not raw text) must detect this and route to a fresh build, never
    # silently reuse the stale venv.
    monkeypatch.setattr(
        btl, "_validated_trusted_index_url",
        lambda env: ("https://example.internal/simple/", None),  # noqa: ARG005
    )
    resolved = {"value": "/opt/pythons/3.10/bin/python"}
    monkeypatch.setattr(
        btl, "_resolve_interpreter_identity", lambda python, env: resolved["value"]  # noqa: ARG005
    )
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("original-build", encoding="utf-8")
    btl._write_provenance_marker(
        venv_dir, "https://example.internal/simple/", resolved["value"]
    )

    # The SAME selector is used both times -- but the symlink it resolves
    # to is simulated as having been repointed in between.
    resolved["value"] = "/opt/pythons/3.11/bin/python"
    build_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            build_cmds.append(cmd)
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("rebuilt-after-repoint", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    lock = bpa.resolve_toolchain_lock(venv_dir, python="python3")

    assert len(build_cmds) == 1
    assert venv_python.read_text(encoding="utf-8") == "original-build"
    assert lock.venv_python.read_text(encoding="utf-8") == "rebuilt-after-repoint"


def test_resolve_toolchain_lock_rejects_anomalous_alternate_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: the alternate sibling is keyed EXACTLY on this call's
    # own identity, so finding something there that does NOT match is a
    # genuine anomaly (never an expected race) -- must fail closed rather
    # than silently rebuild over or reuse an unverified venv.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_python = bpa._venv_python_path(venv_dir)
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")
    btl._write_provenance_marker(venv_dir, "https://different.example/simple/")

    alt_dir = btl._alternate_toolchain_dir(
        venv_dir, "https://example.internal/simple/", None
    )
    alt_python = bpa._venv_python_path(alt_dir)
    alt_python.parent.mkdir(parents=True, exist_ok=True)
    alt_python.write_text("", encoding="utf-8")
    btl._write_provenance_marker(alt_dir, "https://yet-another.example/simple/")

    monkeypatch.setattr(
        bpa.subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, stdout="", stderr=""),  # noqa: ARG005
    )
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(venv_dir)


def test_resolve_toolchain_lock_treats_preexisting_empty_dir_as_occupied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: an empty (or partially-built) pre-existing directory at
    # --toolchain-venv -- e.g. manually created by an operator, or residue
    # from a crashed prior run -- is not detectable via `venv_python.
    # is_file()` alone (it's False either way), but attempting to build
    # directly into it would make the eventual publish rename fail
    # (Windows rejects renaming onto an already-existing destination,
    # even an empty one). Must be routed to the alternate slot instead,
    # never attempted as a build target directly.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    venv_dir.mkdir()  # exists, but empty -- no venv_python, no marker at all

    build_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            build_cmds.append(cmd)
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("rebuilt", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    lock = bpa.resolve_toolchain_lock(venv_dir)

    assert len(build_cmds) == 1
    assert lock.venv_python.read_text(encoding="utf-8") == "rebuilt"
    expected_alt = btl._alternate_toolchain_dir(
        venv_dir, "https://example.internal/simple/", None
    )
    assert lock.venv_python == bpa._venv_python_path(expected_alt)
    # The original empty directory itself was never touched/populated.
    assert list(venv_dir.iterdir()) == []


# --- provenance marker never persists embedded credentials ---------------


def _credentialed_url(host_path: str) -> str:
    """Builds a URL with embedded credentials via runtime string
    concatenation -- avoids a literal credential-bearing URL pattern
    anywhere in this source file."""
    return "https://" + "produser" + ":" + "hunter2" + "@" + host_path


def test_provenance_marker_redacts_embedded_credentials(tmp_path: Path):
    # Regression: a validated index URL may carry embedded credentials
    # (uv supports this), but those credentials must never be written to
    # the on-disk marker persisted into every shared venv -- the marker
    # stores only a one-way hash of the full URL, never the URL itself in
    # any form.
    venv_dir = tmp_path / "toolchain-venv"
    venv_dir.mkdir()
    btl._write_provenance_marker(
        venv_dir, _credentialed_url("example.internal/simple/")
    )
    marker_text = (venv_dir / btl._PROVENANCE_MARKER_NAME).read_text(encoding="utf-8")
    assert "hunter2" not in marker_text
    assert "produser" not in marker_text
    assert "example.internal" not in marker_text


def test_provenance_matches_compares_full_url_identity(tmp_path: Path):
    # The exact same credentialed URL, re-supplied on a later call (e.g. a
    # second plugin build in the same promotion run), must still match.
    venv_dir = tmp_path / "toolchain-venv"
    venv_dir.mkdir()
    btl._write_provenance_marker(
        venv_dir, _credentialed_url("example.internal/simple/")
    )
    assert btl._provenance_matches(
        venv_dir, _credentialed_url("example.internal/simple/")
    )
    # A DIFFERENT set of credentials against the same host/path does NOT
    # match -- the full URL (not a redacted host/path-only form) is what
    # gets hashed and compared, so a rotated credential is treated as a
    # genuinely different identity rather than silently reused.
    assert not btl._provenance_matches(
        venv_dir, "https://" + "otheruser" + ":" + "otherpass" + "@example.internal/simple/"
    )
    assert not btl._provenance_matches(venv_dir, "https://different.example/simple/")


def test_provenance_matches_treats_differing_query_as_different_identity(
    tmp_path: Path,
):
    # Regression: a query string can select a tenant/feed, not just carry
    # a credential -- two URLs sharing a host and path but differing only
    # by query parameter must be treated as genuinely different indexes,
    # never silently conflated into the same reuse identity.
    venv_dir = tmp_path / "toolchain-venv"
    venv_dir.mkdir()
    btl._write_provenance_marker(venv_dir, "https://example.internal/simple/?feed=A")
    assert btl._provenance_matches(venv_dir, "https://example.internal/simple/?feed=A")
    assert not btl._provenance_matches(venv_dir, "https://example.internal/simple/?feed=B")
    assert not btl._provenance_matches(venv_dir, "https://example.internal/simple/")


def test_opaque_index_identity_is_not_reversible_to_the_raw_url():
    digest = btl._opaque_index_identity(_credentialed_url("example.internal/simple/"))
    assert "hunter2" not in digest
    assert "produser" not in digest
    assert "example.internal" not in digest
    # Deterministic: the same input always hashes to the same identity.
    assert digest == btl._opaque_index_identity(
        _credentialed_url("example.internal/simple/")
    )


def test_opaque_index_identity_is_keyed_not_a_bare_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression (round 20): a bare `hashlib.sha256(url)` digest is an
    # offline-crackable verifier for a low-entropy embedded credential --
    # most of a governed-feed URL is predictable, so an attacker holding
    # only the persisted digest could brute-force a guessable password
    # against it with no network access at all. The digest must instead
    # depend on machine-local secret key material: the SAME url must
    # produce a DIFFERENT digest under a different key, and must never
    # equal the bare (unkeyed) hash.
    url = _credentialed_url("example.internal/simple/")
    monkeypatch.setattr(gft, "_provenance_key_dir", lambda: tmp_path / "key-a")
    digest_a = btl._opaque_index_identity(url)
    monkeypatch.setattr(gft, "_provenance_key_dir", lambda: tmp_path / "key-b")
    digest_b = btl._opaque_index_identity(url)
    assert digest_a != digest_b
    import hashlib as _hashlib  # local import: avoid a module-level alias collision

    assert digest_a != _hashlib.sha256(url.encode("utf-8")).hexdigest()


def test_provenance_key_persists_and_is_reused_across_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # The key must be generated once per machine and REUSED, not re-rolled
    # per call -- otherwise the same (index, python) identity would never
    # compare equal across separate invocations, breaking
    # `resolve_toolchain_lock`'s own reuse/provenance-match contract.
    key_dir = tmp_path / "provenance-key-dir"
    monkeypatch.setattr(gft, "_provenance_key_dir", lambda: key_dir)

    key1 = gft._provenance_key()
    key2 = gft._provenance_key()
    assert key1 == key2
    assert len(key1) == 32
    key_path = key_dir / "provenance-key"
    assert key_path.is_file()
    if sys.platform != "win32":
        import stat

        mode = stat.S_IMODE(key_path.stat().st_mode)
        assert mode == 0o600


def test_provenance_key_hardens_acl_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression (round 22): the key is just as credential-bearing as the
    # index-config temp file it protects -- 0o600 mode bits alone are not
    # an owner-only ACL on Windows, so it must go through the same
    # `_restrict_file_to_owner` hardening.
    monkeypatch.setattr(gft, "_provenance_key_dir", lambda: tmp_path / "key-dir")
    monkeypatch.setattr(gft.sys, "platform", "win32")
    monkeypatch.setenv("USERDOMAIN", "REDMOND")
    monkeypatch.setenv("USERNAME", "svc")
    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(gft.subprocess, "run", fake_run)
    gft._provenance_key()

    assert len(seen_cmds) == 3
    grant_cmd = seen_cmds[0]
    assert grant_cmd[0] == "icacls"
    assert "/inheritance:r" in grant_cmd
    assert "REDMOND\\svc:F" in grant_cmd


def test_provenance_key_returns_final_persisted_value_on_publish_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression (round 22): two concurrent first-run callers can both
    # observe a missing key and each publish their own -- whichever
    # `os.replace` lands LAST silently becomes the real machine key. A
    # caller must return whatever is ACTUALLY persisted afterward, never
    # trust the in-memory key it itself generated, or its own later
    # provenance computations would key on a value no longer on disk.
    monkeypatch.setattr(gft, "_provenance_key_dir", lambda: tmp_path / "key-dir")
    real_replace = gft.os.replace

    def racing_replace(src, dst):  # noqa: ARG001
        # Simulate a concurrent winner publishing a DIFFERENT key
        # immediately after this call's own os.replace, but before it
        # re-reads -- by publishing the winner's key INSTEAD of this
        # call's own.
        Path(dst).write_bytes(b"\xaa" * 32)

    monkeypatch.setattr(gft.os, "replace", racing_replace)
    key = gft._provenance_key()
    assert key == b"\xaa" * 32  # the winner's key, not whatever we generated
    monkeypatch.setattr(gft.os, "replace", real_replace)


def test_credential_free_index_identity_strips_userinfo_only():
    assert (
        btl._credential_free_index_identity(
            _credentialed_url("example.internal:8443/simple/")
        )
        == "https://example.internal:8443/simple/"
    )
    # A URL with no embedded credentials is returned unchanged.
    assert (
        btl._credential_free_index_identity("https://example.internal/simple/")
        == "https://example.internal/simple/"
    )


def test_credential_free_index_identity_strips_query_and_fragment():
    # Regression: a governed feed may use a signed URL with credentials
    # carried in the query string (e.g. `?token=...`) or fragment, not
    # just userinfo -- both must be stripped before the value is ever
    # persisted or displayed, same as userinfo.
    assert (
        btl._credential_free_index_identity(
            "https://example.internal/simple/?token=topsecret"
        )
        == "https://example.internal/simple/"
    )
    assert (
        btl._credential_free_index_identity(
            "https://example.internal/simple/#topsecret"
        )
        == "https://example.internal/simple/"
    )
    assert (
        btl._credential_free_index_identity(
            _credentialed_url("example.internal/simple/?token=topsecret#frag")
        )
        == "https://example.internal/simple/"
    )


def test_resolve_toolchain_lock_venv_creation_failure_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no governed feed")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(tmp_path / "toolchain-venv")


def test_resolve_toolchain_lock_install_failure_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
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

    def fake_run(cmd, **kwargs):  # noqa: ARG001
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

    def fake_run(cmd, **kwargs):  # noqa: ARG001
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

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return subprocess.CompletedProcess(
            cmd, 0, stdout=_toolchain_query_stdout({"setuptools": "84.1.0"}), stderr=""
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(venv_dir)


# --- governed-feed enforcement (affirmative trust-policy allowlist) ----

_TRUST_VAR = bpa._TRUSTED_INDEX_HOSTS_ENV_VAR


def test_governed_feed_configured_via_default_index_env_vars():
    assert bpa._governed_feed_configured(
        env={"UV_INDEX_URL": "https://example.internal/simple/", _TRUST_VAR: "example.internal"}
    )
    assert bpa._governed_feed_configured(
        env={"UV_DEFAULT_INDEX": "https://example.internal/simple/", _TRUST_VAR: "example.internal"}
    )


def test_governed_feed_not_configured_without_any_trust_policy():
    # Regression: an affirmatively non-public, explicitly configured
    # default index must still fail closed if this machine has not
    # affirmatively asserted ANY trust policy at all -- inferring trust
    # from "doesn't look like public PyPI" is exactly the inference this
    # allowlist design replaces.
    assert not bpa._governed_feed_configured(
        env={"UV_DEFAULT_INDEX": "https://example.internal/simple/"}
    )


def test_governed_feed_not_configured_when_index_host_not_in_trust_policy():
    # Regression: a trust policy that simply doesn't name the configured
    # index's host must not somehow be satisfied by unrelated entries.
    assert not bpa._governed_feed_configured(
        env={
            "UV_DEFAULT_INDEX": "https://unexpected.example/simple/",
            _TRUST_VAR: "example.internal,other.internal",
        }
    )


def test_governed_feed_not_configured_via_supplemental_uv_index_alone():
    # Regression: UV_INDEX (plural) only adds a SUPPLEMENTAL index -- `uv`
    # still falls back to public PyPI for anything it doesn't resolve, so
    # this alone must never satisfy the governed-feed-only contract, even
    # with a matching trust policy.
    assert not bpa._governed_feed_configured(
        env={"UV_INDEX": "https://example.internal/simple/", _TRUST_VAR: "example.internal"}
    )


def test_governed_feed_not_configured_when_default_index_is_public_pypi():
    # Regression: an explicitly configured default that just points AT
    # public PyPI itself is not a governed feed, even if (implausibly)
    # pypi.org were ever listed in the trust policy.
    assert not bpa._governed_feed_configured(
        env={"UV_DEFAULT_INDEX": "https://pypi.org/simple", _TRUST_VAR: "pypi.org"}
    )
    assert not bpa._governed_feed_configured(
        env={"UV_INDEX_URL": "https://PyPI.org/simple/", _TRUST_VAR: "pypi.org"}
    )


def test_governed_feed_not_configured_with_empty_env():
    assert not bpa._governed_feed_configured(env={})


def test_governed_feed_configured_via_user_uv_toml_index_url_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    uv_toml = tmp_path / "uv" / "uv.toml"
    uv_toml.parent.mkdir(parents=True)
    uv_toml.write_text('index-url = "https://example.internal/simple/"\n', encoding="utf-8")
    assert bpa._governed_feed_configured(
        env={"APPDATA": str(tmp_path), _TRUST_VAR: "example.internal"}
    )


def test_governed_feed_configured_via_user_uv_toml_default_index_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bpa.sys, "platform", "linux")
    uv_toml = tmp_path / "uv" / "uv.toml"
    uv_toml.parent.mkdir(parents=True)
    uv_toml.write_text(
        '[[index]]\nname = "governed"\nurl = "https://example.internal/simple/"\ndefault = true\n',
        encoding="utf-8",
    )
    env = {"XDG_CONFIG_HOME": str(tmp_path), _TRUST_VAR: "example.internal"}
    assert bpa._governed_feed_configured(env=env)
    # Regression (round 19): a named `[[index]]` entry's own `name` must
    # survive through both `_effective_default_index_url` and
    # `_validated_trusted_index_url` -- a caller authenticating a named
    # index (`UV_INDEX_<NAME>_USERNAME`/`PASSWORD`) cannot recover the
    # name from the URL alone.
    assert btl._effective_default_index_url(env) == (
        "https://example.internal/simple/", "governed",
    )
    assert btl._validated_trusted_index_url(env) == (
        "https://example.internal/simple/", "governed",
    )


def test_effective_uv_toml_candidates_windows_includes_programdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a system-level uv.toml (%PROGRAMDATA%) must also be
    # discovered -- matching this repo's own install.ps1 precedent
    # (Test-UvConfiguredIndex) -- not just the user-level %APPDATA% path.
    monkeypatch.setattr(btl.sys, "platform", "win32")
    candidates = btl._effective_uv_toml_candidates(
        {"APPDATA": r"C:\Users\x\AppData\Roaming", "PROGRAMDATA": r"C:\ProgramData"}
    )
    assert Path(r"C:\ProgramData\uv\uv.toml") in candidates


def test_governed_feed_configured_via_programdata_uv_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    uv_toml = tmp_path / "uv" / "uv.toml"
    uv_toml.parent.mkdir(parents=True)
    uv_toml.write_text('index-url = "https://example.internal/simple/"\n', encoding="utf-8")
    assert bpa._governed_feed_configured(
        env={"PROGRAMDATA": str(tmp_path), _TRUST_VAR: "example.internal"}
    )


def test_effective_uv_toml_candidates_posix_includes_system_paths(
    monkeypatch: pytest.MonkeyPatch,
):
    # Regression: /etc/uv/uv.toml and /etc/xdg/uv/uv.toml (system-level)
    # must also be discovered -- matching this repo's own install.sh
    # precedent (_ensure_uv_index), not just the user-level path.
    monkeypatch.setattr(btl.sys, "platform", "linux")
    candidates = btl._effective_uv_toml_candidates({"HOME": "/home/x"})
    assert Path("/etc/uv/uv.toml") in candidates
    assert Path("/etc/xdg/uv/uv.toml") in candidates


def test_governed_feed_not_configured_via_supplemental_index_table_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: an `[[index]]` entry with no `default = true` only adds
    # a supplemental index -- `uv` still falls back to public PyPI for the
    # default, so this alone must not satisfy the gate.
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    uv_toml = tmp_path / "uv" / "uv.toml"
    uv_toml.parent.mkdir(parents=True)
    uv_toml.write_text(
        '[[index]]\nname = "extra"\nurl = "https://example.internal/simple/"\n',
        encoding="utf-8",
    )
    assert not bpa._governed_feed_configured(
        env={"APPDATA": str(tmp_path), _TRUST_VAR: "example.internal"}
    )


def test_governed_feed_not_configured_when_uv_toml_default_index_is_public_pypi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    uv_toml = tmp_path / "uv" / "uv.toml"
    uv_toml.parent.mkdir(parents=True)
    uv_toml.write_text('index-url = "https://pypi.org/simple"\n', encoding="utf-8")
    assert not bpa._governed_feed_configured(
        env={"APPDATA": str(tmp_path), _TRUST_VAR: "pypi.org"}
    )


def test_governed_feed_not_configured_when_uv_toml_has_no_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    uv_toml = tmp_path / "uv" / "uv.toml"
    uv_toml.parent.mkdir(parents=True)
    uv_toml.write_text('# no index configured here\n', encoding="utf-8")
    assert not bpa._governed_feed_configured(
        env={"APPDATA": str(tmp_path), _TRUST_VAR: "example.internal"}
    )


def test_governed_feed_not_configured_ignores_malformed_uv_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # A malformed uv.toml must never be silently treated as "configured" --
    # fail closed the same as "absent".
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    uv_toml = tmp_path / "uv" / "uv.toml"
    uv_toml.parent.mkdir(parents=True)
    uv_toml.write_text("not = [valid toml", encoding="utf-8")
    assert not bpa._governed_feed_configured(
        env={"APPDATA": str(tmp_path), _TRUST_VAR: "example.internal"}
    )


def test_resolve_toolchain_lock_refuses_when_no_governed_feed_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: with no configured governed feed at all, resolving the
    # toolchain must fail closed rather than let `uv pip install` silently
    # resolve setuptools/wheel from public PyPI.
    monkeypatch.setattr(btl, "_validated_trusted_index_url", lambda env: None)  # noqa: ARG005
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(tmp_path / "toolchain-venv")
    assert calls == []  # never even attempted `uv venv`/`uv pip install`


# --- resolve_toolchain_lock staging/atomic-publish behavior -------------


def test_resolve_toolchain_lock_recovers_from_prior_interrupted_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a FIRST attempt where `uv venv` succeeds but `uv pip
    # install` fails (simulating an interrupted/failed setup) must not
    # leave behind a venv that looks complete -- a retry must redo both
    # steps, not skip straight to the (forever-failing) version query.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    attempt = {"n": 0}

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            attempt["n"] += 1
            if attempt["n"] == 1:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="interrupted")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(venv_dir)
    assert not bpa._venv_python_path(venv_dir).is_file()
    assert not venv_dir.exists()

    lock = bpa.resolve_toolchain_lock(venv_dir)
    assert lock.packages == {"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}


def test_resolve_toolchain_lock_never_publishes_venv_failing_version_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: if the installed-versions query fails against the
    # STAGING venv (malformed output, a missing package), the venv must
    # never be published (marker written + renamed) -- otherwise every
    # retry would see a "complete, matching" venv at venv_dir and reuse
    # the same broken install forever, instead of rebuilding.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    attempt = {"n": 0}

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:1] == ["icacls"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        # the version query itself -- missing "packaging" on the first
        # attempt (simulating a broken/incomplete install), healthy on retry
        attempt["n"] += 1
        if attempt["n"] == 1:
            return subprocess.CompletedProcess(
                cmd, 0,
                stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0"}),
                stderr="",
            )
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError, match="packaging"):
        bpa.resolve_toolchain_lock(venv_dir)

    # Never published: no marker, no interpreter at venv_dir, and no
    # leftover staging directory.
    assert not bpa._venv_python_path(venv_dir).is_file()
    assert not venv_dir.exists()
    assert list(tmp_path.glob(".toolchain-venv.staging-*")) == []

    # A retry redoes the whole build and succeeds once the install is
    # genuinely complete.
    lock = bpa.resolve_toolchain_lock(venv_dir)
    assert lock.packages == {"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}


def test_resolve_toolchain_lock_leaves_no_staging_directory_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.resolve_toolchain_lock(venv_dir)
    leftovers = list(tmp_path.glob(".toolchain-venv.staging-*"))
    assert leftovers == []


# --- build_wheel with a locked toolchain --------------------------------


def test_build_wheel_with_toolchain_uses_no_build_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    out_dir = tmp_path / "dist"
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools>=60"]\n', encoding="utf-8"
    )
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )
    seen_cmd: list[str] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[0] == str(toolchain.venv_python):
            return _dispatch_toolchain_subprocess(cmd, kwargs, toolchain)
        seen_cmd.extend(cmd)
        (_staging_dir_from_cmd(cmd) / "demo-1.0-py3-none-any.whl").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    # A `python` argument must be ignored once a toolchain is given -- the
    # toolchain's own venv_python is the only interpreter used.
    bpa.build_wheel(src_dir, out_dir, python="/some/other/python", toolchain=toolchain)

    assert "--no-build-isolation" in seen_cmd
    assert str(toolchain.venv_python) in seen_cmd
    assert "/some/other/python" not in seen_cmd


# --- UV_CONFIG_FILE exclusivity ------------------------------------------


def test_governed_feed_uv_config_file_is_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: UV_CONFIG_FILE disables uv's normal config discovery --
    # a governed user-level uv.toml must NOT be consulted once it's set,
    # even though it exists and would otherwise satisfy the gate (and
    # even with a trust policy that WOULD accept its host).
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    user_uv_toml = tmp_path / "appdata" / "uv" / "uv.toml"
    user_uv_toml.parent.mkdir(parents=True)
    user_uv_toml.write_text('index-url = "https://example.internal/simple/"\n', encoding="utf-8")
    explicit_config = tmp_path / "explicit-uv.toml"
    explicit_config.write_text("# no index configured here\n", encoding="utf-8")
    assert not bpa._governed_feed_configured(
        env={
            "APPDATA": str(tmp_path / "appdata"),
            "UV_CONFIG_FILE": str(explicit_config),
            _TRUST_VAR: "example.internal",
        }
    )


def test_governed_feed_uv_config_file_governed_is_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    explicit_config = tmp_path / "explicit-uv.toml"
    explicit_config.write_text(
        'index-url = "https://example.internal/simple/"\n', encoding="utf-8"
    )
    assert bpa._governed_feed_configured(
        env={"UV_CONFIG_FILE": str(explicit_config), _TRUST_VAR: "example.internal"}
    )


# --- project-level uv.toml/pyproject.toml config discovery ---------------


def test_governed_feed_project_uv_toml_takes_precedence_over_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a project-level uv.toml (found by walking up from the
    # current working directory) must be consulted BEFORE user-/system-
    # level config, and its own index wins even when a DIFFERENT
    # (otherwise also-governed) index is configured at the user level.
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "uv.toml").write_text(
        'index-url = "https://project.internal/simple/"\n', encoding="utf-8"
    )
    user_uv_toml = tmp_path / "appdata" / "uv" / "uv.toml"
    user_uv_toml.parent.mkdir(parents=True)
    user_uv_toml.write_text(
        'index-url = "https://user.internal/simple/"\n', encoding="utf-8"
    )
    monkeypatch.chdir(project_dir)
    assert btl._effective_default_index_url(
        {"APPDATA": str(tmp_path / "appdata")}
    ) == ("https://project.internal/simple/", None)


def test_governed_feed_project_pyproject_tool_uv_section_is_honored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # A pyproject.toml's own [tool.uv] table is the project-level
    # equivalent of a dedicated uv.toml, nested under `tool.uv` rather
    # than top-level.
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text(
        '[tool.uv]\nindex-url = "https://project.internal/simple/"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(project_dir)
    assert btl._effective_default_index_url({}) == ("https://project.internal/simple/", None)


def test_governed_feed_project_pyproject_without_tool_uv_table_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # A pyproject.toml with no [tool.uv] table at all carries no uv
    # config -- must not be mistaken for an empty-but-present index.
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n', encoding="utf-8"
    )
    monkeypatch.chdir(project_dir)
    assert btl._effective_default_index_url({}) is None


def test_governed_feed_project_discovery_skipped_when_uv_config_file_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # UV_CONFIG_FILE's exclusivity (uv skips ALL normal discovery once
    # set) must suppress project-level discovery too, not just user/
    # system config.
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "uv.toml").write_text(
        'index-url = "https://project.internal/simple/"\n', encoding="utf-8"
    )
    explicit_config = tmp_path / "explicit-uv.toml"
    explicit_config.write_text("# no index configured here\n", encoding="utf-8")
    monkeypatch.chdir(project_dir)
    assert btl._effective_default_index_url(
        {"UV_CONFIG_FILE": str(explicit_config)}
    ) is None


def test_governed_feed_malformed_project_config_fails_closed_not_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a malformed HIGHER-precedence config (one that exists
    # at a real tier `uv` itself would read) must never be skipped in
    # favor of a lower-precedence one -- `uv` itself would not silently
    # fall through to a different file either. A valid, otherwise-
    # trusted user-level uv.toml must NOT be used when the project-level
    # one (checked first) is unparseable.
    monkeypatch.setattr(bpa.sys, "platform", "win32")
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "uv.toml").write_text("not = [valid toml", encoding="utf-8")
    user_uv_toml = tmp_path / "appdata" / "uv" / "uv.toml"
    user_uv_toml.parent.mkdir(parents=True)
    user_uv_toml.write_text(
        'index-url = "https://user.internal/simple/"\n', encoding="utf-8"
    )
    monkeypatch.chdir(project_dir)
    assert btl._effective_default_index_url(
        {"APPDATA": str(tmp_path / "appdata")}
    ) is None


# --- Test PyPI is treated as public --------------------------------------


def test_governed_feed_not_configured_when_default_index_is_test_pypi():
    assert not bpa._governed_feed_configured(
        env={"UV_DEFAULT_INDEX": "https://test.pypi.org/simple", _TRUST_VAR: "test.pypi.org"}
    )


def test_governed_feed_not_configured_when_public_pypi_host_has_trailing_dot():
    # Regression: a fully-qualified hostname ending in "." (DNS-equivalent
    # to the same host without it) must not bypass the public-PyPI
    # denylist, even if that exact spelling is listed as trusted.
    assert not bpa._governed_feed_configured(
        env={"UV_DEFAULT_INDEX": "https://pypi.org./simple", _TRUST_VAR: "pypi.org."}
    )
    assert not bpa._governed_feed_configured(
        env={"UV_DEFAULT_INDEX": "https://pypi.org./simple", _TRUST_VAR: "pypi.org"}
    )


def test_governed_feed_configured_when_trusted_host_has_trailing_dot_either_side():
    # The normalization must work symmetrically: a trusted-list entry with
    # a trailing dot must still match an index URL host without one, and
    # vice versa.
    assert bpa._governed_feed_configured(
        env={"UV_DEFAULT_INDEX": "https://example.internal./simple", _TRUST_VAR: "example.internal"}
    )
    assert bpa._governed_feed_configured(
        env={"UV_DEFAULT_INDEX": "https://example.internal/simple", _TRUST_VAR: "example.internal."}
    )


# --- HTTPS is required even for a trusted hostname ------------------------


def test_governed_feed_not_configured_when_trusted_host_is_plain_http():
    # Regression: an allowlisted HOSTNAME is not itself proof of identity
    # over a plaintext connection -- a network attacker able to intercept
    # `http://<trusted-host>/...` could impersonate the real governed feed.
    assert not bpa._governed_feed_configured(
        env={"UV_DEFAULT_INDEX": "http://example.internal/simple", _TRUST_VAR: "example.internal"}
    )


def test_governed_feed_configured_when_trusted_host_is_https():
    assert bpa._governed_feed_configured(
        env={"UV_DEFAULT_INDEX": "https://example.internal/simple", _TRUST_VAR: "example.internal"}
    )


# --- resolve_toolchain_lock concurrent-publisher race --------------------


def test_resolve_toolchain_lock_defers_to_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: if another process publishes a complete venv at the same
    # venv_dir between our own absence check and our own publish, we must
    # NOT delete/replace that live venv -- discard our own staging copy
    # and let the (now-existing) destination stand.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    sentinel = venv_dir / "WINNER"

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_dir = Path(cmd[3])
            staging_python = bpa._venv_python_path(staging_dir)
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            # Simulate a concurrent publisher winning the race: publish a
            # complete, marked venv -- carrying a MATCHING provenance
            # marker, exactly as the real publish path writes one before
            # its own rename -- at the real destination BEFORE this
            # call's own staging -> venv_dir rename happens.
            winner_python = bpa._venv_python_path(venv_dir)
            winner_python.parent.mkdir(parents=True, exist_ok=True)
            winner_python.write_text("", encoding="utf-8")
            btl._write_provenance_marker(venv_dir, "https://example.internal/simple/")
            sentinel.write_text("winner", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.resolve_toolchain_lock(venv_dir)

    # The "winner"'s venv must still be standing untouched -- our own
    # losing publisher must never have deleted/replaced it.
    assert sentinel.is_file()


def test_resolve_toolchain_lock_different_identity_race_redirects_to_alternate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression (round 20): a rename failure caused by a DIFFERENT
    # identity winning the race must NOT fail closed -- two concurrent
    # callers validating DIFFERENT indexes can both observe an absent
    # destination before either publishes. This call's own already-
    # validated staging venv must instead be redirected to its own
    # deterministic alternate slot (exactly as if the pre-staging
    # occupancy check had caught the occupation before staging began),
    # leaving the winner's own venv at the shared slot completely
    # untouched.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_dir = Path(cmd[3])
            staging_python = bpa._venv_python_path(staging_dir)
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            # Simulate a concurrent publisher winning the race, but from a
            # DIFFERENT validated index -- its provenance marker does not
            # match what THIS call validated. This must happen AFTER this
            # call's own pre-staging occupancy check already passed (that
            # check runs before any `uv` subprocess at all), so it only
            # exercises the later, rename-time race.
            winner_python = bpa._venv_python_path(venv_dir)
            winner_python.parent.mkdir(parents=True, exist_ok=True)
            winner_python.write_text("", encoding="utf-8")
            btl._write_provenance_marker(venv_dir, "https://different.example/simple/")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    lock = bpa.resolve_toolchain_lock(venv_dir)

    expected_alt = btl._alternate_toolchain_dir(
        venv_dir, "https://example.internal/simple/", ""
    )
    assert lock.venv_python == bpa._venv_python_path(expected_alt)
    assert btl._provenance_matches(expected_alt, "https://example.internal/simple/", "")
    # The winner's own venv at the shared slot must be left completely
    # untouched -- never renamed, deleted, or overwritten.
    assert btl._provenance_matches(venv_dir, "https://different.example/simple/", "")


def test_resolve_toolchain_lock_fails_closed_on_mismatch_at_alternate_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a mismatch found AT an identity-keyed alternate path
    # (never the original shared slot) is a genuine anomaly, not an
    # expected race -- this must still fail closed, exactly like the
    # pre-staging `_occupied_by_other_identity` check does for the same
    # scenario.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"
    # Pre-occupy the SHARED slot with a different identity so this call's
    # own pre-staging check routes it to the alternate slot from the
    # start (allow_redirect=False once there).
    bpa._venv_python_path(venv_dir).parent.mkdir(parents=True, exist_ok=True)
    bpa._venv_python_path(venv_dir).write_text("", encoding="utf-8")
    btl._write_provenance_marker(venv_dir, "https://different.example/simple/", "")
    alt_dir = btl._alternate_toolchain_dir(venv_dir, "https://example.internal/simple/", "")

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_dir = Path(cmd[3])
            staging_python = bpa._venv_python_path(staging_dir)
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            # Simulate a THIRD, anomalous occupant appearing at the
            # identity-keyed alternate path itself, between this call's
            # own pre-staging check and its own publish attempt.
            alt_python = bpa._venv_python_path(alt_dir)
            alt_python.parent.mkdir(parents=True, exist_ok=True)
            alt_python.write_text("", encoding="utf-8")
            btl._write_provenance_marker(alt_dir, "https://yet-another.example/simple/")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(venv_dir)



def test_resolve_toolchain_lock_raises_on_genuine_rename_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a rename failure must not be silently swallowed as if a
    # concurrent publisher always won -- if the destination never actually
    # appeared (a genuine permission/filesystem/invalid-destination
    # error), this must surface as ArtifactBuildError, not fall through to
    # a later crash trying to launch a nonexistent interpreter.
    _assume_governed_feed_configured(monkeypatch)
    venv_dir = tmp_path / "toolchain-venv"

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)

    real_rename = Path.rename

    def failing_rename(self, target):  # noqa: ARG001
        raise OSError("simulated permission error")

    monkeypatch.setattr(Path, "rename", failing_rename)
    try:
        with pytest.raises(bpa.ArtifactBuildError):
            bpa.resolve_toolchain_lock(venv_dir)
    finally:
        monkeypatch.setattr(Path, "rename", real_rename)
    # No winner ever appeared -- the destination must genuinely be absent.
    assert not bpa._venv_python_path(venv_dir).is_file()


def test_resolve_toolchain_lock_uses_unique_staging_dirs_per_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: the staging directory name must be genuinely unique per
    # call (tempfile.mkdtemp), not merely PID-qualified -- two calls in
    # the SAME process (simulating two threads sharing a PID) must never
    # reuse the same staging path.
    _assume_governed_feed_configured(monkeypatch)
    staging_dirs_seen: list[str] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_dirs_seen.append(cmd[3])
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.resolve_toolchain_lock(tmp_path / "toolchain-venv-1")
    bpa.resolve_toolchain_lock(tmp_path / "toolchain-venv-2")

    assert len(staging_dirs_seen) == 2
    assert staging_dirs_seen[0] != staging_dirs_seen[1]


def test_resolve_toolchain_lock_pins_install_via_sanitized_config_not_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression (round 19): the validated index URL must never appear in
    # the `uv pip install` subprocess's own argv (visible in process
    # listings). Instead it is written to a minimal, sanitized temp uv
    # config file pointed to via `UV_CONFIG_FILE` -- EXCLUSIVE in uv's own
    # resolution, so nothing else can supply a different index, the same
    # guarantee `--no-config --index-url` was providing. Every ambient
    # alternative-package-source environment variable must still be
    # stripped -- never trusting uv's own ambient config, which could
    # otherwise still consult an untrusted supplemental index (UV_INDEX,
    # UV_EXTRA_INDEX_URL, a plain [[index]] entry) or a flat-file link
    # source (UV_FIND_LINKS, confirmed common on this repo's own
    # clean-room runners).
    monkeypatch.setattr(
        btl, "_validated_trusted_index_url",
        lambda env: ("https://governed.example/simple/", None),  # noqa: ARG005
    )
    monkeypatch.setattr(btl, "_resolve_interpreter_identity", lambda python, env: python or "")
    monkeypatch.setenv("UV_INDEX", "https://untrusted.example/simple/")
    monkeypatch.setenv("UV_EXTRA_INDEX_URL", "https://untrusted.example/extra/")
    monkeypatch.setenv("UV_FIND_LINKS", "https://untrusted.example/links/")
    venv_dir = tmp_path / "toolchain-venv"
    install_cmds = []
    install_kwargs = []
    config_texts_at_install_time = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            install_cmds.append(cmd)
            install_kwargs.append(kwargs)
            # Read the config file NOW, while the install call is actually
            # running -- `resolve_toolchain_lock`'s own `finally` block
            # deletes it once this call returns, so it is gone by the time
            # the test function resumes after `resolve_toolchain_lock`.
            config_texts_at_install_time.append(
                Path(kwargs["env"]["UV_CONFIG_FILE"]).read_text(encoding="utf-8")
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.resolve_toolchain_lock(venv_dir)

    assert len(install_cmds) == 1
    install_cmd = install_cmds[0]
    assert "--index-url" not in install_cmd
    assert not any("governed.example" in str(arg) for arg in install_cmd)
    install_env = install_kwargs[0].get("env")
    assert install_env is not None
    assert "UV_INDEX" not in install_env
    assert "UV_EXTRA_INDEX_URL" not in install_env
    assert "UV_FIND_LINKS" not in install_env
    config_text = config_texts_at_install_time[0]
    assert "[[index]]" in config_text
    assert 'url = "https://governed.example/simple/"' in config_text
    assert "default = true" in config_text
    assert 'name = "' not in config_text  # unnamed index: no name line written
    # Cleaned up afterward (success path).
    assert not Path(install_env["UV_CONFIG_FILE"]).exists()


def test_resolve_toolchain_lock_pins_named_index_and_preserves_credential_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression (round 19): a NAMED `[[index]]` entry's `name` must be
    # written into the sanitized temp config too, so a
    # `UV_INDEX_<NAME>_USERNAME`/`PASSWORD`-authenticated index can still
    # authenticate -- and those credential env vars must themselves keep
    # flowing through the install call's env unstripped, unlike every
    # other alternative-package-source variable.
    monkeypatch.setattr(
        btl, "_validated_trusted_index_url",
        lambda env: ("https://governed.example/simple/", "governed"),  # noqa: ARG005
    )
    monkeypatch.setattr(btl, "_resolve_interpreter_identity", lambda python, env: python or "")
    monkeypatch.setenv("UV_INDEX_GOVERNED_USERNAME", "svc-account")
    monkeypatch.setenv("UV_INDEX_GOVERNED_PASSWORD", "token-value")
    venv_dir = tmp_path / "toolchain-venv"
    install_kwargs = []
    config_texts_at_install_time = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            install_kwargs.append(kwargs)
            config_texts_at_install_time.append(
                Path(kwargs["env"]["UV_CONFIG_FILE"]).read_text(encoding="utf-8")
            )
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.resolve_toolchain_lock(venv_dir)

    install_env = install_kwargs[0].get("env")
    assert install_env is not None
    assert install_env.get("UV_INDEX_GOVERNED_USERNAME") == "svc-account"
    assert install_env.get("UV_INDEX_GOVERNED_PASSWORD") == "token-value"
    config_text = config_texts_at_install_time[0]
    assert 'name = "governed"' in config_text
    assert 'url = "https://governed.example/simple/"' in config_text
    assert not Path(install_env["UV_CONFIG_FILE"]).exists()  # cleaned up afterward


def test_resolve_toolchain_lock_cleans_up_sanitized_config_on_install_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: the temp sanitized config file must be cleaned up even
    # when `uv pip install` itself fails, not just on the success path.
    monkeypatch.setattr(
        btl, "_validated_trusted_index_url",
        lambda env: ("https://governed.example/simple/", None),  # noqa: ARG005
    )
    monkeypatch.setattr(btl, "_resolve_interpreter_identity", lambda python, env: python or "")
    venv_dir = tmp_path / "toolchain-venv"
    seen_config_paths: list[Path] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            config_path = Path(kwargs["env"]["UV_CONFIG_FILE"])
            assert config_path.exists()  # exists while the install call runs
            seen_config_paths.append(config_path)
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.resolve_toolchain_lock(venv_dir)

    assert len(seen_config_paths) == 1
    assert not seen_config_paths[0].exists()  # cleaned up despite the failure



def test_resolve_toolchain_lock_strips_uv_constraint_and_override_vars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: `--no-config` does not suppress `UV_CONSTRAINT`/
    # `UV_OVERRIDE`/`UV_BUILD_CONSTRAINT` -- any of these could redirect
    # the setuptools/wheel/packaging install to a direct URL regardless of
    # `--index-url`, bypassing the validated governed feed entirely.
    monkeypatch.setattr(
        btl, "_validated_trusted_index_url",
        lambda env: ("https://governed.example/simple/", None),  # noqa: ARG005
    )
    monkeypatch.setattr(btl, "_resolve_interpreter_identity", lambda python, env: python or "")
    monkeypatch.setenv("UV_CONSTRAINT", "https://untrusted.example/constraints.txt")
    monkeypatch.setenv("UV_OVERRIDE", "https://untrusted.example/overrides.txt")
    monkeypatch.setenv("UV_BUILD_CONSTRAINT", "https://untrusted.example/build-constraints.txt")
    venv_dir = tmp_path / "toolchain-venv"
    seen_envs: list[dict] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_envs.append(kwargs.get("env") or {})
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.resolve_toolchain_lock(venv_dir)

    for env in seen_envs[:2]:  # the uv venv and uv pip install calls
        assert "UV_CONSTRAINT" not in env
        assert "UV_OVERRIDE" not in env
        assert "UV_BUILD_CONSTRAINT" not in env


def test_resolve_toolchain_lock_strips_uv_insecure_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: `uv` honors `UV_INSECURE_HOST` to disable TLS
    # certificate verification for a named host -- left ambient, it could
    # let a network attacker impersonate even an allowlisted, HTTPS-only
    # governed-feed hostname despite the scheme check in
    # `_validated_trusted_index_url`.
    monkeypatch.setattr(
        btl, "_validated_trusted_index_url",
        lambda env: ("https://governed.example/simple/", None),  # noqa: ARG005
    )
    monkeypatch.setattr(btl, "_resolve_interpreter_identity", lambda python, env: python or "")
    monkeypatch.setenv("UV_INSECURE_HOST", "governed.example")
    venv_dir = tmp_path / "toolchain-venv"
    seen_envs: list[dict] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_envs.append(kwargs.get("env") or {})
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.resolve_toolchain_lock(venv_dir)

    for env in seen_envs[:2]:  # the uv venv and uv pip install calls
        assert "UV_INSECURE_HOST" not in env


# --- round 21: shared strip helper + final-build credential stripping ----


def test_strip_package_source_env_vars_keeps_credentials_by_default():
    env = {
        "UV_INDEX": "x", "UV_INDEX_URL": "x", "UV_INSECURE_HOST": "x",
        "UV_INDEX_GOVERNED_USERNAME": "svc", "UV_INDEX_GOVERNED_PASSWORD": "tok",
        "OTHER": "keep",
    }
    gft.strip_package_source_env_vars(env)
    assert "UV_INDEX" not in env
    assert "UV_INSECURE_HOST" not in env
    # Credentials survive unless explicitly asked to strip them too.
    assert env["UV_INDEX_GOVERNED_USERNAME"] == "svc"
    assert env["UV_INDEX_GOVERNED_PASSWORD"] == "tok"
    assert env["OTHER"] == "keep"


def test_strip_package_source_env_vars_with_strip_credentials():
    env = {
        "UV_INDEX_URL": "x",
        "UV_INDEX_GOVERNED_USERNAME": "svc",
        "UV_INDEX_GOVERNED_PASSWORD": "tok",
        "UV_INDEX_OTHER_FEED_USERNAME": "svc2",
        "OTHER": "keep",
    }
    gft.strip_package_source_env_vars(env, strip_credentials=True)
    assert "UV_INDEX_URL" not in env
    assert "UV_INDEX_GOVERNED_USERNAME" not in env
    assert "UV_INDEX_GOVERNED_PASSWORD" not in env
    assert "UV_INDEX_OTHER_FEED_USERNAME" not in env
    assert env["OTHER"] == "keep"


def test_build_wheel_strips_index_credentials_with_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression (round 21): `--no-build-isolation` means this build needs
    # NO index access at all -- yet the build BACKEND executes arbitrary
    # code from the source tree, which would otherwise still observe an
    # ambient credentialed index URL or named-index credential env var.
    monkeypatch.setenv("UV_INDEX_URL", "https://untrusted.example/simple/")
    monkeypatch.setenv("UV_INDEX_GOVERNED_USERNAME", "svc-account")
    monkeypatch.setenv("UV_INDEX_GOVERNED_PASSWORD", "token-value")
    source_dir = tmp_path / "plugin"
    source_dir.mkdir()
    (source_dir / "pyproject.toml").write_text(
        '[build-system]\nrequires = []\n', encoding="utf-8"
    )
    build_envs: list[dict] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:2] == ["uv", "build"]:
            build_envs.append(kwargs.get("env") or {})
            out_dir = Path(cmd[cmd.index("-o") + 1])
            (out_dir / "plugin-1.0.0-py3-none-any.whl").write_bytes(b"")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if len(cmd) > 3 and cmd[3] == btl._MARKER_ENV_QUERY_SCRIPT:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"python_version": "3.12"}), stderr=""
            )
        if len(cmd) > 3 and cmd[3] == btl._BUILD_REQUIRES_CHECK_SCRIPT:
            # `requires = []` -- trivially satisfied, no need to exercise
            # the real comparison logic for this credential-stripping test.
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"ok": True}), stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.build_wheel(source_dir, tmp_path / "out", toolchain=_FAKE_TOOLCHAIN)

    assert len(build_envs) == 1
    build_env = build_envs[0]
    assert "UV_INDEX_URL" not in build_env
    assert "UV_INDEX_GOVERNED_USERNAME" not in build_env
    assert "UV_INDEX_GOVERNED_PASSWORD" not in build_env


def test_build_wheel_keeps_index_credentials_without_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Without a locked toolchain, this falls back to uv's normal ISOLATED
    # build, which genuinely needs index access to resolve build-system
    # requires -- credentials must NOT be stripped in that path.
    monkeypatch.setenv("UV_INDEX_GOVERNED_USERNAME", "svc-account")
    source_dir = tmp_path / "plugin"
    source_dir.mkdir()
    (source_dir / "pyproject.toml").write_text(
        '[build-system]\nrequires = []\n', encoding="utf-8"
    )
    seen_envs: list[dict] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_envs.append(kwargs.get("env") or {})
        if cmd[:2] == ["uv", "build"]:
            out_dir = Path(cmd[cmd.index("-o") + 1])
            (out_dir / "plugin-1.0.0-py3-none-any.whl").write_bytes(b"")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.build_wheel(source_dir, tmp_path / "out")

    assert seen_envs[0].get("UV_INDEX_GOVERNED_USERNAME") == "svc-account"


# --- round 21: Windows ACL hardening for the credential-bearing config ---


def test_restrict_file_to_owner_is_noop_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(btl.sys, "platform", "linux")
    calls: list[list[str]] = []
    monkeypatch.setattr(
        btl.subprocess, "run",
        lambda cmd, **kwargs: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0),  # noqa: ARG005
    )
    btl._restrict_file_to_owner(tmp_path / "secret.toml")
    assert calls == []


def test_restrict_file_to_owner_invokes_icacls_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(btl.sys, "platform", "win32")
    monkeypatch.setenv("USERDOMAIN", "REDMOND")
    monkeypatch.setenv("USERNAME", "svc")
    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_cmds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(btl.subprocess, "run", fake_run)
    target = tmp_path / "secret.toml"
    btl._restrict_file_to_owner(target)

    # Three icacls invocations: grant owner+SYSTEM, strip broad built-in
    # principals by well-known SID, then a final verify query.
    assert len(seen_cmds) == 3
    grant_cmd, remove_cmd, verify_cmd = seen_cmds
    assert grant_cmd[0] == "icacls"
    assert grant_cmd[1] == str(target)
    assert "/inheritance:r" in grant_cmd
    assert "REDMOND\\svc:F" in grant_cmd
    assert "SYSTEM:F" in grant_cmd
    assert remove_cmd[:3] == ["icacls", str(target), "/remove:g"]
    assert verify_cmd == ["icacls", str(target)]


def test_restrict_file_to_owner_fails_closed_when_icacls_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(btl.sys, "platform", "win32")
    monkeypatch.setenv("USERDOMAIN", "REDMOND")
    monkeypatch.setenv("USERNAME", "svc")
    monkeypatch.setattr(
        btl.subprocess, "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="denied"),  # noqa: ARG005
    )
    with pytest.raises(bpa.ArtifactBuildError):
        btl._restrict_file_to_owner(tmp_path / "secret.toml")


def test_restrict_file_to_owner_fails_closed_when_user_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(btl.sys, "platform", "win32")
    monkeypatch.delenv("USERNAME", raising=False)
    monkeypatch.delenv("USERDOMAIN", raising=False)
    with pytest.raises(bpa.ArtifactBuildError):
        btl._restrict_file_to_owner(tmp_path / "secret.toml")


def test_resolve_toolchain_lock_hardens_index_config_acl_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression (round 21): the sanitized temp index-config file must be
    # hardened via `_restrict_file_to_owner` BEFORE its secret-bearing
    # content is written -- `0o600` mode bits alone are not an owner-only
    # ACL on Windows.
    _assume_governed_feed_configured(monkeypatch)
    monkeypatch.setattr(btl.sys, "platform", "win32")
    monkeypatch.setenv("USERDOMAIN", "REDMOND")
    monkeypatch.setenv("USERNAME", "svc")
    venv_dir = tmp_path / "toolchain-venv"
    hardened_before_write: list[bool] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[:1] == ["icacls"] and str(cmd[1]).endswith(".index-config.toml"):
            config_path = Path(cmd[1])
            # The file must exist but still be EMPTY at hardening time --
            # content is written only after this call succeeds.
            hardened_before_write.append(config_path.read_text(encoding="utf-8") == "")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:1] == ["icacls"]:
            # The provenance-key file's OWN icacls hardening (round 22) --
            # not this test's concern; just let it succeed.
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.resolve_toolchain_lock(venv_dir)

    # Several icacls sub-invocations now touch the index-config file
    # (grant, strip-broad-principals, verify) -- all must see it EMPTY.
    assert hardened_before_write and all(hardened_before_write)


def test_resolve_toolchain_lock_strips_pythonpath_and_pythonhome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: an ambient PYTHONPATH/PYTHONHOME must never reach the
    # subprocesses that invoke a specific interpreter directly -- an
    # ambient module on PYTHONPATH could otherwise shadow the locked
    # setuptools/wheel/packaging, or PYTHONHOME could redirect the
    # interpreter's own standard-library resolution entirely.
    _assume_governed_feed_configured(monkeypatch)
    # Pinned to a non-Windows platform so this test's own call count stays
    # platform-independent -- `_restrict_file_to_owner` (round 21) only
    # shells out to `icacls` on win32; see the dedicated
    # `test_resolve_toolchain_lock_hardens_index_config_acl_on_windows`
    # for that behavior instead.
    monkeypatch.setattr(btl.sys, "platform", "linux")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "shadow-modules"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "other-home"))
    venv_dir = tmp_path / "toolchain-venv"
    seen_envs: list[dict] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_envs.append(kwargs.get("env") or {})
        if cmd[:2] == ["uv", "venv"]:
            staging_python = bpa._venv_python_path(Path(cmd[3]))
            staging_python.parent.mkdir(parents=True, exist_ok=True)
            staging_python.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:3] == ["uv", "pip", "install"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=_toolchain_query_stdout({"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"}),
            stderr="",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.resolve_toolchain_lock(venv_dir)

    assert len(seen_envs) == 3  # uv venv, uv pip install, version query
    for env in seen_envs:
        assert "PYTHONPATH" not in env
        assert "PYTHONHOME" not in env


def test_query_marker_environment_strips_pythonpath_and_pythonhome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "shadow-modules"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "other-home"))
    seen_env = {}

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_env.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"python_version": "3.12"}), stderr=""
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa._query_marker_environment(tmp_path / "python")

    assert "PYTHONPATH" not in seen_env
    assert "PYTHONHOME" not in seen_env


def test_build_wheel_strips_pythonpath_and_pythonhome_from_build_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: the final `uv build --no-build-isolation` call itself
    # inherited the caller's ambient PYTHONPATH/PYTHONHOME, letting an
    # ambient module shadow the locked setuptools even though the
    # manifest claims the locked venv was authoritative.
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "shadow-modules"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "other-home"))
    seen_envs: list[dict] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_envs.append(kwargs.get("env") or {})
        (_staging_dir_from_cmd(cmd) / "demo-1.0-py3-none-any.whl").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.build_wheel(tmp_path / "src", tmp_path / "dist")

    assert len(seen_envs) == 1
    assert "PYTHONPATH" not in seen_envs[0]
    assert "PYTHONHOME" not in seen_envs[0]


# --- build-system.requires enforcement against the locked toolchain -----


def _write_build_system_requires(source_dir: Path, requires: list[str]) -> None:
    source_dir.mkdir(parents=True, exist_ok=True)
    requires_toml = ", ".join(json.dumps(r) for r in requires)
    (source_dir / "pyproject.toml").write_text(
        f"[build-system]\nrequires = [{requires_toml}]\n", encoding="utf-8"
    )


def _run_build_requires_check_inline(payload_json: str) -> str:
    """Executes `_BUILD_REQUIRES_CHECK_SCRIPT` directly in THIS test
    process via `exec`, feeding it ``payload_json`` on a fake stdin and
    capturing what it prints -- the same real script
    `_assert_toolchain_satisfies_build_requires` now runs via subprocess
    through the locked toolchain's own interpreter (where `packaging` is
    guaranteed present), exercised here against the real script source
    without needing an actual toolchain venv."""
    import contextlib
    import io

    original_stdin = sys.stdin
    buf = io.StringIO()
    sys.stdin = io.StringIO(payload_json)
    try:
        with contextlib.redirect_stdout(buf):
            try:
                exec(
                    compile(
                        btl._BUILD_REQUIRES_CHECK_SCRIPT,
                        "<build-requires-check>",
                        "exec",
                    ),
                    {"__name__": "__main__"},
                )
            except SystemExit:
                pass
    finally:
        sys.stdin = original_stdin
    return buf.getvalue()


def _dispatch_toolchain_subprocess(
    cmd, kwargs, toolchain, *, marker_environment: dict | None = None
):
    """Shared `fake_run` dispatcher for `build_wheel(..., toolchain=...)`
    tests below: routes the two distinct subprocess calls
    `_assert_toolchain_satisfies_build_requires` (and the
    `ToolchainLock.marker_environment` property it always reads) invoke
    through the locked toolchain's own interpreter -- the marker-
    environment query and the build-requires check script itself (run for
    real via `_run_build_requires_check_inline`, so these tests exercise
    the actual comparison logic rather than a reimplementation of it)."""
    assert cmd[0] == str(toolchain.venv_python)
    assert cmd[1] == "-I"
    assert cmd[2] == "-c"
    script = cmd[3]
    if script == btl._MARKER_ENV_QUERY_SCRIPT:
        return subprocess.CompletedProcess(
            cmd, 0,
            stdout=json.dumps(
                marker_environment
                if marker_environment is not None
                else {"python_version": "3.12", "sys_platform": "win32"}
            ),
            stderr="",
        )
    assert script == btl._BUILD_REQUIRES_CHECK_SCRIPT
    return subprocess.CompletedProcess(
        cmd, 0, stdout=_run_build_requires_check_inline(kwargs["input"]), stderr=""
    )


def test_build_wheel_rejects_toolchain_below_declared_build_requires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    src_dir = tmp_path / "src"
    _write_build_system_requires(src_dir, ["setuptools>=90.0.0"])
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return _dispatch_toolchain_subprocess(cmd, kwargs, toolchain)

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_wheel(src_dir, tmp_path / "dist", toolchain=toolchain)


def test_build_wheel_rejects_direct_url_build_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a direct URL reference (`name @ https://...`) has an
    # EMPTY version specifier and would otherwise trivially "pass" against
    # any installed version, despite actually requiring a specific
    # alternate source this locked toolchain never installs from.
    src_dir = tmp_path / "src"
    _write_build_system_requires(
        src_dir, ["setuptools @ https://example.internal/setuptools-90.0.0-py3-none-any.whl"]
    )
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return _dispatch_toolchain_subprocess(cmd, kwargs, toolchain)

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError, match="direct URL"):
        bpa.build_wheel(src_dir, tmp_path / "dist", toolchain=toolchain)


def test_build_wheel_rejects_build_requirement_with_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: an extras clause (`name[extra]`) would otherwise "pass"
    # without the extra's own additional dependencies ever being
    # installed, since this locked toolchain only ever installs the bare
    # locked packages.
    src_dir = tmp_path / "src"
    _write_build_system_requires(src_dir, ["setuptools[extra_feature]>=60.0.0"])
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return _dispatch_toolchain_subprocess(cmd, kwargs, toolchain)

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError, match="extra"):
        bpa.build_wheel(src_dir, tmp_path / "dist", toolchain=toolchain)


def test_build_wheel_accepts_toolchain_meeting_declared_build_requires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    src_dir = tmp_path / "src"
    _write_build_system_requires(src_dir, ["setuptools>=60.0.0", "wheel"])
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[0] == str(toolchain.venv_python):
            return _dispatch_toolchain_subprocess(cmd, kwargs, toolchain)
        (_staging_dir_from_cmd(cmd) / "demo-1.0-py3-none-any.whl").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.build_wheel(src_dir, tmp_path / "dist", toolchain=toolchain)  # must not raise


def test_build_wheel_matches_build_requires_name_case_insensitively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: PEP 508 names are case-insensitive, but Requirement.name
    # preserves the source's own literal spelling -- a declared
    # "Setuptools>=60" must still match the locked lowercase "setuptools"
    # entry rather than being treated as "not locked at all".
    src_dir = tmp_path / "src"
    _write_build_system_requires(src_dir, ["Setuptools>=60.0.0", "Wheel"])
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[0] == str(toolchain.venv_python):
            return _dispatch_toolchain_subprocess(cmd, kwargs, toolchain)
        (_staging_dir_from_cmd(cmd) / "demo-1.0-py3-none-any.whl").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.build_wheel(src_dir, tmp_path / "dist", toolchain=toolchain)  # must not raise


def test_build_wheel_rejects_build_requires_for_packages_outside_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: an applicable requirement on a package this toolchain
    # does NOT lock at all (e.g. a project-specific build backend) must
    # fail closed, not be silently treated as satisfied -- with
    # --no-build-isolation, nothing installs it automatically.
    src_dir = tmp_path / "src"
    _write_build_system_requires(src_dir, ["some-other-backend>=999.0.0"])
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return _dispatch_toolchain_subprocess(cmd, kwargs, toolchain)

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_wheel(src_dir, tmp_path / "dist", toolchain=toolchain)


def test_build_wheel_ignores_build_requires_with_inapplicable_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # A constraint gated behind an environment marker that doesn't apply
    # here must be skipped, not enforced.
    src_dir = tmp_path / "src"
    _write_build_system_requires(
        src_dir, ['setuptools>=999.0.0; python_version < "3.0"']
    )
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[0] == str(toolchain.venv_python):
            # the marker-environment query against the locked interpreter
            # reports python_version "3.12", so the marker does not apply
            return _dispatch_toolchain_subprocess(cmd, kwargs, toolchain)
        (_staging_dir_from_cmd(cmd) / "demo-1.0-py3-none-any.whl").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.build_wheel(src_dir, tmp_path / "dist", toolchain=toolchain)  # must not raise


def test_build_wheel_evaluates_markers_against_locked_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: markers must evaluate against the LOCKED interpreter's
    # own environment (queried from toolchain.venv_python), never the
    # process running this tool -- simulate a locked interpreter reporting
    # python_version "2.7" so the marker DOES apply, and the (insufficient)
    # constraint is enforced.
    src_dir = tmp_path / "src"
    _write_build_system_requires(
        src_dir, ['setuptools>=999.0.0; python_version < "3.0"']
    )
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return _dispatch_toolchain_subprocess(
            cmd, kwargs, toolchain,
            marker_environment={"python_version": "2.7", "sys_platform": "win32"},
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_wheel(src_dir, tmp_path / "dist", toolchain=toolchain)


def test_toolchain_lock_marker_environment_is_queried_once_and_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    toolchain = bpa.ToolchainLock(tmp_path / "venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"})
    calls = {"n": 0}

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        calls["n"] += 1
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"python_version": "3.12"}), stderr=""
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    env1 = toolchain.marker_environment
    env2 = toolchain.marker_environment
    assert env1 == {"python_version": "3.12"}
    assert env1 is env2
    assert calls["n"] == 1


def test_query_marker_environment_nonzero_exit_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa._query_marker_environment(tmp_path / "python")


def test_query_marker_environment_malformed_json_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return subprocess.CompletedProcess(cmd, 0, stdout="not json", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa._query_marker_environment(tmp_path / "python")


def test_query_marker_environment_against_real_interpreter(
    monkeypatch: pytest.MonkeyPatch,
):
    # Regression: implementation_version must come from
    # sys.implementation.version (via the same format_full_version logic
    # packaging.markers uses), NOT platform.python_version() -- they
    # coincide on CPython today, but running the REAL query script end-to-
    # end against the actual interpreter proves it computes a well-formed
    # value via that code path rather than silently using the wrong one.
    # Needs the REAL subprocess.run, not this file's own autouse default
    # stub (which exists only to keep `_provenance_key`'s unrelated ACL
    # hardening out of routine tests).
    monkeypatch.setattr(gft.subprocess, "run", _REAL_SUBPROCESS_RUN)
    env = bpa._query_marker_environment(Path(sys.executable))
    expected = f"{sys.implementation.version.major}.{sys.implementation.version.minor}.{sys.implementation.version.micro}"
    if sys.implementation.version.releaselevel != "final":
        expected += sys.implementation.version.releaselevel[0] + str(sys.implementation.version.serial)
    assert env["implementation_version"] == expected
    assert env["implementation_name"] == sys.implementation.name
    assert env["sys_platform"] == sys.platform


def test_build_wheel_rejects_unparseable_build_requires_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    src_dir = tmp_path / "src"
    _write_build_system_requires(src_dir, ["not a valid requirement!!!"])
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        return _dispatch_toolchain_subprocess(cmd, kwargs, toolchain)

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError):
        bpa.build_wheel(src_dir, tmp_path / "dist", toolchain=toolchain)


def test_assert_toolchain_satisfies_build_requires_runs_check_inside_toolchain_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: the [build-system].requires verification must run
    # through the LOCKED toolchain's own interpreter (where `packaging` is
    # guaranteed present, since it is one of this toolchain's own locked
    # packages) rather than importing `packaging` in the calling process
    # -- a genuinely clean machine with just Python and `uv` installed
    # would not have `packaging` importable here at all. Verified by
    # confirming the actual work is delegated to a subprocess invoking
    # `toolchain.venv_python` (never computed in-process).
    src_dir = tmp_path / "src"
    _write_build_system_requires(src_dir, ["setuptools>=60.0.0"])
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python", {"setuptools": "84.1.0", "wheel": "0.44.0"}
    )
    seen_cmds: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        seen_cmds.append(cmd)
        return _dispatch_toolchain_subprocess(cmd, kwargs, toolchain)

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    btl._assert_toolchain_satisfies_build_requires(src_dir, toolchain)  # must not raise
    assert any(
        cmd[0] == str(toolchain.venv_python) and cmd[3] == btl._BUILD_REQUIRES_CHECK_SCRIPT
        for cmd in seen_cmds
    )


def test_assert_toolchain_satisfies_build_requires_fails_closed_when_toolchain_lacks_packaging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Regression: a toolchain venv genuinely missing `packaging` (e.g. an
    # on-disk venv published before this toolchain locked it) must fail
    # closed through the normal subprocess-failure path, with an
    # actionable message, not a bare, hard-to-diagnose traceback.
    src_dir = tmp_path / "src"
    _write_build_system_requires(src_dir, ["setuptools>=60.0.0"])
    toolchain = bpa.ToolchainLock(
        tmp_path / "toolchain-venv" / "python",
        {"setuptools": "84.1.0", "wheel": "0.44.0", "packaging": "24.0"},
    )

    def fake_run(cmd, **kwargs):  # noqa: ARG001
        if cmd[3] == btl._MARKER_ENV_QUERY_SCRIPT:
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps({"python_version": "3.12"}), stderr=""
            )
        return subprocess.CompletedProcess(
            cmd, 1, stdout="",
            stderr="ModuleNotFoundError: No module named 'packaging'",
        )

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    with pytest.raises(bpa.ArtifactBuildError, match="packaging"):
        btl._assert_toolchain_satisfies_build_requires(src_dir, toolchain)


def test_build_wheel_skips_build_requires_check_without_toolchain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Unaffected baseline: without a toolchain (the direct-callers / low-
    # level isolated-build path), no pyproject.toml is needed at all --
    # this check must only engage when a toolchain is actually given.
    def fake_run(cmd, **kwargs):  # noqa: ARG001
        (_staging_dir_from_cmd(cmd) / "demo-1.0-py3-none-any.whl").write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(bpa.subprocess, "run", fake_run)
    bpa.build_wheel(tmp_path / "src", tmp_path / "dist")  # must not raise


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
    parent_existed_at_call: list[bool] = []

    def fake_resolve_toolchain_lock(venv_dir: Path, *, python=None):  # noqa: ARG001
        resolved_dirs.append(venv_dir)
        # Regression: the path passed must itself NOT pre-exist (a real
        # `tempfile.TemporaryDirectory()` creates ITS OWN directory
        # immediately, but `resolve_toolchain_lock` publishes by renaming a
        # staging dir ONTO this exact path -- Windows `Path.rename()`
        # always fails onto an already-existing directory, even empty) --
        # only its PARENT (the real temp-dir root) should already exist.
        parent_existed_at_call.append(venv_dir.parent.is_dir())
        assert not venv_dir.exists()
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
    assert parent_existed_at_call == [True]
    assert not resolved_dirs[0].exists()  # the disposable toolchain dir is cleaned up
    assert manifest["build_toolchain"]["lock_id"] == _FAKE_TOOLCHAIN.lock_id
