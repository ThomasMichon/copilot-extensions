# Tier-E execution — design

> **What this is:** the concrete **execution design** for Tier-E (agent-driven,
> eval) clean-room runs — the layer that turns *"drop Copilot on a fresh box and
> have it read our docs and act"* into a repeatable, judged PASS/FAIL. It fills
> the gap between the Tier-E *concept* (already defined in
> [`ARCHITECTURE.md`](./ARCHITECTURE.md) §1, the [vision](../../visions/clean-room-validation/README.md),
> and the [`clean-room-judge`](../../plugins/copilot-extensions-harness/agents/clean-room-judge.agent.md)
> agent) and a working implementation. **Status: beachhead BUILT** — the runner
> `-Mode eval` path, the `lib/literal-mode.md` fixture, and the reference
> `agent-vault-eval` scenario are implemented and validated end-to-end (drive →
> capture → `clean-room-judge` verdict). The **extreme** whole-harness F1-E/F3-E
> variant remains future work (Phase 6). See the phased plan in §11.
>
> Tier P (programmatic) runs today (17 scenarios). Tier E's pieces — the
> `bridge-register` transport, the judge agent, the literal-mode principle — are
> now wired together by an execution path (`-Mode eval`) with one public reference
> eval scenario. This document is the design of record for that layer.

## 1. What Tier-E execution must serve

Two operator-facing variants of the same machinery, from the original ask:

- **Less-extreme (per-plugin doc-audit).** Start from a **partial harness** (e.g.
  a fresh box with one plugin installed), give Copilot a natural-language prompt
  — *"set up agent-vault for use"* / *"using agent-vault, list my vault entries"*
  — and judge whether it could **parse the plugin's docs and satisfy its needs**
  one-shot. This is **F2-E** (does a solo plugin's docs let a fresh agent stand it
  up / drive it), and the doc-audit form of **F1-E**.
- **Extreme (whole-harness from bare).** Start from an **empty box** (Copilot +
  auth only), point Copilot at the harness-setup skill, say *"set this up"*, and
  judge whether it produces a **validly-functional harness**. This is **F1-E**
  (suite self-assembly) escalating to **F3-E / turn-key assembly acceptance** (the
  vision's ultimate gate: fresh harness + empty knowledge repo completes a real
  task with zero manual setup).

Both are the **same pipeline** (§3) pointed at a different *starting state* and
*prompt*. The design makes the starting state and prompt the two scenario knobs so
one runner serves the whole spectrum.

> **Why this tier exists:** Tier P proves the *CLI surfaces* work; it cannot prove
> the **docs are followable by an agent**. A plugin can pass every Tier-P assertion
> and still be un-set-up-able because its SKILL/README doesn't tell an agent what
> it needs. Tier E is the only tier that audits the docs as *instructions to an
> agent*.

## 2. The non-negotiable: literal mode (why a naive eval lies)

A capable model will **hammer** at a broken or under-documented setup —
improvising, hand-installing missing pieces, retrying — and *reach a working end
state anyway*. That **masks the very gap under test** and turns a doc failure into
a green run. Every Tier-E run therefore puts the driven Copilot in a
**take-it-literally-or-bust** posture (see ARCHITECTURE.md §1 and the judge agent):

- Do **exactly** the named step; do **not** diagnose, repair, or work around.
- On the **first** obstacle, **stop and report verbatim** what blocked you.
- Treat the **absence** of an explicit "ready" signal as **not set up**, never
  "probably fine."

This is injected as **run-time instructions to the driven agent** (§6), never
baked into the plugins, and the [`clean-room-judge`](../../plugins/copilot-extensions-harness/agents/clean-room-judge.agent.md)
scores under the same rule: **a pass that depended on improvised self-repair is a
FALSE-PASS → FAIL**, and the finding is a *scenario/plugin* defect.

## 3. The end-to-end run loop

