# native-hook-composition-eval - expected outcome

This Tier-E witness proves whether the **host itself** now composes
`additionalContext` from every independent `sessionStart` hook
(native multi-hook composition)
instead of retaining only the last hook's output
([github/copilot-cli#3589](https://github.com/github/copilot-cli/issues/3589)).
It intentionally contains **no** custom aggregation authority, wrapper,
resolver, rendezvous, cache, or adoption configuration of any kind -- see
`docs/patterns/README.md` and the retired
`efforts/active/custom-context-aggregator-retirement/README.md` for why that
layer was removed. This scenario exists to prove the "Future Native
Composition" gate documented there.

## Starting state

`setup.sh` creates a local synthetic marketplace with two independent
plugins, `synthetic-hook-alpha` and `synthetic-hook-beta`. Each plugin:

- declares exactly one plain `sessionStart` command hook;
- writes its own distinct high-entropy `CRHOOK_CANARY_...` token directly as
  `additionalContext`; and
- shares no code, wrapper, authority, or configuration with the other
  plugin.

One disposable repository enables both plugins directly in
`.github/copilot/settings.json`. There is no `.context-injection` directory,
adoption config, or any other cross-plugin composition mechanism.

## Tier-P prerequisite

Before the bridge turn, `fixture.py tier-p` proves the fixture itself is
correct: each plugin's hook script, invoked directly and independently with a
synthetic payload, emits exactly its own distinct canary. This catches a
fixture bug before spending a real Tier-E run.

## Tier-E pass

For every fresh agent-bridge session:

- the final transcript contains one exact compact JSON response;
- both expected canaries occur exactly once;
- no invented canary occurs; and
- no tool call or file read reconstructs the response.

A result reached by reading fixture files, settings, plugin payloads, or
diagnostics is a false pass and must be scored FAIL.

## Reading the verdict

- **Both tokens, every run:** the host now composes `additionalContext` from
  every independent `sessionStart` hook. This is the proof the "Future Native
  Composition" gate requires before any plugin may replace its exact-session
  guidance writer with direct output.
- **Exactly one token, every run:** the historical single-hook-wins behavior
  still holds at this container's runtime floor. The exact-session guidance
  pattern remains the only reliable path; do not activate native composition.
- **Neither token, or an inconsistent mix across runs:** classify as a
  scenario/transport gap or a genuine regression -- not evidence either way
  about composition. Re-run before drawing a conclusion.

Run the scenario with at least three fresh sessions (the manifest default).
The overall claim is unanimous: any run that disagrees with the others is
itself a finding (the host's behavior is non-deterministic across sessions),
not something to average away.

## Classified gap

If the stock CLI cannot install the local marketplace, or a fresh
agent-bridge ACP session cannot load the installed plugins and execute their
`sessionStart` hooks, classify the result as `scenario-transport-gap`. That
is a transport/runtime limitation, not evidence about composition either way.
