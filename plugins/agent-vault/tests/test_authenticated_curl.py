"""Destination and caller-option refusals happen before any credential use."""

from __future__ import annotations

import argparse
import base64
import importlib.metadata
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import quote

import pytest

from agent_vault import authenticated_curl as http
from agent_vault import config
from agent_vault import extensions as ext
from agent_vault.curl_arguments import parse_arguments
from agent_vault.extensions import ExtensionRegistry
from agent_vault.http_bindings import (
    BindingError,
    HttpBinding,
    binding_file,
    destination,
    read_bindings,
    select_binding,
)

pytestmark = [pytest.mark.portfolio_tier("T1"), pytest.mark.effect("filesystem")]
URL = "https://api.example.test/v1/items?offset=2"
SECRET = "synthetic-http-credential"


def binding(scheme: str = "bearer") -> HttpBinding:
    return HttpBinding(destination("https://api.example.test/v1/"), "API/service", scheme)


def document(path: Path, **updates: object) -> Path:
    item = {
        "url_prefix": "https://api.example.test/v1/",
        "entry": "API/service",
        "scheme": "bearer",
        "allow_injection": True,
        **updates,
    }
    path.write_text(json.dumps({"version": 1, "bindings": [item]}), encoding="utf-8")
    return path


def test_exact_origin_and_boundary_path(tmp_path: Path) -> None:
    bindings = read_bindings(document(tmp_path / "bindings.json"))
    assert select_binding(URL, bindings).entry == "API/service"
    assert select_binding("https://API.EXAMPLE.TEST:443/v1", bindings) == bindings[0]
    for url in (
        "https://api.example.test/v10/items",
        "https://api.example.test/v1-other",
        "https://api.example.test:444/v1/items",
        "http://api.example.test/v1/items",
        "https://other.api.example.test/v1/items",
    ):
        with pytest.raises(BindingError, match="no binding"):
            select_binding(url, bindings)


@pytest.mark.parametrize(
    "url",
    [
        "https://user@api.example.test/v1/",
        "https://api.example.test/v1/../admin",
        "https://api.example.test/v1/%2e%2e/admin",
        "https://api.example.test/v1/%252e%252e/admin",
        "https://api.example.test/v1//items",
        "https://api.example.test/v1/.;/admin",
        "https://api.example.test/v1/\\admin",
        "https://api.example.test./v1/",
        "https://api.example.test:0/v1/",
        "https://api.example.test:65536/v1/",
        "https://api.example.test/v1/#fragment",
        "https://api.example.test/v1/#",
        "file:///v1/",
    ],
)
def test_ambiguous_urls_are_refused(url: str) -> None:
    with pytest.raises(BindingError):
        destination(url)


def test_disabled_missing_and_overlapping_bindings_fail_closed(tmp_path: Path) -> None:
    path = document(tmp_path / "bindings.json", allow_injection=False)
    with pytest.raises(BindingError, match="no binding"):
        select_binding(URL, read_bindings(path))
    with pytest.raises(BindingError, match="no binding"):
        select_binding(URL, read_bindings(tmp_path / "missing.json"))
    broad = HttpBinding(destination("https://api.example.test/"), "API/other", "token")
    with pytest.raises(BindingError, match="ambiguous binding"):
        select_binding(URL, [binding(), broad])


@pytest.mark.parametrize(
    "updates",
    [
        {"allow_injection": "true"},
        {"allow_injection": 1},
        {"scheme": ["bearer"]},
        {"scheme": "unknown"},
        {"entry": "bare-name"},
        {"entry": " API/service"},
        {"url_prefix": "https://api.example.test/v1/?scope=admin"},
        {"url_prefix": "https://api.example.test/v1/?"},
        {"unknown": True},
    ],
)
def test_malformed_policy_never_enables_an_entry(tmp_path: Path, updates: dict) -> None:
    with pytest.raises(BindingError):
        read_bindings(document(tmp_path / "bindings.json", **updates))


def test_duplicate_opt_in_key_is_rejected(tmp_path: Path) -> None:
    path = document(tmp_path / "bindings.json")
    path.write_text(
        path.read_text().replace(
            '"allow_injection": true', '"allow_injection": false, "allow_injection": true'
        )
    )
    with pytest.raises(BindingError):
        read_bindings(path)


