FROM mcr.microsoft.com/devcontainers/python@sha256:ca7b20585cf65295cd0cef8ae8f02a008c1c85e83909d1e2af8d19706d3f34ee

USER root
ENV PATH="/opt/trusted-harness/node/bin:/opt/trusted-harness/copilot:/opt/trusted-harness/uv:/usr/local/python/current/bin:/usr/local/py-utils/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
ENV container=docker
COPY dependencies.lock.json install.py build.py /opt/trusted-harness/recipe/
RUN python3 /opt/trusted-harness/recipe/install.py
COPY --chmod=0755 entrypoint.sh /usr/local/sbin/trusted-harness-init
COPY --chmod=0644 profile.sh /etc/profile.d/trusted-harness.sh
COPY sshd.conf /etc/ssh/sshd_config.d/10-trusted-harness.conf
WORKDIR /workspaces
STOPSIGNAL SIGRTMIN+3
ENTRYPOINT ["/usr/local/sbin/trusted-harness-init"]
CMD ["/lib/systemd/systemd"]
