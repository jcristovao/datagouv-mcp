import os

from mcp.server.transport_security import TransportSecuritySettings

# Hosts accepted by default: the data.gouv.fr deployments and local development.
DEFAULT_ALLOWED_HOSTS = [
    "mcp.data.gouv.fr",
    "mcp.preprod.data.gouv.fr",
    "localhost:*",
    "127.0.0.1:*",
]
DEFAULT_ALLOWED_ORIGINS = [
    "https://mcp.data.gouv.fr",
    "https://mcp.preprod.data.gouv.fr",
    "http://localhost:*",
    "http://127.0.0.1:*",
]


def extra_allowed_hosts() -> list[str]:
    """
    Host names from MCP_ALLOWED_HOSTS (comma-separated), e.g. "mcp.example.pt".

    Needed when the server is hosted publicly under a name other than the
    data.gouv.fr ones. Entries may carry a port ("host:8080") or a port
    wildcard ("host:*"). A scheme or path, if pasted by mistake, is dropped.
    """
    hosts = []
    for raw in os.getenv("MCP_ALLOWED_HOSTS", "").split(","):
        host = raw.strip().lower()
        if "://" in host:
            host = host.split("://", 1)[1]
        host = host.split("/", 1)[0]
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def build_transport_security() -> TransportSecuritySettings:
    """
    DNS-rebinding protection settings (MCP spec: validate Host and Origin).

    Each host in MCP_ALLOWED_HOSTS is accepted as a Host header, and
    https://<host> as an Origin header.
    """
    extra = extra_allowed_hosts()
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=DEFAULT_ALLOWED_HOSTS + extra,
        allowed_origins=DEFAULT_ALLOWED_ORIGINS + [f"https://{host}" for host in extra],
    )
