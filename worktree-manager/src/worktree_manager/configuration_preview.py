"""Optional Configuration row-form preview contract, without UI dependencies."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


def validate_preview_actions(data: Mapping[str, object]) -> None:
    """Reject preview declarations outside confirmed Configuration row forms."""
    def reject(value: object, path: str) -> None:
        if isinstance(value, Mapping):
            if "preview" in value:
                raise ValueError(f"`{path}.preview` is only supported on Configuration row forms")
            for key in ("configure_action",):
                reject(value.get(key), f"{path}.{key}")

    reject(data, "manifest")
    reject(data.get("create_action"), "create_action")
    for collection in ("view_actions", "worktree_actions", "config_sections"):
        entries = data.get(collection)
        if isinstance(entries, Sequence) and not isinstance(entries, (str, bytes)):
            for index, item in enumerate(entries):
                reject(item, f"{collection}[{index}]")
    actions = data.get("actions")
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
        return
    for index, action in enumerate(actions):
        if not isinstance(action, Mapping) or "preview" not in action:
            continue
        path = f"actions[{index}].preview"
        if (
            data.get("placement") != "config"
            or data.get("scope") not in ("account", "global")
            or action.get("kind") != "form"
            or action.get("confirm") is not True
        ):
            raise ValueError(
                f"`{path}` requires a Configuration row form with `confirm: true`"
            )
        argv = action["preview"]
        if (
            not isinstance(argv, Sequence) or isinstance(argv, (str, bytes))
            or not argv or any(not isinstance(arg, str) or not arg for arg in argv)
        ):
            raise ValueError(f"`{path}` must be a non-empty array of non-empty strings")


def configuration_target(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"installation", "machine", "resource"}:
        raise ValueError("Configuration target requires installation, machine and resource")
    target: dict[str, str] = {}
    for key in ("installation", "machine", "resource"):
        item = value[key]
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"Configuration target {key} must be a non-empty string")
        target[key] = item
    return target


@dataclass(frozen=True)
class PreviewFinding:
    code: str
    severity: str
    message: str


@dataclass(frozen=True)
class ConfigurationPreview:
    token: str
    target: dict[str, str]
    operation: str
    summary: str
    effects: tuple[str, ...]
    findings: tuple[PreviewFinding, ...]

    @property
    def blocked(self) -> bool:
        return any(finding.severity == "error" for finding in self.findings)

    def describe(self) -> str:
        lines = [
            self.summary, "", f"Operation: {self.operation}", "Scope: machine-local",
            *(f"{key}: {value}" for key, value in self.target.items()),
            "", "Effects:", *self.effects,
        ]
        if self.findings:
            lines.extend(["", "Findings:"])
            lines.extend(
                f"{item.severity}: {item.code}: {item.message}" for item in self.findings
            )
        return "\n".join(lines)


def parse_configuration_preview(output: str, expected_target: object) -> ConfigurationPreview:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate preview key: {key}")
            result[key] = value
        return result

    data = json.loads(output, object_pairs_hook=pairs)
    keys = {"version", "token", "target", "scope", "operation", "summary", "effects", "findings"}
    if not isinstance(data, dict) or set(data) != keys:
        raise ValueError("Preview must be a version-1 JSON object with the exact contract fields")
    if type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Unsupported Configuration preview version")
    token = data["token"]
    if not isinstance(token, str) or not token.strip():
        raise ValueError("Preview token must be a non-empty string")
    target = configuration_target(data["target"])
    if target != configuration_target(expected_target):
        raise ValueError("Preview target differs from the selected Configuration row")
    if data["scope"] != "machine-local" or data["operation"] not in ("set", "reset"):
        raise ValueError("Preview requires machine-local scope and a set/reset operation")
    if not isinstance(data["summary"], str):
        raise ValueError("Preview summary must be a string")
    effects = data["effects"]
    if not isinstance(effects, list) or any(not isinstance(item, str) for item in effects):
        raise ValueError("Preview effects must be an array of strings")
    findings = data["findings"]
    if not isinstance(findings, list):
        raise ValueError("Preview findings must be an array")
    parsed = []
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or set(finding) != {"code", "severity", "message"}
            or any(not isinstance(finding[key], str) for key in finding)
            or not finding["code"].strip()
            or finding["severity"] not in ("error", "warning", "info")
        ):
            raise ValueError("Malformed Configuration preview finding")
        parsed.append(PreviewFinding(**finding))
    return ConfigurationPreview(
        token, target, data["operation"], data["summary"], tuple(effects), tuple(parsed),
    )
