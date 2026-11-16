# Contract-compatible retained writers

Part of [Task Relevance Reconciliation](README.md), continuing
[#12696](https://github.com/ThomasMichon/copilot-extensions/issues/12696)
and [#9532](https://github.com/ThomasMichon/copilot-extensions/issues/9532).
**Status: plan reviewed in #12884; implementation qualification remains open.** This slice repairs retained writer
compatibility, not task policy, production activation or unknown-send recovery.

## Intent and existing gap

The [service-model intent](../../../visions/plugin-services/README.md#version-skew-tolerant-contracts)
and [contract-evolution pattern](../../../docs/patterns/service-contract-evolution.md#durable-state-must-not-require-its-original-executable)
separate compatible behavior from package/build equality. A retained
conversation must not require keeping its original executable forever.
Original build identity remains useful provenance, not a lifetime execution pin.

Bridge's `WorktreeWriterRequirement` currently embeds an installation root and
runtime generation. Its acquisition and keyed-creation journals retain an
executable prefix; `acquisition_worktree_writer._qualify` requires the executing
writer's identity to equal the historical identity. Keyed creation's
`creation_worktree_writer.retained` reuses that original command. These protect
real ownership and serialization requirements, but also prohibit a semantically
compatible newer writer. Removing identity checks without replacing their
contract meaning would lose those protections.

This is a bounded refinement of the existing native-wake settlement and
original-conversation recovery work, not an all-plugin pin audit. The
[settlement plan](native-wake-settlement.md) still owns admission history and
durable original-key exclusion. A compatible writer does not settle a prompt.

## Proposed implementation sequence

1. **Separate provenance from requirements.** Define the versioned retained
   record/writer semantics within the existing Worktrees capability boundary:
   supported record generations and required incarnation, relationship,
   native-registration and retained-recovery behavior. Preserve the historical
   installation/build, journal, creation nonce, workspace and native identity.
   The executing owner has its own current identity; do not overwrite birth
   provenance or use a package-version comparison as capability negotiation.
2. **Expand the owning serializer first.** Worktrees accepts a compatible
   executing writer while retaining incarnation, relationship, head revision,
   native activation and original-operation checks at mutation. Reuse its
   existing installation/runtime selection and IPC serialization owner; do not
   add a parallel daemon, executable registry or prerequisite RPC.
   Exact-build startup selection may identify the daemon being invoked, but
   cannot become the durable record's compatibility requirement.
3. **Adapt existing records explicitly.** Interpret qualified legacy
   build-bound requirements and saved prefixes through a documented adapter.
   Validate their actual schema and semantics, not just their age or build
   label. Keep immutable creation/acquisition facts and request digests intact;
   record any necessary adoption in the existing owning journal without
   bypassing its immutability rules. Concurrent adoption/recovery must retain
   one current serializer and the same original operation. Missing or
   contradictory legacy evidence refuses that operation before effects.
   Do not silently upgrade an operation to guarantees its original writer lacked.
4. **Migrate Bridge consumers conditionally.** Use the compatible selected
   executor for both acquisition-retained and ordinary keyed-creation paths,
   including cold resume and fresh native-activation corroboration. Legacy
   records must not launch their saved executable prefix as a fallback.
   Preserve existing compatible readers and ordinary creation semantics; no
   parser-error retry, replacement conversation, automatic resend, generation
   reset or reset policy clock. Unknown sends retain their existing settlement
   owner and fences.
5. **Qualify upgrade and rollback before adoption.** Document supported legacy
   schemas, required semantics and unsupported outcomes. A supported prior
   writer must honor any persisted adoption/exclusion state; an incompatible
   rollback must refuse the affected mutation before effects rather than
   reopen an original key or erase adoption. Do not equate that operation-level
   refusal with a ban on installing newer code. Publish receiver support before
   enabling callers; release, authorized adoption and live continuation are
   separate facts.

The [authoritative replacement contract](../../../visions/installer/README.md#authoritative-runtime-replacement)
remains binding: safe incoming data/schema compatibility and complete current
selection define readiness; original provenance or best-effort outgoing
recovery cannot veto installation. Retained conversation/session hosts and
unresolved effects remain protected. This plan adds no termination, deletion,
credential, deployment or new lifecycle authority.

## Validation plan

- [ ] A legacy keyed-creation record and a legacy acquisition record each
      reach a compatible newer Worktrees serializer through the actual Bridge
      process/IPC boundary without executing the saved old prefix.
- [ ] The same native conversation can record a new proved activation after
      its earlier activation ended. Workspace incarnation, original provenance,
      relationship and request history stay unchanged; no old Start is replayed.
- [ ] Unsupported contracts, corrupt/ambiguous legacy requirements, foreign
      installations, changed creation nonces and stale heads refuse before
      mutation or native effects. A compatible newer generation is not itself
      a refusal reason.
- [ ] Independent-process concurrent adoption, restart and lost responses
      preserve the same original operation and one admitted writer; unresolved
      outcomes remain unknown rather than being retried.
- [ ] Current/prior callers, providers and supported rollback preserve ordinary
      creation, retained registration and original-key exclusions. An unsupported
      rollback cannot mutate adopted records, but does not veto incoming install.
- [ ] A bounded isolated retained-conversation flow reaches a second turn and
      normal closeout through supported front doors. Source tests, release,
      runtime selection and native continuation are reported separately.

Use focused unit controls for semantic selection and record adaptation.
Put real process/IPC and native lifecycle qualification in the existing selected
integration portfolio, not an exhaustive new required-CI matrix. No passing
control is permission to replay an unresolved original send or mutate an
unrelated retained conversation.
