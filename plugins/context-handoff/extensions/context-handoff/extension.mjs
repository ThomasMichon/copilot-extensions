// Context Handoff Extension for Copilot CLI
//
// Tracks session state and provides a generate_handoff_prompt tool
// for creating continuation prompts when context is getting large.
//
// Uses the session.usage_info event for accurate context window monitoring
// (currentTokens / tokenLimit) instead of heuristic turn counting.
//
// Integration points (all via observable session events -- the native runtime
// removed SDK callback hooks, so no `hooks` object is passed to joinSession):
// 1. generate_handoff_prompt tool -- on-demand structured handoff data
// 2. save_handoff_prompt tool -- persist composed handoff to machine-local state
// 3. session.usage_info event -- real-time context utilization monitoring
// 4. tool.execution_start / _complete events -- track modified files + tools
// 5. user.message event -- tracks turn count + first prompt (topic bias)
// 6. session.idle event -- delivers the queued context-pressure nudge to the
//    agent via session.send() (replaces onPostToolUse additionalContext). The
//    nudge JUST tells the agent to invoke the context-handoff skill; it does
//    not prescribe tool calls or a "write a file" outcome -- the skill owns the
//    sequencing (compose/save routinely; trigger only with explicit approval or
//    autopilot/pre-authorization).
//
// The /handoff gesture is handled as a skill invocation (context-handoff
// skill), not a slash command. The skill triggers the agent to call
// generate_handoff_prompt, compose prose, and call save_handoff_prompt.
// Context-pressure-driven handoffs then trigger immediately to preserve
// continuity; only turn-end follow-up handoffs ask first unless autopilot or
// prior explicit authorization already covers them.

import {
  existsSync,
  writeFileSync,
} from "node:fs";
import { join, basename } from "node:path";
import { homedir } from "node:os";
import { approveAll } from "@github/copilot-sdk";
import { joinSession } from "@github/copilot-sdk/extension";
import {
  agentDispatchAvailable,
  agentWorktreesGet,
  attemptWorktreeSync,
  buildResumePrompt,
  buildSeedForStored,
  collectAdvisoryGitFacts,
  consumeDispatchHandoffTask,
  consumeFileHandoff,
  findHandoffFile,
  findHandoffTask,
  findTaskDeliveryCheckpoint,
  formatConsumeResult,
  logHandoffPromptReceived,
  markDeliveryPromptInjected,
  normalizeHandoffTitle,
  runCli,
  storeHandoff,
  triggerHandoff,
  waitForWorktreeSyncToSettle,
} from "./handoff-core.mjs";
import { extractRecoveryLocatorFromPrompt, recoveryLocatorFor } from "./cutover-seed.mjs";
import { defaultConfig, loadContextHandoffConfigAsync } from "./config.mjs";
import {
  FORCE_TIER_DENY_FEEDBACK,
  isReadOnlyPermissionRequest,
} from "./force-tier.mjs";
import {
  automaticHandoffEnabled,
  manualHandoffEnabled,
} from "./mode.mjs";
import {
  contextPressure,
  formatConfiguredThreshold,
  formatContextUsage,
} from "./thresholds.mjs";

// --- State ---
// This used to be a synchronous `loadContextHandoffConfig(process.cwd())`
// call at module top level, before joinSession() -- a smaller, spawn-free
// instance of the same load-time-blocking-I/O class that broke
// agent-bridge's readiness handshake (four sequential execSync/execFileSync
// spawns there vs. a bounded directory walk + up to two config-file reads
// here). `handoffConfig` starts as the safe, zero-I/O default and is
// resolved via `handoffConfigPromise`, fired fire-and-forget -- NEVER
// awaited on the path to joinSession()/readiness (a slow filesystem must not
// hold extension registration hostage). Declared `let` (not `const`) so
// every closure that reads `handoffConfig.mode`/`.thresholds` sees the
// resolved value.
//
// Because the promise isn't awaited before readiness, every tool handler
// and session-event listener that reads `handoffConfig.mode`/`.thresholds`
// MUST `await handoffConfigPromise;` as its first act -- otherwise a call
// arriving before the config resolves would silently act on the safe
// default (`manual-only`) instead of a repository's real `mode: off`,
// `mode: auto`, or custom thresholds. Awaiting an already-resolved promise
// is a cheap microtask, so this costs nothing once config has loaded.
let handoffConfig = defaultConfig();
const handoffConfigPromise = loadContextHandoffConfigAsync(process.cwd()).then(
  (resolved) => {
    handoffConfig = resolved;
    return resolved;
  },
);
handoffConfigPromise.catch(() => {}); // loadContextHandoffConfigAsync never rejects; guard anyway.

// Phase 4 judgment call: `mode: off` is a repo-level opt-out of the handoff
// mechanism itself, not only the automatic pressure monitor. Refuse every
// extension-provided manual entry point as well so the repository owner gets a
// true disable instead of a "still works if you know the tool names" loophole.
function handoffDisabledResult() {
  return (
    "Context handoff is disabled for this repository " +
    "(configured `mode: off` in .context-handoff/config.yaml or " +
    "~/.context-handoff/config.yaml). Re-enable it to generate, store, " +
    "trigger, or consume handoffs here."
  );
}
const state = {
  turnCount: 0,
  sessionId: null,
  cwd: null,
  filesModified: new Map(),       // path → { tool, turnIndex }
  toolInvocations: [],            // { tool, turn, summary }
  softReminderSent: false,        // additionalContext injected to agent
  hardReminderSent: false,        // additionalContext injected to agent
  softLogShown: false,            // session.log shown to user
  hardLogShown: false,            // session.log shown to user
  handoffGenerated: false,
  pendingHandoff: null,
  firstUserPrompt: null,          // first user message (for topic bias)
  // Phase 3 item 3: guards the one-shot first-turn pickup-signal check
  // (extractRecoveryLocatorFromPrompt) so it never re-fires on a reforked
  // module (this module is reimported on every reconnect, not just true
  // session start -- see the top-level session-lifecycle comment below).
  pickupSignalChecked: false,
  // Force tier (Goal 1): once true, an auto-handoff has been drafted/stored/
  // triggered without waiting for the agent, and onPermissionRequest below
  // denies further mutating tool calls for the remainder of the session
  // (reset only on a successful compaction, mirroring the soft/hard resets).
  forceTriggered: false,
  blockMutatingTools: false,
  thresholdWarningShown: false,
  // Context window tracking (from session.usage_info events)
  currentTokens: 0,
  tokenLimit: 0,
  conversationTokens: 0,
  systemTokens: 0,
  toolDefinitionsTokens: 0,
  messagesLength: 0,
  lastUtilization: 0,             // currentTokens / tokenLimit
};

// --- Helpers ---

// Lazy-initialize state from invocation context if onSessionStart missed
function ensureState(invocation) {
  if (!state.sessionId && invocation?.sessionId) {
    state.sessionId = invocation.sessionId;
  }
  if (!state.cwd) {
    state.cwd = process.cwd();
  }
}

// --- Shared Logic ---

// Collect structured handoff data from current session state.
// Used by both the generate_handoff_prompt tool and the /handoff command.
function collectHandoffData(sid, overrides = {}) {
  const cwd = state.cwd || process.cwd();
  const git = collectAdvisoryGitFacts(cwd);
  const utilPct = state.tokenLimit > 0
    ? Math.round(state.lastUtilization * 100)
    : null;
  const modifiedEntries = [...state.filesModified.entries()].slice(-20);

  return {
    data: {
      sessionId: sid,
      cwd,
      branch: git.branch,
      repo: git.repo,
      turnCount: state.turnCount,
      contextUtilization: utilPct !== null ? `${utilPct}%` : "unknown",
      currentTokens: state.currentTokens,
      tokenLimit: state.tokenLimit,
      filesModified: Object.fromEntries(modifiedEntries),
      gitStatus: git.status,
      toolInvocations: state.toolInvocations.slice(-10),
      firstUserPrompt: state.firstUserPrompt || null,
      agentSummary: overrides.summary || null,
      agentNextSteps: overrides.next_steps || null,
      generatedAt: new Date().toISOString(),
    },
    modifiedEntries,
    git,
    utilPct,
  };
}

