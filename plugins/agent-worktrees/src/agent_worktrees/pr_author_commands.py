"""Data-only author command registration for lazy and eager CLI consumers."""

HANDLERS = {
    "pr-author-capabilities": "cmd_capabilities",
    "pr-update": "cmd_update",
    "pr-push": "cmd_push",
    "pr-author-ready": "cmd_ready",
    "pr-author-nudge": "cmd_nudge",
    "pr-thread-resolve": "cmd_thread_resolve",
    "pr-thread-reopen": "cmd_thread_reopen",
}
LAZY_DISPATCH = {name: ("pr_author_cli", handler) for name, handler in HANDLERS.items()}


def handlers():
    from . import pr_author_cli

    return {name: getattr(pr_author_cli, handler) for name, handler in HANDLERS.items()}
