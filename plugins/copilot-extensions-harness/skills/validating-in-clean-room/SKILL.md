---
name: validating-in-clean-room
description: >
  How to validate copilot-extensions plugins and assembled harnesses in the
  disposable clean-room rig (tools/clean-room/): run a scenario on a fresh box,
  evaluate cr-report.json / cr-logs and classified jams, delegate Tier-E
  literal-mode judging to clean-room-judge, and author scenarios against the
  scenario contract. Use when running, reading, or extending clean-room
  validation of install/bootstrap/provision/behavior flows. Trigger phrases
  include:
  - 'clean room'
  - 'clean-room test'
  - 'validate plugin install'
  - 'run the clean room'
  - 'fresh machine test'
  - 'clean-room scenario'
  - 'author a clean-room scenario'
  - 'evaluate a clean-room run'
  - 'does this plugin self-provision'
  - 'validate on a fresh box'
  - 'pristine box test'
---

# Validating in the clean room

The clean room is a disposable **fresh machine** (a Docker box with a stock
login-shell PATH and none of your runtime state) that turns "I *think* the
install/bootstrap/provision flow does X" into a hard **PASS/FAIL** line. This
skill is the operator's map for three things: **run** a scenario, **evaluate**
the result, and **author more**.

- **Intent (why):** the [clean-room-validation vision](../../../../visions/clean-room-validation/README.md).
- **Architecture (what):** [`tools/clean-room/ARCHITECTURE.md`](../../../../tools/clean-room/ARCHITECTURE.md)
  — the tiers (P/E), families (F1/F2/F3), the scenario contract, and the jam taxonomy.
- **Operator guide (invocation detail):** [`tools/clean-room/README.md`](../../../../tools/clean-room/README.md).

Resolve the `copilot-extensions` checkout first (its path varies by machine — do
not hardcode it); the rig is at `<checkout>/tools/clean-room/`.

## Mental model (read once)

Validation is a **matrix**: two *tiers* × three *families* (full detail in
ARCHITECTURE.md).

- **Tier P — programmatic:** deterministic, agent-free, CI-able. Assert on the
  plugins' real CLIs (binstub on PATH, `--help`/`doctor` exits 0, `register`/
  `create`/`finalize` round-trips). No credits.
- **Tier E — eval:** drive the in-container Copilot over agent-bridge and **judge**
  the outcome — for questions no deterministic check can answer. **Always under
  literal mode** (below). Costs credits; gate it behind Tier P.

The **rig is name-free and public**; scenarios that name specific repos live with
the consuming harness and are mounted in via `-Scenario <dir>`. The checked-in
public scenarios are mostly Tier-P/F1 (plus the configurable
`generic-single-plugin` reference); **Tier-E is now runnable end-to-end** via
`-Mode eval` (drive + capture) plus the `clean-room-judge` verdict, with
`agent-vault-eval` as the public reference eval. Name-ful eval scenarios (e.g. a
whole-harness "set this up" from bare) still live downstream.

## 1. Run

From `tools/clean-room/` (`run.ps1` on Windows, `run.sh` on Linux/WSL/macOS):

```powershell
./run.ps1 -Mode all                          # build -> run the default scenario
./run.ps1                                     # run generic-single-plugin (base image)
./run.ps1 -Scenario <name|dir>               # run a named/self-contained scenario
./run.ps1 -Image pristine -Mode shell        # drop into the harshest fresh box (headed copilot)
./run.ps1 -Image base -NameSuffix agc        # second concurrent base box (cr-base-agc)
./run.ps1 -Until 3 -Then shell               # prepare through stage 3, then hand off to a shell
./run.ps1 -NpmRegistry https://…/npm/        # build-time Copilot CLI feed on governed hosts
./run.ps1 -UvIndex https://…/pypi/simple/    # opt-in uv-index fixture (governed box)
./run.ps1 -TokenAccount <user>               # choose the host gh account for token injection
./run.ps1 -NoToken -Mode auth                # fallback device-code cached :authed image path
./run.ps1 -Mode bridge-register              # expose the box as an agent-bridge agent (Tier-E transport)
./run.ps1 -Scenario agent-vault-eval -Mode eval   # Tier-E: setup -> drive Copilot (literal-mode) -> capture transcript + judge packet
./run.ps1 -Scenario agent-vault-eval -Mode eval -Runs 3   # N-run for a gating claim (see flake policy)
./run.ps1 -Image pristine -Mode down         # remove the container
```

