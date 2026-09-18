# Skill runtime conformance

Companion to [reviewing-customizations](../SKILL.md). The installed Copilot CLI
owns YAML parsing, frontmatter interpretation, and its additional loading rules.
Check that actual implementation, not a vendored parser or reconstructed schema:

```text
python "<skill-dir>/scripts/validate_skill.py" "<target-skill>" --json
python "<skill-dir>/scripts/validate_skill.py" "<target-skill>" --expect-description "Intended parsed text"
```

`<skill-dir>` is the loaded `reviewing-customizations` directory; `<target-skill>`
is a skill directory or its SKILL.md. Resolve both paths before invocation.
The expectation form accepts one target and catches silent YAML comment/folding
changes. Multiple explicit targets can otherwise be checked together.

## Contract and prerequisites

Python 3.10+ and an installed Copilot CLI supporting `skill list --json` and
`--config-dir` are required. No Python/npm dependencies are installed.
The default finds `copilot` on PATH; `--copilot` selects an explicit executable
and repeated `--copilot-arg` values support a known interpreter/entrypoint pair.
Windows batch launchers are refused; use an executable or Node plus the
installed CLI entrypoint.

The command stages byte-identical SKILL.md files in isolated configuration,
copies no candidate hooks/settings/helpers, invokes no skill or model prompt,
and removes its temporary files. It checks original-byte integrity before
returning. The report records the CLI version used.

A zero CLI exit is insufficient: invalid skills can be omitted while listing
still succeeds. Acceptance requires the exact staged candidate directory,
well-formed loaded metadata, project source, and enabled state. Diagnostics,
unknown output, missing CLI, and timeouts cannot produce a pass. Duplicate names
may shadow a candidate in a batch; rerun the missing candidate alone.

## Runtime versus authoring policy

The default also checks the resolved name's lowercase kebab-case and a nonblank
description without angle brackets or non-whitespace control characters, with
a maximum of **1,024 UTF-16 code units**. These are distinct authoring-policy
findings, not claims that every runtime rejects those values. Unicode characters
outside the BMP count as two units. `--runtime-only` skips this policy layer,
not the runtime or explicit-description expectation.

Discovery output cannot establish that metadata was explicitly authored rather
than derived from the folder/body; the editorial review checks that separately.
An isolated file load does not prove real-suite plugin enablement, discovery
precedence, complete companion files, good triggering, or workflow correctness.
Acceptance applies only to the CLI version reported.

On a host without Copilot CLI, continue the editorial review but label runtime
conformance not checked. Do not substitute another parser or claim compatibility
with untested hosts. There is no automatic repository-wide publication gate.

## Helper regressions

Run the colocated standard-library tests with:

```text
python -m unittest discover -s "<skill-dir>/scripts" -p test_validate_skill.py
```

Set `COPILOT_SKILL_LIVE_TESTS=1` for explicit actual-CLI probes, including
copied-payload first use and poisoned-settings isolation. Ordinary tests use
synthetic data and do not require a CLI or model invocation.
