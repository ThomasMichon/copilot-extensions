# Proposal — proportional engineering conduct

Linked from [the effort](README.md). This is a proposal, not live guidance.
Nothing here is projected into sessions until the plan is accepted and the
scratch-worktree comparison says the wording helps.

## Durable goal

An agent realizing an asked change does the smallest faithful thing the owning
repository's stated architecture already allows. It extends that architecture
only when the ask cannot be expressed inside it, and only after that extension
is accepted. Caution is not a reason to invent a second system.

_(agent-recommended)_ Place this as a child of
[harness guidance](../../../visions/harness-guidance/README.md), not as an edit
to that vision's delivery contract. Delivery (who owns guidance, how it
arrives, how context is budgeted) is a different subject from judgment (what
the agent should do once guidance has arrived). Suggested leaf:
`visions/harness-guidance/practical-realization`. Do not author that vision
until this plan is accepted.

Suggested behaviors, intent only:

- **implementation-altitude-first** — if the stated architecture can express
  the fix, the fix is an implementation change.
- **extension-by-accepted-proposal** — an architectural extension is proposed
  and accepted before it is built. A reviewable change is the preferred
  acceptance. An operator decision is the fallback.
- **repository-guidance-is-authoritative** — the owning repository's test
  strategy, patterns, and invariants are the engineering guidance. This
  conduct kernel points at them. It does not restate or replace them.
- **no-unjustified-parallel-machinery** — do not invent a second test system,
  validation framework, or chained receipt, hash, or ownership scheme for
  routine already-authorized work. A small check that fits the existing
  architecture is an implementation fix when a concrete failure justifies it
  and a simpler check cannot meet that need. Absence of a pre-existing
  contract sentence is not a ban on that fix. Do not remove a check the
  owning contract already requires.
- **low-cadence-plain-status** — the agent says what it is doing and the one
  concern, in plain language, at an existing disposition or status seam and
  when the plan changes. No second status channel. No conversational filler.
- **real-gates-remain** — confirmation, elevation, publication, and
  destructive-action gates stay in force. This guidance does not weaken them.

Non-goals:

- Do not prescribe code shape (classes versus functions, linear scripts).
- Do not forbid the repository's own vocabulary, and do not require analogies.
- Do not restate `TESTING.md`, the install contract, or a repository's patterns.
- Do not treat "be careful" as permission to build a parallel harness.
- Do not treat a missing contract sentence as a ban on a proportionate
  in-architecture fix for a concrete failure.

## Assessment of the style overlay

A five-part style overlay was proposed: direct answers with no filler;
anti-over-engineering; stated tradeoffs without corporate disclaimers; dense
scannable formatting; explanations aimed at user utility.

**It can help as a tone bias. It cannot be the fix.**

| Overlay item | Verdict | Why |
|---|---|---|
| No filler; start with the answer | Keep, narrowed | Conversational openers and closers are noise. Operational status is not filler. "Start with the answer" fights a multi-step change where the operator needs the plan when architecture must move. |
| Anti-over-engineering | Rewrite | "No classes, write a linear script" pushes agents off the repository's architecture. The failure is process over-building: a new test system, a new validation layer, a design spike for an implementation bug. |
| State tradeoffs; skip corporate disclaimers | Keep, bounded | Say the tradeoff and the edge case. Do not add a disclaimer the boundary did not require. Do not skip a real gate. |
| Dense formatting and bolding rules | Defer | Secondary style. An always-on bolding rule spends context and collides with adopter voice or writing guidance. Not part of the first kernel. |
| Analogies; avoid jargon | Reject | The audience is an agent working a repository. It should use that repository's vocabulary. Analogies for a general reader are the wrong default. |

The overlay also misses the planning rule. Planning is required when the
current architecture cannot express the fix: inspect the vision and the
invariants, propose the smallest extension, accept it, then build inside it.
Planning is not the default response to a defect.

## Where it layers

| Layer | Home | Role |
|---|---|---|
| Intent | child of `visions/harness-guidance` | Standing should-be. Not a style patch. |
| Always-on kernel | `agent-conduct-guidance` instruction projection | The nudge. Short. Agents will not remember to invoke a skill for this. |
| Task-time procedure | paired skill, only if the kernel cannot carry the altitude decision without growing | When to propose versus when to just fix. |
| Repository engineering guidance | stays in the owning repository (`TESTING.md`, patterns, invariants) | Point at it. Do not copy it. |
| Status cadence | existing disposition / status seam | Say the update there. Do not add a channel. |

Do not put this in a repository `AGENTS.md` as a copy of the kernel. Plugin
guidance owns the generic policy (`authoritative-ownership`).

Do not weaken
[instruction source resolution](../instruction-source-resolution/README.md).
That effort keeps required provenance and reviewed-fallback checks. This
effort forbids unjustified parallel machinery. It does not forbid a
proportionate safeguard that fits the existing architecture and is justified
by a concrete failure, and it does not remove a check the owning contract
already requires. `CONTRIBUTING.md` (*Proportionate safeguards and reliable
cooperation*) is the local statement of that line for this repository.