```
                          Tier-P gate (precondition; cheap, agent-free)
                                        │  green
                                        ▼
  ┌──────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────┐
  │ 1 build/ │──▶│ 2 establish  │──▶│ 3 register   │──▶│ 4 seed prompt │──▶│ 5 capture│
  │  start   │   │ starting     │   │ box as an    │   │ (literal-mode │   │ transcript│
  │  box     │   │ state (setup)│   │ bridge agent │   │  + purpose)   │   │  + report │
  └──────────┘   └──────────────┘   └──────────────┘   └───────────────┘   └────┬─────┘
                                                                                 │
                            ┌────────────────────────────────────────────────────┘
                            ▼
                    ┌──────────────┐   ┌───────────────┐   ┌────────────────┐
                    │ 6 optional   │──▶│ 7 assemble    │──▶│ 8 judge →       │
                    │ programmatic │   │ judge packet  │   │ record verdict  │
                    │ post-check   │   │ (expected +   │   │ (cr-eval.json)  │
                    └──────────────┘   │  evidence)    │   └────────────────┘
                                       └───────────────┘
```

1. **Build/start the box** — reuse today's `run.ps1`/`run.sh` build + auth-inject +
   persistent named container (`base` or `pristine` per the manifest).
2. **Establish the starting state** — run the scenario's `setup.sh` (partial
   harness): enable the marketplace, install the declared plugin subset, apply any
   fixtures (governed `pip.conf`, uv-index). This is *deterministic setup*, not the
   thing under test — it uses the same `lib/` helpers as a Tier-P scenario, but its
   phases only **arrange the box**, they do not assert the eval. For the **extreme**
   variant this is a near-no-op (bare box; the agent does the setup).
3. **Register the box as a bridge agent** — `bridge_register.py register` (the
   existing `command`-type provider agent: `docker exec -i cr-<img> bash -lc
   "copilot --acp --stdio --allow-all-tools"`), TTL-scoped.
