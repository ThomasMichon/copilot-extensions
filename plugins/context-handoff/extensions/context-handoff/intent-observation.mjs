import { IntentCorpus, classifyIntent, INTENT_RESEARCH_DIRECTIVE } from "./intent-corpus.mjs";

export function registerIntentObservation(session, state, {
  createCorpus = (cwd) => new IntentCorpus(cwd),
  readGoal = async () => ({ status: "unbound", goal: null }),
  allowAutomaticReplay = async () => true,
  resolveCwd = (cwd) => cwd,
} = {}) {
  const corpora = new Map();
  const pending = new Map();
  const cached = new Map();
  const unsubscribers = [];
  let disposed = false;
  let delivery = Promise.resolve();
  let latest = null;
  let failures = 0;
  let startupPending = true;
  let scopedSessionId = session.sessionId || state.sessionId || null;
  function resolveScope() {
    const sid = session.sessionId || state.sessionId;
    if (!sid || sid === scopedSessionId) return;
    const previousCwd = state.cwd || process.cwd();
    const cwd = resolveCwd(previousCwd, sid);
    state.cwd = cwd;
    state.sessionId ||= sid;
    scopedSessionId = sid;
    for (const item of pending.values()) {
      if (!item.input.sessionId && item.cwd === previousCwd) item.cwd = cwd;
    }
  }
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
    resolveScope();
    for (const [key, item] of pending) {
      if (item.blocked) continue;
      const { cwd, input } = item;
      if (!input.sessionId) input.sessionId = session.sessionId || state.sessionId || null;
      try { corpus(cwd).capture(input); pending.delete(key); }
      catch (error) {
        if (error.message === "intent-event-identity-conflict") item.blocked = true;
        failures++; warn();
      }
    }
  }
  function capture(event) {
    if (disposed) return;
    resolveScope();
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
  async function facts(cwd) {
    retry();
    cwd ||= state.cwd || process.cwd();
    let goal;
    try {
      goal = session.sessionId || state.sessionId
        ? await readGoal(cwd)
        : { status: "unqualified", goal: null, reason: "session identity is not yet available" };
    }
    catch { goal = { status: "unavailable", goal: null }; }
    try {
      const snapshot = corpus(cwd).snapshot();
      const page = snapshot ? corpus(cwd).page(snapshot.id) : null;
      const data = { status: pending.size ? "incomplete" : "available", goal, corpus: snapshot, page,
        pendingInputs: pending.size, failures, directive: INTENT_RESEARCH_DIRECTIVE };
      cached.set(cwd, data);
      return data;
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
    resolveScope();
    if (!session.sessionId && !state.sessionId) { startupPending = true; return delivery; }
    startupPending = false;
    const cwd = state.cwd || process.cwd();
    delivery = delivery.catch(() => {}).then(async () => {
      if (disposed) return;
      const scopeChanged = () => {
        if ((state.cwd || process.cwd()) === cwd) return false;
        startupPending = true;
        return true;
      };
      if (scopeChanged()) return;
      if (!await allowAutomaticReplay()) { startupPending = true; return; }
      if (disposed || scopeChanged()) return;
      const data = await facts(cwd);
      if (disposed || scopeChanged()) return;
      const unavailableReference = data.goal?.status === "unavailable" && data.goal?.ref;
      if (disposed || (!data.corpus?.count && !data.goal?.goal && !data.pendingInputs
          && data.status !== "unavailable" && !data.goal?.path && !unavailableReference)) return;
      const firstPage = data.page;
      const prompt = [
        `<intent-continuity reason="${reason}">`,
        INTENT_RESEARCH_DIRECTIVE,
        JSON.stringify(data),
        firstPage?.next
          ? "Continue with read_intent_corpus using this snapshot and next cursor until next=null; " +
            "history is not complete at this page."
          : "This snapshot has no further pages. Verify the current Goal source before action.",
        "</intent-continuity>",
      ].join("\n\n");
      await session.send({ prompt, source: "system", mode: "immediate",
        displayPrompt: "Canonical Goal and durable intent history" });
      if (disposed || scopeChanged()) return;
      latest = { reason, ...data };
    }).catch(() => { startupPending = true; failures++; warn(); });
    return delivery;
  }
  for (const [event, handler] of [
    ["user.message", capture],
    ["session.context_cleared", (event) => { if (!event.agentId) void replay("context-cleared"); }],
    ["session.compaction_complete", (event) => {
      if (!event.agentId && event.data?.success) void replay("compaction-complete");
    }],
    ["session.idle", () => { if (!disposed && pending.size) retry(); }],
    ["tool.execution_complete", (event) => {
      if (!disposed && !event.agentId && pending.size) retry();
    }],
    ["tool.execution_start", (event) => {
      if (!event.agentId && startupPending) void replay("startup-or-reconnect");
    }],
  ]) {
    const unsubscribe = session.on(event, handler);
    if (typeof unsubscribe === "function") unsubscribers.push(unsubscribe);
  }
  const observation = {
    facts,
    resolveScope,
    emergencyFacts(cwd = state.cwd || process.cwd()) {
      const data = cached.get(cwd);
      return {
        status: "cached-unqualified",
        goal: { status: "unqualified", goal: null,
          ref: data?.goal?.ref ?? null, path: data?.goal?.path ?? null },
        corpus: data?.corpus ?? null, page: data?.page ?? null,
        corpusPath: corpora.get(cwd)?.home ?? null,
        pendingInputs: pending.size, failures, directive: INTENT_RESEARCH_DIRECTIVE,
        instruction: "Emergency cached pointers only; current Goal and corpus freshness are unqualified. " +
          "Read the bound effort and retained corpus before action; cached pages may omit later inputs.",
      };
    },
    replay,
    page(snapshot, cursor) {
      resolveScope();
      return corpus(state.cwd || process.cwd()).page(snapshot, cursor);
    },
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
