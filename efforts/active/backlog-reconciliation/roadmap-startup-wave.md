*The following contribution was assisted using Copilot.*

# Selected Startup Wave: Preserve Declared Launch Scope

Parent: [Backlog Reconciliation](README.md), Phase 2b.
Integration: [stabilization roadmap](stabilization-roadmap.md) and
[cross-wave sequencing](roadmap-sequencing.md).

**Status:** Agent-recommended bounded execution proposal, not an implementation
assignment or a whole-harness readiness claim. It converts two already
qualified source findings into explicit priority, ownership, entry and exit
decisions. The historical twelve nominations remain useful; this is a later,
non-exhaustive addition, not a title-based reranking of the backlog.

## Evidence and priority

The complete current originals for #12657 and #12691 have no comments.
Their original/discussion inputs and complete provenance are unchanged from
the preceding qualification. Seven selected implementation blobs are also
unchanged in current Dev at
[`a3c4801adf25f2bf461f47d8b1833c5bb2eeef4d`](https://github.com/ThomasMichon/copilot-extensions/tree/a3c4801adf25f2bf461f47d8b1833c5bb2eeef4d).
This reuses the admitted static findings rather than repeating a product trace.
No product test, native launch or installed observation was performed.

| Finding | Selected immutable source |
|---|---|
| Keyed Bridge policy | [Request model](https://github.com/ThomasMichon/copilot-extensions/blob/a3c4801adf25f2bf461f47d8b1833c5bb2eeef4d/plugins/agent-bridge/src/agent_bridge/session_start_models.py), [allocation forwarding](https://github.com/ThomasMichon/copilot-extensions/blob/a3c4801adf25f2bf461f47d8b1833c5bb2eeef4d/plugins/agent-bridge/src/agent_bridge/workspace_creation.py) and the [active HTTP route binding](https://github.com/ThomasMichon/copilot-extensions/blob/a3c4801adf25f2bf461f47d8b1833c5bb2eeef4d/plugins/agent-bridge/src/agent_bridge/routes/sessions.py). The selected model/forwarding/allocation path does not carry `no_pair`; existing keyed request/conflict handling is credited, not discarded. |
| Opted-in New readiness | [Configuration](https://github.com/ThomasMichon/copilot-extensions/blob/a3c4801adf25f2bf461f47d8b1833c5bb2eeef4d/plugins/agent-worktrees/src/agent_worktrees/config.py), [preflight](https://github.com/ThomasMichon/copilot-extensions/blob/a3c4801adf25f2bf461f47d8b1833c5bb2eeef4d/plugins/agent-worktrees/src/agent_worktrees/launch_preflight.py), [creation caller](https://github.com/ThomasMichon/copilot-extensions/blob/a3c4801adf25f2bf461f47d8b1833c5bb2eeef4d/plugins/agent-worktrees/src/agent_worktrees/worktree_creation.py) and [application admission helper](https://github.com/ThomasMichon/copilot-extensions/blob/a3c4801adf25f2bf461f47d8b1833c5bb2eeef4d/plugins/agent-worktrees/src/agent_worktrees/application_admission.py). The selected opted-in helper requires Machines and requests `--required --caller-owned-readiness`, without explicit launch selection or an empty-selection return. |

| Selected contribution | Consequence and proposed priority | Evidence confidence and limit |
|---|---|---|
| #12657: carry `no_pair` through keyed Bridge workspace creation | **P1 startup contribution:** a declared Dispatch pool policy cannot be represented at its actual Bridge allocation boundary. The safe present outcome is refusal, not silently creating a paired workspace. Restore the requested supported path without weakening that refusal. | The selected request model, allocator and active HTTP route support the source gap. This is not a new measured outage, a characterization of every transport, or proof about an installed daemon. |
| #12691: separate explicit New prerequisites from general application requirements | **P1 investigation/source contribution within the readiness opt-in:** unrelated optional application health can prevent creation of the authoring workspace needed to repair it. The default-disabled option means this is not a claim that every New launch is broken. | The selected active caller reaches a helper that requires Machines and requests the general required-module set; no explicit empty-launch-set exit is present there. Other launch modes and runtime incidence remain unqualified. |

Both priorities use the existing rubric's material startup/recovery consequence,
not age or a shared plugin name. Keep confidence separate from severity.
A changed consumer or an existing sufficient delivery can narrow the source
work to qualification; it must not trigger another implementation of a solved
predicate.

The first proposed contribution targets the Bridge vision's
[workspace allocation and session embodiment](../../../visions/plugins/agent-bridge/README.md#workspace-allocation-and-session-embodiment)
and [negotiated contract envelope](../../../visions/plugins/agent-bridge/README.md#negotiated-contract-envelope):
the selected workspace and replay semantics survive composition, while required
semantics are selected before effects. The second targets the Installer vision's
[one-way, independent installation boundary](../../../visions/installer/README.md#purpose--intent)
without weakening [selected repository onboarding](../../../visions/installer/README.md#staged-repository-onboarding).
An application's setup requirements and an ordinary launch's explicit
prerequisites remain distinct. Neither contribution changes authoritative
runtime replacement, service retirement or durable user-state preservation.

## Existing homes and custody

| Original tracker and existing home | Ownership retained | Remaining home/acceptance uncertainty |
|---|---|---|
| #12657, the original Bridge/Dispatch request | Existing workspace-allocation and Dispatch startup owners; this proposal appoints no implementer and does not treat the reporter as an implementation claimant. | No whole-request effort intake is asserted. Keep the original issue as the canonical coordination token and use the normal contribution/claiming flow; this proposal creates no claim or transfer. A related programme is not a receiving receipt. |
| #12691, the original explicitly claimed launch-scope correction | The original source claimant, selected Machines installation owner and existing Worktrees launch owners | Keep that claim and tracker. No new owner, installation controller or accepted transfer is created by this proposal. |

These are retained original work homes, not a duplicate umbrella. Neither
original retires as redundant, and a reviewed proposal does not fulfill
another party's unfinished request.

## Parallel wave and scoped prerequisites

The two rows are independent. There is no edge from completing all Bridge work,
all Machines readiness work, the Installer programme or #11722 to either row.
The following are **within-contribution** precedences, separate from the
145 historical relationship rows; they add no whole-issue cycle or global
serialization requirement.

| Slice | Entry and first useful contribution | Exact prerequisite to its consumer | Finite source exit |
|---|---|---|---|
| A: keyed workspace policy | Through the existing Bridge/Dispatch owners, characterize the actual keyed request and allocation seam using #12657's fresh, exact-key replay and conflicting-replay cases. Preserve safe refusal while extending the selected contract. | The request model, immutable request binding, allocator forwarding and advertised protocol must uphold `no_pair` before the Dispatch headless consumer relies on it. Unsupported older daemons refuse before workspace creation. | The selected choice reaches Worktrees as `--no-pair`; exact-key replay recovers the same result; changing the choice under the same key refuses; the actual Dispatch headless path and old-daemon refusal have focused regression evidence and normal current-head review/CI. Do not silently reinterpret an existing request. |
| B: explicit launch prerequisite selection | Through #12691's original claimant, characterize empty and explicitly declared launch requirements at ordinary New admission. Credit the existing strict boolean opt-in, knowledge/profile checks and caller-owned read-only budgets. | Only a nonempty, valid explicit launch selection reaches its selected Machines owner and complete module closure. An empty set exits without needing Machines or probing unrelated applications; setup/repair keeps its own selection. | Empty selection never queries optional Vault/Index/MCP readiness; a declared prerequisite truthfully blocks when unavailable and passes only with its complete dependency closure; malformed selection refuses. Preserve knowledge/native prerequisites, source-only/existing/recovery exemptions, caller ownership, bounded deadlines and unchanged setup/repair behavior, with focused regressions and normal current-head review/CI. |

Use the existing owners and contract-evolution pattern, not a second bus,
generic prerequisite framework or runtime ledger. The original issue determines
the actual source acceptance. Package version equality is not a substitute for
the required selected Bridge semantics; no unsupported legacy retry is implied.

### Source, release and selected-consumer exits

1. **Entry:** reconcile the current original, changed source and actual claim
   through the existing owners. Preserve all independent source, review and
   resource restrictions. This research proposal supplies no execution grant.
2. **Source delivery:** credit only the selected regression-backed contract and
   its normal reviewed merge. A test count or an unrelated passing startup
   check is not the whole source exit.
3. **Release:** identify a containing ordinary release separately. A Dev merge
   does not establish that a consuming daemon supports the contract.
4. **Selected-consumer qualification:** where an already authorized adopter
   requires it, identify the actual serving peer/launch mode and its original
   consumer evidence. No all-host rollout, new native test allowance or
   installed success follows from this proposal.
5. **Parent return:** retain each original's full acceptance and owner; update
   the roadmap's relevant evidence without declaring the numerical campaign
   or the remaining integration gates complete.

This wave's planning exit is a reviewed finite proposal with truthful
uncertainties, not completed implementation. The broader integrated execution
plan still owes current coverage, remaining priority/home decisions and the
other [six-gate assessment](stabilization-roadmap.md#2026-11-20-integration-assessment)
conditions.

## Do not duplicate the pending finite fixes

Two other qualified current originals remain useful **observation lanes**, not
parallel assignments for another implementer:

| Original and actual pending delivery | Retained finite acceptance | Next discriminator |
|---|---|---|
| #13753 / OPEN #13759 | Preserve the exact `psmux.exe` basename in an owner-retained, non-disposable version/process directory; support no-signal/no-kill recovery of an already renamed image; preserve side-by-side versions and all running sessions. Add an isolated installer guard/regression only if the existing owner offers that seam. | The existing author's normal reviewed delivery and the complete conditional acceptance. Do not merge or take over that author's PR, move a live installation or terminate a session. |
| #12970 / OPEN #12978 | Stream hashing under explicit limits suitable for shipped payloads; preserve path/type/content-change checks, stale-plan binding and honest failure above the limits. No activation, retirement or application-state change. | The existing author's actual merged source, whole regression/acceptance and ordinary delivery gates. The author's implementation comment and an open PR are not completed fulfillment. |

The current native-reader/provider stages and other new source deliveries are
credited separately in the [dated evidence journal](journal/2026/11.20-selected-startup-wave.md).
They do not supply a shortcut through these original contracts or the held
whole-source cases.

## Coverage remains explicit

The complete 2026-11-20 23:58:27 UTC census has **884 open issues**:
446 creator-eligible and 438 excluded metadata-only, with 408 original-cohort
and 476 inflow issues. It is **155 above** the fixed 729 checkpoint.
Two arrivals and no departures occurred since the preceding 882-issue census.

Of the 446 creator-eligible open issues, 292 belong to the historical
682-assignment set and 154 do not. The earlier finite locator reconciliation
accounts for 153 outside-assignment identities; the newly qualified #13765
is the additional eligible arrival. Locators are not current whole-contract,
vision, priority or receiving qualification. This selected wave reuses two
actual current contracts and static findings; it does not turn the other
records into researched or closable issues. All exclusions and refresh gaps
remain counted.

No issue was closed, no implementation/resource owner changed, and none of the
six global integration checkboxes is resolved by this document alone.
