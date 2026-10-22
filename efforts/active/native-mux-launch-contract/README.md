# Native Mux Launch Contract

_This proposal was prepared with GitHub Copilot._

- **Slug:** `native-mux-launch-contract`
- **Repo:** copilot-extensions
- **Branch(es):** sequential managed proposal and implementation PRs against `dev`
- **Created:** 2026-10-22
- **Status:** Draft
- **Vision:** closes [session-hosting](../../../visions/session-hosting/README.md) host-owned presentation and exact initial context; preserves [architecture invariants](../../../docs/patterns/README.md)
- **Umbrella issue:** #7962

## Guiding Intent

An interactive `agent-worktrees copilot` invocation must keep terminal input
with its attached mux until that client exits. Reattaching by the mux's native
command must not lose worktree status. Resuming a conversation must execute in
the selected worktree rather than restore an unrelated anchor directory.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Fix driver | Own the bounded attach, connected-session status and directory repairs through review and source acceptance | Managed source worktree `soldered-bearing` |
| Existing native recovery owner | Retain restart-recovery implementation and activation | #4564; `steady-resistor` |
| Existing session-host owners | Retain provider selection, remote identity and graceful cutover | #352, #5961 and #4496 |

## Coordination

- **Topology:** reviewed proposal first, then one cohesive source repair.
- **Host (owns PRs):** Fix driver.
- **Delegates:** none initially; any later delegation needs an exclusive,
  non-overlapping source scope.
- **Scope:** existing native CLI and Manager mux boundaries, not reboot
  recovery, arbitrary foreign mux adoption, provider replacement or rollout.
- **Handoff:** preserve this complete objective, source workspace, pending
  review and remaining validation; a merged slice is not parent completion.

## Context

- The Windows `copilot` terminal handoff currently calls `os.execvp`, while
  the Manager's Windows foreground launcher waits for its child. A Windows
  overlay is not the POSIX process-replacement/terminal-custody contract.
- Direct embodiment creates its mux without going through the Manager's
  normal launch-script mapping registration. Existing attachment observation
  sees registered mappings, not every native attachment.
- Copilot advertises `-C <directory>` as an explicit working-directory
  selection. A mux's pane starting directory and a resumed conversation's
  recorded workspace are different evidence.
- The reporter identified the wrong directory through Copilot session
  metadata or its displayed workspace, not an independently observed shell
  `pwd`. The repair must test actual execution and distinguish historical
  metadata from current process context.
- Existing native restart, provider attachment identity and daemon cutover
  owners remain unchanged. No installed payload, live session or global
  terminal/feed configuration is patched by this effort.

## Request

The operator reported (verbatim, with no private identifiers):

> Bugs in `agent-worktrees copilot`:
> 1. Although it seemingly rendered the mux session in the terminal, I could not interact with it; key presses fell through to the backing console. I had to use `psmux attach ____` to get back to the session manually
> 2. Manually re-attaching to the mux session failed to ensure the Worktree Manager mux daemon pushed sttaus to it. We should ensure that the Mux daemon pushes status to all muxes with active connections
> 3. The CWD of copilot in that session was somehow set to the anchor repo root, and not the worktree folder.

The operator confirmed `native-mux-launch-contract` and clarified that the
directory observation came from "Copilot session metadata or displayed
workspace." The implementation and validation decomposition below is
**agent-recommended**, not additional operator scope.

## Plan

### Phase 1 - Reviewed contract and bounded reproduction

- [ ] Review and merge this proposal before implementation.
- [ ] Establish fail-first terminal-custody, manual-attachment status and
  resumed-directory regressions at their existing owning boundaries.

### Phase 2 - Existing-owner repairs

- [ ] Keep the Windows interactive attach caller waiting with inherited
  terminal streams; preserve POSIX replacement, detach and exit-code behavior.
- [ ] Ensure every connected, attributable worktree mux receives status through
  existing Manager/engine ownership, including native manual reattachment.
  Preserve incarnation fences and standalone operation when Manager is absent.
- [ ] Pin the selected execution directory through native Copilot launch/resume
  without rewriting session history, dropping the conversation, or accepting a
  foreign worktree identity.
- [ ] Update authoritative launch/status documentation and changefiles for all
  affected shipping owners.

### Phase 3 - Source acceptance and conclusion

- [ ] Exercise focused tests and native Windows consumer boundaries; record
  exact evidence and any unavailable platform or interactive tier.
- [ ] Land the repair through genuine current-head review and required CI.
- [ ] Resolve every Plan and Validation Plan item, journal the result and
  archive this effort. Release and productive rollout remain explicitly
  separate, and no updater is authorized by this source-fix request.

## Validation Plan

- [ ] Terminal tests prove the Windows parent cannot return before its attach
  client finishes, inherits rather than pipes input/output, propagates client
  failure, and preserves POSIX `exec` behavior.
- [ ] Connected-session controls include multiple sessions, manual reattach,
  daemon restart, stale mapping/incarnation refusal and disconnected sessions.
  Unknown attachment probes must not become confirmed zero or claim ownership.
- [ ] Resume tests use an anchor-origin conversation and a distinct selected
  worktree; prove the actual child's directory, not only launch JSON or a title.
- [ ] Native Windows checks exercise the real process boundary in contained,
  disposable state. A synthetic mux client does not prove full human keyboard
  interaction; any unavailable real terminal tier is stated explicitly.
- [ ] POSIX behavior is exercised where available, or remains an explicit
  native-CI obligation without representing Windows compatibility as POSIX proof.
- [ ] Run touched-code lint, install/resource contracts and the relevant
  focused regression portfolios without adding a broad promotion matrix.
- [ ] Review final documentation and graceful-cutover impact; verify actual
  source merge before reporting completion. No running-state claim follows
  from source tests or a merge.

## Proposal

Repair the existing foreground attachment and status-routing contracts through
their current owners. Preserve the session-host process boundary and reuse
native directory selection instead of editing Copilot's stored transcript.
Qualify actual native execution separately from metadata and wrapper output.

## Journal

### 2026-10-22 - Report and scope confirmation

- Confirmed the effort slug and the reporter's directory-observation source.
- Checked current open mux/provider PRs and adjacent attachment/restart issues.
  Existing recovery and provider owners retain their distinct work.
- Traced the Windows overlay call, direct embodiment and Manager mapping seams.
  No executable source or installed runtime has been changed.
