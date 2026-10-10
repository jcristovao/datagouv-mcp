"""Tests for MCP_ALLOWED_HOSTS (DNS-rebinding protection settings)."""

from mcp.server.transport_security import TransportSecurityMiddleware

from helpers import transport_security


def _middleware() -> TransportSecurityMiddleware:
    return TransportSecurityMiddleware(transport_security.build_transport_security())


def test_defaults_unchanged_without_env(monkeypatch):
    monkeypatch.delenv("MCP_ALLOWED_HOSTS", raising=False)
    settings = transport_security.build_transport_security()
    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == transport_security.DEFAULT_ALLOWED_HOSTS
    assert settings.allowed_origins == transport_security.DEFAULT_ALLOWED_ORIGINS


def test_blank_env_counts_as_unset(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", " , ")
    assert transport_security.extra_allowed_hosts() == []


def test_parses_list_and_normalises(monkeypatch):
    monkeypatch.setenv(
        "MCP_ALLOWED_HOSTS",
        " MCP.Example.pt , https://other.example.pt/mcp, mcp.example.pt, dev.example.pt:8080 ",
    )
    assert transport_security.extra_allowed_hosts() == [
        "mcp.example.pt",
        "other.example.pt",
        "dev.example.pt:8080",
    ]


def test_extra_hosts_and_origins_are_accepted(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.example.pt")
    middleware = _middleware()
    assert middleware._validate_host("mcp.example.pt")
    assert middleware._validate_origin("https://mcp.example.pt")
    # Defaults still work.
    assert middleware._validate_host("mcp.data.gouv.fr")
    assert middleware._validate_host("127.0.0.1:8917")


def test_unknown_host_is_rejected(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.example.pt")
    middleware = _middleware()
    assert not middleware._validate_host("evil.example.com")
    assert not middleware._validate_origin("https://evil.example.com")
    # Plain http is not accepted as an origin for a public host.
    assert not middleware._validate_origin("http://mcp.example.pt")


def test_port_wildcard(monkeypatch):
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "mcp.example.pt:*")
    middleware = _middleware()
    assert middleware._validate_host("mcp.example.pt:8443")
