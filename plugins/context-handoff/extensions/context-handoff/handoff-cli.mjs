#!/usr/bin/env node
// handoff-cli.mjs -- invoke a context handoff DIRECTLY from the command line,
// without the context-handoff session extension.
//
// Why this exists: the handoff tools (generate_handoff_prompt /
// save_handoff_prompt / trigger_handoff) are provided by the context-handoff
// EXTENSION. When that extension does not resolve or fails to load -- most
// notably a Bare-resumed session, where NO extensions load and
// `extensions list` shows nothing -- an agent has no in-session way to hand off.
// This CLI is the fallback: a plain `node handoff-cli.mjs ...` an agent can run
// via its shell tool. It reuses the SDK-free `handoff-core.mjs`, so a handoff it
// stores is byte-compatible with the extension's consume / `/resume-handoff`.
//
// The agent composes the handoff markdown itself (the extension's in-memory
// session state -- token counts, per-turn file edits -- is unavailable
// out-of-band, so the rich auto-collected facts are the agent's to write) and
// passes it in via --prompt-file / --prompt / stdin.
//
// Usage:
//   node handoff-cli.mjs trigger --title "<t>" --prompt-file <f>  # store + signal pickup
//   node handoff-cli.mjs save    --title "<t>" --prompt-file <f>  # store only
//   node handoff-cli.mjs consume --locator "task:<id>"            # consume a task baton
//   node handoff-cli.mjs consume --locator "file:<id>"            # consume a file baton
//   node handoff-cli.mjs facts --json                             # basic extension-free handoff facts
//   node handoff-cli.mjs help
//
// Options:
//   --prompt-file <f> | --prompt <text> | (piped stdin)  the handoff markdown
//   --title <t>            short topic (leads the seed / task title)
//   --session-id <sid>     default: $COPILOT_AGENT_SESSION_ID
//   --cwd <dir>            default: current directory
//   --no-task              force the file store (skip an agent-dispatch task)
//   --handoff-token <id>   reuse an already stored baton when triggering
//   --locator "task:<id>"|"file:<id>" | --task-id <id> | --handoff-id <id> | --path <f>
//                          stored handoff to consume
//   --defer-complete       retain an agent-dispatch task until the handoff goal completes
//   --json                 machine-readable output

import { readFileSync } from "node:fs";
import { parseRecoveryLocator } from "./cutover-seed.mjs";
import {
  storeHandoff, buildSeedForStored,
  consumeFileHandoff, consumeDispatchHandoffTask,
  collectCliHandoffFacts, formatConsumeResult,
  normalizeHandoffTitle,
  triggerHandoff,
} from "./handoff-core.mjs";

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const flags = new Set([
        "no-task", "json", "defer-complete",
      ]);
      if (flags.has(key)) { out[key] = true; continue; }
      out[key] = argv[++i];
    } else {
      out._.push(a);
    }
  }
  return out;
}

function readPrompt(args) {
  if (args["prompt-file"]) return readFileSync(args["prompt-file"], "utf-8");
  if (args.prompt != null) return String(args.prompt);
  // Piped stdin (fd 0). Empty when nothing is piped.
  try {
    const s = readFileSync(0, "utf-8");
    if (s && s.trim()) return s;
  } catch { /* no stdin */ }
  return null;
}

function resolveSid(args) {
  return args["session-id"] || process.env.COPILOT_AGENT_SESSION_ID || null;
}

function emit(obj, args) {
  if (args.json) {
    process.stdout.write(JSON.stringify(obj, null, 2) + "\n");
    return;
  }
  return obj;
}

const HELP = `handoff-cli -- invoke a context handoff from the CLI (extension-free fallback).

  node handoff-cli.mjs trigger --title "<t>" --prompt-file <f>  store + signal pickup
  node handoff-cli.mjs save    --title "<t>" --prompt-file <f>  store + print seed
  node handoff-cli.mjs consume --locator "task:<id>"            consume a task baton
  node handoff-cli.mjs consume --locator "file:<id>"            consume a file baton
  node handoff-cli.mjs facts --json                             emit basic extension-free facts

Options: --prompt-file|--prompt|stdin, --title, --session-id ($COPILOT_AGENT_SESSION_ID),
         --cwd, --no-task, --handoff-token,
         --locator|--task-id|--handoff-id|--path, --defer-complete, --json`;

function requireSid(command, args) {
  const sid = resolveSid(args);
  if (sid) return sid;
  process.stderr.write(
    `handoff-cli ${command}: --session-id or COPILOT_AGENT_SESSION_ID is required\n`,
  );
  process.exit(2);
}