def test_native_policy_precedes_extension_and_ignores_repository_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_global_config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        config, "load_global_config", lambda **kwargs: {"http_bindings": "native.json"}
    )
    registry = Mock()
    registry.collect_config.side_effect = AssertionError("unused extension must not be consulted")
    monkeypatch.setattr("agent_vault.extensions.get_registry", lambda: registry)
    (tmp_path / ".agent-vault.json").write_text('{"http_bindings":"wrong.json"}')
    assert binding_file() == tmp_path / "native.json"
    registry.collect_config.assert_not_called()


def test_extension_policy_and_absent_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr("agent_vault.extensions._REGISTRY", registry)
    monkeypatch.setattr(config, "load_global_config", lambda **kwargs: {})
    monkeypatch.setattr(config, "_global_config_dir", lambda: tmp_path)
    assert binding_file() == tmp_path / "http-bindings.json"
    registry.register_config_source(
        lambda cwd: {"http_bindings": str(tmp_path / "extension.json")}
    )
    assert binding_file() == tmp_path / "extension.json"


def test_extension_receives_selected_configuration_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = ExtensionRegistry()
    registry._loaded = True
    monkeypatch.setattr("agent_vault.extensions._REGISTRY", registry)
    monkeypatch.setattr(config, "load_global_config", lambda **kwargs: {})
    selected = str(tmp_path / "selected")
    monkeypatch.setattr(config, "configuration_source", lambda cwd: selected)
    received: list[str | None] = []

    def source(cwd: str | None) -> dict[str, str]:
        received.append(cwd)
        return {"http_bindings": str(tmp_path / "extension.json")}

    registry.register_config_source(source)
    assert binding_file() == tmp_path / "extension.json"
    assert received == [selected]