```bash
./run.sh all
./run.sh --scenario <name|dir>
./run.sh --image pristine shell
./run.sh --image base --name-suffix agc
./run.sh --until 3 --then shell run
./run.sh --npm-registry https://…/npm/ run
./run.sh bridge-register
```

Notes:
- **Pick the image by what you're falsifying.** `base` (git, python3, node, uv)
  for a plain install check; `pristine` (Copilot + git, with only the system
  `python3` a real box would have — no venv module, pip, uv, `~/.local/bin`, or
  feed governance) to force self-provisioning so uv/venv/pip-feed jams
  **surface**.
- **Auth is borrowed, not tested.** By default a Copilot token from your host
  `gh` is injected automatically; the account must have Copilot entitlement
  (`-TokenAccount <user>` / `--token-account <user>` to pick which `gh` account).
  `-NoToken` / `--no-token` falls back to the explicit device-code `auth` image.
  Never bake credentials into a base/pristine image.
- **Governed box.** Pass an internal npm feed explicitly to install the Copilot
  prereq (`-NpmRegistry …`); the runner does **not** auto-forward host config
  (that would bias the fresh-machine experiment). The `toolchain-uv` asymmetry
  (pip's feed set, uv's not) is reproduced with `-UvIndex` / the fixture.
- **Never write run artifacts into the repo tree.** Results land in a machine-local
  dir outside the repo (the run prints its exact path). The rig may run from an
  anchor checkout; per-run state in a repo is a hazard.

Parameterize the reference scenario for a quick single-plugin check via
PowerShell params (`-MarketplaceRepo`, `-MarketplaceName`, `-PrimaryPlugin`,
`-ExpectDeps`) or cross-platform `CR_*` env: `CR_PRIMARY_PLUGIN`,
`CR_EXPECT_DEPS`, `CR_MARKETPLACE_REPO/NAME`, `CR_UV_INDEX`, `CR_UNTIL`,
`CR_RESULTS_DIR`.

## 2. Evaluate

Each run writes a machine-local dir: `cr-report.json` (structured PASS/FAIL) plus
per-phase logs under `cr-logs/`.

- **Read the verdict:** per-stage `pass`/`fail`, the `env{}` snapshot, and the
  classified `jams[]`. A red line is never bare — it carries a **jam category +
  evidence + hint** (see the taxonomy in ARCHITECTURE.md §5).
- **Act on the jam, don't paper over it.** A `toolchain-uv` jam (the #1 governed-box
  failure) means uv's index is unconfigured, not that uv is missing — the hint
  derives the internal index from pip's config. Confirmed fixes flow back to the
  **owning plugin/effort**; the clean room proves, it does not house the repair.
