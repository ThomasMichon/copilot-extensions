"""Stdlib-only capture of held native UV feed settings, never approval."""
from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import io
import json
import netrc
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit, urlunsplit

import installer_dependency_policy as dependencies
from installer_feed_policy import _is_public_pypi_url
from installer_wheel_acquisition import AcquisitionUnavailable, WheelFeed, _name, _tls_record, _url
from installer_wheel_inputs import _check_deadline, _ordinary
from installer_wheel_snapshot import _open, _unchanged


class NativeFeedError(ValueError):
    """Unsupported, ambiguous or inconsistent native feed inputs."""


def _body(value: str, prefix: str, opening="{", closing="}") -> str:
    match = re.fullmatch(re.escape(prefix) + r"\s*" + re.escape(opening) + r".*", value, re.DOTALL)
    if match is None:
        raise NativeFeedError("native feed projection is unsupported")
    position = value.index(opening, len(prefix))
    body, end = dependencies._balanced(value, position, opening, closing)
    if value[end:].strip().strip(","):
        raise NativeFeedError("native feed projection has trailing data")
    return body


def _fields(body: str) -> dict[str, str]:
    result, position = {}, 0
    while position < len(body):
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*):\s*", body[position:])
        if match is None:
            if body[position:].strip().strip(","):
                raise NativeFeedError("native feed fields are unsupported")
            break
        name = match[1]
        if name in result:
            raise NativeFeedError("native feed projection has duplicate fields")
        position += match.end()
        start = position
        while position < len(body) and body[position] != ",":
            character = body[position]
            if character in "{([":
                _, position = dependencies._balanced(
                    body, position, character, {"{": "}", "(": ")", "[": "]"}[character],
                )
            elif character == '"':
                try:
                    _, used = json.JSONDecoder().raw_decode(body[position:])
                except (ValueError, RecursionError):
                    raise NativeFeedError("native feed string is unsupported") from None
                position += used
            else:
                position += 1
        result[name] = body[start:position].strip()
        position += 1
    return result


