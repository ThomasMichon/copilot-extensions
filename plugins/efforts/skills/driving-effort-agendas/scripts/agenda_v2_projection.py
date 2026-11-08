"""Truthful coarse projection of active and deferred frame-local queues."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agenda import DeferredBranch, Snapshot, _time
from agenda_v2_schema import definition, record


def project(
    documents: dict[str, Any], records: dict[str, Any], *, now: datetime | None = None,
) -> Snapshot:
    cursor = documents["cursor"]
    frames, items = cursor["frame_state"], cursor["item_state"]
    at = now if now is not None else datetime.now(timezone.utc)

    def root(frame_id: str) -> str:
        original = record(records, frames[frame_id]["origin_id"], "frame")
        source = original["source"]
        return f"{source['kind']}:{source['ref']}"

    def stack(identities: list[str]) -> list[str]:
        labels: list[str] = []
        for frame_id in identities:
            pending = frames[frame_id]["pending"]
            if not pending:
                labels.append(f"{root(frame_id)} / accepted child queue; return pending")
                continue
            original = record(records, items[pending[0]]["origin_id"], "item")
            labels.append(f"{root(frame_id)} / {original['definition_id']}")
        return labels

    branches: list[DeferredBranch] = []
    caps = cursor["resource_caps"]
    for saved in cursor["deferred"]:
        origin = records[saved["id"]]
        body = record(records, saved["id"], "deferred")
        saved_frames = saved["frames"]
        frame = frames[saved_frames[-1]]
        pending = frame["pending"]
        resume = items[pending[0]]["next_action"] if pending else "Verify child acceptance and return."
        due = at >= _time(saved["revisit_at"], "deferred revisit")
        counts = [items[item_id]["decompositions"] for frame_id in saved_frames
                  for item_id in frames[frame_id]["pending"][:1]]
        branches.append(DeferredBranch(saved["id"], root(saved_frames[0]), stack(saved_frames),
            resume, saved["decision_ref"], saved["decision_packet_ref"],
            f"Pending decision: {body['decision_ref']}", origin["created_at"], saved["revisit_at"],
            "Recheck current decision and authority; do not replay the saved effect.",
            saved["saved_revision"], caps["max_depth"], caps["max_no_parent_progress"],
            max(counts, default=0), due, "time" if due else None))
    branches.sort(key=lambda branch: (_time(branch.revisit_at, "revisit"), branch.id))
    due = [branch.id for branch in branches if branch.due]
    next_revisit = min((branch.revisit_at for branch in branches if not branch.due),
                      key=lambda value: _time(value, "revisit"), default=None)
    identities = cursor["frames"]
    root_label = root(identities[0]) if identities else branches[0].root if branches else "No active root"
    action, exits, outcomes = "Reconcile canonical acceptance independently.", [], {}
    if identities:
        frame = frames[identities[-1]]
        if frame["pending"]:
            item = items[frame["pending"][0]]
            current = definition(documents, item)
            action, exits, outcomes = item["next_action"], current["exit"], current["on"]
        else:
            action = "Verify child queue acceptance and return to the saved parent."
    elif branches:
        branch = next((branch for branch in branches if branch.due), branches[0])
        action = (f"Recheck deferred branch {branch.id}: {branch.recheck_action}" if branch.due else
                  f"Await/recheck deferred decision {branch.decision_ref}; no effect replay.")
    queued: list[str] = []
    notes = list(cursor["notes"]) + [
        "Context relevance is agent judgment, not permission or external completion.",
        "Deferred budget fields project current v4 depth/no-parent-progress decomposition bounds.",
    ]
    visible_frames = list(identities)
    for saved in cursor["deferred"]:
        visible_frames.extend(saved["frames"])
    for frame_id in visible_frames:
        pending = frames[frame_id]["pending"]
        queued.extend(f"{frame_id}: {items[item_id]['next_action']}" for item_id in pending[1:])
        if pending and frame_id not in identities:
            notes.append(f"Deferred head {frame_id}: {items[pending[0]]['next_action']}")
    return Snapshot(cursor["worktree"], cursor["revision"], cursor["status"],
                    root_label, stack(identities), action, exits, outcomes,
                    queued, len(cursor["parked"]), notes, 4, branches, due, next_revisit)
