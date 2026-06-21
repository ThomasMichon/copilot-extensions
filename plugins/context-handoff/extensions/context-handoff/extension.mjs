// Context Handoff Extension for Copilot CLI
//
// Tracks session state and provides a generate_handoff_prompt tool
// for creating continuation prompts when context is getting large.
//
// Uses the session.usage_info event for accurate context window monitoring
// (currentTokens / tokenLimit) instead of heuristic turn counting.
//
// Integration points:
// 1. generate_handoff_prompt tool -- on-demand structured handoff data
// 2. save_handoff_prompt tool -- persist composed handoff to session folder
// 3. session.usage_info event -- real-time context utilization monitoring
// 4. onPostToolUse hook -- tracks modified files, context utilization reminders
// 5. onUserPromptSubmitted hook -- tracks turn count (supplementary metric)
//
// The /handoff gesture is handled as a skill invocation (context-handoff
// skill), not a slash command. The skill triggers the agent to call
// generate_handoff_prompt, compose prose, and call save_handoff_prompt.
// The user then copies the short prompt and pastes it into a new session.

import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import { execSync } from "node:child_process";
import { join } from "node:path";
import { homedir } from "node:os";
import { approveAll } from "@github/copilot-sdk";
import { joinSession } from "@github/copilot-sdk/extension";

// --- Configuration ---
// Context utilization thresholds (0.0-1.0)
// Background compaction defaults to 0.80, so we warn well before that.
const SOFT_UTILIZATION_THRESHOLD = 0.55;  // Gentle reminder
const HARD_UTILIZATION_THRESHOLD = 0.70;  // Urgent reminder

// --- State ---
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

function getGitInfo(cwd) {
  const run = (cmd) => {
    try {
      return execSync(cmd, { cwd, timeout: 5000, encoding: "utf-8" }).trim();
    } catch { return null; }
  };
  // Cap status to first 30 lines to avoid huge diffs
  let status = run("git status --short");
  if (status) {
    const lines = status.split("\n");
    if (lines.length > 30) {
      status = lines.slice(0, 30).join("\n") + `\n... ${lines.length - 30} more files omitted`;
    }
  }
  return {
    branch: run("git rev-parse --abbrev-ref HEAD"),
    repo: run("git remote get-url origin"),
    status,
  };
}

// --- Shared Logic ---

