"""Tool registration and behaviour on a custom udata instance (mocked HTTP)."""

import re

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.types import TextContent
from niquests_mock import MockRouter

from tools import register_tools

_SATELLITE_VARS = ("TABULAR_API_URL", "METRICS_API_URL", "CRAWLER_API_URL")
_RID = "11111111-1111-1111-1111-111111111111"
_DID = "22222222-2222-2222-2222-222222222222"


def _make_mcp() -> FastMCP:
    app = FastMCP()
    register_tools(app)
    return app


@pytest.fixture
def custom_instance(monkeypatch):
    for var in (*_SATELLITE_VARS, "UDATA_SITE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("UDATA_API_URL", "https://dados.gov.pt/api/")
    monkeypatch.setenv("UDATA_PORTAL_NAME", "dados.gov.pt")


async def _tools_by_name(app: FastMCP) -> dict:
    return {tool.name: tool for tool in await app.list_tools()}


async def test_default_registers_all_tools(monkeypatch):
    monkeypatch.delenv("UDATA_API_URL", raising=False)
    tools = await _tools_by_name(_make_mcp())
    assert {"query_resource_data", "get_metrics", "get_resource_info"} <= set(tools)
    assert len(tools) == 10


async def test_custom_instance_skips_satellite_tools(custom_instance):
    tools = await _tools_by_name(_make_mcp())
    assert "query_resource_data" not in tools
    assert "get_metrics" not in tools
    assert "get_resource_info" in tools
    assert len(tools) == 8


@pytest.mark.parametrize(
    "var_name,tool_name",
    [("TABULAR_API_URL", "query_resource_data"), ("METRICS_API_URL", "get_metrics")],
)
async def test_custom_instance_registers_configured_satellites(
    custom_instance, monkeypatch, var_name, tool_name
):
    monkeypatch.setenv(var_name, "https://satellite.example.org/api/")
    tools = await _tools_by_name(_make_mcp())
    assert tool_name in tools


async def test_custom_instance_descriptions_name_portal(custom_instance):
    tools = await _tools_by_name(_make_mcp())
    for tool in tools.values():
        assert "data.gouv.fr" not in (tool.description or "")
        assert "{portal}" not in (tool.description or "")
    assert "on dados.gov.pt" in tools["search_datasets"].description
    assert "on dados.gov.pt" in tools["search_organizations"].description
    assert "dados.gov.pt catalog" in tools["search_dataservices"].description


async def test_default_descriptions_name_data_gouv(monkeypatch):
    monkeypatch.delenv("UDATA_API_URL", raising=False)
    tools = await _tools_by_name(_make_mcp())
    assert (
        tools["search_datasets"]
        .description.strip()
        .startswith("Search for datasets on data.gouv.fr by keywords.")
    )


async def test_get_resource_info_skips_unavailable_satellites(
    custom_instance, niquests_mock: MockRouter
):
    """Only the core API is called; no Tabular or Crawler request is made."""
    niquests_mock.get(
        re.compile(rf"https://dados\.gov\.pt/api/2/datasets/resources/{_RID}/")
    ).respond(
        json={
            "resource": {"id": _RID, "title": "contratos.csv", "format": "csv"},
            "dataset_id": _DID,
        }
    )
    niquests_mock.get(
        re.compile(rf"https://dados\.gov\.pt/api/1/datasets/{_DID}/")
    ).respond(json={"id": _DID, "title": "Contratos públicos"})

    content, _ = await _make_mcp().call_tool("get_resource_info", {"resource_id": _RID})
    assert isinstance(content[0], TextContent)  # type: ignore
    text = content[0].text  # type: ignore

    assert "Resource Information: contratos.csv" in text
    assert "Dataset: Contratos públicos" in text
    assert "Tabular API availability" not in text
    assert "Tabular API: not available on dados.gov.pt" in text
    assert len(niquests_mock.calls) == 2