function cmdSave(args) {
  const promptText = readPrompt(args);
  if (!promptText) {
    process.stderr.write("handoff-cli save: no handoff text (use --prompt-file, --prompt, or pipe stdin)\n");
    process.exit(2);
  }
  const sid = requireSid("save", args);
  const cwd = args.cwd || process.cwd();
  const stored = storeHandoff({
    promptText,
    sid,
    cwd,
    title: normalizeHandoffTitle(args.title),
    preferTask: !args["no-task"],
  });
  if (!stored?.storage) {
    process.stderr.write(
      "handoff-cli save: could not store the handoff. " +
      `${stored?.error || "No safe machine-local state directory resolved."}\n`,
    );
    process.exit(1);
  }
  const seed = buildSeedForStored(stored);
  const result = {
    ok: true,
    storage: stored.storage,
    id: stored.id,
    seed,
    handoffToken: stored.id,
  };
  if (stored.path) result.path = stored.path;
  if (args.json) return emit(result, args);
  process.stdout.write(
    `Handoff stored (${stored.storage}: ${stored.id}).\n\n` +
    "This preserves the baton without requesting pickup yet. Later, either " +
    "trigger it explicitly or continue manually by opening the successor " +
    "session and running `/consume-handoff` (or the payload-local CLI with the " +
    "recovery locator embedded in the seed).\n\n" +
    `HANDOFF_SEED: ${seed}\n` +
    `HANDOFF_TOKEN: ${stored.id}\n`,
  );
}

async function cmdTrigger(args) {
  const sid = requireSid("trigger", args);
  const cwd = args.cwd || process.cwd();
  const promptText = readPrompt(args);
  if (!promptText && !args["handoff-token"]) {
    process.stderr.write(
      "handoff-cli trigger: pass --prompt-file/--prompt/stdin or --handoff-token <id>\n",
    );
    process.exit(2);
  }
  const result = await triggerHandoff({
    promptText,
    sid,
    cwd,
    title: normalizeHandoffTitle(args.title),
    preferTask: !args["no-task"],
    handoffToken: args["handoff-token"] || null,
  });
  if (!result.ok) {
    process.stderr.write(
      `handoff-cli trigger: ${result.error || result.reason || "failed"}\n`,
    );
    process.exit(1);
  }
  if (args.json) return emit(result, args);

  const pickup = result.pickup || { via: [], waitedMs: 0 };
  const header = pickup.pickedUp
    ? `Handoff request signaled (${result.stored.storage}: ${result.stored.id}); pickup acknowledged via ${pickup.via.join(", ")} after ${(pickup.waitedMs / 1000).toFixed(1)}s.`
    : `Handoff request signaled (${result.stored.storage}: ${result.stored.id}); no pickup signal arrived within ${(pickup.waitedMs / 1000).toFixed(1)}s.`;
  process.stdout.write(
    `${header}\n\n` +
    (
      result.manualInstructions
        ? `${result.manualInstructions}\n\n`
        : "A control system appears to have picked the request up. Keep the same seed available in case manual continuation is still needed.\n\n"
    ) +
    "Final short handoff prompt/seed:\n\n" +
    "```text\n" +
    `${result.seed}\n` +
    "```\n",
  );
}

function cmdConsume(args) {
  const cwd = args.cwd || process.cwd();
  const sid = requireSid("consume", args);
  let taskId = args["task-id"];
  let handoffId = args["handoff-id"];
  let deferComplete = Boolean(args["defer-complete"]);
  if (args.locator) {
    let parsed;
    try {
      parsed = parseRecoveryLocator(args.locator);
    } catch (error) {
      process.stderr.write(`handoff-cli consume: ${error.message}\n`);
      process.exit(2);
    }
    if (taskId || handoffId || args.path) {
      process.stderr.write(
        "handoff-cli consume: --locator cannot be combined with --task-id, " +
        "--handoff-id, or --path\n",
      );
      process.exit(2);
    }
    if (parsed.kind === "task") {
      taskId = parsed.id;
      deferComplete = true;
    } else {
      handoffId = parsed.id;
    }
  }
  const targetCount = [taskId, handoffId, args.path].filter(Boolean).length;
  if (targetCount !== 1) {
    process.stderr.write(
      "handoff-cli consume: exactly one of --locator, --task-id, " +
      "--handoff-id, or --path is required\n",
    );
    process.exit(2);
  }
  if (deferComplete && !taskId) {
    process.stderr.write(
      "handoff-cli consume: --defer-complete is only valid with a task target\n",
    );
    process.exit(2);
  }
  const consumed = taskId
    ? consumeDispatchHandoffTask(cwd, taskId, sid, deferComplete)
    : consumeFileHandoff(cwd, sid, handoffId, args.path || null);
  if (!consumed.ok) {
    process.stderr.write(`handoff-cli consume: ${consumed.message}\n`);
    process.exit(1);
  }
  if (args.json) return emit(consumed, args);
  process.stdout.write(
    formatConsumeResult(consumed, {
      deferComplete,
    }) + "\n",
  );
}

function cmdFacts(args) {
  const cwd = args.cwd || process.cwd();
  const result = collectCliHandoffFacts(cwd, resolveSid(args));
  if (args.json) return emit(result, args);
  process.stdout.write(JSON.stringify(result, null, 2) + "\n");
}

async function main() {
  const argv = process.argv.slice(2);
  const args = parseArgs(argv);
  const cmd = args._[0] || "trigger";
  switch (cmd) {
    case "trigger": return await cmdTrigger(args);
    case "save": return cmdSave(args);
    case "consume": return cmdConsume(args);
    case "facts": return cmdFacts(args);
    case "help":
    case "-h":
    case "--help":
      process.stdout.write(HELP + "\n");
      return;
    default:
      process.stderr.write(`handoff-cli: unknown command '${cmd}'\n\n${HELP}\n`);
      process.exit(2);
  }
}

await main();
