"""Closed version-1 vocabulary; no live capability advertisement."""

VERSION = 1
FEED_CAPABILITY = "worktree-observations-feed-v1"
HISTORY_CAPABILITY = "worktree-observations-history-v1"
FRAME_BYTES = 35 * 1024
BODY_BYTES = 32 * 1024
PAGE_ENTRIES = 64
MAX_POSITION = (1 << 63) - 1
FRAME_KINDS = frozenset(
    """
snapshot delta history-page gap heartbeat going-away error
""".split()
)
EVIDENCE_KINDS = frozenset(
    """
persisted-mutation retained-event observed-change reported-lifecycle
""".split()
)
KINDS = frozenset(
    """
worktree-created purpose-changed effort-bound effort-released claim-added
claim-released claim-settled contribution-reported source-gap observed-change source-event
""".split()
)
AVAILABILITY = frozenset({"observed", "unsupported", "unavailable", "head-changed", "unknown"})
COMPLETENESS = frozenset({"complete", "partial", "unknown"})
DURABILITY = frozenset({"committed", "pending", "unknown"})
QUALIFICATIONS = frozenset({"authoritative", "reported", "unknown"})
ACTOR_KINDS = frozenset(
    """
worktree session subagent bridge-session resource unknown
""".split()
)
RELATION_KINDS = frozenset({"parent", "return", "resource", "succession", "unlinked"})
GAP_REASONS = frozenset(
    {"retention", "source-replaced", "journal-write-failed", "cursor-unavailable"}
)
CORRELATION_FIELDS = frozenset(
    """
session_id launch_id task_id tool_call_id agent_id parent_event_id request_id
""".split()
)
ENTRY_FIELDS = frozenset(
    """
kind evidence_kind native_kind occurred_at observed_at actor correlation relations
record_fence source_ref summary details reason phase payload
""".split()
)
HEADER_FIELDS = frozenset(
    """
type contract_version source source_generation resident_generation project worktree_id
head_session_id cursor
""".split()
)
BODY_FIELDS = {
    "snapshot": set(
        """availability completeness durability history_scope history_floor
        replay_barrier base_cursor entries replay_complete facts observed_at""".split()
    ),
    "delta": set(
        """availability completeness durability history_scope history_floor
        base_cursor entries facts observed_at""".split()
    ),
    "history-page": set(
        """availability completeness durability history_scope history_floor
        before_cursor next_before entries has_more observed_at""".split()
    ),
    "gap": set(
        """availability completeness durability history_scope history_floor
        base_cursor reason details reconcile source_ref observed_at""".split()
    ),
    "heartbeat": set(),
    "going-away": {"reason"},
}
