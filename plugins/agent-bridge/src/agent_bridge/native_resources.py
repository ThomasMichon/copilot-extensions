"""Optional, execution-scoped host resources over the existing native transport."""

from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import sys
import time

from .native_store import NativeError, NativeStore, identifier, signature

CAPABILITY = "native-host-resources-v1"
DEFINITIONS_SCHEMA = "copilot-extensions.native-host-resources"
DESCRIPTOR_SCHEMA = "copilot-extensions.native-host-resource-capability"
PROVIDER_SCHEMA = "copilot-extensions.native-host-resource-provider"
RESULT_SCHEMA = "copilot-extensions.native-host-resource-result"
MAX_BYTES = 65536
REQUEST_FIELDS = {"executionId", "generation", "sessionId", "requestId", "resource", "input"}
EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def bounded_json(value, maximum=MAX_BYTES):
    try:
        data = json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise NativeError("invalid_resource", "Resource data must be finite JSON", 400) from exc
    if len(data) > maximum:
        raise NativeError("invalid_resource", "Resource data exceeds its size limit", 400)
    return data


def read_json_file(path: str) -> dict:
    source = Path(path)
    try:
        if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_BYTES:
            raise ValueError("unsafe resource file")
        value = json.loads(source.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise NativeError("invalid_resource_file", "Expected a bounded regular UTF-8 JSON resource file", 400) from exc
    if not isinstance(value, dict):
        raise NativeError("invalid_resource_file", "Resource file must contain an object", 400)
    bounded_json(value)
    return value


def validate_schema(schema: dict) -> dict:
    if (
        not isinstance(schema, dict)
        or set(schema) - {"type", "properties", "additionalProperties", "required"}
        or schema.get("type") != "object" or schema.get("additionalProperties") is not False
        or not isinstance(schema.get("properties"), dict) or len(schema["properties"]) > 16
    ):
        raise NativeError("invalid_resource", "Expected a closed primitive input schema", 400)
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(k, str) or k not in schema["properties"] for k in required):
        raise NativeError("invalid_resource", "Invalid required resource inputs", 400)
    for key, rule in schema["properties"].items():
        identifier(key)
        if not isinstance(rule, dict) or set(rule) - {"type", "enum"} or rule.get("type") not in {"string", "integer", "boolean"}:
            raise NativeError("invalid_resource", "Resource input properties must be primitive", 400)
        if "enum" in rule:
            if not isinstance(rule["enum"], list) or not 1 <= len(rule["enum"]) <= 32:
                raise NativeError("invalid_resource", "Invalid resource input enumeration", 400)
            for value in rule["enum"]:
                _validate_primitive(value, rule["type"])
    return schema


def _validate_primitive(value, kind: str):
    valid = (kind == "string" and isinstance(value, str) and len(value) <= 4096 and "\0" not in value
             or kind == "boolean" and type(value) is bool
             or kind == "integer" and type(value) is int and abs(value) <= 2**53 - 1)
    if not valid:
        raise NativeError("invalid_resource_input", "Resource input does not match its declared type", 400)


def validate_input(value, schema):
    if not isinstance(value, dict):
        raise NativeError("invalid_resource_input", "Resource input must be an object", 400)
    bounded_json(value, 16384)
    properties = schema["properties"]
    if set(value) - properties.keys() or set(schema.get("required", [])) - value.keys():
        raise NativeError("invalid_resource_input", "Unexpected or missing resource inputs", 400)
    for key, item in value.items():
        rule = properties[key]
        _validate_primitive(item, rule["type"])
        if "enum" in rule and item not in rule["enum"]:
            raise NativeError("invalid_resource_input", "Resource input is outside its declared enumeration", 400)
    return value


def validate_definitions(value):
    bounded_json(value)
    if (
        not isinstance(value, dict) or set(value) != {"schema", "version", "resources"}
        or value["schema"] != DEFINITIONS_SCHEMA or type(value["version"]) is not int or value["version"] != 1
        or not isinstance(value["resources"], dict) or not 1 <= len(value["resources"]) <= 8
    ):
        raise NativeError("invalid_resource", "Unsupported native host resource definitions", 400)
    for name, definition in value["resources"].items():
        identifier(name)
        if not isinstance(definition, dict) or set(definition) - {"argv", "config", "inputSchema"}:
            raise NativeError("invalid_resource", "Invalid local resource provider definition", 400)
        argv = definition.get("argv")
        if (
            not isinstance(argv, list) or not 1 <= len(argv) <= 32
            or any(not isinstance(arg, str) or not arg or "\0" in arg or len(arg) > 8192 for arg in argv)
            or not Path(argv[0]).is_absolute() or not Path(argv[0]).is_file()
        ):
            raise NativeError("invalid_resource", "Resource argv requires an absolute local executable", 400)
        if not isinstance(definition.get("config", {}), dict):
            raise NativeError("invalid_resource", "Resource provider config must be an object", 400)
        validate_schema(definition.get("inputSchema", EMPTY_SCHEMA))
    return value


