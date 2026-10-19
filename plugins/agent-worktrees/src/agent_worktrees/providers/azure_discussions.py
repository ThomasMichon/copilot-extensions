"""Native iteration-bound discussion reads through the central PR surface."""

from __future__ import annotations

from dataclasses import asdict

from .azure_observation import identity, thread_evidence, validate_target
from .azure_observation_reads import AdoReader


def get_report(provider, repo, number, *, api_base, token, reader_factory=AdoReader):
    from .azure_remediation import thread_fingerprint

    reader = reader_factory(provider, repo, number, api_base, token)
    before = reader.pr()
    validate_target(before, provider, repo)
    collection = reader.threads()
    error = collection.error
    report = {
        "schema": "agent-worktrees.pr-threads", "version": 1,
        "provider": "azure-devops", "repo": repo, "pr": number,
        "supported": True, "complete": False, "threads": [], "error": error,
    }
    if error:
        return report
    try:
        discussions, _ = thread_evidence(collection.records, (before.get("createdBy") or {}).get("id"))
        by_id = {str(row["id"]): row for row in collection.records}
        for discussion in discussions:
            row = by_id[discussion.id]
            comments = []
            for comment in row.get("comments") or []:
                if comment.get("isDeleted") or comment.get("commentType") not in ("text", 1):
                    continue
                text = comment.get("content")
                if not isinstance(text, str):
                    raise ValueError("discussion comment body is unavailable")
                comments.append({
                    "id": str(comment.get("id") or ""),
                    "parent_id": str(comment.get("parentCommentId") or ""),
                    "author_id": identity((comment.get("author") or {}).get("id")),
                    "author": str((comment.get("author") or {}).get("displayName") or ""),
                    "text": text,
                    "published_at": str(comment.get("publishedDate") or ""),
                    "updated_at": str(comment.get("lastUpdatedDate") or ""),
                })
            try:
                fingerprint, reply_error = thread_fingerprint(row), ""
            except ValueError as exc:
                fingerprint, reply_error = "", str(exc)
            report["threads"].append({
                **asdict(discussion), "comments": comments,
                "fingerprint": fingerprint, "reply_error": reply_error,
                "iteration_context": (row.get("pullRequestThreadContext") or {}).get("iterationContext"),
            })
    except ValueError as exc:
        report["error"] = str(exc)
        return report
    after = reader.pr()
    validate_target(after, provider, repo)
    report.update(
        complete=True,
        head_sha=(after.get("lastMergeSourceCommit") or {}).get("commitId") or "",
        observed_head_sha=(before.get("lastMergeSourceCommit") or {}).get("commitId") or "",
        candidate_changed=(after.get("lastMergeSourceCommit") or {}).get("commitId")
        != (before.get("lastMergeSourceCommit") or {}).get("commitId"),
    )
    return report
