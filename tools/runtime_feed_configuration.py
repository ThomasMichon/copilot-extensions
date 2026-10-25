"""Captured native UV feed inputs under the existing installation authority.

This adapter is not a configuration-precedence reader, profile approver or
resolver. Unsupported native projections/providers refuse before HTTP.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
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

from build_toolchain_lock import _MARKER_ENV_QUERY_SCRIPT, sanitize_subprocess_env
from governed_feed_trust import _is_public_pypi_url, _opaque_index_identity
from runtime_profile import ProfileError, ProfileUnavailable, validate_profile
from runtime_profile_policy import InstallationProfilePlan
from runtime_source_lock import SourceLockCandidate

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "libs" / "installer-engine"))
sys.path.insert(0, str(_ROOT / "libs" / "installation-context"))
import installer_dependency_policy as dependencies
from installer_wheel_acquisition import AcquisitionUnavailable, WheelFeed, _name, _tls_record, _url
from installer_wheel_inputs import _check_deadline, _ordinary
from installer_wheel_snapshot import _open, _unchanged
from installation_context import resolve_runtime_profile_policy


def _body(value: str, prefix: str, opening="{", closing="}") -> str:
    match = re.fullmatch(re.escape(prefix) + r"\s*" + re.escape(opening) + r".*", value, re.DOTALL)
    if match is None:
        raise ProfileError("native feed projection is unsupported")
    position = value.index(opening, len(prefix))
    body, end = dependencies._balanced(value, position, opening, closing)
    if value[end:].strip().strip(","):
        raise ProfileError("native feed projection has trailing data")
    return body


def _fields(body: str) -> dict[str, str]:
    result, position = {}, 0
    while position < len(body):
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*):\s*", body[position:])
        if match is None:
            if body[position:].strip().strip(","):
                raise ProfileError("native feed fields are unsupported")
            break
        name = match[1]
        if name in result:
            raise ProfileError("native feed projection has duplicate fields")
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
                    raise ProfileError("native feed string is unsupported") from None
                position += used
            else:
                position += 1
        result[name] = body[start:position].strip()
        position += 1
    return result


def _projection(projection: str, kind: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^" + re.escape(kind) + r" \{", projection))
    if len(matches) != 1:
        raise ProfileError("native feed settings are ambiguous")
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
        raise ProfileError("native feed string is unsupported") from None


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
        raise ProfileError("native feed host is unsupported")
    port = safe.get("port")
    if port != "None":
        port = _body(port or "", "Some", "(", ")").strip().rstrip(",")
        if not port.isdecimal():
            raise ProfileError("native feed port is unsupported")
        host += ":" + port
    query = _optional(safe.get("query", ""))
    if _optional(safe.get("fragment", "")) is not None:
        raise ProfileError("native feed index fragments are unsupported")
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
                raise ProfileError("retained feed URL has an unresolved environment reference")
            return environment[match[1]]
        expanded.append((name, re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)))
    return expanded


def _basic(user: str, password: str) -> tuple[tuple[str, str], ...]:
    if any(character in user + password for character in "\r\n") or ":" in user:
        raise ProfileError("native feed credentials are unsupported")
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
            raise ProfileError("native netrc credential input is oversized")
        with _open(path) as stream:
            opened = _unchanged(path, before, stream)
            content = stream.read((1 << 20) + 1)
            if len(content) > 1 << 20:
                raise ProfileError("native netrc credential input is oversized")
            _unchanged(path, before, stream, opened=opened)
        provider = netrc.netrc.__new__(netrc.netrc)
        provider.hosts, provider.macros = {}, {}
        provider._parse(str(path), io.StringIO(content.decode("utf-8")), False)
        credentials = provider.authenticators(host)
        if credentials is None:
            return None
        user, _, password = credentials
        if username and username != user:
            raise ProfileError("native netrc credential selection is ambiguous")
        return user, password
    except (OSError, ValueError, netrc.NetrcParseError):
        raise ProfileError("native netrc credential input cannot be captured") from None


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
        raise ProfileError("native feed credential inputs are ambiguous")
    user, password = matches[0] if matches else (username, None)
    if index["authenticate"] == "Never":
        if password_present or password is not None:
            raise ProfileError("native feed never-authenticate policy conflicts with credentials")
        return ()
    if password_present and password is None:
        raise ProfileError("native embedded feed credentials could not be captured")
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
            raise ProfileError("native feed credential lookup failed or timed out") from None
        _check_deadline(deadline)
        if result.returncode == 0:
            if not user:
                raise AcquisitionUnavailable("native stored credentials require an explicit selected username")
            password = result.stdout.rstrip("\r\n")
        else:
            missing = "error: Failed to fetch credentials for `" + _url(location)[1] + "/`"
            if result.returncode != 2 or result.stderr.strip() != missing:
                raise ProfileError("native feed credential lookup failed") from None
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
        raise ProfileError("insecure native feed trust is not permitted")
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
                raise ProfileError("native custom certificate projection is unsupported")
            try:
                context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(bytes.fromhex(match[1])))
            except (ValueError, ssl.SSLError):
                raise ProfileError("native custom certificate projection is invalid") from None
            body = body[match.end():]
    return context


def _machine_constraints(outer, settings, transaction, wheels):
    for key in (
        "constraints", "overrides", "excludes", "build_constraints",
        "constraints_from_workspace", "overrides_from_workspace",
        "excludes_from_workspace", "build_constraints_from_workspace",
    ):
        if outer.get(key) != "[]":
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
                packages, balanced=dependencies._balanced, error_type=ProfileError,
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


def _capture(
    transaction, projection, original, profile, policy, uv, deadline, *,
    source_lock=None, source_resolution=False,
):
    if source_resolution:
        if profile is not None or source_lock is not None:
            raise ProfileError("source resolution preflight cannot carry an artifact or lock")
        wheels, profile_policy = None, "source-resolution"
    elif source_lock is None:
        wheels = profile.record()["wheels"]
        profile_policy = profile.record()["acquisition_policy"]
    else:
        if profile is not None:
            raise ProfileError("source feed capture must not carry an artifact profile")
        wheels = source_lock.record()["files"]
        profile_policy = "source-lock:" + source_lock.lock_id
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
    _machine_constraints(outer, settings, transaction, wheels)
    if settings.get("index_strategy") != "FirstIndex" or settings.get("keyring_provider") != "Disabled":
        raise AcquisitionUnavailable("native index strategy or credential provider is not yet supported")
    indexes = _fields(_body(settings.get("index_locations", ""), "IndexLocations"))
    if indexes.get("flat_index") != "[]" or indexes.get("no_index") != "false":
        raise ProfileError("native feed acquisition requires configured registry indexes")
    body = _body(indexes.get("indexes", ""), "", "[", "]")
    raw, selected = _raw_indexes(transaction), []
    while body.strip():
        _check_deadline(deadline)
        match = re.match(r"\s*Index\s*\{", body)
        if match is None:
            raise ProfileError("native index list is unsupported")
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
            raise ProfileError("native index lacks supported affirmative source configuration")
        headers = _credentials(
            {**index, "name": name}, transaction, location, username, password_present,
            raw, uv, deadline,
        )
        identity = "sha256:" + _opaque_index_identity(json.dumps(
            {"name": name, "url": location, "authenticate": index["authenticate"], "headers": headers},
            sort_keys=True, separators=(",", ":"),
        ))
        selected.append((index.get("default") == "true", WheelFeed(identity, location, headers)))
        body = body[end:].strip().removeprefix(",")
    if not selected or len(selected) > 256:
        raise ProfileError("native feed configuration has no bounded selected indexes")
    feeds = tuple(feed for _, feed in sorted(selected, key=lambda item: item[0]))
    pairs = list(dependencies._argument_pairs(transaction.argv[3:]))
    cutoff = next((value for option, value in pairs if option == "--exclude-newer"), None)
    if cutoff is None:
        raise ProfileError("native feed configuration lacks a frozen seasoning cutoff")
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
    transport = _tls_record(context)
    transport["certificates"] = sorted(transport["certificates"])
    original_settings = _fields(_body(
        _projection(original, "PipCompileSettings").get("settings", ""), "PipSettings",
    ))
    identity = "sha256:" + _opaque_index_identity(json.dumps({
        "owner_policy": policy["policyId"], "feeds": [feed.identity for feed in feeds],
        "transport": transport, "seasoning": _seasoning_identity(original_settings),
        "index_strategy": settings["index_strategy"],
        "dependency_selection": {
            key: original_settings.get(key) for key in (
                "resolution", "prerelease", "fork_strategy", "dependency_mode",
            )
        },
        "profile_policy": profile_policy,
    }, sort_keys=True, separators=(",", ":")))
    proxies = {}
    for key in ("http_proxy", "https_proxy"):
        value = _optional(network.get(key, "None"))
        if value is not None:
            proxies[key.removesuffix("_proxy")] = value
    _check_deadline(deadline)
    return NativeFeedInputs(profile, feeds, cutoffs, identity, origins, context, proxies)


@contextmanager
def retain_native_feed_inputs(
    plan: InstallationProfilePlan, *, installation: dict, uv: str, python: Path,
    deadline: float, env: dict[str, str] | None = None,
):
    """Read actual owner policy and native settings without resolving/installing."""
    _check_deadline(deadline)
    current = resolve_runtime_profile_policy(**installation)["runtimeProfiles"]
    if current.get("status") != "ready" or current != plan.policy():
        raise ProfileError("native acquisition policy differs from its installation owner")
    if plan.release_selection is None:
        raise ProfileUnavailable("native artifact acquisition requires selected release mode")
    profile = validate_profile(plan.release_selection.selected.profile.record(), deadline=deadline)
    environment = dependencies._captured_environment(env)
    cwd = Path(installation["payload_root"])
    if not python.is_absolute() or not python.is_file():
        raise ProfileError("native feed capture needs the exact selected interpreter")
    try:
        observed = subprocess.run(
            [str(python), "-I", "-S", "-c", _MARKER_ENV_QUERY_SCRIPT],
            env=sanitize_subprocess_env(environment), capture_output=True, text=True,
            timeout=deadline - time.monotonic(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        _check_deadline(deadline)
        actual, expected = json.loads(observed.stdout), profile.record()["target"]["environment"]
        if observed.returncode or not isinstance(actual, dict):
            raise ProfileError("native feed target probe did not return marker facts")
        if actual != expected:
            fields = sorted(key for key in set(actual) | set(expected) if actual.get(key) != expected.get(key))
            raise ProfileError("native feed target differs from the selected interpreter: " + ", ".join(fields))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        raise ProfileError("native feed target probe failed or timed out") from None
    with dependencies._temporary_inputs(None) as temporary:
        root = dependencies._private_retained_root(Path(temporary)).resolve()
        requirements = root / "selected.in"
        requirements.write_text("".join(
            wheel["name"] + "==" + wheel["version"] + "\n" for wheel in profile.record()["wheels"]
        ), encoding="utf-8")
        projection = []
        with dependencies.retain_uv_compile(
            uv, [str(requirements), "--python", str(python), "--only-binary", ":all:",
                 "--no-python-downloads", "--python-version", profile.record()["target"]["environment"]["python_full_version"]],
            env=environment, cwd=cwd, trusted_source_roots=(root, cwd),
            deadline=deadline, native_target=True, _projection=projection,
        ) as transaction:
            if len(projection) != 2:
                raise ProfileError("native feed capture did not produce paired native configurations")
            yield _capture(transaction, projection[1], projection[0], profile, current, transaction.argv[0], deadline)


def _source_policy(plan: InstallationProfilePlan, installation: dict) -> dict:
    current = resolve_runtime_profile_policy(**installation)["runtimeProfiles"]
    if (
        current.get("status") != "ready" or current != plan.policy()
        or plan.release_selection is not None
        or plan.source_trigger not in (
            "release-index-unavailable", "artifact-unavailable", "approved-profile-unavailable",
        )
        or not current["selection"]["allowSourceFallback"]
    ):
        raise ProfileError("source feed capture lacks its actual owner")
    return current


def validate_source_resolution_inputs(
    plan: InstallationProfilePlan, *, installation: dict, transaction, projections: list[str],
    deadline: float,
) -> str:
    """Validate the held native settings before the compiler can request packages."""
    _check_deadline(deadline)
    current = _source_policy(plan, installation)
    if len(projections) != 2:
        raise ProfileError("source resolution preflight needs paired native configurations")
    return _capture(
        transaction, projections[1], projections[0], None, current,
        transaction.argv[0], deadline, source_resolution=True,
    ).policy_id


@contextmanager
def retain_source_feed_inputs(
    plan: InstallationProfilePlan, source_lock: SourceLockCandidate, *, installation: dict, deadline: float,
):
    """Reuse the source compiler's held native configuration, never re-resolve it."""
    _check_deadline(deadline)
    current = _source_policy(plan, installation)
    if (
        not isinstance(source_lock, SourceLockCandidate) or source_lock.native_lock is None
        or source_lock.transaction is None or len(source_lock.projections) != 2
    ):
        raise ProfileError("source feed capture lacks its native lock")
    record = source_lock.record()
    if (
        record["policy"] != current or record["trigger"] != plan.source_trigger or record["mode"] != "source"
        or record["native_lock_sha256"] != "sha256:" + hashlib.sha256(source_lock.native_lock).hexdigest()
        or record["requirements_sha256"] != "sha256:" + hashlib.sha256(source_lock.requirements).hexdigest()
    ):
        raise ProfileError("source feed capture differs from its retained lock and owner")
    yield _capture(
        source_lock.transaction, source_lock.projections[1], source_lock.projections[0],
        None, current, source_lock.transaction.argv[0], deadline, source_lock=source_lock,
    )
    _check_deadline(deadline)
    if resolve_runtime_profile_policy(**installation)["runtimeProfiles"] != current:
        raise ProfileError("source acquisition policy changed during consumption")
