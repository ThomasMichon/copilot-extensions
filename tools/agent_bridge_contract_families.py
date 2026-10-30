"""Route semantic corpus validation to each generation's existing owner."""

from pathlib import Path


def validate_http_families(
    root: Path, contract: dict, loaded: dict, errors: list[str], current: int,
) -> None:
    from bridge_http45_evidence import validate_families

    validate_families(contract["fixtures"], loaded, errors)
    if current >= 47:
        from bridge_http47_validation import validate_http47
        validate_http47(root, {"contracts": [contract]}, loaded, errors)
    if current >= 48:
        from bridge_http48_evidence import validate_http48
        validate_http48(root, {"contracts": [contract]}, loaded, errors)
    if current >= 49:
        from bridge_http49_evidence import validate_http49
        validate_http49(root, {"contracts": [contract]}, loaded, errors)
    if current >= 50:
        from bridge_http50_evidence import validate_http50
        validate_http50(root, {"contracts": [contract]}, loaded, errors)
    if current >= 51:
        from bridge_http51_evidence import validate_http51
        validate_http51(root, {"contracts": [contract]}, loaded, errors)
    if current >= 52:
        from bridge_http52_evidence import validate_http52
        validate_http52(root, {"contracts": [contract]}, loaded, errors)
