import { existsSync, readFileSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { makeBridgeEndpoint } from "./core/bridge-endpoint.mjs";
import { consumedMessageId, drainInbox, InFlightMessages, transcriptTail } from "./core/delivery.mjs";
import { processIdentity } from "./core/metadata.mjs";
import { loadExtensionSdk } from "./core/sdk-compat.mjs";

const directory = process.env.BRIDGE_ACK_FIXTURE_DIRECTORY;
if (!directory) throw Error("Missing private native ACK fixture directory");
const config = JSON.parse(readFileSync(join(directory, "consumer.json"), "utf8"));
if (!/^http:\/\/127\.0\.0\.1:\d+$/.test(config.base)) {
  throw Error("Native ACK fixture requires its own loopback Bridge");
}
const startup = { startup_phase: "loading-sdk", error: null };
let startupRevision = 0;
const publishStartup = () => {
  const file = join(directory, `startup-${String(++startupRevision).padStart(6, "0")}.json`);
  writeFileSync(file + ".tmp", JSON.stringify(startup));
  renameSync(file + ".tmp", file);
};
const initialize = async () => {
  try {
    publishStartup();
    const { joinSession } = await loadExtensionSdk();
    startup.startup_phase = "joining-session";
    publishStartup();
    const session = await joinSession();
    startup.startup_phase = "reading-metadata";
    publishStartup();
    const context = await session.rpc.metadata.snapshot();
    if (context.sessionId !== config.sessionId) throw Error("Native fixture session changed");
    startup.startup_phase = "ready";
    publishStartup();
    return { session, context };
  } catch (error) {
    startup.error = String(error).slice(0, 1_024);
    publishStartup();
    throw error;
  }
};
const { session, context } = await initialize();
const inFlight = new InFlightMessages();
const identity = processIdentity();
const state = {
  session_id: context.sessionId,
  native_pid: process.ppid,
  extension_pid: identity.pid,
  extension_started_at: identity.process_started_at,
  polls: 0, sends: 0, accepted: 0, acknowledgements: 0,
  acknowledged_ids: [], native_event: null, released_event_id: null, error: null, finished: false,
};
let revision = 0;
const publish = () => {
  const bytes = JSON.stringify(state);
  if (Buffer.byteLength(bytes) > 32_768) throw Error("Native fixture evidence bound exceeded");
  if (++revision > 1_000) throw Error("Native fixture observation bound exceeded");
  const name = `status-${String(revision).padStart(6, "0")}.json`;
  writeFileSync(join(directory, name + ".tmp"), bytes);
  renameSync(join(directory, name + ".tmp"), join(directory, name));
};
const endpoint = makeBridgeEndpoint({
  resolveBase: () => config.base, resolveToken: () => config.token, fetchImpl: fetch,
  timeoutMs: 2_000,
});
const request = async (method, path, body) => {
  const response = await endpoint.call(method, path, body);
  if (!response.ok) throw Error(`Native ACK fixture HTTP ${response.status}`);
  return response.json();
};
session.on((event) => {
  if (consumedMessageId(event) === null) return;
  if (state.native_event) {
    if (state.native_event.id !== event.id) {
      state.error = "Original native input produced a second event identity";
      publish();
    }
    return;
  }
  state.native_event = event;
  publish();
});
const dependencies = {
  inFlight,
  getJson: (path) => request("GET", path),
  post: async (method, path, body) => {
    await request(method, path, body);
    if (path.endsWith("/messages/ack")) {
      state.acknowledgements += 1;
      state.acknowledged_ids.push(...body.ids);
      publish();
    }
    return true;
  },
  session: {
    rpc: session.rpc,
    abort: () => { throw Error("Native ACK fixture must not abort"); },
    send: async (options) => {
      state.sends += 1;
      publish();
      const result = await session.send(options);
      state.accepted += 1;
      publish();
      return result;
    },
  },
};
let running = false;
const poll = async () => {
  if (running || state.error || state.finished || !existsSync(join(directory, "start"))) return;
  running = true;
  try {
    if (state.native_event && !state.released_event_id
        && existsSync(join(directory, "release"))) {
      await request("POST", `/api/v1/live-sessions/${encodeURIComponent(config.sessionId)}/events`, {
        events: transcriptTail(JSON.stringify(state.native_event), new Set(["user.message"]), 1),
      });
      inFlight.observe(state.native_event);
      state.released_event_id = state.native_event.id;
    }
    await drainInbox(config.sessionId, dependencies);
    state.polls += 1;
    state.finished = state.acknowledgements === 1;
    publish();
  } catch (error) {
    state.error = String(error).slice(0, 1_024);
    publish();
  } finally {
    running = false;
  }
};
publish();
const timer = setInterval(() => { void poll(); }, 100);
timer.unref();
const expiry = setTimeout(() => {
  clearInterval(timer);
  if (!state.acknowledgements) {
    state.error ??= "Native ACK fixture observation deadline expired";
    publish();
  }
}, 60_000);
expiry.unref();
