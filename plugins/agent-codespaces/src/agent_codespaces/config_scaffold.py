"""Render the supplementary ``config.yaml`` scaffold written by ``config init``."""

from __future__ import annotations

from typing import Any


def render_codespaces_yaml(defaults: dict[str, Any] | None) -> str:
    """Render a ``.copilot-extensions/agent-codespaces/config.yaml``.

    The file is **supplementary-only**: it carries just the CodeSpace-specific
    bits convention can't derive. A repo that matches convention (machine
    defaults, ``/workspaces/<basename>`` checkout, git-credential relay for
    github.com + ADO) needs no file at all -- so the template leads with that and
    keeps every block commented unless a discovered CodeSpace supplies a value.
    """
    header = (
        "# .copilot-extensions/agent-codespaces/config.yaml -- SUPPLEMENTARY CodeSpace config.\n"
        "#\n"
        "# Most repos need NO file here. agent-codespaces derives by convention:\n"
        "#   * machine_type=largePremiumLinux, location=EastUs\n"
        "#   * checkout at /workspaces/<repo-basename>\n"
        "#   * git-credential relay serving github.com AND Azure DevOps (via GCM)\n"
        "# Add a block below ONLY when your repo deviates (e.g. a split\n"
        "# CodeSpaces-vs-product repo, a pinned devcontainer, an ADO host, or a\n"
        "# provision hook). All org/account/URL values live HERE, in your repo --\n"
        "# never in the copilot-extensions plugin.\n"
    )

    if defaults:
        repo = defaults.get("repository")
        machine = defaults["machine_type"]
        ws = defaults.get("workspace_folder")
        derived = (
            f"# Derived from your CodeSpace '{defaults['source_name']}'.\n"
        )
        # Only emit a repos: block when the discovered machine differs from the
        # convention default, or a non-convention workspace folder was found.
        basename = repo.split("/")[-1] if repo else ""
        conv_ws = f"/workspaces/{basename.removesuffix('-codespaces')}" if basename else ""
        lines: list[str] = []
        if machine and machine != "largePremiumLinux":
            lines.append(f"    machine_type: {machine}")
        if ws and ws != conv_ws:
            lines.append(f"    workspace_folder: {ws}")
        elif basename.endswith("-codespaces"):
            # A split repo: record the product mapping so agents land right.
            product = basename.removesuffix("-codespaces")
            lines.append(f"    workspace_repo: {product}   # -> /workspaces/{product}")
        if lines and repo:
            repo_block = f"\nrepos:\n  {repo}:\n" + "\n".join(lines) + "\n"
        else:
            repo_block = (
                f"\n# This repo matches convention -- no overrides needed.\n"
                f"# Add a repos: block only if that changes:\n"
                f"# repos:\n#   {repo or '<org>/<repo>'}:\n"
                f"#     workspace_repo: <product>   # only for split *-codespaces repos\n"
            )
        return header + derived + repo_block

    return header + (
        "\n# No existing CodeSpaces detected. Uncomment and adapt ONLY what you\n"
        "# need; delete the rest.\n"
        "#\n"
        "# repos:\n"
        "#   <org>/<repo>-codespaces:\n"
        "#     workspace_repo: <product>          # split repo -> /workspaces/<product>\n"
        "#     machine_type: largePremiumLinux256gb\n"
        "#     devcontainer_path: .devcontainer/devcontainer.json  # pin if repo ships >1\n"
        "#\n"
        "# credentials:\n"
        "#   ado_host: <your-org>.visualstudio.com   # only for bare ADO get-access-token\n"
    )