@pytest.mark.parametrize("value", [None, False, "", [], {}])
def test_present_invalid_native_policy_cannot_select_a_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    value: object,
) -> None:
    document(tmp_path / "http-bindings.json")
    monkeypatch.setattr(config, "_global_config_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "load_global_config", lambda **kwargs: {"http_bindings": value})
    with pytest.raises(BindingError):
        binding_file()


def test_failed_extension_policy_cannot_fall_through_to_another_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    document(tmp_path / "http-bindings.json")
    registry = ExtensionRegistry()
    registry._loaded = True

    def failed_source(cwd: str | None) -> dict:
        raise RuntimeError(SECRET)

    registry.register_config_source(failed_source)
    monkeypatch.setattr("agent_vault.extensions._REGISTRY", registry)
    monkeypatch.setattr(config, "load_global_config", lambda **kwargs: {})
    monkeypatch.setattr(config, "_global_config_dir", lambda: tmp_path)
    with pytest.raises(BindingError, match="configuration could not be resolved"):
        binding_file()
    assert SECRET not in caplog.text


@pytest.mark.parametrize("failure", [
    "module", "callable", "register", "discovery", "entry-point", "entry-point-callable",
])
def test_incomplete_extension_loading_refuses_http_but_preserves_legacy_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    failure: str,
) -> None:
    document(tmp_path / "http-bindings.json")
    entry_points = Mock(return_value=SimpleNamespace(select=lambda **kwargs: []))
    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)
    monkeypatch.setenv(ext.EXTENSIONS_ENV, "")
    failed = Mock(side_effect=RuntimeError("synthetic extension failure"))
    if failure in {"module", "callable", "register"}:
        monkeypatch.setenv(ext.EXTENSIONS_ENV, "synthetic_http_policy")
        loader = failed if failure == "module" else Mock(
            return_value=SimpleNamespace(register=None if failure == "callable" else failed)
        )
        monkeypatch.setattr(ext.importlib, "import_module", loader)
    elif failure == "discovery":
        entry_points.side_effect = RuntimeError("synthetic discovery failure")
    else:
        point = SimpleNamespace(
            name="synthetic-http-policy",
            load=failed if failure == "entry-point" else lambda: None,
        )
        entry_points.return_value = SimpleNamespace(select=lambda **kwargs: [point])

    registry = ext.load_extensions(ExtensionRegistry())
    registry.register_config_source(lambda cwd: {"group": "legacy-compatible"})
    assert registry.collect_config(None)["group"] == "legacy-compatible"
    monkeypatch.setattr(ext, "_REGISTRY", registry)
    monkeypatch.setattr(config, "load_global_config", lambda **kwargs: {})
    monkeypatch.setattr(config, "_global_config_dir", lambda: tmp_path)
    read = Mock(side_effect=AssertionError("credential read is forbidden"))
    monkeypatch.setattr(http, "read_credential", read)
    assert http.command(argparse.Namespace(curl_arguments=["--", URL], prompt=False)) == 2
    read.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == "" and "configuration could not be resolved" in captured.err

    monkeypatch.setattr(
        config, "load_global_config", lambda **kwargs: {"http_bindings": "owner.json"}
    )
    assert binding_file() == tmp_path / "owner.json"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--", "-L", URL],
        ["--", "--location-trusted", URL],
        ["--", "--config", "-", URL],
        ["--", "-Kconfig", URL],
        ["--", "--trace", "-", URL],
        ["--", "-sv", URL],
        ["--", "--dump-header", "headers.txt", URL],
        ["--", "-o", "response.txt", URL],
        ["--", "--output-dir", ".", URL],
        ["--", "--proxy", "http://other.example.test", URL],
        ["--", "--resolve", "api.example.test:443:127.0.0.1", URL],
        ["--", "--connect-to", "api.example.test:443:other.example.test:443", URL],
        ["--", "--netrc", URL],
        ["--", "-u", "other:password", URL],
        ["--", "-H", "Authorization: caller-value", URL],
        ["--", "-H", "Host: other.example.test", URL],
        ["--", "-H", "@headers.txt", URL],
        ["--", "-H", "Accept: json\r\nAuthorization: caller-value", URL],
        ["--", "-d", "@-", URL],
        ["--", "--data-binary", "@/proc/self/fd/0", URL],
        ["--", "--request-target", "/admin", URL],
        ["--", "--next", URL],
        ["--", URL, "https://other.example.test/"],
        ["--", "--write-out", "%{header_json}", URL],
        ["--", "--write-out", "%output{file.txt}", URL],
        ["--", "--requ", "GET", URL],
        ["--", "--max-time", "nan", URL],
        [URL],
    ],
)
def test_unsafe_options_are_refused_before_credential_lookup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    arguments: list[str],
) -> None:
    read = Mock(side_effect=AssertionError("credential read is forbidden"))
    monkeypatch.setattr(http, "read_credential", read)
    assert http.command(argparse.Namespace(curl_arguments=arguments, prompt=False)) == 2
    read.assert_not_called()
    assert capsys.readouterr().out == ""


def test_supported_operands_remain_data_not_curl_options() -> None:
    args = parse_arguments(
        [
            "--",
            "-fsS",
            "-XPOST",
            "-HAccept: application/json",
            "--data-raw",
            "@- --location",
            "-w",
            r"\n%{http_code}\n",
            "--url=" + URL,
        ]
    )
    assert args.url == URL and args.discard_errors
    assert args.argv == [
        "--request",
        "POST",
        "--header",
        "Accept: application/json",
        "--data-raw",
        "@- --location",
    ]


@pytest.mark.parametrize("scheme", ["token", "bearer", "basic"])
def test_real_auth_construction_is_unmasked(monkeypatch: pytest.MonkeyPatch, scheme: str) -> None:
    def read(selected: HttpBinding, field: str, prompt: bool) -> str:
        return "sample-user" if field == "username" else SECRET

    monkeypatch.setattr(http, "_credential", read)
    header, protected = http.authentication(binding(scheme))
    if scheme == "basic":
        assert base64.b64decode(header.split(" ", 1)[1]).decode() == "sample-user:" + SECRET
    else:
        assert header.split(" ", 1)[1] == SECRET
    assert SECRET in protected
    assert "*" * 6 not in header


