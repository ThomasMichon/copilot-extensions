import { closeSync, openSync, readSync, realpathSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { IntentCorpus, INTENT_RESEARCH_DIRECTIVE } from "./intent-corpus.mjs";

export function resolveIntentCwd(cwd, sessionId, execute) {
  if (!sessionId) return cwd;
  try {
    const bound = execute("agent-worktrees", ["get", "worktree-dir", "--session-id", sessionId],
      { cwd, timeout: 3000, allowProvision: false }).trim();
    return bound ? realpathSync(bound) : cwd;
  } catch {
    return cwd;
  }
}

export function intentSection(text, heading) {
  const lines = [];
  let active = false;
  let fence = null;
  for (const line of text.replace(/\r\n/gu, "\n").split("\n")) {
    const marker = /^\s{0,3}(`{3,}|~{3,})/u.exec(line);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length) fence = null;
    }
    if (!fence && !marker) {
      if (active && /^-\s+\*\*(?:Slug|Repo|Status|Created|Branch\(es\)):\*\*/u.test(line)) break;
      const title = /^##\s+(.+?)\s*#*\s*$/u.exec(line);
      if (title) {
        if (active) break;
        active = title[1].toLowerCase() === heading.toLowerCase();
        continue;
      }
    }
    if (active) lines.push(line);
  }
  return lines.join("\n").trim() || null;
}

export function readCanonicalGoal(cwd, execute) {
  let reference = {};
  let candidate = null;
  try {
    const options = { cwd, timeout: 3000, allowProvision: false };
    const binding = JSON.parse(execute("agent-worktrees",
      ["effort-focus", "show", "--json"], options)).active_effort;
    if (!binding || typeof binding.path !== "string") {
      return { status: "unbound", goal: null };
    }
    reference = { ref: binding.path, participant: binding.participant, slice: binding.slice,
      bindingState: binding.state, bindingReason: binding.reason };
    if (!binding.active && binding.state !== "stale") {
      return { ...reference, status: binding.state || "closed", goal: null };
    }
    const root = JSON.parse(execute("agent-worktrees", ["state-root", "--json"], options));
    let home;
    if (root.requires_external === true) {
      const pair = JSON.parse(execute("agent-worktrees", ["state-root", "--pair", "--json"], options));
      if (!pair.paired || pair.error || !pair.sibling?.path) throw new Error("effort-pair-unavailable");
      home = realpathSync(pair.sibling.path);
    } else {
      if (root.requires_external !== false || !root.bound || !root.state_root) {
        throw new Error("effort-root-unavailable");
      }
      home = realpathSync(root.state_root);
    }
    candidate = resolve(home, binding.path);
    const lexical = relative(home, candidate);
    if (isAbsolute(binding.path) || isAbsolute(lexical) || lexical === ".." || lexical.startsWith(`..${sep}`)) {
      candidate = null;
      throw new Error("goal-outside-effort-home");
    }
    if (binding.state === "stale") return { ...reference, status: "unavailable", path: candidate,
      goal: null, instruction: "The bound effort is stale; inspect its original reference and reconcile current state." };
    const path = realpathSync(candidate);
    const inside = relative(home, path);
    if (isAbsolute(binding.path) || isAbsolute(inside) || inside === ".." || inside.startsWith(`..${sep}`)) {
      throw new Error("goal-outside-effort-home");
    }
    const buffer = Buffer.alloc(1048577);
    const fd = openSync(path, "r");
    let length = 0;
    try {
      while (length < buffer.length) {
        const count = readSync(fd, buffer, length, buffer.length - length, length);
        if (!count) break;
        length += count;
      }
    }
    finally { closeSync(fd); }
    if (length > 1048576) return { ...reference, status: "oversized", path, goal: null,
      instruction: "Read this canonical effort in bounded ranges; no Goal text was truncated." };
    const text = buffer.subarray(0, length).toString("utf8").replace(/^\uFEFF/u, "");
    const goal = intentSection(text, "Goal");
    const selected = goal || intentSection(text, "Guiding Intent");
    const status = goal ? "goal" : "legacy-guiding-intent";
    if (selected && Buffer.byteLength(JSON.stringify(selected), "utf8") > 12000) {
      return { ...reference, status: `${status}-reference`, path, goal: null,
        instruction: "Read the selected current or legacy intent in bounded file ranges; no text was truncated." };
    }
    return { ...reference, status, path, goal: selected };
  } catch {
    return { ...reference, status: "unavailable", path: candidate, goal: null,
      instruction: "Resolve the current canonical effort with planning-efforts; do not infer its Goal." };
  }
}

export function collectLocalIntentFacts(cwd, execute) {
  const goal = readCanonicalGoal(cwd, execute);
  try {
    const store = new IntentCorpus(cwd);
    const corpus = store.snapshot();
    return { goal, corpus, page: corpus ? store.page(corpus.id) : null,
      directive: INTENT_RESEARCH_DIRECTIVE };
  } catch {
    return { goal, corpus: null, status: "unavailable",
      directive: INTENT_RESEARCH_DIRECTIVE,
      instruction: "Intent storage is unavailable; do not assume the corpus was preserved or read." };
  }
}
