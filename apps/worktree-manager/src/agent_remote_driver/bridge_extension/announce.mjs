// Once-per-session announcement for the live-session extension.
//
// The CLI restarts extension hosts around permission changes and resumes; each
// restart re-runs extension.mjs, which would otherwise repeat its "loaded" line
// in the session. A marker in the session's own state dir (the same sidecar
// pattern as agent-worktrees' substatus.json) records that it was shown.

import { existsSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export const ANNOUNCED_MARKER = "agent-bridge-announced";

/**
 * True the first time it is asked for a session, false afterwards. With no
 * session id or no session-state dir it cannot remember, so it says yes: a
 * repeated line is better than a missing one.
 */
export function firstLoadThisSession(
  sessionId,
  { stateRoot = join(homedir(), ".copilot", "session-state"), now = () => new Date() } = {},
) {
  if (!sessionId) return true;
  try {
    const dir = join(stateRoot, sessionId);
    if (!existsSync(dir)) return true;
    const marker = join(dir, ANNOUNCED_MARKER);
    if (existsSync(marker)) return false;
    writeFileSync(marker, now().toISOString(), "utf-8");
  } catch {
    /* best-effort: announce when unsure */
  }
  return true;
}
