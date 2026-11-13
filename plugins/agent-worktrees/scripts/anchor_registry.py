"""Original standalone-anchor registry acquisition and optional observations."""

from pathlib import Path


def _inventory_observation(observations, path, parser, *, reason="") -> None:
    if observations is not None:
        observations.append({
            "source": str(path), "parser": parser,
            "status": "unavailable" if reason else "observed", "reason": reason,
        })


def _repos_yaml(registry_root: Path) -> Path:
    return registry_root / "repos.yaml"


def _yaml_unquote(val: str) -> str:
    val = val.strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        quote = val[0]
        val = val[1:-1]
        if quote == '"':
            val = val.replace("\\\\", "\\")
    return val


def load_base_repo_names(registry_root: Path, *, _observations=None) -> set[str]:
    """Original projects.yaml base-repository exclusions; never raises."""
    source = registry_root / "projects.yaml"
    try:
        text = source.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _inventory_observation(_observations, source, "none", reason=type(exc).__name__)
        return set()
    try:
        import yaml  # type: ignore

        projects = (yaml.safe_load(text) or {}).get("projects") or {}
        names = {
            str(name) for name, meta in projects.items()
            if isinstance(meta, dict) and meta.get("base_repo") is True
        } if isinstance(projects, dict) else set()
        _inventory_observation(
            _observations, source, "pyyaml",
            reason="" if isinstance(projects, dict) else "invalid-projects-shape",
        )
        return names
    except Exception as exc:
        fallback_reason = "" if isinstance(exc, ImportError) else type(exc).__name__
    names: set[str] = set()
    in_projects = False
    cur_name: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if indent == 0:
            in_projects = stripped == "projects:"
            cur_name = None
        elif in_projects and indent == 2 and stripped.endswith(":"):
            cur_name = _yaml_unquote(stripped[:-1])
        elif in_projects and indent >= 4 and cur_name:
            key, sep, val = stripped.partition(":")
            if sep and key.strip() == "base_repo" and _yaml_unquote(val).lower() == "true":
                names.add(cur_name)
    _inventory_observation(_observations, source, "fallback", reason=fallback_reason)
    return names


def _load_worktree_class_anchors(registry_root: Path, *, _observations=None) -> list[dict]:
    """Original exact worktree-class rows across all platform keys; never raises."""
    source = _repos_yaml(registry_root)
    try:
        text = source.read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _inventory_observation(_observations, source, "none", reason=type(exc).__name__)
        return []
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        anchors: list[dict] = []
        repos = (data or {}).get("repos") or {}
        if isinstance(repos, dict):
            for name, meta in repos.items():
                if not isinstance(meta, dict) or meta.get("class") != "worktree":
                    continue
                for plat in ("windows", "wsl", "linux"):
                    p = meta.get(plat)
                    if isinstance(p, str) and p.strip():
                        anchors.append({"name": str(name), "path": p.strip()})
        _inventory_observation(
            _observations, source, "pyyaml",
            reason="" if isinstance(repos, dict) else "invalid-repos-shape",
        )
        return anchors
    except Exception as exc:
        fallback_reason = "" if isinstance(exc, ImportError) else type(exc).__name__
    anchors = []
    in_repos = False
    cur_name: str | None = None
    cur_class: str | None = None
    cur_paths: list[str] = []

    def flush() -> None:
        if cur_name and cur_class == "worktree":
            for p in cur_paths:
                anchors.append({"name": cur_name, "path": p})

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        stripped = raw.strip()
        if indent == 0:
            flush()
            cur_name, cur_class, cur_paths = None, None, []
            in_repos = stripped.rstrip() == "repos:"
            continue
        if not in_repos:
            continue
        if indent == 2 and stripped.endswith(":"):
            flush()
            cur_name = _yaml_unquote(stripped[:-1])
            cur_class, cur_paths = None, []
            continue
        if indent >= 4 and cur_name:
            key, sep, val = stripped.partition(":")
            if not sep:
                continue
            key = key.strip()
            if key == "class":
                cur_class = _yaml_unquote(val)
            elif key in ("windows", "wsl", "linux"):
                v = _yaml_unquote(val)
                if v:
                    cur_paths.append(v)
    flush()
    _inventory_observation(_observations, source, "fallback", reason=fallback_reason)
    return anchors
