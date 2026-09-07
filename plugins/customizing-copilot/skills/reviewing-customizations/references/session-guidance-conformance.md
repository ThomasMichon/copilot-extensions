# Session Guidance Conformance Runbook

Return to [`reviewing-customizations`](../SKILL.md#session-guidance-conformance).

Use this runbook to migrate an adopting repository or a plugin-suite repository
to the current ambient-guidance contract:

1. **Current reliable path:** checked-in static pointer plus plugin-owned
   exact-session guidance file.
2. **Static-only policy:** reviewed checked-in projection with no dynamic hook.
3. **Output-free operational hook:** bootstrap, registration, or another
   side effect explicitly classified as `context: none`.
4. **Future path:** direct plugin-owned `additionalContext` only after native
   host composition is proven at the supported version floor.

A custom cross-plugin aggregation authority, producer wrapper, resolver,
rendezvous, shared cache, spill engine, or repository adoption config is not a
valid conformance option.

## Mode A: Conform an Adopting Repository

### 1. Update the plugin payloads first

Use the repository's normal plugin update/install workflow. Do not synchronize
projections from stale installed payloads.

### 2. Disable the retired authority at repository precedence

In `.github/copilot/settings.json`, remove any active
`context-injection@copilot-extensions: true` entry and add an explicit false
override when a user-global setting could otherwise reactivate it:

```json
{
  "enabledPlugins": {
    "context-injection@copilot-extensions": false
  }
}
```

Remove `.context-injection/config.yaml` and any repository-owned custom
aggregator, adapter, wrapper, resolver, cache, or spill configuration. Keep
historical effort/log evidence unchanged.

### 3. Synchronize every enabled projection

Run from the adopting repository:

```bash
python3 <skill-dir>/scripts/manage-instruction-projections.py sync <repo-root>
python3 <skill-dir>/scripts/manage-instruction-projections.py \
  scan <repo-root> --from-settings --json
```

Review the complete transaction. Every declared destination must be
provenance-marked and locked; unmarked local files, ownership conflicts, stale
locks, and budget excess are blocking.

### 4. Scan the settings-derived plugin roster

```bash
python3 <skill-dir>/scripts/scan-customizations.py <repo-root> \
  --from-settings --context-budget --strict
```

Accept only:

- an output-free stack;
- at most one possible non-empty `sessionStart` output; or
- a future multi-output stack covered by a separately proven native merge
  contract at the repository's supported runtime floor.

Do not accept an unknown external hook beside another possible non-empty
output. Do not treat hook execution as proof that model context arrived.

### 5. Run blind launch-path probes

Use hidden, high-entropy canaries that the task prompt never reveals. Cover the
launch paths the repository supports:

- fresh and resumed interactive sessions;
- prompt/autopilot;
- ACP;
- custom agents;
- explicit `--no-custom-instructions`;
- CodeSpace or other remote venues when supported; and
- hookless/App paths for the reviewed static baseline.

For dynamic guidance, prove the written file belongs to the exact session and
canonical CWD, stale prior-session files are not read, and a missing file fails
open without a fabricated value.

## Mode B: Conform a Plugin Suite

### 1. Inventory the complete marketplace roster

Derive the plugin list from the marketplace catalog. Do not maintain an
adopter-specific allowlist. For every plugin with `sessionStart`, classify each
hook as:

- exact-session guidance writer;
- static-only policy;
- output-free bootstrap/registration/side effect; or
- future native output-capable hook.

Unclassified and legacy-direct hooks fail the migration.

### 2. Conform dynamic guidance plugins

A dynamic guidance plugin ships:

- `instruction-projections.json`;
- a minimal `instructions/<topic>.instructions.md` pointer template;
- an output-free `sessionStart` wrapper;
- a bounded, atomic, path-contained writer using the hook payload's
  `sessionId` and `cwd`;
- plugin-owned emitters invoked fresh by that writer; and
- tests for malformed input, containment, stale replacement, byte budget,
  cross-platform wrappers, hook registration, and projection declaration.

The writer emits exactly `{}`. A classification manifest may retain a
historical schema name for compatibility, but it contains
`contributors: []` and `sessionStart.context: none`.

### 3. Conform static-only and operational plugins

A static-only policy plugin declares and tests its checked-in projection and
registers no dynamic guidance hook.

A bootstrap, registration, readiness, or lifecycle hook either carries a
complete output-free declaration or matches a suite-owned, mechanically
recognized output-free command shape. It must not invoke a context emitter
directly.

### 4. Remove retired machinery everywhere

Search every marketplace plugin and shared tool for:

```text
context-injection
invoke-context-contributor
resolve_context_authority
aggregate-authority
pair-key rendezvous
spill
```

Negative tests and explicit non-goal documentation may retain these strings.
Active hooks, payload files, setup guidance, patterns, and review instructions
may not.

### 5. Enforce the roster mechanically

The suite guard should derive expectations from manifests and projection
templates. It must prove:

- every output-free declaration is complete and has no contributors;
- every dynamic pointer projection has an exact-session writer;
- no retired adapter file exists in any marketplace plugin;
- no output-free hook invokes an aggregate producer; and
- the marketplace, plugin manifests, package versions, and docs agree.

Run the plugin suites, projection scan, settings-aware strict customization
scan, marketplace/version/docs consistency checks, and install-contract guards.

## Future Native Composition

When native host composition becomes eligible:

1. Pin the supported runtime floor containing the fix.
2. Prove every independent `additionalContext` value survives fresh, resume,
   non-interactive, ACP, and supported remote launch paths.
3. Activate direct plugin-owned output in a reviewed migration.
4. Keep static fail-safes for hookless paths.
5. Remove an exact-session compatibility pointer only after equivalent
   delivery is proven.

Never reintroduce a custom composition authority as an intermediate step.
