"""Native caller permission evaluation; not credential-grant or audience verification."""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlencode
from uuid import UUID


def _uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("native security identity is unavailable")
    parsed = str(UUID(value))
    if parsed != value.lower() or parsed == str(UUID(int=0)):
        raise ValueError("native security identity is unavailable")
    return parsed


def _collection(body: dict[str, Any]) -> list[Any]:
    values = body.get("value")
    if (
        not isinstance(values, list) or type(body.get("count")) is not int
        or body["count"] != len(values)
    ):
        raise ValueError("native permission information is incomplete")
    return values


def _namespace(body: dict[str, Any]) -> tuple[str, tuple[int, int]]:
    values = _collection(body)
    if any(not isinstance(value, dict) for value in values):
        raise ValueError("native security namespaces are malformed")
    matches = [value for value in values if value.get("name") == "Git Repositories"]
    if len(matches) != 1:
        raise ValueError("native Git security namespace is unavailable")
    selected = matches[0]
    namespace = _uuid(selected.get("namespaceId"))
    if (
        selected.get("separatorValue") != "/" or type(selected.get("structureValue")) is not int
        or selected["structureValue"] != 1 or type(selected.get("useTokenTranslator")) is not bool
        or not isinstance(selected.get("actions"), list)
    ):
        raise ValueError("native Git security namespace is unsupported")
    actions, bits = {}, set()
    for action in selected["actions"]:
        if not isinstance(action, dict):
            raise ValueError("native Git security action is malformed")
        name, bit = action.get("name"), action.get("bit")
        if (
            not isinstance(name, str) or not name or name in actions
            or type(bit) is not int or not 0 < bit < 2**31 or bit & (bit - 1) or bit in bits
            or _uuid(action.get("namespaceId")) != namespace
        ):
            raise ValueError("native Git security actions are ambiguous")
        actions[name] = bit
        bits.add(bit)
    if (
        not {"GenericRead", "PullRequestContribute"} <= actions.keys()
        or type(selected.get("readPermission")) is not int
        or selected["readPermission"] != actions["GenericRead"]
    ):
        raise ValueError("native Git review actions are unavailable")
    return namespace, (actions["GenericRead"], actions["PullRequestContribute"])


def _tokens(scope: dict[str, Any], pr: dict[str, Any]) -> tuple[str, str]:
    repository = pr.get("repository")
    if not isinstance(repository, dict) or not isinstance(repository.get("project"), dict):
        raise ValueError("native repository identity is unavailable")
    if (
        repository.get("id") != scope["repository_id"]
        or repository["project"].get("id") != scope["project_id"]
        or type(pr.get("pullRequestId")) is not int or pr["pullRequestId"] != scope["number"]
    ):
        raise ValueError("native permission target differs from selected PR")
    project, repo = _uuid(repository["project"]["id"]), _uuid(repository["id"])
    ref = pr.get("targetRefName")
    if not isinstance(ref, str) or not ref.startswith("refs/heads/") or len(ref) > 1024:
        raise ValueError("native target branch is unavailable")
    branch = ref[len("refs/heads/"):]
    parts = branch.split("/")
    if any(
        not part or part.startswith(".") or part.endswith((".", ".lock"))
        or ".." in part or "@{" in part
        or any(ord(char) < 33 or ord(char) == 127 or char in "~^:?*[\\" for char in part)
        for part in parts
    ):
        raise ValueError("native target branch is unsupported")
    encoded = "/".join(part.encode("utf-16-le").hex() for part in parts)
    root = f"repoV2/{project}/{repo}"
    return root, root + "/refs/heads/" + encoded + "/"


def require_review_permissions(
    read_json: Callable[[str], dict[str, Any]], *, scope: dict[str, Any], pr: dict[str, Any],
) -> None:
    """Require native read/review permission at repository and exact target branch.

    Both levels are required deliberately: a repository result alone misses a
    branch denial, while a branch-only grant is not repository-wide PR access.
    No response is cached across preflight or dispatch.
    """
    tokens = _tokens(scope, pr)
    namespace, bits = _namespace(read_json(scope["endpoint"] + "/_apis/securitynamespaces?api-version=7.1"))
    for bit in bits:
        url = scope["endpoint"] + f"/_apis/permissions/{namespace}/{bit}?" + urlencode({
            "tokens": ",".join(tokens), "alwaysAllowAdministrators": "false", "api-version": "7.1",
        })
        values = _collection(read_json(url))
        if len(values) != len(tokens) or any(type(value) is not bool for value in values):
            raise ValueError("native permission results are incomplete")
        if not all(values):
            raise PermissionError("native read/review permission is denied")
