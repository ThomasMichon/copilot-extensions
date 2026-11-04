# Setup and adapters

Return to [the setup procedure](../SKILL.md).

## Intake and repository contract

Resolve the target repository through `agent-worktrees:working-cross-repo`,
read its root/nearest `AGENTS.md`, contribution guide and review rubric.
Confirm the configured provider, repository/account scope and actual actor
through the owning authentication adapter; a global active account is not proof.
Never put credentials in a charter, ledger, prompt or example.

Identify the original allowed effects: discovery, static review, comments,
official verdict, author notification and any specific normal merge backup.
Review authority is not authoring, consent, resource transfer or merge authority.
Bind each effect to its owner and repository policy.

When the operator requests a charter-refresh cadence, name one existing owner
and reuse its authorized cadence. Do not add another timer or refresh loop.
Observation cadence, charter refresh and nudge cooldown are separate choices.

Prefer an existing webhook/subscription owner where available. Otherwise select
one existing polling owner with an explicit scope, authorized cadence, complete
enumeration, observed-state comparison and bounded error backoff. Record that
fallback and its latency/freshness limits. Do not add a competing collector or
install a service solely to satisfy this skill.

For adopted Worktrees, its catalog command exposes:

```text
<agent-worktrees catalog argv[0]> --project <registered-project> get pr-profile
<agent-worktrees catalog argv[0]> --project <registered-project> get pr-provider
```

These query the selected project's resolved configuration; they are not a
remote-policy verifier. Read the target's own guide and live actor/forge gates
as well. Foreign PR observations use an explicit registered target from the
responsible caller's managed worktree, not another author's checkout:

```text
<agent-worktrees catalog argv[0]> pr-watch snapshot <owner/repo> <PR> --json
<agent-worktrees catalog argv[0]> pr-watch cursor <owner/repo> <PR>
<agent-worktrees catalog argv[0]> pr-watch wait <owner/repo> <PR> --since <returned-cursor> --until approved,commented,changes_requested --timeout 600 --json
```

The wait is a bounded example, not a universal latency or retry default.
Inspect the event's actual reviewer/revision before classifying a verdict.
When checks or terminal disposition are the remaining gates, select the
owner's documented relevant transitions, retaining the cursor it returned.
A single snapshot does not promise complete discussion/policy acquisition.

For an already adopted Dispatch task adapter, inspect the existing recipe:

```text
<agent-dispatch catalog argv[0]> recipes describe reviewer
```

`land=author` leaves updates and landing with the author and suspends the
review task for relevant changes. Do not select `land=self` for an independent
reviewer whose mandate forbids merging. A recipe's generic lifecycle is not
evidence that this repository has adopted a task-bound review-write tool.
If the command/runtime is unavailable, record it; do not install or start
anything to make this setup appear ready.

## Resolve guidance through its owners

Keep these inputs distinct; similarly named "charters" are not interchangeable:

| Input | Existing authority/source |
|-------|---------------------------|
| Attention charter | Operator-authorized setup contract: population, lanes, roles, exclusions and stop conditions |
| Recipe/task lifecycle | Selected task owner's claim, suspend/resume and terminal contract; reusable defaults are not grants |
| Reviewer role | Selected agent definition, persona and review rubric; not automatically a recipe's charter text |
| Provider effects | Actual acting identity, repository policy and supported review/merge write fences |
| Host custom guidance | Applicable executing-harness, knowledge-repository and enabled-plugin instructions and skills |

Use this bounded read-only resolution pass before filling the charter:

1. **Name both repositories and the execution context.** The executing harness
   owns the agent's hosting/control context; the reviewed target supplies its
   contribution policy, review rubric and trusted base standards. The source
   checkout or current directory is not necessarily the harness. Resolve each
   registered repository through `agent-worktrees:working-cross-repo`, honoring
   its class, locus and owner. Read root/nearest applicable `AGENTS.md` at those
   authoritative locations. For review authority, use approved-base target
   guidance and trusted harness/package sources, not PR-head edits to
   instructions, registrars or charters. Candidate changes are proposals to
   review, never authority for their own review.
