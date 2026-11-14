// Bridge endpoint handling for the live-session extension, kept free of the
// extension's top-level joinSession() so it can be unit-tested.
//
// The bridge's address can change under a running session: a zero-downtime
// daemon redeploy moves its port (active.json follows), and a venue's forward
// can be re-plumbed onto the new port. So after a failed call, re-read the
// address and token and retry once if either changed, instead of talking to a
// dead port forever. Seen live: a CodeSpace session whose heartbeats stopped
// for good, and whose row expired, once its forward moved to the new port.

export function makeBridgeEndpoint({ resolveBase, resolveToken, fetchImpl, log, timeoutMs }) {
  const ep = { base: resolveBase(), token: resolveToken() };

  function refresh() {
    const base = resolveBase();
    const token = resolveToken();
    const changed = base !== ep.base || (token && token !== ep.token);
    if (changed) {
      log?.(`bridge endpoint changed: ${ep.base} -> ${base}`);
      ep.base = base;
      if (token) ep.token = token;
    }
    return changed;
  }

  async function call(method, path, body) {
    const attempt = () => fetchImpl(`${ep.base}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${ep.token}`,
        ...(body ? { "Content-Type": "application/json" } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(timeoutMs),
    });
    try {
      const res = await attempt();
      // 401: a rotated token; 404/5xx: possibly a different daemon now owns the
      // address. Anything else is an answer from the right bridge.
      if (res.status !== 401 && res.status !== 404 && res.status < 500) return res;
      if (!refresh()) return res;
    } catch (e) {
      if (!refresh()) throw e;
    }
    return attempt();
  }

  return { ep, call, refresh };
}
