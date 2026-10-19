from agent_worktrees.pr_review_policy import (
    ReviewEvidence,
    effective_review,
    latest_effective_reviews,
    outstanding_change_requests,
)


def review(rid, state, user, commit="head", *, dismissed=False):
    return ReviewEvidence(rid, state, user, commit_id=commit, dismissed=dismissed)


def test_negative_precedes_later_other_reviewer_approval_and_comment():
    reviews = [
        review(1, "CHANGES_REQUESTED", "Human"),
        review(2, "APPROVED", "copilot"),
        review(3, "COMMENTED", "other"),
    ]
    assert effective_review(reviews, include_comments=True).state == "CHANGES_REQUESTED"
    assert outstanding_change_requests(reviews) == ("Human",)


def test_same_reviewer_comment_or_pending_does_not_clear_negative():
    reviews = [
        review(1, "REQUEST_CHANGES", "Alice"),
        review(2, "COMMENTED", "alice"),
        review(3, "PENDING", "ALICE"),
    ]
    assert outstanding_change_requests(reviews) == ("Alice",)
    assert latest_effective_reviews(reviews)["alice"].state == "CHANGES_REQUESTED"


def test_same_reviewer_approval_or_provider_dismissal_clears_negative():
    asked = review(1, "CHANGES_REQUESTED", "Alice")
    assert outstanding_change_requests([asked, review(2, "APPROVED", "alice")]) == ()
    assert outstanding_change_requests([asked, review(2, "DISMISSED", "ALICE")]) == ()
    assert outstanding_change_requests([asked, review(2, "APPROVED", "alice", dismissed=True)]) == ()


def test_author_reviews_are_excluded_case_insensitively():
    reviews = [review(1, "CHANGES_REQUESTED", "Author")]
    assert effective_review(reviews, "author", include_comments=True) is None
    assert outstanding_change_requests(reviews, "AUTHOR") == ()


def test_old_head_negative_survives_head_movement():
    reviews = [
        review(1, "CHANGES_REQUESTED", "human", commit="old"),
        review(2, "APPROVED", "copilot", commit="new"),
    ]
    assert effective_review(reviews).state == "CHANGES_REQUESTED"


def test_clean_comment_remains_a_nonblocking_verdict_without_negatives():
    verdict = effective_review(
        [review(1, "COMMENTED", "copilot")], include_comments=True,
    )
    assert verdict is not None and verdict.state == "COMMENTED"


def test_mixed_precision_timestamps_preserve_the_later_veto():
    reviews = [
        ReviewEvidence(
            2, "APPROVED", "reviewer",
            submitted_at="2026-10-19T09:00:00Z",
        ),
        ReviewEvidence(
            1, "CHANGES_REQUESTED", "reviewer",
            submitted_at="2026-10-19T09:00:00.500Z",
        ),
    ]
    assert outstanding_change_requests(reviews) == ("reviewer",)


def test_missing_timestamp_falls_back_to_later_review_id():
    reviews = [
        ReviewEvidence(
            1, "APPROVED", "reviewer",
            submitted_at="2026-10-19T09:00:00Z",
        ),
        ReviewEvidence(2, "CHANGES_REQUESTED", "reviewer"),
    ]
    assert outstanding_change_requests(reviews) == ("reviewer",)


def test_unknown_reviewer_change_request_fails_closed():
    reviews = [
        ReviewEvidence(
            1, "CHANGES_REQUESTED", "",
            submitted_at="2026-10-19T09:00:00Z",
        ),
        ReviewEvidence(
            2, "APPROVED", "copilot",
            submitted_at="2026-10-19T09:01:00Z",
        ),
    ]
    assert outstanding_change_requests(reviews) == ("unknown reviewer",)
    assert effective_review(reviews).state == "CHANGES_REQUESTED"
