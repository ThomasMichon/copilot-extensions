"""Schema-neutral safe YAML decoding with duplicate mapping-key rejection."""

from __future__ import annotations

import yaml


class _StrictLoader(yaml.SafeLoader):
    label: str = "YAML document"


def _mapping(loader: _StrictLoader, node: yaml.Node) -> dict:
    if not isinstance(node, yaml.MappingNode):
        raise ValueError(f"invalid mapping node in {loader.label}")
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        mark = key_node.start_mark
        location = f"{loader.label} at line {mark.line + 1}, column {mark.column + 1}"
        try:
            duplicate = key in result
        except TypeError as exc:
            raise ValueError(f"unhashable mapping key in {location}") from exc
        if duplicate:
            raise ValueError(
                f"duplicate mapping key in {location}: {key!r}"
            )
        result[key] = loader.construct_object(value_node)
    return result


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def load_strict_yaml_document(content: str, *, label: str = "YAML document") -> object:
    """Decode safe YAML without letting duplicate keys erase prior evidence."""
    loader = _StrictLoader(content)
    loader.label = label
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()
