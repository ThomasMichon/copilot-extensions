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
//    sequencing (a live cutover under mux).
//
// The /handoff gesture is handled as a skill invocation (context-handoff
// skill), not a slash command. The skill triggers the agent to call
// generate_handoff_prompt, compose prose, and call save_handoff_prompt. The
// PRIMARY path then performs a live cutover (continue_handoff) -- spinning up a
// successor in the same mux and retiring this session, hands-free -- and only
// falls back to a copy/paste reply when no mux session is present.

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
  buildResumePrompt,
  buildSeedForStored,
  collectAdvisoryGitFacts,
  completeHandoffLifecycle,
  consumeDispatchHandoffTask,
  consumeFileHandoff,
  findHandoffFile,
  findHandoffTask,
  findTaskCutoverCheckpoint,
  formatConsumeResult,
  manualFallbackInstructions,
  normalizeHandoffTitle,
  runCli,
  runHandoffCutover,
  retryStoredHandoffCutover,
  storeHandoff,
} from "./handoff-core.mjs";
import { loadContextHandoffConfig } from "./config.mjs";
import { contextPressure, formatContextUsage } from "./thresholds.mjs";

// --- State ---
const handoffConfig = loadContextHandoffConfig(process.cwd());
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

// Best-effort SDK logging is injected into the SDK-free handoff core.
function logProgress(msg, opts = { level: "info" }) {
  try {
    const pending = session?.log?.(msg, opts);
    if (pending && typeof pending.catch === "function") pending.catch(() => {});
  } catch {
    // Logging never controls handoff progress.
  }
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

  return lines.join("\n");
}

// --- Extension ---

