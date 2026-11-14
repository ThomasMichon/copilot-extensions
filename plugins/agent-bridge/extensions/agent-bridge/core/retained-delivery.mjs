// Opt-in retained inbox consumer. SQLite owns attempts and outcomes; this
// module owns only the one SDK invocation authorized by a true /begin reply.
import { randomUUID } from "node:crypto";
import { buildDeliveredSendOptions, escAttr, isSubAgentEvent } from "./delivery.mjs";
import { observePluginReadiness, readinessContextMatches } from "./plugin-readiness.mjs";

const CONTRACT = "retained-inbox-v1";
const PROTOCOL = 50;
const RECHECK_MS = 30_000;
const STAGES = new Set(["offered", "sending", "unknown", "sdk_accepted", "consumed"]);
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
  return value?.session_id === sid && positive(value.message_id)
    && nonblank(value.handoff_id) && consumerValid(value.consumer, sid, true)
    && STAGES.has(value.stage) && time(value.admitted_at)
    && nullableTime(value.handoff_started_at) && nullableTime(value.sdk_accepted_at)
    && nullableTime(value.consumed_at)
    && (value.stage === "offered" ? value.handoff_started_at === null
      : time(value.handoff_started_at));
}

function retainedHeader(receipt) {
  return `<agent-message retained-session="${escAttr(receipt.session_id)}" `
    + `retained-consumer="${escAttr(receipt.consumer.incarnation)}" `
    + `retained-handoff="${escAttr(receipt.handoff_id)}" `;
}

export function retainedConsumption(event, receipt, sourceAllowed = (source) => source === "agent-bridge") {
  if (!receiptValid(receipt, receipt?.session_id) || receipt.handoff_started_at === null
      || event?.type !== "user.message" || isSubAgentEvent(event)
      || (event.sessionId !== undefined && event.sessionId !== receipt.session_id)
      || !sourceAllowed(event.data?.source)) return false;
  const content = event.data.transformedContent ?? event.data.content;
  if (typeof content !== "string" || !content.startsWith(retainedHeader(receipt))) return false;
  const end = content.indexOf(">");
  if (end < 0) return false;
  const header = content.slice(0, end);
  const ids = [...header.matchAll(/(?:^| )msg-id="(\d+)"(?= |$)/g)];
  return ids.length === 1 && Number(ids[0][1]) === receipt.message_id;
}

