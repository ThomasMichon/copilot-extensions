"""Schema-neutral safe YAML decoding with duplicate mapping-key rejection."""

from __future__ import annotations

import yaml


def load_strict_yaml_document(content: str, *, label: str = "YAML document") -> object:
    """Decode safe YAML without letting duplicate keys erase prior evidence."""
    class StrictLoader(yaml.SafeLoader):
        pass

    def mapping(loader: yaml.SafeLoader, node: yaml.Node) -> dict:
        if not isinstance(node, yaml.MappingNode):
            raise ValueError(f"invalid mapping node in {label}")
        loader.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if key in result:
                raise ValueError(f"duplicate mapping key in {label}")
            result[key] = loader.construct_object(value_node)
        return result

    StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    return yaml.load(content, Loader=StrictLoader)
