"""The provider conformance contract -- Phase 1 of the ``pull-request-capability``
effort (Vision ``plugins/agent-worktrees/pull-requests`` §Features/
``conformance-verified-mock-provider``).

This is the single, named place that states what *every* ``PRProvider`` --
real (``gitea`` / ``github`` / ``azure-devops``) or fabricated (``mock``) --
must honor structurally, plus the full behavioral lifecycle exercised end to
end against ``mock`` (the only provider that can run a create -> observe ->
review -> merge cycle with no network/CLI).

Two layers, deliberately not blended:

1. ``TestProviderRegistryConformance`` -- runs against **every** entry in
   ``_PROVIDERS``. Structural only (protocol shape, declared name, and the
   handful of documented capability defaults that don't require a live
   transport call) so it is safe to run for real providers in any CI
   environment with no credentials.
2. ``TestMockProviderLifecycle`` -- the full behavioral contract (every
   ``PRProvider`` operation, chained into one realistic PR lifecycle), run
   only against ``mock`` since it is the one provider that needs no faked
   transport. ``gitea`` / ``github`` / ``azure-devops`` already exercise
   their own success/failure paths against faked ``run_cli``/``curl`` output
   in ``test_providers.py``; this file does not duplicate that -- it is the
   cross-provider contract those per-provider suites all satisfy.
"""

from __future__ import annotations

import pytest

from agent_worktrees.providers import get_provider
from agent_worktrees.providers.base import PRProvider, PRScope, _PROVIDERS
from agent_worktrees.pr_contract import PRSnapshot, ThreadsResult

ALL_PROVIDER_NAMES = tuple(sorted(_PROVIDERS))


# ---------------------------------------------------------------------------
# 1. Structural conformance -- every registered provider
# ---------------------------------------------------------------------------

class TestProviderRegistryConformance:
    """What every registered ``PRProvider`` must be true of, with no transport."""

    @pytest.mark.parametrize("provider_name", ALL_PROVIDER_NAMES)
    def test_satisfies_pr_provider_protocol(self, provider_name):
        provider = get_provider(provider_name)
        assert isinstance(provider, PRProvider)

    @pytest.mark.parametrize("provider_name", ALL_PROVIDER_NAMES)
    def test_declares_its_own_registry_name(self, provider_name):
        provider = get_provider(provider_name)
        assert provider.name == provider_name

    @pytest.mark.parametrize("provider_name", ALL_PROVIDER_NAMES)
    def test_authority_endpoint_is_a_string(self, provider_name):
        provider = get_provider(provider_name)
        assert isinstance(provider.authority_endpoint(""), str)
        assert isinstance(provider.authority_endpoint("https://example.test"), str)

    @pytest.mark.parametrize("provider_name", ALL_PROVIDER_NAMES)
    def test_head_contained_in_base_never_raises_on_absent_input(self, provider_name):
        # Every provider must treat "nothing to check" as unknown (None), not
        # raise -- this is a pure/no-transport call for every implementation
        # today (missing base/head short-circuits before any network access).
        provider = get_provider(provider_name)
        result = provider.head_contained_in_base("o/r", "", "")
        assert result is None or isinstance(result, bool)

    def test_unknown_provider_name_raises_with_known_list(self):
        from agent_worktrees.providers.base import ProviderError

        with pytest.raises(ProviderError) as exc_info:
            get_provider("not-a-real-provider")
        message = str(exc_info.value)
        for name in ALL_PROVIDER_NAMES:
            assert name in message


# ---------------------------------------------------------------------------
# 2. Full behavioral lifecycle -- mock only
# ---------------------------------------------------------------------------

@pytest.fixture
def provider():
    return get_provider("mock")


@pytest.fixture
def scope():
    return PRScope(
        repo="acme/widgets",
        head="feature/thing",
        base="main",
        title="Add the thing",
        body="Body text.",
        labels=("source:mock",),
    )