// Persist a small context-usage sidecar that the agent-worktrees picker
// reads to show live context-window utilization per worktree. The exact
// token counts arrive via the session.usage_info event, which is delivered
// only to this extension (never written to events.jsonl), so this file is
// the sole on-disk source. Best-effort: never throws into the event loop.
function persistState() {
  try {
    const sid = state.sessionId;
    if (!sid) return;
    const dir = join(homedir(), ".copilot", "session-state", sid);
    // Don't create the dir -- an active session already owns it; a missing
    // dir means there's nothing meaningful to associate the sidecar with.
    if (!existsSync(dir)) return;
    const pct = state.tokenLimit > 0
      ? Math.round(state.lastUtilization * 100)
      : null;
    const payload = {
      sessionId: sid,
      currentTokens: state.currentTokens,
      tokenLimit: state.tokenLimit,
      utilizationPct: pct,
      // Numerator breakdown, straight from the session.usage_info event. Lets a
      // consumer see whether currentTokens is dominated by the fixed
      // system-prompt + tool/skill-definition overhead vs. live conversation --
      // i.e. why this utilization% can differ from a narrower conversation-only
      // display elsewhere.
      conversationTokens: state.conversationTokens,
      systemTokens: state.systemTokens,
      toolDefinitionsTokens: state.toolDefinitionsTokens,
      turnCount: state.turnCount,
      updatedAt: new Date().toISOString(),
    };
    writeFileSync(join(dir, "context.json"), JSON.stringify(payload), "utf-8");
  } catch {
    // Best-effort; the picker simply omits context% when the file is absent.
  }
}

// Format handoff data as a markdown document suitable for continuation.
function formatHandoffMarkdown(handoffData, scope) {
  const lines = [
    `# Session Handoff`,
    "",
    `**Session:** ${handoffData.sessionId}`,
    `**CWD:** ${handoffData.cwd}`,
    `**Branch:** ${
      handoffData.branch === null
        ? "(unavailable)"
        : handoffData.branch || "(detached)"
    }`,
    `**Turn count:** ${handoffData.turnCount}`,
    `**Context utilization:** ${handoffData.contextUtilization}`,
    `**Generated:** ${handoffData.generatedAt}`,
    "",
  ];

  if (scope) {
    lines.push(`## Continuation Scope`, `> ${scope}`, "");
  }

  if (handoffData.firstUserPrompt) {
    lines.push(
      `## Original Request`,
      `> ${handoffData.firstUserPrompt.slice(0, 500)}`,
      ""
    );
  }

  const files = Object.entries(handoffData.filesModified || {});
  if (files.length > 0) {
    lines.push(`## Files Modified`);
    for (const [path, info] of files) {
      lines.push(`- \`${path}\` (${info.tool}, turn ${info.turnIndex})`);
    }
    lines.push("");
  }

  if (handoffData.gitStatus) {
    lines.push(`## Git Status`, "```", handoffData.gitStatus, "```", "");
  }

  if (handoffData.agentSummary) {
    lines.push(`## Summary`, handoffData.agentSummary, "");
  }
  if (handoffData.agentNextSteps) {
    lines.push(`## Next Steps`, handoffData.agentNextSteps, "");
  }

  // This formatter has no agent composition step (force-tier auto-drafts
  // without the agent), so it cannot discover background flows/external
  // state itself. Per the schema, that must be an explicit open item, never
  // a silent omission (see the skill's Outstanding Background Flows rule).
  lines.push(
    "## Outstanding Background Flows & External State",
    "Not captured -- this handoff was auto-drafted by the force tier without " +
      "agent composition. The successor MUST ask the user (or check the " +
      "worktree/dispatch/PR state directly) for any active watches, polls, " +
      "scheduled prompts, open PRs, held claims/leases, or peer-agent " +
      "coordination this session may have owned before assuming there are none.",
    "",
  );

  return lines.join("\n");
}

// --- Force tier (Goal 1) ---
//
// The force threshold is the last chance to capture a handoff before the
// runtime's own auto-compaction (~80%) destroys the conversation state a
// handoff needs to describe. Crossing it must not wait for the agent: this
// auto-drafts a handoff from whatever facts collectHandoffData/
// formatHandoffMarkdown can gather right now (the same auto-formatter the
// generate_handoff_prompt tool's caller would otherwise compose prose from),
// stores + triggers it via the same handoff-core.mjs path the CLI/tools use
// (so it is bash-first-seed-compatible, never routing through
// consume_handoff-as-first-tool-call on the successor -- see
// handoff-cutover-reload-robustness), and denies further mutating tool calls
// via onPermissionRequest below (the only tool-call gate this runtime still
// honors; the SDK's newer `hooks.onPreToolUse` hard-fails here, see the
// "SDK hook callbacks are no longer supported" note further down). The
// read-only/mutating classification itself lives in force-tier.mjs so it is
// unit-testable without the live SDK connection.

async function onPermissionRequest(request, invocation) {
  if (state.blockMutatingTools && !isReadOnlyPermissionRequest(request)) {
    return { kind: "reject", feedback: FORCE_TIER_DENY_FEEDBACK };
  }
  return approveAll(request, invocation);
}

// Ceiling on how long the force-tier trigger will wait for the post-store
// worktree sync to settle before arming live pickup (see autoForceHandoff's
// beforeArmPickup comment for the round-12/26/27/28 tension this bounds),
// and how long consume_handoff (below) will defensively wait at successor
// startup in case pickup was armed while a sync was still settling.
// Comfortably shorter than triggerHandoff's own default 30s pickup-wait
// window, so this can never itself become the dominant source of the
// force-tier trigger's total latency, while still covering a healthy,
// reachable remote's typical fetch+rebase duration in the common case.
const FORCE_TIER_SYNC_ARM_TIMEOUT_MS = 15000;

