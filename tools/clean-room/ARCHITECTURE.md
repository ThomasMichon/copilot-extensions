# Clean-room rig — architecture

> **What this is:** the concrete architecture of the clean-room validation rig
> that lives in this repo at [`tools/clean-room/`](./README.md). For the
> *operator guide* (how to invoke it) see [`README.md`](./README.md); for the
> *north-star intent* see the [clean-room-validation vision](../../visions/clean-room-validation/README.md);
> for *how to run, evaluate, and author scenarios* use the **`validating-in-clean-room`**
> skill (in the `copilot-extensions-harness` plugin).

The rig is deliberately **generic and name-free** of any operator's repos: it is
the public substrate. Scenarios that name specific repos or internal venues live
with the **harness that consumes them**, mounted into the box at run time (see
*§6 Ownership & homing*).

## 1. The matrix — tiers × families

The clean room is not one flow but a **matrix**: two *validation tiers* (how we
test) crossed with three *subject families* (what is under test).

### Two validation tiers (how)

- **Tier P — Programmatic** (deterministic, CI-able, no model in the loop).
  Install Copilot → auth via an injected token → drive the plugin install/update
  CLI → then run the **`agent-*` CLIs themselves** and assert on their output.
  Because the plugins carry real programmatic surfaces, the flow is verifiable
  *without* an agent: each binstub exists and is on PATH, `--help`/`version`/a
  `doctor`/status subcommand exits 0, `agent-worktrees repos`/`projects`
  recognizes a registered repo, `create`/`finalize` round-trips a worktree,
  `agent-bridge agents`/`machines` enumerates, venue plugins list their venues.
  Fast, hermetic, exit-code + JSON → suitable as a **scheduled CI job** and a
  local run.
- **Tier E — Eval** (agent-driven, non-deterministic, local). Use **agent-bridge**
  to drive the in-container Copilot through a scenario and **judge** the outcome.
  Covers what Tier P cannot: does "set up X for this repo" work one-shot; do
  degenerate subset installs self-repair or fail clearly; do assembly-requiring
  casual queries resolve. Runs locally (needs the bridge + real credits), gated
  behind Tier P.

> **Literal mode is a first-class Tier-E requirement.** A capable model will
> *hammer* at a broken setup — improvising workarounds, installing missing
> pieces by hand, retrying — which **masks the very gap under test** and turns a
> fail into a false pass. Every Tier-E scenario injects a small constrained-agent
> instruction set that puts the driven Copilot in a *take-it-literally-or-bust*
> posture: do exactly the named step; do **not** diagnose/repair/work around; on
> the first obstacle **stop and report** verbatim what blocked you. This "brittle
> witness" profile is what makes an eval falsifying instead of self-healing. It
> is injected as scenario instructions to the bridge agent, **never** baked into
> the plugins. The **`clean-room-judge`** sub-agent scores under the same rule:
> credit only the literal outcome, never improvised self-repair.

### Three subject families (what)

| Family | Subject | Primary tier | The question |
|--------|---------|--------------|--------------|
| **F1 — copilot-extensions suite** | the `agent-*` plugins themselves | **P** (CI) + **E** (extended) | Does enabling/installing the suite on a bare box yield a *working, self-provisioned* harness — binstubs, projects, worktrees, bridge — with no manual steps? |
| **F2 — downstream plugins on top** | plugins layered on bare Copilot + the suite | **E** (fail-fast) | Installed solo, does each plugin **self-diagnose and fail fast** — guiding the user to run its setup — instead of silently not working? |
| **F3 — assembled harness** | a fully-configured harness | **E** (assembly) | After a fresh checkout of a fully-configured harness, do **casual requests that require the assembled plugin set** resolve correctly? |

**F1 subset-installs** are a specifically valuable degenerate case: prove a plugin
stands up **without the worktree base** (e.g. `agent-codespaces`/`agent-bridge`
without `agent-worktrees`). Those plugins are loose-coupled by design (data-file +
binstub shell-outs, no hard import) and **degrade-safe** — the flow asserts that
contract holds and surfaces any place a missing base becomes a hard failure
instead of a graceful degrade.

## 2. The self-sufficiency contract (the property under test)

The cross-cutting property the clean room exists to enforce, per plugin: **every
extension, installed solo, must (a) stamp a callable binstub on first session,
(b) self-provision its runtime on first use, and (c) surface readiness and guide
the next correct step.** Fail-closed. Three
mechanism components:

1. **First-install on first session (not just reconcile).** The `bootstrap-check`
   sessionStart hook must perform the grace-window-cheap `stamp` when the runtime
   is absent; the stamped binstub performs the expensive provision on first use.
