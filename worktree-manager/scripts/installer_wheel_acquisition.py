"""Bounded acquisition of exact selected wheels; never a resolver or approval."""
from __future__ import annotations

import base64
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import ssl
import subprocess
import sys
import time
from types import ModuleType
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from installer_wheel_inputs import WheelError, _check_deadline
from installer_wheel_snapshot import WheelInstallInputs, WheelSnapshot, retain_wheel_inputs
import installer_wheel_inputs as _inspection
import installer_wheel_snapshot as _snapshots


class AcquisitionUnavailable(WheelError):
    """The permitted acquisition path is unavailable, not an integrity refusal."""


@dataclass(frozen=True)
class AcquisitionLimits:
    max_packages: int
    max_requests: int
    max_redirects: int
    max_candidates_per_project: int
    max_index_bytes: int
    max_total_metadata_bytes: int
    max_wheel_bytes: int
    max_closure_bytes: int
    max_temporary_bytes: int

    def verify(self) -> None:
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise WheelError("wheel acquisition needs explicit positive finite limits")


@dataclass(frozen=True)
class WheelFeed:
    """Captured transport inputs; the installation owner establishes their trust."""

    identity: str
    index_url: str = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(default=(), repr=False)


@dataclass(frozen=True)
class AcquiredWheels:
    snapshot: WheelSnapshot
    evidence_json: bytes

    def evidence(self) -> dict:
        return json.loads(self.evidence_json)

    def construction_inputs(self, inputs: WheelInstallInputs) -> WheelInstallInputs:
        """Bind acquired facts into the existing receipt, never grant approval."""
        if not isinstance(inputs, WheelInstallInputs) or inputs.snapshot is not self.snapshot:
            raise WheelError("acquired construction inputs must share exact retained custody")
        canonical = self.evidence_json.decode("ascii")
        if inputs.acquisition_json is not None and inputs.acquisition_json != canonical:
            raise WheelError("wheel construction is already bound to another acquisition")
        result = replace(inputs, acquisition_json=canonical)
        result.verify()
        return result


def _url(value: str) -> tuple[str, str]:
    try:
        if (
            not isinstance(value, str) or not value.isascii() or len(value) > 16_384
            or "\\" in value
            or any(character.isspace() or ord(character) < 32 for character in value)
        ):
            raise ValueError
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.port == 0
        ):
            raise ValueError
        host = parsed.hostname.lower()
        if ":" in host:
            host = "[" + host + "]"
        elif not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        ):
            raise ValueError
        origin = "https://" + host
        if parsed.port is not None and parsed.port != 443:
            origin += ":" + str(parsed.port)
        return urlunsplit(("https", origin[8:], parsed.path or "/", parsed.query, "")), origin
    except (TypeError, ValueError):
        raise WheelError("wheel acquisition requires an ordinary HTTPS location") from None