def _projection(projection: str, kind: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^" + re.escape(kind) + r" \{", projection))
    if len(matches) != 1:
        raise NativeFeedError("native feed settings are ambiguous")
    start = projection.index("{", matches[0].start())
    body, _ = dependencies._balanced(projection, start, "{", "}")
    return _fields(body)


def _string(value: str) -> str:
    try:
        result = json.loads(value)
        if not isinstance(result, str):
            raise ValueError
        return result
    except (TypeError, ValueError, RecursionError):
        raise NativeFeedError("native feed string is unsupported") from None


def _optional(value: str) -> str | None:
    return None if value == "None" else _string(_body(value, "Some", "(", ")").strip().rstrip(","))


def _index_url(value: str) -> tuple[str, str, bool]:
    verbatim = _fields(_body(_body(value, "Url", "(", ")").strip(), "VerbatimUrl"))
    safe = _fields(_body(verbatim.get("url", ""), "SafeUrl"))
    scheme, path = _string(safe.get("scheme", "")), _string(safe.get("path", ""))
    host = _body(safe.get("host", ""), "Some", "(", ")").strip().rstrip(",")
    if host.startswith("Domain("):
        host = _string(_body(host, "Domain", "(", ")").strip().rstrip(","))
    elif host.startswith("Ipv4("):
        host = _body(host, "Ipv4", "(", ")").strip().rstrip(",")
    elif host.startswith("Ipv6("):
        host = "[" + _body(host, "Ipv6", "(", ")").strip().rstrip(",") + "]"
    else:
        raise NativeFeedError("native feed host is unsupported")
    port = safe.get("port")
    if port != "None":
        port = _body(port or "", "Some", "(", ")").strip().rstrip(",")
        if not port.isdecimal():
            raise NativeFeedError("native feed port is unsupported")
        host += ":" + port
    query = _optional(safe.get("query", ""))
    if _optional(safe.get("fragment", "")) is not None:
        raise NativeFeedError("native feed index fragments are unsupported")
    location, _ = _url(urlunsplit((scheme, host, path, query or "", "")))
    return location, unquote(_string(safe.get("username", ""))), safe.get("password") != "None"


def _raw_indexes(transaction) -> list[tuple[str | None, str]]:
    environment = transaction.environment
    config = dependencies._toml(Path(environment["UV_CONFIG_FILE"]))
    result = []
    for item in config.get("index", []):
        if isinstance(item, dict) and isinstance(item.get("url"), str):
            result.append((item.get("name"), item["url"]))
    for key in ("index-url", "extra-index-url", "default-index"):
        value = config.get(key)
        if isinstance(value, str):
            result.append((None, value))
        elif isinstance(value, list):
            result.extend((None, item) for item in value if isinstance(item, str))
    for key in ("UV_DEFAULT_INDEX", "UV_INDEX_URL", "UV_INDEX", "UV_EXTRA_INDEX_URL"):
        for value in environment.get(key, "").split():
            name, separator, url = value.partition("=")
            result.append((name, url) if separator and "://" not in name else (None, value))
    expanded = []
    for name, value in result:
        def replace(match):
            if match[1] not in environment:
                raise NativeFeedError("retained feed URL has an unresolved environment reference")
            return environment[match[1]]
        expanded.append((name, re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)))
    return expanded


def _basic(user: str, password: str) -> tuple[tuple[str, str], ...]:
    if any(character in user + password for character in "\r\n") or ":" in user:
        raise NativeFeedError("native feed credentials are unsupported")
    encoded = base64.b64encode((user + ":" + password).encode("utf-8")).decode("ascii")
    scheme = "Ba" + "sic "
    return (("Authorization", scheme + encoded),)


def _netrc(environment, host, username):
    home = environment.get("USERPROFILE" if os.name == "nt" else "HOME")
    location = environment.get("NETRC")
    path = Path(location) if location else Path(home) / ".netrc" if home else None
    if path is None or not path.exists():
        return None
    try:
        before = _ordinary(path)
        if before.st_size > 1 << 20:
            raise NativeFeedError("native netrc credential input is oversized")
        with _open(path) as stream:
            opened = _unchanged(path, before, stream)
            content = stream.read((1 << 20) + 1)
            if len(content) > 1 << 20:
                raise NativeFeedError("native netrc credential input is oversized")
            _unchanged(path, before, stream, opened=opened)
        provider = netrc.netrc.__new__(netrc.netrc)
        provider.hosts, provider.macros = {}, {}
        provider._parse(str(path), io.StringIO(content.decode("utf-8")), False)
        credentials = provider.authenticators(host)
        if credentials is None:
            return None
        user, _, password = credentials
        if username and username != user:
            raise NativeFeedError("native netrc credential selection is ambiguous")
        return user, password
    except (OSError, ValueError, netrc.NetrcParseError):
        raise NativeFeedError("native netrc credential input cannot be captured") from None


def _credentials(index, transaction, location, username, password_present, raw, uv, deadline):
    name = index["name"]
    matches = []
    for candidate_name, candidate in raw:
        if candidate_name is not None and candidate_name != name:
            continue
        parsed = urlsplit(candidate)
        clean = urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, parsed.query, ""))
        if _url(clean)[0] != location:
            continue
        if parsed.username is not None:
            pair = unquote(parsed.username), unquote(parsed.password) if parsed.password is not None else None
            if not username or pair[0] == username:
                matches.append(pair)
    environment = transaction.environment
    if name is not None:
        stem = "UV_INDEX_" + re.sub(r"[^A-Za-z0-9]", "_", name).upper()
        user, password = environment.get(stem + "_USERNAME"), environment.get(stem + "_PASSWORD")
        if user is not None or password is not None:
            matches.append((user if user is not None else username, password))
    if len(set(matches)) > 1:
        raise NativeFeedError("native feed credential inputs are ambiguous")
    user, password = matches[0] if matches else (username, None)
    if index["authenticate"] == "Never":
        if password_present or password is not None:
            raise NativeFeedError("native feed never-authenticate policy conflicts with credentials")
        return ()
    if password_present and password is None:
        raise NativeFeedError("native embedded feed credentials could not be captured")
    if password is None:
        pair = _netrc(environment, urlsplit(location).hostname, user)
        if pair is not None:
            user, password = pair
    if password is None:
        command = [uv, "auth", "token", _url(location)[1]]
        if user:
            command.extend(["--username", user])
        _check_deadline(deadline)
        try:
            result = subprocess.run(
                command, env=environment, cwd=transaction.cwd, capture_output=True,
                text=True, stdin=subprocess.DEVNULL, timeout=deadline - time.monotonic(),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise NativeFeedError("native feed credential lookup failed or timed out") from None
        _check_deadline(deadline)
        if result.returncode == 0:
            if not user:
                raise AcquisitionUnavailable("native stored credentials require an explicit selected username")
            password = result.stdout.rstrip("\r\n")
        else:
            missing = "error: Failed to fetch credentials for `" + _url(location)[1] + "/`"
            if result.returncode != 2 or result.stderr.strip() != missing:
                raise NativeFeedError("native feed credential lookup failed") from None
    if password is None:
        if index["authenticate"] == "Always":
            raise AcquisitionUnavailable("native feed requires unavailable authentication")
        return ()
    return _basic(user, password)


def _configured_index(name, location, raw):
    for candidate_name, candidate in raw:
        if candidate_name is not None and candidate_name != name:
            continue
        parsed = urlsplit(candidate)
        clean = urlunsplit((parsed.scheme, parsed.netloc.rsplit("@", 1)[-1], parsed.path, parsed.query, ""))
        if _url(clean)[0] == location:
            return True
    return False


def _tls(network):
    if network.get("allow_insecure_host") != "[]":
        raise NativeFeedError("insecure native feed trust is not permitted")
    if network.get("system_certs") != "true" or sys.platform == "darwin":
        raise AcquisitionUnavailable("this native certificate provider is not yet supported")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    if sys.platform == "win32":
        for store in ("CA", "ROOT"):
            for certificate, encoding, trust in ssl.enum_certificates(store):
                if encoding == "x509_asn" and (trust is True or ssl.Purpose.SERVER_AUTH.oid in trust):
                    context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(certificate))
    else:
        paths = ssl.get_default_verify_paths()
        cafile = paths.openssl_cafile if Path(paths.openssl_cafile).is_file() else None
        capath = paths.openssl_capath if Path(paths.openssl_capath).is_dir() else None
        if cafile is None and capath is None:
            raise AcquisitionUnavailable("native platform certificate roots are unavailable")
        context.load_verify_locations(cafile=cafile, capath=capath)
    certificates = network.get("custom_certificates", "None")
    if certificates != "None":
        body = _body(_body(certificates, "Some", "(", ")").strip(), "Certificates", "(", ")").strip()
        body = _body(body, "", "[", "]")
        pattern = r"\s*CertificateDer\(\s*0x([0-9a-f]+),?\s*\),?"
        while body.strip():
            match = re.match(pattern, body)
            if match is None or len(match[1]) % 2:
                raise NativeFeedError("native custom certificate projection is unsupported")
            try:
                context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(bytes.fromhex(match[1])))
            except (ValueError, ssl.SSLError):
                raise NativeFeedError("native custom certificate projection is invalid") from None
            body = body[match.end():]
    return context


