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

import { readdirSync, readFileSync, unlinkSync, renameSync } from "node:fs";
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
//
// A missing directory is an EMPTY FLEET, not an error -- the common case
// for a freshly provisioned machine/image before any session has ever
// registered. Any OTHER `readdirSync` failure (EACCES, EIO, a transient
// mount issue) is a genuine inability to see the registry at all and must
// propagate: silently returning `[]` for those too would make a sweep
// falsely "succeed" and make `bin/list-sessions.mjs` falsely report an
// empty fleet when the registry is actually just inaccessible.
export function listDescriptorFiles(dir) {
  let names;
  try {
    names = readdirSync(dir);
  } catch (e) {
    if (e && e.code === "ENOENT") return [];
    throw e;
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
//
// A descriptor with NO `updatedAt` key at all (as opposed to one present but
// unparseable) is treated as pid-liveness-only -- never reaped on heartbeat
// grounds. Every descriptor THIS version of the plugin writes always
// carries `updatedAt` (see discovery.mjs's buildDescriptor), so this case
// cannot occur today; it exists for forward compatibility with a future
// rolling update where an already-running OLDER session (launched before a
// heartbeat-protocol change) is still alive but was never taught to refresh
// a field a newer sibling session's sweep now expects. Reaping such a
// session purely because it predates a protocol change would be a
// self-inflicted fleet outage, not a hygiene win -- fail toward keeping,
// not deleting, exactly like isProcessAlive's EPERM handling above.
export function isStale(descriptor, { now = Date.now(), heartbeatTimeoutMs = DEFAULT_HEARTBEAT_TIMEOUT_MS } = {}) {
  if (!descriptor) return true; // unreadable/malformed -- always reap
  if (!isProcessAlive(descriptor.pid)) return true;
  if (descriptor.updatedAt === undefined) return false; // no heartbeat capability -- pid-liveness governs
  const updatedAt = Date.parse(descriptor.updatedAt);
  if (!Number.isFinite(updatedAt)) return true; // present but garbage -- a real corruption signal, reap
  return now - updatedAt > heartbeatTimeoutMs;
}

// Revalidates ONE snapshot-flagged-stale entry immediately before acting on
// it, closing the TOCTOU window between a sweep's staleness snapshot and
// its actual deletion. A plain "re-read then unlink" still has a race (the
// owner can rename a fresh heartbeat into `path` between the re-read and the
// unlink); this instead CLAIMS the entry atomically first:
//
//   1. `renameSync(path, claimPath)` -- atomic on both POSIX and Windows.
//      Whichever write wins this instant wins outright: if the owner's own
//      heartbeat rename lands on `path` first, our rename captures that
//      FRESH content into `claimPath` (we see exactly what the owner wrote,
//      nothing older); if ours lands first, the owner's subsequent heartbeat
//      rename simply recreates `path` from scratch (rename to a path that no
//      longer exists just creates it) -- completely unaffected by what we do
//      to `claimPath` afterward. Either way, there is no instant after this
//      rename where a concurrent writer and our reaper can observe or act on
//      the SAME path.
//   2. Revalidate staleness against the CLAIMED copy -- no further race is
//      possible on it, since nothing else holds a reference to `claimPath`.
//   3. Still stale -> delete the claimed copy. No longer stale (the owner's
//      write actually won the rename race) -> rename it back to `path`,
//      restoring the fresh descriptor exactly where it belongs.
//
// Exported (not just inlined into sweepStale's loop) so the TOCTOU fix
// itself is independently testable. `renameFn`/`unlinkFn` (default to the
// real `renameSync`/`unlinkSync`) are injectable so a test can force a
// non-ENOENT claim or delete failure deterministically, without relying on
// platform-specific permission APIs that behave inconsistently across
// POSIX and Windows.
export function reapIfStillStale(path, snapshotDescriptor, opts = {}, renameFn = renameSync, unlinkFn = unlinkSync) {
  if (!isStale(snapshotDescriptor, opts)) return { removed: false, descriptor: snapshotDescriptor };

  const claimPath = `${path}.reap-claim.${process.pid}.${Date.now()}`;
  try {
    renameFn(path, claimPath);
  } catch (e) {
    if (e && e.code === "ENOENT") return { removed: false, descriptor: null }; // already gone -- another sweep won, or the owner exited cleanly
    // A permission/other claim failure leaves us unable to confirm this
    // entry's CURRENT state at all. `snapshotDescriptor` was already judged
    // stale -- returning it here would make a failed reap attempt expose a
    // known-stale descriptor as if it were live (sweepStale would add it to
    // `kept`, and listLive()/bin/list-sessions.mjs would report and probe a
    // dead endpoint). Report nothing usable instead: neither removed nor
    // live, an honest "could not act on this entry."
    return { removed: false, descriptor: null }; // permission/other error -- never guess-delete, never report as live either
  }

  const claimed = readDescriptorSafe(claimPath);
  if (!isStale(claimed, opts)) {
    // The owner's own fresh write actually won the rename race above -- this
    // IS the live descriptor; put it back exactly where it belongs.
    try {
      renameFn(claimPath, path);
      return { removed: false, descriptor: claimed };
    } catch {
      // Something (almost certainly the owner's own next heartbeat) already
      // recreated `path` in the meantime -- there's nothing to restore to;
      // the owner's descriptor already exists there correctly on its own.
      return { removed: false, descriptor: claimed };
    }
  }

  let removed = true;
  let descriptor = claimed;
  try {
    unlinkFn(claimPath);
  } catch (e) {
    // The claim itself succeeded (we own this file now, under this unique
    // claim name), so an unlink failure here is a genuine, reportable
    // failure -- NOT "already gone" (nothing else knows this claimPath
    // exists) and must not be reported as a successful reap. `claimed` was
    // already confirmed stale above (that's why we're deleting it at all)
    // -- returning it here on a FAILED delete would repeat the exact
    // failed-reap-exposes-stale-as-live bug fixed earlier in this file for
    // the claim step, just one step later: never report a known-stale
    // descriptor as live just because deleting it didn't work.
    removed = e && e.code === "ENOENT";
    if (!removed) descriptor = null;
  }
  return { removed, descriptor };
}

// Matches an orphaned sidecar's name AND captures its OWNER pid directly
// from the filename -- not from the descriptor content, which for a
// `.reap-claim.*` file is the CLAIMED (already-judged-stale) target
// session's own pid, never the claimant/reaper's. Checking the content pid
// there would make a live, mid-reap claimant's own in-flight file look
// "owned by a dead pid" and get deleted out from under it. The two
// capture groups correspond to the two sidecar shapes:
//   - `<name>.json.<pid>.<ts>.tmp`           (an interrupted writeDescriptorAtomic)
//   - `<name>.json.reap-claim.<pid>.<ts>`    (an interrupted reapIfStillStale)
// Both carry the same bearer token as the real descriptor they were derived
// from.
const SIDECAR_RE = /\.json\.(?:(\d+)\.\d+\.tmp|reap-claim\.(\d+)\.\d+)$/;

function listSidecarEntries(dir) {
  let names;
  try {
    names = readdirSync(dir);
  } catch (e) {
    if (e && e.code === "ENOENT") return [];
    throw e;
  }
  const entries = [];
  for (const n of names) {
    const m = n.match(SIDECAR_RE);
    if (!m) continue;
    const ownerPid = Number(m[1] || m[2]);
    entries.push({ path: join(dir, n), ownerPid });
  }
  return entries;
}

// Removes orphaned sidecar files. `listDescriptorFiles` only ever looks at
// `*.json` -- a crash exactly mid-`writeDescriptorAtomic` (between creating
// the `.tmp` and its rename) or mid-`reapIfStillStale` (between claiming and
// deleting/restoring) leaves one of these sidecars on disk with NOTHING in
// this module ever sweeping it, since neither pattern is a bare `.json`
// file. Both carry a bearer token, so an orphan left behind is a
// credential-bearing file that would otherwise persist forever. A sidecar
// is reaped only when its OWNER pid (parsed from the FILENAME -- see
// SIDECAR_RE above, never the descriptor's own content) is dead (the same
// conservative "fail toward keeping" posture as the rest of this module) --
// while its owner is still alive it may genuinely be mid-operation, not
// orphaned. This also means a `.tmp` file that is still genuinely mid-write
// (and therefore parses as unreadable/null) is correctly left alone as long
// as its writer is alive -- liveness never depends on being able to parse
// the file's own content at all.
export function sweepOrphanedSidecars(dir) {
  const removed = [];
  for (const { path, ownerPid } of listSidecarEntries(dir)) {
    if (isProcessAlive(ownerPid)) continue; // may be genuinely mid-operation
    try {
      unlinkSync(path);
      removed.push(path);
    } catch {
      /* already gone or in-use; fine either way */
    }
  }
  return removed;
}


// Removes every stale descriptor in `dir`, AND any orphaned write/claim
// sidecar (see sweepOrphanedSidecars above). Best-effort: a file that
// disappears between listing and reaping (another sweep won the race, or
// the session itself just exited cleanly) is not an error. Returns which
// paths were removed vs kept, for logging/tests.
//
// TOCTOU guard (load-bearing): `listDescriptorFiles` above returns a
// SNAPSHOT. See `reapIfStillStale` above for how acting on that snapshot is
// made safe via an atomic claim-rename rather than a plain re-read-then-
// unlink (which still has its own narrower race).
export function sweepStale(dir, opts = {}) {
  const removed = sweepOrphanedSidecars(dir);
  const kept = [];
  for (const { path, descriptor } of listDescriptorFiles(dir)) {
    const result = reapIfStillStale(path, descriptor, opts);
    if (result.removed) {
      removed.push(path);
    } else if (result.descriptor) {
      kept.push({ path, descriptor: result.descriptor });
    }
    // else: the entry vanished entirely (ENOENT on claim), or the claim
    // itself failed for another reason -- nothing to report as kept, and
    // it was never actually removed BY this call either.
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

