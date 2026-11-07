"""Registry-owned adoption defaults and their original acquisition boundaries."""

from __future__ import annotations

from typing import Any

from . import config_acquisition as acquisition


def resolve_adoption_defaults(name: str, platform: str) -> dict[str, Any]:
    """Keep registry order, coercions and partial defaults without reacquisition."""
    out: dict[str, Any] = {}
    start = acquisition.registry_call_started()
    try:
        from . import repos as repos_mod

        entry = repos_mod.read_registry().repos.get(name)
        if entry is not None and entry.default_branch:
            out["default_branch"] = entry.default_branch
    except Exception as exc:
        acquisition.registry_call_failed("adoption-repository-registry", exc)
    finally:
        acquisition.registry_call_finished(start, "adoption-repository-registry")
    start = acquisition.registry_call_started()
    reader = None
    try:
        from . import installer

        reader = installer.read_projects_registry
        proj = (reader().get("projects") or {}).get(name)
        if isinstance(proj, dict):
            if "default_branch" not in out and proj.get("default_branch"):
                out["default_branch"] = str(proj["default_branch"])
            if proj.get("base_repo") is not None:
                out["base_repo"] = bool(proj["base_repo"])
    except Exception as exc:
        acquisition.registry_call_failed("adoption-projects-registry", exc)
    finally:
        if reader is not None and reader is not getattr(installer, "_ORIGINAL_PROJECTS_READER", None):
            acquisition.opaque_source("adoption-projects-registry")
        acquisition.registry_call_finished(start, "adoption-projects-registry", stage="projects-registry")
    return out