def _local_exclusions(value, transaction, wheels, selected):
    """Admit only the held exact exclusion file selected by the composition owner."""
    try:
        if not isinstance(selected, tuple) or not 1 <= len(selected) <= 256:
            raise NativeFeedError("native local exclusions need a bounded immutable selection")
        names = tuple(sorted(_name(name) for name in selected))
        if len(set(names)) != len(names) or wheels is None or any(
            _name(wheel["name"]) in names for wheel in wheels
        ):
            raise NativeFeedError("native local exclusions conflict with the selected wheel set")
        body = _body(value, "", "[", "]").strip()
        path = Path(_string(_body(body, "Local", "(", ")").strip().rstrip(",")))
        options = [Path(argument) for option, argument in dependencies._argument_pairs(transaction.argv[3:])
                   if option in ("--constraint", "--constraints", "-c")]
        if not path.is_absolute() or options != [path]:
            raise NativeFeedError("native local exclusion input differs from retained arguments")
        before = _ordinary(path)
        root = Path(transaction.environment["UV_CONFIG_FILE"]).parent.resolve(strict=True)
        if not path.resolve(strict=True).is_relative_to(root) or before.st_size > 1 << 20:
            raise NativeFeedError("native local exclusion input is outside its bounded retained root")
        with _open(path) as stream:
            opened = _unchanged(path, before, stream)
            content = stream.read((1 << 20) + 1)
            _unchanged(path, before, stream, opened=opened)
        if len(content) > 1 << 20 or content.decode("utf-8").splitlines() != [
            name + "<0,>=0" for name in names
        ]:
            raise NativeFeedError("native constraints differ from the selected exact local exclusions")
        return names
    except (OSError, UnicodeError, ValueError, KeyError, TypeError):
        raise NativeFeedError("native exact local exclusions cannot be captured") from None