## Draft kernel

Not projected. Target: one short always-on reminder, same shape as the other
`agent-conduct-guidance` fallbacks.

```markdown
# Practical realization

**Fallback policy `[owner: agent-conduct-guidance]`:** Solve the asked
problem inside the architecture the owning repository already states. Do not
invent a parallel design, test system, or safety layer because caution feels
safer.

- **Match altitude.** If the existing architecture can express the fix, change
  the implementation. Do not open a design spike, a new framework, or a
  multi-part harness.
- **Extend only by acceptance.** If the ask cannot be expressed inside current
  invariants, stop building and propose the smallest architectural extension.
  Prefer a reviewable change. Do not build the extension until it is accepted.
- **Use the repository's own engineering guidance.** Follow its stated test
  strategy, patterns, and invariants. Do not restate them. Do not invent a
  parallel harness, or add chained receipts, exact-hash attestation,
  ownership schemes, redundant downloads, or extra refreshes to routine
  already-authorized work. A small check that fits the existing architecture
  is allowed when a concrete failure justifies it and a simpler check cannot
  meet that need. Do not remove a check the contract already requires.
- **Say what you are doing.** Skip conversational filler. At a disposition or
  status seam, and when the plan changes, tell the operator in one or two
  plain sentences what you are doing and the one real concern. Then continue.
- **Real gates stay.** Confirmation, elevation, publication, and
  destructive-action gates are unchanged.
```

_(agent-recommended)_ If scratch comparison shows agents still skip the
altitude decision, add a paired skill that is a procedure, not a second copy
of the kernel. If the kernel alone holds, do not add the skill.

## Scratch comparison

_(agent-recommended)_ Before any instruction projection:

Run the same three task shapes in a scratch worktree with the draft kernel
injected, and in a control worktree without it. Score behavior, not prose.

1. **Implementation repro.** A known defect whose repository already states
   the test venue. Pass: the agent uses that venue. Fail: it designs a new
   harness.
2. **Simplify an existing path.** A request to make an existing flow faster
   or smaller. Pass: the change removes work, or adds only a proportionate
   check for a concrete failure that fits the existing architecture. Fail:
   it adds a parallel validation layer, chained receipts, redundant
   downloads, or extra refreshes that no concrete failure justifies. Also
   fail if the agent refuses a small in-architecture check solely because
   the contract did not already name it.
3. **Real extension.** A change the current invariants cannot express. Pass:
   the agent proposes and waits. Fail: it builds the extension first, or it
   hacks around the invariant under "just implement it."

Also fail the wording if the guided agent skips a real confirmation gate, goes
mute, or treats the repository's stated test strategy as optional.

Do not project the kernel live on a mixed or negative result. Revise the
wording and repeat the comparison.

## Prompt comparison scorecard

Recorded 2026-10-26. This was an explicit prompt, not an always-on
instruction file in a session. It does not authorize projection.

| Shape | Guided | Control |
|---|---|---|
| Reproduce in the stated venue | Used that venue. No new harness. | Same. No delta. |
| Make an existing installer path faster | Changed the existing path. No new framework or unrequired checks. Answered without a long investigation. | Same destination, after extended exploration. |
| Contract cannot express the check | Proposed the extension and waited. | Same. No delta. |

Destinations did not diverge. Process cost diverged only on the open-ended
speed task. The guided condition was told to follow the kernel, so this is
not evidence that an ambient file would produce the same bound. Hold
projection until a session-injection comparison says otherwise.

## Session-injection comparison

Recorded 2026-10-26. This was an always-on instruction file in one scratch
session, not a prompt that said to follow the kernel. The control session
had no such file. Both used the same model and the same commit. The request
was the open-ended installer-speed shape. It does not authorize projection.

| | Guided | Control |
|---|---|---|
| Wall time | about 11 minutes | about 10 minutes |
| Model requests | 34 | 42 |
| Destination | Reused an existing fingerprint path: resolve each distinct parent once per call. Per-file checks stayed. Reported a hashing-stage improvement, not a whole-installer claim. | Reused the existing source-snapshot copy loop: bounded parallel copies for larger snapshots, serial for small ones. Existing verification stayed. Reported a source-freezing improvement, not a whole-installer claim. |
| Unjustified machinery | None added. | None added. |
| Gates | No push, no pull request, not mute. | Same. |

Destinations diverged. Both stayed inside an existing installer path.
Neither added a parallel validation layer, chained receipts, redundant
downloads, or extra refreshes. Process cost did not diverge the way the
prompt comparison did. The guided session was not faster on the wall clock.
It used fewer model requests.

Both sessions were told not to push and not to open a pull request, so this
run does not show that the kernel preserves publication gates. It also does
not show that the ambient file prevents installer bloat, because the control
did not bloat either. No harm observed. Hold projection.
