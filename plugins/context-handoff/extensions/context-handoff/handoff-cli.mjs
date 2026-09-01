#!/usr/bin/env node
// handoff-cli.mjs -- invoke a context handoff DIRECTLY from the command line,
// without the context-handoff session extension.
//
// Why this exists: the handoff tools (generate_handoff_prompt /
// save_handoff_prompt / continue_handoff) are provided by the context-handoff
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
//   node handoff-cli.mjs cutover  --title "<t>" --prompt-file <f>   # store + live cutover (default)
//   node handoff-cli.mjs save     --title "<t>" --prompt-file <f>   # store + print seed + paste prompt (no cutover)
//   node handoff-cli.mjs continue --seed "<HANDOFF_SEED>"           # trigger the cutover for an existing seed
//   node handoff-cli.mjs consume  --handoff-id <id> | --path <f>    # load a file handoff, mark consumed, print brief
//   node handoff-cli.mjs help
//
// Options:
//   --prompt-file <f> | --prompt <text> | (piped stdin)  the handoff markdown
//   --title <t>            short topic (leads the seed / task title)
//   --session-id <sid>     default: $COPILOT_AGENT_SESSION_ID
//   --cwd <dir>            default: current directory
//   --no-task             force the file store (skip an agent-dispatch task)
//   --seed <s>            (continue) the exact HANDOFF_SEED to spawn a successor with
//   --handoff-id <id> | --path <f>   (consume) which file-backed handoff to load
//   --json                machine-readable output

import { readFileSync } from "node:fs";
import {
  storeHandoff, buildSeedForStored, runHandoffCutover,
  consumeFileHandoffOnce, decodeHandoffPayload,
} from "./handoff-core.mjs";

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const flags = new Set(["no-task", "json", "no-cutover"]);
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
  if (args.json) { process.stdout.write(JSON.stringify(obj, null, 2) + "\n"); return; }
  return obj;
}

const HELP = `handoff-cli -- invoke a context handoff from the CLI (extension-free fallback).

  node handoff-cli.mjs cutover  --title "<t>" --prompt-file <f>   store + live cutover (default)
  node handoff-cli.mjs save     --title "<t>" --prompt-file <f>   store + print seed + paste prompt
  node handoff-cli.mjs continue --seed "<HANDOFF_SEED>"           trigger the cutover for a seed
  node handoff-cli.mjs consume  --handoff-id <id> | --path <f>    load a file handoff + mark consumed

Options: --prompt-file|--prompt|stdin, --title, --session-id (\$COPILOT_AGENT_SESSION_ID),
         --cwd, --no-task, --seed, --handoff-id|--path, --json`;

function cmdStore(args, { cutover }) {
  const promptText = readPrompt(args);
  if (!promptText) {
    process.stderr.write("handoff-cli: no handoff text (use --prompt-file, --prompt, or pipe stdin)\n");
    process.exit(2);
  }
  const sid = resolveSid(args);
  if (!sid) {
    process.stderr.write("handoff-cli: no session id (pass --session-id or set COPILOT_AGENT_SESSION_ID)\n");
    process.exit(2);
  }
  const cwd = args.cwd || process.cwd();
  const stored = storeHandoff({
    promptText, sid, cwd, title: args.title || "",
    preferTask: !args["no-task"],
  });
  if (!stored?.storage) {
    process.stderr.write(
      "handoff-cli: could not store the handoff. " +
      `${stored?.error || "No safe machine-local state directory resolved."}\n`);
    process.exit(1);
  }
  const seed = buildSeedForStored(stored, { retry: true });
  const pastePrompt = buildSeedForStored(stored, { retry: false });
  const result = { ok: true, storage: stored.storage, id: stored.id, seed, pastePrompt };
  if (stored.path) result.path = stored.path;

  if (cutover && !args["no-cutover"]) {
    const cut = runHandoffCutover(cwd, seed, sid);
    result.cutover = cut;
    if (args.json) return emit(result, args);
    if (cut.ok) {
      const lifecycle = cut.host === "herdr"
        ? "the successor stops the identity-matched predecessor after consumption"
        : "the successor retires this pane after it consumes the handoff";
      process.stdout.write(
        `Handoff stored (${stored.storage}: ${stored.id}) and live cutover started ` +
        `(pane ${cut.new_pane || "?"}). End your turn now; ${lifecycle}.\n`);
    } else {
      process.stdout.write(
        `Handoff stored (${stored.storage}: ${stored.id}), but the live cutover could not run ` +
        `(reason: ${cut.reason}${cut.error ? " -- " + cut.error : ""}). ` +
        `Resume it in a new session with this paste prompt:\n\n${pastePrompt}\n`);
    }
    return;
  }

  if (args.json) return emit(result, args);
  process.stdout.write(
    `Handoff stored (${stored.storage}: ${stored.id}).\n\n` +
    `-- Paste prompt (for a new session) --\n${pastePrompt}\n\n` +
    `-- HANDOFF_SEED (for \`handoff-cli continue --seed\` under Herdr or mux) --\n${seed}\n`);
}

function cmdContinue(args) {
  const seed = args.seed;
  if (!seed) {
    process.stderr.write("handoff-cli continue: --seed <HANDOFF_SEED> required\n");
    process.exit(2);
  }
  const cwd = args.cwd || process.cwd();
  const sid = resolveSid(args);
  const cut = runHandoffCutover(cwd, seed, sid);
  if (args.json) return emit({ ok: cut.ok, cutover: cut }, args);
  if (cut.ok) {
    process.stdout.write(`Live cutover started (pane ${cut.new_pane || "?"}). End your turn now.\n`);
  } else {
    process.stderr.write(`Cutover could not run (reason: ${cut.reason}${cut.error ? " -- " + cut.error : ""}).\n`);
    process.exit(1);
  }
}

function cmdConsume(args) {
  const cwd = args.cwd || process.cwd();
  const sid = resolveSid(args);
  const consumed = consumeFileHandoffOnce(
    cwd, sid, args["handoff-id"], args.path || null,
  );
  if (!consumed.ok) {
    process.stderr.write(`handoff-cli consume: ${consumed.message}\n`);
    process.exit(1);
  }
  const brief = consumed.record.promptText
    || decodeHandoffPayload(consumed.record.payload || "").text
    || "";
  if (args.json) return emit({ ok: true, id: consumed.record.id, brief }, args);
  process.stdout.write(brief.endsWith("\n") ? brief : brief + "\n");
}

function main() {
  const argv = process.argv.slice(2);
  const args = parseArgs(argv);
  const cmd = args._[0] || "cutover";
  switch (cmd) {
    case "cutover": return cmdStore(args, { cutover: true });
    case "save":    return cmdStore(args, { cutover: false });
    case "continue": return cmdContinue(args);
    case "consume": return cmdConsume(args);
    case "help": case "-h": case "--help":
      process.stdout.write(HELP + "\n"); return;
    default:
      process.stderr.write(`handoff-cli: unknown command '${cmd}'\n\n${HELP}\n`);
      process.exit(2);
  }
}

main();