const session = await joinSession({
  onPermissionRequest: approveAll,

  tools: [
    {
      name: "generate_handoff_prompt",
      description:
        "Generate structured session facts for creating a continuation " +
        "prompt. Returns session metadata, files modified, git status, " +
        "and key tool invocations. Compose the compact effort-backed shape " +
        "when a valid open active effort exists; otherwise compose the full " +
        "standalone shape. In either mode, preserve the parent completion gate " +
        "rather than treating the latest completed phase as the objective.",
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
            "   If the parent objective truly has no actionable work left, do not",
            "   create a live handoff merely to report that fact; finish instead.",
            "2. Call save_handoff_prompt with the composed markdown as `prompt_text`",
            "   (and an optional short `title`). It stores the handoff — as an",
            "   agent-dispatch task when a coordinator is reachable, else a",
            "   one-time worktree-state file — and returns the short paste prompt",
            "   plus HANDOFF_SEED/HANDOFF_TOKEN for live cutover.",
            "3. Under mux, call continue_handoff with the exact seed and token.",
            "   The successor consumes the stored handoff and retires this",
            "   predecessor. Without mux, reply with ONLY the short paste prompt.",
            "Do NOT paste the handoff contents, commit anything, or claim the",
            "handoff auto-loads on restart (it does not).",
          ].join("\n"),
          resultType: "success",
        };
      },
    },
    {
      name: "save_handoff_prompt",
      description:
        "Store the composed handoff markdown and return what short prompt to reply " +
        "with. When an agent-dispatch coordinator is reachable, the handoff is " +
        "stored as a *proposed, handoff-labeled task* pinned to this worktree " +
        "(payload = the markdown, no session file) and resumed next session via " +
        "/resume-handoff; otherwise it falls back to a one-time file in this " +
        "worktree's agent-worktrees state directory outside the repo checkout. " +
        "Call this after composing the handoff from " +
        "generate_handoff_prompt data. Pass the markdown as `prompt_text` (the " +
        "`prompt` alias is also accepted); an optional short `title` labels the " +
        "task. Returns the short reply prompt AND, on a `HANDOFF_SEED:` line, the " +
        "exact seed string plus a `HANDOFF_TOKEN:` to pass to `continue_handoff` " +
        "if you are performing a " +
        "LIVE cutover (e.g. from /handoff-continue). The handoff is NEVER loaded " +
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
        const cutoverSeed = buildSeedForStored(stored);
        state.pendingHandoff = {
          seed: cutoverSeed,
          token: stored.id,
          worktree: stored.metadata?.worktree || null,
        };
        return (
          `Handoff stored (${stored.storage}: ${stored.id}). The stored record ` +
          "and note-handoff pointer remain available even if no mux exists.\n\n" +
          "For an automatic mux cutover, call `continue_handoff` with the exact " +
          "HANDOFF_SEED below.\n\n" +
          `HANDOFF_SEED: ${cutoverSeed}\n` +
          `HANDOFF_TOKEN: ${stored.id}\n` +
          "\n" +
          manualFallbackInstructions(stored, cutoverSeed)
        );
      },
    },
    {
      name: "consume_handoff",
      description:
        "Consume a stored context handoff exactly once. For agent-dispatch " +
        "handoffs, pass task_id; for file-backed handoffs, pass handoff_id " +
        "(or path). The tool loads the handoff, marks file-backed handoffs " +
        "consumed so they do not replay, and retires the recorded predecessor " +
        "pane after the successor is alive.",
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
        const cwd = state.cwd || process.cwd();
        const sid = state.sessionId || invocation?.sessionId || null;
        const taskId = (args?.task_id ?? "").toString().trim();
        const handoffId = (args?.handoff_id ?? "").toString().trim();
        const path = (args?.path ?? "").toString().trim();
        const deferComplete = Boolean(args?.defer_complete);

        let result;
        if (taskId) {
          result = consumeDispatchHandoffTask(
            cwd, taskId, sid, deferComplete, { log: logProgress },
          );
        } else if (handoffId || path) {
          result = consumeFileHandoff(
            cwd, sid, handoffId, path || null, { log: logProgress },
          );
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
      name: "continue_handoff",
      description:
        "Live-cutover the CURRENT session to a seeded successor. Call this AFTER " +
        "save_handoff_prompt (the explicit 'kick the flow' step of a live " +
        "handoff): pass `seed` = the exact HANDOFF_SEED string save_handoff_prompt " +
        "returned and `handoff_token` = HANDOFF_TOKEN when supplied. It spawns a successor Copilot in a new window of this " +
        "worktree's mux session, seeds it with that prompt (copilot -i), cuts the " +
        "operator over to it; the successor retires THIS predecessor only after " +
        "it consumes the stored handoff. Requires running " +
        "under a mux session; if not (or the cutover fails) it does nothing " +
        "destructive and says so -- the handoff is still safely stored.",
      skipPermission: true,
      parameters: {
        type: "object",
        properties: {
          seed: {
            type: "string",
            description:
              "The successor's first interactive prompt -- pass the exact " +
              "HANDOFF_SEED string returned by save_handoff_prompt.",
          },
          handoff_token: {
            type: "string",
            description:
              "Optional stored handoff token returned by save_handoff_prompt; " +
              "the current session's most recently saved token is the default.",
          },
        },
      },
      handler: async (args, invocation) => {
        ensureState(invocation);
        const seed = (args?.seed ?? "").toString().trim();
        if (!seed) {
          return (
            "Cannot continue handoff: pass the HANDOFF_SEED string returned by " +
            "save_handoff_prompt as `seed`. Nothing was done. (Call " +
            "save_handoff_prompt first to store the handoff and get the seed.)"
          );
        }
        const cwd = state.cwd || process.cwd();
        const sid = state.sessionId || invocation?.sessionId || null;
        const pending = (
          state.pendingHandoff?.seed === seed
            ? state.pendingHandoff
            : null
        );
        const handoffToken = args?.handoff_token || pending?.token || null;
        if (!handoffToken) {
          return (
            "Cannot continue handoff safely: pass the HANDOFF_TOKEN returned " +
            "by save_handoff_prompt. The successor must receive that token at " +
            "launch so sessionStart can associate the real post-prompt session " +
            "before explicit acknowledgement. Nothing was spawned."
          );
        }
        const result = runHandoffCutover(
          cwd,
          seed,
          sid,
          runCli,
          {
            handoffToken,
            worktreeId: pending?.worktree || null,
          },
        );
        if (!result || !result.ok) {
          const reason = result?.reason || "error";
          const tail =
            " Nothing destructive was done. The handoff is safely stored -- " +
            "resume it the normal way (paste the reply prompt into '/clear', or " +
            "run /resume-handoff in a fresh session in this worktree).";
          if (reason === "no-worktree") {
            // The common bare-resume case: the process IS inside the wt-<id>
            // mux, but Copilot was launched with its cwd at HOME (e.g. a "Bare
            // resume", or the #1416 HOME-cwd binding), so the host verb could
            // not resolve WHICH worktree from cwd -- not a genuine "no mux".
            return (
              "Live cutover is unavailable: could not determine which worktree " +
              "this session belongs to from its working directory (it looks like " +
              "a bare/HOME-cwd resume). The session may well be inside a mux, but " +
              "the cutover needs the worktree checkout as its cwd. `cd` into this " +
              "worktree's directory and try the handoff again, or resume it the " +
              "normal way." +
              tail +
              (result?.error ? ` [host: ${result.error}]` : "")
            );
          }
          if (reason === "no-mux") {
            return (
              "Live cutover is unavailable: this session is not running under a " +
              "mux session (no live wt-<id> session to cut into)." +
              tail +
              (result?.error ? ` [host: ${result.error}]` : "")
            );
          }
          return (
            "Live cutover is unavailable: the cutover verb failed." +
            tail +
            (result?.error ? ` [host: ${result.error}]` : "")
          );
        }
        return (
          `Live cutover initiated. A successor Copilot was spawned in a new window ` +
          `of this worktree's mux session (pane ${result.new_pane || "?"}) and ` +
          `seeded to consume the handoff; the operator has been cut over to it. ` +
          `The predecessor pane will remain available unless and until the ` +
          `successor consumes the handoff and retires it. Do NOT start new work ` +
          `here; simply end your turn. (If the successor window comes up EMPTY -- ` +
          `no session created because its first prompt was never submitted -- ` +
          `call retry_handoff_cutover from here to re-attempt from the same saved ` +
          `handoff, without regenerating it.)`
        );
      },
    },
    {
      name: "retry_handoff_cutover",
      description:
        "Re-attempt a live cutover using the ALREADY-SAVED handoff, WITHOUT " +
        "regenerating or revising it. Use when a prior continue_handoff spawned a " +
        "successor window but no session was created -- the window came up " +
        "'empty' because Copilot never received a submitted first prompt, so no " +
        "sessionStart fired, no changeover was recorded, and the predecessor is " +
        "still live (closing the empty window drops you back here). This tool " +
        "recovers this worktree's stored handoff (agent-dispatch task, else " +
        "worktree file), rebuilds the exact same cutover seed, and spawns a fresh " +
        "seeded successor in the mux. Run it from the predecessor (the pane you " +
        "land on after closing the empty window). Takes no arguments.",
      skipPermission: true,
      parameters: { type: "object", properties: {} },
      handler: async (args, invocation) => {
        void args;
        ensureState(invocation);
        const cwd = state.cwd || process.cwd();
        const sid = state.sessionId || invocation?.sessionId || null;

        const result = retryStoredHandoffCutover(cwd, sid, runCli);
        if (result?.reason === "not-found") {
          return (
            "Cannot retry the cutover: no saved handoff was found for this " +
            "worktree (no proposed agent-dispatch 'handoff' task pinned here, and " +
            "no unconsumed worktree-state handoff file). If you have not saved " +
            "one yet, run save_handoff_prompt first -- there is nothing to " +
            "re-attempt."
          );
        }
        if (!result || !result.ok) {
          const reason = result?.reason || "error";
          const tail =
            " Nothing destructive was done; the saved handoff is untouched. " +
            "Resume it the normal way (/resume-handoff in a fresh session in this " +
            "worktree, or paste the reply prompt into /clear).";
          if (reason === "no-worktree") {
            return (
              "Cannot retry the cutover: could not determine which worktree this " +
              "session belongs to from its working directory (it looks like a " +
              "bare/HOME-cwd resume). `cd` into this worktree's checkout and try " +
              "again." +
              tail +
              (result?.error ? ` [host: ${result.error}]` : "")
            );
          }
          if (reason === "no-mux") {
            return (
              "Cannot retry the cutover: this session is not running under a mux " +
              "session (no live wt-<id> to cut into)." +
              tail +
              (result?.error ? ` [host: ${result.error}]` : "")
            );
          }
          return (
            "Cannot retry the cutover: the cutover verb failed." +
            tail +
            (result?.error ? ` [host: ${result.error}]` : "")
          );
        }
        const src =
          result.stored.storage === "agent-dispatch"
            ? `agent-dispatch task ${result.stored.id}`
            : `handoff file ${result.stored.id}`;
        return (
          `Cutover re-attempted from the saved handoff (${src}). A fresh ` +
          `successor Copilot was spawned in a new window of this worktree's mux ` +
          `session (pane ${result.new_pane || "?"}) and seeded to consume the ` +
          `handoff. If a previous EMPTY successor window is still open (one that ` +
          `never created a session), close it -- it holds no session and nothing ` +
          `is lost. Do NOT start new work here; end your turn -- this predecessor ` +
          `remains a recovery point until the successor consumes the handoff and ` +
          `retires it.`
        );
      },
    },
  ],

  commands: [
    {
      name: "handoff-continue",
      description:
        "Live-cutover handoff: generate a handoff for THIS session, spawn a " +
        "seeded successor Copilot in a new mux window, cut the operator over to " +
        "it; the successor retires the predecessor when it consumes the handoff.",
      handler: async (ctx) => {
        void ctx;
        await session.send({
          prompt:
            "Perform a LIVE-CUTOVER handoff now (the operator invoked " +
            "/handoff-continue). Steps: (1) call generate_handoff_prompt to " +
            "collect session facts; (2) compose continuation markdown per the " +
            "context-handoff skill -- use its compact effort-backed shape when " +
            "a valid open active effort exists, otherwise the full standalone " +
            "shape; (3) call save_handoff_prompt with that markdown as " +
            "`prompt_text` and a short specific `title` -- it stores the handoff " +
            "and returns HANDOFF_SEED/HANDOFF_TOKEN lines; (4) call " +
            "continue_handoff with both exact values -- it spawns the " +
            "seeded successor Copilot in a new window of this worktree's mux " +
            "session and cuts the operator over. After continue_handoff returns " +
            "its confirmation, DO NOT start new work -- just end your turn; this " +
            "session remains as a recovery point until the successor consumes " +
            "the handoff and retires it.",
          displayPrompt: "Live-cutover handoff (/handoff-continue)",
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
              const consumed = consumeDispatchHandoffTask(
                cwd, task.id, sid, true, { deferRetire: true },
              );
              const body = consumed?.payload || "";
              if (!consumed?.ok || !body) {
                await session.log(
                  `Found handoff task ${task.id.slice(0, 8)} but could not claim ` +
                    "and load it. Nothing was injected or retired.",
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
              } catch {
                await session.log(
                  `Claimed handoff task ${task.id.slice(0, 8)}, but prompt ` +
                    "injection failed. The predecessor was not retired; the " +
                    "task remains owned and the durable checkpoint can retry " +
                    "delivery in this successor.",
                  { level: "warning" },
                );
                return;
              }
              completeHandoffLifecycle(
                cwd, consumed.metadata, sid, task.id,
                { checkpoint: consumed.checkpointState, log: logProgress },
              );
              return;
            }
          }
        }

        const checkpoint = findTaskCutoverCheckpoint(cwd, sid);
        if (checkpoint?.handoffToken) {
          const resumed = consumeDispatchHandoffTask(
            cwd,
            checkpoint.handoffToken,
            sid,
            true,
            { deferRetire: true, log: logProgress },
          );
          if (resumed?.ok) {
            await session.send({
              prompt: buildResumePrompt(
                resumed.payload,
                "task-backed cutover checkpoint",
                { deferredTaskId: checkpoint.handoffToken },
              ),
              displayPrompt:
                `Resuming checkpoint ${checkpoint.handoffToken.slice(0, 8)}`,
            });
            completeHandoffLifecycle(
              cwd,
              resumed.metadata,
              sid,
              checkpoint.handoffToken,
              {
                checkpoint: resumed.checkpointState,
                log: logProgress,
              },
            );
            return;
          }
        }

        // Fallback: the newest worktree-state handoff file for this worktree.
        const file = findHandoffFile(cwd, sid);
        if (file) {
          const consumed = consumeFileHandoff(
            cwd, sid, file.record.id, file.path, { deferRetire: true },
          );
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
          completeHandoffLifecycle(
            cwd, consumed.metadata, sid, consumed.id,
            { log: logProgress },
          );
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
if (handoffConfig.warning) {
  session.log(`[Context Handoff] ${handoffConfig.warning}`, { level: "warning" });
}

// Turn counting + first-prompt capture (replaces onUserPromptSubmitted).
session.on("user.message", (event) => {
  state.turnCount++;
  if (!state.firstUserPrompt && event.data?.content) {
    state.firstUserPrompt = event.data.content;
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
let pendingNudge = null;  // null | "soft" | "hard"

session.on("session.idle", () => {
  if (!pendingNudge) return;
  const level = pendingNudge;
  pendingNudge = null;
  const usage = formatContextUsage(state.currentTokens, state.tokenLimit);
  // The nudge JUST hands the agent to the context-handoff skill -- it does NOT
  // prescribe individual tool calls (generate_handoff_prompt/save_handoff_prompt/
  // continue_handoff) or a "write a file" outcome. The skill owns the sequencing;
  // under a mux session that means the autonomous live cutover (spin up a
  // successor Copilot in place, end the turn), not a paste prompt.
  const msg = level === "hard"
    ? `[Context Handoff -- automated] Context utilization is ${usage.utilization} ` +
      `(${usage.tokens}). ` +
      `The configured hard threshold was reached; auto-compaction still triggers ` +
      `at ~80%. Invoke the context-handoff skill now to ` +
      `hand off before context is lost -- under a mux session it cuts over to a ` +
      `fresh successor Copilot in place, automatically (no copy/paste); otherwise ` +
      `it stores the handoff and hands you a short resume prompt.`
    : `[Context Handoff -- automated] Context utilization is ${usage.utilization} ` +
      `(${usage.tokens}). ` +
      `The configured soft threshold was reached. Invoke the context-handoff skill ` +
      `at the next clean boundary -- under a mux session it cuts over to a fresh ` +
      `successor Copilot in place.`;
  session.send(msg).catch((e) =>
    session.log(`[Context Handoff] nudge send failed: ${e.message}`, { level: "warning" })
  );
});

// --- Real-time context utilization monitoring ---
// The session.usage_info event fires with exact token counts after each
// model interaction. This is the authoritative signal for context usage.

session.on("session.usage_info", (event) => {
  const d = event.data;
  state.currentTokens = d.currentTokens;
  state.tokenLimit = d.tokenLimit;
  state.conversationTokens = d.conversationTokens ?? 0;
  state.systemTokens = d.systemTokens ?? 0;
  state.toolDefinitionsTokens = d.toolDefinitionsTokens ?? 0;
  state.messagesLength = d.messagesLength;
  state.lastUtilization = d.tokenLimit > 0 ? d.currentTokens / d.tokenLimit : 0;

  const pressure = contextPressure(
    d.currentTokens,
    d.tokenLimit,
    handoffConfig.thresholds,
  );
  const usage = formatContextUsage(d.currentTokens, d.tokenLimit);

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

  // Soft reminder at threshold (user-visible log only -- agent nudged via session.send on idle)
  if (pressure.soft &&
      !state.softLogShown && !state.handoffGenerated) {
    state.softLogShown = true;
    session.log(
      `[Context Handoff] Context utilization ${usage.utilization} ` +
      `(${usage.tokens}; ` +
      `conversation ${(d.conversationTokens ?? 0).toLocaleString()}, ` +
      `system ${(d.systemTokens ?? 0).toLocaleString()}, ` +
      `tool-defs ${(d.toolDefinitionsTokens ?? 0).toLocaleString()}). ` +
      `Configured ${pressure.softPercent}% threshold ` +
      `${Math.round(pressure.softThreshold).toLocaleString()} ` +
      `tokens reached. Hand off at the next clean boundary ` +
      `(invoke the context-handoff skill).`,
      { level: "warning" }
    );
  }

  // Hard reminder at threshold (user-visible log only -- agent nudged via session.send on idle)
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
      `Configured ${pressure.hardPercent}% hard threshold ` +
      `${Math.round(pressure.hardThreshold).toLocaleString()} ` +
      `tokens reached; auto-compaction still triggers at ~80%. ` +
      `Hand off NOW -- invoke the context-handoff skill.`,
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
    session.log(
      `[Context Handoff] Compaction complete. ` +
      `${d.tokensRemoved?.toLocaleString() ?? "?"} tokens removed, ` +
      `${d.postCompactionTokens?.toLocaleString() ?? "?"} tokens remaining.`
    );
  }
});
