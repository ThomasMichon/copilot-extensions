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
// 2. save_handoff_prompt tool -- persist composed handoff to session folder
// 3. session.usage_info event -- real-time context utilization monitoring
// 4. tool.execution_start / _complete events -- track modified files + tools
// 5. user.message event -- tracks turn count + first prompt (topic bias)
// 6. session.idle event -- delivers the queued context-pressure nudge to the
//    agent via session.send() (replaces onPostToolUse additionalContext)
//
// The /handoff gesture is handled as a skill invocation (context-handoff
// skill), not a slash command. The skill triggers the agent to call
// generate_handoff_prompt, compose prose, and call save_handoff_prompt.
// The user then copies the short prompt and pastes it into a new session.

import {
  existsSync,
  mkdirSync,
  writeFileSync,
  unlinkSync,
  readFileSync,
  readdirSync,
  statSync,
} from "node:fs";
import { execSync, execFileSync } from "node:child_process";
import { join, basename } from "node:path";
import { homedir, tmpdir } from "node:os";
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

// --- agent-dispatch integration (soft dependency) ---
// When an agent-dispatch coordinator is reachable, a handoff is stored as a
// *task* (payload = the handoff markdown) instead of a session-folder file, so
// it becomes durable, browsable, and claimable -- consumed next session with
// /resume-handoff (or by pasting the "Claim and act on the handoff <id>"
// prompt). context-handoff sits *on top of* agent-dispatch when it exists, and
// falls back to the file flow when it doesn't. All best-effort: any failure
// returns null / a safe default so the caller degrades to the file path.