// Auto-draft, store, and trigger a handoff without agent involvement. Fire-
// and-forget from the session.usage_info handler (below); reports its own
// outcome via session.log/session.send rather than being awaited there.
//
// Capture happens FIRST, with no sync/network dependency at all: the force
// tier is explicitly the last chance before the runtime's own auto-
// compaction destroys the conversation, so nothing may delay getting a
// baton stored. The worktree sync (same principle as the agent-guided
// skill flow -- an un-synced tip hands the successor stale plugin/
// instruction code too) runs strictly AFTER the handoff is already safely
// stored, as pure best-effort: it can never block or delay the capture
// itself, only report its own outcome via session.log once it settles.
async function autoForceHandoff(sid, cwd) {
  let markdown;
  try {
    const { data } = collectHandoffData(sid);
    markdown = formatHandoffMarkdown(data, null);
  } catch (error) {
    session.log(
      `[Context Handoff] Force-tier auto-draft failed: ${error.message}. ` +
      "No handoff was stored; manual continuation is required.",
      { level: "error" },
    );
    return;
  }
  let result;
  try {
    // syncPromise is assigned inside afterStore -- started ONLY once
    // triggerHandoff has confirmed the baton is durably stored (a round-13
    // review finding: starting the sync before the store was even
    // attempted meant a store failure could still leave a sync running,
    // contradicting the "post-capture only" contract). It runs regardless
    // of mode -- a worktree sync benefits a manual-only handoff too (a
    // human resuming it later still wants the latest code), not just an
    // auto-mode live pickup.
    //
    // beforeArmPickup below polls the SAME shared worktree-sync lock via
    // waitForWorktreeSyncToSettle (not syncPromise directly), bounded to
    // FORCE_TIER_SYNC_ARM_TIMEOUT_MS -- resolving a genuine tension
    // between prior findings rather than picking one side over the other:
    //   - round 12: arming pickup with NO regard for the sync at all let a
    //     fast live monitor launch a successor before the sync had even
    //     begun (or, per round 27/28's re-statement of the same concern,
    //     while it was still mid-fetch/rebase), handing the successor a
    //     stale or momentarily-inconsistent worktree.
    //   - round 26: awaiting the sync's FULL completion (multiple 20s+
    //     network/CLI timeouts stacked) could hold the last-chance/fire-
    //     and-forget trigger long enough for compaction to happen,
    //     defeating the guarantee this path exists for.
    // Polling the lock's own liveness (via waitForWorktreeSyncToSettle,
    // not the promise below) gets both AND settles faster than a blind
    // wait whenever the sync finishes early: a healthy, reachable
    // remote's lock clears well inside the timeout, so pickup is armed
    // immediately once it does; an unreachable/slow remote still cannot
    // block the trigger past a small, fixed ceiling. consume_handoff
    // below performs the SAME bounded wait defensively at successor
    // startup, in case pickup was armed (by this path or a manual
    // /consume-handoff) while the lock was still held. The sync keeps
    // running in the background regardless of which side stops waiting
    // first -- its outcome is only ever logged, never folded back into
    // the already-stored baton (there is no update path for a handoff
    // that has already been triggered).
    result = await triggerHandoff({
      promptText: markdown,
      sid,
      cwd,
      title: "Force-threshold auto-handoff",
      mode: handoffConfig.mode,
      afterStore: () => {
        attemptWorktreeSync(cwd)
          .then((syncResult) => {
            if (syncResult.synced) {
              session.log(
                "[Context Handoff] Force-tier: worktree synced onto the " +
                "latest default branch.",
                { level: "info" },
              );
            } else {
              session.log(
                `[Context Handoff] Force-tier: worktree sync ` +
                `${syncResult.attempted ? "failed" : "was skipped"} ` +
                `(${syncResult.reason}). The successor should sync onto ` +
                "the latest default branch itself.",
                { level: "warning" },
              );
            }
            return syncResult;
          })
          .catch(() => {});
      },
      beforeArmPickup: () =>
        waitForWorktreeSyncToSettle(cwd, { timeoutMs: FORCE_TIER_SYNC_ARM_TIMEOUT_MS }),
    });
  } catch (error) {
    session.log(
      `[Context Handoff] Force-tier auto-trigger threw: ${error.message}. ` +
      "The draft above was not stored; manual continuation is required.",
      { level: "error" },
    );
    return;
  }
  if (!result.ok) {
    session.log(
      `[Context Handoff] Force-tier auto-handoff failed: ` +
      `${result.error || result.reason || "unknown failure"}. ` +
      "Manual continuation is required -- invoke the context-handoff skill.",
      { level: "error" },
    );
    return;
  }
  session.log(
    `[Context Handoff] Force-tier handoff stored (${result.stored.storage}: ` +
    `${result.stored.id}) and triggered. ` +
    (result.pickup?.pickedUp
      ? `Pickup acknowledged via ${result.pickup.via.join(", ")}.`
      : "No pickup signal arrived within the wait window."),
    { level: "warning" },
  );
  if (!result.pickup?.pickedUp && result.manualInstructions) {
    session.send(result.manualInstructions).catch((e) =>
      session.log(`[Context Handoff] Force-tier manual-instructions send failed: ${e.message}`, { level: "warning" })
    );
  }
}

