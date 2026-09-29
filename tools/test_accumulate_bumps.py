"""Tests for tools/accumulate_bumps.py: the version-bump math, plus the
consume-changefiles -> write-three-files integration."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import accumulate_bumps as acc
import changefile

# --- pure version-bump math -------------------------------------------------

@pytest.mark.parametrize("current,bump,expected", [
    ("1.3.1", "patch", "1.3.2-dev1"),
    ("1.3.1-dev5", "patch", "1.3.2-dev1"),
    ("1.3.1-dev5", "dev", "1.3.1-dev6"),
    ("1.3.1", "dev", "1.3.1-dev1"),
    ("1.3.1-dev5", "minor", "1.4.0-dev1"),
    ("1.3.1-dev5", "major", "2.0.0-dev1"),
    ("0.1.0-dev20", "dev", "0.1.0-dev21"),
])
def test_bump_version(current, bump, expected):
    assert acc.bump_version(current, bump) == expected


def test_bump_version_rejects_unparseable():
    with pytest.raises(acc.VersionError):
        acc.bump_version("not-a-version", "patch")


def test_bump_version_rejects_unknown_type():
    with pytest.raises(acc.VersionError):
        acc.bump_version("1.0.0", "gigantic")


@pytest.mark.parametrize("types,expected", [
    (["patch"], "patch"),
    (["dev", "patch"], "patch"),
    (["patch", "minor", "dev"], "minor"),
    (["major", "minor", "patch", "dev"], "major"),
    (["dev", "dev"], "dev"),
])
def test_highest_bump(types, expected):
    assert acc.highest_bump(types) == expected


# --- integration: changefiles -> computed + applied versions ---------------

def _plugin(root: Path, name: str, version: str, *, with_pyproject: bool = True) -> None:
    d = root / "plugins" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "plugin.json").write_text(
        json.dumps({"name": name, "version": version}, indent=2) + "\n", encoding="utf-8"
    )
    if with_pyproject:
        (d / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8"
        )


def _marketplace(root: Path, entries: dict[str, str], *, metadata_version: str) -> Path:
    mkt = root / ".github" / "plugin"
    mkt.mkdir(parents=True, exist_ok=True)
    path = mkt / "marketplace.json"
    data = {
        "name": "copilot-extensions",
        "metadata": {"description": "x", "version": metadata_version},
        "plugins": [
            {"name": name, "description": "d", "version": version, "source": f"plugins/{name}"}
            for name, version in entries.items()
        ],
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    monkeypatch.setattr(acc, "REPO", root)
    monkeypatch.setattr(acc, "PLUGINS_DIR", root / "plugins")
    monkeypatch.setattr(acc, "MARKETPLACE", root / ".github" / "plugin" / "marketplace.json")
    monkeypatch.setattr(changefile, "CHANGEFILES_DIR", root / ".changefiles")
    return root


def test_pending_bumps_groups_by_plugin(isolated: Path):
    changefile.write_changefile([{"plugin": "agent-worktrees", "type": "patch"}], "a")
    changefile.write_changefile([
        {"plugin": "agent-worktrees", "type": "dev"},
        {"plugin": "agent-bridge", "type": "minor"},
    ], "b")
    grouped = acc.pending_bumps()
    assert grouped == {
        "agent-worktrees": ["patch", "dev"],
        "agent-bridge": ["minor"],
    }


def test_compute_and_apply_writes_all_three_files(isolated: Path):
    _plugin(isolated, "agent-worktrees", "1.5.5-dev253")
    _marketplace(isolated, {"agent-worktrees": "1.5.5-dev253"}, metadata_version="1.7.7-dev214")
    changefile.write_changefile([{"plugin": "agent-worktrees", "type": "patch"}], "Fix X")

    grouped = acc.pending_bumps()
    result = acc.compute(grouped)
    assert result == {"agent-worktrees": ("1.5.5-dev253", "1.5.6-dev1")}

    applied = acc.apply(result)
    assert applied == ["agent-worktrees"]

    pj = json.loads((isolated / "plugins/agent-worktrees/plugin.json").read_text())
    assert pj["version"] == "1.5.6-dev1"
    pp = (isolated / "plugins/agent-worktrees/pyproject.toml").read_text()
    assert 'version = "1.5.6-dev1"' in pp
    mkt = json.loads((isolated / ".github/plugin/marketplace.json").read_text())
    entry = next(p for p in mkt["plugins"] if p["name"] == "agent-worktrees")
    assert entry["version"] == "1.5.6-dev1"
    # agent-worktrees also bumps the catalog metadata.version.
    assert mkt["metadata"]["version"] == "1.5.6-dev1"


def test_apply_does_not_bump_metadata_for_other_plugins(isolated: Path):
    _plugin(isolated, "agent-bridge", "1.0.0-dev1")
    _marketplace(isolated, {"agent-bridge": "1.0.0-dev1"}, metadata_version="1.7.7-dev214")
    changefile.write_changefile([{"plugin": "agent-bridge", "type": "dev"}], "Fix Y")

    result = acc.compute(acc.pending_bumps())
    acc.apply(result)

    mkt = json.loads((isolated / ".github/plugin/marketplace.json").read_text())
    assert mkt["metadata"]["version"] == "1.7.7-dev214"  # untouched


def test_multiple_changefiles_same_plugin_pick_highest_bump(isolated: Path):
    _plugin(isolated, "agent-bridge", "2.1.0-dev9")
    _marketplace(isolated, {"agent-bridge": "2.1.0-dev9"}, metadata_version="1.0.0")
    changefile.write_changefile([{"plugin": "agent-bridge", "type": "dev"}], "small fix")
    changefile.write_changefile([{"plugin": "agent-bridge", "type": "minor"}], "new feature")

    result = acc.compute(acc.pending_bumps())
    assert result == {"agent-bridge": ("2.1.0-dev9", "2.2.0-dev1")}


def test_full_cli_apply_consumes_changefiles(isolated: Path):
    _plugin(isolated, "agent-bridge", "1.0.0-dev1", with_pyproject=False)
    _marketplace(isolated, {"agent-bridge": "1.0.0-dev1"}, metadata_version="1.0.0")
    changefile.write_changefile([{"plugin": "agent-bridge", "type": "patch"}], "Fix Z")

    code = acc.main(["--apply"])
    assert code == 0
    assert changefile.read_changefiles() == []
    pj = json.loads((isolated / "plugins/agent-bridge/plugin.json").read_text())
    assert pj["version"] == "1.0.1-dev1"


def test_skips_plugin_with_no_plugin_json(isolated: Path, capsys):
    changefile.write_changefile([{"plugin": "ghost-plugin", "type": "patch"}], "typo'd plugin")
    result = acc.compute(acc.pending_bumps())
    assert result == {}
    assert "no plugin.json" in capsys.readouterr().err


# --- standalone (out-of-plugin) consumers, e.g. worktree-manager -----------

def _standalone(root: Path, name: str, version: str) -> None:
    """A top-level, out-of-plugin consumer tree (e.g. ``worktree-manager``):
    its own ``pyproject.toml`` ``[project].version``, no ``plugin.json`` at
    all -- distinct from `_plugin()`'s `plugins/<name>` shape."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\n', encoding="utf-8"
    )


