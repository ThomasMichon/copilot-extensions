# Ordered reliability tracking and diagnostic receiving

Canonical campaign: [Backlog Reconciliation](README.md).
Source: [#4231](https://github.com/ThomasMichon/copilot-extensions/issues/4231),
including its complete inherited #4223 diagnostic lane and both tracking
refinements. Diagnostic receiving home:
[Truthful Terminal State](../agent-bridge-truthful-terminal-state/README.md).

## Status and scope

This is the accepted **tracking map with planned diagnostic receiving**.
#4231 is CLOSED/NOT_PLANNED after actual direct D4231 receiving in OPEN #2464,
reviewed RD4231 ROOT delivery and separate whole-source judgment. The
[public explanation](https://github.com/ThomasMichon/copilot-extensions/issues/4231#issuecomment-6839200603)
distinguishes administrative retirement from implementation or resource release.

The original TRACK task does not require every future implementation to be
Done. Conversely, a five-row map cannot erase the separately inherited
Host-child diagnostic task. Keep existing owners and all original source,
native, claim and resource conditions; do not activate the Draft receiving
ROOT or appoint an implementer.

## Five areas in the literal original order

All five are **PARTIAL** at their actual observed tiers. Presentation order
does not create whole-programme serial prerequisites.

| Order | Area and delivered credit | Remaining work and actual existing owner |
|---|---|---|
| 1 | **Process custody:** original-owner launch/recovery admission, exact Host/child/process-birth/generation registration, and a survivable Host owning its ACP child are real source. | OPEN #2464 retains local/provider/remote/privileged lifetime, active/unknown child, cancellation and safe-retirement consumers. #7237's explicit external-conversation relationship claim is not peer-process ownership. PID, endpoint or relationship presence is not complete custody. |
| 2 | **Truthful turn continuity:** per-turn result/tool/error persistence and terminal publication exist; actual tool-progress streaming and represented non-aborting steering exist at different supported boundaries. | OPEN #2464 retains exactly-once terminal, dormancy/provider/native qualification. OPEN #4029 directly retains ongoing progress **and** durable attributed results **and** active-turn steering; hosted busy input still queues. CLOSED/NOT_PLANNED #4171 is historical provenance, not a required live token. |
| 3 | **Cold-launch diagnostics:** classic child stderr has bounded rolling/event persistence; #547/#553/#559 supply finite same-conversation retry/exhaustion and permitted controlled fallback. | The actual supported ACP Host CHILD still has stderr=DEVNULL. The separate 4096-byte Host bootstrap tail is not that child's startup output; host-mode frontend stderr tail is explicitly empty. The whole diagnostic lane is now directly retained by D4231 in OPEN #2464 and RD4231 in its existing Draft ROOT; product acceptance remains unchecked. |
| 4 | **Crash isolation:** separate Host/child/frame custody, child-exit/liveness publication and per-turn error settlement exist. Frontend disconnect and hard Host death are different cases. | OPEN #2464 retains child EOF/reader-loss terminal/no-duplicate/Host-survival cases and #9025's still-unexplained two-Host loss. Native isolation cannot be inferred from separate classes or one happy drill. #954 remains a venue-only validation interface with actual P3 reservations. |
| 5 | **Durable connection ownership:** the separate Connection Owner has actual native channel-WORK exclusion and persistent legacy process-birth barriers, not only passive readiness/beacons. | OPEN #4309 remains the whole endpoint/credential/prefetch/tenant/idle-pin/mux-evaluation/cardinality/cold-demand/reboot keeper. New lock source is not proof every actual credential-consumer or remote-listener case is qualified. Its existing owners and nonexpiring custody remain. |

### Attributable source and merged receipts

The inspected immutable source is
[`9d194f7c20fdf2303bc5411396bb9d958cffbb3b`](https://github.com/ThomasMichon/copilot-extensions/tree/9d194f7c20fdf2303bc5411396bb9d958cffbb3b).
All 25 selected paths matched the study's final Dev and Main correspondence;
that establishes selected source applicability, not installed behavior.
Those comparisons preceded the receiving amendment; they do not assert that
the new RD4231 planning text was already released.

- **Custody:** Bridge's
  [launch admission](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-bridge/src/agent_bridge/session_host/launcher.py#L18-L48),
  [exact registration](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-bridge/src/agent_bridge/session_host_launch_custody.py#L12-L40)
  and [shared Host ownership](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/libs/copilot-driver-core/src/agent_remote_driver/session_host/launcher.py#L211-L256)
  support row 1. #7364's merged external-relationship source is narrower,
  not a peer-process custody receipt.
- **Continuity:** actual
  [terminal persistence](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-bridge/src/agent_bridge/session_prompts.py#L531-L598),
  [progress streaming](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-bridge/src/agent_bridge/session_streaming_cli.py#L98-L132)
  and [represented steering](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-bridge/extensions/agent-bridge/delivery.mjs#L209-L227)
  are distinct from the
  [hosted busy-input queue](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-bridge/src/agent_bridge/session_prompts.py#L311-L360).
  #3058 and #6420 are actual merged finite source, not whole native acceptance.
- **Diagnostics:** follow the admission shim to the actual shared
  [ACP child spawn](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/libs/copilot-driver-core/src/agent_remote_driver/session_host/launcher.py#L533-L576),
  then distinguish
  [classic persistence and absent host-mode tail](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-bridge/src/agent_bridge/acp_client.py#L1305-L1369).
  #547/#553/#559 are actually merged. Historical #553/#559 head rollups now
  show FAILURE, so no fresh-green or native conclusion is asserted.
- **Recovery and isolation:** existing
  [no-recreate custody](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-bridge/src/agent_bridge/session_resume.py#L125-L155),
  [finite exhaustion/refusal](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-bridge/src/agent_bridge/session_resume.py#L555-L626),
  [permitted fallback identity](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-bridge/src/agent_bridge/session_resume.py#L695-L733)
  and [turn-aware Host lifetime](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/libs/copilot-driver-core/src/agent_remote_driver/session_host/host.py#L483-L550)
  remain bounded source credit, not new fault execution.
- **Connection ownership:** actual
  [native channel-WORK exclusion](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-codespaces/src/agent_codespaces/owner_beacon.py#L155-L242)
  and [legacy birth barriers](https://github.com/ThomasMichon/copilot-extensions/blob/9d194f7c20fdf2303bc5411396bb9d958cffbb3b/plugins/agent-codespaces/src/agent_codespaces/owner_identity.py#L191-L226)
  supersede the old beacon-only work-owner inference. #4309's whole consumer
  contract remains independent.

PR metadata is not PR-description/review-body admission or a new native test.
No bot-review body/diff or new operational result was acquired by this study.

## Scoped dependencies and parallelism

1. Establish exact Host/child/conversation identity and allowed custody before
   attributing terminal state, diagnostics or recovery. This is a bounded
   prerequisite, not completion of every process programme.
2. Use truthful persisted/event outcomes to judge disrupted turns. Native
   isolation, result handling and active steering can progress independently.
3. Child diagnostics assist crash attribution; unfinished diagnostics neither
   grant recreation/teardown nor block every independent isolation slice.
4. Connection/relay/credential admission composes through #4309's existing
   authority. It is not a new whole-connection prerequisite before all earlier
   areas may progress, and transport sharing is not exclusive work authority.
5. Preserve the existing truthful-state ROOT's independently reviewable
   Phase A/B lanes; Phase C follows their own exits.

## Complete diagnostic receiving contract

The actual final diagnostic keeper is **OPEN #2464**, under its existing
truthful-state, Host/shared-driver, process, provider, relay, native and
source/resource custodians. Its related ROOT remains **Draft**.

The direct issue D4231 Plan/local/global Validation and Coordination retain:

- Authoritative actual supported Host CHILD startup-output relay through the
  Host and consumer persistence/query, bounded content, appropriate redaction
  and correct child/Host/process/session/native/provider attribution. Host
  bootstrap, helpers or classic-only stderr do not substitute.
- Credited finite #547/#553/#559, bounded same-conversation retries and
  controlled fallback; no repeated implementation of the delivered ladder.
- Default no-recreate, exact native/provider/remote/privileged/cold-recovery
  custody, terminal launch-policy/cleanup-pending refusal, bounded exhaustion,
  truthful STOPPED/error and retained unknown authority/cleanup holds.
- Where current authority actually permits opt-in fresh fallback, old/new
  native identity **and** `context_dropped=true`, not replacement against
  an owner-fenced no-recreation path or ambiguous-delivery replay.
- Actual child-to-Host relay/persistence/query/bounds/redaction/attribution
  coverage plus finite success/exhaustion/allowed-fallback/failed-fallback/
  terminal-refusal controls at the original consumer and native tiers.
  No new every-host, all-platform or whole-programme gate.
- The entire #2464 incumbent, discussions, check states, actual claims and
  independent #4029/#4309/#954/#1138/#1460 boundaries unchanged. #7237 remains
  an external relationship owner, not a newly appointed process custodian.

The complete original retained from #4223 is:

> Persist child startup diagnostics and recover stalled resumed sessions through bounded retries followed by a controlled fresh-session fallback.

The corresponding RD4231 Plan/local/global Validation and Coordination are
directly retained in the existing ROOT through merged #12317,
`489f990945c01d9ad3a194c40baf35e441909a14`. Product rows remain unchecked and
the ROOT stays Draft. No launch, resume, fallback, stream capture, native/test,
process, credential, service, runtime, venue, replay or cleanup is authorized.

## Tracking acceptance and actual publication

The T4231 map preserves every original/refinement clause and literal
alternative, not only the five area names. The source-specific route was
separately verified:

1. #12317 actually merged after current-head approval, successful required
   checks and zero findings/threads. All five reviewed/merged/current document
   blobs matched; the complete 245-line receiving ROOT was read back.
2. The complete direct D4231 lane and full #4223 original were appended to
   OPEN #2464. Its whole 50,418-character body and unchanged discussion were
   read back; the entire 45,898-character incumbent prefix was preserved.
3. The five ordered status/receipt/dependency/owner rows were accepted in
   #4231's whole 9,646-character body, preserving its incumbent and both
   refinements. Three new checked rows describe ADMINISTRATIVE TRACK
   acceptance; all original product checkboxes remain unchanged.
4. #4231 was separately closed NOT_PLANNED after fresh provenance/custody
   checks and exact public explanation/provider-state readback. #2464
   stays OPEN; its seven new diagnostic product rows and the ROOT's four
   RD4231 rows remain unchecked. No physical or source claim was settled.

This sequence is specific to the proposed changed ROOT and composite source,
not a universal new review/ACK ceremony. No new personal ACK or numeric-live
token condition was found. Acceptance of TRACK does not finish the operational
keepers; a map alone would not have disposed of the concrete diagnostic lane.
