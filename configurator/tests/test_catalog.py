"""Phase 1 tests: the installer-owned declarative plugin-knowledge model.

These exercise the catalog itself and its opportunistic reconcile against a real
copilot-extensions checkout (when the tests run inside one). See ``catalog.py``
and ``data/plugins.toml``.
"""

from __future__ import annotations

from configurator.catalog import (
    KINDS,
    Catalog,
    all_prereqs,
    find_repo_root,
    load_catalog,
    published_prereq_names,
)
from configurator.model import coverage
from configurator.__main__ import main


def test_catalog_loads_and_is_well_formed():
    cat = load_catalog()
    assert isinstance(cat, Catalog)
    assert cat.schema_version >= 1
    assert cat.plugins, "catalog should model at least one plugin"
    names = cat.names
    assert len(names) == len(set(names)), "plugin names must be unique"
    for p in cat.plugins:
        assert p.name
        assert p.kind in KINDS
        assert p.steps, f"{p.name}: every plugin needs at least one 'what to do' step"


def test_core_plugin_is_agent_worktrees():
    cat = load_catalog()
    core = [p for p in cat.plugins if p.kind == "core"]
    assert [p.name for p in core] == ["agent-worktrees"]
    aw = cat.get("agent-worktrees")
    assert aw is not None
    # It must carry a runnable install step and its published git/python prereqs.
    assert any(s.runs and "install.ps1" in s.runs for s in aw.steps)
    assert {pr.name for pr in aw.published_prereqs} >= {"git", "python3"}


def test_baseline_prereqs_present():
    cat = load_catalog()
    baseline = {pr.name for pr in cat.baseline_prereqs}
    assert {"git", "python3", "uv"} <= baseline


def test_all_prereqs_dedups_and_prefers_required():
    cat = load_catalog()
    union = all_prereqs(cat)
    names = [pr.name for pr in union]
    assert len(names) == len(set(names)), "union must be de-duplicated by name"
    # python3 appears required somewhere, so the union entry is not optional.
    py = next(pr for pr in union if pr.name == "python3")
    assert not py.optional


def test_dependency_edges_reference_known_plugins():
    cat = load_catalog()
    known = set(cat.names)
    for p in cat.plugins:
        for dep in p.depends_on:
            assert dep in known, f"{p.name} depends on unknown plugin {dep!r}"


def test_plugins_command_lists_and_details(capsys):
    assert main(["plugins"]) == 0
    out = capsys.readouterr().out
    assert "agent-worktrees" in out

    assert main(["plugins", "agent-worktrees"]) == 0
    detail = capsys.readouterr().out
    assert "install.ps1" in detail
    assert "prerequisites" in detail

    assert main(["plugins", "--prereqs"]) == 0
    assert "git" in capsys.readouterr().out


def test_plugins_command_unknown_name_errors(capsys):
    assert main(["plugins", "does-not-exist"]) == 2
    assert "unknown plugin" in capsys.readouterr().out


# ── coverage against a real checkout (only when running inside one) ─────────

def test_coverage_against_checkout_has_no_errors():
    """Inside a checkout, membership is *discovered*, so uncatalogued plugins are
    allowed (inference handles them). What must never happen: a phantom catalog
    entry (authored but not discovered) or a published service.yaml prereq the
    catalog fails to carry. Those are the forcing functions that keep the
    knowledge overlay honest.
    """
    root = find_repo_root()
    if root is None:
        return  # no checkout — discovery would go remote; skip the local assertion
    cov = coverage(repo_root=root, allow_remote=False)
    assert cov.source_kind == "checkout"
    assert not cov.phantom, f"catalog names not in the marketplace (phantom/renamed): {cov.phantom}"
    assert not cov.published_prereq_gaps, (
        f"published service.yaml prereqs missing from the catalog: {cov.published_prereq_gaps}"
    )


def test_published_prereq_reader_matches_service_yaml():
    root = find_repo_root()
    if root is None:
        return
    # agent-codespaces publishes gh + ssh + python3 in its service.yaml.
    names = set(published_prereq_names(root, "agent-codespaces"))
    if names:  # present only inside a checkout that ships the service.yaml
        assert {"gh", "ssh", "python3"} <= names