def test_is_standalone_consumer_detects_out_of_plugin_tree(isolated: Path):
    _plugin(isolated, "agent-bridge", "1.0.0")
    _standalone(isolated, "worktree-manager", "0.1.0-dev95")
    assert acc.is_standalone_consumer("agent-bridge") is False
    assert acc.is_standalone_consumer("worktree-manager") is True


def test_is_standalone_consumer_rejects_unrecognized_names(isolated: Path):
    """`is_standalone_consumer()` must restrict to the recognized
    out-of-plugin registry, never "any name without a plugin.json" --
    otherwise a malformed/typo'd changefile name like `libs/zdd` or
    `plugins/agent-bridge` would resolve through `_consumer_root()` to
    some unintended real `pyproject.toml` elsewhere in the tree and get
    bumped as if it were a genuine standalone consumer (PR #4514 review).
    A directory that merely happens to exist and lack a plugin.json (but
    isn't the recognized `worktree-manager` name) must be rejected too."""
    (isolated / "libs" / "zdd").mkdir(parents=True)
    (isolated / "libs" / "zdd" / "pyproject.toml").write_text(
        '[project]\nname = "zdd"\nversion = "1.0.0"\n', encoding="utf-8",
    )
    assert acc.is_standalone_consumer("libs/zdd") is False
    assert acc.is_standalone_consumer("plugins/agent-bridge") is False
    assert acc.is_standalone_consumer("some-random-unrecognized-name") is False


