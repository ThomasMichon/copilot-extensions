import { readFileSync, realpathSync } from "node:fs";
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
  try {
    const options = { cwd, timeout: 3000, allowProvision: false };
    const binding = JSON.parse(execute("agent-worktrees",
      ["effort-focus", "show", "--json"], options)).active_effort;
    if (!binding?.active || typeof binding.path !== "string") {
      return { status: "unbound", goal: null };
    }

    const home = realpathSync(execute("agent-worktrees", ["state-root"], options).trim());
    const path = realpathSync(resolve(home, binding.path));
    const inside = relative(home, path);
    if (isAbsolute(binding.path) || isAbsolute(inside) || inside === ".." || inside.startsWith(`..${sep}`)) {
      throw new Error("goal-outside-effort-home");
    }
    const bytes = readFileSync(path);
    if (bytes.length > 1048576) return { status: "oversized", path, goal: null,
      instruction: "Read this canonical effort in bounded ranges; no Goal text was truncated." };
    const text = bytes.toString("utf8").replace(/^\uFEFF/u, "");
    const goal = intentSection(text, "Goal");
    if (goal?.length > 12000) return { status: "goal-reference", path, goal: null,
      instruction: "Read the current Goal in bounded file ranges; no Goal text was truncated." };
    return { status: goal ? "goal" : "legacy-guiding-intent", path,
      participant: binding.participant, slice: binding.slice,
      goal: goal || intentSection(text, "Guiding Intent") };
  } catch {
    return { status: "unavailable", goal: null,
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
