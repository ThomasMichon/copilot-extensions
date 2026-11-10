"""Private session-custody negotiation, independent of package release labels."""

MAX_APPLICATION = 64
MAX_CONTROL = 32

CONTRACT = {
    "name": "agent-mcp.connection-custody",
    "minimum": 1,
    "maximum": 1,
    "capabilities": ["quiescent-checkpoint", "retained-upstream", "exclusive-epoch"],
}


def supports(value) -> bool:
    return (
        isinstance(value, dict)
        and value.get("name") == CONTRACT["name"]
        and type(value.get("minimum")) is int
        and type(value.get("maximum")) is int
        and value["minimum"] <= 1 <= value["maximum"]
        and isinstance(value.get("capabilities"), list)
        and all(capability in value["capabilities"] for capability in CONTRACT["capabilities"])
    )