// True if the `agent-dispatch` CLI answers a health probe (a live coordinator).
function agentDispatchAvailable() {
  try {
    execSync("agent-dispatch health", { timeout: 5000, stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

// Resolve an agent-worktrees identity value for the current CWD (null on miss).
function agentWorktreesGet(key, cwd) {
  try {
    const out = execFileSync("agent-worktrees", ["get", key], {
      cwd,
      timeout: 5000,
      encoding: "utf-8",
    }).trim();
    return out || null;
  } catch {
    return null;
  }
}

// Store a handoff as a proposed, handoff-labeled agent-dispatch task pinned to
// the current worktree; payload = the handoff markdown. Returns the task id, or
// null if anything fails (the caller then falls back to a session file).
function dispatchHandoff(promptText, sid, cwd, title) {
  const tmp = join(tmpdir(), `handoff-${sid}.md`);
  try {
    writeFileSync(tmp, promptText, "utf-8");
    const machine = agentWorktreesGet("machine", cwd);
    const wtDir = agentWorktreesGet("worktree-dir", cwd);
    const worktree = wtDir ? basename(wtDir) : null;
    // A handoff must land in *its own* worktree; if we can't resolve one, bail
    // to the file flow rather than file an unpinned, anyone-can-claim task.
    if (!worktree) return null;
    const argv = [
      "create",
      title || "Handoff: continue this session",
      "--proposed",
      "--label", "handoff",
      "--source", "context-handoff",
      "--dedup-key", `handoff-${sid}`,
      "--payload-file", tmp,
      "--target-worktree", worktree,
      "--affinity", `worktree=${worktree}`,
    ];
    if (machine) argv.push("--target-machine", machine);
    const out = execFileSync("agent-dispatch", argv, {
      cwd,
      timeout: 15000,
      encoding: "utf-8",
    });
    const task = JSON.parse(out);
    return task?.id || null;
  } catch {
    return null;
  } finally {
    try { unlinkSync(tmp); } catch { /* temp already gone -- fine */ }
  }
}

// Run an `agent-dispatch` subcommand, returning parsed JSON (or null on error).
function agentDispatchJson(argv, cwd) {
  try {
    const out = execFileSync("agent-dispatch", argv, {
      cwd,
      timeout: 15000,
      encoding: "utf-8",
    });
    return JSON.parse(out);
  } catch {
    return null;
  }
}

// Find this worktree's newest pending handoff task (proposed + label 'handoff',
// pinned to `worktree`). Returns the task object, or null if none / no CLI.
function findHandoffTask(cwd, worktree) {
  const tasks = agentDispatchJson(
    ["list", "--status", "proposed", "--label", "handoff"],
    cwd,
  );
  if (!Array.isArray(tasks)) return null;
  const mine = tasks.filter((t) => t?.target_worktree === worktree);
  if (mine.length === 0) return null;
  mine.sort((a, b) => (a.created_at < b.created_at ? 1 : -1)); // newest first
  return mine[0];
}

// Consume a handoff task (approve -> claim -> start -> complete: a handoff is a
// baton, delivered once picked up) and return { payload, prompt, id } for
// injection, or null if any step fails (e.g. another session claimed it first).
function consumeHandoffTask(cwd, task, sid) {
  const id = task.id;
  if (!agentDispatchJson(["approve", id], cwd)) return null;
  const claimed = agentDispatchJson(["claim", "--task", id], cwd);
  const owner = claimed?.owner;
  if (!owner) return null;
  agentDispatchJson(["start", id, owner], cwd);
  agentDispatchJson(
    ["complete", id, owner, "--result-ref", `resumed:${sid}`],
    cwd,
  );
  let payload = "";
  try {
    payload = execFileSync("agent-dispatch", ["payload", id, "--raw"], {
      cwd,
      timeout: 15000,
      encoding: "utf-8",
    });
  } catch {
    payload = "";
  }
  return { id, owner, payload: payload.trim(), prompt: task.prompt || "" };
}

// Fallback (no coordinator): find the newest session-folder handoff file whose
// recorded CWD matches the current one. Returns { path, text } or null.
function findHandoffFile(cwd) {
  const root = join(homedir(), ".copilot", "session-state");
  if (!existsSync(root)) return null;
  let best = null;
  let bestMtime = 0;
  let sessions;
  try {
    sessions = readdirSync(root);
  } catch {
    return null;
  }
  for (const sid of sessions) {
    const path = join(root, sid, "files", `${sid}-prompt.md`);
    if (!existsSync(path)) continue;
    let text;
    let mtime;
    try {
      text = readFileSync(path, "utf-8");
      mtime = statSync(path).mtimeMs;
    } catch {
      continue;
    }
    // Prefer files whose recorded "**CWD:** <path>" matches this worktree.
    const cwdMatch = text.includes(`**CWD:** ${cwd}`) || text.includes(cwd);
    const score = mtime + (cwdMatch ? 1e15 : 0); // CWD match dominates recency
    if (score > bestMtime) {
      bestMtime = score;
      best = { path, text };
    }
  }
  return best;
}

// Compose the prompt injected into the current session on resume.
function buildResumePrompt(handoffText, source) {
  return [
    `You are resuming a handoff (${source}). The full continuation context`,
    `follows -- treat it as the founding brief for this session and carry the`,
    `work forward from where the previous session left off. Do NOT start over`,
    `or spin up a fresh worktree; continue in place.`,
    ``,
    `---`,
    ``,
    handoffText,
  ].join("\n");
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
            "Now follow the context-handoff skill:",
            "1. Compose the FULL handoff markdown — direction + motivation of the",
            "   work, key next action items, and target goals — from this data",
            "   plus your live context. Lead with the original topic/request.",
            "2. Call save_handoff_prompt with the full markdown as `prompt_text`",
            "   (and an optional short `title`). It stores the handoff — as an",
            "   agent-dispatch task when a coordinator is reachable, else a",
            "   session file — and returns EXACTLY the short prompt to reply with.",
            "3. Reply with ONLY that short prompt (the tool tells you which form):",
            "   either 'Claim and act on the handoff <id> from agent-dispatch: …'",
            "   or 'Read the handoff at <path> and continue: …'. The user pastes",
            "   it into '/clear' (or '/new'); the dispatch form is also resumable",
            "   via /resume-handoff.",
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
        "Store the full handoff markdown and return what short prompt to reply " +
        "with. When an agent-dispatch coordinator is reachable, the handoff is " +
        "stored as a *proposed, handoff-labeled task* pinned to this worktree " +
        "(payload = the markdown, no session file) and resumed next session via " +
        "/resume-handoff; otherwise it falls back to a file in the CURRENT " +
        "session's state folder. Call this after composing the handoff from " +
        "generate_handoff_prompt data. Pass the markdown as `prompt_text` (the " +
        "`prompt` alias is also accepted); an optional short `title` labels the " +
        "task. The handoff is NEVER loaded automatically by a future session.",
      skipPermission: true,
      parameters: {
        type: "object",
        properties: {
          prompt_text: {
            type: "string",
            description: "The full composed handoff markdown text.",
          },
          title: {
            type: "string",
            description:
              "Optional short, specific title for the handoff task (e.g. " +
              "'Continue: agent-dispatch producers'). Used only in the " +
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
            "Cannot save handoff: pass the full handoff markdown as `prompt_text` " +
            "(the `prompt` alias is also accepted). Nothing was written."
          );
        }

        const cwd = state.cwd || process.cwd();
        const title = (args?.title ?? "").toString().trim();

        // Prefer agent-dispatch: store the handoff as a claimable task (no file).
        if (agentDispatchAvailable()) {
          const taskId = dispatchHandoff(text, sid, cwd, title);
          if (taskId) {
            return (
              `Handoff stored as agent-dispatch task ${taskId} (proposed, label ` +
              `'handoff', pinned to this worktree). No session file was written.\n\n` +
              `Reply to the user with ONLY this short prompt. They resume by ` +
              `running /resume-handoff in a fresh session in this worktree, or by ` +
              `pasting it into '/clear' (or '/new'):\n` +
              `  Claim and act on the handoff ${taskId} from agent-dispatch: <one-line topic>.\n` +
              `Do NOT paste the handoff contents -- the payload lives in the task, ` +
              `and it is never loaded automatically.`
            );
          }
          // Task creation failed -- fall through to the file flow.
        }

        const promptPath = saveHandoffPrompt(text, sid);

        return (
          `Handoff saved to ${promptPath}\n\n` +
          `(Stored as a session file — no reachable agent-dispatch coordinator, ` +
          `or the worktree couldn't be resolved.) Reply to the user with ONLY a ` +
          `short wrapper prompt they copy verbatim into '/clear' (or '/new'). ` +
          `The wrapper is addressed to the NEXT session's agent and must (1) name ` +
          `this absolute path (a ~/ form is fine) and (2) instruct that agent to ` +
          `READ the handoff file and continue. For example:\n` +
          `  Read the handoff at ${promptPath} and continue: <one-line topic>.\n` +
          `Do NOT paste the file's contents, and do NOT claim it loads ` +
          `automatically on restart -- it does not.`
        );
      },
    },
  ],

  commands: [
    {
      name: "resume-handoff",
      description:
        "Dig up this worktree's pending handoff and inject its continuation " +
        "prompt into THIS session (foreground). Consumes the agent-dispatch " +
        "handoff task if present, else the newest matching session file.",
      handler: async (ctx) => {
        const cwd = state.cwd || process.cwd();
        const sid = state.sessionId || ctx?.sessionId || "unknown";

        // Prefer an agent-dispatch handoff task pinned to this worktree.
        if (agentDispatchAvailable()) {
          const wtDir = agentWorktreesGet("worktree-dir", cwd);
          const worktree = wtDir ? basename(wtDir) : null;
          if (worktree) {
            const task = findHandoffTask(cwd, worktree);
            if (task) {
              const consumed = consumeHandoffTask(cwd, task, sid);
              const body = consumed?.payload || consumed?.prompt;
              if (consumed && body) {
                await session.send({
                  prompt: buildResumePrompt(body, "agent-dispatch task"),
                  displayPrompt: `Resuming handoff ${consumed.id.slice(0, 8)} from agent-dispatch`,
                });
                return;
              }
              await session.log(
                `Found handoff task ${task.id.slice(0, 8)} but could not consume it ` +
                  `(it may have been claimed by another session). Nothing injected.`,
                { level: "warning" },
              );
              return;
            }
          }
        }

        // Fallback: the newest session-folder handoff file for this worktree.
        const file = findHandoffFile(cwd);
        if (file) {
          await session.send({
            prompt: buildResumePrompt(file.text, `file ${file.path}`),
            displayPrompt: `Resuming handoff from ${basename(file.path)}`,
          });
          return;
        }

        await session.log(
          "No pending handoff found for this worktree (no agent-dispatch task " +
            "and no matching session file). If you have a handoff prompt, paste it directly.",
          { level: "warning" },
        );
      },
    },
  ],
});

await session.log("Context handoff extension loaded");

// --- Session lifecycle reconstructed from events (SDK callback hooks removed) ---
// The native runtime dropped SDK callback hooks ("SDK hook callbacks are no
// longer supported by the native runtime"), which hard-failed joinSession when
// a `hooks` object was passed. The former onSessionStart / onUserPromptSubmitted
// / onPostToolUse behaviours are reconstructed below from observable session
// events. The extension module loads once per session, so session-start work
// runs inline here (session.sessionId is available directly on the session).
state.sessionId = session.sessionId ?? state.sessionId ?? null;
state.cwd = state.cwd || process.cwd();
state.turnCount = 0;
await session.log(
  `[Context Handoff] Session started (id=${state.sessionId}, cwd=${state.cwd})`
);

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
  const pct = Math.round(state.lastUtilization * 100);
  const tokens =
    `${state.currentTokens.toLocaleString()} / ${state.tokenLimit.toLocaleString()} tokens`;
  const msg = level === "hard"
    ? `[Context Handoff -- automated] Context window is ${pct}% full (${tokens}). ` +
      `Auto-compaction triggers at ~80%. Invoke the context-handoff skill now ` +
      `(call generate_handoff_prompt) to write a handoff file before context is lost.`
    : `[Context Handoff -- automated] Context window is ${pct}% full (${tokens}). ` +
      `Consider invoking the context-handoff skill soon (call generate_handoff_prompt) ` +
      `to write a handoff file. No rush -- finish your current task first.`;
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

  const pct = Math.round(state.lastUtilization * 100);

  // Queue an agent-facing nudge once per threshold, delivered on the next
  // idle via session.send() (see the session.idle handler above). This is the
  // agent-visible counterpart to the user-visible logs below.
  if (state.lastUtilization >= HARD_UTILIZATION_THRESHOLD &&
      !state.hardReminderSent && !state.handoffGenerated) {
    state.hardReminderSent = true;
    state.softReminderSent = true;  // hard implies soft
    pendingNudge = "hard";
  } else if (state.lastUtilization >= SOFT_UTILIZATION_THRESHOLD &&
      !state.softReminderSent && !state.handoffGenerated) {
    state.softReminderSent = true;
    pendingNudge = "soft";
  }

  // Soft reminder at threshold (user-visible log only -- agent nudged via session.send on idle)
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

  // Hard reminder at threshold (user-visible log only -- agent nudged via session.send on idle)
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
