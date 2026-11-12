"""Resolve core candidates through the existing guarded wheel-feed owner."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent.parent
_ENGINE = ROOT / "libs" / "installer-engine"
if _ENGINE.is_dir():
    sys.path.insert(0, str(_ENGINE))

import installer_dependency_policy as dependencies
import installer_native_feed as native_feed
import installer_wheel_acquisition as acquisition
import installer_wheel_inputs as inspection
import installer_wheel_snapshot as snapshots
from core_native_feed_errors import guarded_stop_reason
from core_native_feed_target import require_native_target
from installer_engine_process import ChildLaunchError, ChildTimeoutExpired, run_owned
from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name, parse_wheel_filename
from runtime_wheel_inspection import inspect_wheel_facts

_PLACEHOLDER = "http://127.0.0.1:1/guarded-index-placeholder/simple/"
_SOURCE_LIMIT = 1 << 20
_CONTROL_LIMIT = 4 << 20
_TAG_PART = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*")


class GuardedSelectionError(ValueError):
    """A guarded native selection could not establish its input boundaries."""


@contextmanager
def hold_compile_inputs(transaction, deadline: float):
    """Keep the exact retained configuration and requirement files through publication."""
    if transaction.sources:
        raise GuardedSelectionError("guarded selection requires flat retained requirements")
    root = Path(dependencies._native_retained_path(
        Path(transaction.environment["UV_CONFIG_FILE"]).parent,
    )).resolve(strict=True)
    paths = [Path(transaction.environment["UV_CONFIG_FILE"])]
    for option, value in dependencies._argument_pairs(transaction.argv[3:]):
        if (not option and value) or option in ("--constraint", "--constraints", "-c"):
            paths.append(Path(value))
    if len(paths) > 4 or len(set(paths)) != len(paths):
        raise GuardedSelectionError("guarded selection has unsupported retained inputs")
    with ExitStack() as stack:
        held = []
        total = 0
        for path in paths:
            path = Path(dependencies._native_retained_path(path))
            if not path.resolve(strict=True).is_relative_to(root):
                raise GuardedSelectionError("guarded input is outside its retained root")
            before = inspection._ordinary(path)
            total += before.st_size
            if total > _CONTROL_LIMIT:
                raise GuardedSelectionError("guarded retained inputs exceed their budget")
            stream = stack.enter_context(snapshots._open(path))
            opened = snapshots._unchanged(path, before, stream)
            digest = snapshots._digest(stream, _CONTROL_LIMIT, deadline)
            held.append((path, before, opened, stream, digest))
        yield
        for path, before, opened, stream, digest in held:
            snapshots._unchanged(path, before, stream, opened=opened)
            if snapshots._digest(stream, _CONTROL_LIMIT, deadline) != digest:
                raise GuardedSelectionError("guarded retained input changed")


def _read_source(path: Path, deadline: float) -> bytes:
    inspection._check_deadline(deadline)
    path = Path(dependencies._native_retained_path(path))
    before = inspection._ordinary(path)
    if before.st_size > _SOURCE_LIMIT:
        raise GuardedSelectionError("guarded resolver source exceeds its budget")
    with snapshots._open(path) as stream:
        opened = snapshots._unchanged(path, before, stream)
        content = stream.read(_SOURCE_LIMIT + 1)
        snapshots._unchanged(path, before, stream, opened=opened)
    if len(content) > _SOURCE_LIMIT:
        raise GuardedSelectionError("guarded resolver source exceeds its budget")
    return content


def _worker_sources(deadline: float) -> dict[str, bytes]:
    sources = acquisition._worker_sources()
    extra = (
        Path(__file__), ROOT / "tools" / "core_native_feed_target.py",
        ROOT / "tools" / "core_native_feed_errors.py",
        ROOT / "tools" / "runtime_wheel_inspection.py",
        ROOT / "tools" / "artifact_source_build.py",
        ROOT / "libs" / "installer-engine" / "installer_native_feed.py",
        ROOT / "libs" / "installer-engine" / "installer_dependency_policy.py",
        ROOT / "libs" / "installer-engine" / "installer_dependency_config.py",
        ROOT / "libs" / "installer-engine" / "installer_dependency_arguments.py",
        ROOT / "libs" / "installer-engine" / "installer_retained_content.py",
        ROOT / "libs" / "installer-engine" / "installer_feed_policy.py",
        ROOT / "libs" / "installer-engine" / "installer_engine_process.py",
    )
    for path in extra:
        sources[path.name] = _read_source(path, deadline)
    import packaging

    packages = [Path(packaging.__file__).resolve().parent]
    if sys.version_info < (3, 11):  # noqa: UP036
        import tomli

        packages.append(Path(tomli.__file__).resolve().parent)
    for package in packages:
        paths = sorted(package.rglob("*.py"))
        if not paths or len(paths) > 256:
            raise GuardedSelectionError("guarded resolver parser source is unsupported")
        for path in paths:
            name = (Path(package.name) / path.relative_to(package)).as_posix()
            sources[name] = _read_source(path, deadline)
    return sources


def _local_configuration(transaction, directory: Path) -> tuple[list[str], dict[str, str], str]:
    argv = list(transaction.argv)
    pairs = list(dependencies._argument_pairs(argv[3:]))
    forbidden = dependencies._INDEX_OPTIONS | {"-f", "--find-links", "--allow-insecure-host", "--no-index"}
    if any(option in forbidden for option, _value in pairs):
        raise GuardedSelectionError("guarded selection does not accept direct index arguments")
    positions = [index for index, value in enumerate(argv) if value == "--config-file"]
    if len(positions) != 1 or positions[0] + 1 >= len(argv):
        raise GuardedSelectionError("guarded selection needs one retained configuration")
    environment = dict(transaction.environment)
    config = dependencies._toml(Path(environment["UV_CONFIG_FILE"]))
    if config.get("sources") or config.get("workspace"):
        raise GuardedSelectionError("guarded resolver configuration has unsupported sources")
    for key in ("index", "index-url", "extra-index-url", "default-index", "find-links",
                "no-index", "allow-insecure-host"):
        config.pop(key, None)
    config["index"] = [{"url": _PLACEHOLDER, "default": True, "authenticate": "never"}]
    template = directory / "template.toml"
    dependencies._write_toml(template, config)
    content = template.read_text(encoding="utf-8")
    if content.count(_PLACEHOLDER) != 1:
        raise GuardedSelectionError("guarded resolver configuration is ambiguous")
    for key in tuple(environment):
        upper = key.upper()
        if upper in {
            "UV_INDEX", "UV_DEFAULT_INDEX", "UV_INDEX_URL", "UV_EXTRA_INDEX_URL",
            "UV_FIND_LINKS", "UV_INSECURE_HOST", "UV_ALLOW_INSECURE_HOST",
            "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL", "PIP_FIND_LINKS",
        } or (upper.startswith("UV_INDEX_") and upper != "UV_INDEX_STRATEGY"):
            del environment[key]
    config_path = str(directory / "local-uv.toml")
    environment["UV_CONFIG_FILE"] = config_path
    argv[positions[0] + 1] = config_path
    argv.extend(["--no-cache", "--allow-insecure-host", "127.0.0.1"])
    return argv, environment, content


def _check_local_projection(original: str, observed: str, expected: str) -> None:
    before = native_feed._projection(original, "PipCompileSettings")
    after = native_feed._projection(observed, "PipCompileSettings")
    original_settings = native_feed._fields(native_feed._body(before.pop("settings"), "PipSettings"))
    local_settings = native_feed._fields(native_feed._body(after.pop("settings"), "PipSettings"))
    original_settings.pop("index_locations")
    indexes = native_feed._fields(native_feed._body(local_settings.pop("index_locations"), "IndexLocations"))
    before.pop("refresh", None)
    after.pop("refresh", None)
    if before != after or original_settings != local_settings:
        raise GuardedSelectionError("guarded selection changed native resolver settings")
    if indexes.get("flat_index") != "[]" or indexes.get("no_index") != "false":
        raise GuardedSelectionError("guarded resolver has an extra index")
    body = native_feed._body(indexes.get("indexes", ""), "", "[", "]").strip()
    if not body.startswith("Index"):
        raise GuardedSelectionError("guarded resolver index is unsupported")
    start = body.index("{")
    fields, end = dependencies._balanced(body, start, "{", "}")
    if body[end:].strip().strip(","):
        raise GuardedSelectionError("guarded resolver has multiple indexes")
    index = native_feed._fields(fields)
    if any(index.get(key) != value for key, value in {
        "explicit": "false", "default": "true", "format": "Simple",
        "authenticate": "Never", "ignore_error_codes": "None", "exclude_newer": "None",
    }.items()) or any(
        index.get(key, "None") != "None"
        for key in ("name", "origin", "publish_url", "cache_control", "hash_algorithm")
    ):
        raise GuardedSelectionError("guarded resolver index settings differ")
    wrapped = native_feed._body(index.get("url", ""), "Url", "(", ")").strip()
    verbatim = native_feed._fields(native_feed._body(wrapped, "VerbatimUrl"))
    if native_feed._optional(verbatim.get("given", "")) != expected:
        raise GuardedSelectionError("guarded resolver index differs")
    url = verbatim.get("url", "")
    wrapper = "DisplaySafeUrl" if url.startswith("DisplaySafeUrl") else "SafeUrl"
    safe = native_feed._fields(native_feed._body(url, wrapper))
    host = native_feed._body(safe.get("host", ""), "Some", "(", ")").strip().rstrip(",")
    host = native_feed._body(host, "Ipv4", "(", ")").strip().rstrip(",")
    port = native_feed._body(safe.get("port", ""), "Some", "(", ")").strip().rstrip(",")
    actual = urlunsplit((
        native_feed._string(safe.get("scheme", "")), host + ":" + port,
        native_feed._string(safe.get("path", "")), "", "",
    ))
    if (
        actual != expected or host != "127.0.0.1"
        or safe.get("cannot_be_a_base", "false") != "false"
        or native_feed._string(safe.get("username", "")) != ""
        or safe.get("password") != "None" or safe.get("query") != "None"
        or safe.get("fragment") != "None"
        or verbatim.get("expanded") != "false" or verbatim.get("force_relative") != "false"
    ):
        raise GuardedSelectionError("guarded resolver index is not confined to loopback")


def _project_url(feed, name: str) -> str:
    parsed = urlsplit(feed.index_url)
    return urlunsplit((
        parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/" + name + "/",
        parsed.query, "",
    ))


def _fetch_project(feed, name, limits, deadline, context, budget):
    client = acquisition._Client(limits, deadline, context, {}, budget)
    location = _project_url(feed, name)
    origin = acquisition._url(location)[1]
    try:
        response, base, chain = client.request(
            location, feed, None, "application/vnd.pypi.simple.v1+json, text/html", index=True,
        )
    except acquisition.AcquisitionUnavailable:
        return None
    try:
        content, _digest, _size = client.read(response, limits.max_index_bytes)
        media = response.headers.get_content_type()
        entries = acquisition._entries(
            content, media, name,
            limits.max_candidates_per_project,
        )
    finally:
        response.close()
    identity = hashlib.sha256(json.dumps(
        [base, media, chain], sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return entries, base, origin, identity


def _target_project(project, name, compatible_tags, wheel_limits, deadline):
    if project is None:
        return None
    entries, base, origin, transport = project
    candidates, seen = [], set()
    for entry in entries:
        inspection._check_deadline(deadline)
        filename = entry.get("filename")
        if not isinstance(filename, str):
            raise GuardedSelectionError("guarded index contains an invalid filename")
        if not filename.endswith(".whl"):
            continue
        if (
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,250}\.whl", filename) is None
            or inspection._unsafe_windows_component(filename)
        ):
            raise GuardedSelectionError("guarded index contains an invalid wheel name")
        tokens = filename[:-4].split("-")
        if len(tokens) < 5:
            raise GuardedSelectionError("guarded index contains an invalid wheel name")
        if any(_TAG_PART.fullmatch(part) is None for part in tokens[-3:]):
            # Preserve prefix/project validation before omitting an unselectable tag.
            prefix_probe = "-".join((*tokens[:-3], "py3", "none", "any")) + ".whl"
            wheel_name, _version, _build, _tags = parse_wheel_filename(prefix_probe)
            if canonicalize_name(wheel_name) != name:
                raise GuardedSelectionError("guarded index contains another project")
            continue
        inspection.bounded_tag(
            "-".join(tokens[-3:]), limit=wheel_limits.max_tags, deadline=deadline,
        )
        wheel_name, _version, _build, tags = parse_wheel_filename(filename)
        if canonicalize_name(wheel_name) != name:
            raise GuardedSelectionError("guarded index contains another project")
        location, hashes = entry.get("url"), entry.get("hashes", {})
        if not isinstance(location, str) or not location or not isinstance(hashes, dict):
            raise GuardedSelectionError("guarded index contains an invalid wheel entry")
        fragment = urlsplit(location).fragment
        fragment_digest = fragment.removeprefix("sha256=") if fragment.startswith("sha256=") else None
        digest = hashes.get("sha256", fragment_digest)
        if digest is not None and (
            not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise GuardedSelectionError("guarded index contains an invalid digest")
        yanked, withdrawn = entry.get("yanked", False), entry.get("withdrawn", False)
        if not isinstance(yanked, (bool, str)) or type(withdrawn) is not bool:
            raise GuardedSelectionError("guarded index contains invalid admission evidence")
        if not withdrawn:
            hint, uploaded = entry.get("requires-python"), entry.get("upload-time")
            if hint not in (None, ""):
                if not isinstance(hint, str) or len(hint) > 4096:
                    raise GuardedSelectionError("guarded index contains an invalid Python hint")
                try:
                    SpecifierSet(hint)
                except InvalidSpecifier:
                    raise GuardedSelectionError("guarded index contains an invalid Python hint") from None
            if uploaded is not None and (
                not isinstance(uploaded, str) or len(uploaded) > 128
                or not uploaded.isascii() or any(ord(char) < 32 for char in uploaded)
            ):
                raise GuardedSelectionError("guarded index contains an invalid upload time")
            folded = filename.casefold()
            if folded in seen:
                raise GuardedSelectionError("guarded index contains duplicate wheel names")
            seen.add(folded)
        if not tags.isdisjoint(compatible_tags):
            candidates.append(entry)
    identity = hashlib.sha256(json.dumps(
        [transport, candidates], sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return candidates, base, origin, identity


class _GuardedIndex(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False

    def __init__(self, feed, origins, floor, cutoffs, exclusions, limits, wheel_limits,
                 deadline, context, control_bytes, compatible_tags):
        self.request_queue_size = limits.max_workers * 2
        super().__init__(("127.0.0.1", 0), _Handler)
        self.feed, self.origins = feed, origins
        self.floor, self.cutoffs = floor, cutoffs
        self.exclusions = set(exclusions)
        self.compatible_tags = frozenset(compatible_tags)
        self.limits, self.wheel_limits, self.deadline = limits, wheel_limits, deadline
        self.context = context
        self.budget = acquisition._Budget(limits, deadline, control_bytes)
        remaining = limits.max_temporary_bytes - control_bytes - _CONTROL_LIMIT
        if remaining <= 3:
            raise GuardedSelectionError("guarded resolver storage budget is too small")
        self.budget.maximums["metadata_bytes"] = min(
            self.budget.maximums["metadata_bytes"], remaining // 4,
        )
        self.budget.maximums["wheel_bytes"] = min(
            self.budget.maximums["wheel_bytes"],
            (remaining - self.budget.maximums["metadata_bytes"]) // 3,
        )
        self.secret = secrets.token_urlsafe(24)
        self.base = f"http://127.0.0.1:{self.server_port}/{self.secret}"
        self.index_url = self.base + "/simple/"
        self.lock = threading.RLock()
        self.slots = threading.BoundedSemaphore(limits.max_workers)
        self.project_locks = {}
        self.wheel_locks = {}
        self.projects = {}
        self.files = {}
        self.offered = {}
        self.admitted = {}
        self.served = set()
        self.cache = {}
        self.local_requests = 0
        self.candidate_count = 0
        self.filenames = set()

    def process_request(self, request, client_address):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or not self.slots.acquire(timeout=remaining):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(max(0.001, self.deadline - time.monotonic()))
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, _request, _client_address):
        self.budget.fail(GuardedSelectionError("guarded loopback request failed"))

    def count_request(self):
        with self.lock:
            self.budget.check()
            self.local_requests += 1
            if self.local_requests > self.limits.max_requests:
                raise GuardedSelectionError("guarded loopback request budget exceeded")

    def _project(self, name):
        return _fetch_project(
            self.feed, name, self.limits, self.deadline, self.context, self.budget,
        )

    def page(self, name):
        name = acquisition._name(name)
        if name in self.exclusions:
            raise GuardedSelectionError("guarded selection encountered a local package")
        with self.lock:
            if name not in self.project_locks:
                if len(self.project_locks) >= self.limits.max_packages:
                    raise GuardedSelectionError("guarded selection package budget exceeded")
                self.project_locks[name] = threading.Lock()
            project_lock = self.project_locks[name]
        with project_lock:
            if name in self.projects:
                return self.projects[name]["page"]
            project = _target_project(
                self._project(name), name, self.compatible_tags, self.wheel_limits, self.deadline,
            )
            if project is None:
                self.projects[name] = {"identity": None, "page": None}
                return None
            entries, base, origin, identity = project
            files = []
            for entry in entries:
                inspection._check_deadline(self.deadline)
                if entry.get("withdrawn", False):
                    continue
                filename = entry["filename"]
                hashes = entry.get("hashes", {})
                fragment = urlsplit(entry["url"]).fragment
                fragment_digest = fragment.removeprefix("sha256=") if fragment.startswith("sha256=") else None
                digest = hashes.get("sha256", fragment_digest)
                yanked = entry.get("yanked", False)
                hint, uploaded = entry.get("requires-python"), entry.get("upload-time")
                folded = filename.casefold()
                key = hashlib.sha256((name + "\0" + filename).encode("ascii")).hexdigest()
                path = f"/{self.secret}/files/{key}/{filename}"
                url = self.base + f"/files/{key}/{filename}"
                with self.lock:
                    if folded in self.filenames:
                        raise GuardedSelectionError("guarded index contains duplicate wheel names")
                    self.filenames.add(folded)
                    self.candidate_count += 1
                    if self.candidate_count > self.limits.max_requests * self.limits.max_workers:
                        raise GuardedSelectionError("guarded candidate count exceeds its budget")
                    self.files[path] = (name, filename, digest)
                    self.offered[url] = {"filename": filename, "sha256": "sha256:" + digest if digest else None}
                row = {
                    "filename": filename, "url": url,
                    "hashes": {"sha256": digest} if digest else {},
                    "yanked": yanked is not False,
                }
                if hint not in (None, ""):
                    row["requires-python"] = hint
                if uploaded is not None:
                    row["upload-time"] = uploaded
                files.append(row)
            page = json.dumps({
                "meta": {"api-version": "1.1"}, "name": name, "files": files,
            }, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            if len(page) > self.limits.max_index_bytes:
                raise GuardedSelectionError("guarded index exceeds its byte budget")
            self.projects[name] = {
                "identity": identity, "page": page, "entries": entries, "base": base, "origin": origin,
            }
            return page

    def wheel(self, path):
        with self.lock:
            candidate = self.files.get(path)
            if candidate is None:
                return None
            name, filename, digest = candidate
            wheel_lock = self.wheel_locks.setdefault(path, threading.Lock())
        with wheel_lock:
            if path in self.cache:
                return self.cache[path]
            if digest is None:
                raise GuardedSelectionError("guarded candidate lacks a digest")
            expected = {"name": name, "filename": filename, "sha256": "sha256:" + digest}
            project = self.projects[name]
            location, uploaded, hint = acquisition._selected(
                project["entries"], expected, project["base"], self.cutoffs.get(name, self.floor),
            )
            client = acquisition._Client(self.limits, self.deadline, self.context, {}, self.budget)
            response, _location, _chain = client.request(
                location, self.feed, {project["origin"], *self.origins} if self.origins else None,
                "application/octet-stream",
            )
            try:
                output = io.BytesIO()
                _body, actual, size = client.read(response, self.limits.max_wheel_bytes, output=output)
                content = output.getvalue()
            finally:
                response.close()
            if len(content) != size or actual != expected["sha256"]:
                raise GuardedSelectionError("guarded candidate changed after acquisition")
            facts = inspect_wheel_facts(
                io.BytesIO(content), {"filename": filename}, limits=self.wheel_limits,
                max_archive_bytes=self.wheel_limits.max_archive_bytes,
                max_unpacked_bytes=self.wheel_limits.max_expanded_bytes,
                max_metadata_bytes=self.wheel_limits.max_metadata_member_bytes,
                deadline=self.deadline,
            )
            for value in facts.requires_dist:
                inspection._check_deadline(self.deadline)
                try:
                    requirement = Requirement(value)
                except InvalidRequirement:
                    raise GuardedSelectionError("guarded candidate has invalid dependencies") from None
                if requirement.url:
                    raise GuardedSelectionError("guarded candidate has a direct dependency")
            self.admitted[path] = {
                "upload-time": uploaded, "requires-python": hint, "yanked": False, "withdrawn": False,
            }
            self.cache[path] = content
            return content

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *_arguments):
        pass

    def do_HEAD(self):
        self.respond(head=True)

    def do_GET(self):
        self.respond(head=False)

    def respond(self, *, head):
        server = self.server
        try:
            server.count_request()
            parsed = urlsplit(self.path)
            if (
                parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
                or self.client_address[0] != "127.0.0.1"
                or "Authorization" in self.headers or "Proxy-Authorization" in self.headers
            ):
                raise GuardedSelectionError("guarded loopback request is invalid")
            prefix = f"/{server.secret}/simple/"
            if parsed.path.startswith(prefix) and parsed.path.endswith("/"):
                name = parsed.path[len(prefix):-1]
                if not name or "/" in name or acquisition._name(name) != name:
                    raise GuardedSelectionError("guarded project request is invalid")
                body = server.page(name)
                media = "application/vnd.pypi.simple.v1+json"
            else:
                body = server.wheel(parsed.path)
                media = "application/octet-stream"
            if body is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if media != "application/octet-stream":
                server.budget.reserve("metadata_bytes", len(body))
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head:
                self.wfile.write(body)
                if media == "application/octet-stream":
                    with server.lock:
                        server.served.add(server.base + parsed.path.removeprefix("/" + server.secret))
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:  # noqa: BLE001
            server.budget.fail(error)
            try:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except OSError:
                pass


def _run_uv(argv, options, server, deadline):
    process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, **options)

    def stop():
        if process.poll() is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        process.wait(timeout=10)

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or server.budget.failed.is_set():
                stop()
                raise GuardedSelectionError(guarded_stop_reason(server.budget.failure, deadline_expired=remaining <= 0))
            try:
                status = process.wait(timeout=min(0.1, remaining))
                break
            except subprocess.TimeoutExpired:
                continue
        if status:
            if server.budget.failed.is_set():
                raise GuardedSelectionError(guarded_stop_reason(server.budget.failure, deadline_expired=False))
            raise GuardedSelectionError("guarded native resolver refused the candidate")
    finally:
        if process.poll() is None:
            stop()


def _serve(server):
    server.serve_forever(poll_interval=0.05)


def _request_feed(request):
    feed_record = request["feed"]
    return acquisition.WheelFeed(
        feed_record["identity"], feed_record["index_url"],
        tuple(tuple(pair) for pair in feed_record["headers"]),
    )


def _usage(budget):
    return {
        key: budget.current(key)
        for key in ("requests", "redirects", "metadata_bytes", "wheel_bytes")
    }


def _request_tags(request):
    tags = tuple(sys_tags())
    if request.get("target_tags") != [str(tag) for tag in tags]:
        raise GuardedSelectionError("guarded worker target differs from the native interpreter")
    return frozenset(tags)


def _worker(request, control: Path) -> dict:
    feed = _request_feed(request)
    limits = acquisition.AcquisitionLimits(**request["limits"])
    limits.verify()
    wheel_limits = inspection.WheelLimits(**request["wheel_limits"])
    deadline = request["deadline"]
    compatible_tags = _request_tags(request)
    server = _GuardedIndex(
        feed, set(request["origins"]), datetime.fromisoformat(request["floor"]),
        {name: datetime.fromisoformat(value) for name, value in request["cutoffs"].items()},
        request["exclusions"], limits, wheel_limits, deadline,
        acquisition._tls_context(request["tls"]), request["control_bytes"], compatible_tags,
    )
    config = Path(request["config"])
    template = request["template"]
    if template.count(_PLACEHOLDER) != 1:
        raise GuardedSelectionError("guarded resolver template is invalid")
    config.write_text(template.replace(_PLACEHOLDER, server.index_url), encoding="utf-8")
    config.chmod(0o400)
    with dependencies._temporary_inputs(control.parent) as temporary:
        temporary = Path(dependencies._native_retained_path(temporary))
        request["environment"].update({
            "TMP": str(temporary), "TEMP": str(temporary), "TMPDIR": str(temporary),
            "UV_CACHE_DIR": str(temporary / "uv-cache"),
        })
        before = inspection._ordinary(config)
        thread = threading.Thread(target=_serve, args=(server,))
        thread.start()
        try:
            with snapshots._open(config) as held:
                opened = snapshots._unchanged(config, before, held)
                original_hash = snapshots._digest(held, _CONTROL_LIMIT, deadline)
                options = {
                    "cwd": request["cwd"], "env": request["environment"],
                    "stdin": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                    "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                }
                projection = subprocess.run(
                    [*request["argv"], "--show-settings"], stdout=subprocess.PIPE, check=False,
                    timeout=max(0.001, deadline - time.monotonic()), **options,
                )
                if projection.returncode or len(projection.stdout) > _CONTROL_LIMIT:
                    raise GuardedSelectionError("guarded resolver settings are unavailable")
                _check_local_projection(
                    request["projection"], projection.stdout.decode("utf-8"), server.index_url,
                )
                _run_uv(request["argv"], options, server, deadline)
                snapshots._unchanged(config, before, held, opened=opened)
                if snapshots._digest(held, _CONTROL_LIMIT, deadline) != original_hash:
                    raise GuardedSelectionError("guarded resolver configuration changed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    if server.budget.failure is not None:
        raise GuardedSelectionError(guarded_stop_reason(server.budget.failure, deadline_expired=False))
    downloads = control.parent / "downloads"
    downloads.mkdir()
    for url in sorted(server.served):
        inspection._check_deadline(deadline)
        path = urlsplit(url).path
        _name, filename, _digest = server.files[path]
        destination = downloads / filename
        with destination.open("xb") as output:
            output.write(server.cache[path])
        destination.chmod(0o400)
    return {
        "offered": server.offered, "served": sorted(server.served),
        "admitted": {url: server.admitted[urlsplit(url).path] for url in sorted(server.served)},
        "projects": {name: value["identity"] for name, value in server.projects.items()},
        "usage": _usage(server.budget),
    }


def _verify_worker(request) -> dict:
    feed = _request_feed(request)
    limits = acquisition.AcquisitionLimits(**request["limits"])
    limits.verify()
    wheel_limits = inspection.WheelLimits(**request["wheel_limits"])
    compatible_tags = _request_tags(request)
    deadline = request["deadline"]
    budget = acquisition._Budget(limits, deadline, request["control_bytes"])
    budget.maximums["metadata_bytes"] = min(
        budget.maximums["metadata_bytes"],
        max(0, (limits.max_temporary_bytes - request["control_bytes"]) // 2),
    )
    context = acquisition._tls_context(request["tls"])
    for name, expected in sorted(request["projects"].items()):
        inspection._check_deadline(deadline)
        current = _target_project(
            _fetch_project(feed, name, limits, deadline, context, budget),
            name, compatible_tags, wheel_limits, deadline,
        )
        if (None if current is None else current[3]) != expected:
            raise GuardedSelectionError("guarded candidate index changed before publication")
    return {"usage": _usage(budget)}


def _worker_main(control: str) -> None:
    try:
        path = Path(control)
        content = path.read_bytes()
        if len(content) > _CONTROL_LIMIT:
            raise GuardedSelectionError("guarded resolver control exceeds its budget")
        request = json.loads(content, object_pairs_hook=acquisition._unique)
        if request.get("version") != 1:
            raise GuardedSelectionError("unsupported guarded resolver protocol")
        if request.get("operation") == "resolve":
            result = {"status": "ok", **_worker(request, path)}
        elif request.get("operation") == "verify":
            result = {"status": "ok", **_verify_worker(request)}
        else:
            raise GuardedSelectionError("unsupported guarded resolver operation")
    except GuardedSelectionError as error:
        result = {"status": "refused", "reason": str(error)}
    except Exception as error:  # noqa: BLE001
        result = {"status": "refused", "reason": "guarded worker failed: " + type(error).__name__}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")), flush=True)


def _validate_usage(usage, limits):
    if not isinstance(usage, dict) or set(usage) != {"requests", "redirects", "metadata_bytes", "wheel_bytes"}:
        raise GuardedSelectionError("guarded resolver returned invalid evidence")
    maximums = {
        "requests": limits.max_requests, "redirects": limits.max_redirects,
        "metadata_bytes": limits.max_total_metadata_bytes, "wheel_bytes": limits.max_closure_bytes,
    }
    if any(type(usage[key]) is not int or not 0 <= usage[key] <= maximum for key, maximum in maximums.items()):
        raise GuardedSelectionError("guarded resolver returned invalid resource evidence")


def _validate_projects(projects, limits):
    if not isinstance(projects, dict) or len(projects) > limits.max_packages:
        raise GuardedSelectionError("guarded resolver returned invalid project evidence")
    for name, identity in projects.items():
        if (
            not isinstance(name, str) or acquisition._name(name) != name
            or (identity is not None and (
                not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{64}", identity) is None
            ))
        ):
            raise GuardedSelectionError("guarded resolver returned invalid project evidence")


def _validate_response(response, limits, deadline):
    if not isinstance(response, dict) or set(response) != {
        "status", "offered", "served", "admitted", "projects", "usage",
    }:
        raise GuardedSelectionError("guarded resolver returned invalid evidence")
    offered, served, admitted = response["offered"], response["served"], response["admitted"]
    _validate_usage(response["usage"], limits)
    _validate_projects(response["projects"], limits)
    if (
        not isinstance(offered, dict) or len(offered) > limits.max_requests * limits.max_workers
        or not isinstance(served, list) or any(not isinstance(url, str) for url in served)
        or len(set(served)) != len(served)
        or not isinstance(admitted, dict) or set(admitted) != set(served)
    ):
        raise GuardedSelectionError("guarded resolver returned invalid evidence")
    for url, item in offered.items():
        inspection._check_deadline(deadline)
        if not isinstance(url, str):
            raise GuardedSelectionError("guarded resolver returned invalid candidate evidence")
        parsed = urlsplit(url)
        if (
            len(url) > 1024 or parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1" or parsed.port is None
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or not isinstance(item, dict)
            or set(item) != {"filename", "sha256"}
            or not isinstance(item["filename"], str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,250}\.whl", item["filename"]) is None
            or inspection._unsafe_windows_component(item["filename"])
            or unquote(parsed.path.rsplit("/", 1)[-1]) != item["filename"]
            or (item["sha256"] is not None and (
                not isinstance(item["sha256"], str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", item["sha256"]) is None
            ))
        ):
            raise GuardedSelectionError("guarded resolver returned invalid candidate evidence")
        project = acquisition._name(item["filename"].split("-", 1)[0])
        if response["projects"].get(project) is None:
            raise GuardedSelectionError("guarded candidate lacks its project witness")
    for url in served:
        item = admitted[url]
        if (
            url not in offered or offered[url]["sha256"] is None or not isinstance(item, dict)
            or set(item) != {"upload-time", "requires-python", "yanked", "withdrawn"}
            or item["yanked"] is not False or item["withdrawn"] is not False
            or not isinstance(item["upload-time"], str) or len(item["upload-time"]) > 128
            or not item["upload-time"].isascii() or any(ord(char) < 32 for char in item["upload-time"])
            or (item["requires-python"] not in (None, "") and (
                not isinstance(item["requires-python"], str) or len(item["requires-python"]) > 4096
            ))
        ):
            raise GuardedSelectionError("guarded resolver returned invalid served evidence")
        try:
            uploaded = datetime.fromisoformat(item["upload-time"].replace("Z", "+00:00"))  # noqa: FURB162
            if uploaded.tzinfo is None:
                raise ValueError
        except ValueError:
            raise GuardedSelectionError("guarded resolver returned invalid upload evidence") from None


def _feed_request(feed):
    if (
        not isinstance(feed, native_feed.NativeFeedInputs) or len(feed.feeds) != 1
        or not isinstance(feed.context, ssl.SSLContext) or not feed.context.check_hostname
        or feed.context.verify_mode != ssl.CERT_REQUIRED or feed.proxies
    ):
        raise GuardedSelectionError("guarded selection requires captured native feed inputs")
    origins = []
    for origin in feed.download_origins:
        location, identity = acquisition._url(origin)
        if location != identity + "/":
            raise GuardedSelectionError("guarded download trust requires exact origins")
        origins.append(identity)
    return {
        "feed": asdict(feed.feeds[0]), "origins": sorted(origins),
        "tls": acquisition._tls_record(feed.context),
    }


def _run_isolated(request, root, sources, limits, deadline, procutil):
    template = request.get("template", "")
    while True:
        content = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
        overhead = sum(map(len, sources.values())) + len(content) + 2 * len(template.encode("utf-8"))
        if overhead == request["control_bytes"]:
            break
        request["control_bytes"] = overhead
    if len(content) > _CONTROL_LIMIT or overhead >= limits.max_temporary_bytes:
        raise GuardedSelectionError("guarded resolver control exceeds its storage budget")
    worker = root / "worker"
    worker.mkdir()
    for name, value in sources.items():
        path = worker / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    control = root / "control.json"
    control.write_bytes(content)
    child_environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_", "PYTHON", "SSL_"))
        and key.upper() not in {
            "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        }
    }
    executable = sys.executable
    if os.name == "nt" and Path(executable).name.lower() == "pythonw.exe":
        executable, extra = procutil.direct_python(executable)
        child_environment.pop("__PYVENV_LAUNCHER__", None)
        child_environment.update(extra)
    launch = (
        "import sys;sys.path.insert(0,sys.argv[1]);"
        "from core_native_feed_resolution import _worker_main;_worker_main(sys.argv[2])"
    )
    try:
        with ExitStack() as held:
            for name in sources:
                held.enter_context(snapshots._open(worker / name))
            custody = held.enter_context(snapshots._open(control))
            result = run_owned(
                [executable, "-I", "-S", "-B", "-c", launch, str(worker), str(control)],
                cwd=root, env=child_environment, timeout=max(0.001, deadline - time.monotonic()),
                procutil=procutil, construction_fd=custody.fileno(), deadline=deadline,
            )
        if len(result.stdout) > _CONTROL_LIMIT:
            raise GuardedSelectionError("guarded resolver result exceeds its budget")
        response = json.loads(result.stdout, object_pairs_hook=acquisition._unique)
        if response.get("status") != "ok":
            reason = response.get("reason")
            if isinstance(reason, str) and reason.isascii() and len(reason) <= 128:
                raise GuardedSelectionError(reason)
            raise GuardedSelectionError("guarded native feed selection was refused")
        return response
    except GuardedSelectionError:
        raise
    except ChildTimeoutExpired:
        raise GuardedSelectionError("guarded native resolver deadline exceeded") from None
    except (ChildLaunchError, subprocess.CalledProcessError, OSError, ValueError, KeyError, TypeError) as error:
        raise GuardedSelectionError("guarded worker failed: " + type(error).__name__) from None


@contextmanager
def compile_guarded(
    transaction, projection: str, feed, *, floor: datetime, cutoffs: dict,
    exclusions: tuple[str, ...], target_tags: list[str], limits, wheel_limits, deadline: float, procutil,
):
    """Run native UV against inspected, origin-guarded candidates and retain their bytes."""
    inspection._check_deadline(deadline)
    limits.verify()
    if not isinstance(floor, datetime) or floor.tzinfo is None or not isinstance(exclusions, tuple):
        raise GuardedSelectionError("guarded selection requires captured native feed inputs")
    try:
        require_native_target(transaction, projection, target_tags)
    except ValueError as error:
        raise GuardedSelectionError(str(error)) from None
    source = _feed_request(feed)
    sources = _worker_sources(deadline)
    with dependencies._temporary_inputs(None) as temporary:
        root = Path(dependencies._native_retained_path(
            dependencies._private_retained_root(Path(temporary)).resolve(),
        ))
        argv, environment, template = _local_configuration(transaction, root)
        request = {
            "version": 1, "operation": "resolve", **source,
            "floor": floor.isoformat(), "cutoffs": {name: value.isoformat() for name, value in cutoffs.items()},
            "exclusions": list(exclusions), "limits": asdict(limits), "wheel_limits": asdict(wheel_limits),
            "target_tags": target_tags,
            "deadline": deadline, "control_bytes": 0,
            "argv": argv, "cwd": str(transaction.cwd), "environment": environment,
            "config": environment["UV_CONFIG_FILE"], "template": template, "projection": projection,
        }
        response = _run_isolated(request, root, sources, limits, deadline, procutil)
        _validate_response(response, limits, deadline)
        snapshot_budget = (
            limits.max_temporary_bytes - request["control_bytes"] - _CONTROL_LIMIT
            - response["usage"]["wheel_bytes"]
        )
        if snapshot_budget <= 0:
            raise GuardedSelectionError("guarded resolver exhausted its snapshot storage budget")
        yield response, root / "downloads", snapshot_budget


def verify_guarded_projects(
    feed, projects, *, limits, wheel_limits, target_tags, deadline: float, procutil,
) -> None:
    """Recheck the observed candidate indexes after final wheel acquisition."""
    inspection._check_deadline(deadline)
    limits.verify()
    _validate_projects(projects, limits)
    if not projects:
        return
    source = _feed_request(feed)
    sources = _worker_sources(deadline)
    with dependencies._temporary_inputs(None) as temporary:
        root = Path(dependencies._native_retained_path(
            dependencies._private_retained_root(Path(temporary)).resolve(),
        ))
        request = {
            "version": 1, "operation": "verify", **source, "projects": projects,
            "wheel_limits": asdict(wheel_limits), "target_tags": target_tags,
            "limits": asdict(limits), "deadline": deadline, "control_bytes": 0,
        }
        response = _run_isolated(request, root, sources, limits, deadline, procutil)
        if set(response) != {"status", "usage"}:
            raise GuardedSelectionError("guarded verifier returned invalid evidence")
        _validate_usage(response["usage"], limits)


def verify_lock_witness(content: bytes, selected: list[dict], witness: dict, deadline: float) -> dict[str, str]:
    """Require every lock URL to be offered locally and every selected wheel served."""
    try:
        document = dependencies._toml_parser().loads(content.decode("utf-8"))
        offered, served = witness["offered"], set(witness["served"])
        selected_keys = {(item["name"], item["filename"], item["sha256"]) for item in selected}
        observed = {}
        for package in document.get("packages", []):
            name = canonicalize_name(package["name"], validate=True)
            for wheel in package["wheels"]:
                inspection._check_deadline(deadline)
                url = wheel["url"]
                item = offered[url]
                digest = "sha256:" + wheel["hashes"]["sha256"]
                if item["sha256"] != digest or unquote(urlsplit(url).path.rsplit("/", 1)[-1]) != item["filename"]:
                    raise GuardedSelectionError("guarded native lock differs from the offered candidates")
                key = (name, item["filename"], digest)
                if key in selected_keys and url in served:
                    observed[key] = url
        if set(observed) != selected_keys or len({key[0] for key in selected_keys}) != len(selected_keys):
            raise GuardedSelectionError("guarded native lock contains an uninspected selection")
        return {key[0]: url for key, url in observed.items()}
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise GuardedSelectionError("guarded native lock lacks its candidate witness") from None


def verify_guarded_cutoffs(selected, urls, witness, cutoffs, *, deadline: float) -> None:
    """Reapply the acquisition owner's file-admission rules to the held selection."""
    for item in selected:
        inspection._check_deadline(deadline)
        try:
            url = urls[item["name"]]
            row = witness["offered"][url]
            admitted = witness["admitted"][url]
            cutoff = cutoffs[item["name"]]
            if not isinstance(cutoff, datetime) or cutoff.tzinfo is None:
                raise GuardedSelectionError("guarded selection lacks a valid cutoff")
            entry = {
                "filename": row["filename"], "url": url,
                "hashes": {"sha256": row["sha256"].removeprefix("sha256:")},
                "upload-time": admitted["upload-time"], "requires-python": admitted["requires-python"],
                "yanked": admitted["yanked"], "withdrawn": admitted["withdrawn"],
            }
        except (KeyError, TypeError, AttributeError):
            raise GuardedSelectionError("guarded selection lacks file-admission evidence") from None
        acquisition._selected([entry], item, url, cutoff)