def _machine_constraints(outer, settings, transaction, wheels, *, excluded_local_packages=()):
    exclusions = ()
    if excluded_local_packages != ():
        exclusions = _local_exclusions(
            outer.get("constraints", ""), transaction, wheels, excluded_local_packages,
        )
    for key in (
        "constraints", "overrides", "excludes", "build_constraints",
        "constraints_from_workspace", "overrides_from_workspace",
        "excludes_from_workspace", "build_constraints_from_workspace",
    ):
        if outer.get(key) != "[]" and not (key == "constraints" and exclusions):
            raise AcquisitionUnavailable("native resolver-altering package constraints are not yet supported")
    metadata = _body(settings.get("dependency_metadata", ""), "DependencyMetadata", "(", ")").strip().rstrip(",")
    if metadata != "{}":
        raise AcquisitionUnavailable("native dependency metadata overrides are not yet supported")
    options = _fields(_body(settings.get("build_options", ""), "BuildOptions"))
    if options.get("no_binary") != "None":
        raise AcquisitionUnavailable("native source-only package policy cannot admit selected wheels")
    names = None if wheels is None else {_name(wheel["name"]) for wheel in wheels}
    mappings = [dependencies._toml(Path(transaction.environment["UV_CONFIG_FILE"])).get("sources", {})]
    for project in transaction.sources.values():
        mappings.append(dependencies._toml(project / "pyproject.toml").get("tool", {}).get("uv", {}).get("sources", {}))
    for mapping in mappings:
        for name, choices in mapping.items():
            if names is not None and _name(name) not in names:
                continue
            choices = choices if isinstance(choices, list) else [choices]
            if any(isinstance(choice, dict) and "index" in choice for choice in choices):
                raise AcquisitionUnavailable("native per-package index routing is not yet supported")
    return exclusions


def _seasoning_identity(settings):
    cutoff = _fields(_body(settings.get("exclude_newer", ""), "ExcludeNewer"))
    packages = _body(cutoff.get("package", ""), "ExcludeNewerPackage", "(", ")").strip().rstrip(",")
    packages = _body(packages, "", "{", "}")
    return {
        "minimum_age_seconds": dependencies.MIN_AGE_SECONDS,
        "global": re.sub(r"\s+", "", cutoff.get("global", "")),
        "packages": {
            name: re.sub(r"\s+", "", value)
            for name, value in dependencies._configuration.package_cutoffs(
                packages, balanced=dependencies._balanced, error_type=NativeFeedError,
            )
        },
    }


@dataclass(frozen=True)
class NativeFeedInputs:
    profile: object
    feeds: tuple[WheelFeed, ...]
    cutoffs: dict[str, datetime]
    policy_id: str
    download_origins: tuple[str, ...]
    context: ssl.SSLContext = field(repr=False)
    proxies: dict[str, str] = field(repr=False)


def _transport_identity(context):
    transport = _tls_record(context)
    transport["certificates"] = sorted(set(transport["certificates"]))
    return transport