// --- Extension ---
// Review note: joinSession() must resolve on its own -- it is NOT wrapped
// in Promise.all with handoffConfigPromise, or a slow filesystem would
// delay readiness by the same I/O latency this change removes.
const session = await joinSession({
  onPermissionRequest,

  tools: [
    {
      name: "generate_handoff_prompt",
      description:
        "Generate structured session facts for creating a continuation " +
        "prompt. Returns session metadata, files modified, git status, " +
        "and key tool invocations. Compose the compact effort-backed shape " +
        "when a valid open active effort exists; otherwise compose the full " +
        "standalone shape. In either mode, preserve the parent completion gate " +
        "rather than treating the latest completed phase as the objective, and " +
        "carry forward any outstanding background flows (watches, polls, " +
        "scheduled prompts) or external state (open PRs, held claims/leases, " +
        "peer-agent coordination) this session owns -- never silently drop them.",
      skipPermission: true,
      parameters: {
        type: "object",
        properties: {
          summary: {
            type: "string",
            description:
              "Optional 1-2 sentence summary of what the session accomplished. " +
              "If omitted, the tool returns raw facts only.",
          },
          next_steps: {
            type: "string",
            description:
              "Optional description of what should happen next.",
          },
        },
      },
      handler: async (args, invocation) => {
        ensureState(invocation);
        await handoffConfigPromise;
        if (!manualHandoffEnabled(handoffConfig.mode)) {
          return handoffDisabledResult();
        }
        const sid = state.sessionId || invocation?.sessionId || "unknown";
        const { data: handoffData, modifiedEntries } = collectHandoffData(sid, args);

        state.handoffGenerated = true;

        return {
          textResultForLlm: [
            "## Handoff Data",
            "",
            `**Session:** ${handoffData.sessionId}`,
            `**CWD:** ${handoffData.cwd}`,
            `**Branch:** ${
              handoffData.branch === null
                ? "(unavailable)"
                : handoffData.branch || "(detached)"
            }`,
            `**Turn count:** ${handoffData.turnCount}`,
            `**Context utilization:** ${handoffData.contextUtilization}`,
            "",
            handoffData.firstUserPrompt
              ? `### Original Request\n> ${handoffData.firstUserPrompt.slice(0, 300)}\n`
              : "",
            "### Files Modified",
            ...modifiedEntries.map(
              ([path, info]) => `- \`${path}\` (${info.tool}, turn ${info.turnIndex})`
            ),
            "",
            "### Git Status",
            "```",
            handoffData.gitStatus === null
              ? "(unavailable)"
              : handoffData.gitStatus || "(clean)",
            "```",
            "",
            args.summary ? `### Agent Summary\n${args.summary}\n` : "",
            args.next_steps ? `### Agent Next Steps\n${args.next_steps}\n` : "",
            "",
            "---",
            "Now follow the context-handoff skill:",
            "1. Compose handoff markdown from this data plus your live context.",
            "   Use the compact effort-backed shape when a valid open active",
            "   effort exists; otherwise use the full standalone shape. Keep",
            "   separate completion gates for this handoff leg and the parent",
            "   objective/worktree.",
            "   A completed phase is progress, not a reason to omit later work.",
            "   If this repo also has an effort capability, use that binding to",
            "   let one session own only a natural slice of the larger objective;",
            "   stride forward confidently, stop at a clean boundary, and hand off",
            "   the next slice rather than forcing one session to bite off the",
            "   entire effort.",
            "   If the parent objective truly has no actionable work left, do not",
            "   create a handoff merely to report that fact; finish instead.",
            "   Include an 'Outstanding Background Flows & External State'",
            "   section: never silently drop active watches/polls/scheduled",
            "   prompts, open PRs, held claims/leases/dispatch tasks, or",
            "   peer-agent coordination this session owns -- carry each forward",
            "   as resumable or as an explicit open item; write \"none\" only",
            "   when genuinely none exist.",
            "2. Distinguish the trigger BEFORE saving:",
            "   - If context pressure is the reason for the handoff and work",
            "     remains: sync the worktree onto the latest default branch",
            "     NOW, before saving. First inspect the tree -- never",
            "     blanket-commit (`git add -A`/`git commit -a`); stage and",
            "     commit only paths you recognize as your own reviewed,",
            "     intentional changes this session. If anything looks",
            "     unfamiliar, untracked, or possibly sensitive, or you are",
            "     unsure it is safe to commit, skip the sync entirely and",
            "     note in the brief that the worktree may be behind the",
            "     default branch. Otherwise commit local WIP, then run the",
            "     payload-local `handoff-cli.mjs sync-worktree` command (NOT",
            "     a bare `agent-worktrees git sync`) -- see the",
            "     context-handoff skill's 'Sync before triggering' section",
            "     for the exact invocation; it shares the same lock and",
            "     rebase guard as the force-tier path. Note any conflict in",
            "     the brief rather than blocking on it. ALWAYS call",
            "     generate_handoff_prompt again after the sync (even if it",
            "     looked like a no-op) so the Git Status you compose from is",
            "     current -- a WIP commit or a failed sync attempt both change",
            "     what the successor needs to know, whether or not the branch",
            "     itself moved. Then call save_handoff_prompt, then call",
            "     trigger_handoff immediately. Do NOT ask for confirmation",
            "     first; continuity is the point.",
            "   - If you are otherwise done with the requested work and would end",
            "     the turn by listing follow-up ideas/questions: call",
            "     save_handoff_prompt now (a not-yet-approved handoff has no",
            "     sync to reflect yet), replace that list with one short offer",
            "     to continue via handoff. Only after the user says yes: sync",
            "     the worktree, then ALWAYS call generate_handoff_prompt and",
            "     save_handoff_prompt again -- even if the sync looked like a",
            "     no-op -- so the stored baton reflects the post-sync state;",
            "     trigger_handoff otherwise reuses the pre-sync brief and",
            "     silently omits a WIP commit, a failed sync, or a conflict",
            "     outcome. Then call trigger_handoff. Never sync or commit",
            "     before the user has agreed, unless autopilot or prior",
            "     authorization already covers that turn-end follow-up path.",
            "save_handoff_prompt stores the handoff — as an agent-dispatch task",
            "when a coordinator is reachable, else a one-time worktree-state",
            "file — and returns the short handoff prompt plus its exact",
            "HANDOFF_SEED/HANDOFF_TOKEN identifiers.",
            "Do NOT paste the handoff contents or claim the handoff auto-loads",
            "on restart (it does not). The one exception to \"do not commit\" is",
            "the deliberate WIP-commit-then-sync step above -- never commit for",
            "any other reason as part of handling a handoff.",
          ].join("\n"),
          resultType: "success",
        };
      },
    },
    {
      name: "save_handoff_prompt",
      description:
        "Store the composed handoff markdown and return the short prompt/seed " +
        "that a human or control system can use to resume it later. This is the " +
        "routine, non-committal step: safe to call whenever you reach a natural " +
        "stopping point. Use it both for context-pressure handoffs that will " +
        "trigger immediately and for turn-end follow-up handoffs where you want " +
        "to preserve the baton before offering the user a low-friction yes/no " +
        "choice. When an agent-dispatch coordinator is reachable, " +
        "the handoff is stored as a *proposed, handoff-labeled task* pinned to " +
        "this worktree (payload = the markdown, no session file) and resumed via " +
        "/resume-handoff; otherwise it falls back to a one-time file in this " +
        "worktree's agent-worktrees state directory outside the repo checkout. " +
        "Call this after composing the handoff from generate_handoff_prompt " +
        "data. Pass the markdown as `prompt_text` (the `prompt` alias is also " +
        "accepted); an optional short `title` labels the task. Returns the short " +
        "handoff prompt plus exact `HANDOFF_SEED:` / `HANDOFF_TOKEN:` lines for " +
        "later manual or tool-driven pickup. The handoff is NEVER loaded " +
        "automatically by a future session.",
      skipPermission: true,
      parameters: {
        type: "object",
        properties: {
          prompt_text: {
            type: "string",
            description: "The composed effort-backed or standalone handoff markdown.",
          },
          title: {
            type: "string",
            description:
              "Optional short, specific title for the handoff task (e.g. " +
              "'Fix agent-dispatch producer recovery'). Used only in the " +
              "agent-dispatch task path.",
          },
          prompt: {
            type: "string",
            description: "Alias for prompt_text (accepted for convenience).",
          },
        },
        // Intentionally no `required`: the handler validates so a missing or
        // misnamed argument returns a clear message instead of a generic
        // "tool execution failed" (writeFileSync on undefined used to throw).
      },
      handler: async (args, invocation) => {
        ensureState(invocation);
        await handoffConfigPromise;
        if (!manualHandoffEnabled(handoffConfig.mode)) {
          return handoffDisabledResult();
        }
        const sid = state.sessionId || invocation?.sessionId;
        if (!sid || sid === "unknown") {
          return "Cannot save handoff prompt: sessionId is unavailable.";
        }

        const text = (args?.prompt_text ?? args?.prompt ?? "").toString().trim();
        if (!text) {
          return (
            "Cannot save handoff: pass the composed handoff markdown as `prompt_text` " +
            "(the `prompt` alias is also accepted). Nothing was written."
          );
        }

        const cwd = state.cwd || process.cwd();
        const title = normalizeHandoffTitle(args?.title);
        const stored = storeHandoff({ promptText: text, sid, cwd, title });
        if (!stored?.storage) {
          return (
            "Cannot save handoff: no safe task or file store was available. " +
            `${stored?.error || "Nothing was written."}`
          );
        }
        const handoffSeed = buildSeedForStored(stored);
        state.pendingHandoff = {
          seed: handoffSeed,
          token: stored.id,
          storage: stored.storage,
          worktree: stored.metadata?.worktree || null,
        };
        return (
          `Handoff stored (${stored.storage}: ${stored.id}). This preserves the ` +
          "baton without arming the pickup flow.\n\n" +
          "If this handoff exists because context pressure is rising and work " +
          "still remains: you should already have synced the worktree onto the " +
          "latest default branch before calling generate_handoff_prompt (see " +
          "the context-handoff skill's 'Sync before triggering' section) -- " +
          "call `trigger_handoff` directly now.\n\n" +
          "If this is a turn-end follow-up handoff, ask the user whether to " +
          "continue via handoff. Only after they say yes: sync the worktree " +
          "(inspect the tree first -- never blanket-commit; skip the sync " +
          "entirely if anything looks unfamiliar or unsafe to commit), then " +
          "ALWAYS re-run generate_handoff_prompt and save_handoff_prompt again " +
          "-- even if the sync looked like a no-op -- so the stored baton " +
          "reflects the post-sync state before calling trigger_handoff. Never " +
          "sync or commit before the user has agreed, unless autopilot or " +
          "prior authorization already covers that turn-end follow-up path.\n\n" +
          "If you later need manual continuation, open the successor session and " +
          "run `/consume-handoff`; if that command is unavailable, use the " +
          "payload-local context-handoff CLI with the recovery locator embedded " +
          "in the seed below.\n\n" +
          `HANDOFF_SEED: ${handoffSeed}\n` +
          `HANDOFF_TOKEN: ${stored.id}`
        );
      },
    },
    {
      name: "consume_handoff",
      description:
        "Consume a stored context handoff exactly once. For agent-dispatch " +
        "handoffs, pass task_id; for file-backed handoffs, pass handoff_id " +
        "(or path). The tool loads the handoff, marks file-backed handoffs " +
        "consumed so they do not replay, updates the predecessor session-state " +
        "marker when present, and returns the stored continuation without " +
        "performing any process management.",
      skipPermission: true,
      parameters: {
        type: "object",
        properties: {
          task_id: {
            type: "string",
            description: "agent-dispatch task id for a task-backed handoff.",
          },
          handoff_id: {
            type: "string",
            description: "File-backed handoff id, e.g. handoff-<session-id>.",
          },
          path: {
            type: "string",
            description: "Explicit file-backed handoff JSON path.",
          },
          defer_complete: {
            type: "boolean",
            description:
              "For task-backed handoffs, consume with --defer-complete so the " +
              "successor completes the task only when the handoff goal is reached.",
          },
        },
      },
      handler: async (args, invocation) => {
        ensureState(invocation);
        await handoffConfigPromise;
        if (!manualHandoffEnabled(handoffConfig.mode)) {
          return {
            textResultForLlm: handoffDisabledResult(),
            resultType: "error",
          };
        }
        const cwd = state.cwd || process.cwd();
        const sid = state.sessionId || invocation?.sessionId || null;
        const taskId = (args?.task_id ?? "").toString().trim();
        const handoffId = (args?.handoff_id ?? "").toString().trim();
        const path = (args?.path ?? "").toString().trim();
        const deferComplete = Boolean(args?.defer_complete);

        // Defensive: if the predecessor's own bounded wait (autoForceHandoff's
        // beforeArmPickup) already elapsed while its sync was still running,
        // or pickup was armed via a path with no such gate at all (a manual
        // /consume-handoff run right after cutover), this worktree could
        // still be mid-fetch/rebase right now. Poll the SAME shared lock
        // once more here, bounded the same way, before reading anything --
        // never blocking indefinitely, just narrowing the window where a
        // successor reads a momentarily-inconsistent tree.
        const settleResult = await waitForWorktreeSyncToSettle(cwd, {
          timeoutMs: FORCE_TIER_SYNC_ARM_TIMEOUT_MS,
        });
        if (settleResult.waited && !settleResult.settled) {
          session.log(
            "[Context Handoff] consume_handoff: the predecessor's worktree " +
            "sync still appears to be in progress after the wait window; " +
            "proceeding anyway -- verify the worktree yourself if anything " +
            "looks unexpectedly stale or mid-rebase.",
            { level: "warning" },
          );
        }

        let result;
        if (taskId) {
          result = consumeDispatchHandoffTask(cwd, taskId, sid, deferComplete);
        } else if (handoffId || path) {
          result = consumeFileHandoff(cwd, sid, handoffId, path || null);
        } else {
          return (
            "Cannot consume handoff: pass task_id for an agent-dispatch handoff " +
            "or handoff_id/path for a file-backed handoff."
          );
        }

        return {
          textResultForLlm: formatConsumeResult(result, { deferComplete }),
          resultType: result?.ok ? "success" : "error",
        };
      },
    },
    {
      name: "trigger_handoff",
      description:
        "Signal that THIS session is ready for a handoff pickup, without " +
        "performing any process management. Call this only after the user said " +
        "yes to continuing via handoff, or when autopilot / prior explicit " +
        "authorization already permits it -- EXCEPT for context-pressure-driven " +
        "handoffs with work still left to do, which should trigger immediately " +
        "without asking. The tool may either reuse the current " +
        "session's most recently saved handoff or store fresh markdown passed as " +
        "`prompt_text` / `prompt`. It always: (1) drops the full handoff " +
        "markdown in this session's session-state folder; (2) durably stores " +
        "it (agent-dispatch task or worktree-state file). ONLY when " +
        "`.context-handoff/config.yaml`'s `mode` is `auto` (the default is " +
        "`manual-only`) does it additionally: (3) note it in the worktree's " +
        "own record (creates a pending-handoff entry agent-worktrees' " +
        "resident monitor can discover and claim on its own -- gated " +
        "identically to the two triggers below, not merely advisory), and " +
        "refresh worktree-visible PENDING-HANDOFF state when agent-worktrees " +
        "is available; (4) best-effort ping agent-bridge if present; (5) wait " +
        "up to 30 seconds for the CUTOVER to start (a status-monitor " +
        "`handoff_cutover_spawn` acknowledgement) -- not for the successor to " +
        "fully finish cold-starting and consume the handoff, which " +
        "legitimately takes longer and is not worth blocking on. Regardless " +
        "of mode, it always: (6) checks once whether a cutover is already " +
        "under way, already fully picked up, or neither (under `manual-only` " +
        "this is a single check, not a polling wait -- steps 3-5 are the " +
        "only ones actually skipped); and (7) prints manual fallback " +
        "guidance -- distinctly worded when automatic cutover is simply " +
        "disabled by mode versus when it was attempted and nothing happened " +
        "-- and (8) ALWAYS ends with the final short handoff prompt/seed. " +
        "It NEVER checks panes or PIDs, spawns or retires sessions, or " +
        "performs any cutover itself.",
      skipPermission: true,
      parameters: {
        type: "object",
        properties: {
          prompt_text: {
            type: "string",
            description:
              "Optional composed handoff markdown. When supplied, trigger_handoff " +
              "stores/supersedes the baton first and then signals pickup in the " +
              "same tool call.",
          },
          handoff_token: {
            type: "string",
            description:
              "Optional stored handoff token to reuse instead of the session's " +
              "most recently saved baton.",
          },
          title: {
            type: "string",
            description:
              "Optional short title when `prompt_text` is supplied and a new " +
              "stored baton is being created in this tool call.",
          },
          prompt: {
            type: "string",
            description: "Alias for prompt_text (accepted for convenience).",
          },
        },
      },
      handler: async (args, invocation) => {
        ensureState(invocation);
        await handoffConfigPromise;
        if (!manualHandoffEnabled(handoffConfig.mode)) {
          return handoffDisabledResult();
        }
        const text = (args?.prompt_text ?? args?.prompt ?? "").toString().trim();
        const cwd = state.cwd || process.cwd();
        const sid = state.sessionId || invocation?.sessionId;
        if (!sid || sid === "unknown") {
          return "Cannot trigger handoff: sessionId is unavailable.";
        }
        if (!text && !args?.handoff_token && !state.pendingHandoff?.token) {
          return (
            "Cannot trigger handoff: pass composed handoff markdown as " +
            "`prompt_text` (or `prompt`), or save a baton first with " +
            "`save_handoff_prompt` and then re-run `trigger_handoff`."
          );
        }
        const title = normalizeHandoffTitle(args?.title);
        const result = await triggerHandoff({
          promptText: text || null,
          sid,
          cwd,
          title,
          handoffToken:
            (args?.handoff_token ?? state.pendingHandoff?.token ?? "").toString().trim() || null,
          mode: handoffConfig.mode,
        });
        if (!result?.ok) {
          return (
            "Cannot trigger handoff: " +
            `${result?.error || "the pickup signal flow failed before it could start."}`
          );
        }
        state.pendingHandoff = {
          seed: result.seed,
          token: result.stored.id,
          storage: result.stored.storage,
          worktree: result.stored.metadata?.worktree || null,
        };
        const pickup = result.pickup || { via: [] };
        const pickedUp = pickup.pickedUp;
        const pickupLine = pickedUp
          ? `Pickup acknowledged within ${(pickup.waitedMs / 1000).toFixed(1)}s via ${pickup.via.join(", ")}.`
          : pickup.spawnInFlight
            ? `No full pickup yet, but a successor spawn was acknowledged within ${(pickup.waitedMs / 1000).toFixed(1)}s -- an automatic cutover is already under way (real Copilot cold-start just takes longer than that).`
            : `No pickup signal arrived within ${(pickup.waitedMs / 1000).toFixed(1)}s.`;
        const bridgeLine = result.bridge?.attempted
          ? (
              result.bridge.accepted
                ? "agent-bridge accepted a best-effort handoff request ping."
                : `agent-bridge ping did not confirm pickup${result.bridge.error ? ` (${result.bridge.error})` : ""}.`
            )
          : "agent-bridge was not pinged.";
        if (result.automaticCutoverDisabled) {
          return (
            `Handoff stored (automatic cutover disabled) for ${result.stored.storage} ` +
            `baton ${result.stored.id}. None of the live-cutover triggers ran ` +
            "-- not the worktree-record note, not the `handoff_requested` " +
            "activity event agent-worktrees' resident monitor watches for, " +
            "and not an agent-bridge ping -- because `.context-handoff/" +
            "config.yaml`'s `mode` is not `auto`. No successor pane will be " +
            "spawned automatically.\n\n" +
            `${result.manualInstructions
              || "A manually-launched successor already appears to have " +
                "picked this up. Keep the same seed available in case a " +
                "human or tool still needs to resume it manually.\n"}\n\n` +
            "Final short handoff prompt/seed:\n\n" +
            "```text\n" +
            `${result.seed}\n` +
            "```"
          );
        }
        return (
          `Handoff request signaled for ${result.stored.storage} baton ` +
          `${result.stored.id}. ${pickupLine}\n\n` +
          `${bridgeLine}\n\n` +
          (
            result.manualInstructions
              ? `${result.manualInstructions}\n\n`
              : "A control system appears to have picked the request up. Keep the same seed available in case a human or tool needs to resume it manually.\n\n"
          ) +
          "Final short handoff prompt/seed:\n\n" +
          "```text\n" +
          `${result.seed}\n` +
          "```"
        );
      },
    },
  ],

  commands: [
    {
      name: "handoff-continue",
      description:
        "Generate a handoff for THIS session, store it, and trigger the " +
        "pending-handoff signal flow (only wired up automatically when " +
        "mode: auto is configured -- see the context-handoff skill's Mode " +
        "gate). This is the explicit human yes-path: it does NOT spawn or " +
        "retire sessions, but it does arm the baton for any human or " +
        "control system watching the worktree, dispatch task, or " +
        "session-state marker when automatic mode is enabled.",
      handler: async (ctx) => {
        void ctx;
        await handoffConfigPromise;
        if (!manualHandoffEnabled(handoffConfig.mode)) {
          await session.log(handoffDisabledResult(), { level: "warning" });
          return;
        }
        await session.send({
          prompt:
            "Perform a handoff now (the operator invoked /handoff-continue, " +
            "which is explicit authorization). Steps: (1) sync the worktree " +
            "onto the latest default branch first -- inspect the tree before " +
            "committing anything (never blanket-commit via `git add -A`/" +
            "`git commit -a`; stage and commit only paths you recognize as " +
            "your own reviewed, intentional changes this session; if " +
            "anything looks unfamiliar, untracked, or possibly sensitive, or " +
            "you are unsure it is safe to commit, skip the sync entirely and " +
            "note in the brief that the worktree may be behind the default " +
            "branch), then run the payload-local `handoff-cli.mjs " +
            "sync-worktree` command (NOT a bare `agent-worktrees git " +
            "sync`) -- see the context-handoff skill's 'Sync before " +
            "triggering' section for the exact invocation; it shares the " +
            "same lock and rebase guard as the force-tier path; note any " +
            "conflict in the brief rather than blocking on it); (2) call " +
            "generate_handoff_prompt to collect session facts; (3) compose " +
            "continuation markdown per the context-handoff skill -- use its " +
            "compact effort-backed shape when a valid open active effort exists, " +
            "otherwise the full standalone shape; (4) call trigger_handoff with " +
            "that markdown as `prompt_text` and a short specific `title`. " +
            "trigger_handoff always stores/refreshes the baton and ends with " +
            "the final short handoff prompt/seed; it only signals pending " +
            "handoff state across the available systems and waits briefly for " +
            "a pickup when `.context-handoff/config.yaml`'s `mode` is `auto` " +
            "(the default, `manual-only`, skips that live-cutover signaling " +
            "entirely). Do NOT claim the baton auto-loads on restart; if no " +
            "control system picks it up (or automatic mode is disabled), " +
            "follow the manual instructions it printed.",
          displayPrompt: "Trigger handoff pickup (/handoff-continue)",
        });
      },
    },
    {
      name: "consume-handoff",
      description:
        "Dig up this worktree's pending handoff and inject its continuation " +
        "prompt into THIS session (foreground). Consumes the agent-dispatch " +
        "handoff task if present, else the newest matching worktree handoff file.",
      handler: async (ctx) => {
        await handoffConfigPromise;
        if (!manualHandoffEnabled(handoffConfig.mode)) {
          await session.log(handoffDisabledResult(), { level: "warning" });
          return;
        }
        const cwd = state.cwd || process.cwd();
        const sid = state.sessionId || ctx?.sessionId || "unknown";

        // Prefer an agent-dispatch handoff task pinned to this worktree.
        if (agentDispatchAvailable()) {
          // Binding-first (#4098): pass the real session id (not the "unknown"
          // sentinel) so a bare-resumed session (cwd=HOME) still resolves its
          // worktree from the session binding.
          const bindSid = sid && sid !== "unknown" ? sid : null;
          const wtDir = agentWorktreesGet("worktree-dir", cwd, bindSid);
          const worktree = wtDir ? basename(wtDir) : null;
          if (worktree) {
            const task = findHandoffTask(cwd, worktree);
            if (task) {
              const consumed = consumeDispatchHandoffTask(cwd, task.id, sid, true);
              const body = consumed?.payload || "";
              if (!consumed?.ok || !body) {
                await session.log(
                  `Found handoff task ${task.id.slice(0, 8)} but could not claim ` +
                    "and load it. Nothing was injected.",
                  { level: "warning" },
                );
                return;
              }
              try {
                await session.send({
                  prompt: buildResumePrompt(
                    body,
                    "agent-dispatch task",
                    { deferredTaskId: task.id },
                  ),
                  displayPrompt: `Resuming handoff ${task.id.slice(0, 8)} from agent-dispatch`,
                });
                markDeliveryPromptInjected(consumed.checkpointState);
              } catch {
                await session.log(
                  `Claimed handoff task ${task.id.slice(0, 8)}, but prompt injection failed. ` +
                    "The task remains owned and the durable delivery checkpoint can retry it in this same successor.",
                  { level: "warning" },
                );
                return;
              }
              return;
            }
          }
        }

        const checkpoint = findTaskDeliveryCheckpoint(cwd, sid);
        if (checkpoint?.handoffToken) {
          const resumed = consumeDispatchHandoffTask(
            cwd,
            checkpoint.handoffToken,
            sid,
            true,
          );
          if (resumed?.ok) {
            await session.send({
              prompt: buildResumePrompt(
                resumed.payload,
                "task-backed delivery checkpoint",
                { deferredTaskId: checkpoint.handoffToken },
              ),
              displayPrompt:
                `Resuming checkpoint ${checkpoint.handoffToken.slice(0, 8)}`,
            });
            markDeliveryPromptInjected(resumed.checkpointState);
            return;
          }
        }

        // Fallback: the newest worktree-state handoff file for this worktree.
        const file = findHandoffFile(cwd, sid);
        if (file) {
          const consumed = consumeFileHandoff(cwd, sid, file.record.id, file.path);
          if (!consumed?.ok) {
            await session.log(
              consumed?.message || "Found a handoff file but could not consume it.",
              { level: "warning" },
            );
            return;
          }
          await session.send({
            prompt: buildResumePrompt(consumed.payload, `file ${file.path}`),
            displayPrompt: `Resuming handoff ${consumed.id || basename(file.path)}`,
          });
          return;
        }

        await session.log(
          "No pending handoff found for this worktree (no agent-dispatch task " +
            "and no matching worktree handoff file). If you have a handoff prompt, paste it directly.",
          { level: "warning" },
        );
      },
    },
    {
      name: "resume-handoff",
      description:
        "Compatibility alias for /consume-handoff. Asks this session to invoke " +
        "the canonical stored-handoff consumer.",
      handler: async () => {
        await handoffConfigPromise;
        if (!manualHandoffEnabled(handoffConfig.mode)) {
          await session.log(handoffDisabledResult(), { level: "warning" });
          return;
        }
        await session.send({
          prompt:
            "Invoke /consume-handoff now to load this worktree's pending " +
            "handoff and continue from its stored brief.",
          displayPrompt: "Resume stored handoff",
        });
      },
    },
  ],
});