class TestMockProviderLifecycle:
    def test_create_pull_returns_open_pull_result(self, provider, scope):
        result = provider.create_pull(scope)
        assert result.number == 1
        assert result.url.endswith("/pull/1")
        assert result.state == "open"
        assert result.merged is False

    def test_create_pull_numbers_increment_per_repo(self, provider, scope):
        first = provider.create_pull(scope)
        second = provider.create_pull(scope)
        other_repo = provider.create_pull(
            PRScope(repo="acme/other", head="h", base="main", title="t")
        )
        assert (first.number, second.number) == (1, 2)
        assert other_repo.number == 1  # independent numbering per repo

    def test_get_pull_roundtrips_created_pr(self, provider, scope):
        created = provider.create_pull(scope)
        fetched = provider.get_pull(scope.repo, created.number)
        assert fetched.number == created.number
        assert fetched.url == created.url
        assert fetched.state == "open"

    def test_get_pull_unknown_number_raises(self, provider, scope):
        from agent_worktrees.providers.base import ProviderError

        with pytest.raises(ProviderError):
            provider.get_pull(scope.repo, 999)

    def test_observe_head_refreshes_timestamp(self, provider, scope):
        created = provider.create_pull(scope)
        observed = provider.observe_head(scope.repo, created.number)
        assert observed.head_sha == f"sha-{scope.head}"
        assert observed.observed_at  # non-empty -- a provider-clock timestamp

    def test_publish_source_marker_succeeds(self, provider, scope):
        created = provider.create_pull(scope)
        assert provider.publish_source_marker(
            scope.repo, created.number, "marker text"
        ) == ""

    def test_label_add_and_remove_roundtrip(self, provider, scope):
        created = provider.create_pull(scope)
        assert provider.add_label(scope.repo, created.number, "auto-merge") == ""
        snap = provider.get_snapshot(scope.repo, created.number)
        assert "auto-merge" in snap.labels
        assert "source:mock" in snap.labels  # from scope.labels at create time

        assert provider.remove_label(scope.repo, created.number, "auto-merge") == ""
        snap = provider.get_snapshot(scope.repo, created.number)
        assert "auto-merge" not in snap.labels

    def test_remove_label_absent_is_a_no_op_success(self, provider, scope):
        created = provider.create_pull(scope)
        # Removing a label that was never applied is still "" (idempotent).
        assert provider.remove_label(scope.repo, created.number, "never-there") == ""

    def test_mark_ready_flips_draft_and_strips_wip_prefix(self, provider):
        draft_scope = PRScope(
            repo="acme/widgets", head="h", base="main", title="[WIP] Add thing",
            draft=True,
        )
        created = provider.create_pull(draft_scope)
        snap = provider.get_snapshot(draft_scope.repo, created.number)
        assert snap.draft is True

        err = provider.mark_ready(
            draft_scope.repo, created.number, wip_title_prefixes=("[WIP] ",)
        )
        assert err == ""
        snap = provider.get_snapshot(draft_scope.repo, created.number)
        assert snap.draft is False
        assert snap.title == "Add thing"

    def test_mark_ready_on_non_draft_reports_error(self, provider, scope):
        created = provider.create_pull(scope)  # not a draft
        err = provider.mark_ready(scope.repo, created.number)
        assert err != ""

    def test_get_snapshot_reflects_reviews(self, provider, scope):
        created = provider.create_pull(scope)
        provider.add_review(
            scope.repo, created.number, id=1, state="APPROVED", user="reviewer1",
        )
        snap = provider.get_snapshot(scope.repo, created.number)
        assert isinstance(snap, PRSnapshot)
        assert [r.state for r in snap.reviews] == ["APPROVED"]
        assert snap.max_review_id == 1

    def test_merge_pull_succeeds_once_then_reports_already_merged(
        self, provider, scope
    ):
        created = provider.create_pull(scope)
        assert provider.merge_pull(scope.repo, created.number) == ""
        result = provider.get_pull(scope.repo, created.number)
        assert result.merged is True
        assert result.state == "closed"

        # Merging again is a documented error, not a silent no-op.
        err = provider.merge_pull(scope.repo, created.number)
        assert err != ""

    def test_request_auto_complete_applies_label(self, provider, scope):
        created = provider.create_pull(scope)
        err = provider.request_auto_complete(
            scope.repo, created.number, automerge_label="auto-merge"
        )
        assert err == ""
        snap = provider.get_snapshot(scope.repo, created.number)
        assert "auto-merge" in snap.labels

    def test_enable_auto_merge_succeeds(self, provider, scope):
        created = provider.create_pull(scope)
        assert provider.enable_auto_merge(scope.repo, created.number) == ""

    def test_get_repo_policy_reports_supported(self, provider, scope):
        policy = provider.get_repo_policy(scope.repo)
        assert policy.supported is True
        assert policy.viewer_permission == "admin"

    def test_head_contained_in_base_true_only_after_merge(self, provider, scope):
        created = provider.create_pull(scope)
        head_sha = f"sha-{scope.head}"
        assert provider.head_contained_in_base(
            scope.repo, scope.base, head_sha
        ) is False
        provider.merge_pull(scope.repo, created.number)
        assert provider.head_contained_in_base(
            scope.repo, scope.base, head_sha
        ) is True

    def test_head_contained_in_base_unknown_head_is_none(self, provider, scope):
        assert provider.head_contained_in_base(
            scope.repo, scope.base, "sha-never-existed"
        ) is None

    def test_ensure_fork_is_idempotent(self, provider, scope):
        first = provider.ensure_fork(scope.repo)
        second = provider.ensure_fork(scope.repo)
        assert first == second
        assert first is not None
        owner, clone_url = first
        assert owner and clone_url

    def test_get_comment_threads_reports_supported(self, provider, scope):
        created = provider.create_pull(scope)
        provider.add_thread(scope.repo, created.number, status="active")
        result = provider.get_comment_threads(scope.repo, created.number)
        assert isinstance(result, ThreadsResult)
        assert result.supported is True
        assert len(result.active) == 1

    def test_resolve_threads_marks_targets_resolved(self, provider, scope):
        created = provider.create_pull(scope)
        tid = provider.add_thread(scope.repo, created.number, status="active")
        provider.add_thread(scope.repo, created.number, status="active")

        err = provider.resolve_threads(
            scope.repo, created.number, thread_ids=(tid,)
        )
        assert err == ""
        result = provider.get_comment_threads(scope.repo, created.number)
        assert len(result.active) == 1  # only the untargeted thread remains active

    def test_resolve_threads_with_no_ids_resolves_all_active(self, provider, scope):
        created = provider.create_pull(scope)
        provider.add_thread(scope.repo, created.number, status="active")
        provider.add_thread(scope.repo, created.number, status="active")

        assert provider.resolve_threads(scope.repo, created.number) == ""
        result = provider.get_comment_threads(scope.repo, created.number)
        assert len(result.active) == 0

    def test_list_open_pulls_excludes_merged(self, provider, scope):
        first = provider.create_pull(scope)
        second = provider.create_pull(scope)
        provider.merge_pull(scope.repo, first.number)

        open_numbers = provider.list_open_pulls(scope.repo)
        assert open_numbers == (second.number,)