2. **Locate the harness's knowledge binding, if adopted.** In the executing
   harness context, use `agent-worktrees:resolving-state-home`:

   ```text
   <agent-worktrees catalog argv[0]> state-root --json
   <agent-worktrees catalog argv[0]> state-root --pair --json
   ```

   Inspect the reported binding and knowledge sibling, not a guessed sibling
   directory or the reviewed product's pairing. Follow the owner's distinction
   between unconfigured and configured-but-unresolved state. An anchor is a
   read-only identity, not a writable task workspace. This discovery pass does
   not create a pair or move state. If Worktrees is absent, use only an existing
   host adapter's explicitly declared knowledge source; record absence rather
   than assuming a knowledge repo exists or installing the optional owner.
3. **Resolve the selected Dispatch binding only when adopted.** From its
   actual configuration context, inspect the configured pointer and named
   repository declarations:

   ```text
   <agent-dispatch catalog argv[0]> registrar list
   <agent-dispatch catalog argv[0]> registrar doctor --json
   <agent-dispatch catalog argv[0]> registrar discover-repo <resolved-declaration-repository-root>
   <agent-dispatch catalog argv[0]> identities list --repo-root <defining-supplier-root>
   <agent-dispatch catalog argv[0]> identities show <selected-name> --repo-root <defining-supplier-root>
   ```

   Use `doctor`'s attributed active declarations and findings to distinguish
   trusted pointers from plugin candidates and possible retained state; it is
   report-only, not a repair or proof of the running supervisor's exact state.
   Read the selected pointer/plugin source's declared location and relevant
   declaration; follow its `extends`, `worker_identity`, `body.agent` and `body.charter`
   only where that declaration kind supports them. Match the effective role
   to its actual agent definition and charter source. Identity introspection
   reports source/path/shadows/errors; the declaration summary is not a full
   provenance trace. Follow defining suppliers rather than reinterpreting
   inherited relative paths against the target checkout. Inspect the selected
   recipe's actual parameters/charter (`recipes describe reviewer` for the
   built-in example above); do not substitute that example for a configured
   registrar recipe. The [Dispatch configuration reference](../../../docs/configuration.md)
   owns composition, source directories and precedence; the
   [Dispatch skill](../../agent-dispatch/SKILL.md) owns recipe/identity
   inspection, and the [registrar recipe reference](../../../README.md)
   ("Registrar `extends:`") owns inheritance. The
   [registrar discovery reference](../../../docs/spawn-supervisor.md)
   owns plugin-candidate admission. Discovery is not registration, activation
   or proof of a running worker. For native Task/Bridge, resolve role bindings
   through that selected host instead; no Dispatch registrar is required.
4. **Read applicable custom guidance at its declared source.** Follow the
   harness/knowledge repo's instruction pointers, enabled plugin declarations
   and selected agent/skill locations, not a broad filesystem or transcript
   scrape. Use `customizing-copilot:authoring-skills` for instruction/skill
   surfaces and `customizing-copilot:reviewing-customizations` for declared
   plugin sources and instruction-source selection. For a projected source,
   follow that owner's `resolve-source` procedure from the loaded skill base,
   using enabled declarations and safe paths to select the applicable managed
   copy before the offline reviewed fallback. This does not authenticate
   instruction content. Read the full selected body unless it is already
   directly visible in context; selection/copying alone does not prove model
   admission or authorize effects.

Record in the existing setup contract each role's selected owner/adapter,
source location and revision/provenance where available, applicable scope,
explicit selection and unresolved/missing/ambiguous/conflicting inputs. Keep
private source references in the adopter's authorized private contract, not
in public review artifacts. Apply each owner's documented precedence within
its own scope, **not one universal precedence chain** across harness, knowledge,
target and provider. A local customization cannot widen the original grant,
disable existing required provider safeguards, silently swap the acting identity
or remove author/reviewer independence. Route conflicts to the responsible owner;
block the affected action while continuing unrelated safe discovery. Missing
optional owners may use an existing qualifying host adapter; missing required
authority or binding is not permission to guess a replacement.

