// agent-remote-driver -- the local HTTP driver surface.
//
// A tiny, dependency-free HTTP server any external process can attach to for
// baseline drivability: GET /events (SSE), POST /send, POST /steer,
// POST /abort, GET /health. Bound to 127.0.0.1 only; every request must carry
// the bearer token recorded in this session's discovery descriptor
// (see discovery.mjs).
//
// Deliberately decoupled from @github/copilot-sdk: this module takes a plain
// `driver` object (see createDriverServer's JSDoc below) rather than an SDK
// session, so it can be unit tested with a fake driver and no SDK/runtime
// dependency at all. extension.mjs supplies the real SDK-backed driver.
//
// NOTE on driver exclusivity: this server accepts /send, /steer, and /abort
// from ANY holder of the bearer token with no arbitration between multiple
// concurrent callers. That is Phase 2's job (mux-native-driver-exclusivity,
// see efforts/active/cli-default-bridging/README.md) -- not a gap introduced
// here, but one this module does not attempt to close on its own.

import { createServer } from "node:http";
import { authorizes } from "./discovery.mjs";

const MAX_BODY_BYTES = 1_000_000; // 1MB cap on a request body; avoids unbounded buffering

function sendJson(res, status, body) {
  const data = JSON.stringify(body);
  res.writeHead(status, {
    "content-type": "application/json",
    "content-length": Buffer.byteLength(data),
  });
  res.end(data);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error("request body too large"));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf-8")));
    req.on("error", reject);
  });
}

async function readJsonBody(req) {
  const raw = await readBody(req);
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    throw new Error("request body must be valid JSON");
  }
}

/**
 * @param {object} opts
 * @param {() => string|null} opts.getSessionId -- current session id, if known
 * @param {() => number} opts.getPid
 * @param {string} opts.token -- the bearer token this server requires
 * @param {(opts: {content: string, mode?: string}) => Promise<unknown>} opts.send
 * @param {() => Promise<unknown>} opts.abort
 * @param {(listener: (event: unknown) => void) => () => void} opts.subscribe --
 *   registers a listener for forwarded events; returns an unsubscribe fn.
 *   Callers (extension.mjs) are responsible for invoking listeners OFF the
 *   CLI's own event loop (see the hot-potato discipline note in
 *   extension.mjs) -- this module just wires whatever it's given to SSE.
 * @returns {{server: import("node:http").Server, listen: () => Promise<number>, close: () => Promise<void>}}
 */
export function createDriverServer(opts) {
  const { getSessionId, getPid, token, send, abort, subscribe } = opts;
  const sseClients = new Set();

  function unauthorized(res) {
    sendJson(res, 401, { ok: false, error: "unauthorized" });
  }

  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, "http://127.0.0.1");
      if (!authorizes(req.headers.authorization, token)) {
        unauthorized(res);
        return;
      }

      if (req.method === "GET" && url.pathname === "/health") {
        sendJson(res, 200, { ok: true, sessionId: getSessionId(), pid: getPid() });
        return;
      }

      if (req.method === "GET" && url.pathname === "/events") {
        res.writeHead(200, {
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
          connection: "keep-alive",
        });
        res.write(": connected\n\n");
        const listener = (event) => {
          try {
            res.write(`data: ${JSON.stringify(event)}\n\n`);
          } catch {
            /* connection likely closed; cleanup below will catch up */
          }
        };
        const unsubscribe = subscribe(listener);
        sseClients.add(res);
        req.on("close", () => {
          sseClients.delete(res);
          unsubscribe();
        });
        return;
      }

      if (req.method === "POST" && url.pathname === "/send") {
        const body = await readJsonBody(req);
        if (!body || typeof body.content !== "string" || !body.content) {
          sendJson(res, 400, { ok: false, error: "content (string) is required" });
          return;
        }
        const result = await send({ content: body.content, mode: body.mode });
        sendJson(res, 200, { ok: true, result: result ?? null });
        return;
      }

      if (req.method === "POST" && url.pathname === "/steer") {
        const body = await readJsonBody(req);
        if (!body || typeof body.content !== "string" || !body.content) {
          sendJson(res, 400, { ok: false, error: "content (string) is required" });
          return;
        }
        await abort();
        const result = await send({ content: body.content, mode: "immediate" });
        sendJson(res, 200, { ok: true, result: result ?? null });
        return;
      }

      if (req.method === "POST" && url.pathname === "/abort") {
        const result = await abort();
        sendJson(res, 200, { ok: true, result: result ?? null });
        return;
      }

      sendJson(res, 404, { ok: false, error: "not found" });
    } catch (e) {
      sendJson(res, 500, { ok: false, error: e?.message || "internal error" });
    }
  });

  return {
    server,
    listen() {
      return new Promise((resolve, reject) => {
        server.once("error", reject);
        // Port 0 -> OS-assigned ephemeral port; loopback-only bind.
        server.listen(0, "127.0.0.1", () => {
          server.removeListener("error", reject);
          resolve(server.address().port);
        });
      });
    },
    close() {
      for (const res of sseClients) {
        try {
          res.end();
        } catch {
          /* ignore */
        }
      }
      sseClients.clear();
      return new Promise((resolve) => server.close(() => resolve()));
    },
  };
}