def test_lookup_diagnostics_never_reach_the_caller(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def failed_read(args: object) -> SimpleNamespace:
        print(SECRET)
        print(SECRET, file=sys.stderr)
        logging.warning(SECRET)
        return SimpleNamespace(code=1, value=None, error=SECRET)

    monkeypatch.setattr(config, "resolve_context", lambda **kwargs: None)
    monkeypatch.setattr(http, "read_credential", failed_read)
    with pytest.raises(BindingError, match="Credential unavailable"):
        http.authentication(binding())
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    assert SECRET not in caplog.text


def test_protected_credential_cannot_be_copied_into_request_operands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    path = document(tmp_path / "bindings.json")
    monkeypatch.setattr(http, "binding_file", lambda: path)
    monkeypatch.setattr(http, "curl_binary", lambda: "not-launched")
    monkeypatch.setattr(config, "resolve_context", lambda **kwargs: None)
    monkeypatch.setattr(
        http, "read_credential", lambda args: SimpleNamespace(code=0, value=SECRET)
    )
    execute = AsyncMock()
    monkeypatch.setattr(http, "execute", execute)
    assert (
        http.command(
            argparse.Namespace(
                curl_arguments=["--", "--data-raw", SECRET, URL],
                prompt=False,
            )
        )
        == 2
    )
    execute.assert_not_called()
    output = capsys.readouterr()
    assert output.out == "" and SECRET not in output.err


@pytest.mark.parametrize(
    "echo",
    [
        SECRET.encode(),
        base64.b64encode(SECRET.encode()),
        (r"quoted:\"" + SECRET).encode(),
    ],
)
def test_credential_echo_withholds_entire_response(echo: bytes) -> None:
    with pytest.raises(BindingError, match="response withheld"):
        http.redact(b'{"echo":' + echo + b"}", [SECRET])
    assert http.redact(b'{"ok":true}', [SECRET]) == b'{"ok":true}'


@pytest.mark.parametrize("encoding", ["json", "url", "url-lower", "base64"])
def test_encoded_credential_echo_is_withheld(encoding: str) -> None:
    value = 'synthetic:/\u00e9\\"'
    encoded = {
        "json": json.dumps(value, ensure_ascii=True)[1:-1].encode(),
        "url": quote(value, safe="").encode(),
        "url-lower": quote(value, safe="").lower().encode(),
        "base64": base64.b64encode(value.encode()),
    }[encoding]
    assert value.encode() not in encoded
    with pytest.raises(BindingError, match="response withheld"):
        http.redact(encoded, [value])


@pytest.mark.parametrize("echo", [
    b"Synthetic%2fAbC%3aCredential",
    b"Synthetic%2FAbC%3aCredential",
    b'"Synthetic\\/AbC:Credential"',
    b'"\\u0053ynthetic\\/AbC:Credential"',
])
def test_encoding_normalization_preserves_literal_case(echo: bytes) -> None:
    value = "Synthetic/AbC:Credential"
    assert http._contains_credential(echo, [value])
    with pytest.raises(BindingError, match="response withheld"):
        http.redact(echo, [value])
    assert not http._contains_credential(echo.lower(), [value])


@pytest.mark.parametrize("uppercase_hex", [False, True])
def test_mixed_unicode_json_password_echo_is_withheld(uppercase_hex: bool) -> None:
    value = "synthetic-\u00e9\U0001f600-password"
    encoded = json.dumps({"echo": value})
    if uppercase_hex:
        encoded = encoded.replace("00e9", "00E9").replace("d83d", "D83D").replace("de00", "DE00")
    with pytest.raises(BindingError, match="response withheld"):
        http.redact(encoded.encode(), [value])


@pytest.mark.parametrize(("body", "suffix"), [
    (b"", "Synthetic%{http_code}"),
    (b"Synthetic", "%{http_code}"),
])
def test_rendered_output_is_screened_across_body_suffix_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture,
    body: bytes,
    suffix: str,
) -> None:
    path = document(tmp_path / "bindings.json")
    monkeypatch.setattr(http, "binding_file", lambda: path)
    monkeypatch.setattr(http, "curl_binary", lambda: "not-launched")
    monkeypatch.setattr(http, "_credential", lambda *args: "Synthetic200")
    monkeypatch.setattr(http, "execute", AsyncMock(return_value=http.CurlResult(body, 200, 0)))
    assert http.command(
        argparse.Namespace(curl_arguments=["--", "-w", suffix, URL], prompt=False)
    ) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "response withheld" in captured.err
    assert "Synthetic200" not in captured.err
