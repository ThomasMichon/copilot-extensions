// Opt-in retained inbox consumer. SQLite owns attempts and outcomes; this
// module owns only the one SDK invocation authorized by a true /begin reply.
import { randomUUID } from "node:crypto";
import { Buffer } from "node:buffer";
import {
  DELIVERY_SOURCE, buildDeliveredSendOptions, deliveryPlan, escAttr, isSubAgentEvent,
} from "./delivery.mjs";
import { observePluginReadiness, readinessContextMatches } from "./plugin-readiness.mjs";

const CONTRACT = "retained-inbox-v1";
const PROTOCOL = 50;
const RECHECK_MS = 30_000;
const NATIVE_INPUT_LIMIT = 1_000;
const NATIVE_HEADER_LIMIT = 4_096;
// Bounded recent-first history: user.message pages only, a few per read.
const HISTORY_PAGE = 100;
const HISTORY_PAGES = 8;
const HISTORY_SLACK_S = 60;
const DEFERRED_LIMIT = 1_000;
// Cached history evidence is bounded; settled originals are pruned and never
// re-cached, and evicting evidence withdraws the coverage it supported.
const HISTORY_INPUT_LIMIT = 1_000;
const SETTLED_LIMIT = 1_000;
export const RESUME_NUDGE = "All queued agent-bridge messages were already delivered; resume.";
const STAGES = new Set(["offered", "sending", "unknown", "sdk_accepted", "consumed", "rejected"]);
const TERMINAL = new Set(["sdk_accepted", "consumed", "rejected"]);
const nonblank = (value) => typeof value === "string" && value.trim().length > 0;
const positive = (value) => Number.isSafeInteger(value) && value > 0;
const time = (value) => typeof value === "number" && Number.isFinite(value) && value > 0;
const nullableTime = (value) => value === null || time(value);
const errorText = (error) => error instanceof Error ? error.message : String(error);
const sameConsumer = (a, b) => a?.session_id === b?.session_id
  && a?.incarnation === b?.incarnation && (a?.pid ?? null) === (b?.pid ?? null)
  && (a?.process_started_at ?? null) === (b?.process_started_at ?? null)
  && (a?.portable_launch ?? null) === (b?.portable_launch ?? null);
const sameBinding = (a, b) => a?.installation === b?.installation && a?.epoch === b?.epoch;

export const retainedDeliverySelected = (value) => /^(1|true|on|yes)$/i.test(value || "");

function consumerValid(value, sid, compatible = false) {
  return value?.session_id === sid && nonblank(value.incarnation)
    && (compatible
      ? (value.pid == null || positive(value.pid))
        && (value.process_started_at == null || time(value.process_started_at))
      : positive(value.pid) && time(value.process_started_at))
    && (value.portable_launch === undefined
      || /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(value.portable_launch));
}

function receiptValid(value, sid) {
  return value != null && typeof value === "object"
    && value.session_id === sid && positive(value.message_id)
    && nonblank(value.handoff_id) && consumerValid(value.consumer, sid, true)
    && STAGES.has(value.stage) && time(value.admitted_at)
    && nullableTime(value.handoff_started_at) && nullableTime(value.sdk_accepted_at)
    && nullableTime(value.consumed_at)
    && (["offered", "rejected"].includes(value.stage) ? value.handoff_started_at === null
      : time(value.handoff_started_at));
}

function retainedHeader(receipt) {
  return `<agent-message retained-session="${escAttr(receipt.session_id)}" `
    + `retained-consumer="${escAttr(receipt.consumer.incarnation)}" `
    + `retained-handoff="${escAttr(receipt.handoff_id)}" `;
}

const RUNTIME_DATETIME_PREFIX = /^<current_datetime>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})<\/current_datetime>\r?\n\r?\n/;

function headerMessageId(header) {
  const ids = [...header.matchAll(/(?:^| )msg-id="(\d+)"(?= |$)/g)];
  const id = ids.length === 1 ? Number(ids[0][1]) : null;
  return positive(id) ? id : null;
}

export function retainedConsumptionReason(event, receipt, sourceAllowed = (source) => source === "agent-bridge") {
  if (!receiptValid(receipt, receipt?.session_id)) return "invalid_receipt";
  if (receipt.handoff_started_at === null) return "handoff_not_started";
  if (event?.type !== "user.message" || isSubAgentEvent(event)) return "not_root_input";
  if (event.sessionId !== undefined && event.sessionId !== receipt.session_id) return "session_mismatch";
  if (!sourceAllowed(event.data?.source)) return "source_mismatch";
  let content = event.data.transformedContent ?? event.data.content;
  if (typeof content !== "string") return "content_unavailable";
  const prefix = RUNTIME_DATETIME_PREFIX.exec(content);
  if (prefix) content = content.slice(prefix[0].length);
  if (!content.startsWith(retainedHeader(receipt))) return "header_mismatch";
  const end = content.indexOf(">");
  if (end < 0) return "header_unterminated";
  const header = content.slice(0, end);
  return headerMessageId(header) === receipt.message_id
    ? "matched" : "message_id_mismatch";
}

export function retainedConsumption(event, receipt, sourceAllowed) {
  return retainedConsumptionReason(event, receipt, sourceAllowed) === "matched";
}

