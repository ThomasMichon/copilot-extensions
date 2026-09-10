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
  buildResumePrompt,
  buildSeedForStored,
  collectAdvisoryGitFacts,
  consumeDispatchHandoffTask,
  consumeFileHandoff,
  findHandoffFile,
  findHandoffTask,
  findTaskDeliveryCheckpoint,
  formatConsumeResult,
  markDeliveryPromptInjected,
  normalizeHandoffTitle,
  runCli,
  storeHandoff,
  triggerHandoff,
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
            "   If this repo also has an effort capability, use that binding to",
            "   let one session own only a natural slice of the larger objective;",
            "   stride forward confidently, stop at a clean boundary, and hand off",
            "   the next slice rather than forcing one session to bite off the",
            "   entire effort.",
            "   If the parent objective truly has no actionable work left, do not",
            "   create a handoff merely to report that fact; finish instead.",
            "2. Call save_handoff_prompt with the composed markdown as `prompt_text`",
            "   (and an optional short `title`). It stores the handoff — as an",
            "   agent-dispatch task when a coordinator is reachable, else a",
            "   one-time worktree-state file — and returns the short handoff",
            "   prompt plus its exact HANDOFF_SEED/HANDOFF_TOKEN identifiers.",
            "3. Distinguish the trigger:",
            "   - If context pressure is the reason for the handoff and work",
            "     remains, call trigger_handoff directly after saving. Do NOT ask",
            "     for confirmation first; continuity is the point.",
            "   - If you are otherwise done with the requested work and would end",
            "     the turn by listing follow-up ideas/questions, store the baton",
            "     and replace that list with one short offer to continue via",
            "     handoff. Only after the user says yes should you call",
            "     trigger_handoff, unless autopilot or prior authorization",
            "     already covers that turn-end follow-up path.",
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
          "still remains, call `trigger_handoff` directly now.\n\n" +
          "If this is a turn-end follow-up handoff, ask the user whether to " +
          "continue via handoff and call `trigger_handoff` only after they say " +
          "yes, unless autopilot or prior authorization already covers that path.\n\n" +
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
        const cwd = state.cwd || process.cwd();
        const sid = state.sessionId || invocation?.sessionId || null;
        const taskId = (args?.task_id ?? "").toString().trim();
        const handoffId = (args?.handoff_id ?? "").toString().trim();
        const path = (args?.path ?? "").toString().trim();
        const deferComplete = Boolean(args?.defer_complete);

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
        "`prompt_text` / `prompt`. It then: (1) drops the full handoff markdown " +
        "in this session's session-state folder; (2) refreshes worktree-visible " +
        "pending-handoff state when agent-worktrees is available; (3) reuses the " +
        "existing agent-dispatch task path when available; (4) best-effort pings " +
        "agent-bridge if present; (5) waits up to 30 seconds for the CUTOVER " +
        "to start (a status-monitor `handoff_cutover_spawn` acknowledgement) " +
        "-- not for the successor to fully finish cold-starting and consume " +
        "the handoff, which legitimately takes longer and is not worth " +
        "blocking on; (6) reports whether a cutover is already under way, " +
        "already fully picked up, or neither; (7) prints manual fallback " +
        "guidance only when truly nothing happened; and " +
        "(8) ALWAYS ends with the final short handoff prompt/seed. " +
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
        "pending-handoff signal flow. This is the explicit human yes-path: it " +
        "does NOT spawn or retire sessions, but it does arm the baton for any " +
        "human or control system watching the worktree, dispatch task, or " +
        "session-state marker.",
      handler: async (ctx) => {
        void ctx;
        await session.send({
          prompt:
            "Perform a handoff now (the operator invoked /handoff-continue, " +
            "which is explicit authorization). Steps: (1) call " +
            "generate_handoff_prompt to collect session facts; (2) compose " +
            "continuation markdown per the context-handoff skill -- use its " +
            "compact effort-backed shape when a valid open active effort exists, " +
            "otherwise the full standalone shape; (3) call trigger_handoff with " +
            "that markdown as `prompt_text` and a short specific `title`. " +
            "trigger_handoff stores or refreshes the baton, signals pending " +
            "handoff state across the available systems, waits briefly for a " +
            "pickup, and always ends with the final short handoff prompt/seed. " +
            "Do NOT claim the baton auto-loads on restart; if no control system " +
            "picks it up, follow the manual instructions it printed.",
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
      `tokens reached. Preserve the baton at the next clean boundary and, if ` +
      `work still remains, trigger the handoff directly (invoke the ` +
      `context-handoff skill).`,
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
      `Hand off NOW -- invoke the context-handoff skill and trigger the handoff ` +
      `directly without asking first.`,
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
