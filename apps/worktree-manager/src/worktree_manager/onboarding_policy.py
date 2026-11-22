"""Read addressed activation policy in a fresh installed Manager interpreter."""
from __future__ import annotations

from importlib import import_module
from pathlib import Path
from types import ModuleType
import sys


def observe(app: str, root: Path, project: str, anchor: Path, companion: Path | None, target: str) -> str:
    kit = Path(__file__).resolve().parent / "_runtime_assets/scripts/runtime-source-tools/policy"
    if not kit.is_dir():
        raise ValueError("Missing packaged activation readers")
    sys.path.insert(0, str(kit))

    def owner(name: str) -> ModuleType:
        module = import_module(name)
        if not Path(module.__file__ or "").resolve().is_relative_to(kit):
            raise ValueError("Foreign activation reader")
        return module

    owner("configuration_layers")
    owner("configuration_context")
    context = {"base_root": str(anchor), "repository_id": project, "installation_cell": str(root),
               "execution_machine": target, "binding": "bound" if companion else "unbound"}
    if companion:
        context["knowledge_root"] = str(companion)
    local = root / "repos" / project
    if app == "agent-index":
        native = owner("resolve_effective_config")
        status, _, _ = native._load_repo_candidate(anchor, compose_preferences=False)
        if status == "absent":
            state, external = native._requires_external(anchor)
            if state != "ready" or (external and not companion):
                raise ValueError("Index configuration root unresolved")
            if external:
                status, _, _ = native._load_repo_candidate(companion, compose_preferences=False)
        if status == "absent":
            return "unconfigured"
        if status != "ready":
            raise ValueError("Invalid native Index activation configuration")
        index_config = owner("agent_index.config")
        machine_path = index_config.config_path()
        if not machine_path.is_absolute():
            raise ValueError("Index machine configuration path must be absolute")
        result = owner("agent_index.configuration_layers").explicit_repository_layers(
            anchor, context, local, machine_path, parser=native._parse_yaml_for_layers,
            marketplace=index_config._repo_marketplace_overlay_path(anchor))
        if result["layers"] == ["defaults"]:
            return "unconfigured"
        machines = {x["machine"].casefold() for x in native._validate_config(result["values"])["indexers"]}
        index_machine = index_config.machine_id().casefold()
        return ("host" if index_machine in machines else "client") if machines else "unconfigured"
    if app == "agent-vault":
        vault = owner("agent_vault.configuration")
        vault_config = owner("agent_vault.config")
        machine_path = vault_config._global_config_path()
        data = vault_config.load_global_config(strict=True)
        document = owner("configuration_layers").read_source(str(anchor), ".agent-vault.json")
        repository = document["values"] if document else {}
        validate = vault_config._validate_config_data
        validate(repository, "repository vault configuration")
        validate(data, "machine vault configuration")
        result = vault.explicit_preferences(anchor, context, str(local), repository, data, {}, anchor, machine_path.parent)
        return "configured" if result["values"]["enabled"] else "configuration-empty"
    raise ValueError("Unsupported activation reader")


if __name__ == "__main__":
    app, root, project, anchor, knowledge, target = sys.argv[1:]
    print(observe(app, Path(root), project, Path(anchor), Path(knowledge) if knowledge else None, target))