- **Tier-E judging — delegate to `clean-room-judge`.** A Tier-E eval is a
  two-step flow: **(1) drive + capture** with `-Mode eval` — it establishes the
  scenario's starting state (`setup.sh`), registers the box as a bridge agent,
  drives the in-container Copilot with the **literal-mode fixture + the scenario's
  stated-purpose prompt** (a *fresh* session per run), and captures the
  transcript(s) to `<results>/eval/` alongside an optional programmatic
  `post_check.sh` (ground-truth self-heal tripwires). It prints the **judge
  packet** path and does **not** itself judge. **(2) judge** — hand that packet
  (the scenario's `manifest.json` `expected_outcome` + `prompt`/`expected.md`, the
  `eval/transcript.txt`, `cr-report.json`, `cr-logs/`, and `eval/literal-mode.txt`)
  to the **`clean-room-judge`** sub-agent; write its verdict to `eval/cr-eval.json`.
  It renders PASS/FAIL + evidence **under literal-mode rules**: it credits only the
  literal task and treats a "pass" that depended on the agent improvising around a
  broken setup as a **FALSE-PASS → FAIL** (a scenario/plugin defect). Do not eyeball
  an eval as passing because the agent "eventually got there." Full execution design
  (manifest fields, artifacts, flake/cost policy): [`TIER-E-EXECUTION.md`](../../../../tools/clean-room/TIER-E-EXECUTION.md).

### Literal mode (non-negotiable for Tier E)

A capable model will hammer at a broken setup — improvising, hand-installing
missing pieces, retrying — which **masks the gap under test**. Every Tier-E
scenario must inject a constrained-agent instruction set: *do exactly the named
step; do not diagnose/repair/work around; on the first obstacle stop and report
verbatim what blocked you.* This "brittle witness" profile is what makes an eval
falsifying instead of self-healing. Inject it as scenario instructions to the
bridge agent — **never** bake it into the plugins.

## 3. Author more

A scenario is a **self-describing directory** (contract in ARCHITECTURE.md §4):

```
<scenario>/
├── manifest.json   # name; image variant; prereq presence; required auth; expected artifacts; ordered stages
├── scenario.sh     # sources lib/clean-room-lib.sh; defines its stages via the helper API
└── fixtures/       # optional seed files (governed pip.conf, opt-in uv-index unjam, …)
```

- **Use the shared lib API** (`lib/clean-room-lib.sh`, mounted read-only) so every
  scenario reports uniformly: `phase` · `pass`/`fail`/`info` · `capture <label> --
  <cmd…>` · `envdump` · `jam <category> <evidence> [hint]` · `cr_meta` ·
  `cr_finalize`. Copy `scenarios/generic-single-plugin/` as the reference.
- **Assert on filesystem/CLI outcomes, not exact CLI syntax**, so a scenario stays
  robust across `copilot` versions and records the surface it saw.
- **Keep the public rig name-free.** A scenario that names a specific repo or
  internal venue is *not* a substrate change — it lives with the **consuming
  harness** and is run via `-Scenario <dir>` (bind-mounted verbatim). Only generic,
  reference scenarios belong in `tools/clean-room/scenarios/`.
- **Emit a classified jam on every failure** (pick the right taxonomy category) so
  the result is legible and, where a fix is deterministic and safe, the scenario can
  apply the unjam and re-run the stage idempotently.
- **Pick the family + tier deliberately.** A deterministic CLI assertion is Tier P;
  a "can a fresh agent carry out the plugin's stated purpose" check is Tier E under
  literal mode (and scored by `clean-room-judge`).

### Current public scenarios

Under `tools/clean-room/scenarios/` today:

