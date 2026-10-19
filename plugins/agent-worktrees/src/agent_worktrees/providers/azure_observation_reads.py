"""Bounded native ADO reads for the centralized PR driver, not a public HTTP tool."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from http.client import HTTPResponse, HTTPSConnection
from io import BufferedReader, RawIOBase
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from .base import ProviderError


MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 20_000


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _ReadDeadlineExpired(TimeoutError):
    pass


class _DeadlineRaw(RawIOBase):
    def __init__(self, raw, sock, deadline, clock):
        self.raw, self.sock = raw, sock
        self.deadline, self.clock = deadline, clock

    def readable(self):
        return True

    def readinto(self, buffer):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise _ReadDeadlineExpired("ADO observation read budget exhausted")
        self.sock.settimeout(min(20, remaining))
        try:
            return self.raw.readinto(buffer)
        except TimeoutError as exc:
            if remaining <= 20:
                raise _ReadDeadlineExpired("ADO observation read budget exhausted") from exc
            raise

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class _DeadlineSocket:
    def __init__(self, sock, deadline, clock):
        self.sock, self.deadline, self.clock = sock, deadline, clock

    def __getattr__(self, name):
        return getattr(self.sock, name)

    def makefile(self, mode):
        raw = self.sock.makefile(mode, buffering=0)
        return BufferedReader(_DeadlineRaw(raw, self.sock, self.deadline, self.clock))


class _DeadlineHTTPSConnection(HTTPSConnection):
    def __init__(self, host, *, deadline, clock, **kwargs):
        super().__init__(host, **kwargs)

        def response(sock, *args, **options):
            # Wrap below HTTP buffering, including chunk framing and proxy headers.
            return HTTPResponse(_DeadlineSocket(sock, deadline, clock), *args, **options)

        self.response_class = response


class _DeadlineHTTPSHandler(HTTPSHandler):
    def __init__(self, deadline, clock):
        super().__init__()
        self.deadline, self.clock = deadline, clock

    def https_open(self, request):
        def connection(host, **kwargs):
            return _DeadlineHTTPSConnection(
                host, deadline=self.deadline, clock=self.clock, **kwargs,
            )

        return self.do_open(connection, request, context=self._context)


@dataclass(frozen=True)
class Collection:
    records: tuple[dict, ...] = ()
    error: str = ""

    @property
    def complete(self) -> bool:
        return not self.error


class AdoReader:
    def __init__(self, provider, repo, number, api_base, token, *, opener=None,
                 clock=time.monotonic, budget=120):
        parsed = urlsplit(api_base)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ProviderError("ADO observation requires a configured HTTPS organization endpoint")
        project, repository = provider._split_repo(repo)
        self.base = (api_base.rstrip("/") + "/" + quote(project, safe="")
                     + "/_apis/git/repositories/" + quote(repository, safe="")
                     + f"/pullRequests/{number}")
        self.project_base = self.base.split("/_apis/git/", 1)[0] + "/_apis"
        header, error = provider._auth_header(token)
        if error:
            raise ProviderError("ADO observation authentication is unavailable")
        name, separator, value = header.partition(":")
        if not separator or name.casefold() != "authorization" or not value.strip():
            raise ProviderError("ADO observation received an invalid authentication binding")
        self._authorization = value.strip()
        self.clock = clock
        self.deadline = clock() + budget
        self.opener = opener or build_opener(NoRedirect(), _DeadlineHTTPSHandler(self.deadline, clock))

    def json(self, url, *, query=None) -> tuple[dict, str]:
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise ProviderError("ADO observation read budget exhausted", transient=True)
        if query:
            url += ("&" if "?" in url else "?") + urlencode(query)
        request = Request(url, headers={"Authorization": self._authorization, "Accept": "application/json"})
        try:
            with self.opener.open(request, timeout=min(20, remaining)) as response:
                if response.status != 200:
                    raise ProviderError(f"ADO observation GET returned HTTP {response.status}")
                data = response.read(MAX_RESPONSE_BYTES + 1)
                continuation = response.headers.get("x-ms-continuationtoken", "")
        except HTTPError as exc:
            raise ProviderError(f"ADO observation GET returned HTTP {exc.code}",
                                transient=exc.code in (408, 429) or exc.code >= 500) from exc
        except (URLError, OSError, TimeoutError) as exc:
            if (isinstance(exc, _ReadDeadlineExpired)
                    or isinstance(getattr(exc, "reason", None), _ReadDeadlineExpired)
                    or self.clock() >= self.deadline):
                raise ProviderError("ADO observation read budget exhausted", transient=True) from exc
            raise ProviderError("ADO observation transport unavailable", transient=True) from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise ProviderError("ADO observation response exceeds its byte limit")
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError) as exc:
            raise ProviderError("ADO observation returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise ProviderError("ADO observation returned an unexpected JSON shape")
        return result, continuation

    def collection(self, url, *, skip_paging=False, continuation_paging=False) -> Collection:
        records = []
        seen_pages = set()
        seen_tokens = set()
        token = ""
        try:
            for page in range(201):
                query = {"$top": 100, "$skip": page * 100} if skip_paging else None
                if continuation_paging:
                    query = {"$top": 100, **({"continuationToken": token} if token else {})}
                body, continuation = self.json(url, query=query)
                values = body.get("value")
                if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
                    raise ProviderError("ADO observation collection has an invalid shape")
                if continuation and not continuation_paging:
                    raise ProviderError("ADO observation collection supplied an unsupported continuation")
                count = body.get("count")
                if count is not None and (type(count) is not int or count != len(values)):
                    raise ProviderError("ADO observation collection count is inconsistent")
                signature = json.dumps(values, sort_keys=True)
                if values and signature in seen_pages:
                    raise ProviderError("ADO observation pagination repeated a page")
                seen_pages.add(signature)
                records.extend(values)
                if len(records) > MAX_RECORDS:
                    raise ProviderError("ADO observation collection exceeds its record limit")
                if continuation_paging and continuation:
                    if not isinstance(continuation, str) or continuation in seen_tokens:
                        raise ProviderError("ADO observation continuation repeated or is invalid")
                    seen_tokens.add(continuation)
                    token = continuation
                    continue
                if continuation_paging or not skip_paging or len(values) < 100:
                    return Collection(tuple(records))
            raise ProviderError("ADO observation pagination limit reached")
        except ProviderError as exc:
            return Collection(tuple(records), str(exc))

    def pr(self) -> dict:
        body, continuation = self.json(self.base + "?api-version=7.1")
        if continuation:
            raise ProviderError("ADO PR metadata unexpectedly requires continuation")
        return body

    def iterations(self) -> Collection:
        return self.collection(self.base + "/iterations?api-version=7.1")

    def threads(self) -> Collection:
        return self.collection(self.base + "/threads?api-version=7.1")

    def statuses(self) -> Collection:
        return self.collection(self.base + "/statuses?api-version=7.1")

    def evaluations(self, project_id, number) -> Collection:
        artifact = f"vstfs:///CodeReview/CodeReviewId/{project_id}/{number}"
        url = self.project_base + "/policy/evaluations?" + urlencode({
            "artifactId": artifact, "includeNotApplicable": "true", "api-version": "7.1-preview.1",
        })
        return self.collection(url, skip_paging=True)

    def configurations(self, repository_id, ref_name) -> Collection:
        if not repository_id or not ref_name:
            return Collection(error="Target branch policy scope is unavailable")
        url = self.project_base + "/git/policy/configurations?" + urlencode({
            "repositoryId": repository_id, "refName": ref_name, "api-version": "7.1",
        })
        return self.collection(url, continuation_paging=True)

    def build(self, number) -> dict:
        body, continuation = self.json(self.project_base + f"/build/builds/{number}?api-version=7.1")
        if continuation:
            raise ProviderError("ADO build metadata unexpectedly requires continuation")
        return body

    def failed_tasks(self, number) -> tuple[dict, ...]:
        body, continuation = self.json(self.project_base + f"/build/builds/{number}/timeline?api-version=7.1")
        if continuation or not isinstance(body.get("records"), list):
            raise ProviderError("ADO build timeline evidence is incomplete")
        return tuple({
            "task": record.get("name", ""), "log": (record.get("log") or {}).get("url", ""),
            "errors": [str(issue.get("message", ""))[:300]
                       for issue in record.get("issues", []) if issue.get("type") == "error"][:3],
        } for record in body["records"]
            if record.get("type") == "Task" and record.get("result") == "failed")[:5]
