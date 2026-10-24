import { createServer } from "node:net";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { join } from "node:path";

export async function residentFixture() {
  const root = mkdtempSync(join(process.cwd(), ".resident-receipt-"));
  const sessionDir = join(root, "session-state", "subject");
  const runtimeRoot = join(root, "runtime");
  const guidanceDir = join(sessionDir, "instructions", "agent-worktrees");
  mkdirSync(guidanceDir, { recursive: true });
  mkdirSync(runtimeRoot, { recursive: true });
  const activeRoutePath = join(runtimeRoot, "active.json");
  const tokenPath = join(runtimeRoot, "token");
  const guidance = join(guidanceDir, "session-guidance.instructions.md");
  const bindingPath = join(sessionDir, "worktree-binding.json");
  const identity = {
    installationId: "installation", sourceFingerprint: "source",
    marketplaceId: "marketplace", pluginId: "agent-worktrees",
  };
  const owner = { ...identity, routeGeneration: 1, pid: 123,
    processStartTime: "start", runtimeGeneration: "runtime-one" };
  const candidate = { ...identity, version: 1, state: "candidate", runtimeRoot, activeRoutePath, tokenPath };
  const catalog = {
    schema: "copilot-extensions.session-command-catalog", version: 1, plugin: "agent-worktrees",
    binding: {
      schema: "copilot-extensions.command-installation-binding", version: 1, status: "selected",
      source: { fingerprint: "source", marketplaceId: "marketplace", pluginId: "agent-worktrees" },
      installation: { ...identity, runtimeRoot },
    },
    residentControl: candidate,
  };
  const subjectBinding = {
    sessionId: "subject", project: "project", worktreeId: "worktree", creationNonce: "incarnation",
  };
  const sockets = new Set();
  const requests = [];
  const effects = new Set();
  const fixture = {
    root, sessionDir, runtimeRoot, activeRoutePath, tokenPath, guidance, bindingPath,
    owner, candidate, catalog, subjectBinding, requests, effects, sockets,
    dropRecordReply: false, hangHealth: false, oversized: false, capability: true, inFlight: false,
    writeCatalog() { writeFileSync(guidance, "```json\n" + JSON.stringify(catalog) + "\n```\n"); },
    writeBinding() { writeFileSync(bindingPath, JSON.stringify(subjectBinding)); },
  };
  const server = createServer((socket) => {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
    let raw = "";
    socket.on("data", (data) => {
      raw += data.toString();
      if (!raw.endsWith("\n")) return;
      const request = JSON.parse(raw);
      raw = "";
      requests.push(request);
      const send = (result) => socket.end(JSON.stringify({ version: 1, result }) + "\n");
      if (request.token !== "fixture-token") return send({ error: "unauthorized" });
      if (request.action === "health") {
        if (fixture.hangHealth) return;
        return send({
          capabilities: fixture.capability ? ["handoff-prompt-receipt-v1"] : [],
          promptReceipt: { contractVersion: 1, status: fixture.capability ? "ready" : "unavailable", owner },
        });
      }
      const subject = request.payload.subject;
      const key = JSON.stringify(subject);
      let status = effects.has(key) ? "received" : "absent";
      const replayed = status === "received";
      if (fixture.inFlight) status = "unknown";
      else if (request.payload.operation === "record") {
        effects.add(key);
        status = "received";
        if (fixture.dropRecordReply) return socket.destroy();
      }
      if (fixture.oversized) return socket.end("x".repeat(8193));
      send({ contractVersion: 1, status, reason: "fixture-result", replayed, subject, owner });
    });
  });
  try {
    await new Promise((resolve, reject) => {
      server.once("error", reject);
      server.listen(0, "127.0.0.1", resolve);
    });
  } catch (error) {
    server.close(() => {});
    rmSync(root, { recursive: true, force: true });
    throw error;
  }
  fixture.route = { bind: "127.0.0.1", port: server.address().port,
    pid: 123, generation: 1, process_start_time: "start" };
  fixture.writeRoute = () => writeFileSync(activeRoutePath, JSON.stringify({ active: fixture.route }));
  fixture.writeCatalog();
  fixture.writeBinding();
  fixture.writeRoute();
  writeFileSync(tokenPath, "fixture-token");
  fixture.close = async () => {
    for (const socket of sockets) socket.destroy();
    await new Promise((resolve) => server.close(resolve));
    rmSync(root, { recursive: true, force: true });
  };
  return fixture;
}