4. **Seed the prompt** — send the **literal-mode framing** (§6) followed by the
   scenario's **stated-purpose prose** to the agent (`agent-bridge send <name>
   "<framing>\n\n<prompt>"`). One turn; the agent acts inside the box against the
   real docs/skills.
5. **Capture the transcript + report** — persist what the agent *did* (its reply,
   tool calls, and any files it touched) to the results dir as the judge's primary
   evidence (§7).
6. **Optional programmatic post-check** — an OPTIONAL `post_check.sh` runs the
   plugin's real CLI to capture *ground-truth* evidence of whether the box ended up
   set up (e.g. `agent-vault which` exits 0, the binstub is on PATH). This gives the
   judge an objective anchor beside the transcript; it never *substitutes* for the
   literal-mode judgment (the agent self-healing to a good end state is still a
   FALSE-PASS).
7. **Assemble the judge packet** — the `validating-in-clean-room` skill gathers
   {`manifest.json` expected outcome + stated-purpose prose, `cr-report.json`,
   `cr-logs/`, the transcript, the literal-mode instruction set} (exactly the
   inputs the judge agent documents).
8. **Judge → record** — hand the packet to `clean-room-judge`; record its
   structured verdict into `cr-eval.json` (§7). The runner does not itself decide
   PASS/FAIL for an eval — the judge does.

## 4. The Tier-E scenario contract

Extends the §4 scenario contract in ARCHITECTURE.md. A Tier-E scenario directory:

```
<scenario>/
├── manifest.json     # tier:"E"; image; prereqs; starting_state ref; prompt; expected_outcome; runs
├── setup.sh          # establish the STARTING STATE (partial harness). Sources lib/; setup-only phases.
├── prompt.md         # (optional) the stated-purpose prose seed, if not inline in manifest
├── expected.md       # (optional) the judge rubric / ordered intended steps, if not inline
├── post_check.sh     # (optional) programmatic ground-truth evidence AFTER the agent turn
└── fixtures/         # optional seed files (governed pip.conf, uv-index unjam, a seeded .kdbx, …)
```

`manifest.json` (Tier-E fields, on top of the shared ones):

```jsonc
{
  "name": "agent-vault-eval",
  "tier": "E",
  "family": "F2",                      // F1 | F2 | F3
  "image": "base",                     // base | pristine
  "prereqs": { "present": ["git","python3","node","uv"] },

  // The partial-harness STARTING STATE. "setup" names the setup driver; the
  // extreme variant sets this to a near-empty setup (bare box).
  "starting_state": {
    "setup": "setup.sh",
    "installed_plugins": ["agent-vault"],   // what setup.sh leaves installed (documentation; setup.sh is authoritative)
    "notes": "agent-vault installed solo; no worktree base; no .kdbx configured"
  },

  // WHAT the driven agent is told to do (stated-purpose prose). Inline or prompt.md.
  "prompt": "Using the agent-vault plugin that is installed here, set it up for use and then list the entries in my vault. Do exactly what agent-vault's own documentation tells you.",

  // WHAT a correct run looks like, for the judge to reconstruct the intended path.
  // Ordered, literal steps + the affirmative readiness signal to require.
  "expected_outcome": {
    "steps": [
      "Discover agent-vault's setup/usage docs (its SKILL/README).",
      "Follow the documented first-use path (the binstub self-provisions the runtime).",
      "Reach an AFFIRMATIVE readiness/lock state, or STOP and report the documented prerequisite (a configured .kdbx) is missing."
    ],
    "affirmative_ready": "agent-vault reports a ready/locked state, or clearly names the missing .kdbx prerequisite",
    "false_pass_if": "the agent hand-creates a .kdbx, edits config, or installs a missing prereq the docs didn't tell it to"
  },

  // Flake/cost policy for THIS scenario (see §8).
  "runs": { "count": 1, "aggregate": "unanimous", "per_turn_timeout_s": 900, "max_credits": 5 },

  // Optional programmatic anchor after the agent turn.
  "post_check": "post_check.sh"
}
```

**Design choices:**

- **Setup and eval are separated.** `setup.sh` only *arranges* the box (it may use
  `phase`/`pass`/`info` for its own legibility, but its passes are **setup
  telemetry, not the verdict**). This keeps "what we pre-installed" auditable and
  reusable, and prevents a scenario author from accidentally asserting the eval
  programmatically (that would be Tier P).
- **The prompt is the stated purpose, verbatim-ish.** It should read like a real
  user request and lean on *the plugin's own docs* ("do what its documentation
  says"), so the run audits the docs, not a bespoke script.
- **`expected_outcome` is a rubric, not an assertion.** It gives the judge the
  intended ordered path, the affirmative-ready signal to require, and the explicit
  **false-pass tripwires** (self-heals that must fail the run).
- **`family` selects the question** (F1/F2/F3 per ARCHITECTURE §1) and, for the
  extreme variant, `starting_state.setup` shrinks toward empty.

## 5. Two worked scenarios (the beachhead + the north star)

- **`agent-vault-eval` (F2-E, the beachhead).** Starting state: `agent-vault`
  installed solo, no `.kdbx`. Prompt: *"set up agent-vault and list my vault
  entries, following its docs."* A correct **literal** run either reaches an
  affirmative ready/locked state **or stops and reports the missing `.kdbx`
  prerequisite** — both are PASS. A run where the agent *hand-creates a vault* to
  "get it working" is a **FALSE-PASS** (the docs failed to state the prereq, or the
  plugin didn't fail-closed). This directly audits the agent-vault SKILL/README as
  *instructions to an agent* — the cheapest, most falsifying first eval.
- **`harness-from-bare` (F1-E → F3-E, the north star).** Starting state: bare box
  (Copilot + auth). Prompt: *"set up this harness"* pointed at the harness-setup
  skill. Judge whether the suite self-assembles (binstubs, projects, worktrees,
  bridge) with **zero manual steps**, escalating to the vision's **turn-key
  assembly acceptance** (bind an empty knowledge repo; complete a real task). This
  one is **name-ful** (it names a specific harness/skill) and therefore **homes
  downstream** (ARCHITECTURE §6), mounted via `-Scenario <dir>`.

## 6. Literal-mode fixture (substrate-owned, reusable)

A single shared **constrained-agent instruction block** lives in the rig substrate
— proposed `lib/literal-mode.md` — and is injected as the **framing prefix** of the
seed turn (before the scenario prompt). It is **never** baked into any plugin. It
encodes the brittle-witness posture:

> You are validating documentation on a fresh machine under **literal mode**. Do
> **exactly** the step you are asked, using only what the relevant plugin's own
> documentation instructs. Do **not** diagnose, repair, install missing
> prerequisites, edit configuration, or work around any obstacle. The **absence**
> of an explicit "ready/success" signal means *not set up* — never assume it
> worked. On the **first** thing that blocks you, **stop immediately** and report,
> verbatim: the exact command/step, its exact output, and what you believe is
> missing. Reaching a working end state by improvising is a **failure of this
> test**, not a success.

A scenario MAY append extra constraints (`literal_mode.extra` in the manifest), but
cannot weaken the base. The judge is handed the exact injected text so it can flag
where literal mode broke down.

## 7. Artifacts & report shape

Tier-E runs write to the same machine-local, out-of-tree results dir as Tier P,
adding an `eval/` subtree and a sibling **`cr-eval.json`**:

```
<results>/<timestamp>/
├── cr-report.json         # setup.sh + post_check.sh telemetry (Tier-P-shaped)
├── cr-logs/               # per-phase setup/post-check logs
└── eval/
    ├── prompt.txt         # the exact seed (literal-mode framing + stated purpose)
    ├── transcript.txt     # human-readable driven-agent transcript
    ├── turns.jsonl        # structured per-turn record (tool calls, outputs) when available
    ├── literal-mode.txt   # the exact injected instruction block
    └── cr-eval.json       # the judge verdict + run metadata (below)
