"""Bounded real HTTPS/DPoP probes; stdout contains outcomes, never credentials."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import aiohttp
from joserfc.jwk import ECKey

from fleet_manager.gateway_auth import make_proof
from fleet_manager.gateway_service_connector import verified_tls

ORIGIN = "https://gateway:8443"
ROOT = Path("/credentials")


def auth_headers(access: str, proof: str) -> dict[str, str]:
    scheme = "DP" + "oP "
    return {"Authorization": scheme + access, "DPoP": proof}


class Probe:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def auth(self, path: str, subject: str, method: str = "GET") -> dict[str, str]:
        async with self.session.get(ORIGIN + "/_fleet/challenge") as response:
            if response.status != 200:
                raise RuntimeError("challenge-unavailable")
            nonce = (await response.json())["nonce"]
        access = (ROOT / (subject + ".token")).read_text(encoding="ascii")
        key = ECKey.import_key(json.loads((ROOT / (subject + ".key.json")).read_text()))
        proof = make_proof(key.get_op_key("sign"), access, nonce=nonce,
                           target_uri=ORIGIN + path, method=method)
        return auth_headers(access, proof)

    async def request(self, path: str, status: int, *, subject: str = "caller-a",
                      instance: str | None = None, html: bool = False) -> dict:
        selected = await self.auth(path, subject)
        async with self.session.get(ORIGIN + path, headers=selected) as response:
            if response.status != status:
                raise RuntimeError("unexpected-http-status")
            text = await response.text()
            if len(text) > 16384:
                raise RuntimeError("probe-response-limit")
            if html:
                if (not instance or response.content_type != "text/html"
                        or text != f"<html><body>{instance}</body></html>"):
                    raise RuntimeError("wrong-html-response")
                return {}
            value = json.loads(text)
            if instance and value.get("instance") != instance:
                raise RuntimeError("wrong-service-instance")
            return value

    async def check(self, mode: str) -> dict:
        if mode == "ready":
            async with self.session.get(ORIGIN + "/_fleet/challenge") as response:
                if response.status != 200:
                    raise RuntimeError("listener-not-ready")
        elif mode == "absent":
            for letter in ("a", "b"):
                value = await self.request("/api/" + letter, 503)
                if value.get("error") != "service-unavailable":
                    raise RuntimeError("absence-not-explicit")
        elif mode in ("routes", "replacement", "updated", "reconnected"):
            instance_a = "a-replacement" if mode in ("replacement", "reconnected") else "a-original"
            await self.request("/api/a", 200, instance=instance_a)
            prefix = "/v2/b" if mode in ("updated", "replacement", "reconnected") else "/api/b"
            await self.request(prefix, 200, instance="b-original")
            await self.request("/ui/a/index.html", 200, instance=instance_a, html=True)
            await self.request("/ui/b/index.html", 200, instance="b-original", html=True)
            if mode == "updated":
                await self.request("/api/b", 404)
        elif mode == "denials":
            await self.request("/api/b", 403, subject="caller-limited")
            await self.request("/api/a", 403, subject="service-a")
            path = "/_fleet/service-admit"
            selected = await self.auth(path, "service-a", "POST")
            access = (ROOT / "service-a.token").read_text(encoding="ascii")
            async with self.session.post(
                ORIGIN + path, json={"slot": "service-b", "access": access,
                                     "proof": selected["DPoP"]},
            ) as response:
                if response.status not in (401, 403):
                    raise RuntimeError("slot-hijack-not-denied")
        elif mode == "revoked":
            for path in ("/api/a", "/v2/b", "/ui/a/index.html"):
                await self.request(path, 403)
        elif mode == "stream":
            selected = await self.auth("/api/a/events", "caller-a")
            async with self.session.get(ORIGIN + "/api/a/events", headers=selected) as response:
                if response.status != 200:
                    raise RuntimeError("stream-unavailable")
                first = await asyncio.wait_for(response.content.readexactly(13), 5)
                if first != b"data: first\n\n":
                    raise RuntimeError("wrong-first-chunk")
                state = await self.request("/api/a/state", 200)
                if not state["started"] or state["finished"]:
                    raise RuntimeError("stream-was-buffered")
                await self.request("/api/a/release", 200)
                rest = await asyncio.wait_for(response.content.readexactly(12), 5)
                if rest != b"data: last\n\n":
                    raise RuntimeError("stream-not-completed")
                if await response.content.read(1):
                    raise RuntimeError("stream-extra-data")
            return {"result": "pass", "mode": mode,
                    "first_chunk_before_backend_finished": True, "first_chunk_bytes": 13}
        else:
            raise RuntimeError("unknown-probe-mode")
        return {"result": "pass", "mode": mode}


async def main(mode: str) -> dict:
    if mode == "idle":
        await asyncio.sleep(300)
        return {"result": "pass", "mode": mode}
    tls = verified_tls(str(ROOT / "ca.pem"))
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=tls, limit=4),
        timeout=aiohttp.ClientTimeout(total=15, connect=2, sock_read=6),
        trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
    ) as session:
        return await asyncio.wait_for(Probe(session).check(mode), 20)


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(main(sys.argv[1]))))
    except Exception:
        print('{"result":"fail","code":"synthetic-probe-failed"}')
        raise SystemExit(1) from None
