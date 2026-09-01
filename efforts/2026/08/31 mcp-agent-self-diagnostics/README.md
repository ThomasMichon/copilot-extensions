# MCP Agent Self-Diagnostics

- **Slug:** `mcp-agent-self-diagnostics`
- **Repo:** copilot-extensions
- **Branch(es):** managed worktree → generated PR branch
- **Created:** 2026-08-31
- **Status:** Done
- **Vision:** Harness Guidance §Features `authoritative-ownership`; §Behaviors `guidance-follows-ownership`, `task-detail-on-demand`
- **Umbrella issue:** #1520

## Guiding Intent

Make every plugin that owns an MCP-backed sub-agent self-diagnosing: its
task-time guidance should route failures to a discoverable troubleshooting
skill, and its README should state the dependencies and prerequisites required
for the agent to produce data. The customization reviewer should enforce this
shape consistently for owned plugins and surface advisory findings for external
plugins.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Implementation host | Plan, scanner implementation, tests, and PR | isolated issue worktree |

## Coordination

- **Topology:** One worktree and one PR sequence: reviewed plan, then implementation.
- **Host (owns PRs):** Implementation host.
- **Delegates:** Independent reviewer for design critique only.
- **Handoff:** The effort README is the durable checkpoint.

## Context

The customization scanner already checks MCP readiness sections,
anti-self-delegation, and materialized fallback policy. It does not verify that
an MCP-owning plugin ships a troubleshooting skill, nor that the plugin README
declares its dependencies and runtime prerequisites. This leaves operational
recovery and setup requirements implicit even when the agent itself is
well-formed.

The change is an implementation of the existing Harness Guidance vision:
detailed procedures remain discoverable in skills, guidance ships with its
owner, and reusable policy is checked mechanically where possible.

## Request

> We should ensure that every plugin which has an agent includes a
> troubleshooting skill for its MCP, plus the README.md files for each plugin
> should be explicit about dependencies. Probably worth making that a durable
> guideline in customizing-copilot, enforceable via reviewing-customizations.

## Plan

### Phase 1 — Define the owned-plugin contract
- [x] Add the troubleshooting-skill and README-dependency requirements to the
      `reviewing-customizations` guidance.
- [x] Define conservative mechanical signals that avoid false claims about
      external or non-MCP agents.

### Phase 2 — Enforce and document
- [x] Extend `scan-customizations.py` with owned blocking and external advisory
      findings.
- [x] Add focused scanner fixtures for compliant and non-compliant plugins.
- [x] Update the customizing-copilot README/inventory if the enforced contract
      changes its documented capability.
- [x] Bump the customizing-copilot plugin version consistently.

### Phase 3 — Validate and land
- [x] Run the customizing-copilot test suite and touched-file lint.
- [x] Run repository version and install-contract guards.
- [x] Run the scanner against copilot-extensions and one consuming harness.
- [x] Submit, review, merge, deploy, and archive this effort.

## Validation Plan

- [x] A local plugin with an MCP-owning agent and no troubleshooting skill is a
      blocking finding.
- [x] A loaded external plugin with the same gap is an advisory warning with an
      upstream fix path.
- [x] A plugin with a discoverable troubleshooting skill passes.
- [x] An MCP-owning plugin README that omits dependencies/prerequisites is
      reported; a compliant README passes.
- [x] Plugins without MCP-owning agents are unaffected.
- [x] Existing scanner checks and context-budget behavior remain unchanged.

## Proposal

Reviewed in PR #1525 and merged before implementation. Copilot's final review
reported no findings.

## Journal

### 2026-08-31 — Kickoff
- Claimed #1520 and created the target-owned effort.
- Reconciled the change to the existing Harness Guidance vision; no vision
  revision is required.

### 2026-08-31 — Reviewed plan and implementation
- PR #1525 merged the reviewed plan after all automated-review findings were
  addressed.
- Added owned blocking and external advisory checks for plugin-packaged MCP
  recovery skills and README dependency/prerequisite sections.
- Added suite, local-marketplace, external-plugin, deduplication, near-miss,
  fenced-heading, and non-MCP fixtures; 83 scanner tests pass.
- Real scans of copilot-extensions and the consuming harness worktree found no
  owned violations after the corresponding plugin guidance was added. The
  consuming-harness remediation must merge before this enforcement change.

### 2026-08-31 — Landed, deployed, and archived
- The prerequisite recovery guidance landed in
  `gim-home/odsp-web-harness#157` and
  `tmichon_microsoft/dotfiles#1970`.
- PR #1549 merged the scanner enforcement after Copilot's final review reported
  no findings and required checks passed.
- The unified update deployed `customizing-copilot@0.1.0-dev54`; its installed
  scanner ran successfully against the consuming harness. The broader update
  also reported unrelated runtime-reconcile warnings for other plugins, which
  are outside this effort.
- Moved the completed effort to the dated archive.
