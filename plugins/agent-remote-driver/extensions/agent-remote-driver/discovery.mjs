// agent-remote-driver -- discovery descriptor helpers.
//
// Pure, testable helpers for the per-session discovery file any external
// driver (agent-bridge, a bare script, a human's curl) reads to find this
// session's local driver endpoint. No SDK dependency -- these are plain
// data-shaping and path-resolution functions, deliberately separated from
// extension.mjs's SDK-coupled glue so they can be unit tested directly.

import { join } from "node:path";
import { homedir } from "node:os";
import { randomBytes, timingSafeEqual } from "node:crypto";

// Discovery root: a per-machine (not per-repo) directory, matching the
// vision's "user-global, not per-repo/per-worktree" install requirement.
// Overridable for tests and for a non-default HOME layout.
export function discoveryDir(env = process.env) {
  const base = env.AGENT_REMOTE_DRIVER_CONFIG_DIR || join(homedir(), ".copilot", "remote-driver");
  return join(base, "sessions");
}

export function descriptorPath(sessionId, env = process.env) {
  return join(discoveryDir(env), `${sessionId}.json`);
}

// A fresh, URL-safe bearer token. 32 bytes -> 256 bits of entropy.
export function generateToken() {
  return randomBytes(32).toString("base64url");
}

// The discovery descriptor's shape -- intentionally minimal. `driverVersion`
// lets a future wire-protocol revision of this same extension distinguish
// itself from an older sibling's descriptor without guessing from shape.
// `updatedAt` is a heartbeat: extension.mjs refreshes it periodically while
// the session is alive, so registry.mjs's staleness check has a second
// signal beyond raw pid liveness (needed because a dead pid can be reused by
// an unrelated later process -- see registry.mjs's isStale).
export function buildDescriptor({ sessionId, pid, port, token, cwd, startedAt, updatedAt, driverVersion }) {
  if (!sessionId) throw new Error("buildDescriptor: sessionId is required");
  if (!Number.isInteger(pid) || pid <= 0) throw new Error("buildDescriptor: pid must be a positive integer");
  if (!Number.isInteger(port) || port <= 0) throw new Error("buildDescriptor: port must be a positive integer");
  if (!token) throw new Error("buildDescriptor: token is required");
  const now = new Date().toISOString();
  return {
    version: 1,
    driverVersion: driverVersion || "0.1.0-dev1",
    sessionId,
    pid,
    host: "127.0.0.1",
    port,
    token,
    cwd: cwd || null,
    startedAt: startedAt || now,
    updatedAt: updatedAt || now,
  };
}

// Constant-time bearer-token comparison. Returns false (never throws) for any
// malformed/missing Authorization header -- callers should treat that as
// "unauthenticated", not special-case the error.
export function authorizes(authorizationHeader, expectedToken) {
  if (typeof authorizationHeader !== "string" || !expectedToken) return false;
  const m = authorizationHeader.match(/^Bearer\s+(.+)$/);
  if (!m) return false;
  const given = Buffer.from(m[1], "utf-8");
  const expected = Buffer.from(expectedToken, "utf-8");
  if (given.length !== expected.length) return false;
  return timingSafeEqual(given, expected);
}
