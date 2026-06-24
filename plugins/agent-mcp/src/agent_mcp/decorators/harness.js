'use strict';
// code-mode harness: run user JS/TS with upstream MCP tools bound as async
// functions. Protocol (line-delimited JSON) over stdin/stdout with the Python
// decorator:
//   Python -> Node:  {t:"start", code, toolNames}   then  {t:"call_result", id, ok, result|error}
//   Node -> Python:  {t:"call", id, tool, args}      then  {t:"done", ok, result|error, logs}
// console.* output is captured (never written to stdout) so stdout stays a clean
// protocol channel.

const readline = require('readline');

let nextId = 1;
const pending = new Map();
const logs = [];

function send(obj) { process.stdout.write(JSON.stringify(obj) + '\n'); }
function safeStr(v) { try { return JSON.stringify(v); } catch (e) { return String(v); } }
function logCapture(...args) {
  logs.push(args.map((a) => (typeof a === 'string' ? a : safeStr(a))).join(' '));
}
console.log = logCapture;
console.info = logCapture;
console.warn = logCapture;
console.error = logCapture;
console.debug = logCapture;

function callTool(name, args) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    send({ t: 'call', id, tool: name, args: args || {} });
  });
}

// Unwrap a CallToolResult into an ergonomic value: a lone JSON text block is
// parsed, a lone text block is returned as a string, otherwise the raw result.
function unwrap(result) {
  if (result == null) return null;
  const content = result.content;
  if (Array.isArray(content)) {
    const texts = content.filter((c) => c && c.type === 'text').map((c) => c.text);
    if (texts.length === 1) {
      try { return JSON.parse(texts[0]); } catch (e) { return texts[0]; }
    }
    if (texts.length > 1) return texts;
  }
  return result;
}

async function run(code, toolNames) {
  const tools = {};
  for (const n of toolNames) {
    tools[n] = (args) => callTool(n, args).then(unwrap);
  }
  try {
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    const fn = new AsyncFunction('tools', 'callTool', code);
    const value = await fn(tools, (n, a) => callTool(n, a).then(unwrap));
    send({ t: 'done', ok: true, result: value === undefined ? null : value, logs });
  } catch (e) {
    send({ t: 'done', ok: false, error: String((e && e.stack) || e), logs });
  }
}

const rl = readline.createInterface({ input: process.stdin });
rl.on('line', (line) => {
  const text = line.trim();
  if (!text) return;
  let msg;
  try { msg = JSON.parse(text); } catch (e) { return; }
  if (msg.t === 'start') {
    run(msg.code, msg.toolNames || []);
  } else if (msg.t === 'call_result') {
    const p = pending.get(msg.id);
    if (!p) return;
    pending.delete(msg.id);
    if (msg.ok) p.resolve(msg.result);
    else p.reject(new Error(msg.error || 'tool error'));
  }
});