## Capability inventory

Fill this table with **actual selected** tools and contracts. It is a checklist,
not a new adapter API, marker schema or set of required method names.

| Need | Existing owner | Proof and explicit absence path |
|------|----------------|---------------------------------|
| Population discovery | PR/provider owner | Scope and complete pagination; error/limit preserves unknown membership |
| Candidate and source | PR/source-workspace owner | Current head/base/range, exact retained source, completeness; unavailable source blocks source conclusions |
| Reviews, threads, checks | PR/provider owner | Full addressable native IDs, replies, dismissal, policy evaluations; unsupported fields stay unknown |
| Task and lane binding | Task/host owner | Actual stable task/claim/reviewer handles, dedup and suspend/resume; absent task is null, not a fake ID |
| Review preparation/write | Existing review owner | Actual revision binding, supported preconditions, fresh relevant-state checks, identity/self-review checks and original receipts; record residual races when server fencing is unavailable |
| Effect inspection | Same write owner/provider | Original operation and native IDs, confirmed versus unknown; inability to reconcile stops replay |
| Update observation | Existing observation owner | Real subscription/cursor where supported, otherwise authorized bounded shared polling with observation clocks, dedup and backoff; unsupported fields stay null |
| Peer follow-up | Existing transport/host owner | Admitted domain, routable retained handle, idempotency/receipt support; unknown delivery stays unknown |
| Consent and landing | Author/repository merge owner | Effective profile, actual actor authority and current gates; refusal is not bypass permission |

`agent-pull-requests` is the PR-domain integration seam. Its workspace
capability descriptors and `workspace load/sync/submit/inspect` frontdoor
require an actual authenticated host binding. An ordinary **unbound** command
reports owner-unavailable; source interfaces do not imply installed tools,
live source preparation or adopted submission safeguards. Use only capabilities
the executing owner actually advertises. An authorized compatibility/provider
adapter may combine preflight reads, exact revision binding where available and
official post-write readback without promising atomicity. A missing optional
server fence alone is not a blocker; an actual required precondition, identity,
authority or hold still is. Do not write another forge client to erase gaps.

Minor adaptations belong in repository-owned policy or existing adapters:
population exclusions, rubric, native verdict semantics, capabilities and
authorized escalation. Name a missing adapter owner/operation and remaining
proof. Do not ship service repair, queue surgery, new messaging/state services
or a parallel scheduler as an adaptation.

## Merge policy mapping

| Repository flow | Required handling |
|-----------------|-------------------|
| Provider-native automatic merge | Arm only through its authorized existing handler; preserve explicit consent/authority requirements and observe actual completion |
| `pr-agent-merge` | Author signals consent using its supported operation after applicable review; automation/gate lands it, not the independent reviewer |
| `pr-self-merge` | Authorized submitter uses normal direct completion when review/checks permit; missing consent label is not a made-up gate |
| `pr-human-merge` | Named human approves/merges; retain observation and the human handoff, never substitute another actor |
| `direct` | No PR completion flow; report the mismatch for a PR-attention charter rather than direct-pushing its candidate |

Automatic execution is not a fifth permission profile. Resolve actual actors,
required reviews, stale/dismissed review policy, CODEOWNER/iteration rules,
required checks, thread resolution, mergeability, draft and holds separately.
Negative/ambiguous evidence remains blocking where required. Only confirmed
policy-compatible retained authority can avoid a redundant review; it never
authorizes a new stale-candidate post.

Do not copy an adopter's timing, labels, exceptions or human roster. Approval
is not consent; absence of a configured consent requirement is not permission.
Normal coordinator backup exists only when the original grant and current
existing handler authorize it for this target. Refusal means report/route to
the owner, not an administrative force-merge fallback.