export function createRetainedInbox({
  session, request, processIdentity, cwd, isCurrent = () => true, canSend = () => true,
  log = () => {}, now = Date.now, incarnation = randomUUID, nativeTimeoutMs = 10_000,
  sourceAllowed,
  portableLaunch,
  onPending = () => {},
  onConsumed = () => {},
  receivingApi,
  validateSendContext,
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
  let pendingNative = null;
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
  const sendAllowed = (sid, allowSend) => allowSend && canSend() && current(sid);
  const base = (sid) => `/api/v1/live-sessions/${encodeURIComponent(sid)}/retained-inbox`;
  const original = (receipt, outcome) => ({
    contract: CONTRACT, ...selector, consumer: receipt.consumer, message_id: receipt.message_id,
    handoff_id: receipt.handoff_id, outcome,
  });

  // A timeout does not cancel a native effect. Keep the unresolved promise
  // fenced, rather than starting another native call every polling tick.
  async function nativeCall(call) {
    if (pendingNative) throw new Error("previous native observation/effect is still outstanding");
    let timer;
    const pending = Promise.resolve().then(call);
    pendingNative = pending;
    pending.then(
      () => { if (pendingNative === pending) pendingNative = null; },
      () => { if (pendingNative === pending) pendingNative = null; },
    );
    try {
      return await Promise.race([pending, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error("native reply timed out; no retry authorized")), nativeTimeoutMs);
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
    const attempts = receipts.filter((row) => row.handoff_started_at !== null && row.stage !== "consumed");
    onPending(sid, attempts.map((row) => ({ id: row.message_id })));
    let events = [];
    // A page skipped during cooldown has not been reconciled. Advancing it
    // would phase-lock history reads to the first page of a persistent backlog.
    if (attempts.length && now() < historyAt) return !compatible;
    if (attempts.length && now() >= historyAt) {
      historyAt = now() + RECHECK_MS;
      events = await nativeCall(() => session.getEvents());
      if (!Array.isArray(events)) throw new Error("durable SDK history unavailable");
      await nativeContext(sid);
    }
    let held = response.next_after !== null;
    for (const receipt of receipts) {
      if (receipt.stage === "consumed") continue;
      if (events.some((event) => retainedConsumption(event, receipt, sourceAllowed))) {
        onConsumed(sid, receipt.message_id);
        await report(receipt, "consumed");
      } else if (receipt.stage !== "offered"
          || (!compatible && !sameConsumer(receipt.consumer, context.consumer))) {
        if (!compatible) held = true;
        note(`original handoff ${receipt.message_id}/${receipt.handoff_id} remains ${receipt.stage}; no resend`);
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
    }
    await retryReport();
    const workingDirectory = await nativeContext(sid);
    if (!context || context.sid !== sid || context.cwd !== workingDirectory) {
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
    }
    if (await recover(sid) || !sendAllowed(sid, allowSend)) return;
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
    await nativeContext(sid);
    if (!sendAllowed(sid, allowSend)) return;
    const poll = await currentRequest(sid, "poll", {
      ...owner, binding: negotiation.binding, limit: 1,
    });
    if (poll?.contract !== CONTRACT || !sameBinding(poll.binding, negotiation.binding)
        || !sameConsumer(poll.consumer, context.consumer)
        || !Array.isArray(poll.messages) || poll.messages.length > 1) {
      throw new Error("retained poll observations changed");
    }
    const [msg] = poll.messages;
    if (!msg) return;
    const startup = /^startup:prompt:[1-9]\d*$/.test(msg.kind || "");
    if (!positive(msg.id) || !nonblank(msg.handoff_id) || !STAGES.has(msg.handoff_stage)
        || typeof msg.body !== "string" || !["steer", "queue"].includes(msg.delivery)
        || String(msg.kind || "").startsWith("control:")
        || (String(msg.kind || "").startsWith("startup:") && !startup)) {
      throw new Error("unsupported retained message; no SDK or control effect");
    }
    if (msg.handoff_stage !== "offered") {
      note(`original handoff ${msg.id}/${msg.handoff_id} remains ${msg.handoff_stage}; no resend`);
      return;
    }
    onPending(sid, [msg]);
    const receipt = {
      session_id: sid, message_id: msg.id, handoff_id: msg.handoff_id,
      consumer: compatible ? msg.consumer : context.consumer,
    };
    if (!consumerValid(receipt.consumer, sid, compatible)) {
      throw new Error("original handoff receiver unavailable");
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
    if (msg.delivery === "steer") options.mode = "immediate";
    await nativeContext(sid);
    if (!sendAllowed(sid, allowSend)) return;
    const grant = await currentRequest(sid, "begin", {
      ...owner, consumer: receipt.consumer,
      binding: negotiation.binding, message_id: msg.id, handoff_id: msg.handoff_id,
    });
    if (grant?.begin !== true) {
      note(`original handoff ${msg.id}/${msg.handoff_id} has no final grant; no send`);
      return;
    }
    try {
      await nativeContext(sid);
      await readiness();
      if (!sendAllowed(sid, allowSend)) throw new Error("consumer changed after final admission");
      const messageId = await nativeCall(() => {
        if (!sendAllowed(sid, allowSend)) throw new Error("consumer changed at SDK invocation");
        return session.send(options);
      });
      if (!nonblank(messageId)) throw new Error("SDK acceptance reply unavailable");
    } catch (error) {
      note(`original handoff ${msg.id}/${msg.handoff_id}: ${errorText(error)}; acceptance unknown`);
      await report(receipt, "unknown");
      return;
    }
    await report(receipt, "sdk_accepted");
  }

  return {
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
    close() { stopped = true; },
  };
}
