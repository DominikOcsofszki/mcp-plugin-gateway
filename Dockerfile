# Container image for mcp-plugin-gateway.
#
# One image serves one plugin per container. The build runs the settings doctor
# so a missing secret fails `docker build` instead of the first tool call. The
# secret is mounted as a BuildKit secret, so it never lands in an image layer.
#
#   docker build --secret id=SECURE_PLUGIN_TOKEN,src=./secrets/SECURE_PLUGIN_TOKEN .
#
# A build without that secret fails on purpose:
#
#   docker build .   # exits non-zero, naming SECURE_PLUGIN_TOKEN

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_PLUGIN_GATEWAY_MODE=container

WORKDIR /app

# Copy only what the build needs, so source edits do not invalidate the layer
# that installs dependencies.
COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples

# Install the core package and both example plugins. Each install builds its own
# wheel, so the build needs no network access beyond the dependency resolution
# pip performs for the first install.
RUN pip install . \
    && pip install ./examples/example_plugin \
    && pip install ./examples/secure_plugin

# Build-time configuration check. ``--mount=type=secret`` exposes the secret only
# for this RUN step; the doctor reads it from /run/secrets and exits non-zero when
# a plugin's settings cannot be instantiated. No tool is executed.
#
# ``required=false`` is deliberate: with ``required=true`` BuildKit aborts before
# the check runs. Leaving it optional lets the doctor execute and fail with the
# plugin's own validation error, which names the missing variable.
RUN --mount=type=secret,id=SECURE_PLUGIN_TOKEN,target=/run/secrets/SECURE_PLUGIN_TOKEN \
    python -m mcp_plugin_gateway.settings

# Serve one plugin per container. Override ``--plugin`` to pick another.
ENTRYPOINT ["mcp-plugin-gateway", "mcp"]
CMD ["--plugin", "secure-plugin"]
