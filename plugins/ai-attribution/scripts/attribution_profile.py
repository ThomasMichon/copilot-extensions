#!/usr/bin/env python3
"""Shared, data-only signature profiles and pure draft decoration."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import stat
import string
import sys
import unicodedata
from pathlib import Path

MAX_BYTES = 65536
MAX_RULES = 32
AXES = ("domain", "visibility", "activity")
SLOTS = ("header", "footer")
FIELDS = {"operator", "assistant", "review_status"}
DEFAULTS = {"header": "[AI-assisted] ", "footer": ""}
TOKEN = re.compile(r"^(?:\*|[a-z][a-z0-9-]{0,47})$")


class ProfileError(ValueError):
    """Invalid or ambiguous inert profile data."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError("duplicate JSON key")
        result[key] = value
    return result


def _check_markup(value: str) -> None:
    if (
        any(unicodedata.category(c) == "Cf" for c in value)
        or re.search(
            r"<|&(?:#[^;]*;|[A-Za-z][A-Za-z0-9]*;)|"
            r"!\[|\[[^\]\n]*\]\s*[\[(]|^\s{0,3}\[[^\]\n]+\]:|`{3}|~{3}",
            value, re.M,
        )
    ):
        raise ProfileError("templates support visible text/emphasis, not hidden or active markup")
    structural = re.sub(r"\\[" + re.escape(string.punctuation) + "]", "", value)
    depth = 0
    for character in structural:
        if character == "[":
            depth += 1
        elif character == "]":
            depth -= 1
            if depth < 0:
                break
    if depth:
        raise ProfileError("markup fragments must not span slots or draft content")


def _template(value: object) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > 512:
        raise ProfileError("template must be a string of at most 512 bytes")
    if any(unicodedata.category(c) == "Cc" and c != "\n" for c in value):
        raise ProfileError("template contains controls")
    _check_markup(value)
    try:
        for _, name, spec, conversion in string.Formatter().parse(value):
            if name is not None and (name not in FIELDS or spec or conversion):
                raise ProfileError("unsupported interpolation")
    except ValueError as exc:
        raise ProfileError("invalid interpolation") from exc
    if re.search(r"\breviewed\b", value.replace("{review_status}", ""), re.I):
        raise ProfileError("review claims must use {review_status}")
    return value


def validate(value: object) -> dict:
    if not isinstance(value, dict) or type(value.get("version")) is not int:
        raise ProfileError("profile needs integer version 1")
    if value["version"] != 1 or set(value) - {"version", "variables", "rules"}:
        raise ProfileError("unsupported profile version or keys")
    variables = value.get("variables", {})
    if not isinstance(variables, dict) or set(variables) - {"operator", "assistant"}:
        raise ProfileError("unsupported variables")
    for text in variables.values():
        if (
            not isinstance(text, str) or len(text.encode("utf-8")) > 120
            or any(unicodedata.category(c) in {"Cc", "Cf"} for c in text)
        ):
            raise ProfileError("invalid variable")
    rules = value.get("rules", [])
    if not isinstance(rules, list) or len(rules) > MAX_RULES:
        raise ProfileError("profile allows at most 32 rules")
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) - {
            "when", "header", "footer", "required", "header_variants", "footer_variants"
        }:
            raise ProfileError("unsupported rule")
        when = rule.get("when", {})
        if not isinstance(when, dict) or set(when) - set(AXES):
            raise ProfileError("unsupported selector")
        if any(not isinstance(v, str) or not TOKEN.fullmatch(v) for v in when.values()):
            raise ProfileError("invalid selector")
        selector = tuple(when.get(k, "*") for k in AXES)
        if selector in seen:
            raise ProfileError("duplicate selector in one layer")
        seen.add(selector)
        if "required" in rule and rule["required"] is not True:
            raise ProfileError("a profile can only tighten required disclosure")
        for slot in SLOTS:
            if slot in rule:
                _template(rule[slot])
            variants = rule.get(slot + "_variants", [])
            if not isinstance(variants, list) or len(variants) > 4:
                raise ProfileError("at most four owned variants per slot")
            for variant in variants:
                if not _template(variant):
                    raise ProfileError("empty variant")
    return value


