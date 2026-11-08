import { IntentCorpus, classifyIntent, INTENT_RESEARCH_DIRECTIVE } from "./intent-corpus.mjs";

export function registerIntentObservation(session, state, {
  createCorpus = (cwd) => new IntentCorpus(cwd),
  readGoal = async () => ({ status: "unbound", goal: null }),
  allowAutomaticReplay = async () => true,
} = {}) {
  const corpora = new Map();
  const pending = new Map();
  const unsubscribers = [];
  let disposed = false;
  let delivery = Promise.resolve();
  let latest = null;
  let failures = 0;
  let startupPending = true;
  function corpus(cwd) {
    if (!corpora.has(cwd)) corpora.set(cwd, createCorpus(cwd));
    return corpora.get(cwd);
  }
  function warn() {
    try {
      session.log("[Context Handoff] Intent continuity unavailable; retained inputs will be retried " +
        "at the next lifecycle boundary. Do not assume prompt history was preserved.", { level: "warning" });
    } catch { /* a disconnected log sink must not discard pending capture */ }
  }
  function retry() {
    for (const [key, item] of pending) {
      if (item.blocked) continue;
      const { cwd, input } = item;
      try { corpus(cwd).capture(input); pending.delete(key); }
      catch (error) {
        if (error.message === "intent-event-identity-conflict") item.blocked = true;
        failures++; warn();
      }
    }
  }
  function capture(event) {
    if (disposed) return;
    const input = classifyIntent(event, session.sessionId || state.sessionId);
    if (!input) return;
    const cwd = state.cwd || process.cwd();
    const key = JSON.stringify([cwd, input.sessionId, input.eventId]);
    if (pending.has(key) && JSON.stringify(pending.get(key).input) !== JSON.stringify(input)) {
      failures++;
      warn();
      return;
    }
    pending.set(key, { cwd, input });
    retry();
    if (startupPending) void replay("startup-or-reconnect");
  }
  async function facts(cwd = state.cwd || process.cwd()) {
    retry();
    let goal;
    try { goal = await readGoal(cwd); }
    catch { goal = { status: "unavailable", goal: null }; }
    try {
      const snapshot = corpus(cwd).snapshot();
      const page = snapshot ? corpus(cwd).page(snapshot.id) : null;
      return { status: pending.size ? "incomplete" : "available", goal, corpus: snapshot, page,
        pendingInputs: pending.size, failures, directive: INTENT_RESEARCH_DIRECTIVE };
    } catch {
      failures++;
      warn();
      return { status: "unavailable", goal, corpus: null, page: null,
        pendingInputs: pending.size, failures, directive: INTENT_RESEARCH_DIRECTIVE,
        instruction: "Intent storage is unavailable; continue the handoff without claiming history was read." };
    }
  }
  function replay(reason) {
    if (disposed) return delivery;
    startupPending = false;
    const cwd = state.cwd || process.cwd();
    delivery = delivery.catch(() => {}).then(async () => {
      if (disposed) return;
      if (!await allowAutomaticReplay()) return;
      const data = await facts(cwd);
      if (disposed || (!data.corpus?.count && !data.goal?.goal && !data.pendingInputs
          && data.status !== "unavailable" && !data.goal?.path)) return;
      const firstPage = data.page;
      const prompt = [
        `<intent-continuity reason="${reason}">`,
        INTENT_RESEARCH_DIRECTIVE,
        JSON.stringify(data, null, 2),
        firstPage?.next
          ? "Continue with read_intent_corpus using this snapshot and next cursor until next=null; " +
            "history is not complete at this page."
          : "This snapshot has no further pages. Verify the current Goal source before action.",
        "</intent-continuity>",
      ].join("\n\n");
      await session.send({ prompt, source: "system", mode: "immediate",
        displayPrompt: "Canonical Goal and durable intent history" });
      latest = { reason, ...data };
    }).catch(() => { failures++; warn(); });
    return delivery;
  }
  for (const [event, handler] of [
    ["user.message", capture],
    ["session.context_cleared", (event) => { if (!event.agentId) void replay("context-cleared"); }],
    ["session.compaction_complete", (event) => {
      if (!event.agentId && event.data?.success) void replay("compaction-complete");
    }],
    ["session.idle", () => { if (!disposed && pending.size) retry(); }],
    ["tool.execution_start", (event) => {
      if (!event.agentId && startupPending) void replay("startup-or-reconnect");
    }],
  ]) {
    const unsubscribe = session.on(event, handler);
    if (typeof unsubscribe === "function") unsubscribers.push(unsubscribe);
  }
  const observation = {
    facts,
    replay,
    page(snapshot, cursor) { return corpus(state.cwd || process.cwd()).page(snapshot, cursor); },
    get latest() { return latest; },
    get pendingInputs() { return pending.size; },
    async settle() { await delivery; },
    async dispose() {
      disposed = true;
      for (const unsubscribe of unsubscribers) unsubscribe();
      await delivery;
    },
  };
  return observation;
}
