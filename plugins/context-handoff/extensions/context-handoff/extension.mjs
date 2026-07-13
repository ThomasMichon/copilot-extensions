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
  // Live-cutover handoff (issue #2251). Armed by save_handoff_prompt when the
  // operator opts into a live cutover; the old session retires its own pane on
  // the next session.idle (agent-stop of the handoff turn).
  cutover: null,                  // null | { oldPane, retired: false }
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
// it becomes durable, browsable, and claimable. It is picked up two ways, with
// two completion models: a LIVE CUTOVER successor (the primary path) uses
// `agent-dispatch consume <id> --defer-complete` and completes the task
// explicitly when it reaches the goal (deferred); a human paste / /resume-handoff
// uses `agent-dispatch consume <id>` (baton -- completed on pickup). context-
// handoff sits *on top of* agent-dispatch when it exists, and falls back to the
// file flow when it doesn't. All best-effort: any failure returns null / a safe
// default so the caller degrades to the file path.

// True if the `agent-dispatch` CLI answers a health probe (a live coordinator).
function agentDispatchAvailable() {
  try {
    execSync("agent-dispatch health", { timeout: 5000, stdio: "ignore" });
    return true;
  } catch {
    return false;
  }
}

// Run a facility CLI binary cross-platform, returning stdout (throws on error).
// On Windows the `agent-worktrees` / `agent-dispatch` binstubs are `.cmd` files,
// which Node's `execFileSync` CANNOT spawn directly (CreateProcess won't execute
// a batch file without a shell -- it fails ENOENT). So on win32 we go through the
// shell (`execSync`) with each arg quoted for cmd.exe; elsewhere `execFileSync`
// is exact and injection-safe. This is why every agent-worktrees/agent-dispatch
// call here MUST use runCli, not execFileSync (issue: live cutover + task-mode
// silently fell back to file on Windows because execFileSync could not run .cmd).
function quoteWinArg(s) {
  s = String(s);
  return /[\s"&|<>^()%!]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
}
function runCli(bin, args, opts = {}) {
  const { cwd, timeout = 15000 } = opts;
  if (process.platform === "win32") {
    const line = [bin, ...args].map(quoteWinArg).join(" ");
    return execSync(line, { cwd, timeout, encoding: "utf-8" });
  }
  return execFileSync(bin, args, { cwd, timeout, encoding: "utf-8" });
}

// Resolve an agent-worktrees identity value for the current CWD (null on miss).
function agentWorktreesGet(key, cwd) {
  try {
    const out = runCli("agent-worktrees", ["get", key], {
      cwd,
      timeout: 5000,
    }).trim();
    return out || null;
  } catch {
    return null;
  }
}

// --- Live-cutover handoff (issue #2251) ---
// A live cutover spawns a *seeded successor* Copilot in a new window of this
// worktree's mux session, cuts the operator over to it, and later retires this
// (old) session's pane -- so a handoff continues automatically, in place, with
// interactive CLI state preserved. The mux choreography lives in
// `agent-worktrees handoff-cutover`; this extension is a thin trigger. All
// best-effort: any failure returns null / a safe default so the caller falls
// back to the normal store-task-and-reply flow.

// Spawn the successor + cut over. `seed` is the successor's first interactive
// prompt (copilot -i). Returns { ok, old_pane, new_pane } or null on any error
// (not under mux, mux verb failed, agent-worktrees missing, ...).
function runHandoffCutover(cwd, seed) {
  const argv = ["handoff-cutover", "--seed", seed];
  // The extension runs inside the OLD pane; $TMUX_PANE pins it precisely so the
  // retire step targets the right pane even after the cutover moves the active
  // pane to the successor. Falls back to the session's active pane in the CLI.
  const ownPane = process.env.TMUX_PANE || process.env.PSMUX_PANE || "";
  if (ownPane) argv.push("--old-pane", ownPane);
  try {
    const out = runCli("agent-worktrees", argv, {
      cwd,
      timeout: 20000,
    });
    const result = JSON.parse(out);
    return result?.ok ? result : null;
  } catch {
    return null;
  }
}

// Retire a specific pane (double-Ctrl-C -> Copilot's clean quit). Best-effort.
function retireCutoverPane(cwd, pane) {
  try {
    runCli("agent-worktrees", ["handoff-cutover", "--retire-pane", pane], {
      cwd,
      timeout: 20000,
    });
    return true;
  } catch {
    return false;
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
    const out = runCli("agent-dispatch", argv, {
      cwd,
      timeout: 15000,
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
    const out = runCli("agent-dispatch", argv, {
      cwd,
      timeout: 15000,
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
    payload = runCli("agent-dispatch", ["payload", id, "--raw"], {
      cwd,
      timeout: 15000,
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
            "   either the agent-dispatch resume seed ('You are resuming a handoff",
            "   (agent-dispatch task <id>) … run: agent-dispatch consume <id>')",
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
        "task. Returns the short reply prompt AND, on a `HANDOFF_SEED:` line, the " +
        "exact seed string to pass to `continue_handoff` if you are performing a " +
        "LIVE cutover (e.g. from /handoff-continue). The handoff is NEVER loaded " +
        "automatically by a future session.",
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
        const topic = title || "continue this session";

        // Store the handoff (agent-dispatch task preferred, else session file)
        // and derive both the short reply prompt (the baton paste-seed) and the
        // cutover seed. Storage is single-responsibility here; a live cutover is
        // a SEPARATE, explicit continue_handoff call the agent makes afterward,
        // passing the HANDOFF_SEED below (the *deferred* cutover seed).
        let seed = null;          // baton paste-seed (== the paste reply)
        let cutoverSeed = null;   // deferred cutover seed (== HANDOFF_SEED)
        let storedMsg = null;     // the instruction to reply with

        if (agentDispatchAvailable()) {
          const taskId = dispatchHandoff(text, sid, cwd, title);
          if (taskId) {
            // Two seeds, two completion models (see the context-handoff skill):
            //
            // - PASTE seed (baton): a human resuming in-place (/resume-handoff,
            //   or pasting into /clear) is driving, so `consume <id>` loads the
            //   brief AND marks the baton spent -- completed on pickup. The
            //   continuation *work* is tracked by its effort/issue.
            // - CUTOVER seed (deferred): a live-cutover successor is a dispatched
            //   autopilot CLI, so it uses `consume <id> --defer-complete` (load
            //   brief + take ownership, but NOT complete) and completes the task
            //   EXPLICITLY only when it reaches the handoff's goal -- so
            //   `completed` means the work is done, not the baton was handed off.
            //
            // Both are single-line ASCII so they ride `copilot -i` intact.
            seed =
              `You are resuming a handoff (agent-dispatch task ${taskId}); ` +
              `continue the prior session's work IN PLACE -- do not restart or ` +
              `create a new worktree. Load your full brief by running: ` +
              `agent-dispatch consume ${taskId} ; then continue: ${topic}.`;
            cutoverSeed =
              `You are taking over a handoff (agent-dispatch task ${taskId}) IN ` +
              `PLACE -- do not restart or create a new worktree. Load your brief ` +
              `and take ownership with: agent-dispatch consume ${taskId} ` +
              `--defer-complete ; do the work, and ONLY when you reach the ` +
              `handoff's goal run: agent-dispatch complete ${taskId} . ` +
              `Continue: ${topic}.`;
            storedMsg = (
              `Handoff stored as agent-dispatch task ${taskId} (proposed, label ` +
              `'handoff', pinned to this worktree). No session file was written.\n\n` +
              `PRIMARY PATH -- live cutover (no copy/paste): call continue_handoff ` +
              `with \`seed\` = the HANDOFF_SEED below to spin up the successor in ` +
              `place and hand off automatically. Only if that reports it is not ` +
              `under a mux session (graceful fallback) do you reply to the user ` +
              `with ONLY this short paste prompt (they resume via /resume-handoff ` +
              `or by pasting into /clear):\n` +
              `  ${seed}\n` +
              `Do NOT paste the handoff contents -- the payload lives in the task ` +
              `and is loaded on demand by the embedded command.`
            );
          }
          // Task creation failed -- fall through to the file flow.
        }

        if (!seed) {
          const promptPath = saveHandoffPrompt(text, sid);
          seed = `Read the handoff at ${promptPath} and continue: ${topic}.`;
          cutoverSeed = seed;  // no task in file mode: cutover reuses the paste seed
          storedMsg = (
            `Handoff saved to ${promptPath}\n\n` +
            `(Stored as a session file — no reachable agent-dispatch coordinator, ` +
            `or the worktree couldn't be resolved.) Reply to the user with ONLY a ` +
            `short wrapper prompt they copy verbatim into '/clear' (or '/new'). ` +
            `The wrapper is addressed to the NEXT session's agent and must (1) name ` +
            `this absolute path (a ~/ form is fine) and (2) instruct that agent to ` +
            `READ the handoff file and continue. For example:\n` +
            `  ${seed}\n` +
            `Do NOT paste the file's contents, and do NOT claim it loads ` +
            `automatically on restart -- it does not.`
          );
        }

        // The HANDOFF_SEED line is the machine-readable seed for a LIVE cutover
        // (the PRIMARY handoff path): call continue_handoff with `seed` set to
        // exactly this string. For a task-backed handoff it is the *deferred*
        // cutover seed (the successor completes explicitly at the goal).
        return (
          `${storedMsg}\n\n` +
          `HANDOFF_SEED: ${cutoverSeed}\n` +
          `(Live cutover is the PRIMARY path: call continue_handoff with \`seed\` ` +
          `set to exactly the HANDOFF_SEED string above. Only fall back to the ` +
          `short paste prompt if continue_handoff reports no mux session.)`
        );
      },
    },
    {
      name: "continue_handoff",
      description:
        "Live-cutover the CURRENT session to a seeded successor. Call this AFTER " +
        "save_handoff_prompt (the explicit 'kick the flow' step of a live " +
        "handoff): pass `seed` = the exact HANDOFF_SEED string save_handoff_prompt " +
        "returned. It spawns a successor Copilot in a new window of this " +
        "worktree's mux session, seeds it with that prompt (copilot -i), cuts the " +
        "operator over to it, and arms THIS session to quit when the current turn " +
        "ends (double-Ctrl-C to its own pane on agent-stop). Requires running " +
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
              "HANDOFF_SEED string returned by save_handoff_prompt (e.g. 'Claim " +
              "and act on the handoff <id> …' or 'Read the handoff at <path> …').",
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
        const result = runHandoffCutover(cwd, seed);
        if (!result) {
          return (
            "Live cutover is unavailable: this session is not running under a mux " +
            "session, or the cutover verb failed. Nothing destructive was done. " +
            "The handoff is safely stored -- resume it the normal way (paste the " +
            "reply prompt into '/clear', or run /resume-handoff in a fresh session " +
            "in this worktree)."
          );
        }
        // Completion is owned by the successor's `agent-dispatch consume` (the
        // seed's load-and-consume command): a handoff is marked completed the
        // moment it is actually picked up, on every resume path, and a
        // never-consumed handoff correctly stays claimable for retry. The old
        // session therefore does NOT pre-complete the task here.
        // Arm self-retire: this old session quits on the next session.idle
        // (agent-stop of this turn); the successor already holds the seed.
        state.cutover = { oldPane: result.old_pane || null, retired: false };
        return (
          `Live cutover initiated. A successor Copilot was spawned in a new window ` +
          `of this worktree's mux session (pane ${result.new_pane || "?"}) and ` +
          `seeded to resume the handoff; the operator has been cut over to it. ` +
          `THIS session will quit automatically when the current turn ends -- do ` +
          `NOT start new work; simply end your turn.`
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
        "it, and quit this session -- an automatic hands-free continuation.",
      handler: async (ctx) => {
        void ctx;
        await session.send({
          prompt:
            "Perform a LIVE-CUTOVER handoff now (the operator invoked " +
            "/handoff-continue). Steps: (1) call generate_handoff_prompt to " +
            "collect session facts; (2) compose the full continuation markdown " +
            "per the context-handoff skill (original request, direction & " +
            "motivation, progress with file paths, next action items, target " +
            "goals, gotchas); (3) call save_handoff_prompt with that markdown as " +
            "`prompt_text` and a short specific `title` -- it stores the handoff " +
            "and returns a HANDOFF_SEED line; (4) call continue_handoff with " +
            "`seed` set to EXACTLY that HANDOFF_SEED string -- it spawns the " +
            "seeded successor Copilot in a new window of this worktree's mux " +
            "session and cuts the operator over. After continue_handoff returns " +
            "its confirmation, DO NOT start new work -- just end your turn; this " +
            "session quits itself.",
          displayPrompt: "Live-cutover handoff (/handoff-continue)",
        });
      },
    },
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
  // Live-cutover self-retire: once the handoff turn ends (agent-stop), quit this
  // (old) session by double-Ctrl-C'ing its own pane. The successor was already
  // spawned + seeded, so nothing is lost. Only retires a KNOWN pane -- never the
  // session's current active pane, which post-cutover is the successor.
  if (state.cutover && !state.cutover.retired) {
    state.cutover.retired = true;
    if (state.cutover.oldPane) {
      const cwd = state.cwd || process.cwd();
      retireCutoverPane(cwd, state.cutover.oldPane);
    } else {
      session.log(
        "[Context Handoff] live cutover armed but no old pane id was captured; " +
          "leaving this session running (retire manually with a double Ctrl-C).",
        { level: "warning" },
      ).catch(() => {});
    }
    return;
  }

  if (!pendingNudge) return;
  const level = pendingNudge;
  pendingNudge = null;
  const pct = Math.round(state.lastUtilization * 100);
  const tokens =
    `${state.currentTokens.toLocaleString()} / ${state.tokenLimit.toLocaleString()} tokens`;
  // The nudge JUST hands the agent to the context-handoff skill -- it does NOT
  // prescribe individual tool calls (generate_handoff_prompt/save_handoff_prompt/
  // continue_handoff) or a "write a file" outcome. The skill owns the sequencing;
  // under a mux session that means the autonomous live cutover (spin up a
  // successor Copilot in place, end the turn), not a paste prompt.
  const msg = level === "hard"
    ? `[Context Handoff -- automated] Context window is ${pct}% full (${tokens}). ` +
      `Auto-compaction triggers at ~80%. Invoke the context-handoff skill now to ` +
      `hand off before context is lost -- under a mux session it cuts over to a ` +
      `fresh successor Copilot in place, automatically (no copy/paste); otherwise ` +
      `it stores the handoff and hands you a short resume prompt.`
    : `[Context Handoff -- automated] Context window is ${pct}% full (${tokens}). ` +
      `Consider invoking the context-handoff skill soon to hand off -- under a mux ` +
      `session it cuts over to a fresh successor Copilot in place. No rush -- ` +
      `finish your current task first.`;
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
      `Consider handing off soon (invoke the context-handoff skill).`,
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
      `Auto-compaction triggers at ~80%. Hand off NOW -- invoke the context-handoff skill.`,
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
