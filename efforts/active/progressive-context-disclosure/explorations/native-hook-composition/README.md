# Native hook composition - historical exploration

[Back to the canonical effort and journal](../../README.md#journal).
Coordination and remaining proof gates belong to #1612 and the effort's
Phase 5 native-host seam.

**Historical, unvalidated exploration artifacts. Not an installed, active, or
draft clean-room scenario. Not evidence that any released runtime passes.**
The five source snapshots in [source-snapshots.zip](source-snapshots.zip) were
uncommitted work in progress from an older local exploration. They are preserved
as non-executable data, not standalone source files. Preservation does not
execute, repair, or endorse them. Their
original proof-oriented wording is a proposed rubric, not a result or a
replacement for the effort's validation gates.

**Do not execute the archived payload.** The ZIP has no `__main__.py`, contains
only five regular-file members with non-executable `0644` modes, and has no
standalone Python, shell, or scenario manifest files beside it. Re-execution
requires deliberate extraction into a disposable environment and separately
reviewed safety, activation, and validation work first. Extraction alone does
not make the historical implementation safe or supported.

In the archived snapshot, `fixture.py prepare --root ...` recursively
deletes an existing root with `shutil.rmtree` without a sentinel or containment
check. A mistaken argument can destroy unrelated data. The archive is for
inspection only; any future runnable implementation needs a separately reviewed
destructive-operation safety boundary.

## Preserved design

- ZIP member `expected.md` records the proposed rubric;
  `manifest.json` records the intended inputs and three fresh
  ACP sessions.
- ZIP member `fixture.py` generates two independent direct-output plugins.
  Each plain `sessionStart` hook emits its own 192-bit random canary as
  `additionalContext`, without a shared wrapper or composition authority.
- Model-invisible invocation markers are intended to distinguish whether both
  hooks ran from whether both outputs reached the model.
- The intended response is strict compact JSON with each expected canary once,
  lexically sorted, no invented token, and no tools or file reads to reconstruct
  missing context.
- ZIP members `setup.sh` and `post_check.sh` preserve the original
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

Activation is also unvalidated: `activate()` writes `enabledPlugins` entries
with `False` values to user settings, while preparation enables them in repository
settings. The name and setup success text are not evidence of activation; if
the disabling entries take precedence, the plugins will not load. Future reuse
must establish the actual settings-precedence behavior rather than assume the
archive's installation path works.

The archived setup script also does not check `activate` for success before
reporting installation success; its `set -uo pipefail` does not imply `set -e`.
That status message is not reliable evidence of readiness.

The shell post-check's PASS text is abbreviated: the transcript predicate also
checks strict JSON response count and absence of tool markers. Neither that
message nor the original proof-oriented prose supersedes these limitations.

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

Packaging changed only the storage form: the exact sanitized bytes are members
of a standard ZIP, not executable source at the repository's HEAD. No bespoke
archive runner or extraction tooling is supplied. Member read-back verified
all five sanitized hashes below, non-executable modes, absence of an entry
point, and decoded-content publication scanning without extracting or running
the payload.

| Archive | Bytes | SHA-256 |
|---------|------:|---------|
| `source-snapshots.zip` | 9720 | `5c64b5e386025be825d145afe2d2dbbec55d6ade08f1037de884c5f2cfeece4a` |

Sanitized ZIP member SHA-256 values:

| Member | Bytes | SHA-256 |
|--------|------:|---------|
| `expected.md` | 3303 | `d0a112d12d18e7d316344f02286135b353dbfd44d55c7d5f00d8f20816353784` |
| `fixture.py` | 14941 | `946b052bce0b4c32bf3122cbfa08f4bb82c9a08242909cb7055b6517b59f0243` |
| `manifest.json` | 3699 | `1b704fcd21e4b6fc465a402aa45794af8fcf772f82962d95244fe5c4fb245b99` |
| `post_check.sh` | 1069 | `3c86f65cc308e578899d5bdbb667d45a049509912f392326629a564a4735224f` |
| `setup.sh` | 1953 | `4e8e9b39d4b2372f2c3ee75f3204388cda50b6860068852294023afb5ab20044` |

Original SHA-256 values, measured before rehoming:

| File | Bytes | Original SHA-256 |
|------|------:|-----------------|
| `expected.md` | 3370 | `58a4904b72dd0fa38ddd67cf5443360dcfa097f432ae61df32ee10c5167843fa` |
| `fixture.py` | 14946 | `fbdb05121e2d04ebaaba120c2bc343d735ed4761c4c4d763df3562b7a7c36b3a` |
| `manifest.json` | 3704 | `a786136c5d9da2cf009baa5ab0209a60f4bb41bffde20416f26579c92ea24f20` |
| `post_check.sh` | 1069 | `3c86f65cc308e578899d5bdbb667d45a049509912f392326629a564a4735224f` |
| `setup.sh` | 1953 | `4e8e9b39d4b2372f2c3ee75f3204388cda50b6860068852294023afb5ab20044` |