// --- Session lifecycle reconstructed from events (SDK callback hooks removed) ---
// The native runtime dropped SDK callback hooks ("SDK hook callbacks are no
// longer supported by the native runtime"), which hard-failed joinSession when
// a `hooks` object was passed. The former onSessionStart / onUserPromptSubmitted
// / onPostToolUse behaviours are reconstructed below from observable session
// events. This top-level code runs on every import of the module -- and the
// module is (re)imported MULTIPLE times per session: the runtime forks a
// discovery pass plus the real join at startup, and re-forks on
// reconnect/resume. So session-start work here must be idempotent.
//
// NOTE: no user-visible "Session started" breadcrumb is emitted here. session.log
// surfaces to the chat UI, and a single such notification was observed being
// re-painted indefinitely by the CLI's notification renderer (dotfiles#447),
// flooding the UI. The extension's launch is already recorded per-fork in its
// own extension launch log, so nothing is lost by staying silent in the UI.
state.sessionId = session.sessionId ?? state.sessionId ?? null;
state.cwd = state.cwd || process.cwd();
state.turnCount = 0;
// handoffConfig may not have resolved yet at this exact point (it is
// deliberately not awaited before/alongside joinSession() -- see the
// comment above `handoffConfigPromise`). Chain the warning surface off the
// promise instead of reading `handoffConfig` synchronously here, so it
// still reaches the user once config resolves even if that happens a beat
// after readiness.
handoffConfigPromise.then((resolved) => {
  if (resolved.warning) {
    session.log(`[Context Handoff] ${resolved.warning}`, { level: "warning" });
  }
});