def test_compute_and_apply_bumps_a_standalone_consumer(isolated: Path):
    _standalone(isolated, "worktree-manager", "0.1.0-dev95")
    changefile.write_changefile([{"plugin": "worktree-manager", "type": "dev"}], "convert a lib")

    result = acc.compute(acc.pending_bumps())
    assert result == {"worktree-manager": ("0.1.0-dev95", "0.1.0-dev96")}

    applied = acc.apply(result)
    assert applied == ["worktree-manager"]
    pp = (isolated / "worktree-manager/pyproject.toml").read_text()
    assert 'version = "0.1.0-dev96"' in pp


def test_standalone_write_only_touches_project_table_version(isolated: Path):
    """A standalone consumer's `pyproject.toml` may legitimately carry an
    earlier, unrelated table with its OWN `version` key (e.g. a build
    backend's own pinned version) before `[project]` -- the write must be
    scoped to `[project]`'s own span, never the first `version = ` line
    anywhere in the file, or that earlier table's value gets silently
    corrupted while the real package version is left untouched (PR #4514
    review)."""
    d = isolated / "worktree-manager"
    d.mkdir(parents=True)
    (d / "pyproject.toml").write_text(
        '[tool.example]\nversion = "9.9.9"\n\n'
        '[project]\nname = "worktree-manager"\nversion = "0.1.0-dev95"\n',
        encoding="utf-8",
    )

    acc.apply({"worktree-manager": ("0.1.0-dev95", "0.1.0-dev96")})

    text = (d / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "9.9.9"' in text  # untouched, unrelated table
    assert 'version = "0.1.0-dev96"' in text
    assert acc.read_pyproject_project_version(d) == "0.1.0-dev96"


def test_standalone_write_supports_single_quoted_toml_version(isolated: Path):
    """TOML allows either `"..."` or `'...'` string quoting -- both equally
    valid and both already accepted by `read_pyproject_project_version()`'s
    genuine `tomllib` parsing. A write-side regex that only matched double
    quotes would compute a real bump for a single-quoted manifest and then
    silently fail to apply it (`_write_project_version()` returning
    `False`), letting a changefile-consuming promotion ship the OLD
    version with no error at all (PR #4514 review)."""
    d = isolated / "worktree-manager"
    d.mkdir(parents=True)
    (d / "pyproject.toml").write_text(
        "[project]\nname = 'worktree-manager'\nversion = '0.1.0-dev95'\n",
        encoding="utf-8",
    )

    applied = acc.apply({"worktree-manager": ("0.1.0-dev95", "0.1.0-dev96")})

    assert applied == ["worktree-manager"]
    text = (d / "pyproject.toml").read_text(encoding="utf-8")
    assert "version = '0.1.0-dev96'" in text  # quote style preserved
    assert acc.read_pyproject_project_version(d) == "0.1.0-dev96"


def test_standalone_write_accepts_inline_comment_on_project_header(isolated: Path):
    """`tomllib` accepts a valid table header with a trailing inline
    comment (e.g. `[project] # package metadata`), so
    `read_pyproject_project_version()` computes a real bump for such a
    manifest -- but the write-side header regex previously required an
    EXACT `[project]` line with nothing else, silently returning `False`
    and leaving the old version in place for this equally valid form (PR
    #4514 review)."""
    d = isolated / "worktree-manager"
    d.mkdir(parents=True)
    (d / "pyproject.toml").write_text(
        '[project]   # package metadata\n'
        'name = "worktree-manager"\nversion = "0.1.0-dev95"\n',
        encoding="utf-8",
    )

    applied = acc.apply({"worktree-manager": ("0.1.0-dev95", "0.1.0-dev96")})

    assert applied == ["worktree-manager"]
    assert acc.read_pyproject_project_version(d) == "0.1.0-dev96"


def test_standalone_write_accepts_indented_project_header(isolated: Path):
    """`tomllib` accepts leading whitespace before a table header (e.g.
    `  [project] # metadata`), so `read_pyproject_project_version()`
    computes a real bump for such a manifest -- but the write-side header
    regex previously required `[` in column 1, silently returning `False`
    and leaving the old version in place for this equally valid,
    indented form (PR #4514 review)."""
    d = isolated / "worktree-manager"
    d.mkdir(parents=True)
    (d / "pyproject.toml").write_text(
        '  [project] # metadata\n'
        'name = "worktree-manager"\nversion = "0.1.0-dev95"\n',
        encoding="utf-8",
    )

    applied = acc.apply({"worktree-manager": ("0.1.0-dev95", "0.1.0-dev96")})

    assert applied == ["worktree-manager"]
    assert acc.read_pyproject_project_version(d) == "0.1.0-dev96"


def test_standalone_write_accepts_quoted_project_table_name(isolated: Path):
    """TOML allows a table name to be a bare key (`[project]`) or a quoted
    string (`["project"]`/`['project']`) -- all equally valid and all
    already accepted by `read_pyproject_project_version()`'s genuine
    `tomllib` parsing. A write-side header regex that only matched the
    bare-key spelling would compute a real bump for a quoted-table
    manifest and then silently fail to apply it (PR #4514 review)."""
    d = isolated / "worktree-manager"
    d.mkdir(parents=True)
    (d / "pyproject.toml").write_text(
        '["project"]\nname = "worktree-manager"\nversion = "0.1.0-dev95"\n',
        encoding="utf-8",
    )

    applied = acc.apply({"worktree-manager": ("0.1.0-dev95", "0.1.0-dev96")})

    assert applied == ["worktree-manager"]
    assert acc.read_pyproject_project_version(d) == "0.1.0-dev96"


def test_apply_rewrites_standalone_consumer_source_fallback(isolated: Path):
    _standalone(isolated, "worktree-manager", "0.1.0-dev95")
    init = isolated / "worktree-manager/src/worktree_manager/__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text('"""pkg."""\n__version__ = "0.1.0-dev95"\n', encoding="utf-8")

    acc.apply({"worktree-manager": ("0.1.0-dev95", "0.1.0-dev96")})

    assert '__version__ = "0.1.0-dev96"' in init.read_text(encoding="utf-8")


def test_from_diff_bumps_a_standalone_consumer_of_a_changed_lib(diff_repo, monkeypatch):
    root, git = diff_repo
    # Land worktree-manager on `main` too (not just the feature branch) --
    # otherwise `_version_at(base, ...)` sees a brand-new file with no base
    # version, and `_next_after_base` correctly treats that as "new on this
    # branch, no forced bump" rather than the "existing consumer whose
    # vendored lib changed" scenario this test actually exercises.
    git("checkout", "-q", "main")
    _standalone(root, "worktree-manager", "0.1.0-dev95")
    git("add", "-A")
    git("commit", "-q", "-m", "add worktree-manager")
    git("checkout", "-q", "-B", "feature", "main")

    # Stub the guard's consumer map directly rather than depending on its
    # own filesystem real-copy scan resolving against this isolated repo --
    # this test's own contract is "compute_from_diff correctly bumps
    # whichever standalone consumer the guard names", not a re-test of the
    # guard's own scan (that lives in test_check_version_bump.py).
    guard = acc._version_bump_guard()
    monkeypatch.setattr(guard, "_vendored_consumers", lambda: {"shared-lib": ["worktree-manager"]})
    monkeypatch.setattr(acc, "_changed_libs", lambda changed: {"shared-lib"})

    plugins, _libs = acc.compute_from_diff("main")
    assert plugins.get("worktree-manager") == ("0.1.0-dev95", "0.1.0-dev96")


# --- source fallbacks + --from-diff -----------------------------------------

def test_apply_rewrites_literal_version_fallbacks(isolated: Path):
    _plugin(isolated, "agent-codespaces", "0.4.0-dev7")
    _marketplace(isolated, {"agent-codespaces": "0.4.0-dev7"}, metadata_version="9.9.9")
    init = isolated / "plugins/agent-codespaces/src/agent_codespaces/__init__.py"
    init.parent.mkdir(parents=True)
    init.write_text('"""pkg."""\n__version__ = "0.4.0-dev7"\nOTHER = "0.4.0-dev7"\n', encoding="utf-8")
    acc.apply({"agent-codespaces": ("0.4.0-dev7", "0.4.0-dev8")})
    text = init.read_text(encoding="utf-8")
    assert '__version__ = "0.4.0-dev8"' in text
    assert 'OTHER = "0.4.0-dev7"' in text  # only monitored fallbacks


@pytest.mark.parametrize("current, base, expected", [
    ("0.1.0-dev5", "0.1.0-dev4", None),          # already ahead of the base
    ("0.1.0-dev4", "0.1.0-dev4", "0.1.0-dev5"),  # base caught up (e.g. after a rebase)
    ("0.1.0-dev4", "0.1.0-dev6", "0.1.0-dev7"),  # base moved past us
    ("0.1.0-dev1", None, None),                  # new on this branch
])
def test_next_after_base(current, base, expected):
    assert acc._next_after_base(current, base) == expected


def _git_repo(root: Path) -> None:
    import subprocess

    def git(*a):
        subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "feature")
    return git


def _lib(root: Path, plugin: str, lib: str, version: str, body: str = "x = 1\n") -> None:
    d = root / "plugins" / plugin / "libs" / lib
    (d / "src" / lib.replace("-", "_")).mkdir(parents=True, exist_ok=True)
    (d / "pyproject.toml").write_text(f'[project]\nname = "{lib}"\nversion = "{version}"\n', encoding="utf-8")
    (d / "src" / lib.replace("-", "_") / "__init__.py").write_text(body, encoding="utf-8")


@pytest.fixture()
def diff_repo(isolated: Path, monkeypatch: pytest.MonkeyPatch):
    for name, version in (("agent-a", "0.1.0-dev3"), ("agent-b", "0.2.0-dev9"), ("agent-c", "1.0.0-dev1")):
        _plugin(isolated, name, version)
    _marketplace(isolated, {"agent-a": "0.1.0-dev3", "agent-b": "0.2.0-dev9", "agent-c": "1.0.0-dev1"},
                 metadata_version="9.9.9")
    _lib(isolated, "agent-a", "shared-lib", "0.1.0-dev2")
    _lib(isolated, "agent-b", "shared-lib", "0.1.0-dev2")
    git = _git_repo(isolated)
    guard = acc._version_bump_guard()
    monkeypatch.setattr(guard, "PLUGINS_DIR", isolated / "plugins")
    monkeypatch.setattr(acc, "_version_bump_guard", lambda: guard)
    monkeypatch.setattr(acc, "REPO", isolated)
    return isolated, git


def test_from_diff_bumps_every_consumer_of_a_changed_lib_and_the_lib(diff_repo):
    root, _git = diff_repo
    for plugin in ("agent-a", "agent-b"):  # the lib change, synced to both copies
        (root / f"plugins/{plugin}/libs/shared-lib/src/shared_lib/__init__.py").write_text("x = 2\n")
    plugins, libs = acc.compute_from_diff("main")
    assert plugins == {"agent-a": ("0.1.0-dev3", "0.1.0-dev4"), "agent-b": ("0.2.0-dev9", "0.2.0-dev10")}
    assert {p.parent.parent.parent.name: v for p, v in libs.items()} == {
        "agent-a": ("0.1.0-dev2", "0.1.0-dev3"), "agent-b": ("0.1.0-dev2", "0.1.0-dev3"),
    }


def test_from_diff_bumps_a_standalone_consumers_own_vendored_lib_copy(diff_repo):
    """`lib_bumps_from_diff()` only ever globbed `plugins/*/libs/<lib>/`,
    so a recognized standalone consumer's own top-level `libs/<lib>/` real
    copy (mirroring `worktree-manager/libs/zdd`) was left stale by the
    mechanical `--from-diff` shortcut -- `check-vendored-libs-sync.py`
    already includes this shape in its own version-agreement check, so the
    stale copy then fails THAT check even though `--from-diff --apply`
    reported success (PR #4514 review)."""
    root, git = diff_repo
    # Land worktree-manager (with its own real shared-lib copy) on `main`
    # too, same reasoning as the sibling standalone-consumer test above --
    # otherwise it's "new on this branch", exempt from any bump obligation.
    git("checkout", "-q", "main")
    _standalone(root, "worktree-manager", "0.5.0-dev1")
    (root / "worktree-manager/libs/shared-lib/src/shared_lib").mkdir(parents=True)
    (root / "worktree-manager/libs/shared-lib/pyproject.toml").write_text(
        '[project]\nname = "shared-lib"\nversion = "0.1.0-dev2"\n', encoding="utf-8",
    )
    (root / "worktree-manager/libs/shared-lib/src/shared_lib/__init__.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "add worktree-manager with a real shared-lib copy")
    git("checkout", "-q", "-B", "feature", "main")

    for plugin in ("agent-a", "agent-b"):
        (root / f"plugins/{plugin}/libs/shared-lib/src/shared_lib/__init__.py").write_text("x = 2\n")
    (root / "worktree-manager/libs/shared-lib/src/shared_lib/__init__.py").write_text("x = 2\n")

    _plugins, libs = acc.compute_from_diff("main")
    wtm_copy = root / "worktree-manager/libs/shared-lib/pyproject.toml"
    assert libs.get(wtm_copy) == ("0.1.0-dev2", "0.1.0-dev3")


def test_from_diff_bumps_a_standalone_only_lib_edit_with_no_plugin_copy_touched(diff_repo):
    """Same as the sibling test above, but the shared-lib change is
    confined ENTIRELY to `worktree-manager`'s own copy -- no plugin copy
    or top-level canonical copy is also touched. `_changed_libs()`
    previously only recognized `plugins/*/libs/<lib>/src` and top-level
    `libs/<lib>/src` paths, so this diff alone produced an empty
    `_changed_libs()` result and `lib_bumps_from_diff()` never even got a
    lib name to look the standalone copy up under (PR #4514 review)."""
    root, git = diff_repo
    git("checkout", "-q", "main")
    _standalone(root, "worktree-manager", "0.5.0-dev1")
    (root / "worktree-manager/libs/shared-lib/src/shared_lib").mkdir(parents=True)
    (root / "worktree-manager/libs/shared-lib/pyproject.toml").write_text(
        '[project]\nname = "shared-lib"\nversion = "0.1.0-dev2"\n', encoding="utf-8",
    )
    (root / "worktree-manager/libs/shared-lib/src/shared_lib/__init__.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "add worktree-manager with a real shared-lib copy")
    git("checkout", "-q", "-B", "feature", "main")

    (root / "worktree-manager/libs/shared-lib/src/shared_lib/__init__.py").write_text("x = 2\n")

    _plugins, libs = acc.compute_from_diff("main")
    wtm_copy = root / "worktree-manager/libs/shared-lib/pyproject.toml"
    assert libs.get(wtm_copy) == ("0.1.0-dev2", "0.1.0-dev3")


def test_from_diff_bumps_the_canonical_lib_copy_alongside_real_copies(diff_repo):
    """The top-level CANONICAL `libs/<lib>/pyproject.toml` (distinct from
    any plugin's own vendored copy) is what gets materialized into every
    pointer-only consumer at promotion time -- for a lib with a mix of
    real copies and pointer-only consumers, leaving canonical out of the
    `--from-diff` shortcut means promotion ships its OLD, unbumped
    content into every pointer consumer, creating version skew on `main`
    even though every real copy bumped correctly (PR #4514 review)."""
    root, git = diff_repo
    # Land the canonical tree on `main` too -- otherwise it's "new on this
    # branch", exempt from any bump obligation (same reasoning as the
    # standalone-consumer tests above).
    git("checkout", "-q", "main")
    (root / "libs/shared-lib/src/shared_lib").mkdir(parents=True)
    (root / "libs/shared-lib/pyproject.toml").write_text(
        '[project]\nname = "shared-lib"\nversion = "0.1.0-dev2"\n', encoding="utf-8",
    )
    (root / "libs/shared-lib/src/shared_lib/__init__.py").write_text("x = 1\n")
    git("add", "-A")
    git("commit", "-q", "-m", "add canonical shared-lib tree")
    git("checkout", "-q", "-B", "feature", "main")

    (root / "plugins/agent-a/libs/shared-lib/src/shared_lib/__init__.py").write_text("x = 2\n")

    _plugins, libs = acc.compute_from_diff("main")
    canonical_copy = root / "libs/shared-lib/pyproject.toml"
    assert libs.get(canonical_copy) == ("0.1.0-dev2", "0.1.0-dev3")


def test_from_diff_charges_all_copies_even_if_only_one_was_edited(diff_repo):
    root, _git = diff_repo
    (root / "plugins/agent-a/libs/shared-lib/src/shared_lib/__init__.py").write_text("x = 2\n")
    plugins, _libs = acc.compute_from_diff("main")
    assert set(plugins) == {"agent-a", "agent-b"}  # untouched plugin c is not charged


def test_from_diff_apply_is_idempotent_and_follows_a_moved_base(diff_repo):
    root, git = diff_repo
    (root / "plugins/agent-c/extra.md").write_text("change\n")
    assert acc.main(["--from-diff", "main", "--apply"]) == 0
    assert json.loads((root / "plugins/agent-c/plugin.json").read_text())["version"] == "1.0.0-dev2"
    assert acc.compute_from_diff("main") == ({}, {})  # re-run: already ahead, nothing to do
    # main meanwhile consumes 1.0.0-dev2 in another PR; the branch must move past it
    git("add", "-A"); git("commit", "-q", "-m", "feature")
    git("checkout", "-q", "main")
    _plugin(root, "agent-c", "1.0.0-dev2")
    git("add", "-A"); git("commit", "-q", "-m", "other PR")
    git("checkout", "-q", "feature")
    plugins, _ = acc.compute_from_diff("main")
    assert plugins == {"agent-c": ("1.0.0-dev2", "1.0.0-dev3")}
