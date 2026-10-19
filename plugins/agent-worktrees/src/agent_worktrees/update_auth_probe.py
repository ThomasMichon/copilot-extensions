"""Isolated, bounded identity probe for Git's native HTTP credential schemes."""

from __future__ import annotations

import json
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .repo_accounts import remote_host
from .update_auth_process import publish_group


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def login(host: str, authtype: str, credential: str) -> dict[str, str]:
    try:
        host = remote_host(host)
    except ValueError:
        return {"status": "denied"}
    authority = urlsplit("https://" + host)
    if (
        not authority.hostname or authority.username or authority.password
        or authority.path or authority.query or authority.fragment
        or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", authtype)
        or not credential or any(char in credential for char in "\r\n\0")
    ):
        return {"status": "denied"}
    endpoint = (
        "https://api.github.com/user"
        if host.casefold() == "github.com"
        else "https://" + host + "/api/v3/user"
    )
    authorization = authtype + " " + credential
    request = Request(
        endpoint,
        headers={
            "Authorization": authorization,
            "Accept": "application/vnd.github+json",
            "User-Agent": "copilot-extensions-update-auth",
        },
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=4) as response:
            body = response.read(65537)
        if len(body) > 65536:
            return {"status": "denied"}
        payload = json.loads(body)
        observed = payload.get("login") if isinstance(payload, dict) else None
        if not isinstance(observed, str) or not observed or len(observed) > 256:
            return {"status": "denied"}
        return {"status": "ok", "login": observed}
    except HTTPError:
        return {"status": "denied"}
    except TimeoutError:
        return {"status": "timeout"}
    except URLError as error:
        return {"status": "timeout" if isinstance(error.reason, TimeoutError) else "unavailable"}
    except (OSError, ValueError):
        return {"status": "unavailable"}


def main() -> int:
    publish_group()
    try:
        spec = json.loads(sys.stdin.read(1024 * 1024))
        if not isinstance(spec, dict) or any(
            not isinstance(spec.get(key), str) for key in ("host", "authtype", "credential")
        ):
            raise ValueError("invalid native identity probe")
        result = login(spec["host"], spec["authtype"], spec["credential"])
    except (TypeError, ValueError):
        result = {"status": "denied"}
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