def public_definitions(value):
    return {name: definition.get("inputSchema", EMPTY_SCHEMA)
            for name, definition in value["resources"].items()}


def validate_public(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= 8:
        raise NativeError("invalid_resource", "Invalid native resource capability", 400)
    bounded_json(value)
    for name, schema in value.items():
        identifier(name)
        validate_schema(schema)
    return value


def descriptor(execution, generation, definitions):
    identifier(execution)
    identifier(generation)
    validate_public(definitions)
    return {
        "schema": DESCRIPTOR_SCHEMA, "version": 1, "capability": CAPABILITY,
        "executionId": execution, "generation": generation,
        "resources": {name: {
            "inputSchema": schema,
            "ensureCommand": [sys.executable, "-m", "agent_bridge", "native", "resource", "ensure", name, "--json"],
        } for name, schema in definitions.items()},
    }


def resource_result(request, *, value=None, error=None):
    result = {key: request[key] for key in ("executionId", "generation", "sessionId", "requestId", "resource")}
    result.update(schema=RESULT_SCHEMA, version=1, state="failed" if error else "ready")
    result["error" if error else "value"] = error if error else value
    bounded_json(result)
    return result


class ResourceMailbox:
    """Venue-local durable requests; delivery rides native status/control."""

    def __init__(self, store: NativeStore):
        self.store = store
        with store._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS native_resource_requests (
                execution TEXT NOT NULL, generation TEXT NOT NULL, request_id TEXT NOT NULL,
                signature TEXT NOT NULL, request TEXT NOT NULL, result TEXT,
                PRIMARY KEY(execution,generation,request_id))""")

    def submit(self, request):
        if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
            raise NativeError("invalid_resource_input", "Unknown native resource request fields", 400)
        for key in ("executionId", "generation", "requestId", "resource", "sessionId"):
            identifier(request.get(key))
        bounded_json(request)
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.store._decode(db.execute("SELECT * FROM executions WHERE id=?", (request["executionId"],)).fetchone())
            if (
                not row or row["generation"] != request["generation"]
                or row["data"].get("sessionId") != request["sessionId"]
                or row["state"] != "ready"
            ):
                raise NativeError("identity_mismatch", "Native resource request requires the registered running execution")
            schema = row["data"].get("resourceDefinitions", {}).get(request["resource"])
            if schema is None:
                raise NativeError("resource_forbidden", "Resource is not registered for this execution", 403)
            validate_input(request["input"], schema)
            key = (request["executionId"], request["generation"], request["requestId"])
            old = db.execute("SELECT signature,result FROM native_resource_requests WHERE execution=? AND generation=? AND request_id=?", key).fetchone()
            digest = signature(request)
            if old:
                if old["signature"] != digest:
                    raise NativeError("request_conflict", "Resource request ID was reused with different input")
                result = json.loads(old["result"]) if old["result"] else None
                if result and result["state"] == "failed":
                    db.execute("UPDATE native_resource_requests SET result=NULL WHERE execution=? AND generation=? AND request_id=?", key)
                return
            count = db.execute("SELECT count(*) FROM native_resource_requests WHERE execution=? AND generation=?", key[:2]).fetchone()[0]
            if count >= 256:
                raise NativeError("resource_request_limit", "Native execution resource request limit reached", 429)
            db.execute("INSERT INTO native_resource_requests VALUES (?,?,?,?,?,NULL)",
                       (*key, digest, json.dumps(request)))

    def pending(self, execution, generation):
        with self.store._connection() as db:
            rows = db.execute("SELECT request FROM native_resource_requests WHERE execution=? AND generation=? AND result IS NULL LIMIT 16",
                              (execution, generation)).fetchall()
        return [json.loads(row["request"]) for row in rows]

    def result(self, execution, generation, request_id):
        self.store.get(execution, generation)
        with self.store._connection() as db:
            row = db.execute("SELECT result FROM native_resource_requests WHERE execution=? AND generation=? AND request_id=?",
                             (execution, generation, request_id)).fetchone()
        if row is None:
            raise NativeError("not_found", "Resource request is not recorded", 404)
        return json.loads(row["result"]) if row["result"] else None

    def complete(self, execution, generation, result):
        bounded_json(result)
        self.store.get(execution, generation)
        if result.get("schema") != RESULT_SCHEMA or type(result.get("version")) is not int or result.get("version") != 1 or result.get("state") not in {"ready", "failed"}:
            raise NativeError("invalid_resource", "Invalid native resource result", 400)
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT request,result FROM native_resource_requests WHERE execution=? AND generation=? AND request_id=?",
                             (execution, generation, result.get("requestId"))).fetchone()
            if row is None:
                raise NativeError("not_found", "Resource request is not recorded", 404)
            request = json.loads(row["request"])
            if any(result.get(k) != request[k] for k in ("executionId", "generation", "sessionId", "resource", "requestId")):
                raise NativeError("identity_mismatch", "Native resource reply identity does not match")
            if row["result"] is not None and json.loads(row["result"]) != result:
                raise NativeError("request_conflict", "Native resource request already has another result")
            db.execute("UPDATE native_resource_requests SET result=? WHERE execution=? AND generation=? AND request_id=?",
                       (json.dumps(result), execution, generation, result["requestId"]))
        return {"recorded": True}


class HostResources:
    """Trusted local provider invocation; no resource work during registration."""

    def __init__(self, store, serving, invoke=None):
        self.store, self.serving = store, serving
        self.invoke = invoke or self._invoke
        self.locks = {}

    @staticmethod
    def key(name):
        return "_host_resource_" + identifier(name)

    def _reserve_attempt(self, execution, generation, name, request):
        key = self.key(name)
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = self.store._decode(db.execute("SELECT * FROM executions WHERE id=?", (execution,)).fetchone())
            if (
                not row or row["generation"] != generation or row["state"] != "ready"
                or row["data"].get("sessionId") != request["sessionId"] or not self.serving()
            ):
                raise NativeError("identity_mismatch", "Native ownership changed before resource admission")
            definition = row["data"]["spec"]["hostResources"]["resources"][name]
            inputs = validate_input(request["input"], definition.get("inputSchema", EMPTY_SCHEMA))
            previous = row["data"].get(key, {})
            digest = signature(inputs)
            if previous.get("inputSignature", digest) != digest:
                raise NativeError("resource_conflict", "Resource input is fixed after its first ensure")
            state = {**previous, "attempted": True, "owned": None, "released": False,
                     "inputSignature": digest, "input": inputs}
            row["data"][key] = state
            db.execute("UPDATE executions SET data=?,updated=? WHERE id=? AND generation=?",
                       (json.dumps(row["data"]), time.time(), execution, generation))
        return row, state

    async def _invoke(self, definition, payload, owner):
        from .session_host.launcher import host_spawn_kwargs

        packet = bounded_json({"argv": definition["argv"], "request": payload})
        env = dict(os.environ)
        for key in ("COPILOT_AGENT_SESSION_ID", "COPILOT_SESSION_ID", "SESSION_ID", "COPILOT_PLUGIN_ROOT",
                    "AGENT_BRIDGE_NATIVE_RESOURCES", "AGENT_BRIDGE_NATIVE_EXECUTION_ID", "AGENT_BRIDGE_NATIVE_GENERATION"):
            env.pop(key, None)
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "agent_bridge.native_resource_worker",
            cwd=owner, env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            **host_spawn_kwargs(),
        )
        async def read_reply():
            chunks = []
            size = 0
            while data := await process.stdout.read(min(4096, MAX_BYTES + 1 - size)):
                chunks.append(data)
                size += len(data)
                if size > MAX_BYTES:
                    raise NativeError("resource_provider_failed", "Host resource reply exceeds its limit", 502)
            return b"".join(chunks)

        try:
            process.stdin.write(packet)
            await process.stdin.drain()
            process.stdin.close()
            output = await asyncio.wait_for(read_reply(), 100)
            await asyncio.wait_for(process.wait(), 5)
            if len(output) > MAX_BYTES or process.returncode:
                raise NativeError("resource_provider_failed", "Host resource provider failed; owned state is preserved", 502)
            try:
                result = json.loads(output)
            except (ValueError, UnicodeError) as exc:
                raise NativeError("resource_provider_failed", "Host resource provider returned invalid JSON", 502) from exc
            if not isinstance(result, dict):
                raise NativeError("resource_provider_failed", "Host resource provider returned a non-object", 502)
            if result.get("workerError") in {"resource_busy", "resource_provider_failed"}:
                raise NativeError(result["workerError"], "Host resource operation is unavailable; retry the same request", 503)
            return result
        except (TimeoutError, asyncio.TimeoutError, OSError) as exc:
            raise NativeError("resource_provider_unavailable", "Host resource reply unavailable; owned state is preserved", 503) from exc
        finally:
            if not process.stdin.is_closing():
                process.stdin.close()
            if process.returncode is None:
                # The bounded worker retains its cross-generation lock and its
                # own provider timeout even if this controller disappears.
                async def reap():
                    try:
                        await asyncio.wait_for(process.communicate(), 105)
                    except (TimeoutError, asyncio.TimeoutError):
                        if process.returncode is None:
                            process.kill()
                        await process.wait()
                asyncio.create_task(reap())

    def _request(self, row, name, operation, state, inputs):
        return {
            "schema": PROVIDER_SCHEMA, "version": 1, "operation": operation,
            "executionId": row["id"], "generation": row["generation"], "resource": name,
            "operationId": signature({"execution": row["id"], "generation": row["generation"], "resource": name}),
            "stateDir": str(self.store.root / "host-resources" / row["id"] / row["generation"] / name),
            "config": row["data"]["spec"]["hostResources"]["resources"][name].get("config", {}),
            "input": inputs, "previous": state.get("receipt"),
        }

    @staticmethod
    def _validate_reply(payload, reply, operation):
        if not isinstance(reply, dict) or type(reply.get("version")) is not int or any(reply.get(k) != payload[k] for k in (
            "schema", "version", "executionId", "generation", "resource", "operationId",
        )) or reply.get("ok") is not True:
            raise NativeError("resource_provider_failed", "Host resource provider identity/result is invalid", 502)
        bounded_json(reply)
        if operation == "ensure":
            if type(reply.get("owned")) is not bool or not isinstance(reply.get("value"), dict) or not isinstance(reply.get("receipt"), dict):
                raise NativeError("resource_provider_failed", "Host resource readiness receipt is invalid", 502)
        elif reply.get("released") is not True:
            raise NativeError("resource_cleanup_unconfirmed", "Host resource release is not confirmed", 503)

    async def ensure(self, execution, generation, request):
        if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
            raise NativeError("invalid_resource_input", "Unknown native resource request fields", 400)
        name = identifier(request.get("resource"))
        lock = self.locks.setdefault((execution, generation, name), asyncio.Lock())
        async with lock:
            row = self.store.get(execution, generation)
            if not self.serving() or row["state"] != "ready" or request.get("sessionId") != row["data"].get("sessionId"):
                raise NativeError("identity_mismatch", "Host resource requires the current registered execution")
            if request.get("executionId") != execution or request.get("generation") != generation:
                raise NativeError("identity_mismatch", "Host resource request identity differs")
            identifier(request.get("requestId"))
            definitions = row["data"]["spec"].get("hostResources", {}).get("resources", {})
            if name not in definitions:
                raise NativeError("resource_forbidden", "Host resource is not allowlisted", 403)
            key = self.key(name)
            row, state = self._reserve_attempt(execution, generation, name, request)
            payload = self._request(row, name, "ensure", state, state["input"])
            reply = await self.invoke(definitions[name], payload, row["owner"])
            self._validate_reply(payload, reply, "ensure")
            state.update(owned=reply["owned"], receipt=reply["receipt"])
            self.store.update(execution, generation, allowed_states=("ready", "stopping", "unreachable", "unrepresented"),
                              **{key: state})
            return resource_result(request, value=reply["value"])

    async def cleanup(self, execution, generation):
        row = self.store.get(execution, generation)
        definitions = row["data"].get("spec", {}).get("hostResources", {}).get("resources", {})
        for name, definition in definitions.items():
            lock = self.locks.setdefault((execution, generation, name), asyncio.Lock())
            async with lock:
                row = self.store.get(execution, generation)
                state = row["data"].get(self.key(name), {})
                if not state.get("attempted") or state.get("released") or state.get("owned") is False:
                    continue
                if not self.serving():
                    raise NativeError("not_ready", "Resource cleanup belongs to the current controller", 503)
                payload = self._request(row, name, "release", state, state.get("input", {}))
                reply = await self.invoke(definition, payload, row["owner"])
                self._validate_reply(payload, reply, "release")
                self.store.update(execution, generation, **{self.key(name): {**state, "released": True}})


def validate_timeout(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not 1 <= value <= 120:
        raise NativeError("invalid_timeout", "Resource timeout must be in 1..120 seconds", 400)
    return value