2. **Affirmative readiness confirmation.** Provisioning state is conveyed as a
   **positive "ready" signal** (emitted only when venv + version marker + binstub
   all check out); its **absence is treated as "not set up,"** never inferred from
   a negative "I'm broken" message (a hook that never ran emits *nothing*).
3. **Skill fail-closed prerequisite check.** A plugin's skill requires the
   affirmative readiness confirmation before acting and, if it is absent for any
   reason, **stops and guides** rather than attempting the operation — and states
   the plugin's own minimum prerequisites.

Tier P checks (1)/(2) structurally (marker + binstub present); Tier E checks the
capability end-to-end (install solo → hand the agent the plugin's stated purpose
as prose → judge completion) under literal mode.

## 3. Fresh-machine fidelity variants

Fidelity is selectable so a scenario picks how much the box may assume:

| Variant | Toolchain present | Use |
|---------|-------------------|-----|
| **base** | git, python, node, uv | Install checks where a stock dev toolchain is assumed. |
| **pristine** | **Copilot + git only** (a system `python3` exists, but no venv module / pip / uv / `~/.local/bin` / feed governance) | The harshest fresh internal box — forces the harness to provision its own toolchain, so uv/venv/pip-feed jams **surface** instead of hiding. |

The images keep a distro `rg` under `/opt/copilot-cleanroom/bin`, outside the
scenario's stock PATH. The Tier-E ACP command alone prepends that directory and
sets `USE_BUILTIN_RIPGREP=false`, avoiding Copilot's bundled ARM64 `rg` failure
on 16 KiB-page hosts without changing Tier-P prerequisite fidelity. It also
exposes the compatibility PATH to the driven agent's subprocess tree and
disables core dumps so a failing agent tool cannot dirty the fixture it is
supposed to inspect.

Two orthogonal knobs a scenario sets:

- **Prereq presence** — which of {python, uv, gh, node} are pre-present vs. must
  be provisioned by the flow. Start harshest and relax as we learn what the setup
  flow can legitimately assume.
- **Feed governance** — an **opt-in** fixture reproducing the corp-box case. The
  realistic profile is an **asymmetry, not a clean block**: policy configures
  **pip's** internal feed (a global `pip.conf` `index-url`) but **not uv**, so
  `pip install` resolves against the mirror while **uv** — used for every venv —
  still defaults to the TLS-blocked public PyPI. This is the single
  highest-probability provisioning jam; the fixture mirrors it exactly (write the
  internal `pip.conf`, leave uv unconfigured), and is never baked into an image
  (that would make the box non-fresh and bias the experiment).

## 4. The scenario contract

A scenario directory is self-describing so the public runner stays name-free:

```
<scenario>/
├── manifest.json      # name; image variant; prereq presence; required auth; expected artifacts; ordered stages
├── scenario.sh        # sources lib/clean-room-lib.sh; defines its stages via the helper API
└── fixtures/          # optional seed files (e.g. a governed pip.conf, an opt-in uv-index unjam)
```

The shared **`lib/clean-room-lib.sh`** (substrate, mounted read-only) provides the
uniform helper API every scenario uses, so assertions, reporting, and diagnostics
are consistent:

- `phase <n> <title>` — a stage boundary (also the `--until` gate).
- `pass <msg>` · `fail <msg>` · `info <msg>` — the report vocabulary.
- `capture <label> -- <cmd…>` — run a command, tee stdout/stderr to
  `cr-logs/<label>.log`, record its exit code.
- `envdump` — snapshot `PATH`, `which` of key tools, versions, and named config
  files into the report.
- `jam <category> <evidence-ref> [<hint>]` — emit a **classified failure** (§5).
- `cr_meta <key> <value>` · `cr_finalize` — scenario metadata + report close-out.

