import { test } from "node:test";
import assert from "node:assert/strict";

import { makeBridgeEndpoint } from "../extensions/agent-bridge/bridge-endpoint.mjs";

function setup({ bases, tokens = ["tok"], responses }) {
  const seen = [];
  let b = 0;
  let t = 0;
  const ep = makeBridgeEndpoint({
    resolveBase: () => bases[Math.min(b++, bases.length - 1)],
    resolveToken: () => tokens[Math.min(t++, tokens.length - 1)],
    fetchImpl: async (url, opts) => {
      seen.push({ url, auth: opts.headers.Authorization });
      const next = responses.shift();
      if (next instanceof Error) throw next;
      return { status: next, ok: next < 300 };
    },
    log: () => {},
    timeoutMs: 1000,
  });
  return { ep, seen };
}

test("a moved port is picked up from active.json and the call retried once", async () => {
  const { ep, seen } = setup({
    bases: ["http://127.0.0.1:57577", "http://127.0.0.1:58008"],
    responses: [new TypeError("fetch failed"), 200],
  });
  const res = await ep.call("POST", "/api/v1/live-sessions", { session_id: "s" });
  assert.equal(res.status, 200);
  assert.deepEqual(seen.map((s) => s.url), [
    "http://127.0.0.1:57577/api/v1/live-sessions",
    "http://127.0.0.1:58008/api/v1/live-sessions",
  ]);
  assert.equal(ep.ep.base, "http://127.0.0.1:58008");
});

test("an unchanged endpoint is not retried: the failure surfaces as before", async () => {
  const { ep, seen } = setup({
    bases: ["http://127.0.0.1:1"],
    responses: [new TypeError("fetch failed")],
  });
  await assert.rejects(ep.call("GET", "/x"));
  assert.equal(seen.length, 1);
});

test("a rotated token (401) is re-read and the call retried with it", async () => {
  const { ep, seen } = setup({
    bases: ["http://127.0.0.1:1"],
    tokens: ["old", "new"],
    responses: [401, 200],
  });
  const res = await ep.call("GET", "/x");
  assert.equal(res.status, 200);
  assert.deepEqual(seen.map((s) => s.auth), ["Bearer old", "Bearer new"]);
});

test("ordinary answers from the right bridge are returned without re-resolving", async () => {
  const { ep, seen } = setup({ bases: ["http://127.0.0.1:1", "http://127.0.0.1:2"], responses: [409] });
  const res = await ep.call("POST", "/messages", {});
  assert.equal(res.status, 409);
  assert.equal(seen.length, 1);
  assert.equal(ep.ep.base, "http://127.0.0.1:1");
});
