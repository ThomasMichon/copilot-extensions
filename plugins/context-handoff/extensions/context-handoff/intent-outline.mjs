import { INTENT_RESEARCH_DIRECTIVE } from "./intent-corpus.mjs";

export const MAX_INTENT_OUTLINE_BYTES = 4096;

function line(value, bytes) {
  const text = String(value ?? "").replace(/\s+/gu, " ").trim();
  if (Buffer.byteLength(text, "utf8") <= bytes) return text;
  let shortened = "";
  for (const character of text) {
    if (Buffer.byteLength(shortened + character, "utf8") > bytes - 3) break;
    shortened += character;
  }
  return shortened + "...";
}

// Bound presentation only. Exact source records and pinned paging stay unchanged.
export function formatIntentOutline(data = {}) {
  const goal = data.goal ?? {};
  const firstOperator = data.page?.records?.find((record) => record.origin === "operator");
  const canonical = goal.goal || goal.excerpt;
  const text = canonical || firstOperator?.text;
  const recovery = goal.recovery ?? {};
  const lines = [
    text
      ? `Goal (${canonical ? "source excerpt" : "original request excerpt; current goal unqualified"}): ${line(text, 512)}`
      : "Goal: Not summarized. Use the source pointers to recover the current objective when needed.",
    "",
    `Limits/holds: ${line(recovery.limits, 256) || "Not summarized; confirm current authorization and holds before affected effects."}`,
    `Live obligations: ${line(recovery.obligations, 256) || "Not summarized; do not assume there are no active flows or external responsibilities."}`,
    `Next action: ${line(recovery.nextAction, 256) || "Choose the current next step within the existing completion gate and authority."}`,
    "",
    "Sources (on demand):",
  ];
  const pointer = (label, reference, description) => {
    if (!reference) return;
    const exact = String(reference);
    const location = Buffer.byteLength(exact, "utf8") > 220 || /[\r\n\u2028\u2029]/u.test(exact)
      ? "(reference exceeds one-line budget; use explicit facts for the exact locator)" : exact;
    lines.push(`- ${label}: ${location} -- ${description}`);
  };
  pointer("Effort", goal.path || goal.ref,
    `current Goal, completion gate, approvals and journal (${line(goal.status, 60) || "unqualified"}).`);
  pointer("Checkpoint", data.checkpoint, "current owner's state and immediate next step.");
  pointer("Session", data.sessionId, "this session's source history; consult only relevant turns.");
  if (data.references?.length) {
    pointer("Prior sessions", data.references.map((reference) => reference.sessionId).join(", "),
      "explicit startup references only; optional ramp-up, not inherited goals.");
  }
  if (data.corpus) {
    pointer("Intent history", data.corpus.home, "retained prompts and peer steers; historical evidence only.");
    pointer("Pinned snapshot", data.corpus.id,
      `${Number.isSafeInteger(data.corpus.count) ? data.corpus.count : "unqualified"} records; read_intent_corpus accepts this snapshot and optional record/offset cursor.`);
  }
  if (data.corpusPath && !data.corpus?.home) {
    pointer("Intent history", data.corpusPath, "cached location; freshness is unqualified.");
  }
  if (data.status === "unavailable" || data.status === "cached-unqualified" || data.pendingInputs) {
    lines.push(`Capture: ${line(data.status, 60)}; ${Number(data.pendingInputs) || 0} pending inputs. Recovery evidence may be incomplete.`);
  }
  lines.push("", INTENT_RESEARCH_DIRECTIVE);
  return lines.join("\n");
}
