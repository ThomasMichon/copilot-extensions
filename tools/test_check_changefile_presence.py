"""Regression tests for the changefile-presence guard (the changefile-based
replacement for check-version-bump.py's manual bump requirement).

Drives the real ``tools/check-changefile-presence.py`` as a subprocess inside
a throwaway git repo (mirroring ``test_check_version_bump.py``), copying its
real dependencies (``check-version-bump.py`` for plugin-diff detection plus
ITS OWN two imports, and ``changefile.py`` for reading pending changefiles)
alongside it.

Run:  python -m pytest tools/test_check_changefile_presence.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS_DIR = Path(__file__).resolve().parent
SCRIPT = TOOLS_DIR / "check-changefile-presence.py"
# check-version-bump.py itself imports uv_editable_ref and installer_engine_ref
# -- omitting either produced a silent ModuleNotFoundError that failed EVERY
# test in this file. The changed-path-gated vendored-library CI step now runs
# this suite alongside the synchronizer and version-bump regressions.
DEP_SCRIPTS = ["check-version-bump.py", "changefile.py", "uv_editable_ref.py",
               "installer_engine_ref.py", "standalone_consumers.py",
               "vendored_lib_inventory.py", "launch_wrapper_assets_ref.py"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _plugin(repo: Path, name: str, version: str = "1.0.0") -> None:
    _write(repo, f"plugins/{name}/plugin.json",
           json.dumps({"name": name, "version": version}) + "\n")
    _write(repo, f"plugins/{name}/src/{name.replace('-', '_')}/__init__.py", "x = 1\n")


def _run(repo: Path, *extra: str, guard: str = SCRIPT.name) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / guard), *extra],
        cwd=repo, capture_output=True, text=True, check=False,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "tools").mkdir(parents=True)
    (r / "tools" / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    for dep in DEP_SCRIPTS:
        (r / "tools" / dep).write_bytes((TOOLS_DIR / dep).read_bytes())

    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    _git(r, "checkout", "-q", "-b", "dev")

    _plugin(r, "alpha")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(r, "update-ref", "refs/remotes/origin/main", base_sha)
    # Also simulate `origin/dev` at the same point -- this tool's own CLI
    # default base is `origin/dev` (this repo's real contribution trunk),
    # so a bare `_run(repo)` with no explicit `--base` needs this ref to
    # resolve. A SYMBOLIC ref (not a plain `update-ref`) means `origin/dev`
    # always tracks wherever `origin/main` currently points, in case a
    # future test advances it mid-run.
    _git(r, "symbolic-ref", "refs/remotes/origin/dev", "refs/remotes/origin/main")
    return r


def test_no_changes_passes(repo: Path):
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_touched_plugin_without_changefile_fails(repo: Path):
    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature, no changefile")
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "alpha" in result.stderr
    assert "no pending changefile" in result.stderr


def test_base_sharing_no_merge_base_degrades_to_soft_skip(repo: Path):
    """A `base` that RESOLVES but shares no common ancestor with HEAD at
    all (the confirmed fallout of a deliberate `main` history rewrite --
    see docs/pipelines.md's "If main's history is force-rewritten") must
    degrade to a soft no-op (no plugin misreported as touched), never
    silently diff raw `base` directly and misreport every plugin that
    merely differs between `base`'s snapshot and HEAD as missing a
    changefile."""
    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature, no changefile")

    _git(repo, "checkout", "-q", "--orphan", "rewritten-main")
    _write(repo, "unrelated.txt", "rewritten history\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated root (simulates a rewritten main)")
    rewritten_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", rewritten_sha)
    _git(repo, "checkout", "-q", "dev")

    result = _run(repo, "--base", "origin/main")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "alpha" not in result.stdout + result.stderr
    assert "shares no common history" in result.stdout + result.stderr


def test_touched_plugin_with_changefile_passes(repo: Path):
    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _write(repo, ".changefiles/fix.json",
           json.dumps({"comment": "fix", "changes": [{"plugin": "alpha", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature + changefile")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_changefile_for_a_different_plugin_does_not_satisfy(repo: Path):
    _plugin(repo, "beta")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add beta")
    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _write(repo, ".changefiles/fix.json",
           json.dumps({"comment": "fix", "changes": [{"plugin": "beta", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature, changefile names beta instead")
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "alpha" in result.stderr


def test_unrelated_pending_changefile_does_not_satisfy(repo: Path):
    """The regression this test exists for (PR #4942 review): `dev` is a
    rolling pre-release branch, so some OTHER, already-merged-but-not-yet-
    promoted PR routinely leaves its own still-pending changefile for a
    plugin THIS PR also happens to touch. That coincidence must not count --
    only a changefile THIS diff itself adds may satisfy the requirement, or
    a later promotion that consumes the unrelated changefile first would
    ship this PR's content with no bump at all."""
    _write(repo, ".changefiles/unrelated-already-pending.json",
           json.dumps({"comment": "unrelated", "changes": [{"plugin": "alpha", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated PR already merged, still pending promotion")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)

    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature, no changefile of its own")
    result = _run(repo)
    assert result.returncode == 1, (
        "alpha must still be charged even though an unrelated, already-"
        "pending changefile from a different PR already names it: "
        + result.stdout + result.stderr
    )
    assert "alpha" in result.stderr


def test_nested_added_changefile_does_not_satisfy_via_basename_collision(repo: Path):
    """The regression this test exists for (PR #4954 review): a changefile
    added at a NESTED path (e.g. ``.changefiles/archive/pending.json``) must
    not satisfy the requirement by basename alone just because an existing,
    unrelated TOP-LEVEL file of the same name (``.changefiles/pending.json``)
    already happens to name the SAME plugin this diff touches --
    ``changefile.read_changefiles()`` only ever globs ``.changefiles/*.json``
    non-recursively, so the nested file this PR adds is never actually
    consumed at all. The existing top-level file must name `alpha` (not an
    unrelated plugin) for this to actually exercise the basename collision:
    naming a different plugin would make `alpha` still get flagged missing
    for an unrelated reason, passing even without the top-level-path
    restriction this test exists to prove (PR #4954 review)."""
    _write(repo, ".changefiles/pending.json",
           json.dumps({"comment": "unrelated top-level file, same basename as the nested one below",
                       "changes": [{"plugin": "alpha", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unrelated top-level pending.json, already merged")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)

    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _write(repo, ".changefiles/archive/pending.json",
           json.dumps({"comment": "archived, not consumable",
                       "changes": [{"plugin": "alpha", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature, changefile only added at a nested path")
    result = _run(repo)
    assert result.returncode == 1, (
        "alpha must still be charged -- the added changefile is nested, not "
        "top-level, so read_changefiles() never actually sees it: "
        + result.stdout + result.stderr
    )
    assert "alpha" in result.stderr


def test_untouched_plugin_is_not_charged(repo: Path):
    _plugin(repo, "beta")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add beta, untouched afterward")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)

    _write(repo, "plugins/alpha/src/alpha/feature.py", "def f():\n    return 1\n")
    _write(repo, ".changefiles/fix.json",
           json.dumps({"comment": "fix", "changes": [{"plugin": "alpha", "type": "patch"}]}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alpha feature + changefile")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "beta" not in result.stdout + result.stderr


def _nested_library_base(repo: Path, *, pending: dict | None = None) -> None:
    _write(repo, "plugins/alpha/pyproject.toml",
           '[tool.uv.sources]\nparent = { path = "../../libs/parent", editable = true }\n')
    _write(repo, "libs/parent/pyproject.toml",
           '[tool.uv.sources]\nleaf = { path = "../leaf", editable = true }\n')
    _write(repo, "libs/leaf/pyproject.toml", '[project]\nname = "leaf"\nversion = "1.0"\n')
    if pending is not None:
        _write(repo, ".changefiles/pending.json", json.dumps(pending))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "nested library baseline")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


def _intent(plugin: str, kind: str = "patch") -> dict:
    return {"comment": "release", "changes": [{"plugin": plugin, "type": kind}]}


@pytest.mark.parametrize(("library_name", "consumer_name"), [
    ("library.json", "consumer.json"),
    ("libr\u00e1ry.json", "consumer.json"),
    ("library.json", "consum\u00e9r.json"),
])
def test_library_changefile_only_requires_nested_consumer_intent(
    repo: Path, library_name: str, consumer_name: str,
):
    _git(repo, "config", "core.quotePath", "true")
    _nested_library_base(repo, pending=_intent("alpha"))
    _write(repo, f".changefiles/{library_name}", json.dumps(_intent("libs/leaf")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "library-only bump intent")
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "alpha" in result.stderr

    _write(repo, f".changefiles/{consumer_name}", json.dumps(_intent("alpha")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cover the nested consumer")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("edit", [
    "unchanged", "comment", "removed", "removed-entry", "type", "added",
])
def test_only_new_or_changed_library_intents_charge_consumers(repo: Path, edit: str):
    pending = _intent("libs/leaf" if edit != "added" else "libs/parent")
    _nested_library_base(repo, pending=pending)
    if edit == "removed":
        _git(repo, "rm", "--", ".changefiles/pending.json")
    elif edit != "unchanged":
        if edit == "comment":
            pending["comment"] = "clarified release note"
        elif edit == "removed-entry":
            pending["changes"] = []
        elif edit == "type":
            pending["changes"][0]["type"] = "minor"
        else:
            pending["changes"].append({"plugin": "libs/leaf", "type": "patch"})
        _write(repo, ".changefiles/pending.json", json.dumps(pending))
    _write(repo, "docs/note.md", "unrelated documentation\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "pending intent delta")
    result = _run(repo)
    assert result.returncode == (1 if edit in ("type", "added") else 0), (
        result.stdout + result.stderr
    )
    if edit in ("type", "added"):
        assert "fresh changefile" in result.stderr


@pytest.mark.parametrize("data", [
    "{broken",
    json.dumps({"changes": "not-a-list"}),
    json.dumps({"changes": [{"plugin": "libs/leaf", "type": "unknown"}]}),
])
def test_malformed_changefile_only_intent_fails_closed(repo: Path, data: str):
    _nested_library_base(repo)
    _write(repo, ".changefiles/library.json", data)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "malformed library intent")
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "changefile" in result.stderr.lower()


def test_unreadable_baseline_changefile_cannot_hide_changed_library_intent(repo: Path):
    _nested_library_base(repo)
    _write(repo, ".changefiles/pending.json", "{broken")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "unreadable baseline intent")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo, ".changefiles/pending.json", json.dumps(_intent("libs/leaf")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "replace unreadable intent")
    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "baseline changefile" in result.stderr.lower()


@pytest.mark.parametrize("checkout_intent", [None, "alpha", "{broken"])
def test_library_intent_uses_selected_head_not_checkout(repo: Path, checkout_intent: str | None):
    _nested_library_base(repo)
    _write(repo, ".changefiles/library.json", json.dumps(_intent("libs/leaf")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "library intent at selected head")
    _git(repo, "update-ref", "refs/heads/candidate", "HEAD")
    _git(repo, "checkout", "-q", "--detach", "origin/main")
    if checkout_intent is not None:
        content = checkout_intent if checkout_intent == "{broken" else json.dumps(
            _intent(checkout_intent)
        )
        _write(repo, ".changefiles/library.json", content)

    result = _run(repo, "--head", "candidate")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "alpha: content changed" in result.stderr


@pytest.mark.parametrize("covered", [False, True])
def test_consumer_coverage_uses_selected_head_not_checkout(repo: Path, covered: bool):
    _nested_library_base(repo)
    _write(repo, ".changefiles/library.json", json.dumps(_intent("libs/leaf")))
    _write(repo, ".changefiles/consumer.json", json.dumps(_intent("alpha" if covered else "beta")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "selected consumer coverage")
    _write(repo, ".changefiles/consumer.json", json.dumps(_intent("beta" if covered else "alpha")))

    result = _run(repo)
    assert result.returncode == (0 if covered else 1), result.stdout + result.stderr
    if not covered:
        assert "alpha: content changed" in result.stderr


@pytest.mark.parametrize(("baseline_mode", "head_mode"), [
    (None, "120000"), ("100644", "120000"), ("120000", "100644"),
])
def test_git_symlink_changefile_rejected_even_with_regular_checkout_file(
    repo: Path, baseline_mode: str | None, head_mode: str,
):
    _nested_library_base(repo)
    data = _intent("libs/leaf")
    data["changes"].append({"plugin": "alpha", "type": "patch"})
    content = json.dumps(data)
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo, input=content,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _write(repo, ".changefiles/library.json", content)
    if baseline_mode is not None:
        _git(repo, "update-index", "--add", "--cacheinfo",
             f"{baseline_mode},{blob},.changefiles/library.json")
        _git(repo, "commit", "-qm", "baseline changefile mode")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "update-index", "--add", "--cacheinfo",
         f"{head_mode},{blob},.changefiles/library.json")
    _git(repo, "commit", "-qm", "symlink mode in selected tree")

    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "symlinked" in result.stderr


@pytest.mark.parametrize("manifest", [
    "libs/parent/pyproject.toml", "plugins/alpha/pyproject.toml",
])
@pytest.mark.parametrize("head_has_edge", [False, True])
def test_dependency_graph_uses_selected_head_not_checkout(
    repo: Path, manifest: str, head_has_edge: bool,
):
    _nested_library_base(repo)
    original = (repo / manifest).read_text(encoding="utf-8")
    without_edge = '[project]\nname = "unrelated"\n'
    if not head_has_edge:
        _write(repo, manifest, without_edge)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "baseline without dependency edge")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo, ".changefiles/library.json", json.dumps(_intent("libs/leaf")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "selected library intent")
    _write(repo, manifest, without_edge if head_has_edge else original)

    result = _run(repo)
    assert result.returncode == (1 if head_has_edge else 0), result.stdout + result.stderr
    if head_has_edge:
        assert "alpha: content changed" in result.stderr


@pytest.mark.parametrize("manifest", [
    "libs/parent/pyproject.toml", "plugins/alpha/pyproject.toml",
])
def test_dependency_graph_validates_git_manifest_modes(repo: Path, manifest: str):
    _nested_library_base(repo)
    blob = subprocess.run(
        ["git", "rev-parse", f"HEAD:{manifest}"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _git(repo, "update-index", "--cacheinfo", f"120000,{blob},{manifest}")
    _write(repo, ".changefiles/library.json", json.dumps(_intent("libs/leaf")))
    _write(repo, ".changefiles/consumer.json", json.dumps(_intent("alpha")))
    _git(repo, "add", "--", ".changefiles")
    _git(repo, "commit", "-qm", "selected symlink mode with regular checkout manifest")

    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "symlink" in result.stderr


def test_dependency_manifest_directory_is_not_treated_as_missing(repo: Path):
    _nested_library_base(repo)
    _git(repo, "rm", "--", "libs/parent/pyproject.toml")
    _write(repo, "libs/parent/pyproject.toml/unexpected.txt", "not a manifest\n")
    _write(repo, ".changefiles/library.json", json.dumps(_intent("libs/leaf")))
    _write(repo, ".changefiles/consumer.json", json.dumps(_intent("alpha")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "directory in place of selected dependency manifest")

    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "not a regular dependency input" in result.stderr


def test_packaged_source_inventory_uses_selected_head_not_checkout(repo: Path):
    _write(repo, "tools/shared.py", "value = 1\n")
    manifest = {
        "schema": "copilot-extensions.packaged-assets", "version": 1,
        "canonicalDir": "tools", "files": ["shared.py"],
    }
    _write(repo, "plugins/alpha/packaged-assets.json", json.dumps(manifest))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "packaged source baseline")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo, "tools/shared.py", "value = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "selected packaged source change")
    manifest["files"] = ["other.py"]
    _write(repo, "plugins/alpha/packaged-assets.json", json.dumps(manifest))

    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "alpha: content changed" in result.stderr


def test_snapshot_ignores_links_inside_retired_build_artifacts(repo: Path):
    content = "outside"
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"], cwd=repo, input=content,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    relative = "plugins/alpha/libs/leaf/build/link"
    _write(repo, relative, content)
    _git(repo, "update-index", "--add", "--cacheinfo", f"120000,{blob},{relative}")
    _git(repo, "commit", "-qm", "retired build artifact baseline")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo, ".changefiles/library.json", json.dumps(_intent("libs/leaf")))
    _git(repo, "add", "--", ".changefiles")
    _git(repo, "commit", "-qm", "library intent without a real copied consumer")

    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_snapshot_recognizes_multilevel_standalone_consumer(repo: Path):
    _write(repo, "services/pr/pyproject.toml", '[project]\nname = "service"\nversion = "1.0"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "standalone consumer baseline")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo, "services/pr/src/service.py", "value = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "standalone consumer source change")

    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "services/pr: content changed" in result.stderr


@pytest.mark.parametrize("guard", [SCRIPT.name, "check-version-bump.py"])
def test_quoted_library_source_path_requires_consumer_bump(repo: Path, guard: str):
    _git(repo, "config", "core.quotePath", "true")
    _nested_library_base(repo)
    _write(repo, "libs/leaf/src/caf\u00e9.py", "value = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "quoted library source change without consumer bump")

    result = subprocess.run(
        [sys.executable, str(repo / "tools" / guard), "--base", "origin/main"],
        cwd=repo, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "alpha" in result.stderr


@pytest.mark.parametrize("edit", ["added", "type"])
def test_consumed_library_intent_requires_fresh_filename(repo: Path, edit: str) -> None:
    baseline = _intent("alpha" if edit == "added" else "libs/leaf")
    _nested_library_base(repo, pending=baseline)
    _git(repo, "update-ref", "refs/heads/last-promotion", "HEAD")
    pending = _intent(baseline["changes"][0]["plugin"])
    if edit == "added":
        pending["changes"].append({"plugin": "libs/leaf", "type": "patch"})
    else:
        pending["changes"][0]["type"] = "minor"
    _write(repo, ".changefiles/pending.json", json.dumps(pending))
    _write(repo, ".changefiles/consumer-new.json", json.dumps(_intent("alpha")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change a consumed library filename with fresh consumer coverage")

    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "fresh changefile" in result.stderr
    assert "pending.json" in result.stderr

    _write(repo, ".changefiles/pending.json", json.dumps(baseline))
    _write(repo, ".changefiles/library-new.json",
           json.dumps(_intent("libs/leaf", "patch" if edit == "added" else "minor")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "place new library release intent in a fresh filename")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("target", ["libs/leaf", "alpha"])
def test_consumed_filename_cannot_be_readded_as_bump_coverage(repo: Path, target: str) -> None:
    _nested_library_base(repo, pending=_intent(target))
    _git(repo, "update-ref", "refs/heads/last-promotion", "HEAD")
    _git(repo, "rm", "--", ".changefiles/pending.json")
    _git(repo, "commit", "-qm", "clear the consumed filename from the contribution branch")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo, ".changefiles/pending.json", json.dumps(_intent(target)))
    other = "alpha" if target == "libs/leaf" else "libs/leaf"
    _write(repo, ".changefiles/fresh-other.json", json.dumps(_intent(other)))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "reuse a consumed filename beside a fresh companion intent")

    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "fresh changefile" in result.stderr
    assert "pending.json" in result.stderr


def test_filename_consumed_on_target_after_branch_fork_is_not_fresh(repo: Path) -> None:
    _nested_library_base(repo)
    _write(repo, ".changefiles/pending.json", json.dumps(_intent("libs/leaf")))
    _write(repo, ".changefiles/consumer-new.json", json.dumps(_intent("alpha")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "candidate release intents")
    _git(repo, "branch", "candidate")
    _git(repo, "checkout", "-q", "-b", "integrated-base", "origin/main")
    _write(repo, ".changefiles/pending.json", json.dumps(_intent("libs/leaf")))
    _write(repo, ".changefiles/consumer-old.json", json.dumps(_intent("alpha")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "the target integrates and promotes the same filename")
    _git(repo, "update-ref", "refs/heads/last-promotion", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "candidate")

    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "fresh changefile" in result.stderr


def test_renamed_consumer_uses_a_fresh_release_identity(repo: Path) -> None:
    _nested_library_base(repo, pending=_intent("alpha"))
    _git(repo, "update-ref", "refs/heads/last-promotion", "HEAD")
    _git(repo, "mv", ".changefiles/pending.json", ".changefiles/consumer-new.json")
    _write(repo, ".changefiles/library-new.json", json.dumps(_intent("libs/leaf")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "fresh library intent and renamed consumer release identity")

    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("scenario", ["new-consumer", "new-edge"])
def test_target_dependency_additions_require_fresh_consumer_coverage(
    repo: Path, scenario: str,
) -> None:
    _nested_library_base(repo)
    if scenario == "new-edge":
        _plugin(repo, "beta")
        _write(repo, "plugins/beta/pyproject.toml",
               '[tool.uv.sources]\nparent = { path = "../../libs/parent", editable = true }\n')
        _write(repo, "libs/parent/pyproject.toml", '[project]\nname = "parent"\n')
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "baseline consumer without the nested edge")
        _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _write(repo, "libs/leaf/src/change.py", "value = 1\n")
    _write(repo, ".changefiles/alpha-new.json", json.dumps(_intent("alpha")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "library edit already covers the original consumer")
    _git(repo, "branch", "candidate")
    _git(repo, "checkout", "-q", "-b", "integrated-base", "origin/main")
    if scenario == "new-consumer":
        _plugin(repo, "beta")
        _write(repo, "plugins/beta/pyproject.toml",
               '[tool.uv.sources]\nparent = { path = "../../libs/parent", editable = true }\n')
    else:
        _write(repo, "libs/parent/pyproject.toml",
               '[tool.uv.sources]\nleaf = { path = "../leaf", editable = true }\n')
    _write(repo, ".changefiles/beta-old.json", json.dumps(_intent("beta")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "target integrates a new materialized consumer")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "candidate")
    manifest = (repo / "libs/parent/pyproject.toml").read_text(encoding="utf-8")

    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "beta" in result.stderr
    assert (repo / "libs/parent/pyproject.toml").read_text(encoding="utf-8") == manifest
    if scenario == "new-consumer":
        assert not (repo / "plugins/beta").exists()
    _write(repo, ".changefiles/beta-new.json", json.dumps(_intent("beta")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "cover the target consumer with a fresh release identity")
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_target_dependency_removal_does_not_create_spurious_obligations(
    repo: Path,
) -> None:
    _nested_library_base(repo)
    _write(repo, "libs/leaf/src/change.py", "value = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "edit a library formerly used by the target")
    _git(repo, "branch", "candidate")
    _git(repo, "checkout", "-q", "-b", "integrated-base", "origin/main")
    _write(repo, "libs/parent/pyproject.toml", '[project]\nname = "parent"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "target removes the nested dependency")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "candidate")

    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_conflicting_target_inputs_require_reconciliation(repo: Path) -> None:
    _nested_library_base(repo)
    original = (repo / "libs/parent/pyproject.toml").read_text(encoding="utf-8")
    _write(repo, "libs/parent/pyproject.toml", '[project]\nname = "head"\n' + original)
    _write(repo, ".changefiles/alpha-new.json", json.dumps(_intent("alpha")))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "candidate dependency manifest edit")
    _git(repo, "branch", "candidate")
    _git(repo, "checkout", "-q", "-b", "integrated-base", "origin/main")
    _write(repo, "libs/parent/pyproject.toml", '[project]\nname = "target"\n' + original)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "conflicting target manifest edit")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "candidate")

    result = _run(repo)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "rebase" in result.stderr.lower()


def test_standalone_version_guard_keeps_literal_head_scope(repo: Path) -> None:
    _nested_library_base(repo)
    _write(repo, "libs/leaf/src/change.py", "value = 1\n")
    _write(repo, "plugins/alpha/plugin.json",
           json.dumps({"name": "alpha", "version": "1.0.1"}))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "literal head changes its only consumer version")
    _git(repo, "branch", "candidate")
    _git(repo, "checkout", "-q", "-b", "integrated-base", "origin/main")
    _plugin(repo, "beta")
    _write(repo, "plugins/beta/pyproject.toml",
           '[tool.uv.sources]\nparent = { path = "../../libs/parent", editable = true }\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a different target has an additional consumer")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    _git(repo, "checkout", "-q", "candidate")

    result = _run(repo, guard="check-version-bump.py")
    assert result.returncode == 0, result.stdout + result.stderr