// Collect structured handoff data from current session state.
// Used by both the generate_handoff_prompt tool and the /handoff command.
function collectHandoffData(sid, overrides = {}) {
  const cwd = state.cwd || process.cwd();
  const git = getGitInfo(cwd);
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

// Persist a handoff prompt file in the current session's state folder.
function saveHandoffPrompt(promptText, sid) {
  const stateDir = join(homedir(), ".copilot", "session-state", sid, "files");
  if (!existsSync(stateDir)) {
    mkdirSync(stateDir, { recursive: true });
  }
  const promptPath = join(stateDir, `${sid}-prompt.md`);
  writeFileSync(promptPath, promptText, "utf-8");
  return promptPath;
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
    `**Branch:** ${handoffData.branch || "(detached)"}`,
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
        "and key tool invocations. The agent should compose the final " +
        "prose handoff using this data plus its own live context.",
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
            `**Branch:** ${handoffData.branch || "(detached)"}`,
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
            handoffData.gitStatus || "(clean)",
            "```",
            "",
            args.summary ? `### Agent Summary\n${args.summary}\n` : "",
            args.next_steps ? `### Agent Next Steps\n${args.next_steps}\n` : "",
            "",
            "---",
            "Compose a continuation prompt using this data plus your live context.",
            "Follow the template in the context-handoff skill.",
            "**IMPORTANT:** Do not exceed 40 lines or ~250 words.",
            "Lead with the original topic/request — not recent activity.",
            "Prefer omission over completeness. Include only details needed to resume.",
            "Present it to the user in a fenced code block they can copy.",
            "Then call save_handoff_prompt with the composed text.",
            "Present the short prompt to the user in a fenced code block they can copy-paste into a new session.",
          ].join("\n"),
          resultType: "success",
        };
      },
    },
    {
      name: "save_handoff_prompt",
      description:
        "Persist a composed continuation prompt so future sessions in " +
        "the same worktree can discover it via onSessionStart. Call this " +
        "after composing the handoff prose from generate_handoff_prompt data.",
      skipPermission: true,
      parameters: {
        type: "object",
        properties: {
          prompt_text: {
            type: "string",
            description: "The full composed continuation prompt text.",
          },
        },
        required: ["prompt_text"],
      },
      handler: async (args, invocation) => {
        ensureState(invocation);
        const sid = state.sessionId || invocation?.sessionId;
        if (!sid || sid === "unknown") {
          return "Cannot save handoff prompt: sessionId is unavailable.";
        }

        const promptPath = saveHandoffPrompt(args.prompt_text, sid);

        return `Handoff prompt saved to ${promptPath} and pointer updated.`;
      },
    },
  ],

  hooks: {
    onSessionStart: async (input, invocation) => {
      state.sessionId = invocation?.sessionId ?? null;
      state.cwd = input?.cwd || process.cwd();
      state.turnCount = 0;

      await session.log(
        `[Context Handoff] Session started (id=${state.sessionId}, cwd=${state.cwd}, source=${input?.source ?? "?"})`
      );
    },

    onUserPromptSubmitted: async (input, invocation) => {
      ensureState(invocation);
      state.turnCount++;

      // Capture the first user message for topic bias in handoffs
      if (!state.firstUserPrompt && input?.prompt) {
        state.firstUserPrompt = input.prompt;
      }
    },

    onPostToolUse: async (input, invocation) => {
      ensureState(invocation);

      // Track file modifications
      if ((input.toolName === "edit" || input.toolName === "create") && input.toolArgs?.path) {
        state.filesModified.set(input.toolArgs.path, {
          tool: input.toolName,
          turnIndex: state.turnCount,
        });
      }

      // Track notable tool invocations (skip high-frequency read-only tools)
      const skipTools = new Set(["view", "glob", "grep", "report_intent", "sql", "session_store_sql"]);
      if (!skipTools.has(input.toolName)) {
        const summary = input.toolName === "edit" || input.toolName === "create"
          ? input.toolArgs?.path || ""
          : input.toolName === "powershell" || input.toolName === "bash"
            ? (String(input.toolArgs?.description || input.toolArgs?.command || "")).slice(0, 80)
            : input.toolName === "task"
              ? `${input.toolArgs?.agent_type || ""}: ${(input.toolArgs?.description || "").slice(0, 60)}`
              : JSON.stringify(input.toolArgs || {}).slice(0, 80);

        state.toolInvocations.push({
          tool: input.toolName,
          turn: state.turnCount,
          summary,
        });

        // Cap at 50 entries to avoid unbounded growth
        if (state.toolInvocations.length > 50) {
          state.toolInvocations = state.toolInvocations.slice(-30);
        }
      }

      // --- Context utilization reminders (injected as additionalContext) ---
      // The session.usage_info event updates state.lastUtilization with exact
      // token counts. We check those values here because onPostToolUse can
      // return additionalContext that the LLM actually sees.
      const pct = Math.round(state.lastUtilization * 100);

      if (state.lastUtilization >= HARD_UTILIZATION_THRESHOLD &&
          !state.hardReminderSent && !state.handoffGenerated) {
        state.hardReminderSent = true;
        state.softReminderSent = true;  // hard implies soft
        return {
          additionalContext:
            `[Context Handoff] ⚠️ Context window is ${pct}% full ` +
            `(${state.currentTokens.toLocaleString()} / ${state.tokenLimit.toLocaleString()} tokens). ` +
            `Auto-compaction triggers at ~80%. Call generate_handoff_prompt NOW ` +
            `to prepare a continuation prompt before context is lost.`,
        };
      }

      if (state.lastUtilization >= SOFT_UTILIZATION_THRESHOLD &&
          !state.softReminderSent && !state.handoffGenerated) {
        state.softReminderSent = true;
        return {
          additionalContext:
            `[Context Handoff] Context window is ${pct}% full ` +
            `(${state.currentTokens.toLocaleString()} / ${state.tokenLimit.toLocaleString()} tokens). ` +
            `Consider calling generate_handoff_prompt soon to prepare a continuation ` +
            `prompt. No rush — finish your current task first.`,
        };
      }
    },
  },
});

await session.log("Context handoff extension loaded");

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

  const pct = Math.round(state.lastUtilization * 100);

  // Soft reminder at threshold (user-visible log only — agent sees it via onPostToolUse)
  if (state.lastUtilization >= SOFT_UTILIZATION_THRESHOLD &&
      !state.softLogShown && !state.handoffGenerated) {
    state.softLogShown = true;
    session.log(
      `[Context Handoff] Context utilization at ${pct}% ` +
      `(${d.currentTokens.toLocaleString()} / ${d.tokenLimit.toLocaleString()} tokens). ` +
      `Consider generating a handoff prompt soon.`,
      { level: "warning" }
    );
  }

  // Hard reminder at threshold (user-visible log only — agent sees it via onPostToolUse)
  if (state.lastUtilization >= HARD_UTILIZATION_THRESHOLD &&
      !state.hardLogShown && !state.handoffGenerated) {
    state.hardLogShown = true;
    state.softLogShown = true;  // hard implies soft
    session.log(
      `[Context Handoff] ⚠️ Context utilization at ${pct}% ` +
      `(${d.currentTokens.toLocaleString()} / ${d.tokenLimit.toLocaleString()} tokens). ` +
      `Auto-compaction triggers at ~80%. Generate a handoff prompt NOW.`,
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
