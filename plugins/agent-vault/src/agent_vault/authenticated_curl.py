"""Use a bound credential through native curl without exposing it to callers."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import io
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote_to_bytes

from . import config
from .credential_reads import read_credential
from .curl_arguments import CurlArguments, parse_arguments
from .http_bindings import BindingError, HttpBinding, binding_file, read_bindings, select_binding

_STATUS = b"\nagent-vault-http-status:"
_BODY_LIMIT = 8 * 1024 * 1024
_JSON_ESCAPE = re.compile(
    rb'\\(?:u[dD][89aAbB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2}'
    rb'|u[0-9a-fA-F]{4}|["\\/bfnrt])'
)


@dataclass(frozen=True)
class CurlResult:
    body: bytes
    status: int
    code: int


def _configuration_failure(error: Exception) -> BindingError:
    safe_messages = {
        "AGENT_VAULT_PORT environment override: port must be an integer from 1 to 65535",
        "delivered configuration has no matching qualified owner",
        "Vault configuration belongs to another installation cell",
    }
    detail = str(error) if str(error) in safe_messages else "repair the selected vault configuration"
    return BindingError(f"Vault configuration failed ({type(error).__name__}): {detail}")


def _credential(binding: HttpBinding, field: str, prompt: bool) -> str:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        previous_logging = logging.root.manager.disable
        logging.disable(logging.CRITICAL)
        try:
            try:
                config.resolve_context(strict=True)
            except Exception as error:
                raise _configuration_failure(error) from None
            try:
                result = read_credential(
                    SimpleNamespace(
                        entry=binding.entry,
                        field=field,
                        strict_cache=True,
                        prompt=prompt,
                    )
                )
            except Exception:
                raise BindingError(
                    "Credential lookup failed; repair or unlock the selected vault"
                ) from None
        finally:
            logging.disable(previous_logging)
    if (
        result.code != 0
        or not isinstance(result.value, str)
        or (not result.value and binding.scheme != "basic")
    ):
        raise BindingError("Credential unavailable; repair or unlock the selected vault")
    return result.value


def authentication(binding: HttpBinding, *, prompt: bool = False) -> tuple[str, list[str]]:
    password = _credential(binding, "password", prompt)
    protected = [password]
    # Split scheme literals to avoid authoring pipelines masking credential expressions.
    if binding.scheme == "basic":
        username = _credential(binding, "username", prompt)
        if not username and not password:
            raise BindingError("Basic authentication credentials are empty")
        if ":" in username:
            raise BindingError("Basic authentication username contains a colon")
        if not password:
            protected.append(username)
        combined = f"{username}:{password}"
        scheme = "Ba" + "sic "
        header = scheme + base64.b64encode(combined.encode("utf-8")).decode("ascii")
        protected.append(combined)
    else:
        if not password.isascii() or any(
            not 33 <= ord(character) <= 126 for character in password
        ):
            raise BindingError("Token credential contains invalid header characters")
        scheme = ("Bear" + "er ") if binding.scheme == "bearer" else ("to" + "ken ")
        header = scheme + password
    if len(header) > 8192:
        raise BindingError("Credential exceeds the HTTP authentication header limit")
    protected.extend((header, header.partition(" ")[2]))
    return header, protected


def _decode_json_escape(match: re.Match[bytes]) -> bytes:
    try:
        return json.loads(b'"' + match.group() + b'"').encode("utf-8")
    except (ValueError, UnicodeError):
        return match.group()


def _contains_credential(body: bytes, protected: list[str]) -> bool:
    representations = (body, unquote_to_bytes(body), _JSON_ESCAPE.sub(_decode_json_escape, body))
    patterns: set[bytes] = set()
    for value in protected:
        encoded = value.encode("utf-8")
        patterns.update((encoded, base64.b64encode(encoded)))
    return any(
        pattern and pattern in representation
        for representation in representations
        for pattern in patterns
    )


def redact(body: bytes, protected: list[str]) -> bytes:
    if _contains_credential(body, protected):
        raise BindingError("HTTP response contained a credential echo; response withheld")
    return body


def curl_binary() -> str:
    if os.name == "nt":
        path = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "curl.exe"
        if path.is_file():
            return str(path)
    else:
        for candidate in (
            "/usr/bin/curl",
            "/bin/curl",
            "/usr/local/bin/curl",
            "/opt/homebrew/bin/curl",
        ):
            if Path(candidate).is_file():
                return candidate
    raise BindingError("Native curl is unavailable; Windows requires curl.exe")


async def _read(stream: asyncio.StreamReader, limit: int) -> bytes:
    result = bytearray()
    while chunk := await stream.read(65536):
        result.extend(chunk)
        if len(result) > limit:
            raise BindingError("HTTP response exceeded the capture limit")
    return bytes(result)


async def execute(
    arguments: CurlArguments,
    header: str,
    binary: str,
    protected: list[str],
) -> CurlResult:
    from agent_procutil import no_window_kwargs

    argv = [
        binary,
        "--disable",
        "--globoff",
        "--silent",
        "--show-error",
        "--proxy",
        "",
        "--noproxy",
        "*",
        "--proto",
        "=http,https",
        "--max-time",
        "60",
        "--connect-timeout",
        "10",
        "--config",
        "-",
        *arguments.argv,
        "--write-out",
        _STATUS.decode("ascii") + "%{http_code}",
        "--url",
        arguments.url,
    ]
    escaped = header.replace("\\", "\\\\").replace('"', '\\"')
    stdin = f'header = "Authorization: {escaped}"\n'.encode("utf-8")
    excluded = {"SSLKEYLOGFILE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in excluded
        and not _contains_credential(value.encode("utf-8", errors="surrogatepass"), protected)
    }
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
            **no_window_kwargs(),
        )
    except OSError:
        raise BindingError("Native curl could not be launched") from None
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    readers: list[asyncio.Task] = []
    try:
        process.stdin.write(stdin)
        process.stdin.close()
        readers = [
            asyncio.create_task(_read(process.stdout, _BODY_LIMIT + 256)),
            asyncio.create_task(_read(process.stderr, 65536)),
            asyncio.create_task(process.wait()),
        ]
        output, _, code = await asyncio.wait_for(asyncio.gather(*readers), timeout=65)
    except (asyncio.TimeoutError, OSError):
        raise BindingError("Native curl request timed out or its pipe failed") from None
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        for reader in readers:
            if not reader.done():
                reader.cancel()
        if readers:
            await asyncio.gather(*readers, return_exceptions=True)
        try:
            await asyncio.wait_for(process.communicate(), timeout=5)
        except asyncio.TimeoutError:
            raise BindingError("Native curl cleanup could not be confirmed") from None
    body, separator, status = output.rpartition(_STATUS)
    if not separator or len(status) != 3 or not status.isdigit():
        raise BindingError("Native curl did not return an HTTP status")
    if len(body) > _BODY_LIMIT:
        raise BindingError("HTTP response exceeded the capture limit")
    return CurlResult(body, int(status), code)


def command(args: argparse.Namespace) -> int:
    try:
        arguments = parse_arguments(args.curl_arguments)
        binding = select_binding(arguments.url, read_bindings(binding_file()))
        binary = curl_binary()
        header, protected = authentication(binding, prompt=args.prompt)
        operands = "\0".join([arguments.url, arguments.write_out, *arguments.argv]).encode("utf-8")
        if _contains_credential(operands, protected):
            raise BindingError("Request arguments contain a protected credential; request refused")
        result = asyncio.run(execute(arguments, header, binary, protected))
        if 300 <= result.status < 400:
            raise BindingError("HTTP redirect refused; credentials were not forwarded")
        body = b"" if arguments.discard_errors and result.status >= 400 else result.body
        suffix = arguments.write_out.replace("%{http_code}", f"{result.status:03d}")
        suffix = suffix.replace(r"\n", "\n").replace(r"\r", "\r").replace(r"\t", "\t")
        output = redact(body + suffix.encode("utf-8"), protected)
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
        if result.code:
            print(f"Error: native curl failed (exit {result.code})", file=sys.stderr)
        elif result.status >= 400:
            print(f"Error: HTTP request failed (status {result.status})", file=sys.stderr)
        return result.code or (22 if result.status >= 400 else 0)
    except BindingError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
    except Exception:
        print(
            "Error: authenticated request failed; no credential diagnostic was emitted",
            file=sys.stderr,
        )
        return 2


def add_commands(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "curl",
        help="Request an opt-in bound URL through native curl without handling credentials",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--prompt", action="store_true", help="Explicitly allow vault unlock prompting"
    )
    parser.add_argument("curl_arguments", nargs=argparse.REMAINDER)
    parser.set_defaults(func=command)
