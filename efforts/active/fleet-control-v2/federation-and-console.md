# Push federation, target-owned control and console choice

Back to [Fleet Control V2](README.md). This captures an operator clarification
and an **agent-recommended design decomposition**, not a runtime implementation,
source-ownership transfer or deployment grant.

## Request

Operator clarification, verbatim:

> Right, the Gateway is a federation service. It will receive pushes from other machines instead of having to pull, so its "All" view will have accurate information near instantly. Any given machine should still source its own info directly, but will server other machines' info from the Gateway instead of the other machine directly.
>
> We'll also consider a scheme where we replace usage of SSH with Gateway making tunneled requests back to worktree-manager or agent-worktrees on each machine to create worktrees and agents. This would be more efficient than sending those requests over SSH; the original caller can elect to call into the TTY via SSH or elect to use some alternate console host protocol, such as a JS-based console host in the Harness Companion or other Web UX. We can pull off the maneuver that VS Code does and use a lighter-weight TTY-only protocol and renderer than full SSH.

The local-direct/remote-federated observation split is the stated direction.
Gateway-mediated creation and lightweight console reach are a scheme to
consider, not a selected wire protocol, a demonstrated performance result or
permission to remove SSH. The no-other-agent-coordination constraint remains.

## Observation: local direct, remote federated

- Read the current machine's facts from its existing local owning services.
  Do not route local reads through the Gateway merely because it holds a
  projection of that origin.
- Read other machines' facts from the selected Gateway's pushed current view.
  A remote fleet-view request does not start SSH, scrape another machine or
  independently poll each origin. Explicit diagnostic introspection remains
  a different operation, not a hidden fallback.
- Compose **All** from the local owner and the Gateway's authorized remote
  observations, excluding the Gateway copy of the local origin. Preserve
  canonical logical machine/container identity rather than deduplicating by
  hostname, physical host or display label.
- Publish promptly after the source commits. The intended near-instant user
  experience needs a measured source-commit-to-view latency budget during
  implementation, not a promise of simultaneous observations across hosts.
  Disconnection, publication lag, replay gaps and incomplete source coverage
  stay visible; an offline origin is neither empty nor necessarily stopped.

This extends the existing [central-state contract](central-state-backing-store.md)
and consumes #3761 origin projections. It does not make the completed point
reader a complete roster, snapshot or federation implementation.

## Control: route named requests to the target owner

Evaluate using an already-authenticated outbound machine channel for named
worktree allocation and agent launch instead of starting a separate SSH command
session. The target's existing worktree/Manager/session-host operation boundary
performs the work; the Gateway does not acquire its own allocation database,
claims transaction engine or process supervisor.

Reuse current scoped admission, qualified target identity, operation correlation,
idempotency and target receipts. A Gateway delivery acknowledgement is not
target acceptance, successful launch or completed opening-context delivery.
Lost replies and retries must not create another worktree or agent. Observation
freshness is useful for presentation but never substitutes for current target
authorization or execution-side fencing.

Do not broaden a named service route into arbitrary commands, URLs or generic
socket forwarding. Existing connector/enrollment and target-owner scopes stay
with #5789/#6443, #3761 and #352; this proposal does not assign their files.

## Presentation: choose attachment independently

After target-owned creation, let the caller choose SSH terminal attachment or a
supported lightweight terminal-only host channel and renderer. A companion/web
client can present the terminal without implementing full SSH or hosting another
Copilot process. The VS Code comparison describes the architectural shape, not
an adoption of a specific implementation or protocol.

Both paths refer to the same target-owned execution and reattachment identity.
Keep input, resize, output observation, detach/reconnect and session retirement
distinct. Closing a view must not retire accepted work, and an output observer
must not acquire input or lifecycle permissions. Existing session-host providers
own terminal/process mechanics and supported recovery; the Gateway and renderer
are clients of those capabilities.

Choice of presentation does not change launch context, plugin readiness,
credential-provider semantics or durable worktree ownership. Unavailable
capabilities produce an explicit outcome rather than silent SSH fallback or
another agent launch. SSH remains supported; no default-mode replacement or
installed console host is selected by this amendment.

## Validation obligations before implementation or adoption

The following are **agent-recommended** obligations for a subsequently assigned,
reviewed implementation, not completed evidence:

- Prove local reads use the local owner, remote reads use only the Gateway,
  and All contains one row per qualified entity without a duplicate local
  origin. Count actual transport calls; a mocked final result is insufficient.
- Exercise real origin commits, push delivery and independent clients;
  measure end-to-end publication latency under a stated workload. Include
  delay, disconnect, stale origin generations, gaps and partial coverage.
- Preserve scoped field/entity grants, authorization before lookup/aggregation
  and revocation without exposing global positions or unrelated activity.
- Prove a tunneled create/launch reaches the existing target owner, with
  lost-ack retry, denial and stale-target cases that do not duplicate effects.
- Attach through each supported presentation to the same execution. Prove
  input/observe permission separation, terminal interaction, bounded buffering,
  slow-reader isolation and detach/reconnect without relaunch or retirement.
- Compare repeated Gateway control with the existing SSH path under matched
  conditions before claiming an efficiency improvement. Record unsupported
  platform/host capabilities honestly; preserve standalone SSH behavior.

The existing isolated observation implementer can propose scoped-view/recovery
contracts and their local proofs. Producer integration, target control,
transport, Manager/console-host implementation and live adoption still require
their own explicit source assignment and normal review. No service, provider,
credential, enrollment, lease or deployment changes follow from this document.