def _name(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", value) is None:
        raise WheelError("wheel acquisition needs an exact distribution identity")
    return re.sub(r"[-_.]+", "-", value).lower()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WheelError("wheel index contains duplicate JSON properties")
        result[key] = value
    return result


class _Links(HTMLParser):
    def __init__(self, limit):
        super().__init__(convert_charrefs=True)
        self.limit = limit
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        if self.current is not None or len(self.links) >= self.limit:
            raise WheelError("wheel index exceeds its candidate budget or has nested links")
        if len({name for name, _ in attrs}) != len(attrs):
            raise WheelError("wheel index has ambiguous link attributes")
        self.current = (dict(attrs), [])

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            attrs, text = self.current
            self.links.append({
                "filename": "".join(text).strip(), "url": attrs.get("href"),
                "hashes": {}, "yanked": "data-yanked" in attrs,
                "upload-time": attrs.get("data-upload-time"),
                "requires-python": attrs.get("data-requires-python"),
                "withdrawn": "data-withdrawn" in attrs,
            })
            self.current = None


def _entries(content: bytes, media: str, name: str, limit: int) -> list[dict]:
    try:
        text = content.decode("utf-8")
        if media in ("application/vnd.pypi.simple.v1+json", "application/json"):
            document = json.loads(text, object_pairs_hook=_unique)
            if (
                not isinstance(document, dict) or _name(document.get("name")) != name
                or not isinstance(document.get("files"), list)
                or not isinstance(document.get("meta"), dict)
                or document["meta"].get("api-version") not in ("1.0", "1.1", "1.2", "1.3", "1.4")
            ):
                raise WheelError("unsupported wheel index document")
            entries = document["files"]
        elif media in ("text/html", "application/vnd.pypi.simple.v1+html"):
            parser = _Links(limit)
            parser.feed(text)
            parser.close()
            if parser.current is not None:
                raise WheelError("wheel index contains an incomplete link")
            entries = parser.links
        else:
            raise WheelError("unsupported wheel index content type")
        if len(entries) > limit or any(not isinstance(item, dict) for item in entries):
            raise WheelError("wheel index exceeds its candidate budget or has invalid entries")
        return entries
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise WheelError("wheel index is malformed") from None


def _selected(entries, expected, base, cutoff):
    selected = [item for item in entries if item.get("filename") == expected["filename"]]
    if not selected:
        raise AcquisitionUnavailable("selected exact wheel is not listed by its feed")
    if len(selected) != 1:
        raise WheelError("wheel index has ambiguous selected-file entries")
    item = selected[0]
    yanked, withdrawn = item.get("yanked", False), item.get("withdrawn", False)
    if not isinstance(yanked, (bool, str)) or type(withdrawn) is not bool:
        raise WheelError("selected wheel has malformed file admission evidence")
    if yanked is not False or withdrawn:
        raise AcquisitionUnavailable("selected exact wheel is refused by its feed")
    location = item.get("url")
    hashes = item.get("hashes", {})
    if not isinstance(location, str) or not location or not isinstance(hashes, dict):
        raise WheelError("selected wheel index entry is malformed")
    fragment = urlsplit(location).fragment
    fragment_digest = fragment.removeprefix("sha256=") if fragment.startswith("sha256=") else None
    digest = hashes.get("sha256", fragment_digest)
    if (
        not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or "sha256:" + digest != expected["sha256"]
        or (fragment_digest is not None and fragment_digest != digest)
    ):
        raise WheelError("selected wheel index digest differs from the exact lock")
    try:
        uploaded = item.get("upload-time")
        if not isinstance(uploaded, str):
            raise ValueError
        timestamp = datetime.fromisoformat(uploaded.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError
    except ValueError:
        raise WheelError("selected wheel lacks an unambiguous upload time") from None
    if timestamp > cutoff:
        raise AcquisitionUnavailable("selected exact wheel does not satisfy seasoning")
    hint = item.get("requires-python")
    if hint not in (None, ""):
        if not isinstance(hint, str) or hint != hint.strip() or len(hint) > 4096:
            raise WheelError("selected wheel index Python hint is malformed")
        if "requires_python" in expected and hint != expected["requires_python"]:
            raise WheelError("selected wheel index Python hint differs from its profile")
    return urljoin(base, location), timestamp.astimezone(timezone.utc).isoformat(), hint


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class _Client:
    def __init__(self, limits, deadline, context, proxies):
        self.limits, self.deadline = limits, deadline
        self.requests = self.redirects = self.metadata_bytes = self.wheel_bytes = 0
        self.opener = build_opener(ProxyHandler(proxies), HTTPSHandler(context=context), _NoRedirect())

    def request(self, location, feed, origins, accept):
        chain = []
        while True:
            _check_deadline(self.deadline)
            location, origin = _url(location)
            if origin not in origins:
                raise WheelError(f"wheel acquisition location is outside selected source trust: {origin}")
            if self.requests >= self.limits.max_requests:
                raise WheelError("wheel acquisition request budget exceeded")
            self.requests += 1
            headers = {"Accept": accept, "Accept-Encoding": "identity"}
            if origin == _url(feed.index_url)[1]:
                headers.update(feed.headers)
            request = Request(location, headers=headers, method="GET")
            chain.append(hashlib.sha256(origin.encode("ascii")).hexdigest())
            try:
                response = self.opener.open(request, timeout=self.deadline - time.monotonic())
                try:
                    _check_deadline(self.deadline)
                except BaseException:
                    response.close()
                    raise
                return response, location, chain
            except HTTPError as error:
                try:
                    if error.code not in (301, 302, 303, 307, 308):
                        if error.code == 404:
                            raise AcquisitionUnavailable("selected feed input is unavailable") from None
                        raise WheelError("wheel feed request failed") from None
                    if self.redirects >= self.limits.max_redirects:
                        raise WheelError("wheel acquisition redirect budget exceeded") from None
                    redirect = error.headers.get("Location")
                    if not redirect:
                        raise WheelError("wheel feed redirect has no location") from None
                    self.redirects += 1
                    location = urljoin(location, redirect)
                finally:
                    error.close()
            except (OSError, URLError, ValueError):
                raise WheelError("wheel feed request failed or timed out") from None

    def read(self, response, limit, *, output=None):
        _check_deadline(self.deadline)
        if (
            len(response.headers.get_all("Content-Length", [])) > 1
            or len(response.headers.get_all("Content-Encoding", [])) > 1
            or len(response.headers.get_all("Content-Type", [])) > 1
        ):
            raise WheelError("wheel acquisition response headers are ambiguous")
        encoding = response.headers.get("Content-Encoding", "identity").strip().lower()
        if encoding != "identity":
            raise WheelError("wheel acquisition refuses encoded response bodies")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdecimal() or int(length) > limit):
            raise WheelError("wheel acquisition response exceeds its byte budget")
        size, chunks, digest = 0, [], hashlib.sha256()
        while True:
            _check_deadline(self.deadline)
            if length is not None and size == int(length):
                break
            try:
                response.fp.raw._sock.settimeout(self.deadline - time.monotonic())
                chunk = response.read1(min(1 << 16, limit - size + 1))
            except (OSError, ValueError, AttributeError):
                raise WheelError("wheel feed body read failed or timed out") from None
            _check_deadline(self.deadline)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise WheelError("wheel acquisition response exceeds its byte budget")
            digest.update(chunk)
            if output is None:
                chunks.append(chunk)
            else:
                output.write(chunk)
        if length is not None and size != int(length):
            raise WheelError("wheel acquisition response length is inconsistent")
        return b"".join(chunks), "sha256:" + digest.hexdigest(), size


@contextmanager
def acquire_wheel_inputs(
    expected, *, feeds: tuple[WheelFeed, ...], download_origins: tuple[str, ...],
    cutoffs: dict[str, datetime], policy_id: str, limits: AcquisitionLimits,
    deadline: float, ssl_context: ssl.SSLContext, proxies: dict[str, str],
    procutil: ModuleType,
):
    """Retain exact downloads and file-policy facts through inspected construction.

    This mechanism cannot establish feed/profile trust. The existing installation
    adapter supplies captured policy, configuration and independently selected
    exact records before calling it.
    """
    import installer_dependency_policy as policy

    limits.verify()
    _check_deadline(deadline)
    if (
        not isinstance(ssl_context, ssl.SSLContext) or not ssl_context.check_hostname
        or ssl_context.verify_mode != ssl.CERT_REQUIRED or not isinstance(proxies, dict) or proxies
        or not isinstance(policy_id, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", policy_id) is None
        or not isinstance(feeds, tuple) or not feeds
        or not isinstance(download_origins, tuple)
        or not isinstance(procutil, ModuleType)
    ):
        raise WheelError("wheel acquisition requires captured verified transport policy")
    origins = set()
    for origin in download_origins:
        canonical, identity = _url(origin)
        if canonical != identity + "/":
            raise WheelError("download trust must name exact HTTPS origins")
        origins.add(identity)
    for feed in feeds:
        if (
            not isinstance(feed, WheelFeed)
            or not isinstance(feed.identity, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", feed.identity) is None
            or not isinstance(feed.headers, tuple)
            or any(
                not isinstance(pair, tuple) or len(pair) != 2
                or pair[0] != "Authorization" or not isinstance(pair[1], str)
                or not pair[1].isascii() or len(pair[1]) > 16_384
                or any(ord(character) < 32 or ord(character) == 127 for character in pair[1])
                for pair in feed.headers
            )
            or len(feed.headers) > 1
        ):
            raise WheelError("wheel acquisition needs captured named-feed inputs")
        _url(feed.index_url)
    if (
        not isinstance(expected, (list, tuple)) or not expected
        or len(expected) > limits.max_packages or not isinstance(cutoffs, dict)
    ):
        raise WheelError("wheel acquisition exceeds its selected package budget")
    selected, names, filenames = [], set(), set()
    for item in expected:
        if not isinstance(item, dict):
            raise WheelError("wheel acquisition needs exact wheel records")
        item = dict(item)
        name, filename = _name(item.get("name")), item.get("filename")
        if (
            name in names or not isinstance(filename, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,250}\.whl", filename) is None
            or _inspection._unsafe_windows_component(filename)
            or filename.casefold() in filenames
            or not isinstance(item.get("sha256"), str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", item["sha256"]) is None
            or not isinstance(cutoffs.get(name), datetime) or cutoffs[name].tzinfo is None
        ):
            raise WheelError("wheel acquisition needs unambiguous exact file/cutoff records")
        names.add(name)
        filenames.add(filename.casefold())
        selected.append(item)
    with policy._temporary_inputs(None) as temporary:
        root = policy._private_retained_root(Path(temporary)).resolve()
        result = _run_worker(
            root, selected, feeds, origins, cutoffs, limits, deadline, ssl_context, procutil,
        )
        paths = {
            item["filename"]: Path(policy._native_retained_path(root / "downloads" / item["filename"]))
            for item in selected
        }
        with retain_wheel_inputs(
            paths, {item["filename"]: item["sha256"] for item in selected},
            max_wheels=limits.max_packages, max_archive_bytes=limits.max_wheel_bytes,
            max_total_bytes=limits.max_closure_bytes,
            max_snapshot_bytes=limits.max_temporary_bytes - result["wheel_bytes"],
            deadline=deadline,
        ) as snapshot:
            evidence = {
                "schema": "copilot-extensions.wheel-acquisition", "version": 1,
                "policy_id": policy_id, **result,
                "temporary_highwater_bytes": max(
                    result["control_bytes"] + result["wheel_bytes"], 2 * result["wheel_bytes"],
                ),
            }
            yield AcquiredWheels(
                snapshot, json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("ascii"),
            )


def _download(root, expected, feeds, origins, cutoffs, limits, deadline, context, control_bytes):
    client = _Client(limits, deadline, context, {})
    root.mkdir()
    records = []
    for item in expected:
        _check_deadline(deadline)
        name = _name(item["name"])
        chosen = None
        for feed in feeds:
            parsed = urlsplit(feed.index_url)
            project = urlunsplit((
                parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/" + name + "/",
                parsed.query, "",
            ))
            origin = _url(project)[1]
            response = None
            try:
                response, base, chain = client.request(
                    project, feed, {origin}, "application/vnd.pypi.simple.v1+json, text/html",
                )
            except AcquisitionUnavailable:
                continue
            try:
                content, _, size = client.read(response, min(
                    limits.max_index_bytes, limits.max_total_metadata_bytes - client.metadata_bytes,
                ))
                client.metadata_bytes += size
                entries = _entries(
                    content, response.headers.get_content_type(), name, limits.max_candidates_per_project,
                )
            finally:
                if response is not None:
                    response.close()
            # The first configured feed owning this project owns selection.
            if entries:
                location, uploaded, hint = _selected(entries, item, base, cutoffs[name])
                chosen = feed, origin, location, uploaded, hint, chain
                break
        if chosen is None:
            raise AcquisitionUnavailable("selected exact project is unavailable on permitted feeds")
        feed, origin, location, uploaded, hint, chain = chosen
        response = None
        try:
            response, _, downloads = client.request(
                location, feed, {origin, *origins}, "application/octet-stream",
            )
            destination = root / item["filename"]
            with destination.open("xb") as output:
                _, digest, size = client.read(response, min(
                    limits.max_wheel_bytes, limits.max_closure_bytes - client.wheel_bytes,
                    limits.max_temporary_bytes - control_bytes - client.wheel_bytes,
                ), output=output)
            if digest != item["sha256"]:
                raise WheelError("acquired wheel digest differs from the exact lock")
            client.wheel_bytes += size
            records.append({
                "filename": item["filename"], "sha256": digest, "size": size,
                "feed": feed.identity, "uploaded": uploaded,
                "source_origins": [*chain, *downloads], "yanked": False, "withdrawn": False,
                **({"requires_python": hint} if hint not in (None, "") else {}),
            })
        finally:
            if response is not None:
                response.close()
    return {
        "files": records, "metadata_bytes": client.metadata_bytes, "wheel_bytes": client.wheel_bytes,
        "request_count": client.requests, "redirect_count": client.redirects, "control_bytes": control_bytes,
    }


def _tls_record(context):
    return {
        "certificates": [base64.b64encode(value).decode("ascii") for value in context.get_ca_certs(binary_form=True)],
        "minimum": int(context.minimum_version), "maximum": int(context.maximum_version),
        "options": context.options, "verify_flags": int(context.verify_flags),
        "ciphers": ":".join(
            item["name"] for item in context.get_ciphers() if item["protocol"] != "TLSv1.3"
        ) + ":@SECLEVEL=" + str(context.security_level),
    }


def _tls_context(record):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version, context.maximum_version = ssl.TLSVersion(record["minimum"]), ssl.TLSVersion(record["maximum"])
    context.options, context.verify_flags = record["options"], record["verify_flags"]
    context.set_ciphers(record["ciphers"])
    for value in record["certificates"]:
        context.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(base64.b64decode(value, validate=True)))
    return context


def _worker_sources():
    result = {}
    for source in (Path(__file__), Path(_inspection.__file__), Path(_snapshots.__file__)):
        before = _inspection._ordinary(source)
        if before.st_size > 1 << 20:
            raise WheelError("acquisition worker source protocol budget exceeded")
        with _snapshots._open(source) as stream:
            opened = _snapshots._unchanged(source, before, stream)
            content = stream.read((1 << 20) + 1)
            _snapshots._unchanged(source, before, stream, opened=opened)
        if len(content) > 1 << 20:
            raise WheelError("acquisition worker source protocol budget exceeded")
        result[source.name] = content
    return result


def _run_worker(root, expected, feeds, origins, cutoffs, limits, deadline, context, procutil):
    from installer_engine_process import ChildLaunchError, run_owned
    from installer_dependency_policy import _native_retained_path

    _check_deadline(deadline)
    sources = _worker_sources()
    request = {
        "version": 1, "expected": expected, "feeds": [asdict(feed) for feed in feeds],
        "origins": sorted(origins), "cutoffs": {name: value.isoformat() for name, value in cutoffs.items()},
        "limits": asdict(limits), "deadline": deadline, "tls": _tls_record(context), "control_bytes": 0,
        "download_root": _native_retained_path(root / "downloads"),
    }
    while True:
        content = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
        overhead = sum(map(len, sources.values())) + len(content)
        if overhead == request["control_bytes"]:
            break
        request["control_bytes"] = overhead
    if len(content) > 4 << 20 or overhead > limits.max_temporary_bytes:
        raise WheelError("acquisition control protocol exceeds its temporary storage budget")
    worker = root / "worker"
    worker.mkdir()
    for name, value in sources.items():
        (worker / name).write_bytes(value)
    control = root / "control.json"
    control.write_bytes(content)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_", "PYTHON", "SSL_"))
        and key.upper() not in {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        }
    }
    executable = sys.executable
    if os.name == "nt" and Path(executable).name.lower() == "pythonw.exe":
        executable, extra = procutil.direct_python(executable)
        environment.pop("__PYVENV_LAUNCHER__", None)
        environment.update(extra)
    launch = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from installer_wheel_acquisition import _worker;_worker(sys.argv[2])"
    )
    try:
        with ExitStack() as held:
            for name in sources:
                held.enter_context(_snapshots._open(worker / name))
            custody = held.enter_context(_snapshots._open(control))
            _check_deadline(deadline)
            result = run_owned(
                [executable, "-I", "-S", "-B", "-c", launch, str(worker), str(control)],
                cwd=root, env=environment, timeout=deadline - time.monotonic(),
                procutil=procutil, construction_fd=custody.fileno(),
                deadline=deadline,
            )
        _check_deadline(deadline)
        if len(result.stdout) > 4 << 20:
            raise WheelError("acquisition worker result exceeds its protocol budget")
        response = json.loads(result.stdout, object_pairs_hook=_unique)
        if response.get("status") != "ok":
            message = response.get("message")
            if not isinstance(message, str) or len(message) > 512 or not message.isascii():
                raise WheelError("acquisition worker returned an unsupported failure")
            error_type = AcquisitionUnavailable if response.get("status") == "unavailable" else WheelError
            raise error_type(message)
        evidence = response["evidence"]
        if evidence.get("control_bytes") != overhead:
            raise WheelError("acquisition worker control storage evidence differs")
    except (ValueError, KeyError, TypeError) as error:
        if isinstance(error, WheelError):
            raise
        raise WheelError("acquisition worker returned malformed protocol data") from None
    except (ChildLaunchError, OSError):
        raise WheelError("acquisition worker could not be started") from None
    except subprocess.TimeoutExpired:
        raise WheelError("wheel acquisition deadline exceeded") from None
    except subprocess.CalledProcessError:
        raise WheelError("wheel acquisition worker failed") from None
    for name in sources:
        (worker / name).unlink()
    worker.rmdir()
    control.unlink()
    return evidence


def _worker(path):
    try:
        control = Path(path)
        with control.open("rb") as stream:
            content = stream.read((4 << 20) + 1)
        if len(content) > 4 << 20:
            raise WheelError("acquisition worker control protocol budget exceeded")
        request = json.loads(content, object_pairs_hook=_unique)
        if request.get("version") != 1:
            raise WheelError("unsupported acquisition worker protocol")
        feeds = tuple(WheelFeed(
            item["identity"], item["index_url"], tuple(tuple(pair) for pair in item["headers"]),
        ) for item in request["feeds"])
        evidence = _download(
            Path(request["download_root"]), request["expected"], feeds, set(request["origins"]),
            {name: datetime.fromisoformat(value) for name, value in request["cutoffs"].items()},
            AcquisitionLimits(**request["limits"]), request["deadline"],
            _tls_context(request["tls"]), request["control_bytes"],
        )
        result = {"status": "ok", "evidence": evidence}
    except WheelError as error:
        result = {
            "status": "unavailable" if isinstance(error, AcquisitionUnavailable) else "refused",
            "message": str(error),
        }
    except Exception:
        result = {"status": "refused", "message": "wheel acquisition worker failed"}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)