// Turn counting + first-prompt capture (replaces onUserPromptSubmitted).
session.on("user.message", (event) => {
  state.turnCount++;
  if (!state.firstUserPrompt && event.data?.content) {
    state.firstUserPrompt = event.data.content;
  }
  // Phase 3 item 3 (efforts/active/context-handoff-overhaul): the
  // deterministic "did my launch actually land" signal. On the very first
  // turn only, grep for the exact recovery locator every cutover seed
  // carries (see cutover-seed.mjs's extractRecoveryLocatorFromPrompt) --
  // no LLM judgment, just a plain string match -- and best-effort log a
  // confirmed-receipt event the predecessor's pickupSignals can read. This
  // fires before any tool/skill call, so it is immune to the skill-load
  // race a mid-session reload can otherwise cause.
  if (state.turnCount === 1 && !state.pickupSignalChecked) {
    state.pickupSignalChecked = true;
    const locator = extractRecoveryLocatorFromPrompt(event.data?.content);
    if (locator) {
      try {
        const cwd = state.cwd || process.cwd();
        const wtDir = agentWorktreesGet("worktree-dir", cwd, state.sessionId);
        const worktreeId = wtDir ? basename(wtDir) : null;
        if (worktreeId) {
          logHandoffPromptReceived(
            cwd, worktreeId, state.sessionId,
            recoveryLocatorFor(locator.kind, locator.id),
          );
        }
      } catch (e) {
        session.log(
          `[Context Handoff] pickup-signal log failed: ${e.message}`,
          { level: "warning" },
        );
      }
    }
  }
});

