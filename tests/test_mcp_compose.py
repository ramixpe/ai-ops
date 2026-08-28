from __future__ import annotations

from pathlib import Path

import yaml


def _compose() -> dict:
    root = Path(__file__).resolve().parent.parent
    return yaml.safe_load((root / "docker-compose.mcp-http.yml").read_text())


def test_compose_http_service_is_persistent_authenticated_and_restartable():
    compose = _compose()
    mcp = compose["services"]["mcp"]

    assert mcp["restart"] == "unless-stopped"
    assert mcp["environment"]["NETTOOLS_MCP_TRANSPORT"] == "streamable-http"
    assert mcp["environment"]["NETBOX_URL"] == "http://netbox:8080"
    assert mcp["environment"]["NEO4J_URI"] == "bolt://neo4j:7687"
    assert {"path": ".env", "required": False} in mcp["env_file"]
    assert {"path": ".env.mcp-http", "required": False} in mcp["env_file"]
    assert "NETTOOLS_MCP_HTTP_BEARER_TOKEN" not in mcp["environment"]
    assert mcp["ports"] == [
        "${NETTOOLS_MCP_HTTP_BIND_ADDRESS:-0.0.0.0}:${NETTOOLS_MCP_HTTP_BIND_PORT:-8000}:8000"
    ]
    assert "mcp-state:/var/lib/nettools" in mcp["volumes"]
    assert mcp["networks"] == [
        "router-management",
        "platform-services",
        "legacy-platform-services",
    ]
    assert compose["networks"]["router-management"]["name"] == "sota_mgmt"
    assert compose["networks"]["platform-services"]["name"] == "sota-lab-platform_labnet"
    assert compose["networks"]["legacy-platform-services"]["name"] == "sota-lab-platform_default"

    recovery = compose["services"]["event-recovery"]
    assert {"path": ".env", "required": False} in recovery["env_file"]
    assert {"path": ".env.mcp-http", "required": False} in recovery["env_file"]


def test_compose_initializer_requires_and_copies_verified_host_keys():
    initializer = _compose()["services"]["mcp-state-init"]
    command = initializer["command"][0]

    assert initializer["user"] == "0:0"
    assert "test -s /host-config/known_hosts" in command
    assert "/host-config/known_hosts /var/lib/nettools/known_hosts" in command
    assert "/bootstrap/tickets" in command
    assert "chown -R 10001:10001 /var/lib/nettools/tickets" in command