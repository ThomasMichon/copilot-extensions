"""Read-only context key descriptions and their configuration dependencies."""

GET_KEYS: dict[str, str] = {
    "repo-dir": "Anchor repo directory",
    "worktree-dir": "Current worktree root (the worktree you are in; empty if not inside one)",
    "worktree-id": "Current worktree id (empty if not inside one)",
    "session-scope-id": "The identity used for live-session self-registration "
    "and CLI-mode reservations: the worktree id when inside a worktree, "
    "`anchor-<repo>` when inside the anchor, empty otherwise",
    "worktree-state-dir": "Per-worktree or adopted-anchor state directory outside the repo checkout",
    "worktrees-root": "Parent directory that holds all worktrees (formerly 'worktree-dir')",
    "src-dir": "Source root (parent of repos)",
    "config-dir": "Per-project config directory (~/.{project})",
    "machine": "Machine name from config",
    "execution-identity": "Versioned configured execution identity (JSON)",
    "platform": "Platform (win/wsl/linux)",
    "project": "Project name",
    "owner-ref": "This worktree's qualified claim ref "
    "(machine/project/worktree_id[#session]) -- the cross-machine "
    "holder identity for resource leases; empty if not in a worktree",
    "repo-remote": "Canonical remote URL of this repo (registry remote; falls "
    "back to git origin) -- the device-independent repo key",
    "git-remote": "Configured Git remote name for the active repository (defaults to origin)",
    "lease-origin": "Resolved explicit/private Git-ref lease store origin URL "
    "-- the **harness identity** shared by every agent of this "
    "harness (the in-CodeSpace cross-harness fence key); empty "
    "if no store is configured",
    "pr-enabled": "Whether PR mode is enabled (true/false)",
    "pr-required": "Whether PRs are required, blocking direct-to-master (true/false)",
    "pr-provider": "PR provider (gitea|github|azure-devops) when PR mode is on",
    "pr-profile": "Configured/offline PR-flow profile: direct|pr-human-merge|"
    "pr-agent-merge|pr-self-merge (networked commands may report an "
    "actor-effective profile)",
}

#: Keys whose value can depend on the control-plane PR-graft overlay
#: (``_control_plane_related_pr_map`` -- see ``config.load_config``'s
#: ``include_control_plane_related_pr``). Every other key never reads
#: ``repo.pr.*``, so skipping that overlay computation for them is safe and,
#: per copilot-extensions#2660, the single highest-leverage latency fix for
#: ``get`` (that computation alone measured 7-8s of an 8-15s total ``get``
#: call, regardless of which key was actually requested -- a 13-subprocess,
#: ~218-file-read installed-plugin/PR-graft walk with no caching).
PR_GRAFT_DEPENDENT_KEYS = frozenset({"pr-enabled", "pr-required", "pr-provider", "pr-profile"})
