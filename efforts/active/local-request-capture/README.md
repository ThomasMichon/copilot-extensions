# Local Request Capture

- **Slug:** `local-request-capture`
- **Repo:** copilot-extensions
- **Branch(es):** independent proposal and implementation PRs against `dev`
- **Created:** 2026-10-29
- **Status:** Active
- **Vision:** `visions/harness-guidance`, `attributable-context-budget`
- **Umbrella issue:** #9581

## Guiding Intent

Let an operator inspect the exact request their Copilot CLI client assembles,
including the instructions and tool definitions their harness contributes.
Intentional local raw-prompt debugging is supported. This must not become
logging for an existing or shared inference service.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Coordinator | Own design, integration, review and completion | Managed source worktree |
| Independent reviewer | Read-only implementation review | Coordinator delegation |

## Coordination

- **Topology:** reviewed proposal, then an implementation PR against `dev`.
- **Host (owns PRs):** Coordinator.
- **Delegates:** Independent reviewer inspects the new capture
  skill/scripts/tests and related plugin documentation without editing.
- **Handoff:** report changed paths, executed checks and unresolved concerns;
  the Coordinator owns integration and the final completion judgment.
- No live harness capture, deployment, global configuration change or shared
  endpoint inspection is authorized by this source effort.

## Context

The public plugin already provides a static context inventory through
`customizing-copilot`'s `reviewing-customizations` scanner. That inventory does
not prove which bytes the CLI sent to a provider. The opt-in native startup
test in `agent-worktrees` uses an inline synthetic provider, but is not a
downstream-consumable debugging surface.

This closes the vision's attributable context-budget intent by adding direct
client-request evidence, while preserving the distinction between local
inventory, actual client composition and provider-side additions.

## Request

The operator confirmed the slug `local-request-capture`.

> We need to make generally available the version which captures the raw prompt,
> as sent by the client. This will assist in operators who want to debug what
> gets loaded by their harness into Copilot's instructions. Since we' should be
> using a localhost-only port and endpoint, and the data will never leave the
> operator's machine, it should be fine (and note that all messages sent by the
> user are captured on-machine in Copilot's session-state folder anyway, so this
> is in the same boundary). The specific safety rail we care about is that we
> will never expose a shared inference endpoint that other users consume for
> which we capture and log the raw prompts. Logging our own for debugging
> purposes is perfectly in-bound.

## Plan

### Phase 1 - Review the portable contract
- [x] Land this proposal through the normal review and merge flow.
- [x] Keep the implementation standalone in the payload-only
  `customizing-copilot` plugin, usable without harness registration.

### Phase 2 - Implement operator-owned capture
- [x] Provide a scriptable invocation that starts its own temporary loopback
  listener and launches the operator-selected Copilot CLI in the selected
  harness directory.
- [x] Write the exact received JSON request bytes, not a reconstruction or a
  counts-only substitute, to an explicitly selected local output location.
- [x] Return a valid terminal completion and settle the owned client/listener.
- [x] Provide a discoverable skill and complete invocation/privacy guidance.

**Agent-recommended implementation details:** use an ephemeral IPv4 loopback
port and a per-run dummy credential passed only to the owned child; accept one
generation request with finite body/time limits; do not capture headers,
forward requests, reuse existing endpoints, print raw prompts to stdout or
overwrite existing output. Keep real harness instructions/plugins enabled.
Document that local hooks/MCP processes still have their ordinary capabilities;
loopback binding is not an operating-system egress sandbox.

### Phase 3 - Qualify and publish
- [x] Exercise the public script boundary using synthetic requests/clients.
- [ ] Complete independent review, required repository checks and the normal
  implementation PR merge.
- [ ] Reconcile the effort's entire Plan and Validation Plan, then archive.

## Validation Plan

All implementation checks below are agent-recommended ways to prove the
operator's requested boundary.

- [x] Byte-for-byte output preserves Unicode, messages, instructions and tools
  from a synthetic client request; stdout contains status/path metadata only.
- [x] Streaming and non-streaming requests receive valid terminal completions.
- [x] The actual launcher directs only its owned child to its ephemeral
  loopback listener without changing user configuration.
- [x] Foreign credentials, wrong routes, oversized/truncated requests and
  request timeouts do not persist a purported successful raw capture.
- [x] Existing outputs are preserved; listener/client failure is explicit;
  owned resources have a bounded lifetime.
- [x] The skill is packaged/discoverable, includes Windows and POSIX usage,
  and clearly excludes shared-endpoint logging and provider-side additions.
- [x] Focused contained tests, touched-code lint and applicable repository
  guards pass; public artifacts contain only synthetic examples.
- [x] No real operator prompt is captured or published as validation evidence.

## Proposal

Ship a small, standard-library capture script with an on-demand skill, rather
than a runtime, daemon, proxy, global hook or automatically enabled logger.
Selecting an output location explicitly requests intentional local raw
persistence. Operators control retention and must not publish the resulting
private diagnostic artifact. The tool does not promise to reproduce additions
made by GitHub's hosted provider.

## Journal

### 2026-10-29 - Proposal
- Confirmed the upstream effort name and claimed #9581.
- Captured the operator's requested raw-persistence and no-shared-service
  boundary. Implementation awaits the proposal's review and merge.

### 2026-10-29 - Reviewed intent and implementation
- Proposal #9583 merged after independent Copilot review and required CI.
  Corrected the review's documentation-impact finding before merge.
- Implemented the standalone capture script and discoverable skill in
  `customizing-copilot`, with explicit local output and no shared endpoint mode.
- Seventeen synthetic controls passed under the contained Windows runner.
  An additional actual native CLI run in a disposable synthetic home/workspace
  captured the fixture's instruction marker and returned normally. No operator
  harness or raw request was published. POSIX native qualification is not
  claimed by these Windows results.
- Independent implementation review reported no significant issues. Source
  skill, customization, headless-launch and touched-code lint checks passed.
  Implementation PR merge and final effort reconciliation remain.
- Implementation review identified relative executable selection and rejection
  diagnostics improvements; both received focused regressions. CI also found
  the new bounded CLI launch missing from the lifecycle census; classified its
  existing finite-worker behavior without weakening the guard.
- All twenty controls, including actual native CLI qualification, passed after
  those corrections; the lifecycle census and touched-code lint passed.
- The next review found three additional ambient GitHub credential variables
  missing from the documented child filter. Added them and extended the
  environment regression; no broader harness credential sanitization is claimed.