// File / tool-invocation tracking (replaces onPostToolUse's bookkeeping).
// tool.execution_complete carries the success flag but NOT the call
// arguments, so the args are stashed from tool.execution_start (keyed by
// toolCallId) and committed on a successful completion -- matching the old
// hook, which ran for successful tool calls only.
const pendingToolArgs = new Map();  // toolCallId -> { toolName, arguments }

session.on("tool.execution_start", (event) => {
  const d = event.data;
  if (!d?.toolCallId) return;
  pendingToolArgs.set(d.toolCallId, {
    toolName: d.toolName,
    arguments: d.arguments || {},
  });
  // Bound the map in case a completion event is ever missed.
  if (pendingToolArgs.size > 200) {
    pendingToolArgs.delete(pendingToolArgs.keys().next().value);
  }
});

session.on("tool.execution_complete", (event) => {
  const d = event.data;
  const pend = d?.toolCallId ? pendingToolArgs.get(d.toolCallId) : null;
  if (d?.toolCallId) pendingToolArgs.delete(d.toolCallId);
  if (!d?.success) return;  // old onPostToolUse fired for successes only

  const toolName = pend?.toolName || d.toolDescription?.name;
  const toolArgs = pend?.arguments || {};
  if (!toolName) return;

  // Track file modifications
  if ((toolName === "edit" || toolName === "create") && toolArgs?.path) {
    state.filesModified.set(toolArgs.path, {
      tool: toolName,
      turnIndex: state.turnCount,
    });
  }

  // Track notable tool invocations (skip high-frequency read-only tools)
  const skipTools = new Set(["view", "glob", "grep", "report_intent", "sql", "session_store_sql"]);
  if (!skipTools.has(toolName)) {
    const summary = toolName === "edit" || toolName === "create"
      ? toolArgs?.path || ""
      : toolName === "powershell" || toolName === "bash"
        ? (String(toolArgs?.description || toolArgs?.command || "")).slice(0, 80)
        : toolName === "task"
          ? `${toolArgs?.agent_type || ""}: ${(toolArgs?.description || "").slice(0, 60)}`
          : JSON.stringify(toolArgs || {}).slice(0, 80);

    state.toolInvocations.push({
      tool: toolName,
      turn: state.turnCount,
      summary,
    });

    // Cap at 50 entries to avoid unbounded growth
    if (state.toolInvocations.length > 50) {
      state.toolInvocations = state.toolInvocations.slice(-30);
    }
  }
});

// Agent-facing context-pressure nudge (replaces the onPostToolUse
// additionalContext return value, which the native runtime no longer
// supports). session.on handlers are observe-only, so the reminder is queued
// in the session.usage_info handler and delivered here as a real user-turn
// message via session.send() on the next idle boundary -- the agent sees and
// can act on it, exactly as the injected additionalContext used to allow.
// Guarded by the once-only softReminderSent / hardReminderSent flags (reset
// on compaction). session.send() inside an idle handler does not loop: the
// queue is cleared before sending and the guard flags prevent re-queueing.
// Fresh-session awareness (Goal 2/3) is NOT delivered from here. It is
// static, session-start guidance (the "mechanism exists" fact never depends
// on a live token count), so it belongs in the hookless
// instructions/context-handoff/session-guidance.instructions.md path (see
// scripts/emit-guidance.*) rather than a runtime session.send() nudge. This
// extension's top-level module is reimported on every reconnect/refork (see
// the session-lifecycle comment above), so an in-memory "sent once" flag
// here is not idempotent across a session's real lifetime and would
// re-deliver the message on every reload -- exactly the failure a static,
// naturally-idempotent file write avoids. See
// efforts/active/context-handoff-overhaul's journal for the incident this
// closed (a mid-session extension/skill reload replayed the nudge and raced
// the skill registry, producing a transient "Skill not found: context-handoff").
let pendingNudge = null;  // null | "soft" | "hard"

