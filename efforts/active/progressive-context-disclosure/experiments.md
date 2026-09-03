# Progressive Context Disclosure - Experiment Design

[Effort](README.md) ·
[Issue #1612](https://github.com/ThomasMichon/copilot-extensions/issues/1612)

## Objective

Identify the smallest upfront context and reference representation that lets an
agent act safely, discover the right detailed grounding when needed, and avoid
reading unrelated guidance.

The experiment changes one axis at a time over a fixed synthetic contributor
corpus and task set. Raw canaries and private ambient context stay out of
tracked evidence; reports carry counts, hashes, timings, selections, and
verdicts.

## Corpus

Use synthetic contributors representing:

| Class | Critical kernel | Deferred guide |
|-------|-----------------|----------------|
| Safety/publication | non-negotiable action boundary | edge cases and examples |
| Contribution boundary | destination class decision | complete contribution matrix |
| Continuity | completion gate and handoff rule | handoff procedure |
| Ownership/routing | destination decision rule | complete routing matrix |
| Runtime readiness | readiness state and exact next action | diagnostics |
| Environment grounding | trusted identity and applicability cue | topology and containment detail |
| Command discovery | attributable command identity | full command reference |
| Capability procedure | definition and applicability cue | detailed execution guide |

Each guide contains high-entropy canaries for strict selection evidence. Tasks
declare the exact guide set that is necessary and the set that would be
irrelevant.

The frozen Phase 1 corpus, current-suite contributor inventory, baseline
measurements, task prompts, variant protocol, evidence schema, and literal-mode
rubric live in
`tools/clean-room/scenarios/progressive-context-disclosure-baseline/`. Tier-E
runs must generate fresh per-run canaries and readable guides outside that
read-only fixture mount; reading the fixture or its answer key is a false pass.

Phase 2 execution uses the same fixture's `configure-scenario`,
`materialize`, `verify-materialized`, `observe`, `write-evidence`, and
`validate-evidence` commands. The runnable ACP template is
`tools/clean-room/scenarios/progressive-context-disclosure-eval/`. Each
configured scenario binds exactly one runnable task and repetition; replicated
claims come from independently generated scenarios rather than transcript
aggregation. Fresh and spill cells are runnable now. Resume and compaction
configuration fails closed until the clean-room driver performs those actual
session transitions rather than relabeling a fresh session.

## Axes

### Deferral level

| Variant | Upfront shape |
|---------|---------------|
| F0 | Full contributor content inline |
| F1 | Critical kernel plus one plugin-level guide index |
| F2 | Critical kernel plus per-topic guide references |
| F3 | Critical kernel plus only references applicable to the current repository |
| F4 | Minimal owner/index locator with almost all content deferred |

F0 is the comprehensive baseline, not the desired answer. F4 tests the
under-grounding boundary.

### Reference representation

- real Markdown link: `[guide](./path.md)`;
- backtick repository-relative path: `` `./path.md` ``;
- backtick payload-relative path with an explicit resolution base;
- backtick absolute contained path;
- bare labeled path;
- HTML comment locator;
- structured reference object rendered by the authority.

Real links must be tested for host or model auto-loading. Comments must be
tested for discoverability; invisibility is not assumed. Relative paths must
state what owns their resolution base.

### Emphasis

- optional: “background is available”;
- conditional: “when the task involves X, read Y”;
- imperative: “read Y before performing X”;
- safety-gated: “do not perform X until Y is loaded.”

The experiment distinguishes wording needed for critical grounding from wording
that causes routine over-reading.

### Assembly

- flat owner-delimited fragments;
- flat fragments plus a generated reference index;
- deterministic semantic zones;
- hierarchical structured fragments.

## Task set

1. **No-guide task:** critical kernel is sufficient.
2. **Single-guide task:** one detailed guide is necessary.
3. **Multi-guide task:** two owners' guides are necessary.
4. **Conflict task:** two attributable rules interact or disagree.
5. **Unavailable-guide task:** the declared target is missing.
6. **Unsafe-guide task:** the target escapes its owner or trusted root.
7. **Resume task:** the context generation is reconstructed.
8. **Compaction task:** earlier conversation is compacted before a later guide need.
9. **Spill task:** the aggregate uses the session-state pointer path.
10. **Command-guide task:** a non-kernel command option requires the owned
    command reference.
11. **Capability-guide task:** entering a multi-step capability flow requires
    its owned procedure.

Every task binds to one boundary. Only the eight `fresh` tasks enter the primary
calibration matrix; resume, compaction, and spill are finalist boundary
confirmations, while ACP is a venue repeated for finalists rather than a task
boundary.

## Evidence

Record per run:

- initial Unicode characters, UTF-8 bytes, and fixed estimated tokens;
- model and venue;
- first-turn correctness;
- required and observed guide ids;
- guide ids loaded eagerly by the host or model before an agent-initiated read;
- irrelevant guide reads;
- turns, tool calls, and elapsed time before grounded action;
- missing or invented paths;
- owner/provenance retention;
- critical-rule violations;
- structured-render hash and selected contributor set; and
- clean-room judge verdict and classified jam.

Transport or setup failures are `INVALID`, not behavioral failures. They record
only a classified jam, do not eliminate a variant, and must be rerun after the
scenario or venue failure is corrected.

## Decision gates

- Reject a variant that lowers first-turn correctness or hides a critical rule.
- Reject a representation that auto-loads deferred material unexpectedly.
- Reject a representation that the agent routinely ignores when its guide is
  required.
- Reject an emphasis level that causes optional guides to be read routinely.
- Prefer the simplest variant inside the correctness envelope; token reduction
  alone does not win.
- Do not generalize from one model or one transport when the result depends on
  rendering or path interpretation.

## Replication and selection

Use sequential elimination rather than running the full Cartesian matrix at
maximum replication:

1. Run deterministic Tier-P rendering and containment checks for every variant.
2. Run each surviving primary variant/task cell in at least three independent
   fresh sessions on the calibration model and venue.
3. Eliminate a variant after any critical-rule violation, missed required guide,
   invented path, provenance loss, or false pass that depends on broad
   compensating exploration.
4. Compare non-critical measures using the median and observed range across
   repeats; never select from one latency, tool-count, or irrelevant-read
   observation.
5. Repeat finalists at least three times per task on a second model and across
   the supported resume and ACP boundary before standardizing them.

Correctness and required grounding are unanimous gates. Context bytes, latency,
and unnecessary reads choose among variants only after they are inside that
gate. If finalists are behaviorally unstable across repeats, models, or venues,
the result is “no standard yet,” not permission to choose the smallest prompt.

## Phase 2 evidence ledger

| Cell | Repetitions | Judge result | Required guides | Canary-backed irrelevant reads | Decision |
|------|-------------|--------------|-----------------|------------------|----------|
| F2 / backtick repository-relative / conditional / flat fragments / one-guide / ACP fresh | 3 | 1 PASS, 2 FALSE-PASS → FAIL | 3/3 loaded `runtime-diagnostics` | 0, 1, 1 | Reject this cell: conditional wording did not reliably prevent broad discovery or the unrelated `command-reference` read. |
| F2 / backtick repository-relative / imperative / flat fragments / one-guide / ACP fresh | 3 | 0 PASS, 3 FALSE-PASS → FAIL | 3/3 loaded `runtime-diagnostics` | 3, 3, 2 | Reject this cell: stronger imperative wording increased compensating exploration and unrelated guide reads. |
| F2 / backtick repository-relative / safety-gated / flat fragments / one-guide / ACP fresh | 3 | 1 PASS, 2 FALSE-PASS → FAIL | 3/3 loaded `runtime-diagnostics` | 0, 1, 2 | Reject this cell: the gate produced one exact one-guide run but did not reliably prevent broad discovery, irrelevant reads, or provenance loss; the repetition-2 transcript judge counted three direct irrelevant document reads. |
| F2 / backtick repository-relative / optional / flat fragments / one-guide / ACP fresh | 3 | 0 PASS, 3 FALSE-PASS → FAIL | 3/3 loaded `runtime-diagnostics` | 1, 1, 2 | Reject this cell: optional wording never produced a literal pass and every repetition broadened beyond the task-applicable guide. |
| F3 / backtick repository-relative / safety-gated / flat fragments / one-guide / ACP fresh | 3 | 3 PASS | 3/3 loaded only `runtime-diagnostics` | 0, 0, 0 | Retain as a surviving cell: task-applicable references removed the F2 exploration failure while reducing initial context from 4,379 to 2,862 characters. |
| F3 / backtick repository-relative / safety-gated / flat fragments / no-guide / ACP fresh | 3 | 3 PASS | no guide required or loaded | 0, 0, 0 | Retain as a surviving boundary: the critical kernel completed the task with zero tool calls and zero deferred reads. |
| F3 / backtick repository-relative / safety-gated / flat fragments / multi-guide / ACP fresh | 3 behavioral + 1 INVALID timeout | 0 PASS, 3 FALSE-PASS → FAIL | all required guides loaded | 0, 1, 2 | Reject this variant: every behavioral run broadened beyond the bounded flow and violated CAP-1; two also loaded unrelated guides, and repetition 3 violated CMD-1. |
| F3 / structured reference / safety-gated / flat fragments / multi-guide / ACP fresh | 3 | 0 PASS, 3 FAIL | all required guides loaded | 3, 3, 2 | Reject this representation: structured metadata increased irrelevant reads, lost provenance in every run, and did not produce the required decision. |
| F3 / backtick repository-relative / safety-gated / flat with generated index / multi-guide / ACP fresh | 3 behavioral + 1 INVALID timeout | 0 PASS, 3 FAIL | all required guides loaded | 2, 2, 2 | Reject this assembly: independent judges counted three direct irrelevant guide reads in every run and each returned the wrong blocked decision; two also invented configuration paths. |

Counts-only records:

- [`evidence/f2-repo-conditional-flat-one-guide-r1.json`](evidence/f2-repo-conditional-flat-one-guide-r1.json)
- [`evidence/f2-repo-conditional-flat-one-guide-r2.json`](evidence/f2-repo-conditional-flat-one-guide-r2.json)
- [`evidence/f2-repo-conditional-flat-one-guide-r3.json`](evidence/f2-repo-conditional-flat-one-guide-r3.json)
- [`evidence/f2-repo-imperative-flat-one-guide-r1.json`](evidence/f2-repo-imperative-flat-one-guide-r1.json)
- [`evidence/f2-repo-imperative-flat-one-guide-r2.json`](evidence/f2-repo-imperative-flat-one-guide-r2.json)
- [`evidence/f2-repo-imperative-flat-one-guide-r3.json`](evidence/f2-repo-imperative-flat-one-guide-r3.json)
- [`evidence/f2-repo-gated-flat-one-guide-r1.json`](evidence/f2-repo-gated-flat-one-guide-r1.json)
- [`evidence/f2-repo-gated-flat-one-guide-r2.json`](evidence/f2-repo-gated-flat-one-guide-r2.json)
- [`evidence/f2-repo-gated-flat-one-guide-r3.json`](evidence/f2-repo-gated-flat-one-guide-r3.json)
- [`evidence/f2-repo-optional-flat-one-guide-r1.json`](evidence/f2-repo-optional-flat-one-guide-r1.json)
- [`evidence/f2-repo-optional-flat-one-guide-r2.json`](evidence/f2-repo-optional-flat-one-guide-r2.json)
- [`evidence/f2-repo-optional-flat-one-guide-r3.json`](evidence/f2-repo-optional-flat-one-guide-r3.json)
- [`evidence/f3-repo-gated-flat-one-guide-r1.json`](evidence/f3-repo-gated-flat-one-guide-r1.json)
- [`evidence/f3-repo-gated-flat-one-guide-r2.json`](evidence/f3-repo-gated-flat-one-guide-r2.json)
- [`evidence/f3-repo-gated-flat-one-guide-r3.json`](evidence/f3-repo-gated-flat-one-guide-r3.json)
- [`evidence/f3-repo-gated-flat-no-guide-r1.json`](evidence/f3-repo-gated-flat-no-guide-r1.json)
- [`evidence/f3-repo-gated-flat-no-guide-r2.json`](evidence/f3-repo-gated-flat-no-guide-r2.json)
- [`evidence/f3-repo-gated-flat-no-guide-r3.json`](evidence/f3-repo-gated-flat-no-guide-r3.json)
- [`evidence/f3-repo-gated-flat-multi-guide-r1.json`](evidence/f3-repo-gated-flat-multi-guide-r1.json)
- [`evidence/f3-repo-gated-flat-multi-guide-r2.json`](evidence/f3-repo-gated-flat-multi-guide-r2.json)
- [`evidence/f3-repo-gated-flat-multi-guide-r3.json`](evidence/f3-repo-gated-flat-multi-guide-r3.json)
- [`evidence/f3-structured-gated-flat-multi-guide-r1.json`](evidence/f3-structured-gated-flat-multi-guide-r1.json)
- [`evidence/f3-structured-gated-flat-multi-guide-r2.json`](evidence/f3-structured-gated-flat-multi-guide-r2.json)
- [`evidence/f3-structured-gated-flat-multi-guide-r3.json`](evidence/f3-structured-gated-flat-multi-guide-r3.json)
- [`evidence/f3-repo-gated-index-multi-guide-r1.json`](evidence/f3-repo-gated-index-multi-guide-r1.json)
- [`evidence/f3-repo-gated-index-multi-guide-r2.json`](evidence/f3-repo-gated-index-multi-guide-r2.json)
- [`evidence/f3-repo-gated-index-multi-guide-r3.json`](evidence/f3-repo-gated-index-multi-guide-r3.json)

All three conditional sessions retained owner provenance, loaded the required
guide, avoided path invention and critical-rule violations, and reached the
owned readiness decision. Repetitions 2 and 3 nevertheless enumerated the guide
tree and loaded `command-reference`; literal mode therefore rejects the cell.
Continue one axis at a time with stronger emphasis before changing the
reference representation or deferral level.

Imperative emphasis did not recover the cell. All three imperative repetitions
loaded at least two irrelevant guides, and two loaded three. The controlled
next comparison is safety-gated emphasis with every other coordinate fixed.

Safety-gated emphasis also failed the unanimous correctness gate. Repetition 1
was the desired behavior: one tool call loaded only `runtime-diagnostics`, then
returned the owned readiness decision with no irrelevant exploration.
Repetitions 2 and 3 searched broadly and loaded unrelated guidance.
Repetition 2 also omitted an encountered capability-guide canary and shifted its
blocker to command discovery, losing complete owner provenance.

The frozen counts-only writer derives observed guides from canaries present in
the transcript. Its repetition-2 record therefore counts one irrelevant guide,
while the independent judge counted three direct irrelevant document reads:
the guide index, `capability-procedure`, and `command-reference`. Preserve that
distinction rather than altering the frozen evidence contract mid-calibration.

No stronger emphasis remains. The controlled next comparison is the optional
edge with every other coordinate fixed; if it also fails, revisit deferral or
reference shape rather than rewriting the task or guide.

Optional emphasis failed all three repetitions. Every session loaded the
required `runtime-diagnostics` guide and preserved READY-1, but all three also
enumerated or searched beyond the task-applicable locator. The frozen
canary-backed records count irrelevant guides as 1, 1, and 2; the independent
transcript judges counted direct irrelevant guide reads as 1, 2, and 2.
Repetition 1 also lost complete owner provenance. Repetition 2 malformed the
`command-reference` canary in its witness, so that direct read is intentionally
absent from the canary-backed observed set.

All four F2 emphasis forms are now rejected for this one-guide cell. The next
controlled comparison changes only deferral from F2 to F3 while retaining the
backtick repository-relative representation, safety-gated emphasis, flat
fragments, task, model, venue, and fresh boundary. F3 emits only the task-required
guide reference, directly testing whether the surplus F2 per-topic references
caused compensating exploration.

F3 safety-gated passed all three one-guide repetitions. Every session loaded
only `runtime-diagnostics`, preserved READY-1 and owner provenance, avoided path
invention and prohibited sources, and performed no capability operation or
compensating exploration. Tool-call counts were 1, 1, and 2; the third run used
one exact locator glob before reading the same required guide.

The F3 render reduced initial context from 4,379 characters / 1,095 estimated
tokens for F2 safety-gated to 2,862 characters / 716 estimated tokens, a 34.6%
reduction, while moving correctness from 1/3 to 3/3 and irrelevant reads from
0/1/2 to 0/0/0. Retain this cell for further task-boundary replication; it is
not yet a selected standard.

The next controlled task boundary is the F3 safety-gated no-guide cell, with all
variant, representation, assembly, model, venue, and fresh-session coordinates
fixed. It must complete from the critical kernel without reading any deferred
material.

F3 safety-gated passed all three no-guide repetitions. Every session completed
from the critical kernel in one turn with zero tool calls, no observed or eager
guide loads, no invented paths or canaries, complete owner provenance, and no
critical-rule violation. Initial context was 2,646 characters / 662 estimated
tokens.

The next controlled task boundary is multi-guide, keeping F3,
repository-relative backticks, safety-gated emphasis, flat fragments, model,
ACP venue, and fresh sessions fixed. It must read the three required guides,
preserve both owners, and avoid every unrelated guide.

The frozen multi-guide task requires three guides across two owners:
`publication-checks`, `destination-matrix`, and `capability-procedure`. The
first attempt timed out at 300 seconds and remains transport `INVALID`; an
independent fresh retry supplied repetition 1.

All three behavioral repetitions loaded every required guide but failed literal
mode. Each enumerated or searched beyond the exact locators and violated CAP-1's
bounded-task rule. Repetitions 2 and 3 additionally loaded one and two unrelated
guides; repetition 3 also loaded `command-reference` without a required
non-kernel command option, violating CMD-1. The repository-relative F3 variant
is therefore eliminated despite its unanimous no-guide and one-guide cells.

The next controlled comparison keeps F3, safety-gated emphasis, flat fragments,
the multi-guide task, model, ACP venue, and fresh sessions fixed while changing
only the reference representation from backtick repository-relative paths to
the frozen structured-reference form. It tests whether explicit structured
metadata can keep a multi-guide flow bounded without adding an assembly index.

Structured references failed all three multi-guide repetitions. Every run read
all required guides but also loaded two or three unrelated guides, lost complete
owner provenance, and returned the wrong blocked or do-not-proceed decision
instead of the required bounded procedure. Broad repository discovery remained
present, with 14-18 tool calls per run.

Reject the structured representation for this cell. The next controlled
comparison should change only assembly from flat fragments to flat fragments
with a generated index while returning to the lower-overhead
repository-relative reference form. This tests whether a single explicit index
can provide ordering without the structured-reference verbosity.

The generated index also failed all three multi-guide repetitions. Every run
loaded the required guides, but each performed broad repository discovery,
and independent judges counted three direct irrelevant guide reads, lost exact
owner provenance, used 20 tool calls, and returned an incorrect blocked
decision. Repetitions 1 and 2 also invented `.git/config` or settings-based
destination locators; both violated ROUTE-1 and CAP-1. The canary-backed
records count two irrelevant guides in each run because the third directly
read guide's canary was absent or malformed in the final witness.

One initial repetition timed out and remains transport `INVALID`. A separate
setup attempt after fast-forwarding the evaluation checkout was also `INVALID`
before agent launch because the frozen suite-inventory guard correctly detected
a later contributor-order change. The behavioral scenarios were therefore
generated from the frozen pre-change source while using the byte-identical
clean-room driver from the current checkout.

Reject `flat-with-index`: adding a global index increased initial context to
4,150 characters / 1,038 estimated tokens without recovering bounded
multi-guide behavior. The next controlled comparison returns to flat fragments
and changes only the reference representation to a contained absolute backtick
path, testing whether eliminating relative-base discovery prevents the
configuration and repository search failures.

## Clean-room shape

Add a dedicated Tier-P renderer/containment scenario and a Tier-E literal-mode
scenario. Freeze prompts, corpus, variant manifest, repetition counts, and
rubric in one reviewed commit before running comparisons. Use fresh sessions
for primary calibration; add resume, compaction, and ACP confirmation after the
base matrix is stable.

Independent judges credit only the literal task. A pass that depends on the
agent searching broadly, reading every guide, or improvising around a broken
path is a false pass.
