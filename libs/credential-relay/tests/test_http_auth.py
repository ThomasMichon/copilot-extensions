"""Source-backed HTTP auth stays explicitly scoped and non-persistent."""

from dataclasses import replace

import pytest

from credential_relay.http_auth import HttpAuthContext, SourceHttpAuth


CONTEXT = HttpAuthContext("https://example.com", "synthetic", "generation")
ENDPOINT = "https://example.com/acp"


class Source:
    name = "synthetic"

    def __init__(self, response="token=synthetic-credential\n\n", *, supported=True):
        self.response = response
        self.supported = supported
        self.support_calls = []
        self.resolve_calls = []

    def supports(self, action, fields):
        self.support_calls.append((action, dict(fields)))
        return self.supported

    async def resolve(self, action, fields, *, timeout):
        self.resolve_calls.append((action, dict(fields), timeout))
        return self.response


@pytest.mark.asyncio
@pytest.mark.parametrize("credential_field", ["token", "password"])
async def test_legacy_defaults_preserve_header_fields_and_no_cache(credential_field):
    source = Source(response=credential_field + "=synthetic-credential\n\n")
    auth = SourceHttpAuth(source, CONTEXT, ENDPOINT)
    scheme = "Bear" + "er "
    expected = (scheme + "synthetic-credential").encode("ascii")
    headers = await auth.headers(CONTEXT)
    assert {key: value.encode("ascii") for key, value in headers.items()} == {
        "Authorization": expected,
    }
    fields = {
        "protocol": "https",
        "host": "example.com",
        "path": "acp",
        "username": "synthetic",
    }
    assert source.support_calls == [("get", fields)]
    assert source.resolve_calls == [("get", fields, 15.0)]
    await auth.invalidate(CONTEXT)
    assert len(source.resolve_calls) == 1
    await auth.headers(CONTEXT)
    assert source.resolve_calls == [("get", fields, 15.0)] * 2
    assert source.support_calls == [("get", fields)] * 2


@pytest.mark.parametrize(
    "changes",
    [{"resource": "https://example.com/resource"}, {"scope": "read"}],
)
def test_legacy_default_refuses_scoped_context(changes):
    with pytest.raises(ValueError, match="Unsupported HTTP credential scope"):
        SourceHttpAuth(Source(), replace(CONTEXT, **changes), ENDPOINT)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resource,scope",
    [
        ("https://example.com/resource", "read write"),
        ("https://example.com/resource", ""),
        ("", "read"),
        ("", ""),
    ],
)
async def test_opt_in_forwards_exact_scope_and_custom_scheme(resource, scope):
    source = Source()
    context = replace(CONTEXT, resource=resource, scope=scope)
    auth = SourceHttpAuth(
        source, context, ENDPOINT, scheme="Token", scoped=True, timeout=3.0,
    )
    prefix = "To" + "ken "
    assert await auth.headers(context) == {
        "Authorization": prefix + "synthetic-credential",
    }
    expected = {
        "protocol": "https",
        "host": "example.com",
        "path": "acp",
        "username": "synthetic",
        "resource": resource,
        "scope": scope,
    }
    assert source.support_calls == [("get", expected)]
    assert source.resolve_calls == [("get", expected, 3.0)]


@pytest.mark.parametrize(
    "scheme",
    [
        "", " ", " Token", "Token ", "To ken", "Token\t", "Token\r", "Token\n",
        "Token\x00", "Token\x1f", "Token\x7f", "Tökén", "Token:", "Token/",
        "Token{token}", None,
    ],
)
def test_scheme_refuses_non_http_tokens(scheme):
    with pytest.raises(ValueError, match="Invalid HTTP credential scheme"):
        SourceHttpAuth(Source(), CONTEXT, ENDPOINT, scheme=scheme)


@pytest.mark.asyncio
async def test_scheme_accepts_rfc_token_ascii_punctuation():
    scheme = "AZaz09!#$%&'*+-.^_`|~"
    auth = SourceHttpAuth(Source(), CONTEXT, ENDPOINT, scheme=scheme)
    prefix = scheme + " "
    assert await auth.headers(CONTEXT) == {
        "Authorization": prefix + "synthetic-credential",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "https://other.example.com"},
        {"identity": "other"},
        {"generation": "other"},
        {"resource": "https://example.com/other"},
        {"scope": "other"},
    ],
)
async def test_exact_context_binds_headers_and_invalidation(scoped, changes):
    source = Source()
    context = (
        replace(CONTEXT, resource="https://example.com/resource", scope="read")
        if scoped else CONTEXT
    )
    auth = SourceHttpAuth(source, context, ENDPOINT, scoped=scoped)
    other = replace(context, **changes)
    with pytest.raises(PermissionError, match="Credential context mismatch"):
        await auth.headers(other)
    with pytest.raises(PermissionError, match="Credential context mismatch"):
        await auth.invalidate(other)
    assert not source.support_calls
    assert not source.resolve_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
async def test_declined_source_is_not_resolved(scoped):
    source = Source(supported=False)
    auth = SourceHttpAuth(source, CONTEXT, ENDPOINT, scoped=scoped)
    with pytest.raises(PermissionError, match="Credential source declined"):
        await auth.headers(CONTEXT)
    assert not source.resolve_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize(
    "response,error",
    [
        (None, "Credential source unavailable"),
        ("", "Credential source unavailable"),
        ("token=\n\n", "Credential source unavailable"),
        ("username=other\ntoken=synthetic-credential\n\n", "Credential identity mismatch"),
    ],
)
async def test_unavailable_or_wrong_identity_refused(scoped, response, error):
    auth = SourceHttpAuth(Source(response), CONTEXT, ENDPOINT, scoped=scoped)
    with pytest.raises(PermissionError, match=error):
        await auth.headers(CONTEXT)


@pytest.mark.parametrize("field", ["resource", "scope"])
@pytest.mark.parametrize("separator", ["\r", "\n"])
def test_scoped_fields_refuse_credential_protocol_injection(field, separator):
    context = replace(CONTEXT, **{field: "read" + separator + "username=other"})
    with pytest.raises(ValueError, match="Invalid credential context"):
        SourceHttpAuth(Source(), context, ENDPOINT, scoped=True)


@pytest.mark.parametrize("scoped", [False, True])
def test_origin_and_explicit_identity_checks_remain(scoped):
    with pytest.raises(ValueError, match="Unsupported HTTP credential scope"):
        SourceHttpAuth(Source(), CONTEXT, "https://other.example.com/acp", scoped=scoped)
    for identity in ("", " synthetic", "synthetic "):
        with pytest.raises(ValueError, match="Explicit credential identity is required"):
            SourceHttpAuth(
                Source(), replace(CONTEXT, identity=identity), ENDPOINT, scoped=scoped,
            )