| Scenario | Tier/family | Purpose |
|----------|-------------|---------|
| `generic-single-plugin` | reference | Configurable install → bootstrap → binstub → plugin-load → register check. |
| `agent-worktrees-solo` | P/F1 | Worktree base stands up solo and round-trips `register` → `create` → `finalize`. |
| `agent-bridge-solo` | P/F1 | agent-bridge provisions and read verbs answer without an agent-worktrees base. |
| `agent-codespaces-solo` | P/F1 | agent-codespaces read verbs degrade safely without an agent-worktrees base. |
| `agent-containers-solo` | P/F1 | agent-containers provisions solo; its knowledge-overlay `state-root` shell-out falls open and read verbs answer without the base. |
| `agent-ssh-solo` | P/F1 | agent-ssh (standalone profile emitter/verifier) installs with no sibling and its transport-contract read verbs answer. |
| `agent-machines-solo` | P/F1 | agent-machines (standalone reconciler) provisions; `restore` (default dry-run) refuses cleanly on validator errors instead of crashing on absent config. |
| `agent-logger-solo` | P/F1 | agent-logger (standalone) provisions; read verbs answer and `session-sync run --dry-run` reports would-push only. |
| `agent-mcp-solo` | P/F1 | agent-mcp (the standalone MCP-wrapper exemplar, no bridge) provisions; `validate` schema-checks a stdio bridge and cleanly rejects a missing one. *(Docker smoke-run green.)* |
| `agent-dispatch-solo` | P/F1 | agent-dispatch (standalone) provisions; `--version` matches package+manifest (not the fallback) and read verbs answer on an empty queue. |
| `agent-index-solo` | P/F1 | agent-index (standalone) provisions the **service** without the heavy engine; status/role read verbs and the direct/bridge MCP surfaces answer. |
| `agent-vault-solo` | P/F1 | agent-vault (standalone secret store) provisions binstub + `vault-askpass`; read verbs report cleanly with no `.kdbx` (no KeePassXC hard-dep). |
| `agent-bridge-cutover` | P/F1 | agent-bridge zdd cutover mechanism: routing flip, drain gate, recovery. |
| `agent-dispatch-cutover` | P/F1 | agent-dispatch cutover preserves queued/held work and heals aborted/wedged cases. |
| `agent-index-cutover` | P/F1 | agent-index service cutover preserves durable queue state and heals drain/recovery cases. |
| `agent-vault-cutover` | P/F1 | Forward-ready witness: proves agent-vault's client-side rendezvous fallback ladder; flags the daemon-side zdd cutover as not-yet-adopted (INFO, #609). |
| `agent-vault-eval` | **E**/F2 | **The reference agent-driven doc-audit:** install agent-vault solo, then drive Copilot under literal mode with "set it up and list my vault, per its docs" — judged (via `clean-room-judge`) on whether the docs carry a fresh agent to an affirmative ready state **or** an honest STOP at the documented `.kdbx`/`KPDB` prerequisite, with no self-heal. |
| `suite-assembly-eval` | **E**/F1 | **Suite self-assembly from bare (the public "extreme"):** install the harness core (agent-worktrees base + agent-bridge), then drive "get the suite working per its docs, then register this repo and create a worktree" — judged on whether the suite's own docs carry a fresh agent through the real `setup → register → create` assembly via documented commands (no hand-edited `projects.yaml` / raw `git worktree`). Surfaced #691 (agent-worktrees doesn't self-provision). |

**The matrix to build toward** (per the vision): each plugin **solo** *(now
complete — every runtime plugin has a solo scenario)* → **reasonable
combinations** → the **full assembled harness**, each **with and without** the
worktree base (does an enhanced feature degrade safely when its base is absent?),
culminating in the turn-key assembly acceptance (a fresh harness + empty knowledge
repo completes a real end-to-end task with zero manual setup).

## Contribution norm

A contribution to `copilot-extensions` that affects **install, bootstrap,
provisioning, or plugin behavior** should, **when practical, run or extend** the
relevant clean-room scenario(s) — so coverage ratchets forward and regressions are
caught on a fresh box, not in the field. This is a norm, not a blocking gate; the
`contributing-to-copilot-extensions` skill points here.

## See Also

- Vision: [`visions/clean-room-validation/README.md`](../../../../visions/clean-room-validation/README.md)
- Architecture: [`tools/clean-room/ARCHITECTURE.md`](../../../../tools/clean-room/ARCHITECTURE.md)
- Operator guide: [`tools/clean-room/README.md`](../../../../tools/clean-room/README.md)
- Judge sub-agent: `clean-room-judge` (this plugin, `agents/clean-room-judge.agent.md`)
- Contributing: `contributing-to-copilot-extensions` (this plugin)
