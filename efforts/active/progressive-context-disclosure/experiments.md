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

## Clean-room shape

Add a dedicated Tier-P renderer/containment scenario and a Tier-E literal-mode
scenario. Freeze prompts, corpus, variant manifest, repetition counts, and
rubric in one reviewed commit before running comparisons. Use fresh sessions
for primary calibration; add resume, compaction, and ACP confirmation after the
base matrix is stable.

Independent judges credit only the literal task. A pass that depends on the
agent searching broadly, reading every guide, or improvising around a broken
path is a false pass.