// Reduce a root retained input to its header so buffered observations never
// retain large message bodies.
function compactRetainedInput(sid, event, acceptsSource) {
  if (event?.type !== "user.message" || isSubAgentEvent(event)
      || (event.sessionId !== undefined && event.sessionId !== sid)
      || !acceptsSource(event?.data?.source)) return null;
  const content = event?.data?.transformedContent ?? event?.data?.content;
  if (typeof content !== "string") return null;
  const start = RUNTIME_DATETIME_PREFIX.exec(content)?.[0].length ?? 0;
  if (!content.startsWith(`<agent-message retained-session="${escAttr(sid)}" `, start)) return null;
  const candidate = content.slice(start, start + NATIVE_HEADER_LIMIT + 1);
  const end = candidate.indexOf(">");
  if (end < 0 || end >= NATIVE_HEADER_LIMIT) return null;
  const id = headerMessageId(candidate.slice(0, end));
  if (id === null) return null;
  // Copy UTF-16 code units: V8 slices can retain the entire original body.
  const header = Buffer.from(candidate.slice(0, end + 1), "utf16le").toString("utf16le");
  return { id, header, event: {
    type: "user.message", sessionId: sid,
    data: { source: event.data.source, turnId: event.data.turnId, transformedContent: header },
  } };
}

const eventSeconds = (event) => {
  const parsed = typeof event?.timestamp === "string" ? Date.parse(event.timestamp) : NaN;
  return Number.isFinite(parsed) ? parsed / 1000 : null;
};