def capture_native_feed_inputs(
    transaction, projection: str, original: str, profile, policy: dict, uv: str,
    deadline: float, *, wheels: list[dict] | None, profile_policy: str,
    opaque_identity: Callable[[str], str],
    excluded_local_packages: tuple[str, ...] = (),
) -> NativeFeedInputs:
    """Capture held native settings; callers retain selection/admission authority.

    The identity callback is the installation's existing secret-safe keyed
    identity owner, not a source/feed approver or an alternate trust policy.
    """
    global_settings = _projection(projection, "GlobalSettings")
    network = _fields(_body(global_settings.get("network_settings", ""), "NetworkSettings"))
    if network.get("connectivity") != "Online" or network.get("offline") != "Disabled":
        raise AcquisitionUnavailable("native package-feed acquisition is offline")
    if network.get("no_proxy") != "None":
        raise AcquisitionUnavailable("native proxy bypass policy is not yet supported")
    if any(network.get(key, "None") != "None" for key in ("http_proxy", "https_proxy")):
        raise AcquisitionUnavailable("native proxy configuration is not yet supported")
    if any(
        key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"} and value
        for key, value in transaction.environment.items()
    ):
        raise AcquisitionUnavailable("retained native proxy environment is not yet supported")
    outer = _projection(projection, "PipCompileSettings")
    settings = _fields(_body(outer.get("settings", ""), "PipSettings"))
    exclusions = _machine_constraints(
        outer, settings, transaction, wheels, excluded_local_packages=excluded_local_packages,
    )
    if settings.get("index_strategy") != "FirstIndex" or settings.get("keyring_provider") != "Disabled":
        raise AcquisitionUnavailable("native index strategy or credential provider is not yet supported")
    indexes = _fields(_body(settings.get("index_locations", ""), "IndexLocations"))
    if indexes.get("flat_index") != "[]" or indexes.get("no_index") != "false":
        raise NativeFeedError("native feed acquisition requires configured registry indexes")
    body = _body(indexes.get("indexes", ""), "", "[", "]")
    raw, selected = _raw_indexes(transaction), []
    while body.strip():
        _check_deadline(deadline)
        match = re.match(r"\s*Index\s*\{", body)
        if match is None:
            raise NativeFeedError("native index list is unsupported")
        position = body.index("{", match.start())
        inner, end = dependencies._balanced(body, position, "{", "}")
        index = _fields(inner)
        name = index.get("name", "")
        name = None if name == "None" else _string(
            _body(_body(name, "Some", "(", ")").strip(), "IndexName", "(", ")").strip().rstrip(","),
        )
        location, username, password_present = _index_url(index.get("url", ""))
        if (
            not _configured_index(name, location, raw) or _is_public_pypi_url(location)
            or index.get("format") != "Simple"
            or index.get("authenticate") not in ("Auto", "Always", "Never")
            or index.get("ignore_error_codes") != "None" or index.get("explicit") != "false"
            or index.get("exclude_newer") != "None"
            or index.get("default") not in ("true", "false")
        ):
            raise NativeFeedError("native index lacks supported affirmative source configuration")
        headers = _credentials(
            {**index, "name": name}, transaction, location, username, password_present,
            raw, uv, deadline,
        )
        identity = "sha256:" + opaque_identity(json.dumps(
            {"name": name, "url": location, "authenticate": index["authenticate"], "headers": headers},
            sort_keys=True, separators=(",", ":"),
        ))
        selected.append((index.get("default") == "true", WheelFeed(identity, location, headers)))
        body = body[end:].strip().removeprefix(",")
    if not selected or len(selected) > 256:
        raise NativeFeedError("native feed configuration has no bounded selected indexes")
    feeds = tuple(feed for _, feed in sorted(selected, key=lambda item: item[0]))
    pairs = list(dependencies._argument_pairs(transaction.argv[3:]))
    cutoff = next((value for option, value in pairs if option == "--exclude-newer"), None)
    if cutoff is None:
        raise NativeFeedError("native feed configuration lacks a frozen seasoning cutoff")
    floor = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    cutoffs = {_name(wheel["name"]): floor for wheel in wheels or ()}
    for option, value in pairs:
        if option == "--exclude-newer-package":
            name, _, value = value.partition("=")
            name = _name(name)
            if name in cutoffs:
                cutoffs[name] = min(cutoffs[name], datetime.fromisoformat(value.replace("Z", "+00:00")))
    origins = tuple(policy["selection"].get("downloadOrigins", ()))
    context = _tls(network)
    transport = _transport_identity(context)
    original_settings = _fields(_body(
        _projection(original, "PipCompileSettings").get("settings", ""), "PipSettings",
    ))
    identity = "sha256:" + opaque_identity(json.dumps({
        "owner_policy": policy["policyId"], "feeds": [feed.identity for feed in feeds],
        "transport": transport, "seasoning": _seasoning_identity(original_settings),
        "index_strategy": settings["index_strategy"],
        "dependency_selection": {
            key: settings.get(key) for key in (
                "resolution", "prerelease", "fork_strategy", "dependency_mode",
            )
        },
        "profile_policy": profile_policy,
        **({"excluded_local_packages": exclusions} if exclusions else {}),
    }, sort_keys=True, separators=(",", ":")))
    proxies = {}
    for key in ("http_proxy", "https_proxy"):
        value = _optional(network.get(key, "None"))
        if value is not None:
            proxies[key.removesuffix("_proxy")] = value
    _check_deadline(deadline)
    return NativeFeedInputs(profile, feeds, cutoffs, identity, origins, context, proxies)