The report (`cr-report.json`) carries per-stage PASS/FAIL, an `env{}` snapshot, and
a classified `jams[]` array. **`generic-single-plugin`** is the reference scenario
(today's Layer-0 install check) — it proves the substrate generalizes without any
internal dependency, and is fully parameterizable via `CR_*` env
(`CR_PRIMARY_PLUGIN`, `CR_EXPECT_DEPS`, `CR_MARKETPLACE_*`, `CR_UV_INDEX`,
`CR_UNTIL`).

## 5. The diagnostic layer — classified jams

Every failing stage emits a **jam**: `{category, evidence (captured logs +
envdump), hint, [autofix]}`. The taxonomy makes a red line *legible*:

| Category | Triggers on | Example unjam |
|----------|-------------|---------------|
| `toolchain-uv` | uv absent, or (**most common**) present but its index is unconfigured (pip's internal feed is set, uv's is not) so `uv` hits the blocked public PyPI | derive the internal index from `pip config get global.index-url` and export it to uv (`UV_INDEX_URL`/`UV_DEFAULT_INDEX`) — uv does **not** read `pip.conf` |
| `toolchain-venv` / `pythonpath` | venv not created / wrong interpreter / PYTHONPATH leak | recreate under the runtime dir; unset inherited PYTHONPATH |
| `pip-feed-governance` | public PyPI blocked and no index configured for the tool in use | point both pip *and* uv at the internal index |
| `npm-registry` | `@github/copilot` / node install blocked | pass an internal registry build-arg |
| `auth-gh` / `auth-copilot` / `auth-ado` | clone/login/relay 401/403 | re-auth; correct account (EMU vs personal) |
| `repo-config` | knowledge binding / `state-root` unresolved | re-run the setup/link step |
| `codespace-config` | CodeSpace create/connect/relay fails | check machine/repo prereqs; relay port |
| `bridge-service` / `path-binstub` | binstub missing / `~/.local/bin` off PATH / bridge port down | first-session provision; export PATH; start service |
| `experimental-mode-gate` | plugins/extensions not honored | enable experimental mode |

Where an unjam is **deterministic and safe**, a scenario may apply it and re-run
the stage idempotently, recording the fix — turning the clean room into a
*repair-discovery engine*. Confirmed fixes flow back to the **owning plugin/effort**;
the clean room discovers and proves, it does not house the repair.

## 6. Ownership & homing

| Layer | Home | Why |
|-------|------|-----|
| **Substrate** — Dockerfiles (base/pristine), the host runner (build · auth · run · shell · bridge-register · down), the shared `lib/`, and the one generic reference scenario | **this repo** (`tools/clean-room/`), public, PR-gated | The rig is generic; it is name-free of any operator's repos. |
| **Scenarios** that name specific repos / internal venues | **the consuming (downstream) harness** | A scenario validates a specific assembled harness; it belongs with that harness and is bind-mounted into the box verbatim by the runner. |

The `-Scenario <dir>` seam is what keeps this split clean: the runner mounts a
self-contained scenario dir (plus the shared `lib/`) read-only into the box, so an
internal scenario carries all its specifics and needs no substrate change.

**Per-suite shared helpers.** A downstream harness that ships *several* related
scenarios can factor their common phases into a `_lib/` directory placed **beside**
the scenario dirs (i.e. `<suite>/_lib/`, a sibling of `<suite>/<scenario>/`). When
the selected scenario has such a sibling `_lib/`, the runner mounts it read-only at
`/home/operator/scenario-lib` and exposes it as **`$CR_SCENARIO_LIB`**, so each
scenario can `source "$CR_SCENARIO_LIB"/<file>.sh` instead of duplicating the phase
logic. This is opt-in and generic (the rig names nothing): absent a sibling `_lib/`,
behavior is unchanged. It complements the substrate `lib/` (`$CR_LIB`), which stays
the rig-owned assertion/reporting harness.

## 7. Execution model

- **Persistent named box + staged handoff.** The runner drives a persistent
  container so an automated scenario (all stages, or `-Until <n>` to stop early)
  can be followed by an interactive `shell` into the *same* box for the parts the
  CLI/ACP surface can't fully automate (headed `copilot` smoke tests). The box
  stays up until `down`.
- **Auth is borrowed.** A Copilot token from the host `gh` is injected as
  `COPILOT_GITHUB_TOKEN`; there is no in-container device-code step, and images
  stay credential-free.
- **Host-safe & out-of-tree.** Everything under test lives in the disposable box;
  run artifacts (`cr-report.json` + `cr-logs/`) are written to a **machine-local
  results dir outside any repo tree**, never into an anchor checkout.
- **Bridge-drive.** The box can be registered as an agent-bridge `command` agent
  (via the runtime provider API) so an orchestrator drives the in-container
  Copilot programmatically — the transport for Tier-E evals. The concrete
  **Tier-E execution design** (the seed-prompt → drive → capture → judge loop, the
  Tier-E scenario contract, the literal-mode fixture, and the flake/cost policy)
  is specified in [`TIER-E-EXECUTION.md`](./TIER-E-EXECUTION.md).

## See Also

- Operator guide: [`README.md`](./README.md)
- Tier-E execution design: [`TIER-E-EXECUTION.md`](./TIER-E-EXECUTION.md) (how an
  agent-driven eval run executes end-to-end — pre-build design)
- Vision: [`visions/clean-room-validation/README.md`](../../visions/clean-room-validation/README.md)
- Skill: **`validating-in-clean-room`** (run / evaluate / author more) — `copilot-extensions-harness` plugin
- Judge: **`clean-room-judge`** sub-agent — `copilot-extensions-harness` plugin
