# MCP server for the IOS-XR read-only network tools.
# Runs over stdio: the MCP client launches `docker run -i` and speaks
# JSON-RPC on the container's stdin/stdout.
#
# EER-013: digest-pinned, not just tag-pinned. `python:3.11-slim` is a
# floating tag -- Debian security rebuilds and Python patch releases move it
# out from under a build with no code change. The digest below is the
# multi-arch index digest for `python:3.11-slim` (resolving today to Debian
# trixie-slim, Python 3.11.16), verified 2026-08-20 three independent ways:
# `docker pull python:3.11-slim`, `docker manifest inspect`, and a direct
# registry-API call, all agreeing. Re-resolve deliberately (`docker pull
# python:3.11-slim` and copy the `Digest:` it prints) rather than editing
# this by hand -- a bump here is the same kind of reviewable decision as a
# dependency bump, not something to do silently.
FROM python:3.11-slim@sha256:9c900dea9e8fb7e16277c179b555cc72d29a352dbc33cff48ad5a0412fd5bfc7

# Keep Python output unbuffered and skip .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy metadata and source, then install runtime deps only (no dev/llm extras).
# The classic MCP surface includes graph topology, so the image installs the
# narrow `graph` extra rather than leaving a public tool without its driver.
# constraints.txt (EER-013) pins the exact versions verified to work together
# rather than whatever each package's index happens to serve on build day.
COPY pyproject.toml README.md constraints.txt ./
COPY src/ ./src/
COPY mcp_server/ ./mcp_server/
COPY scripts/campaign_phase_bridge.py ./scripts/campaign_phase_bridge.py
RUN pip install --no-cache-dir -c constraints.txt ".[graph]"

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# Credentials are provided at runtime via -e / --env-file, never baked in.
# Stdio remains the default entrypoint. Native MCP HTTP mode is opt-in through
# NETTOOLS_MCP_TRANSPORT and requires NETTOOLS_MCP_HTTP_BEARER_TOKEN.
EXPOSE 8000
ENTRYPOINT ["nettools-mcp"]
