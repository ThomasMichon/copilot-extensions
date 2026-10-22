"""Real TLS provider/token/resource consumer; only value-free evidence leaves it."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from pathlib import Path
import secrets
import ssl
import time

import aiohttp
from aiohttp import web
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import ECKey

from fleet_manager.runtime_identity import IdentityError, IdentityVerifier

from prepare import AUDIENCE, CLIENT, ISSUER, SIGNING_KID

ROOT = Path("/credentials")
TOKEN_ENDPOINT = ISSUER + "/api/oidc/token"
JWKS_ENDPOINT = ISSUER + "/jwks.json"
MAX_BODY = 65536


class ProbeError(ValueError):
    """Never retains provider response text or credentials."""


def headers(token: str, proof: str) -> dict[str, str]:
    scheme = "DP" + "oP "
    return {"Authorization": scheme + token, "DPoP": proof}


def proof(key: ECKey, method: str, uri: str, *, token: str | None = None,
          nonce: str | None = None) -> str:
    claims = {"jti": secrets.token_urlsafe(24), "htm": method,
              "htu": uri, "iat": int(time.time())}
    if token is not None:
        claims["ath"] = base64.urlsafe_b64encode(
            hashlib.sha256(token.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    if nonce is not None:
        claims["nonce"] = nonce
    return jwt.encode(
        {"alg": "ES256", "typ": "dpop+jwt", "jwk": key.as_dict(private=False)},
        claims, key,
    )


async def document(response: aiohttp.ClientResponse) -> dict:
    raw = bytearray()
    async for chunk in response.content.iter_chunked(4096):
        raw.extend(chunk)
        if len(raw) > MAX_BODY:
            raise ProbeError("response-size-refused")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError):
        raise ProbeError("response-shape-refused") from None
    if not isinstance(result, dict):
        raise ProbeError("response-shape-refused")
    return result


def token_traits(token: str, key: ECKey) -> dict:
    """Unverified traits only; the serving verifier still checks the actual JWT."""
    if len(token) > 8192:
        raise ProbeError("token-size-refused")
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError
        header, claims = [
            json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
            for value in parts[:2]
        ]
        if not isinstance(header, dict) or not isinstance(claims, dict):
            raise ValueError
    except (ValueError, UnicodeError):
        raise ProbeError("token-shape-refused") from None
    cnf = claims.get("cnf")
    binding = cnf.get("jkt") if isinstance(cnf, dict) else None
    return {
        "access_alg_es256": header.get("alg") == "ES256",
        "access_type_at_jwt": header.get("typ") == "at+jwt",
        "audience_shape": ("string" if isinstance(claims.get("aud"), str)
                           else "array" if isinstance(claims.get("aud"), list) else "other"),
        "audience_exact": claims.get("aud") == AUDIENCE,
        "proof_binding_present": isinstance(binding, str),
        "proof_binding_matches": binding == key.thumbprint(),
        "traits_are_unverified": True,
    }


def trusted_subset(value: dict) -> dict:
    """Explicit fixture key selection from CA-authenticated, fixed-endpoint JWKS."""
    keys = value.get("keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= 16:
        raise ProbeError("provider-jwks-shape-refused")
    selected = [item for item in keys if isinstance(item, dict)
                and item.get("kid") == SIGNING_KID]
    if len(selected) != 1:
        raise ProbeError("provider-key-selection-refused")
    # No rewriting or token-directed key discovery; the SDK validates this exact key.
    return {"keys": selected}


async def resource_check(session: aiohttp.ClientSession, key: ECKey,
                         token: str, jwks: dict) -> dict:
    verifier = IdentityVerifier(
        issuer=ISSUER, audience=AUDIENCE, jwks=jwks,
        trust_valid_until=int(time.time()) + 120, clock=lambda: int(time.time()),
    )

    async def handle(request: web.Request) -> web.Response:
        if request.transport is None or request.transport.get_extra_info("ssl_object") is None:
            raise ProbeError("resource-transport-not-tls")
        supplied = request.headers.get("Authorization", "")
        if not supplied:
            return web.json_response({"result": "challenge"}, status=401,
                                     headers={"DPoP-Nonce": verifier.nonce})
        prefix = "DP" + "oP "
        if not supplied.startswith(prefix):
            return web.json_response({"result": "refused", "code": "scheme-refused"}, status=401)
        try:
            verifier.verify(supplied[len(prefix):], request.headers.get("DPoP", ""),
                            method=request.method, target_uri=resource)
        except IdentityError as exc:
            return web.json_response({"result": "refused", "code": exc.code}, status=401)
        return web.json_response({"result": "verified"})

    app = web.Application(client_max_size=1024)
    app.router.add_get("/resource", handle, allow_head=False)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(ROOT / "cert.pem", ROOT / "key.pem")
        await web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls).start()
        resource = "https://localhost:" + str(runner.addresses[0][1]) + "/resource"
        async with session.get(resource, allow_redirects=False) as response:
            if response.status != 401:
                raise ProbeError("resource-challenge-failed")
            nonce = response.headers.get("DPoP-Nonce")
            if nonce != verifier.nonce:
                raise ProbeError("resource-challenge-failed")
        async with session.get(
            resource, headers=headers(token, proof(key, "GET", resource, token=token, nonce=nonce)),
            allow_redirects=False,
        ) as response:
            outcome = await document(response)
            if response.status == 200 and outcome == {"result": "verified"}:
                return {"result": "verified", "resource_tls_verified": True}
            if response.status == 401 and outcome.get("result") == "refused":
                return {"result": "incompatible", "resource_tls_verified": True,
                        "identity_error": outcome["code"]}
            raise ProbeError("resource-response-refused")
    finally:
        await runner.cleanup()


async def qualify() -> dict:
    inputs = json.loads((ROOT / "inputs.json").read_bytes())
    key = ECKey.import_key(inputs["proof_key"])
    tls = ssl.create_default_context(cafile=ROOT / "ca.pem")
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    timeout = aiohttp.ClientTimeout(total=8)
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=tls), timeout=timeout, trust_env=False,
    ) as session:
        # Only readiness gets bounded retries. Token requests are never blindly retried.
        for attempt in range(20):
            try:
                async with session.get(ISSUER + "/.well-known/openid-configuration",
                                       allow_redirects=False) as response:
                    if response.status != 200:
                        raise ProbeError("provider-discovery-failed")
                    discovery = await document(response)
                break
            except aiohttp.ClientSSLError:
                raise ProbeError("provider-tls-failed") from None
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
                if attempt == 19:
                    raise ProbeError("provider-unavailable") from None
                await asyncio.sleep(1)
        if (discovery.get("issuer") != ISSUER
                or discovery.get("token_endpoint") != TOKEN_ENDPOINT
                or discovery.get("jwks_uri") != JWKS_ENDPOINT):
            raise ProbeError("provider-discovery-mismatch")
        async with session.get(JWKS_ENDPOINT, allow_redirects=False) as response:
            if response.status != 200:
                raise ProbeError("provider-jwks-failed")
            jwks = trusted_subset(await document(response))
        data = {"grant_type": "client_credentials", "client_id": CLIENT,
                "client_secret": inputs["client_secret"], "scope": "fixture.read",
                "audience": AUDIENCE}
        nonce = None
        for attempt in range(2):
            async with session.post(
                TOKEN_ENDPOINT, data=data,
                headers={"DPoP": proof(key, "POST", TOKEN_ENDPOINT, nonce=nonce)},
                allow_redirects=False,
            ) as response:
                result = await document(response)
                if response.status == 200:
                    break
                challenge = response.headers.get("DPoP-Nonce")
                if (attempt == 0 and response.status in (400, 401)
                        and result.get("error") == "use_dpop_nonce"
                        and isinstance(challenge, str) and 0 < len(challenge) <= 256):
                    nonce = challenge
                    continue
                raise ProbeError("provider-token-request-failed")
        token = result.get("access_token")
        if not isinstance(token, str):
            raise ProbeError("provider-access-token-missing")
        traits = token_traits(token, key)
        token_type = result.get("token_type")
        traits["token_type_dpop"] = isinstance(token_type, str) and token_type.lower() == "dpop"
        try:
            outcome = await resource_check(session, key, token, jwks)
        except IdentityError as exc:
            outcome = {"result": "incompatible", "identity_error": exc.code,
                       "resource_tls_verified": False}
        if outcome["result"] == "verified" and not traits["token_type_dpop"]:
            outcome.update(result="incompatible", identity_error="provider-token-type")
        return {
            **outcome, "traits": traits, "provider_tls_verified": True,
            "profile": "jwt-pop-v1", "grant": "client_credentials",
            "human_flow_exercised": False, "enrollment_qualified": False,
            "policy_qualified": False, "production_qualified": False,
        }


async def main() -> int:
    try:
        result = await asyncio.wait_for(qualify(), timeout=100)
    except ProbeError as exc:
        result = {"result": "fail", "code": str(exc)}
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError,
            KeyError, TypeError, JoseError):
        result = {"result": "fail", "code": "provider-probe-failed"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["result"] in ("verified", "incompatible") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
