# copilot-extensions-harness

A **payload-only** Copilot CLI plugin that ships the **operator harness** for the
copilot-extensions repo — the skills to work *on* the plugin suite. Its
**primary purpose** is to make sure any agent using an enabling plugin can
quickly identify which local scripts/processes/paths belong to this system,
restore canonical installer-equivalent artifacts within authorized repair, and
never monkey-patch runtime code. The
[footprint reference](../../docs/installation-footprint.md#installer-equivalent-repair)
owns the inventory and verification map, including failed/refused installer
subtasks and preserved state. Its **secondary purpose**, for repos that also choose to be
contributors, is to describe the correct flow for landing a real fix. Enable it
in any control repo and your agent knows how to **diagnose** the deployed
runtimes, **contribute** changes to the plugins, and **validate** them on a
fresh box, without you hand-writing a per-repo guide or installing a runtime.

The plugin declares four checked-in static projections. Adopting
repositories synchronize them with the
`customizing-copilot:reviewing-customizations` manager; each remains available
without a competing `sessionStart` output.
- **`contribution-boundary`** — the full guide remains versioned at
  [`references/contribution-ground-rules.md`](references/contribution-ground-rules.md):
  generic, organization-neutral capabilities are welcome; personal or
  organization-specific needs are routed elsewhere.
- **`cross-repo-debug-tracking`** — before concluding an `agent-*` or
  `context-handoff` plugin's source is undocumented, or filing an upstream
  bug against one, resolve its actual checked-out location first (for example
  `<agent-worktrees catalog argv[0]> related resolve <repo>` -- the exact
  `argv[0]` from the session command catalog, never a bare PATH lookup), and
  cross-link a local symptom's tracking issue with its upstream
  `ThomasMichon/copilot-extensions` issue/PR in both directions.
- **`commented-review-verdict`** — a PR review verdict that comes back as a
  plain comment (not an approval/change-request) is Copilot's normal
  non-blocking review shape absent a repo-specific override; read it as
  advisory and land the change rather than chasing a further verdict that
  may never arrive.
- **`validate-and-promote-triage`** — a red `validate-and-promote` release
  gate blocks every pending contributor's work, not just whoever broke it;
  identify every job that actually failed (a per-plugin fan-out job, or
  `worktree-manager`/`guards-full-sweep`), and for a fan-out failure
  classify a known accepted flake apart from a CI-environment-specific test
  bug apart from a genuine regression, then fix
  forward. Full runbook in the
  [diagnosing-validate-and-promote-failures](skills/diagnosing-validate-and-promote-failures/SKILL.md)
  skill.

External-service outage reports carry
[first-party bulletin receipts and scoped attribution](references/external-service-outages.md).
The release guide's [GitHub Actions errata](skills/diagnosing-validate-and-promote-failures/references/github-actions.md)
distinguish platform startup/runner failures from executed test failures and
retain normal recovery and publication gates.

| Skill | Priority | Covers |
|-------|----------|--------|
| [reviewing-copilot-extensions-vision](skills/reviewing-copilot-extensions-vision/SKILL.md) | Explicit review assignment | Applies the public [Maintainer's Vision Adherence Review](references/maintainer-vision-adherence-review.md) as a conceptual repository lens; can pair with a generic reviewer without replacing CI, tactical review or merge gates |
| [diagnosing-copilot-extensions](skills/diagnosing-copilot-extensions/SKILL.md) | **Primary (hard guidance)** | Resolve artifact ownership and source/payload/current/running differences; complete authorized canonical installer-equivalent repair when official subtasks fail/refuse; preserve state and verify consumers, never patch runtime package code or erase whole roots |
| [diagnosing-validate-and-promote-failures](skills/diagnosing-validate-and-promote-failures/SKILL.md) | Secondary (opt-in) | Diagnose a red `validate-and-promote` release-pipeline run: isolate the failing per-plugin fan-out job via `gh run view --log`, classify a CI-environment-specific test bug apart from a genuine regression, and land the fix forward |
| [contributing-to-copilot-extensions](skills/contributing-to-copilot-extensions/SKILL.md) | Secondary (opt-in) | Repo layout, the PR-required worktree flow (`create` → `create-pr`/`push-changes` → `pr-merge --now` → `finalize`), the submitter's hard **merged + finalized** completion gate, the **mandatory version bump**, test + install-contract gates, deploy-after-merge, and source-of-truth rules |
| [checking-copilot-extensions-release](skills/checking-copilot-extensions-release/SKILL.md) | Secondary (opt-in) | Verify a PR's effective changes at a pinned `main` snapshot, despite batched promotion and different commit history; distinguish release from local installation |
| [validating-in-clean-room](skills/validating-in-clean-room/SKILL.md) | **Run · evaluate · author** clean-room validation (`tools/clean-room/`): fresh-box scenarios, `cr-report.json` + `cr-logs/`, jam taxonomy, Tier-E literal-mode judging, and the scenario contract |

The plugin also ships `.agent-worktrees/related.yaml`, so an active
`copilot-extensions-harness` contributes portable, lowest-precedence provenance
for the `copilot-extensions` repository. Machine-local registration still owns
the checkout path and repository class, and derives the operator-relative
ownership posture (`owned` for the maintainer, `external` for other users).
Repository operations require a public GitHub account; enterprise-managed
GitHub accounts are appropriate for internal organizations, while Entra
credentials apply to Azure DevOps rather than this GitHub repository.

The plugin also registers one **preToolUse** guard (`hooks.json` +
`scripts/copilot-mention-guard.py`): it denies a `gh` invocation that would
publish an `@copilot` mention in a PR/issue comment or review, scoped to this
plugin's own repo (`ThomasMichon/copilot-extensions`, read live from
`plugin.json`'s `repository` field rather than a second hardcoded string --
a maintainer rename of that field is the only place to update; a GitHub
*fork* does NOT retarget the guard, since a fork's checked-in `plugin.json`
still names the upstream repo). On GitHub, that mention doesn't nudge the
review bot; it delegates to the separate Copilot **cloud coding agent**,
which starts pushing its own commits directly to the PR branch. See
`CONTRIBUTING.md`'s own "Do not comment `@copilot review`" rule for the
written policy this hook mechanically enforces. A control repo enabling this
plugin purely for its instruction projections is never touched by the guard
-- it only fires for a `gh`
invocation whose current directory resolves to this plugin's own target repo,
and only when the workspace explicitly enables `copilot-extensions-harness@<marketplace>`
in `.github/copilot/settings.json` or `settings.local.json`. A matching local
`false` overrides the repository setting. Global installation or user-level
enablement alone is not adoption: unrelated workspaces (including VS Code)
return a no-op, without repository or body discovery. Adoption lookup stops at
the nearest repository boundary; a nested checkout cannot inherit its parent's
opt-in. Missing or malformed project settings do not activate the guard.
Relative body-file and API-input paths use that same resolved workspace, never
the plugin payload/process directory.

The shell entries also fail open when the helper/interpreter is unavailable or
exits unsuccessfully, discarding partial output and emitting only a bounded,
sanitized diagnostic. Unexpected Python exceptions signal the cooperating
wrapper to report that diagnostic while the host-facing hook still exits
successfully; direct legacy callers retain a zero exit code. Deliberate mention/uninspectable-body denials in an adopted,
matching repository remain policy decisions, not execution failures.

The camelCase registration uses `matcher: "bash|powershell"`. Copilot CLI
matches this case-sensitively against the complete runtime `toolName`, before
launching either shell command. Other tools never reach this guard's shell or
Python process. This matches the original evaluator's policy domain; it does
not add a read exemption to another guard or expand aggregate tool metadata.
Shell input, interpreter lookup, live controls, policy and failure handling
are unchanged. No input buffering, decoder, source-hash check or cache is added.

This declaration requires Copilot CLI's command-hook matcher support
(1.0.63 or later); unsupported hosts must retain the previous declaration.
See the [official matcher contract](https://docs.github.com/en/copilot/reference/hooks-reference#matcher-filtering).
The provider feature precedes this declaration change; rolling back the
declaration requires no state migration. A host that ignores the optional
filter retains the original evaluator's no-op path, but that is not proof of
support for hosts that reject unknown configuration.
`registered_matcher_preserves_original_policy_domain` checks the registration
against real evaluator decisions, including exact shell names, non-shell
events, case and qualified-name boundaries. Direct wrapper tests bypass host
registration: they prove the retained evaluator/transport, not live CLI
filtering or installed throughput.

The Python evaluator first classifies publication syntax locally, using the same tokenizer
and write predicates as the body scanner. Its ordinary reads and unrelated shell
commands perform no repository/manifest/body discovery and start no additional child
processes. This is not a mention-substring filter: body files, stdin, implicit
create bodies, command prefixes and compound statements still reach the scoped
policy check.

Relevant decisions share a **1.5-second monotonic budget**, including manifest
and body-file reads and repository discovery. One `git remote --verbose` call
uses only the remaining budget, independently of remote count; only each
remote's first fetch URL counts, with Git's URL rewrites preserved. Git timeout
cleanup kills and reaps that child. A slow file read cannot hold the decision:
its daemon reader is confined to the short-lived hook process and ends when
that process exits. Unknown repository scope remains fail-open as before;
once the target repository is established, uninspectable or timed-out bodies
remain fail-closed. This does not change destination policy or address the
PowerShell/GraphQL parsing gaps tracked in #3677.

The irrelevant-command path also avoids importing Python's subprocess stack;
relevant calls load it within the remaining decision budget, without caching
repository authority or changing publication classification.

The budget starts in Python's entry point, not before shell/interpreter startup;
OS process creation and termination can also add overhead. It is **not** proof
that all installed hooks together finish within two seconds. The focused
`test_copilot_mention_guard.py` native consumer test measures actual Python and
shipped PowerShell-hook wall time, reports empty-shell startup separately, and
audits zero/one Git children for irrelevant/relevant inputs against 24 remotes.
Timing output includes sample count and maximum, not just the median. The
PowerShell wrapper uses the engine's application lookup directly rather than
repeated command/filter/select pipelines; it preserves `python3`, `python`,
then `py` priority and excludes WindowsApps aliases. Fixture tests compare
selection with the prior resolver, and native timing includes that resolver
as a separate baseline. Shell and interpreter startup remain real costs.
Windows path construction and output forwarding use in-process framework
operations rather than path/output cmdlets. The wrapper still invokes the
original Python evaluator, forwards its input, retains the live kill switch
and policy/diagnostic decisions, and discards partial output from failed
children. Bash behavior is unchanged.

For a fixed-fixture Windows A/B/A comparison, select
`windows_wrapper_module_attribution` with `COPILOT_MENTION_PROFILE_BASELINE`
set to a full lowercase commit ID and `COPILOT_MENTION_PROFILE_BASELINE_REPO`
to its readable source checkout. Both shells run fresh processes against
the original evaluator; only the wrapper command changes. Git reads the
baseline without modifying it. The comparison requires the selected child
to complete and preserves the 10-second child, 30-second item and 300-second
group limits. Finite samples do not qualify installed composition or tails.
`windows_wrapper_preserves_policy_and_failure_contract` checks actual
denials, live-stop behavior, missing/failed helpers and input/output forwarding
while trapping path/output cmdlets. These explicit native cases can also use
the [owner-only hosted Windows lane](../../TESTING.md#owner-only-hosted-windows-escape-hatch).

It never executes a proposed `gh` publication or performs a network request:

```text
python tools/run-plugin-tests.py copilot-extensions-harness --test-file tests/test_copilot_mention_guard.py --test-timeout 30 --timeout 300 --plugin-timeout 300
```

| Sub-agent | Covers |
|-----------|--------|
| [clean-room-judge](agents/clean-room-judge.agent.md) | Read-only Tier-E evaluator: scores a clean-room eval run against a scenario's stated outcome under **literal-mode** rules (credits only the literal task; a self-heal "pass" is a false pass), emitting PASS/FAIL + classified jams |

## Enable

Keep this plugin enabled at repository/workspace scope, not as a machine-wide
hook policy. Existing global availability does not opt a project into its guard;
the repository-local declaration below is required. No runtime, binstub,
service, or setup script is involved. Enabling the plugin is
the whole install; restart the session so the skills and agent are scanned.
Synchronize the declared instruction projection in each adopting repository so
the contribution boundary is available on launch paths that load checked-in
instructions.

In a control repo, declare the marketplace (if it is not already declared) and
enable the plugin in `.github/copilot/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "copilot-extensions": {
      "source": { "source": "github", "repo": "ThomasMichon/copilot-extensions" }
    }
  },
  "enabledPlugins": {
    "copilot-extensions-harness@copilot-extensions": true
  }
}
```

Then use the skills directly by asking to contribute to copilot-extensions,
diagnose an installed plugin/runtime, or validate a plugin in the clean room.

The retained cross-platform boundary emitter is not registered as a
**sessionStart** hook. It is the policy producer seam for a future direct
plugin-owned `additionalContext` path, which may be activated only after
native host composition is proven at the supported Copilot CLI version floor
-- multiple plugins' own `additionalContext` currently has no composition
story. The `preToolUse` guard above has no such composition hazard (each
plugin's `preToolUse` hooks run independently, with nothing to compose), so
it is registered normally.

## The `<repo>-harness` standard

This plugin is the reference implementation of a small, reusable pattern.

A **harness plugin** is a payload-only plugin, **named `<repo>-harness`**, shipped
**by** a repo, that provides the skills to operate *on* that repo — typically to
**contribute** to it and **diagnose** it. Because it lives in the repo it
describes, it is **versioned with that repo** and **portable**: any control repo
adopts it with one `enabledPlugins` line, whether the adopter contributes
directly or only needs to diagnose when something breaks.

### How it differs from a related-narrative

| | Harness plugin (`<repo>-harness`) | Related narrative |
|---|---|---|
| **Authored by** | the repo **owner**, once | each **consumer**, per control repo |
| **Ships from** | the target repo (marketplace) | the consumer's control repo |
| **Point of view** | neutral, portable | that consumer's POV |
| **Versioning** | tracks the repo it describes | tracks the consumer repo |
| **Adopt via** | enable `<repo>-harness@<marketplace>` | write `.agent-worktrees/related/<repo>.md` |

They compose: a consumer can **enable the harness plugin** for the authoritative
operator skills and keep a thin related-narrative (or trigger-redirect skill)
only for the *consumer-specific* bits — which machines deploy it, local policy,
adoption status. Prefer the plugin for the substance; keep the narrative thin.

### Authoring your own

To ship a `<repo>-harness` plugin for a different repo, use the
**`customizing-copilot:authoring-harness-plugins`** skill — it
walks the structure (this plugin as the template), the naming rule, what the
contributing/diagnosing skills should contain, and how consumers adopt it.

## License

[MIT](../../LICENSE)


### Emergency pre-tool control

The default installation supports `agent-machines pretool-killswitch on
"<reason>"`, `status --json` and `off`. This explicitly pauses suite-owned
preToolUse workflow guards, not native Copilot permissions or other hook events.
For scope, hot-session delivery, installation-cell isolation and rollback, see
[`libs/bootstrap-killswitch/README.md`](../../libs/bootstrap-killswitch/README.md#emergency-pre-tool-workflow-guard-switch).
The control is inactive until explicitly enabled; a deployed release alone does
not activate it.
