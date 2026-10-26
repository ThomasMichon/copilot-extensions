# Purpose-first Mux Companion continuation

Parent contract: [README.md](README.md), Step 6 and its Validation Plan.
Coordination: #8811. Standing intent: [Mux Companion](../../../visions/mux-companion/README.md).

## Request capture

Public-safe transcription of the continuation request: the private graphical
application name is replaced by `[graphical companion]`; its requested
equivalence and shared-data constraint are unchanged.

> Continue driving the Worktree Manager mux companion app effort and vision.
> Using Textual, and some cleverness with [PS|T]Mux, I want to be able to build a
> rich feature set equivalent to the [graphical companion], backed by the same
> data sources.
>
> Right now, we were building out the Mux Companion as a small dialog overlay
> that can be launched via "Ctrl+K", with the first feature being a session
> selector to aid Handoff. Now that Context Management Tools exist, that's lower
> priority. Instead, I'd like to focus on using the Mux Companion to show
> overall Summary and Claims information, to make it easy to see a worktree's
> "purpose" at-a-glance.
>
> I'm also interested in seeing if we can "go nuts" and make a graph-style
> renderer of a worktree's sub-agent status and high-level states, similar to
> the Companion App, but in a succint way, and present that as an alternate
> "Pane" which can render for a worktree in the Mux server, and allow the user
> to flip between the Copilot CLI view or an overall "summary" view. The summary
> view would show current work phases based on disposition reporting, would
> show allocated sub-agents and bridges, downstream worktrees, open PRs, etc,
> and also show accumulated stats from tool calls. Users could switch to this
> view to spare theselves the "text vomit" of the CLI. Of course, when the CLI
> asks a question or goes idle, they'd get an indicator in that view, or the
> mux toolbar, to switch back to the CLI view.
>
> I am also interested in adding a second permanent line to the Mux status bar,
> to support showing CLAIMS information explicitly, and adding clickable tab
> support for the different views.

**Operator revision, 2026-10-25:**

> We can instead show some hotkey guides to switch modes.

This replaces the required clickable CLI/Summary view tabs with visible
keyboard guides. The permanent second Claims row and same-window pane layout
are unchanged. Guides must name working, context-appropriate controls rather
than implying that a popup-summon key directly toggles persistent panes.
This does not authorize an unreleased mux build, a runtime update or a live
binding change.

## Implementation sequence

The sequencing and safeguards below are **agent-recommended**, realizing the
requested feature set without creating another data or session authority.

1. **Summary/Claims first.** Extend the existing Textual application rather
   than replacing it. Resolve the selected worktree through `engine_client`,
   consume the existing summary/disposition and claims contracts, and make
   Summary the initial view. Keep session lineage and explicit cutover in a
   secondary recovery view. Show unavailable sources distinctly from no claims.
2. **Source contract and topology.** Identify the producer contracts already
   used by graphical companions. Put reusable, organization-neutral readers
   with their existing engine/Bridge/accounting owners, not in a private
   application's backend or a second local ledger. A terminal presenter can
   format a bounded tree/graph without scraping raw transcripts or inferring
   progress from titles. Preserve relationship direction, entity identity,
   freshness and partial failures. Show phases only when actually reported;
   absent telemetry is unknown, not zero.
3. **Alternate pane.** Add a mux-owned observer presentation in the same
   mux window, with explicit view switching that leaves the original CLI pane
   and its process alive. Use existing Manager mux registration and wrappers.
   Visible refresh must not block Textual input or repeat whole-fleet scans.
   Closing the observer is not closing or replacing the CLI.
   The first integration should preserve Ctrl+K's existing popup and offer
   an explicit Show Summary pane action plus return to CLI. Resolve the
   registered session and actual CLI pane/window at action time; never
   assume pane zero. Use the Manager's installation-owned command/wrapper,
   not ambient `uv` or source-checkout borrowing. A split/select/zoom prototype
   is not complete until original CLI identity, repeated switches, observer
   close and detach/reattach are tested through the real consumer.
4. **Attention and status chrome.** Consume the existing attention/turn-state
   source for questions and idle. Show actionable CLI/Summary hotkey guides
   and a permanent claims row through the existing relay. Reuse existing
   keyboard controls where their meaning fits; preserve the Ctrl+K popup
   contract and distinguish it from persistent-pane navigation.
   Keep session-scoped
   psmux configuration distinct from tmux's shared-server boundary. A backend
   lacking a required status feature needs a truthful capability result and
   a tracked qualification gap, not a silent one-line substitute.
5. **Qualification.** Cover small/narrow and large terminals, long purpose
   and claim labels, empty/error/stale data, hierarchy cycles and fan-out,
   successful and failed tool calls, user questions, idle and active CLI,
   repeated switches, detach/reattach and observer exit. Prove real mux-client
   mode-switch key handling separately from pty `send-keys` and registration tables.
   First-provision and live installed lanes remain separate from source tests.

## Scope and coordination

- **Operator layout decision:** when offered native windows versus a
  same-window pane switch, the operator selected `same-window`: "Same mux
  window; switch between CLI and summary panes." Separate CLI/Summary mux
  windows are not an accepted substitute. This is requested intent, not
  agent-recommended sequencing.
- **Operator chrome decision:** hotkey guides replace required clickable mode
  tabs. The relay's older clickable-popup summon work retains its separately
  accepted deferral; it is not a prerequisite for keyboard-guided view switching.
- The same Companion app effort owns this continuation. The existing
  `mux-bind-relay` effort remains the owner for command wrappers, registration,
  popup focus/lifetime and platform-specific relay safety. Neither effort's
  historical acceptance is marked complete by the new presentation.
- Implementation is incremental, in independent source worktrees/PRs. The
  driving participant owns integration and PR stewardship. Existing public
  claims take precedence for overlapping source, attention, accounting and
  relay slices; this proposal does not transfer their custody.
- Reuse installed Textual and Rich; no new graph dependency, daemon, polling
  service, claim writer, answer automation or session-hosting path is implied.
- The parent effort stays Active until its full Plan and Validation Plan are
  resolved or explicitly transferred to named tracked objectives. Finishing
  Summary/Claims alone does not complete topology, native tabs or old cutover
  acceptance.
