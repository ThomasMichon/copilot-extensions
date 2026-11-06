"""Binding-driven related discovery without topology or lifecycle writes."""

from dataclasses import replace

import pytest
from configuration_layers import ConfigurationError
from dropin_registry import ScanAuthority

from agent_worktrees import config, config_sources, related, related_acquisition, repos


@pytest.fixture
def bound_source(tmp_path, monkeypatch):
    harness, knowledge = tmp_path / "harness", tmp_path / "knowledge"
    harness.mkdir()
    knowledge.mkdir()
    registry = repos.ReposRegistry(repos={
        "notes": repos.RepoEntry(
            name="notes", repo_class="knowledge",
            paths={platform: str(knowledge) for platform in ("windows", "linux", "wsl")},
        ),
    })
    monkeypatch.setattr(repos, "read_registry", lambda **_kwargs: registry)
    cfg = config.Config(
        srcroot=str(tmp_path), machine="fixture", platform=config.detect_platform(),
        repo_name="harness", knowledge_repo="notes",
        repos={"harness": config.RepoConfig(
            anchor=str(harness), worktree_root=str(tmp_path / "worktrees"),
            stateless=True,
        )},
    )

    def anchors(configuration=cfg):
        source = config_sources.knowledge_config_source(
            configuration, base_anchor=str(harness),
        )
        return [
            related.config_contribution_anchor(harness, "harness"),
            related.config_contribution_anchor(
                source.anchor, source.origin, state_repo=source.state_repo,
            ),
        ]

    return harness, knowledge, registry, cfg, anchors


@pytest.mark.parametrize("repo_class", ["knowledge", "worktree"])
def test_bound_state_appears_without_a_committed_self_entry(bound_source, repo_class):
    harness, knowledge, registry, _cfg, anchors = bound_source
    registry.repos["notes"].repo_class = repo_class
    selected = anchors()
    entries = related.list_related_grafted(selected, role="knowledge")
    assert [entry.name for entry in entries] == ["notes"]
    entry = entries[0]
    resolution = related.build_resolution(
        entry, current_machine="fixture", repo_class=repo_class,
        repo_path=str(knowledge), adopted=True, base_repo=True,
    )
    assert entry.locus.preferred == "local" and entry.delegate == "none"
    assert resolution.editing_model == "paired-knowledge-worktree"
    assert "never the registered anchor" in " ".join(resolution.steps)
    assert "state-root --pair --json" in " ".join(resolution.steps)
    assert not list(harness.iterdir()) and not list(knowledge.iterdir())


def test_bound_role_wins_without_promoting_a_harness_policy(bound_source):
    harness, _knowledge, _registry, _cfg, anchors = bound_source
    related.write_related(harness, related.RelatedConfig(related={
        "notes": related.RelatedEntry(
            name="notes", role="product", summary="Existing narrative",
            delegate="agent-bridge", audience="private",
            locus=related.Locus(preferred="codespace"),
            pr={"enabled": True},
        ),
    }))
    before = related.related_path(harness).read_bytes()
    entry = related.get_related_grafted(anchors(), "notes")
    assert entry.role == "knowledge" and entry.locus.preferred == "local"
    assert entry.delegate == "none" and not entry.locus.codespace
    assert entry.summary == "Existing narrative" and entry.pr == {"enabled": True}
    assert entry.origin_layer == "harness"
    assert related.effective_audience(entry) == ""
    assert related.related_path(harness).read_bytes() == before


@pytest.mark.parametrize("failure", ["unregistered", "wrong-class", "different-root"])
def test_bound_discovery_refuses_fallback_or_changed_registration(
    bound_source, monkeypatch, tmp_path, failure,
):
    _harness, _knowledge, registry, _cfg, anchors = bound_source
    selected = anchors()
    if failure == "unregistered":
        registry.repos.clear()
    elif failure == "wrong-class":
        registry.repos["notes"].repo_class = "reference"
    else:
        other = tmp_path / "other"
        other.mkdir()
        registry.repos["notes"].paths = {
            platform: str(other) for platform in ("windows", "linux", "wsl")
        }
    monkeypatch.setattr(
        repos, "resolve_path",
        lambda *_args, **_kwargs: pytest.fail("fallback discovery is not canonical"),
    )
    with pytest.raises(ConfigurationError, match="bound knowledge related source"):
        related.read_related_grafted(selected)
    report = related_acquisition.read_related_grafted_report(selected)
    assert report.authority is ScanAuthority.INDETERMINATE
    assert "notes" not in report.value.related


def test_ordinary_knowledge_config_does_not_claim_external_state(bound_source):
    _harness, _knowledge, _registry, cfg, anchors = bound_source
    ordinary = replace(
        cfg, repos={"harness": replace(cfg.default_repo, stateless=False)},
    )
    assert related.list_related_grafted(anchors(ordinary)) == []


def test_conflicting_bound_sources_are_not_silently_overlaid(bound_source):
    _harness, knowledge, _registry, _cfg, anchors = bound_source
    selected = anchors() + [
        related.config_contribution_anchor(
            knowledge, "knowledge", state_repo="different-binding",
        ),
    ]
    with pytest.raises(ConfigurationError, match="ambiguous"):
        related.read_related_grafted(selected)
    report = related_acquisition.read_related_grafted_report(selected)
    assert report.authority is ScanAuthority.INDETERMINATE
    assert not report.value.related


def test_synthesized_projection_cannot_be_written_back_to_topology(bound_source):
    harness, _knowledge, _registry, _cfg, anchors = bound_source
    related.write_related(harness, related.RelatedConfig(related={
        "tool": related.RelatedEntry(name="tool", role="tooling"),
    }))
    before = related.related_path(harness).read_bytes()
    projected = related.read_related_grafted(anchors())
    with pytest.raises(ConfigurationError, match="Cannot persist a synthesized"):
        related.write_related(harness, projected)
    assert related.related_path(harness).read_bytes() == before


def test_current_checkout_briefing_retains_aligned_state_policy(bound_source):
    from agent_worktrees import related_briefing

    _harness, knowledge, _registry, _cfg, anchors = bound_source
    entry = related.get_related_grafted(anchors(), "notes")
    resolution = related.build_resolution(
        entry, current_machine="fixture", repo_class="knowledge",
        repo_path=str(knowledge), adopted=False,
    )
    text = related_briefing.render_briefing(
        entry, resolution, doc_relpath=None,
        current_checkout_path=str(knowledge), registered=True,
    )
    assert "state-root --pair --json" in text
    assert "never the registered anchor" in text
    assert "work here directly" not in text
