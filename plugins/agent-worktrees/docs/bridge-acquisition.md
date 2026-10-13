# Exact bridge-owned CLI launch

Worktree creation allocates execution space without a session. Bridge creation
can compose that allocation with the launch below, or receive an existing
sessionless worktree. Both retain one owner-resolved execution project,
worktree, and path; a task's queue lane never independently redirects the
launch. See the shared
[workspace allocation and session acquisition contract](../../../docs/patterns/workspace-allocation-and-session-acquisition.md).
Its cross-attempt creation-key API remains proposal work until implemented;
it is distinct from the current exact-launch and native-head interfaces.

`embody --worktree-id <id> --bridge-acquisition <native-id|fresh> --json`
is an unseeded launch primitive used beneath agent-bridge acquisition.
It requires an explicit worktree and rejects a simultaneous seed, unchecked
Copilot passthrough arguments, fresh/resume-head mode, or an already-live mux.
`--dry-run` reports the exact `resume_session` intent for capability preflight.

The real launch checks the current worktree record and native head under the
existing lifecycle lock. An exact native resume cannot use filesystem fallback;
the requested ID must match the authoritative head and its process must be
proven dead. Fresh intent also refuses live or unknown prior-head liveness.
It does not consume or overwrite pending worktree creation seeds.

Worktrees owns mux launch and the session ledger; bridge waits for native
registration/head and owns prompt admission. The mux handle returned by embody
is not a native conversation ID. Bridge acquisition adds no PTY or CLI-host
daemon. Ordinary standalone embody/seed behavior is unchanged.
