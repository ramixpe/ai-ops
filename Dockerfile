# MCP server for the IOS-XR read-only network tools.
# Runs over stdio: the MCP client launches `docker run -i` and speaks
# JSON-RPC on the container's stdin/stdout.
FROM python:3.11-slim

# Keep Python output unbuffered and skip .pyc files.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy metadata and source, then install runtime deps only (no dev/llm extras):
# the MCP server needs netmiko + mcp + python-dotenv and nothing else.
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY mcp_server/ ./mcp_server/
RUN pip install --no-cache-dir .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# Credentials are provided at runtime via -e / --env-file, never baked in.
ENTRYPOINT ["nettools-mcp"]
