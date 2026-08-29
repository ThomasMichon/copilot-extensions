# Clean-room base image for validating the copilot-extensions install flow.
#
# A deliberately minimal Linux machine: git, python, node, uv, and the GitHub
# Copilot CLI -- and NOTHING from copilot-extensions. The whole point is to
# reproduce a naive operator's "fresh machine" so the install/bootstrap flow is
# exercised from zero (no ~/.agent-*, no binstubs, no marketplace, no PATH
# tweaks beyond a stock login shell).
#
# Auth is NOT baked here. Run the one-time device-code login and `docker commit`
# to a cached ":authed" tag (see run.ps1 / README.md); this base stays
# credential-free so it is safe to rebuild and share.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# Stock prerequisites a real operator's machine would already have. Node is for
# the Copilot CLI (`@github/copilot`); uv is the runtime-install tool the
# plugins' installers use. We intentionally do NOT pre-create ~/.local/bin or
# touch PATH beyond what Ubuntu's stock ~/.profile does.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git python3 python3-venv ripgrep sudo \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && mkdir -p /opt/copilot-cleanroom/bin \
    && mv /usr/bin/rg /opt/copilot-cleanroom/bin/rg \
    && rm -rf /var/lib/apt/lists/*

# uv (Astral) -- the plugins install their venvs with `uv pip install`.
RUN curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# The GitHub Copilot CLI (agentic `copilot` command). Pinned loosely; the
# harness records the exact version it ran against.
# The distro ripgrep above stays outside the stock PATH. Tier-E launches opt
# into it with USE_BUILTIN_RIPGREP=false because Copilot's bundled ARM64 rg
# cannot run on hosts with 16 KiB pages; Tier-P scenarios remain unbiased.
#
# NPM_REGISTRY is a BUILD-TIME convenience ONLY, used to install the Copilot CLI
# prereq. On a corp-governed box the public registry.npmjs.org is blocked at the
# TLS layer, so an internal npm feed must be passed to install Copilot, e.g.:
#   docker build --build-arg NPM_REGISTRY=https://<your-internal-npm-feed>/ ...
# This is NOT part of the experiment and is NOT inherited into the operator's
# runtime environment -- it only lets `npm install -g @github/copilot` succeed
# (Copilot is a GIVEN prereq). The runner does NOT auto-forward the host's npm
# config: silently inheriting the host feed would bias the fresh-machine
# experiment, so a feed is passed only when set explicitly (see run.ps1/run.sh).
ARG NPM_REGISTRY=https://registry.npmjs.org/
RUN npm config set registry "$NPM_REGISTRY" \
    && npm install -g @github/copilot \
    && npm config delete registry

# A non-root operator, mirroring a real login (stock ~/.profile puts
# ~/.local/bin on PATH *only if it already exists at login* -- exactly the
# timing wrinkle we want to observe, so we do not pre-create it).
RUN groupadd -f operator \
    && useradd -m -s /bin/bash -g operator operator \
    && adduser operator sudo \
    && echo 'operator ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/operator
USER operator
WORKDIR /home/operator

# The harness scripts (driver + assertions) are mounted in at run time so
# iterating on them does not force an image rebuild. Nothing is baked in: the
# base box is driven by a mounted scenario (`run -Scenario …`) or entered
# interactively (`run -Mode shell`). Idle by default so the runner can start it
# detached and `docker exec` into it (matching Dockerfile.pristine).
ENTRYPOINT ["/bin/bash", "-lc"]
CMD ["sleep infinity"]