export function createRetainedInbox({
  session, request, processIdentity, cwd, isCurrent = () => true, canSend = () => true,
  log = () => {}, now = Date.now, incarnation = randomUUID, nativeTimeoutMs = 10_000,
  sourceAllowed,
  portableLaunch,
  onPending = () => {},
  onSubmitted = () => {},
  onConsumed = () => {},
  onObservation = () => {},
  receivingApi,
  validateSendContext,
  replayHistory,
  mainTurn,
}) {
  if (receivingApi !== undefined && receivingApi !== "native-sdk-v1") {
    throw new Error("unsupported native receiving API");
  }
  const compatible = receivingApi === "native-sdk-v1";
  const selector = compatible ? { receiving_api: receivingApi } : {};
  let busy = false;
  let stopped = false;
  let context = null;
  let negotiation = null;
  let enrolled = false;
  let frontendStale = false;
  let healthUntil = 0;
  let historyAt = 0;
  let after = 0;
  let retryAt = 0;
  let pendingReport = null;
  const pendingNative = { serial: null, history: null };
  const nativeInputs = new Map();
  let nativeInputOverflow = false;
  const acceptsSource = sourceAllowed ?? ((source) => source === "agent-bridge");
  // Managed mode: the live main-turn tracker tells us when the session is idle,
  // so every unsubmitted original can be (re)submitted then instead of lingering.
  const managed = compatible && mainTurn != null;
  const idleEpisode = () => (managed && !mainTurn.running ? mainTurn.episode : null);
  let reattemptSupported = false;
  let episode = null;
  let submittedEpisode = null;
  const verified = new Map();
  // History checks deferred by cooldown or a failed read, folded into the next
  // read without holding any recovery page. Bounded; recovery re-adds entries.
  const deferred = new Map();
  let sweepUnsettled = false;
  const fullHistoryRead = new Set();
  let historyCache = null;
  const settledIds = new Set();
  // A settled original needs no further history evidence: drop it and skip it
  // on later reads so long-lived sessions do not accumulate cached headers.
  const settleHistory = (id) => {
    settledIds.delete(id);
    settledIds.add(id);
    while (settledIds.size > SETTLED_LIMIT) settledIds.delete(settledIds.keys().next().value);
    if (!historyCache) return;
    for (const [key, input] of historyCache.inputs) {
      if (input.id === id) historyCache.inputs.delete(key);
    }
  };
  const consumed = (sid, id) => {
    if (episode && episode.id === idleEpisode() && episode.awaiting.has(id)) episode.redundant.add(id);
    verified.delete(id);
    deferred.delete(id);
    settleHistory(id);
    onConsumed(sid, id);
  };
  let lastLog = "";
  let lastLogAt = 0;
  const note = (message) => {
    if (message !== lastLog || now() - lastLogAt >= RECHECK_MS) {
      log(`retained inbox: ${message}`);
      lastLog = message;
      lastLogAt = now();
    }
  };
  const current = (sid) => !stopped && isCurrent(sid) && session?.sessionId === sid;
  const forgetNativeInput = (sid, id) => {
    for (const [key, input] of nativeInputs) {
      if (input.sid === sid && input.id === id) nativeInputs.delete(key);
    }
  };
  const sendAllowed = (sid, allowSend) => allowSend && canSend() && current(sid);
  const base = (sid) => `/api/v1/live-sessions/${encodeURIComponent(sid)}/retained-inbox`;
  const original = (receipt, outcome) => ({
    contract: CONTRACT, ...selector, consumer: receipt.consumer, message_id: receipt.message_id,
    handoff_id: receipt.handoff_id, outcome,
  });
  const observe = (stage, receipt, fields = {}) => {
    try {
      onObservation({
        stage, source: "retained-sdk-recovery", session_id: receipt.session_id,
        message_id: receipt.message_id, handoff_id: receipt.handoff_id,
        consumer_incarnation: receipt.consumer.incarnation, ...fields,
      });
    } catch { /* diagnostics cannot change delivery or receipt settlement */ }
  };

  // History is a read-only observation, not an input effect. Bound it separately
  // so an unresolved read cannot poison fresh context checks or granted input.
  // Each slot stays fenced until its original promise actually settles.
  async function nativeCall(call, { history = false } = {}) {
    const lane = history ? "history" : "serial";
    if (pendingNative[lane]) {
      throw new Error(history ? "previous native history observation is still outstanding"
        : "previous native observation/effect is still outstanding");
    }
    let timer;
    const pending = Promise.resolve().then(call);
    pendingNative[lane] = pending;
    pending.then(
      () => { if (pendingNative[lane] === pending) pendingNative[lane] = null; },
      () => { if (pendingNative[lane] === pending) pendingNative[lane] = null; },
    );
    try {
      return await Promise.race([pending, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(history
          ? "native history reply timed out; observation remains outstanding"
          : "native reply timed out; no retry authorized")), nativeTimeoutMs);
      })]);
    } finally {
      clearTimeout(timer);
    }
  }

  async function nativeContext(sid) {
    if (!current(sid) || typeof session.send !== "function"
        || typeof session.getEvents !== "function"
        || typeof session.rpc?.metadata?.snapshot !== "function") {
      throw new Error("actual SDK session target or required public APIs unavailable");
    }
    const workingDirectory = typeof cwd === "function" ? cwd() : cwd;
    const snapshot = await nativeCall(() => session.rpc.metadata.snapshot());
    if (!current(sid) || !readinessContextMatches(snapshot, {
      expected_session_id: sid, cwd: workingDirectory,
    })) throw new Error("native session/cwd changed; original work retained");
    return workingDirectory;
  }

  async function report(receipt, outcome) {
    pendingReport = { receipt, outcome };
    const result = await request("POST", `${base(receipt.session_id)}/outcome`, original(receipt, outcome));
    if (!STAGES.has(result?.stage)
        || !(result.stage === outcome || result.stage === "consumed"
          || (outcome === "unknown" && result.stage === "sdk_accepted"))) {
      throw new Error("original outcome reply is unavailable or inconsistent");
    }
    pendingReport = null;
    if (result.stage === "consumed") forgetNativeInput(receipt.session_id, receipt.message_id);
    return result.stage;
  }

  async function retryReport() {
    if (!pendingReport) return;
    const { receipt, outcome } = pendingReport;
    const retained = await request("GET", `${base(receipt.session_id)}/recovery/${receipt.message_id}`);
    if (!receiptValid(retained, receipt.session_id)
        || retained.handoff_id !== receipt.handoff_id
        || !sameConsumer(retained.consumer, receipt.consumer)) {
      throw new Error("original outcome recovery observations changed");
    }
    if (retained.stage === outcome || retained.stage === "consumed"
        || (outcome === "unknown" && retained.stage === "sdk_accepted")) {
      pendingReport = null;
      if (retained.stage === "consumed") forgetNativeInput(receipt.session_id, receipt.message_id);
      return;
    }
    await report(receipt, outcome);
  }

  async function currentRequest(sid, action, body) {
    try {
      return await request("POST", `${base(sid)}/${action}`, body);
    } catch (error) {
      if (error?.status === 409) frontendStale = true;
      throw error;
    }
  }

  // Bounded history: read recent root user.message pages newest-first, keep only
  // compact retained headers, and remember how far back the contiguous scan
  // reached so later checks only page through newer events. Only the newest
  // page's IDs are kept to detect overlap, so a deep backward scan keeps its
  // progress however many user messages precede an inherited handoff.
  // Evidence is capped: settled originals are pruned and skipped, the rows being
  // checked are pinned, and evicting another original's header withdraws only
  // that original's coverage until a fresh scan re-reads it, so a removed
  // match can never authorize a duplicate submission.
  async function readRecentHistory(sid, rows) {
    const log = session.rpc.eventLog;
    if (historyCache?.sid !== sid) {
      historyCache = {
        sid, head: new Set(), inputs: new Map(), cursor: null, reached: Infinity, done: false,
        evicted: new Map(), evictedAt: -Infinity,
      };
    }
    const cache = historyCache;
    const reset = () => {
      Object.assign(cache, { head: new Set(), cursor: null, reached: Infinity, done: false });
    };
    const pinned = new Set(rows.map((row) => row.message_id));
    const page = async (cursor) => {
      const result = await nativeCall(() => log.read({
        ...(cursor ? { cursor } : {}), direction: "backward", max: HISTORY_PAGE,
        types: ["user.message"], agentScope: "primary", includeEphemeral: false,
      }), { history: true });
      if (result?.cursorStatus !== "ok" || !Array.isArray(result.events)
          || typeof result.hasMore !== "boolean"
          || (result.hasMore && typeof result.cursor !== "string")) {
        throw new Error("bounded SDK history page unavailable or expired");
      }
      return result;
    };
    const evict = () => {
      let victim = null;
      for (const entry of cache.inputs) {
        const better = victim === null
          || (pinned.has(victim[1].id) && !pinned.has(entry[1].id))
          || (pinned.has(victim[1].id) === pinned.has(entry[1].id) && entry[1].at < victim[1].at);
        if (better) victim = entry;
      }
      const [key, { id, at }] = victim;
      cache.inputs.delete(key);
      cache.evicted.set(id, Math.max(cache.evicted.get(id) ?? -Infinity, at));
      while (cache.evicted.size > SETTLED_LIMIT) {
        // Too many withdrawn originals to track individually: withdraw coverage
        // for every row whose window reaches the oldest one's evidence.
        const [oldId, oldAt] = cache.evicted.entries().next().value;
        cache.evicted.delete(oldId);
        cache.evictedAt = Math.max(cache.evictedAt, oldAt);
      }
    };
    const remember = (event) => {
      const input = compactRetainedInput(sid, event, acceptsSource);
      if (!input || settledIds.has(input.id)) return;
      const key = typeof event?.id === "string" ? `e:${event.id}` : `h:${input.header}`;
      if (cache.inputs.has(key)) return;
      cache.inputs.set(key, { id: input.id, at: eventSeconds(event) ?? Infinity, event: input.event });
      while (cache.inputs.size > HISTORY_INPUT_LIMIT) evict();
    };
    let pages = 0;
    try {
      const fresh = cache.cursor === null && !cache.done;
      // A fresh scan re-reads everything it will claim to cover, re-recording
      // any eviction, so earlier withdrawals no longer apply.
      if (fresh) Object.assign(cache, { evicted: new Map(), evictedAt: -Infinity });
      let cursor = null;
      let reached = Infinity;
      let connected = false;
      let done = false;
      let head = null;
      while (pages < HISTORY_PAGES) {
        const result = await page(cursor);
        pages++;
        head ??= new Set(result.events.map((event) => event?.id).filter((id) => typeof id === "string"));
        for (const event of result.events) {
          if (typeof event?.id === "string" && cache.head.has(event.id)) { connected = true; continue; }
          remember(event);
          const at = eventSeconds(event);
          if (at !== null) reached = Math.min(reached, at);
        }
        cursor = result.cursor ?? null;
        if (!result.hasMore) { done = true; break; }
        if (connected && !fresh) break;
      }
      if (head?.size) cache.head = head;
      if (done) Object.assign(cache, { cursor: null, reached, done: true });
      else if (fresh || !connected) Object.assign(cache, { cursor, reached, done: false });
      const floor = Math.min(...rows.map((row) => row.handoff_started_at)) - HISTORY_SLACK_S;
      while (!cache.done && cache.reached > floor && pages < HISTORY_PAGES && cache.cursor) {
        const result = await page(cache.cursor);
        pages++;
        for (const event of result.events) {
          remember(event);
          const at = eventSeconds(event);
          if (at !== null) cache.reached = Math.min(cache.reached, at);
        }
        cache.cursor = result.cursor ?? null;
        if (!result.hasMore) cache.done = true;
      }
    } catch (error) {
      reset();
      throw error;
    }
    const events = [...cache.inputs.values()].map((input) => input.event);
    const scanned = (row) => cache.done || cache.reached <= row.handoff_started_at - HISTORY_SLACK_S;
    const withdrawn = (row) => cache.evicted.has(row.message_id)
      || row.handoff_started_at - HISTORY_SLACK_S <= cache.evictedAt;
    const covered = new Set(rows.filter((row) => scanned(row) && !withdrawn(row))
      .map((row) => row.message_id));
    // Evicted evidence these rows rely on is re-read by a fresh scan later,
    // when they are pinned.
    if (rows.some((row) => scanned(row) && withdrawn(row))) reset();
    return { events, covered };
  }

  // Without a bounded history API, read the full log at most once per started
  // original in this process. Later attempts made here are covered by live
  // input observation, which clears this memo if it ever overflows.
  async function readHistory(sid, rows) {
    if (typeof session.rpc?.eventLog?.read === "function") return readRecentHistory(sid, rows);
    const key = (row) => `${row.message_id}:${row.handoff_id}`;
    if (rows.every((row) => fullHistoryRead.has(key(row)))) {
      return { events: [], covered: new Set(rows.map((row) => row.message_id)) };
    }
    const events = await nativeCall(() => session.getEvents(), { history: true });
    if (!Array.isArray(events)) throw new Error("durable SDK history unavailable");
    for (const row of rows) fullHistoryRead.add(key(row));
    return { events, covered: new Set(rows.map((row) => row.message_id)) };
  }

  // Managed recovery never leaves a started original ambiguous: while idle it
  // proves each unsubmitted original absent from bounded history (or settles it
  // as consumed) so drain may submit it again through a recorded attempt.
  async function recoverManaged(sid, response, attempts) {
    const unsubmitted = attempts.filter((row) => row.stage !== "sdk_accepted");
    // Before negotiation the message base is unknown; keep the notice until it is.
    const canReattempt = reattemptSupported
      && (negotiation === null || context?.consumer?.message_base === 1);
    if (canReattempt) {
      onPending(sid, unsubmitted.map((row) => ({ id: row.message_id })));
    } else {
      // Without bridge re-attempt support these cannot be submitted again here.
      for (const row of unsubmitted) onSubmitted(sid, row.message_id);
    }
    const idleAt = idleEpisode();
    // Submitted originals still seek consumption evidence, as before, but a
    // full-log fallback never sweeps the same handoff twice.
    const bounded = typeof session.rpc?.eventLog?.read === "function";
    const accepted = attempts.filter((row) => row.stage === "sdk_accepted"
      && (bounded || !fullHistoryRead.has(`${row.message_id}:${row.handoff_id}`)));
    for (const row of [...(idleAt === null ? []
      : unsubmitted.filter((row) => verified.get(row.message_id) !== idleAt)), ...accepted]) {
      deferred.delete(row.message_id);
      deferred.set(row.message_id, row);
    }
    while (deferred.size > DEFERRED_LIMIT) deferred.delete(deferred.keys().next().value);
    // Unsubmitted originals are checked only while idle; accepted ones anytime.
    const candidates = [...deferred.values()].filter((row) => row.stage === "sdk_accepted"
      || (idleAt !== null && verified.get(row.message_id) !== idleAt));
    const blocking = candidates.some((row) => row.stage !== "sdk_accepted");
    // A deferred check never holds this page: later pages keep being discovered,
    // and the resume prompt waits for a sweep with nothing left unverified.
    if (candidates.length && now() < historyAt) {
      if (blocking) sweepUnsettled = true;
    } else if (candidates.length) {
      historyAt = now() + RECHECK_MS;
      let history = null;
      try {
        history = await readHistory(sid, candidates);
      } catch (error) {
        note(`durable SDK history unavailable: ${errorText(error)}; rechecking when idle`);
        if (blocking) sweepUnsettled = true;
      }
      if (history) {
        await nativeContext(sid);
        for (const receipt of candidates) {
          if (history.events.some((event) => retainedConsumption(event, receipt, sourceAllowed))) {
            consumed(sid, receipt.message_id);
            await report(receipt, "consumed");
          } else if (history.covered.has(receipt.message_id)) {
            deferred.delete(receipt.message_id);
            // Only unsubmitted originals become re-attempt eligible; submitted
            // ones keep seeking consumption without occupying the retry list.
            if (receipt.stage !== "sdk_accepted") verified.set(receipt.message_id, idleAt);
          } else {
            if (receipt.stage !== "sdk_accepted") sweepUnsettled = true;
            note(`original handoff ${receipt.message_id}/${receipt.handoff_id} beyond bounded history scan; rechecking`);
          }
        }
      }
    }
    // The resume nudge needs every page of this idle episode reconciled.
    if (!sweepUnsettled && response.next_after === null && episode?.id === idleAt) episode.swept = true;
    after = response.next_after ?? 0;
    return response.next_after !== null;
  }

  async function recover(sid) {
    const response = await request("GET", `${base(sid)}/recovery?limit=100&after=${after}`);
    if (!Array.isArray(response?.messages) || response.messages.length > 100
        || !(response.next_after === null
          || (positive(response.next_after) && response.next_after > after
            && response.next_after === response.messages.at(-1)?.message_id))
        || response.messages.some((row, index, rows) => !receiptValid(row, sid)
          || row.message_id <= after || (index > 0 && row.message_id <= rows[index - 1].message_id))) {
      throw new Error("malformed original handoff recovery response");
    }
    const receipts = response.messages;
    if (after === 0) sweepUnsettled = false;
    const idleAt = idleEpisode();
    if (idleAt !== null && idleAt > 0 && after === 0 && episode?.id !== idleAt) {
      // Snapshot what was queued when this idle episode began, for the nudge.
      episode = { id: idleAt, nudged: false, swept: false, redundant: new Set(), awaiting: new Set() };
    }
    if (episode?.id === idleAt && !episode.swept) {
      // Accumulate every recovery page; the nudge waits for the whole sweep.
      for (const row of receipts) if (!TERMINAL.has(row.stage)) episode.awaiting.add(row.message_id);
      if (!managed && response.next_after === null) episode.swept = true;
    }
    if (nativeInputOverflow) {
      note("native input observation overflow; original receipts remain uncertain");
      nativeInputOverflow = false;
      // Live observation covered in-process attempts; once lossy, a fallback
      // full read is needed again before any re-attempt.
      fullHistoryRead.clear();
    }
    const observed = new Set();
    const settleInput = async (receipt) => {
      if (TERMINAL.has(receipt.stage)) {
        verified.delete(receipt.message_id);
        deferred.delete(receipt.message_id);
      }
      if (receipt.stage === "consumed" || receipt.stage === "rejected") settleHistory(receipt.message_id);
      if (receipt.stage === "consumed" || receipt.stage === "rejected") {
        forgetNativeInput(sid, receipt.message_id);
      } else if ([...nativeInputs.values()].some((input) => input.sid === sid
          && input.id === receipt.message_id && retainedConsumption(input.event, receipt, sourceAllowed))) {
        await nativeContext(sid);
        consumed(sid, receipt.message_id);
        await report(receipt, "consumed");
        observed.add(receipt.message_id);
      } else if (receipt.handoff_started_at !== null) {
        // An offered snapshot may predate a concurrent begin and its live input.
        forgetNativeInput(sid, receipt.message_id);
      }
    };
    for (const receipt of receipts) await settleInput(receipt);
    const pageIds = new Set(receipts.map((receipt) => receipt.message_id));
    const liveIds = [...new Set([...nativeInputs.values()]
      .filter((input) => input.sid === sid && !pageIds.has(input.id)).map((input) => input.id))].slice(0, 100);
    for (const id of liveIds) {
      let receipt;
      try {
        receipt = await request("GET", `${base(sid)}/recovery/${id}`);
      } catch (error) {
        if (error?.status !== 404) throw error;
        forgetNativeInput(sid, id);
        continue;
      }
      if (!receiptValid(receipt, sid) || receipt.message_id !== id) {
        throw new Error("live input original receipt unavailable or inconsistent");
      }
      await settleInput(receipt);
    }
    const attempts = receipts.filter((row) => row.handoff_started_at !== null
      && row.stage !== "consumed" && !observed.has(row.message_id));
    for (const receipt of receipts) {
      if (receipt.stage === "sdk_accepted" || receipt.stage === "consumed") {
        onSubmitted(sid, receipt.message_id);
      }
    }
    if (managed) return recoverManaged(sid, response, attempts);
    onPending(sid, attempts.filter((row) => row.stage !== "sdk_accepted")
      .map((row) => ({ id: row.message_id })));
    let events = [];
    // A page skipped during cooldown has not been reconciled. Advancing it
    // would phase-lock history reads to the first page of a persistent backlog.
    if (attempts.length && now() < historyAt) return !compatible;
    if (attempts.length && now() >= historyAt) {
      historyAt = now() + RECHECK_MS;
      try {
        events = await nativeCall(() => session.getEvents(), { history: true });
      } catch (error) {
        if (!compatible) throw error;
        note(`durable SDK history unavailable: ${errorText(error)}; original receipts retained`);
        return false;
      }
      if (!Array.isArray(events)) throw new Error("durable SDK history unavailable");
      await nativeContext(sid);
    }
    let held = response.next_after !== null;
    for (const receipt of receipts) {
      if (receipt.stage === "consumed" || receipt.stage === "rejected"
          || observed.has(receipt.message_id)) continue;
      const failures = {};
      let matched = false;
      for (const event of events) {
        const reason = retainedConsumptionReason(event, receipt, sourceAllowed);
        if (reason === "matched") { matched = true; break; }
        failures[reason] = (failures[reason] || 0) + 1;
      }
      if (matched) {
        onConsumed(sid, receipt.message_id);
        await report(receipt, "consumed");
      } else {
        if (receipt.handoff_started_at !== null && attempts.length) {
          observe("match_failed", receipt, { event_count: events.length, failures });
        }
        if (receipt.stage !== "offered"
            || (!compatible && !sameConsumer(receipt.consumer, context.consumer))) {
          if (!compatible) held = true;
          note(`original handoff ${receipt.message_id}/${receipt.handoff_id} remains ${receipt.stage}; no resend`);
        }
      }
    }
    after = response.next_after ?? 0;
    return held;
  }

  async function drain(sid, allowSend) {
    if (now() >= healthUntil) {
      const health = await request("GET", "/health");
      if (!Number.isSafeInteger(health?.protocol_version) || health.protocol_version < PROTOCOL) {
        throw new Error(`protocol ${PROTOCOL} recovery support required; legacy downgrade refused`);
      }
      if (compatible && health?.capabilities?.compatible_native_inbox !== 1) {
        throw new Error("compatible native inbox support required; legacy downgrade refused");
      }
      if (portableLaunch !== undefined && (health?.capabilities?.portable_inbox !== 1
          || health?.capabilities?.readonly_plugin_controls !== 1)) {
        throw new Error("portable inbox and readonly plugin controls are required; legacy downgrade refused");
      }
      healthUntil = now() + RECHECK_MS;
      reattemptSupported = health?.capabilities?.retained_delivery_reattempt === 1;
    }
    await retryReport();
    const workingDirectory = await nativeContext(sid);
    if (!context || context.sid !== sid || context.cwd !== workingDirectory) {
      for (const [key, input] of nativeInputs) {
        if (input.sid !== sid) nativeInputs.delete(key);
      }
      const consumer = {
        session_id: sid, incarnation: incarnation(),
        ...(compatible ? {} : {
          pid: processIdentity?.pid, process_started_at: processIdentity?.process_started_at,
        }),
        ...(portableLaunch === undefined ? {} : { portable_launch: portableLaunch }),
      };
      if (!consumerValid(consumer, sid, compatible)) throw new Error("consumer observations unavailable");
      context = { sid, cwd: workingDirectory, consumer };
      negotiation = null;
      enrolled = false;
      frontendStale = false;
      after = 0;
      historyAt = 0;
      verified.clear();
      deferred.clear();
      sweepUnsettled = false;
      fullHistoryRead.clear();
      historyCache = null;
      settledIds.clear();
      episode = null;
      submittedEpisode = null;
    }
    if (!sendAllowed(sid, allowSend)) {
      await recover(sid);
      return;
    }
    const owner = { contract: CONTRACT, ...selector, consumer: context.consumer };
    if (!enrolled || frontendStale) {
      if (!negotiation) {
        const observed = await request("POST", `${base(sid)}/negotiate`, owner);
        if (observed?.contract !== CONTRACT || !nonblank(observed.binding?.installation)
            || !positive(observed.binding?.epoch)
            || !(observed.expected_incarnation === null || nonblank(observed.expected_incarnation))) {
          throw new Error("retained negotiation observations unavailable");
        }
        negotiation = {
          contract: CONTRACT,
          binding: { installation: observed.binding.installation, epoch: observed.binding.epoch },
          expected_incarnation: observed.expected_incarnation,
        };
        if (observed.message_base === 1) {
          context.consumer = { ...context.consumer, message_base: 1 };
          owner.consumer = context.consumer;
        }
      } else if (frontendStale) {
        const observed = await request("POST", `${base(sid)}/negotiate`, owner);
        if (observed?.contract !== CONTRACT || !sameBinding(observed.binding, negotiation.binding)) {
          throw new Error("frontend recovery changed the original binding or consumer; no reenrollment");
        }
        if (compatible && observed.expected_incarnation === null) {
          context.consumer = { ...context.consumer, incarnation: incarnation() };
          owner.consumer = context.consumer;
          negotiation = { ...negotiation, expected_incarnation: null };
        } else if (observed.expected_incarnation !== context.consumer.incarnation
            && observed.expected_incarnation !== negotiation.expected_incarnation) {
          throw new Error("frontend recovery changed the original binding or consumer; no reenrollment");
        }
      }
      await nativeContext(sid);
      if (!sendAllowed(sid, allowSend)) return;
      // Keep the exact CAS request if its reply is lost. Renegotiating would
      // confuse our already-enrolled incarnation with its original predecessor.
      const result = await currentRequest(sid, "enroll", { ...owner, ...negotiation });
      if (result?.contract !== CONTRACT || !sameBinding(result.binding, negotiation.binding)
          || result.expected_incarnation !== negotiation.expected_incarnation) {
        throw new Error("retained enrollment reply unavailable");
      }
      enrolled = true;
      frontendStale = false;
    }
    if (await recover(sid) || !sendAllowed(sid, allowSend)) return;
    await nativeContext(sid);
    if (!sendAllowed(sid, allowSend)) return;
    const idleAt = idleEpisode();
    // Re-attempts are granted only to a message-base consumer (new attempt identity).
    const canReattempt = reattemptSupported && context.consumer.message_base === 1;
    const reattempt = idleAt === null || !canReattempt ? []
      : [...verified].filter(([, at]) => at === idleAt).map(([id]) => id).slice(0, 100);
    const poll = await currentRequest(sid, "poll", {
      ...owner, binding: negotiation.binding, limit: 1,
      ...(reattempt.length ? { reattempt } : {}),
    });
    if (poll?.contract !== CONTRACT || !sameBinding(poll.binding, negotiation.binding)
        || !sameConsumer(poll.consumer, context.consumer)
        || !Array.isArray(poll.messages) || poll.messages.length > 1) {
      throw new Error("retained poll observations changed");
    }
    const [msg] = poll.messages;
    if (!msg) {
      await nudge(sid, allowSend);
      return;
    }
    if (context.consumer.message_base === 1) {
      if (!positive(msg.id)) throw new Error("message correlation unavailable; no effects");
      const disposition = (state, code, reason) => currentRequest(sid, "disposition", {
        ...owner, binding: negotiation.binding, message_id: msg.id, state, code, reason,
      });
      const header = msg.message_contract;
      const startupKind = /^startup:prompt:[1-9]\d*$/.test(msg.kind || "");
      const supported = new Set(["prompt", "notify", "status-check", "reply", "control:replay-history"]);
      let rejection = null;
      if (!header || header.message_id !== msg.id || header.session_id !== sid
          || header.receiver_incarnation !== context.consumer.incarnation
          || header.sender !== msg.sender || header.type !== msg.kind || header.contract_error) {
        rejection = ["invalid_contract", "The correlated message contract is malformed."];
      } else if (header.base_version !== 1) {
        rejection = ["unsupported_base_version", "This receiver does not support the message base version."];
      } else if ((!supported.has(header.type) && !startupKind) || header.type_version !== 1) {
        rejection = ["unsupported_message_version", "This receiver does not support the message type or revision."];
      } else if (!Array.isArray(header.requires) || header.requires.length !== 0) {
        rejection = ["unsupported_required_semantics", "This receiver cannot enforce the required message semantics."];
      } else if (typeof msg.body !== "string" || !["steer", "queue"].includes(msg.delivery)) {
        rejection = ["unsupported_payload", "This receiver cannot interpret the message payload or delivery mode."];
      }
      if (header?.type === "control:replay-history" && (msg.body !== "" || !replayHistory)) {
        rejection = ["unsupported_history_replay", "Bounded history reconstruction is unavailable or has an unsupported payload."];
      }
      if (rejection) {
        await disposition("rejected", ...rejection);
        return;
      }
      if (header.type === "control:replay-history") {
        await nativeContext(sid);
        if (!sendAllowed(sid, allowSend)) return;
        const grant = await disposition("started", "history_replay_started", "Bounded represented-history reconstruction admitted.");
        if (grant?.begin !== true) return;
        let completed = false;
        try {
          await nativeContext(sid);
          if (!sendAllowed(sid, allowSend)) throw new Error("receiver changed before history reconstruction");
          completed = await nativeCall(() => replayHistory(sid)) === true;
        } catch (error) {
          note(`history reconstruction outcome unknown: ${errorText(error)}`);
        }
        await disposition(
          completed ? "completed" : "unknown",
          completed ? "history_replay_completed" : "history_replay_unknown",
          completed ? "Bounded represented history reconstructed; no prompt or tool was rerun."
            : "History reconstruction may have started; no automatic replay is authorized.",
        );
        return;
      }
    }
    const startup = /^startup:prompt:[1-9]\d*$/.test(msg.kind || "");
    if (!positive(msg.id) || !nonblank(msg.handoff_id) || !STAGES.has(msg.handoff_stage)
        || typeof msg.body !== "string" || !["steer", "queue"].includes(msg.delivery)
        || String(msg.kind || "").startsWith("control:")
        || (String(msg.kind || "").startsWith("startup:") && !startup)) {
      throw new Error("unsupported retained message; no SDK or control effect");
    }
    // A fresh offer joins an idle episode that began with queued originals, so
    // a failed send keeps it unresolved and blocks the resume prompt.
    if (episode && episode.id === idleAt && episode.awaiting.size > 0) episode.awaiting.add(msg.id);
    const reattempting = msg.handoff_stage !== "offered";
    if (reattempting && !(managed && canReattempt && idleAt !== null
        && verified.get(msg.id) === idleAt && positive(msg.attempt) && msg.consumer)) {
      note(`original handoff ${msg.id}/${msg.handoff_id} remains ${msg.handoff_stage}; awaiting idle verification`);
      return;
    }
    onPending(sid, [msg]);
    const receipt = {
      session_id: sid, message_id: msg.id, handoff_id: msg.handoff_id,
      consumer: msg.consumer ?? context.consumer,
    };
    if (!consumerValid(receipt.consumer, sid, compatible)) {
      throw new Error("original handoff receiver unavailable");
    }
    const alreadyInput = () => [...nativeInputs.values()].some((input) => input.sid === sid
      && input.id === msg.id && input.event.data.transformedContent.startsWith(retainedHeader(receipt)));
    const settleRedundant = async () => {
      // Already present in this session's own input: delivered, never resent.
      verified.delete(msg.id);
      await nativeContext(sid);
      consumed(sid, msg.id);
      await report(receipt, "consumed");
    };
    if (reattempting && alreadyInput()) {
      await settleRedundant();
      return;
    }
    const readiness = async () => {
      if (validateSendContext) await nativeCall(validateSendContext);
      if (!startup) return;
      if (!msg.plugin_readiness || msg.delivery !== "queue"
          || msg.plugin_readiness.expected_session_id !== sid) {
        throw new Error("original startup readiness context is unavailable");
      }
      const observed = await nativeCall(() => observePluginReadiness(session, msg.plugin_readiness, {
        isCurrent: () => current(sid), waitForReady: false,
      }));
      if (observed.status !== "ready") throw new Error("original startup plugins are not ready");
    };
    await readiness();
    const options = buildDeliveredSendOptions(startup ? { ...msg, kind: "prompt" } : msg);
    options.prompt = options.prompt.replace(/^<agent-message /, retainedHeader(receipt));
    await nativeContext(sid);
    if (!sendAllowed(sid, allowSend)) return;
    // Input or a new turn may have arrived while readiness was awaited.
    if (reattempting && alreadyInput()) {
      await settleRedundant();
      return;
    }
    if (reattempting && idleEpisode() !== idleAt) {
      note(`original handoff ${msg.id}/${msg.handoff_id} re-attempt deferred; session left idle`);
      return;
    }
    const grant = await currentRequest(sid, "begin", {
      ...owner, consumer: context.consumer.message_base === 1 ? context.consumer : receipt.consumer,
      binding: negotiation.binding, message_id: msg.id, handoff_id: msg.handoff_id,
      ...(reattempting ? { attempt: msg.attempt } : {}),
    });
    if (reattempting) verified.delete(msg.id);
    if (grant?.begin !== true) {
      if (reattempting) {
        // The bridge either raced another attempt or terminally failed the
        // original once its attempts were exhausted; recovery re-reports it.
        onSubmitted(sid, msg.id);
        note(`original handoff ${msg.id}/${msg.handoff_id} attempt ${msg.attempt + 1} not granted`);
        return;
      }
      note(`original handoff ${msg.id}/${msg.handoff_id} has no final grant; no send`);
      return;
    }
    let redundant = false;
    try {
      await nativeContext(sid);
      await readiness();
      if (!sendAllowed(sid, allowSend)) throw new Error("consumer changed after final admission");
      await nativeCall(async () => {
        if (!sendAllowed(sid, allowSend)) throw new Error("consumer changed at SDK invocation");
        if (alreadyInput()) {
          redundant = true;
          return;
        }
        if (reattempting && idleEpisode() !== idleAt) throw new Error("session left idle before re-attempt");
        const mode = deliveryPlan(msg, {
          mainTurnRunning: mainTurn ? mainTurn.running : true,
        }).options.mode;
        if (mode !== undefined) options.mode = mode;
        const messageId = await session.send(options);
        if (!nonblank(messageId)) throw new Error("SDK acceptance reply unavailable");
        if (idleAt !== null) submittedEpisode = idleAt;
        // A late reply still settles submission attention, not the original receipt.
        onSubmitted(sid, msg.id);
      });
    } catch (error) {
      note(`original handoff ${msg.id}/${msg.handoff_id}: ${errorText(error)}; acceptance unknown`);
      await report(receipt, "unknown");
      return;
    }
    if (redundant) {
      await settleRedundant();
      return;
    }
    await report(receipt, "sdk_accepted");
  }

  // One plain resume prompt, only when this idle episode began with queued
  // originals and every one of them proved already delivered (none submitted).
  async function nudge(sid, allowSend) {
    const idleAt = idleEpisode();
    // Legacy bridges keep observation-only behavior: never start a turn there.
    if (!reattemptSupported || context?.consumer?.message_base !== 1) return;
    if (!episode || episode.nudged || !episode.swept || episode.id !== idleAt || idleAt === 0
        || submittedEpisode === idleAt || episode.awaiting.size === 0
        || [...episode.awaiting].some((id) => !episode.redundant.has(id))) return;
    episode.nudged = true;
    await nativeContext(sid);
    if (!sendAllowed(sid, allowSend) || mainTurn.running) return;
    try {
      await nativeCall(() => session.send({ prompt: RESUME_NUDGE, source: DELIVERY_SOURCE, mode: "enqueue" }));
    } catch (error) {
      note(`resume prompt not confirmed: ${errorText(error)}; not repeated`);
    }
  }

  return {
    // Diagnostic: cached history headers held for unsettled originals.
    historyEvidenceSize() {
      return historyCache?.inputs.size ?? 0;
    },
    observe(sid, event) {
      if (!current(sid) || !nonblank(event?.data?.turnId)) return;
      const input = compactRetainedInput(sid, event, acceptsSource);
      if (!input) return;
      const key = `${sid}:${input.id}:${input.header}`;
      if (!nativeInputs.has(key) && nativeInputs.size >= NATIVE_INPUT_LIMIT) {
        nativeInputs.delete(nativeInputs.keys().next().value);
        nativeInputOverflow = true;
      }
      nativeInputs.set(key, { sid, id: input.id, event: input.event });
    },
    consumerIncarnation(sid) {
      return current(sid) && context?.sid === sid ? context.consumer.incarnation : null;
    },
    async tick(sid, { allowSend = true } = {}) {
      if (stopped || busy || !nonblank(sid) || now() < retryAt) return;
      busy = true;
      try {
        await drain(sid, allowSend);
        retryAt = 0;
      } catch (error) {
        note(`${errorText(error)}; original work retained`);
        retryAt = now() + RECHECK_MS;
      } finally {
        busy = false;
      }
    },
    close() { stopped = true; nativeInputs.clear(); },
  };
}
