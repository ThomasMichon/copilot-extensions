"""Real TLS provider/token/resource consumer; only value-free evidence leaves it."""

from __future__ import annotations

import asyncio
import argparse
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

from fleet_manager.enrolled_identity import (
    EnrolledBinding, EnrolledIdentity, EnrolledIdentityVerifier,
)
from fleet_manager.runtime_identity import IdentityError, IdentityVerifier

from prepare import AUDIENCE, CLIENT, ENROLLED_PRINCIPAL, ENROLLED_SUBJECT, ISSUER, SIGNING_KID

ROOT = Path("/credentials")
TOKEN_ENDPOINT = ISSUER + "/api/oidc/token"
JWKS_ENDPOINT = ISSUER + "/jwks.json"
MAX_BODY = 65536
DEFAULT_PROFILE = "jwt-pop-v1"
ENROLLED_PROFILE = "enrolled-pop-v1"
PROFILES = (DEFAULT_PROFILE, ENROLLED_PROFILE)


class ProbeError(ValueError):
    """Never retains provider response text or credentials."""


def check_profile(profile: str) -> None:
    if profile not in PROFILES:
        raise ProbeError("unsupported-qualification-profile")


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


def token_traits(token: str, key: ECKey, *, profile: str = DEFAULT_PROFILE) -> dict:
    """Unverified traits only; the serving verifier still checks the actual JWT."""
    check_profile(profile)
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
    traits = {
        "access_alg_es256": header.get("alg") == "ES256",
        "access_type_at_jwt": header.get("typ") == "at+jwt",
        "audience_shape": ("string" if isinstance(claims.get("aud"), str)
                           else "array" if isinstance(claims.get("aud"), list) else "other"),
        "audience_exact": claims.get("aud") == AUDIENCE,
        "proof_binding_present": isinstance(binding, str),
        "proof_binding_matches": binding == key.thumbprint(),
        "traits_are_unverified": True,
    }
    if profile == ENROLLED_PROFILE:
        audience = claims.get("aud")
        traits.update(
            client_id_exact=claims.get("client_id") == CLIENT,
            subject_exact=claims.get("sub") == ENROLLED_SUBJECT,
            audience_allowed=audience == AUDIENCE or (
                isinstance(audience, list) and len(audience) == 1 and audience[0] == AUDIENCE),
        )
    return traits


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
                         token: str, jwks: dict, *, profile: str = DEFAULT_PROFILE) -> dict:
    check_profile(profile)
    expires = int(time.time()) + 120
    verifier: IdentityVerifier
    if profile == ENROLLED_PROFILE:
        # Fixed fixture approval, never an enrollment selected from token traits.
        verifier = EnrolledIdentityVerifier(
            issuer=ISSUER, audience=AUDIENCE, jwks=jwks,
            trust_valid_until=expires, clock=lambda: int(time.time()),
            allowed_audiences=(AUDIENCE,),
            enrollments=(EnrolledBinding(
                subject=ENROLLED_SUBJECT, client_id=CLIENT, principal=ENROLLED_PRINCIPAL,
                kind="caller", resource=AUDIENCE, key_source="qualification-proof",
                proof_key=key.thumbprint(), expires_at=expires,
            ),),
        )
    else:
        verifier = IdentityVerifier(
            issuer=ISSUER, audience=AUDIENCE, jwks=jwks,
            trust_valid_until=expires, clock=lambda: int(time.time()),
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
            if profile == ENROLLED_PROFILE:
                identity = verifier.verify(
                    supplied[len(prefix):], request.headers.get("DPoP", ""),
                    method=request.method, target_uri=resource, kind="caller")
                if (type(identity) is not EnrolledIdentity
                        or identity.profile != ENROLLED_PROFILE or identity.kind != "caller"
                        or identity.subject != ENROLLED_PRINCIPAL):
                    raise ProbeError("resource-identity-shape-refused")
            else:
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
            if not isinstance(nonce, str) or nonce != verifier.nonce:
                raise ProbeError("resource-challenge-failed")
        authenticated = headers(token, proof(key, "GET", resource, token=token, nonce=nonce))
        async with session.get(
            resource, headers=authenticated,
            allow_redirects=False,
        ) as response:
            outcome = await document(response)
            if response.status == 200 and outcome == {"result": "verified"}:
                if profile == ENROLLED_PROFILE:
                    negatives = await negative_checks(
                        session, token, resource, nonce, authenticated)
                    return {"result": "verified", "resource_tls_verified": True,
                            "negative_checks": negatives}
                return {"result": "verified", "resource_tls_verified": True}
            if response.status == 401 and outcome.get("result") == "refused":
                return {"result": "incompatible", "resource_tls_verified": True,
                        "identity_error": outcome["code"],
                        **({"negative_checks": empty_checks()} if profile == ENROLLED_PROFILE
                           else {})}
            raise ProbeError("resource-response-refused")
    finally:
        await runner.cleanup()


def empty_checks() -> dict[str, bool]:
    return dict.fromkeys(("wrong_key_refused", "missing_proof_refused", "replay_refused"), False)


async def negative_checks(session: aiohttp.ClientSession, token: str,
                          resource: str, nonce: str,
                          authenticated: dict[str, str]) -> dict[str, bool]:
    """Copied original token cannot substitute for the fixed enrolled private key."""
    wrong_key = ECKey.generate_key("P-256")
    checks = (
        ("wrong_key_refused", headers(token, proof(
            wrong_key, "GET", resource, token=token, nonce=nonce)), "invalid-key-binding"),
        ("missing_proof_refused", headers(token, ""), "invalid-credential"),
        ("replay_refused", authenticated, "proof-replayed"),
    )
    result = empty_checks()
    for name, supplied, code in checks:
        async with session.get(resource, headers=supplied, allow_redirects=False) as response:
            outcome = await document(response)
            if response.status != 401 or outcome != {"result": "refused", "code": code}:
                raise ProbeError("resource-negative-check-failed")
        result[name] = True
    return result


async def qualify(profile: str = DEFAULT_PROFILE) -> dict:
    check_profile(profile)
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
        for attempt in range(1 if profile == ENROLLED_PROFILE else 2):
            async with session.post(
                TOKEN_ENDPOINT, data=data,
                headers={} if profile == ENROLLED_PROFILE else {
                    "DPoP": proof(key, "POST", TOKEN_ENDPOINT, nonce=nonce)},
                allow_redirects=False,
            ) as response:
                result = await document(response)
                if response.status == 200:
                    break
                challenge = response.headers.get("DPoP-Nonce")
                if (profile == DEFAULT_PROFILE and attempt == 0 and response.status in (400, 401)
                        and result.get("error") == "use_dpop_nonce"
                        and isinstance(challenge, str) and 0 < len(challenge) <= 256):
                    nonce = challenge
                    continue
                raise ProbeError("provider-token-request-failed")
        token = result.get("access_token")
        if not isinstance(token, str):
            raise ProbeError("provider-access-token-missing")
        traits = token_traits(token, key, profile=profile)
        token_type = result.get("token_type")
        traits["token_type_dpop"] = isinstance(token_type, str) and token_type.lower() == "dpop"
        try:
            if profile == DEFAULT_PROFILE:
                outcome = await resource_check(session, key, token, jwks)
            else:
                outcome = await resource_check(session, key, token, jwks, profile=profile)
        except IdentityError as exc:
            outcome = {"result": "incompatible", "identity_error": exc.code,
                       "resource_tls_verified": False,
                       **({"negative_checks": empty_checks()} if profile == ENROLLED_PROFILE
                          else {})}
        if (profile == DEFAULT_PROFILE and outcome["result"] == "verified"
                and not traits["token_type_dpop"]):
            outcome.update(result="incompatible", identity_error="provider-token-type")
        return {
            **outcome, "traits": traits, "provider_tls_verified": True,
            "profile": profile, "grant": "client_credentials",
            "human_flow_exercised": False, "enrollment_qualified": False,
            "policy_qualified": False, "production_qualified": False,
        }


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default=DEFAULT_PROFILE)
    args = parser.parse_args(argv)
    try:
        result = await asyncio.wait_for(qualify(args.profile), timeout=100)
    except ProbeError as exc:
        result = {"result": "fail", "code": str(exc)}
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError,
            KeyError, TypeError, JoseError):
        result = {"result": "fail", "code": "provider-probe-failed"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["result"] in ("verified", "incompatible") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
