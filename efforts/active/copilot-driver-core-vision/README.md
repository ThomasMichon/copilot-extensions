# Copilot Driver Core Vision

- **Slug:** `copilot-driver-core-vision`
- **Repo:** copilot-extensions
- **Branch(es):** independent vision-revision PR
- **Created:** 2026-11-05
- **Status:** Active
- **Vision:** [Copilot Driver](../../../visions/plugins/agent-remote-driver/README.md)
- **Umbrella issue:** #10765
- **Sub-issues:** Existing #10636, #5930 and #5898 retain their owners.

## Guiding Intent

Record and land the standing architecture for one minimal Copilot Driver app:
Session Host, mux wrapping, quickly installable receiving distribution and a
common ACP broker for both launch modes. This finite effort owns the vision
revision only. Runtime extraction and adoption remain in #10765 and their
existing component owners.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Vision editor | Owns the coherent vision revision and PR | Source worktree |

## Coordination

- **Topology:** one source PR, independent of implementation PRs.
- **Host (owns PRs):** Vision editor.
- **Delegates:** none.
- **Handoff:** #10765 carries runtime realization after reviewed intent.
  Existing portable receiving support #10636 and its implementation PR #10748
  are not replaced or superseded.

## Context

[Session Hosting](../../../visions/session-hosting/README.md) defines provider
mechanics separately from durable agency. Its Driver provider now has an
independently installable app boundary. Bridge remains coordination, Worktrees
remains agency, and Manager remains a presentation/installation client.

The [portable receiving effort](../portable-bridge-receiving-support/README.md)
already owns compact receiving composition; #5930 owns driver mutation custody.
This revision preserves both and records the app factoring rather than opening
a competing launcher. The default-mode experiment remains independently gated.
The app follows [authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
and the existing distribution, service-lifecycle and supported-skew patterns.

## Request

Public-safe capture of the operator's request; private coordination reference
omitted, remaining architectural intent retained:

> In alignment with related hosting work, we need to update our architectural
> vision to factor out a core "copilot driver", which handles the bare minimumn
> for mux-wrapping and ACP compatibility.
> The existing "agent-remote-driver" is a reasonable namespace for this. We want
> to move session-host out of agent-bridge, and move the mux wrapper out of
> agent-worktrees and worktree-manager, into this new "core" app. The app can be
> "installed" on a host machine, but its objective it to produce a
> quickly-installable redistributable package which agent-bridge or even
> agent-worktrees directly can hoist into a target machine to launch a durable
> copilot into a remote location, whether another machine, a container, or a
> codespace. If purely headless, then the result is session-host directly owning
> a Copilot process, and serving ACP over TCP/HTTP with a port reservation and
> process record. If headed, then the result is a mux instance wrapping copilot
> in interactive mode, pre-seeded with a prompt and pre-trusting the extension,
> with the extension injected via --plugin-dir into the process; the extension
> then connects to a session-host, so session-host then acts as the broker for
> both cases. The end result should be a session-host serving ACP at a HTTP/TCP
> endpoint either way, with the port reservation, process info, session info etc
> available to a caller.

Follow-on operator direction, captured separately from current source scope:

> After you update the vision, I'm going to have you make and drive and
> implementation effort to do the necessary refactoring. Your goal will be that
> worktree-manager's launch-sessoon flow, agent-worktrees embody/copilot,
> agent-bridge create/resume, etc. all use the new agent-remote-driver core (which
> we could rename to agent-driver). Following the session-host model, these
> processes remain alive so long as their copilot processes need to be. For CLI
> that's until the user closes it, and for ACP that's until stopped by their
> callers or a duration after the agent goes idle without pending background
> tasks, agents, or schedules.

## Scope Approvals

Source vision revision and normal issue/PR publication only. No extraction,
installation, runtime replacement, guest activation, endpoint/control claim,
session migration, broadcast, shutdown or default-mode change is authorized
by this effort. The follow-on direction specifies the next refactoring
objective; implementation is not started by this vision slice. A possible
`agent-driver` rename remains a later naming choice, not a package rename here.
Review of the plan is not rollout permission.

## Plan

### Phase 1 - Coherent architectural intent

- [x] Identify existing receiving, control, hosting and mux owners.
- [x] Add the canonical Driver app vision and reconcile hosting/client maps.
- [x] Validate request capture, stable links, structure and public-safe scope.
- [ ] Land the vision revision after review and required CI.

### Phase 2 - Source closeout

- [ ] Confirm #10765 and existing runtime owners remain open and unchanged.
- [ ] Record the merged revision, mark this finite effort Done and archive it
  through the source PR flow.

## Validation Plan

- [x] Check that both modes lead to one Session Host ACP-compatible endpoint.
- [x] Check distribution, scoped launch trust, port/process/native-session
  evidence, target durability and standalone/hoisted use against the request.
- [x] Check that no active sibling requires Manager-owned mux mechanics,
  Bridge-owned Session Host or a hostless headed Driver session.
- [x] Run changed effort/vision structure and existing documentation checks.
- [ ] Obtain final-head review and required CI without claiming runtime proof.

## Proposal

Agent-recommended validation for future implementation, not tests run here:
fresh standalone/hoisted package installation on supported platforms; direct
ACP and real mux/SDK launch with `--plugin-dir` and scoped extension trust;
exact multiline seed acceptance; truthful port reservation, native binding
and process readiness; survival after caller cleanup; broker reconnect and
exclusive driver/human takeover; exact native restore without seed replay;
supported skew/rollback; preserved repository exposure policy; independently
selected terminal attachment; and scoped keeper retirement after replacement.
Measure package size and cold/warm delivery-to-ready time rather than assuming
quick installation from a file-copy receipt. Runtime implementation must
select actual supported protocol contracts and migration adapters before
effectful consumer changes.

The follow-on caller-convergence matrix covers Manager launch-session,
Worktrees embody/copilot and Bridge create/resume, proving each consumes the
same core. Lifetime controls must cover user-closed CLI sessions with no
automatic idle expiry, caller-stopped ACP sessions, ACP idle grace with and
without pending tasks/agents/schedules, and caller loss without premature
child retirement. The maintenance wind-down budget is not this ACP idle policy.

## Journal

### 2026-11-05 - Vision proposal

Opened #10765 for the app boundary, retaining #10636 portable delivery, #5930
driver control and #5898 mux/AHP integration ownership. This source-only slice
records intent before extraction. No runtime or installation behavior changed.
Related receipt work #10749 preserves distinct mux presence, native
session/process binding, input readiness, prompt acceptance and completed
restoration, with optional selected-runtime capability detection. Core factoring
retains those semantics without adding default waits, transcript scans or a
rival launch journal.

Changed effort/vision structure passed for all eleven staged documents;
documentation consistency and CRLF-aware whitespace passed. The revised
Driver vision and affected hosting/client bodies were checked against the
request. No plugin payload, runtime, manifest or dependency changed, so runtime
tests and a plugin changefile are not part of this documentation-only slice.

The parent fabric's idle reclamation rule follows the same mode-specific
contract: CLI detach/disconnect is not expiry, and ACP disconnect does not
bypass configured idle grace or pending work. Orphan collection remains
separate from authorized provider lifecycle retirement and claim settlement.
