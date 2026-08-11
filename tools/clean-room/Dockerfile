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
        ca-certificates curl git python3 python3-venv sudo \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# uv (Astral) -- the plugins install their venvs with `uv pip install`.
RUN curl -fsSL https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# The GitHub Copilot CLI (agentic `copilot` command). Pinned loosely; the
# harness records the exact version it ran against.
#
# NPM_REGISTRY lets a GOVERNED machine point npm at its internal feed. On a
# corp-governed box the public registry.npmjs.org is blocked at the TLS layer
# (handshake failure), so pass the same governed feed the host uses, e.g.:
#   docker build --build-arg NPM_REGISTRY=https://packagefeedproxy.microsoft.io/npm/ ...
# run.ps1/run.sh forward it automatically (auto-detected from host `npm config`).
ARG NPM_REGISTRY=https://registry.npmjs.org/
RUN npm config set registry "$NPM_REGISTRY" \
    && npm install -g @github/copilot

# A non-root operator, mirroring a real login (stock ~/.profile puts
# ~/.local/bin on PATH *only if it already exists at login* -- exactly the
# timing wrinkle we want to observe, so we do not pre-create it).
RUN groupadd -f operator \
    && useradd -m -s /bin/bash -g operator operator \
    && adduser operator sudo \
    && echo 'operator ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/operator
USER operator
WORKDIR /home/operator

# The harness scripts (driver + assertions) are mounted or copied in at run
# time so iterating on them does not force an image rebuild.
COPY --chown=operator:operator validate.sh /home/operator/validate.sh
RUN chmod +x /home/operator/validate.sh

ENTRYPOINT ["/bin/bash", "-lc"]
CMD ["/home/operator/validate.sh"]
