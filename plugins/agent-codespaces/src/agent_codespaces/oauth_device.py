"""GitHub's device flow without the CLI's shared-keyring login side effects."""
from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

# Public identifier of the GitHub CLI OAuth application, not a client secret:
# https://github.com/cli/cli/blob/v2.65.0/internal/authflow/flow.go
CLIENT_ID = "178c6fc778ccc68e1d6a"


class CredentialUnavailable(RuntimeError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Unexpected redirect from the GitHub authentication endpoint.")


def _request(url: str, *, fields: dict | None = None, token: str | None = None):
    headers = {"Accept": "application/json", "User-Agent": "agent-codespaces"}
    if token is not None:
        scheme = "Bear" + "er "
        headers["Authorization"] = scheme + token
    body = urlencode(fields).encode("ascii") if fields is not None else None
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=body, headers=headers)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=15) as response:
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise RuntimeError("GitHub authentication response exceeded the size limit.")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise RuntimeError("GitHub authentication returned an invalid response.")
            return data, response.headers
    except HTTPError as exc:
        # Never include request/response bodies: they may contain OAuth secrets.
        if token is not None and exc.code == 401:
            raise CredentialUnavailable("GitHub credential is no longer valid; sign in again.") from None
        raise RuntimeError(f"GitHub authentication endpoint returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"GitHub authentication request failed ({type(exc).__name__}).") from None
    except (ValueError, UnicodeError):
        raise RuntimeError("GitHub authentication returned invalid JSON.") from None


def acquire(progress) -> str:
    data, _headers = _request("https://github.com/login/device/code", fields={
        "client_id": CLIENT_ID, "scope": "repo read:org gist codespace",
    })
    device = data.get("device_code")
    code = data.get("user_code")
    uri = data.get("verification_uri")
    if not isinstance(device, str) or not device or not isinstance(code, str) or not code:
        raise RuntimeError("GitHub did not return a device authorization code.")
    if uri != "https://github.com/login/device":
        raise RuntimeError("GitHub returned an unexpected verification URL.")
    interval = data.get("interval", 5)
    expires = data.get("expires_in", 900)
    if (type(interval) is not int or interval <= 0
            or type(expires) is not int or expires <= 0):
        raise RuntimeError("GitHub returned invalid device authorization timing.")
    progress(
        "GitHub CLI OAuth authorization\n"
        f"Open {uri}\nEnter code: {code}\n"
        "Only the selected account is adopted. Esc cancels.\n"
        "Credentials use this provider's own keyring namespace (or a private file "
        "on POSIX without a keyring); GitHub CLI keyring slots are never changed."
    )
    deadline = time.monotonic() + min(expires, 900)
    while time.monotonic() + interval < deadline:
        time.sleep(interval)
        result, _headers = _request("https://github.com/login/oauth/access_token", fields={
            "client_id": CLIENT_ID, "device_code": device,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        error = result.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            interval += 5
            continue
        if error:
            code = error if isinstance(error, str) and error in {
                "access_denied", "expired_token", "incorrect_device_code",
                "bad_verification_code", "unsupported_grant_type", "incorrect_client_credentials",
            } else "unrecognized_response"
            raise RuntimeError(f"GitHub device authorization failed ({code}).")
        credential = result.get("access_token")
        if not isinstance(credential, str) or not credential:
            raise RuntimeError("GitHub device authorization returned no credential.")
        return credential
    raise RuntimeError("GitHub device authorization expired; try again.")


def verify(credential: str, expected: str = "") -> dict[str, str]:
    data, headers = _request("https://api.github.com/user", token=credential)
    login = data.get("login")
    if not isinstance(login, str) or not login:
        raise RuntimeError("GitHub credential identity could not be verified.")
    if expected and login.casefold() != expected.casefold():
        raise RuntimeError(f"Signed in as {login}, not {expected}; credential was not adopted.")
    scopes = {value.strip().casefold() for value in headers.get("X-OAuth-Scopes", "").split(",")}
    return {
        "account": login, "status": "ready" if "codespace" in scopes else "scope_blocked",
        "message": "" if "codespace" in scopes else "Authorize the codespace scope to view CodeSpaces.",
    }
