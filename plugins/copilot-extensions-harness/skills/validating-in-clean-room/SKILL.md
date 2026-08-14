---
name: validating-in-clean-room
description: >
  How to validate copilot-extensions plugins and assembled harnesses in the
  disposable clean-room rig (tools/clean-room/): RUN a scenario on a fresh box,
  EVALUATE the PASS/FAIL result (the cr-report.json + jam taxonomy, and Tier-E
  eval judging via the clean-room-judge sub-agent), and AUTHOR MORE scenarios
  against the scenario contract. Use when standing up, running, reading, or
  extending clean-room validation of the install/bootstrap/provision/behave
  flow, or when a contribution touches those flows and should be validated on a
  fresh box. Trigger phrases include:
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
the consuming harness and are mounted in via `-Scenario <dir>`.

## 1. Run

From `tools/clean-room/` (`run.ps1` on Windows, `run.sh` on Linux/WSL/macOS):

```powershell
./run.ps1 -Mode all                          # build -> one-time login -> run the default scenario
./run.ps1                                     # run generic-single-plugin (base image)
./run.ps1 -Scenario <name|dir>               # run a named/self-contained scenario
./run.ps1 -Image pristine -Mode shell        # drop into the harshest fresh box (headed copilot)
./run.ps1 -Until 3 -Then shell               # prepare through stage 3, then hand off to a shell
./run.ps1 -UvIndex https://…/pypi/simple/    # opt-in uv-index fixture (governed box)
./run.ps1 -Mode bridge-register              # expose the box as an agent-bridge agent (Tier-E transport)
./run.ps1 -Image pristine -Mode down         # remove the container
```

```bash
./run.sh all
./run.sh --scenario <name|dir>
./run.sh --image pristine shell
./run.sh --until 3 --then shell run
./run.sh bridge-register
```

Notes:
- **Pick the image by what you're falsifying.** `base` (stock toolchain) for a
  plain install check; `pristine` (Copilot + git only) to force self-provisioning
  so uv/venv/pip-feed jams **surface**.
- **Auth is borrowed, not tested.** A Copilot token from your host `gh` is
  injected automatically; the account must have Copilot entitlement
  (`-TokenAccount <user>` to pick which `gh` account). Never bake credentials into
  an image.
- **Governed box.** Pass an internal npm feed explicitly to install the Copilot
  prereq (`-NpmRegistry …`); the runner does **not** auto-forward host config
  (that would bias the fresh-machine experiment). The `toolchain-uv` asymmetry
  (pip's feed set, uv's not) is reproduced with `-UvIndex` / the fixture.
- **Never write run artifacts into the repo tree.** Results land in a machine-local
  dir outside the repo (the run prints its exact path). The rig may run from an
  anchor checkout; per-run state in a repo is a hazard.

Parameterize the reference scenario for a quick single-plugin check via `CR_*`
env: `CR_PRIMARY_PLUGIN`, `CR_EXPECT_DEPS`, `CR_MARKETPLACE_REPO/NAME`,
`CR_UV_INDEX`, `CR_UNTIL`.

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
- **Tier-E judging — delegate to `clean-room-judge`.** For an eval run (a driven
  Copilot transcript against a scenario's stated outcome), hand the scenario's
  expected outcome + the run's report/transcript to the **`clean-room-judge`**
  sub-agent. It renders PASS/FAIL + evidence **under literal-mode rules**: it
  credits only the literal task and treats a "pass" that depended on the agent
  improvising around a broken setup as a **false pass** (a scenario defect). Do
  not eyeball an eval as passing because the agent "eventually got there."

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

**The matrix to build toward** (per the vision): each plugin **solo** → **reasonable
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
