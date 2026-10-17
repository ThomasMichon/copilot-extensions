# Native hook composition - historical exploration

[Back to the canonical effort and journal](../../README.md#journal).
Coordination and remaining proof gates belong to #1612 and the effort's
Phase 5 native-host seam.

**Historical, unvalidated exploration artifacts. Not an installed, active, or
draft clean-room scenario. Not evidence that any released runtime passes.**
These five files were uncommitted work in progress from an older local
exploration. Preservation does not execute, repair, or endorse them. Their
original proof-oriented wording is a proposed rubric, not a result or a
replacement for the effort's validation gates.

## Preserved design

- [expected.md](expected.md) records the proposed rubric;
  [manifest.json](manifest.json) records the intended inputs and three fresh
  ACP sessions.
- [fixture.py](fixture.py) generates two independent direct-output plugins.
  Each plain `sessionStart` hook emits its own 192-bit random canary as
  `additionalContext`, without a shared wrapper or composition authority.
- Model-invisible invocation markers are intended to distinguish whether both
  hooks ran from whether both outputs reached the model.
- The intended response is strict compact JSON with each expected canary once,
  lexically sorted, no invented token, and no tools or file reads to reconstruct
  missing context.
- [setup.sh](setup.sh) and [post_check.sh](post_check.sh) preserve the original
  disposable-environment preparation and post-check sketches. Their relative
  library references and generic container paths are historical; this directory
  is deliberately outside `tools/clean-room/scenarios/` discovery and is not a
  supported execution location.

## Known limitations and remaining gates

`fixture.post_check` records `all_hooks_invoked_every_run` but **omits it from
the PASS predicate**. Its verdict checks only that transcripts exist and every
transcript passes the canary/JSON/tool-marker checks. The configured three-run
count is not enforced by that predicate either. Do not treat an archived PASS
string or the original "both tokens" wording as native-composition proof.

The proposed classification distinguishes invocation failure
(`hook-invocation-incomplete`) from composition failure
(`context-composition-incomplete`), with separate literal-mode and transport
classifications. Because invocation completeness does not gate PASS, that
distinction is not sufficient to validate the original proof claim.

Three fresh ACP sessions would not establish the supported-version
fresh/resume/noninteractive/ACP matrix, compaction behavior, or cross-platform
coverage. This archive contains no run transcripts, passing evidence packet,
runtime-version provenance, or independently judged native-composition result.

Any future implementation must separately review and validate a fail-closed
invocation/composition predicate, exact run/session attribution and cardinality,
literal no-tool behavior, supported runtime floors and launch boundaries, and
independent evidence before considering scenario activation or replacing
exact-session guidance with direct multi-plugin output. These gates remain open
under #1612 and the canonical effort; no phase is completed by this archive.

## Preservation and publication

The five originals totaled **25,042 bytes**. Only publication sanitization was
applied: one non-public runtime proposal reference in each of `expected.md`,
`fixture.py`, and `manifest.json` was replaced with the generic phrase
"native multi-hook composition". No executable behavior, proposed rubric, or
known defect was changed. Both shell files are byte-for-byte preserved.

Original SHA-256 values, measured before rehoming:

| File | Bytes | Original SHA-256 |
|------|------:|-----------------|
| `expected.md` | 3370 | `58a4904b72dd0fa38ddd67cf5443360dcfa097f432ae61df32ee10c5167843fa` |
| `fixture.py` | 14946 | `fbdb05121e2d04ebaaba120c2bc343d735ed4761c4c4d763df3562b7a7c36b3a` |
| `manifest.json` | 3704 | `a786136c5d9da2cf009baa5ab0209a60f4bb41bffde20416f26579c92ea24f20` |
| `post_check.sh` | 1069 | `3c86f65cc308e578899d5bdbb667d45a049509912f392326629a564a4735224f` |
| `setup.sh` | 1953 | `4e8e9b39d4b2372f2c3ee75f3204388cda50b6860068852294023afb5ab20044` |