session.on("session.idle", () => {
  const messages = [];
  if (pendingNudge) {
    const level = pendingNudge;
    pendingNudge = null;
    const usage = formatContextUsage(state.currentTokens, state.tokenLimit);
    // The nudge JUST hands the agent to the context-handoff skill. Under context
    // pressure, that skill should compose/save and then trigger_handoff directly
    // without asking. The ask-first branch is only for turn-end follow-up handoffs.
    const msg = level === "hard"
      ? `[Context Handoff -- automated] Context utilization is ${usage.utilization} ` +
        `(${usage.tokens}). ` +
        `The configured hard threshold was reached; auto-compaction still triggers ` +
        `at ~80%. Invoke the context-handoff skill now to preserve continuity ` +
        `before context is lost. Compose/store the baton and then call ` +
        `trigger_handoff directly; do not pause to ask the user first.`
      : `[Context Handoff -- automated] Context utilization is ${usage.utilization} ` +
        `(${usage.tokens}). ` +
        `The configured soft threshold was reached. Invoke the context-handoff skill ` +
        `at the next clean boundary so you can compose/store a baton early and, if ` +
        `work still remains, trigger_handoff directly before the window gets tighter.`;
    messages.push(msg);
  }
  if (messages.length === 0) return;
  session.send(messages.join("\n\n")).catch((e) =>
    session.log(`[Context Handoff] nudge send failed: ${e.message}`, { level: "warning" })
  );
});

// --- Real-time context utilization monitoring ---
// The session.usage_info event fires with exact token counts after each
// model interaction. This is the authoritative signal for context usage.

session.on("session.usage_info", async (event) => {
  const d = event.data;
  state.currentTokens = d.currentTokens;
  state.tokenLimit = d.tokenLimit;
  state.conversationTokens = d.conversationTokens ?? 0;
  state.systemTokens = d.systemTokens ?? 0;
  state.toolDefinitionsTokens = d.toolDefinitionsTokens ?? 0;
  state.messagesLength = d.messagesLength;
  state.lastUtilization = d.tokenLimit > 0 ? d.currentTokens / d.tokenLimit : 0;
  const usage = formatContextUsage(d.currentTokens, d.tokenLimit);
  await handoffConfigPromise;
  if (!automaticHandoffEnabled(handoffConfig.mode)) {
    persistState();
    return;
  }

  let pressure;
  try {
    pressure = contextPressure(
      d.currentTokens,
      d.tokenLimit,
      handoffConfig.thresholds,
    );
  } catch (error) {
    if (!state.thresholdWarningShown) {
      state.thresholdWarningShown = true;
      const message = error instanceof Error ? error.message : String(error);
      session.log(
        `[Context Handoff] Invalid configured thresholds for this token window; ` +
        `using defaults for automatic pressure handling instead: ${message}`,
        { level: "warning" },
      );
    }
    pressure = contextPressure(d.currentTokens, d.tokenLimit);
  }

  // Force tier: crossing it auto-drafts/stores/triggers a handoff and blocks
  // further mutating tool calls, without waiting for the agent. Checked first
  // and sets handoffGenerated so the soft/hard branches below (which guard on
  // !state.handoffGenerated) naturally stand down once forced.
  if (pressure.force && !state.forceTriggered && !state.handoffGenerated) {
    state.forceTriggered = true;
    state.blockMutatingTools = true;
    state.handoffGenerated = true;
    state.hardReminderSent = true;
    state.softReminderSent = true;
    state.hardLogShown = true;
    state.softLogShown = true;
    session.log(
      `[Context Handoff] 🛑 Force threshold reached (${formatConfiguredThreshold(pressure, "force")}, ` +
      `${Math.round(pressure.forceThreshold).toLocaleString()} tokens; ` +
      `current ${usage.tokens}). Auto-generating and triggering a handoff now. ` +
      "Further mutating tool calls are denied for the remainder of this session.",
      { level: "error" },
    );
    autoForceHandoff(state.sessionId, state.cwd || process.cwd()).catch((error) =>
      session.log(`[Context Handoff] Force-tier handler threw: ${error.message}`, { level: "error" })
    );
  }

  // Queue an agent-facing nudge once per threshold, delivered on the next
  // idle via session.send() (see the session.idle handler above). This is the
  // agent-visible counterpart to the user-visible logs below.
  if (pressure.hard &&
      !state.hardReminderSent && !state.handoffGenerated) {
    state.hardReminderSent = true;
    state.softReminderSent = true;  // hard implies soft
    pendingNudge = "hard";
  } else if (pressure.soft &&
      !state.softReminderSent && !state.handoffGenerated) {
    state.softReminderSent = true;
    pendingNudge = "soft";
  }

  // Hard/soft reminders (user-visible log only -- agent nudged via
  // session.send on idle). Mirrors the pendingNudge mutual-exclusion above:
  // if a single event crosses both thresholds at once (e.g. a large jump in
  // usage), only the superseding hard message is shown, not both.
  if (pressure.hard &&
      !state.hardLogShown && !state.handoffGenerated) {
    state.hardLogShown = true;
    state.softLogShown = true;  // hard implies soft
    session.log(
      `[Context Handoff] ⚠️ Context utilization ${usage.utilization} ` +
      `(${usage.tokens}; ` +
      `conversation ${(d.conversationTokens ?? 0).toLocaleString()}, ` +
      `system ${(d.systemTokens ?? 0).toLocaleString()}, ` +
      `tool-defs ${(d.toolDefinitionsTokens ?? 0).toLocaleString()}). ` +
      `Configured ${formatConfiguredThreshold(pressure, "hard")} hard threshold ` +
      `${Math.round(pressure.hardThreshold).toLocaleString()} ` +
      `tokens reached; auto-compaction still triggers at ~80%. ` +
      `Hand off NOW -- invoke the context-handoff skill and trigger the handoff ` +
      `directly without asking first.`,
      { level: "warning" }
    );
  } else if (pressure.soft &&
      !state.softLogShown && !state.handoffGenerated) {
    state.softLogShown = true;
    session.log(
      `[Context Handoff] Context utilization ${usage.utilization} ` +
      `(${usage.tokens}; ` +
      `conversation ${(d.conversationTokens ?? 0).toLocaleString()}, ` +
      `system ${(d.systemTokens ?? 0).toLocaleString()}, ` +
      `tool-defs ${(d.toolDefinitionsTokens ?? 0).toLocaleString()}). ` +
      `Configured ${formatConfiguredThreshold(pressure, "soft")} threshold ` +
      `${Math.round(pressure.softThreshold).toLocaleString()} ` +
      `tokens reached. Preserve the baton at the next clean boundary and, if ` +
      `work still remains, trigger the handoff directly (invoke the ` +
      `context-handoff skill).`,
      { level: "warning" }
    );
  }

  persistState();
});

// Also monitor compaction events for awareness
session.on("session.compaction_start", (event) => {
  session.log(
    `[Context Handoff] Compaction starting. ` +
    `Conversation tokens: ${event.data.conversationTokens?.toLocaleString() ?? "?"}, ` +
    `System tokens: ${event.data.systemTokens?.toLocaleString() ?? "?"}`,
    { level: "warning" }
  );
});

session.on("session.compaction_complete", (event) => {
  const d = event.data;
  if (d.success) {
    // Reset reminder state after successful compaction — utilization
    // will be much lower now, so future reminders should fire fresh
    state.softReminderSent = false;
    state.hardReminderSent = false;
    state.softLogShown = false;
    state.hardLogShown = false;
    // Also lift the force-tier tool block: compaction is itself a corrective
    // action, and the operator may deliberately keep working in this session
    // (e.g. the auto-triggered successor was ignored/cancelled) rather than
    // switching to the handed-off one -- a permanent block with no recovery
    // path would trap them. forceTriggered stays true (it is a once-only
    // guard so the same crossing can't auto-handoff twice); a *later*
    // crossing of the force threshold after this reset is intentionally
    // suppressed too, since handoffGenerated (set when force first fired)
    // is never cleared here -- one auto-handoff per session is the contract.
    state.blockMutatingTools = false;
    session.log(
      `[Context Handoff] Compaction complete. ` +
      `${d.tokensRemoved?.toLocaleString() ?? "?"} tokens removed, ` +
      `${d.postCompactionTokens?.toLocaleString() ?? "?"} tokens remaining.`
    );
  }
});