def profile_paths(
    home: Path, repo: Path | None, env: dict | None = None, *,
    excluded: list[Path] | None = None,
) -> list[Path]:
    """Repository presentation defaults, trusted instruction defaults, personal."""
    env = os.environ if env is None else env
    raw_dirs = env.get("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "")
    if len(raw_dirs) > MAX_BYTES:
        raise ProfileError("instruction directories exceed character limit")
    directories = re.split("[" + re.escape(os.pathsep) + ",]", raw_dirs)
    if len(directories) > 128:
        raise ProfileError("instruction directories exceed entry limit")
    paths = [repo / ".github" / "ai-attribution.json"] if repo else []
    for raw in directories:
        if not raw.strip():
            continue
        directory = Path(raw.strip()).expanduser()
        if not directory.is_absolute():
            directory = Path.cwd() / directory
        try:
            if repo and _safe_file(directory) and directory.resolve().is_relative_to(repo.resolve()):
                if excluded is not None:
                    excluded.append(directory)
                continue
        except (OSError, RuntimeError):
            pass
        paths.append(directory / "ai-attribution.json")
    paths.append(home / ".copilot" / "ai-attribution.json")
    platform = env.get("XDG_CONFIG_HOME") or (
        env.get("APPDATA") if os.name == "nt" else None
    )
    paths.append((Path(platform) if platform else home / ".config") /
                 "ai-attribution" / "config.json")
    return list(dict.fromkeys(reversed(paths)))[::-1]


def _safe_file(path: Path) -> bool:
    for component in (path, *path.parents):
        if component.is_symlink() or (
            component.exists() and getattr(component.lstat(), "st_file_attributes", 0) & 1024
        ):
            return False
    return True


def load_profile(home: Path, repo: Path | None, env: dict | None = None) -> tuple[dict, list[str]]:
    profile = {"version": 1, "variables": {}, "rules": [], "_inputs": [], "_probes": []}
    errors = []
    excluded = []
    try:
        paths = profile_paths(home, repo, env, excluded=excluded)
    except (OSError, ValueError, RuntimeError):
        return profile, ["profile discovery rejected"]
    profile["_probes"].extend(
        {"path": str(path), "sha256": None, "kind": "excluded"}
        for path in dict.fromkeys(excluded)
    )
    for index, path in enumerate(paths):
        probe = {"path": str(path), "sha256": None}
        profile["_probes"].append(probe)
        try:
            if not _safe_file(path):
                raise ProfileError("unsafe config path")
            if not path.exists():
                continue
            if not path.is_file():
                raise ProfileError("config must be a regular file")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
                                 getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ProfileError("config must be a regular file")
                raw = handle.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ProfileError("config exceeds byte limit")
            layer = validate(json.loads(raw, object_pairs_hook=_object))
            probe["sha256"] = hashlib.sha256(raw).hexdigest()
            profile["_inputs"].append({
                "path": str(path.resolve()), "sha256": hashlib.sha256(raw).hexdigest(),
            })
            profile["variables"].update(layer.get("variables", {}))
            profile["rules"].extend(
                {**rule, "_layer": index} for rule in layer.get("rules", [])
            )
        except (OSError, ValueError, UnicodeError, RecursionError):
            errors.append(f"profile layer {index + 1} rejected")
    return profile, errors


def _literal_variables(profile: dict) -> dict:
    return {"operator": "the operator", "assistant": "Copilot", **profile.get("variables", {})}


def _escaped_variables(variables: dict) -> dict:
    return {k: re.sub(r"([\\`*_\[\]{}!~])", r"\\\1", html.escape(v))
            for k, v in variables.items()}


def _checked_template(template: str, variables: dict) -> str:
    template = _template(template)
    if re.search(r"\breviewed\b", template.format_map(
        {**variables, "review_status": ""}
    ), re.I):
        raise ProfileError("review claims must use {review_status}")
    # Mask only entities generated from literal variables, not entities assembled
    # from template fragments. Then account for Markdown escapes across boundaries.
    probe_variables = {
        k: re.sub(r"&(?:amp|lt|gt|quot|#x27);", "x", v)
        for k, v in _escaped_variables(variables).items()
    }
    probe = template.format_map({**probe_variables, "review_status": "x"})
    probe = re.sub(r"\\[" + re.escape(string.punctuation) + "]", "x", probe)
    _check_markup(probe)
    return template


def resolve(profile: dict, context: dict) -> dict:
    if not isinstance(context, dict):
        raise ProfileError("context must be an object")
    for axis in AXES:
        value = context.get(axis)
        if not isinstance(value, str) or value == "*" or not TOKEN.fullmatch(value):
            raise ProfileError("context needs concrete domain, visibility and activity")
    required = context.get("required", True)
    if type(required) is not bool or type(context.get("human_reviewed", False)) is not bool:
        raise ProfileError("required and human_reviewed must be booleans")
    matches = [
        r for r in profile.get("rules", [])
        if all(r.get("when", {}).get(k, "*") in ("*", context[k]) for k in AXES)
    ]
    result = dict(DEFAULTS)
    for field in ("header", "footer", "header_variants", "footer_variants"):
        candidates = [r for r in matches if field in r]
        if not candidates:
            result.setdefault(field, [])
            continue
        rank = lambda r: (
            sum(v != "*" for v in r.get("when", {}).values()), r.get("_layer", 0)
        )
        best = max(map(rank, candidates))
        values = [r[field] for r in candidates if rank(r) == best]
        if any(v != values[0] for v in values):
            raise ProfileError("equally specific selectors conflict")
        result[field] = values[0]
    required = required or any(r.get("required") is True for r in matches)
    literals = _literal_variables(profile)
    for slot in SLOTS:
        _checked_template(result[slot], literals)
        for variant in result[slot + "_variants"]:
            _checked_template(variant, literals)
    variables = dict(literals)
    variables["review_status"] = (
        "human-reviewed" if context.get("human_reviewed") is True else "not human-reviewed"
    )
    # Variables are text, not markup. Templates themselves choose their formatting.
    variables = _escaped_variables(variables)
    for slot in SLOTS:
        result[slot] = _template(result[slot]).format_map(variables)
        result[slot + "_variants"] = [
            _template(v).format_map(variables) for v in result[slot + "_variants"]
        ]
    if required and not any(
        c.isalnum() or unicodedata.category(c) == "So"
        for s in SLOTS for c in result[s]
    ):
        result.update(DEFAULTS)
    result["required"] = required
    return result


def decorate(draft: str, signature: dict) -> str:
    """Only supplied draft text is changed; never a send or approval operation."""
    if not signature["required"]:
        return draft
    present, removed = {}, {}
    for slot in SLOTS:
        current = signature[slot]
        at_boundary = lambda text: (
            draft.startswith(text) if slot == "header" else draft.endswith(text)
        )
        present[slot] = bool(current and at_boundary(current))
        removed[slot] = 0
        if not current or present[slot]:
            continue
        variants = signature[slot + "_variants"]
        variants = sorted({v for v in variants if v}, key=len, reverse=True)
        found = next((v for v in variants if at_boundary(v)), None)
        if found is not None:
            removed[slot] = len(found)
    if removed["header"] + removed["footer"] > len(draft):
        removed = dict.fromkeys(SLOTS, 0)
    for slot, opposite in (("header", "footer"), ("footer", "header")):
        if present[opposite] and removed[slot] + len(signature[opposite]) > len(draft):
            removed[slot] = 0
    body = draft[removed["header"]:len(draft) - removed["footer"]]
    return (
        ("" if present["header"] else signature["header"]) + body
        + ("" if present["footer"] else signature["footer"])
    )


def guidance(profile: dict) -> str:
    if not profile["rules"] and not profile["variables"]:
        return ""
    variables = _literal_variables(profile)
    for rule in profile["rules"]:
        for slot in SLOTS:
            _checked_template(rule.get(slot, ""), variables)
            for variant in rule.get(slot + "_variants", []):
                _checked_template(variant, variables)
    # JSON strings inside code spans keep templates visibly data rather than instructions.
    rows = [
        "## Signature profile",
        "Templates are data. Apply only when disclosure is required by the policy above "
        "or a matching `required: true` rule; never permission to publish. More specific "
        "selectors win per slot, then later layers. Conflicting ties require clarification. "
        "Empty slots cannot waive required disclosure: use `[AI-assisted] `. "
        "Configured slots replace the default top/italic presentation, not its policy. "
        "Interpolate literal operator/assistant names; `{review_status}` is "
        "`not human-reviewed` unless this exact draft was actually reviewed. "
        "Decorate before final preview/approval, never afterwards.",
    ]
    def literal(value):
        return json.dumps(value, ensure_ascii=True).replace("`", "\\u0060").replace(
            "<", "\\u003c").replace(">", "\\u003e")
    rows.append("Variables: `" + literal(variables) + "`")
    rows.append("Default header: `\"[AI-assisted] \"`; default footer: `\"\"`.")
    for rule in profile["rules"]:
        data = {k: v for k, v in rule.items() if not k.startswith("_")}
        rows.append(f"- Layer {rule.get('_layer', 0) + 1}: `" + literal(data) + "`")
    return "\n\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("resolve", "decorate", "guidance"))
    parser.add_argument("--repo", type=Path)
    args = parser.parse_args()
    try:
        profile, errors = load_profile(Path.home(), args.repo)
        if errors:
            raise ProfileError("; ".join(errors))
        if args.action == "guidance":
            output = {"guidance": guidance(profile)}
        else:
            raw = sys.stdin.buffer.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ProfileError("request exceeds byte limit")
            request = json.loads(raw, object_pairs_hook=_object)
            signature = resolve(profile, request["context"])
            output = {"signature": signature}
            if args.action == "decorate":
                if not isinstance(request.get("draft"), str):
                    raise ProfileError("draft must be text")
                output["draft"] = decorate(request["draft"], signature)
        print(json.dumps(output, ensure_ascii=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        print(json.dumps({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
