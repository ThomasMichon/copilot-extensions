"""On-demand discussion bodies stay outside persisted watch snapshots."""

from __future__ import annotations


def thread_report(provider, repo, number, *, api_base="", token=None):
    native = getattr(provider, "get_thread_report", None)
    if native is not None:
        return native(repo, number, api_base=api_base, token=token)
    result = provider.get_comment_threads(repo, number, api_base=api_base, token=token)
    return {
        "schema": "agent-worktrees.pr-threads", "version": 1,
        "provider": provider.name, "repo": repo, "pr": number,
        "supported": result.supported, "complete": False if result.error else None,
        "error": result.error, "threads": [
            {"id": thread.id, "status": thread.status, "file_path": thread.file_path,
             "comments": [{"author": comment.author, "text": comment.content} for comment in thread.comments]}
            for thread in result.threads
        ],
    }