```

`cr-eval.json`:

```jsonc
{
  "scenario": "agent-vault-eval",
  "tier": "E", "family": "F2", "image": "base",
  "runs": [
    { "n": 1, "verdict": "PASS|FAIL|FALSE-PASS", "first_break": "<step or null>",
      "self_heals": ["<verbatim workaround, if any>"], "jams": ["<category>: <evidence>"],
      "credits": 2.7, "duration_s": 240, "transcript": "eval/transcript.txt" }
  ],
  "aggregate": { "verdict": "PASS|FAIL", "policy": "unanimous", "fix_owner": "<plugin/effort>" },
  "judge": "clean-room-judge", "judged_at": "<iso8601>"
}
```

The judge's structured verdict (its documented `VERDICT/STEPS/JAMS/FIX-OWNER`
shape) is stored under each run; `aggregate.verdict` applies the flake policy (§8).

## 8. Flakiness, cost & determinism policy

Tier E runs a **real model in a loop** — non-deterministic and credit-costing. The
policy that keeps it honest and affordable:

- **Local-only, never a blocking CI gate (today).** Per the vision's Non-Goals,
  Tier E is a **contribution/audit tool**, gated **behind** Tier P (which is the
  cheap CI-able gate). A run needs the bridge daemon + real credits.
- **Tier-P precondition.** Do not spend an eval on a box that fails Tier P — the
  runner refuses `-Mode eval` for a plugin whose `*-solo` Tier-P scenario is red
  (a broken CLI surface will fail the eval for the wrong reason).
- **N-run aggregation.** `runs.count` (default **1** for exploration) with
  `aggregate` ∈ {`unanimous`, `majority`}. **A claim used to gate a change
  requires `count ≥ 3` + `unanimous`** — a single green agent run is evidence, not
  proof. A **split** result is itself a finding (the docs are ambiguous enough that
  the agent sometimes self-heals) and is reported, not hidden.
- **Budgets & timeouts.** `per_turn_timeout_s` is **enforced** host-side (the drive
  runs in a bounded job; a turn that overruns is stopped and recorded
  `timed_out: true` — a hung agent is a FAIL, not an infinite wait).
  `runs.max_credits` is **advisory only**: the `agent-bridge create` transport does
  not surface per-turn credits to the runner, so it is recorded as intent but
  cannot be hard-enforced (a future transport that exposes usage would close this).
- **Tier-P precondition (enforced, cheap).** Before spending the drive, the runner
  runs a cheap in-box smoke of the plugin CLI — `manifest.tier_p_precondition`, else
  `<first installed plugin> --version` — and **refuses the eval** if it fails (a
  broken CLI surface would red the eval for the wrong reason). On by default;
  `-SkipTierPGate` forces past it.
- **Determinism caveats, stated not hidden.** Model version, temperature, and doc
  drift all move the result; `cr-eval.json` records the `copilot_version` and a
  hash of the injected prompt + the plugin docs the agent could see, so a verdict
  is reproducible-in-context and a regression is attributable.
- **Cost containment via literal mode.** The brittle-witness posture *also* caps
  cost: a literal agent **stops at the first obstacle** instead of burning credits
  hammering — so a broken scenario fails cheap.

## 9. Runner surface

Additive to today's `run.ps1` (`-Mode eval` is implemented in `run.ps1`; `run.sh`
parity is a follow-up); no change to Tier-P behavior.

```powershell
./run.ps1 -Scenario agent-vault-eval -Mode eval            # run one eval end-to-end (setup→gate→drive→capture)
./run.ps1 -Scenario agent-vault-eval -Mode eval -Runs 3    # N-run for a gating claim
./run.ps1 -Scenario agent-vault-eval -Mode eval -SkipTierPGate   # force past the in-box Tier-P precondition
./run.ps1 -Scenario harness-from-bare -Mode eval -Image base
```

- **`-Mode eval`** (auto-selected when `manifest.tier == "E"`) runs the §3 loop.
  Steps 1–2 reuse the existing build/setup path; step 3 reuses `bridge_register.py`;
  steps 4–5 add the **seed+capture** helper; steps 7–8 hand off to the
  `validating-in-clean-room` skill's judge delegation.
- **Judge invocation stays in the skill, not the shell runner.** The runner
  produces `eval/` evidence and *prints the exact judge packet path*; the skill (or
  an orchestrator) invokes `clean-room-judge` and writes `cr-eval.json`. This keeps
  the public shell runner free of any model/agent dependency (it stays
  Tier-P-hermetic) and keeps judging where the skill already documents it.
- **Transcript capture** is the one genuinely new mechanism: `agent-bridge send`
  returns the agent's reply, but a *full* transcript (tool calls + outputs) is
  richer evidence. Capture, in order of preference: (a) the agent-bridge session
  transcript for the sent turn; (b) the in-container `copilot --resume` session
  log; (c) at minimum, the returned reply text. Whichever is available lands in
  `eval/transcript.txt` (+ `turns.jsonl` when structured).

## 10. Homing (generic vs. downstream)

Per ARCHITECTURE §6, the split is unchanged and matters more for Tier E:

- **Generic, name-free E scenarios** (e.g. `agent-vault-eval` — "set up *this*
  plugin from its own docs") belong in the **public rig** (`tools/clean-room/
  scenarios/`): they audit a copilot-extensions plugin's own docs and name no
  operator repo.
- **Name-ful E scenarios** (e.g. `harness-from-bare` — "set up *my* harness",
  turn-key acceptance against a specific knowledge repo) live with the **consuming
  harness** and are mounted via `-Scenario <dir>`. The literal-mode fixture, the
  runner `-Mode eval` path, `cr-eval.json`, and the judge are all substrate, so a
  downstream E scenario needs no substrate change.

## 11. Phased build plan

1. **✅ Fixture + schema (docs → substrate).** `lib/literal-mode.md` shipped; the
   Tier-E scenario contract + `cr-eval.json` shape are specified here (§4, §7).
2. **✅ Reference eval scenario.** `scenarios/agent-vault-eval/` shipped
   (`manifest.json` + `setup.sh` + `expected.md` + `post_check.sh`) — the
   falsifying beachhead of §5.
3. **✅ Runner `-Mode eval` (seed + capture).** Implemented in `run.ps1`: establish
   the starting state (`setup.sh`), register the bridge agent, drive a **fresh
   session per run** (`agent-bridge create --prompt-file --expand all`; never
   `send`, which resumes a stale session), capture the transcript to `eval/`, run
   `post_check.sh`, and print the judge-packet path.
4. **✅ Judge wiring + `cr-eval.json`.** The `validating-in-clean-room` skill
   documents assembling the packet and invoking `clean-room-judge`; the verdict is
   written to `eval/cr-eval.json`. Validated end-to-end: `agent-vault-eval` yields
   a literal-mode PASS (agent stopped at the documented `.kdbx`/`KPDB` prerequisite;
   no self-heal, corroborated by `post_check.sh` ground-truth).
5. **◐ Flake/cost controls.** `-Runs N` + `runs.count`/`aggregate` are wired;
   `per_turn_timeout_s` is **enforced** (host-side bounded job → `timed_out`);
   the **prompt + docs hash** and `copilot_version` are recorded in
   `eval/eval-run.json` for reproducible-in-context verdicts; and a cheap **in-box
   Tier-P precondition** (`<plugin> --version`, `-SkipTierPGate` to force) refuses
   an eval on a broken CLI. **Advisory-only:** `runs.max_credits` (the
   `agent-bridge create` transport doesn't expose per-turn credits to the runner).
6. **☐ The extreme F1-E.** Author `harness-from-bare` (downstream, name-ful):
   bare box → "set this up" → judge suite self-assembly, escalating toward the
   vision's turn-key F3-E acceptance.

> **Also still to do:** **`run.sh` parity** — `-Mode eval` is implemented in
> `run.ps1` only; the Linux/WSL/macOS `run.sh` does not yet carry it (it errors on
> an unknown mode). Track as a follow-up.

Each phase is independently useful: even Phase 3 (drive + capture, human-judged)
already delivers the operator's core ask — *point a fresh Copilot at our docs and
see what it does*.

## 12. Open questions / risks

- **Transcript fidelity over ACP/agent-bridge.** Exactly what structured turn data
  `agent-bridge send` exposes for a single-turn drive determines how rich
  `turns.jsonl` can be; Phase 3 must probe this and fall back to reply-text capture.
- **One-shot vs. multi-turn.** The design assumes a **single seed turn** (cleanest
  for literal mode). A future variant could allow a bounded multi-turn "keep going"
  loop, but that widens the self-heal surface — deferred.
- **Judging cost.** The judge is itself a model call; keep its input tight (the
  packet, not raw megabytes of logs) so an eval's total credit cost stays bounded.
- **Doc-drift attribution.** A verdict is only meaningful against the docs the
  agent could see; the prompt+docs hash (§8) is the mitigation, but a doc change
  that flips a verdict must be visible in review.

## See Also

- Architecture: [`ARCHITECTURE.md`](./ARCHITECTURE.md) (tiers, families, scenario
  contract §4, jam taxonomy §5, execution model §7)
- Operator guide: [`README.md`](./README.md)
- Vision: [`visions/clean-room-validation/README.md`](../../visions/clean-room-validation/README.md)
  (turn-key assembly acceptance is the ultimate F3-E gate)
- Judge: [`clean-room-judge`](../../plugins/copilot-extensions-harness/agents/clean-room-judge.agent.md)
  (the evaluator this design feeds)
- Skill: **`validating-in-clean-room`** (the caller that assembles the judge packet
  and records the verdict) — `copilot-extensions-harness` plugin
- Transport: [`bridge_register.py`](./bridge_register.py) (the `docker exec … copilot
  --acp --stdio` bridge agent used to drive the box)
