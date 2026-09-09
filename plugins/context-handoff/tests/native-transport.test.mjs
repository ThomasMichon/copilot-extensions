import assert from "node:assert/strict";
import { test } from "node:test";
import { encodeRpcFrame, rpcFrameReader } from "../extensions/context-handoff/native-transport.mjs";

test("native and SDK JSON-RPC frames preserve UTF-8 and split headers/bodies", () => {
  const messages = [
    { jsonrpc: "2.0", id: 1, result: { message: "原生目标" } },
    { jsonrpc: "2.0", id: "native:1", result: { state: null } },
    { jsonrpc: "2.0", method: "session.event", params: { type: "session.idle" } },
  ];
  const bytes = Buffer.concat(messages.map(encodeRpcFrame));
  const received = [];
  const accept = rpcFrameReader(message => received.push(message));
  for (let index = 0; index < bytes.length; index += 3) accept(bytes.subarray(index, index + 3));
  assert.deepEqual(received, messages);
});

test("malformed transport is surfaced rather than fabricating a no-goal state", () => {
  const accept = rpcFrameReader(() => assert.fail("malformed frame accepted"));
  assert.throws(() => accept(Buffer.from("Invalid: 1\r\n\r\n{}")), /Invalid/);
});
