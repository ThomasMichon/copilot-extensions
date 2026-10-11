import fs from "node:fs";

const base = "http://core:9120";
const password = fs.readFileSync("/run/secrets/admin", "utf8");
const mode = process.argv[2] ?? "prepare";
const receipt = { browser_used: false, stages: [] };
let jwt;

async function rpc(path, params, authenticated = true) {
  const response = await fetch(base + path, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(authenticated ? { authorization: jwt } : {}),
    },
    body: JSON.stringify(params),
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`HTTP ${response.status} at ${path}`);
  return response.json();
}

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

try {
  for (let attempt = 0; attempt < 40; attempt++) {
    try {
      const login = await rpc("/auth/login/LoginLocalUser", { username: "proof-admin", password }, false);
      jwt = login.jwt ?? (login.type === "Jwt" ? login.data?.jwt : undefined);
      if (!jwt) throw new Error("Login response has no JWT");
      break;
    } catch (error) {
      receipt.last_retry_type = error.name;
      await pause(2000);
    }
  }
  if (!jwt) throw new Error("Configured admin login did not become ready");
  receipt.stages.push("configured-admin-login");
  receipt.core_version = (await rpc("/read/GetVersion", {})).version;
  if (receipt.core_version !== "2.3.3") throw new Error("Unexpected Core version");
  receipt.stages.push("authenticated-api");

  if (mode === "prepare") {
    const onboarding = await rpc("/write/CreateOnboardingKey", {
      name: "proof-onboarding", expires: Date.now() + 300000,
      privileged: false, create_builder: false,
    });
    if (typeof onboarding.private_key !== "string") throw new Error("No onboarding private key");
    fs.writeFileSync("/exchange/enrolled.toml",
      'core_address = "ws://core:9120"\nconnect_as = "Proof-enrolled"\nonboarding_key = ' +
      JSON.stringify(onboarding.private_key) + "\n");
    receipt.stages.push("bounded-onboarding-key-api");
  }

  let servers;
  for (let attempt = 0; attempt < 40; attempt++) {
    servers = await rpc("/read/ListServers", { limit: 10 });
    if (Array.isArray(servers) && servers.length === (mode === "prepare" ? 1 : 2)) break;
    await pause(2000);
  }
  if (!Array.isArray(servers) || servers.length !== (mode === "prepare" ? 1 : 2)) {
    throw new Error("Unexpected enrolled server count");
  }
  receipt.server_count = servers.length;
  receipt.stages.push("server-inventory");

  if (mode === "deploy") {
    const server = servers.find(server => server.name === "Proof-enrolled");
    if (!server?.id) throw new Error("Enrolled server ID missing");
    await rpc("/write/CreateDeployment", {
      name: "proof-role",
      config: {
        server_id: server.id,
        image: { type: "Image", params: { image: "busybox:1.37" } },
        network: "none", command: "sleep 300", restart: "no",
        send_alerts: false, skip_secret_interp: true, auto_update: false,
      },
    });
    const update = await rpc("/execute/Deploy", { deployment: "proof-role" });
    const updateId = update._id?.$oid;
    if (!updateId) throw new Error("Deployment acceptance lacks operation ID");
    let completed;
    for (let attempt = 0; attempt < 40; attempt++) {
      completed = await rpc("/read/GetUpdate", { id: updateId });
      if (completed.status === "Complete") break;
      await pause(1000);
    }
    if (completed?.status !== "Complete" || completed.success !== true) {
      throw new Error("Deployment did not complete successfully");
    }
    receipt.stages.push("named-role-deployment-complete");
  }
  receipt.result = "pass";
} catch (error) {
  receipt.result = "fail";
  receipt.error_type = error.name;
  receipt.error = error.message.startsWith("HTTP") ? error.message : "Bootstrap contract failed";
}
console.log(JSON.stringify(receipt));
if (receipt.result !== "pass") process.exitCode = 1;
