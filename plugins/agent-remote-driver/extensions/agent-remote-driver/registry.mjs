// agent-remote-driver -- fleet hygiene: stale-descriptor reaping and a
// single aggregated view over every session's discovery descriptor.
//
// A machine running a FLEET of parallel agent sessions (agent-dispatch
// workers, several worktrees, several CodeSpaces/containers each with their
// own copilot process) each writes its own descriptor into the same
// discoveryDir(). This module is what keeps that directory bounded and
// trustworthy without a shared daemon:
//
// - Collisions are structurally avoided already: each descriptor is named by
//   session id (globally unique) and each server binds an OS-assigned
//   ephemeral port (0 -- never a fixed, shared, or guessed port). There is
//   nothing here for two sessions to race over.
// - The real fleet-scale risk is ACCUMULATION: a session that crashes
//   (OOM, SIGKILL, host reboot) never runs its own `exit` cleanup, so its
//   descriptor is orphaned forever unless something reaps it. Multiply that
//   by a long-running fleet and the directory grows without bound, and a
//   consumer following stale descriptors wastes connections/timeouts on
//   sessions that no longer exist -- the "process-bombing" failure mode this
//   module exists to prevent is a consumer repeatedly reconnecting to dead
//   endpoints, or a directory scan that never shrinks.
// - Staleness needs TWO signals, not one: a bare pid-liveness check
//   (`process.kill(pid, 0)`) is necessary but not sufficient, because an OS
//   can and does recycle a pid -- a dead session's old pid can later belong
//   to an unrelated live process, which would make a naive liveness check
//   report a crashed session as "alive." `updatedAt` (refreshed by a
//   heartbeat while the session is genuinely alive, see extension.mjs) is
//   the second signal: a descriptor is only treated as live when BOTH its
//   pid currently exists AND its heartbeat is recent. This mirrors
//   agent-codespaces' own Connection Owner beacon pattern (a process-birth
//   identity defends against exactly this pid-reuse class of false
//   positive) -- full birth-time cross-platform verification is left as a
//   named follow-up (see README), heartbeat freshness already closes the
//   common case (a crashed process's pid is overwhelmingly unlikely to be
//   reused within one heartbeat interval).

import { readdirSync, readFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";

export const DEFAULT_HEARTBEAT_TIMEOUT_MS = 90_000; // 3x extension.mjs's HEARTBEAT_MS

// Returns true if a process with this pid currently exists. Never throws --
// EPERM (exists, no permission to signal it) still counts as alive; any
// other unexpected error is treated conservatively as "alive" too, so a
// transient OS error never causes a live session's descriptor to be reaped.
export function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0); // signal 0: existence check only, nothing delivered
    return true;
  } catch (e) {
    if (e && e.code === "ESRCH") return false; // no such process
    return true; // EPERM or anything else: assume alive, don't guess-delete
  }
}

// Reads and parses one descriptor file. Returns null (never throws) for
// anything unreadable or malformed -- a half-written or corrupt file is
// itself a form of staleness callers should simply skip, not crash on.
function readDescriptorSafe(path) {
  try {
    const data = JSON.parse(readFileSync(path, "utf-8"));
    if (!data || typeof data !== "object" || typeof data.sessionId !== "string") return null;
    return data;
  } catch {
    return null;
  }
}

// Lists every descriptor currently on disk, valid or not. Each entry also
// carries its file `path` so a caller can act on it (e.g. unlink).
export function listDescriptorFiles(dir) {
  let names;
  try {
    names = readdirSync(dir);
  } catch {
    return []; // directory doesn't exist yet -- an empty fleet, not an error
  }
  return names
    .filter((n) => n.endsWith(".json"))
    .map((n) => {
      const path = join(dir, n);
      return { path, descriptor: readDescriptorSafe(path) };
    });
}

// Pure staleness predicate: given a descriptor (or null, for an unreadable
// file) and the current time, is this entry safe to reap?
export function isStale(descriptor, { now = Date.now(), heartbeatTimeoutMs = DEFAULT_HEARTBEAT_TIMEOUT_MS } = {}) {
  if (!descriptor) return true; // unreadable/malformed -- always reap
  if (!isProcessAlive(descriptor.pid)) return true;
  const updatedAt = Date.parse(descriptor.updatedAt || descriptor.startedAt || "");
  if (!Number.isFinite(updatedAt)) return true; // no usable timestamp at all
  return now - updatedAt > heartbeatTimeoutMs;
}

// Revalidates ONE snapshot-flagged-stale entry immediately before acting on
// it, and deletes it only if it is still stale at that moment. Exported
// (not just inlined into sweepStale's loop) specifically so the TOCTOU fix
// itself is independently testable: a test can snapshot a descriptor, then
// mutate the underlying file (simulating the owner's heartbeat racing the
// sweep) before calling this, and assert the refreshed file survives.
export function reapIfStillStale(path, snapshotDescriptor, opts = {}) {
  if (!isStale(snapshotDescriptor, opts)) return { removed: false, descriptor: snapshotDescriptor };
  const revalidated = readDescriptorSafe(path);
  if (!isStale(revalidated, opts)) {
    // The owner refreshed its heartbeat since the snapshot -- it is live
    // again; never delete it on stale information.
    return { removed: false, descriptor: revalidated };
  }
  try {
    unlinkSync(path);
  } catch {
    /* already gone or in-use; fine either way */
  }
  return { removed: true, descriptor: revalidated };
}

// Removes every stale descriptor in `dir`. Best-effort: a file that
// disappears between listing and unlink (another sweep won the race, or the
// session itself just exited cleanly) is not an error. Returns which paths
// were removed vs kept, for logging/tests.
//
// TOCTOU guard (load-bearing): `listDescriptorFiles` above returns a
// SNAPSHOT. Between that snapshot and acting on it, the owning session can
// legitimately refresh its own heartbeat (extension.mjs's periodic
// `writeDescriptor`) -- deleting based on the stale snapshot alone would
// destroy a descriptor that is fresh again by the time we act. See
// `reapIfStillStale` above for the actual revalidate-before-delete logic.
export function sweepStale(dir, opts = {}) {
  const removed = [];
  const kept = [];
  for (const { path, descriptor } of listDescriptorFiles(dir)) {
    const result = reapIfStillStale(path, descriptor, opts);
    if (result.removed) {
      removed.push(path);
    } else {
      kept.push({ path, descriptor: result.descriptor });
    }
  }
  return { removed, kept };
}


// The aggregated, fleet-wide view: sweep first (so a stale entry never
// shows up as "live"), then return every surviving descriptor. This is the
// ONE scan a fleet controller needs instead of every consumer re-implementing
// its own directory walk + liveness guessing.
export function listLive(dir, opts = {}) {
  return sweepStale(dir, opts).kept.map(({ descriptor }) => descriptor);
}
