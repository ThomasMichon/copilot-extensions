#!/usr/bin/env node
// agent-remote-driver -- list-sessions: the one aggregated fleet view.
//
// Scans discoveryDir(), reaps anything stale (see registry.mjs), then
// optionally confirms each survivor with a real GET /health (bounded
// per-request timeout, bounded total concurrency) rather than trusting the
// descriptor file alone -- a descriptor can be live-per-heartbeat yet still
// have a wedged/unresponsive HTTP server. This is deliberately the ONE
// place that does this work: a fleet controller driving N sessions should
// call this instead of re-implementing its own directory scan +
// per-session probe loop, which is exactly the kind of duplicated-probe
// traffic that turns into a connection storm against a large fleet.
//
// Usage:
//   node bin/list-sessions.mjs [--no-probe] [--json]
//
// Exit code is 0 for a normal run (including a genuinely empty fleet);
// failures to probe an individual session are reported per-entry, not as a
// process exit failure. Exit code is 1 only if the discovery directory
// itself cannot be read (a real operational failure, e.g. EACCES/EIO) --
// deliberately distinct from "zero live sessions found," which is a
// normal, successful result.

import { discoveryDir } from "../extensions/agent-remote-driver/discovery.mjs";
import { listLive } from "../extensions/agent-remote-driver/registry.mjs";

const PROBE_TIMEOUT_MS = 2_000;
const MAX_CONCURRENT_PROBES = 8; // bounded fan-out -- never hit N sessions at once

async function probeHealth(descriptor) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const res = await fetch(`http://${descriptor.host}:${descriptor.port}/health`, {
      headers: { authorization: `Bearer ${descriptor.token}` },
      signal: controller.signal,
    });
    if (!res.ok) return { reachable: false, error: `HTTP ${res.status}` };
    const body = await res.json();
    return { reachable: true, health: body };
  } catch (e) {
    return { reachable: false, error: e?.message || String(e) };
  } finally {
    clearTimeout(timer);
  }
}

// Bounded-concurrency map -- never more than `limit` in-flight probes
// regardless of fleet size.
async function mapBounded(items, limit, fn) {
  const results = new Array(items.length);
  let next = 0;
  async function worker() {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

async function main() {
  const args = process.argv.slice(2);
  const probe = !args.includes("--no-probe");
  const asJson = args.includes("--json");

  const dir = discoveryDir();
  let sessions;
  try {
    sessions = listLive(dir);
  } catch (e) {
    process.stderr.write(`agent-remote-driver: could not read the session registry at ${dir}: ${e.message}\n`);
    process.exitCode = 1;
    return;
  }

  let rows = sessions;
  if (probe && sessions.length > 0) {
    const healths = await mapBounded(sessions, MAX_CONCURRENT_PROBES, probeHealth);
    rows = sessions.map((descriptor, i) => ({ ...descriptor, ...healths[i] }));
  }
  // Never print the bearer token: this is a reporting tool, not a credential
  // export -- a consumer that needs to drive a session reads its own
  // descriptor file directly.
  const redacted = rows.map(({ token, ...rest }) => rest);

  if (asJson) {
    process.stdout.write(`${JSON.stringify(redacted, null, 2)}\n`);
    return;
  }
  if (redacted.length === 0) {
    process.stdout.write("No live agent-remote-driver sessions found.\n");
    return;
  }
  for (const s of redacted) {
    const status = probe ? (s.reachable ? "reachable" : `UNREACHABLE (${s.error})`) : "unverified";
    process.stdout.write(`${s.sessionId}  pid=${s.pid}  ${s.host}:${s.port}  ${status}  cwd=${s.cwd || "?"}\n`);
  }
}

await main();
