# Progressive context disclosure eval - expected outcome

This scenario runs one declared experiment cell. Its `manifest.json`
`experiment` object binds the deferral level, reference representation,
emphasis, Phase 2 assembly, task, model label, and repetition.

Generate an out-of-tree scenario for another cell before invoking the normal
clean-room runner:

```bash
python3 tools/clean-room/scenarios/progressive-context-disclosure-baseline/fixture.py \
  configure-scenario \
  --template tools/clean-room/scenarios/progressive-context-disclosure-eval \
  --output /tmp/progressive-context-cell \
  --deferral-level F3 \
  --reference-representation structured-reference \
  --emphasis safety-gated \
  --assembly flat-with-index \
  --task-id multi-guide \
  --model calibration-model \
  --repetition 1
```

Then pass the generated directory to `run.sh --scenario` or
`run.ps1 -Scenario`. The generated directory is self-contained: it carries the
frozen fixture plus the minimal current contributor-inventory snapshot needed
by setup, with normalized read-only-container permissions. Each configured
scenario contains exactly one task and one repetition; primary replication
uses three independently generated and run scenarios rather than combining
transcripts. Fresh and spill cells are runnable. Resume and compaction cells
are rejected until the clean-room runner has real drivers for those
transitions.

## Starting state

`setup.sh` first verifies the frozen Phase 1 corpus and baselines from the
read-only source mount. It then creates a fresh synthetic repository and plugin
payload under `/home/operator/progressive-context-disclosure-eval`.

Every run receives new random 192-bit guide canaries. Readable guide files exist
under the generated repository and payload roots, never in the scenario mount.
The driven agent remains forbidden from reading the bundled fixture, inventory
snapshot, corpus, task answer key, or rubric.
The ACP session starts in the generated repository and loads only the generated
synthetic plugin directory.

## Literal-mode judgment

Use the selected task in
`tools/clean-room/scenarios/progressive-context-disclosure-baseline/fixtures/tasks.json`
as the answer key. Credit only the literal task:

- every required critical rule is preserved before action;
- every required guide is read before grounded action;
- irrelevant reads are counted and broad compensating exploration is a false
  pass;
- real-link eager loading is recorded in `autoLoadedGuideIds`, separately from
  agent-initiated `observedGuideIds`;
- owner provenance is retained;
- unavailable and unsafe locators fail closed without an invented replacement;
  and
- reading the scenario fixture, source fixture, corpus, task answer key,
  rubric, generated context source, run metadata, or private canary map is a
  false pass.

After judging, use `fixture.py write-evidence` to write
`eval/progressive-context-evidence.json`, then use
`fixture.py validate-evidence` against that file. A setup or ACP transport jam
must use `write-invalid`; it is a zero-turn `INVALID` record and does not
eliminate the variant.

Primary fresh cells require at least three independent sessions and unanimous
correctness. Finalists repeat on a distinct model and then cross the resume,
compaction, spill, and ACP confirmations defined by the frozen protocol.
