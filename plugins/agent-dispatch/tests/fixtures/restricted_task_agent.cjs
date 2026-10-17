// Offline ACP witness: every task operation crosses the real HTTP authority.
const fs = require("node:fs");
const crypto = require("node:crypto");
const http = require("node:http");
const readline = require("node:readline");
const assert = require("node:assert/strict");

const task = process.env.AGENT_DISPATCH_TASK_ID;
const base = process.env.AGENT_DISPATCH_TASK_URL;
const tokenPath = process.env.AGENT_DISPATCH_TASK_TOKEN_FILE;
let native;
let prompts = 0;
const digest = value => crypto.createHash("sha256").update(value).digest("hex");
const reply = (id, result) => console.log(JSON.stringify({jsonrpc: "2.0", id, result}));
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function publishProof(value) {
    fs.writeFileSync("/workspace/proof.next", JSON.stringify(value));
    fs.renameSync("/workspace/proof.next", "/workspace/proof.json");
}

async function request(method, path, body, credential = fs.readFileSync(tokenPath, "utf8")) {
    return new Promise((resolve, reject) => {
        const data = body === undefined ? undefined : JSON.stringify(body);
        const req = http.request(new URL(path, base), {
            method, headers: {
                "Content-Type": "application/json",
                "X-Agent-Dispatch-Task-Token": credential,
            }, timeout: 10000,
        }, response => {
            let text = "";
            response.setEncoding("utf8");
            response.on("data", chunk => { text += chunk; });
            response.on("end", () => resolve({status: response.statusCode, text}));
            response.on("error", reject);
        });
        req.on("timeout", () => req.destroy(new Error("fixture HTTP deadline")));
        req.on("error", reject);
        req.end(data);
    });
}

async function work() {
    assert.equal(++prompts, 1);
    assert.equal(process.getuid(), 1000);
    for (const key of ["GH_TOKEN", "GITHUB_TOKEN", "COPILOT_GITHUB_TOKEN",
                       "AGENT_DISPATCH_TOKEN", "AGENT_DISPATCH_CONTROL_TOKEN"]) {
        assert.ok(!process.env[key], key);
    }
    const stat = fs.statSync(tokenPath);
    assert.equal(stat.uid, 0);
    assert.equal(stat.mode & 0o777, 0o444);
    assert.throws(() => fs.writeFileSync(tokenPath, "changed"), /EACCES|EPERM/);
    const old = fs.readFileSync(tokenPath, "utf8");
    const body = {worker_id: "asserted-other-worker", owner_session_id: "asserted-other-native"};
    const started = await request("POST", `/tasks/${task}/start`, body);
    assert.equal(started.status, 200, started.text);
    assert.equal(JSON.parse(started.text).owner_session_id, native);
    assert.ok(!Object.hasOwn(JSON.parse(started.text), "task_token"));
    const payload = await request("GET", `/tasks/${task}/payload`);
    assert.equal(payload.status, 200, payload.text);
    const other = JSON.parse(JSON.parse(payload.text).payload).other;
    const refused = [];
    for (const [method, path, data] of [
        ["GET", `/tasks/${other}`],
        ["POST", `/tasks/${other}/complete`, body],
        ["GET", "/tasks"],
        ["POST", "/claim", body],
        ["POST", `/tasks/${task}/task-grant`, body],
    ]) {
        const response = await request(method, path, data);
        assert.equal(response.status, 403, `${path}: ${response.text}`);
        refused.push({method, path, status: response.status});
    }
    assert.equal((await request("GET", `/tasks/${task}`, undefined, old + "tampered")).status, 401);
    const progress = await request("POST", `/tasks/${task}/progress`, {
        worker_id: body.worker_id, summary: "Waiting for trusted credential rotation",
    });
    assert.equal(progress.status, 200, progress.text);
    publishProof({
        native, prompts, token_digest: digest(old), refused, phase: "awaiting-renewal",
    });
    const deadline = Date.now() + 60000;
    while (digest(fs.readFileSync(tokenPath, "utf8")) === digest(old)) {
        assert.ok(Date.now() < deadline, "trusted renewal never arrived");
        await sleep(100);
    }
    const heartbeat = await request("POST", `/tasks/${task}/heartbeat`, body);
    assert.equal(heartbeat.status, 200, heartbeat.text);
    while (!fs.existsSync("/workspace/complete")) {
        assert.ok(Date.now() < deadline, "host never released completion after renewal");
        await sleep(100);
    }
    const completed = await request("POST", `/tasks/${task}/complete`, {
        worker_id: body.worker_id, result: {native, prompts, refused, rotated: true},
    });
    assert.equal(completed.status, 200, completed.text);
    assert.equal((await request("GET", `/tasks/${task}`)).status, 403);
    publishProof({
        native, prompts, refused, rotated: true, phase: "completed",
    });
}

readline.createInterface({input: process.stdin}).on("line", async line => {
    const message = JSON.parse(line);
    if (!Object.hasOwn(message, "id")) return;
    try {
        if (message.method === "initialize") {
            reply(message.id, {protocolVersion: 1, agentCapabilities: {loadSession: true}});
        } else if (message.method === "session/new") {
            assert.equal(native, undefined);
            native = crypto.randomUUID();
            reply(message.id, {sessionId: native});
        } else if (message.method === "session/load") {
            throw new Error("this first-touch proof must not replace or reload its native");
        } else if (message.method === "session/prompt") {
            assert.equal(message.params.sessionId, native);
            await work();
            reply(message.id, {stopReason: "end_turn"});
        } else {
            throw new Error(`unsupported fixture method: ${message.method}`);
        }
    } catch (error) {
        console.error(error.stack);
        console.log(JSON.stringify({
            jsonrpc: "2.0", id: message.id,
            error: {code: -32603, message: String(error)},
        }));
    }
});
