import json
from dataclasses import replace
from typing import Any

import pytest
from fleet_manager.gateway_service_context import ServicePolicyError
from fleet_manager.gateway_service_policy import ServiceAuthority
from joserfc import jwt
from joserfc.jwk import RSAKey

from gateway_entra import EntraApplication, EntraTrustError, EntraUserProvider, TrustSnapshot
from gateway_entra.trust import _public_keys

TENANT = "11111111-1111-4111-8111-111111111111"
AUDIENCE = "22222222-2222-4222-8222-222222222222"
USER = "33333333-3333-4333-8333-333333333333"
NOW = 2_000_000_000
TARGET = "https://gateway.example.com/control"


@pytest.fixture(scope="module")
def key() -> RSAKey:
    return RSAKey.generate_key(2048)


def prepare(key: RSAKey) -> tuple[EntraUserProvider, TrustSnapshot, dict[str, Any], ServiceAuthority]:
    application = EntraApplication(TENANT, AUDIENCE, "GatewayClient", "example.read")
    snapshot = TrustSnapshot(
        application.issuer, application.audience,
        _public_keys(json.dumps({"keys": [
            {**key.as_dict(private=False), "kid": "selected"},
        ]}).encode("ascii")),
        NOW, NOW + 300,
    )
    provider = EntraUserProvider(application)
    provider.publish(snapshot)
    policy: dict[str, Any] = {
        "schema": "fleet-manager.gateway-service-policy", "version": 5,
        "manifest": {
            "schema": "fleet-manager.gateway-manifest", "version": 2,
            "fleet": "example", "gateway": "gateway", "revision": 1,
            "roles": ["control"], "key_sources": [],
            "slots": [{
                "name": "local-control", "role": "control", "delivery": "local",
                "routes": [{
                    "prefix": "/control", "methods": ["POST"], "capabilities": ["http"],
                }],
            }],
        },
        "service": None,
        "user_callers": provider.user_callers(
            mappings=[{"subject": USER, "principal": "operator-a"}],
            callers=[{"principal": "operator-a", "slots": ["local-control"], "methods": ["POST"]}],
        ),
    }
    authority = ServiceAuthority(
        fleet="example", gateway="gateway", source=lambda: json.dumps(policy).encode("ascii"),
        clock=lambda: NOW, user_identity_provider=provider.identity_provider,
    )
    authority.reload()
    return provider, snapshot, policy, authority


def token(key: RSAKey, **changes: Any) -> str:
    application = EntraApplication(TENANT, AUDIENCE, "GatewayClient", "example.read")
    return jwt.encode(
        {"alg": "RS256", "typ": "JWT", "kid": "selected"},
        {"iss": application.issuer, "aud": AUDIENCE, "iat": NOW, "exp": NOW + 200,
         "tid": TENANT, "oid": USER, "roles": ["GatewayClient"], "scp": "example.read", **changes},
        key,
    )


def authenticate(authority: ServiceAuthority, value: str) -> Any:
    return authority.authenticate_user_caller(
        value, method="POST", target_uri=TARGET, slot="local-control", request_method="POST",
    )


def test_actual_common_verifier_admits_only_the_explicit_mapped_user(key: RSAKey) -> None:
    _, _, _, authority = prepare(key)
    grant = authenticate(authority, token(key))
    assert authority.revalidate_caller(grant).principal == "operator-a"
    assert authority.release_caller(grant)


@pytest.mark.parametrize("changes", [
    {"tid": [TENANT]},
    {"roles": "GatewayClient"},
    {"tid": AUDIENCE},
    {"roles": ["unrelated"]},
    {"oid": AUDIENCE},
    {"aud": TENANT},
    {"scp": ["example.read"]},
    {"scp": "unrelated.read"},
], ids=["tenant-list", "role-scalar", "wrong-tenant", "wrong-role", "unmapped-user",
        "wrong-audience", "scope-list", "wrong-scope"])
def test_real_signed_token_preserves_tenant_role_shape_and_application_checks(
    key: RSAKey, changes: dict[str, Any],
) -> None:
    _, _, _, authority = prepare(key)
    with pytest.raises(ServicePolicyError):
        authenticate(authority, token(key, **changes))


def test_signed_id_token_with_matching_audience_user_and_role_cannot_gain_a_grant(key: RSAKey) -> None:
    _, _, _, authority = prepare(key)
    application = EntraApplication(TENANT, AUDIENCE, "GatewayClient", "example.read")
    id_token = jwt.encode(
        {"alg": "RS256", "typ": "JWT", "kid": "selected"},
        {"iss": application.issuer, "aud": AUDIENCE, "iat": NOW, "exp": NOW + 200,
         "tid": TENANT, "oid": USER, "roles": ["GatewayClient"], "nonce": "authentication-request"},
        key,
    )
    with pytest.raises(ServicePolicyError, match="^required-claim-denied$"):
        authenticate(authority, id_token)


def test_current_caller_withdrawal_revokes_route(key: RSAKey) -> None:
    _, _, policy, authority = prepare(key)
    grant = authenticate(authority, token(key))
    policy["manifest"]["revision"] += 1
    policy["user_callers"]["mappings"] = []
    policy["user_callers"]["callers"] = []
    authority.reload()
    with pytest.raises(ServicePolicyError):
        authority.revalidate_caller(grant)


def test_snapshot_preparation_refuses_another_reference_without_rewriting_trust(key: RSAKey) -> None:
    provider, snapshot, policy, _ = prepare(key)
    identity = policy["user_callers"]["identity"]
    original = provider.identity_provider.prepare(
        json.dumps(identity["configuration"], sort_keys=True, separators=(",", ":")).encode("ascii"),
    )
    assert original.jwks == snapshot.jwks
    with pytest.raises(ValueError, match="^provider-trust-unavailable$"):
        provider.identity_provider.prepare(b'{"snapshot":"not-the-selected-snapshot"}')


def test_unselected_application_snapshot_cannot_replace_selected_public_trust(key: RSAKey) -> None:
    provider, snapshot, policy, _ = prepare(key)
    changed = replace(snapshot, audience=TENANT)
    with pytest.raises(EntraTrustError, match="^invalid-public-trust$"):
        provider.publish(changed)
    assert provider.user_callers(
        mappings=policy["user_callers"]["mappings"], callers=policy["user_callers"]["callers"],
    ) == policy["user_callers"]
