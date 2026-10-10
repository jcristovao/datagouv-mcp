from mcp.server.fastmcp import FastMCP

from helpers import env_config
from tools import query_resource_data
from tools.get_dataservice_info import register_get_dataservice_info_tool
from tools.get_dataservice_openapi_spec import (
    register_get_dataservice_openapi_spec_tool,
)
from tools.get_dataset_info import register_get_dataset_info_tool
from tools.get_metrics import register_get_metrics_tool
from tools.get_resource_info import register_get_resource_info_tool
from tools.list_dataset_resources import register_list_dataset_resources_tool
from tools.query_resource_data import register_query_resource_data_tool
from tools.search_dataservices import register_search_dataservices_tool
from tools.search_datasets import register_search_datasets_tool
from tools.search_organizations import register_search_organizations_tool


def register_tools(mcp: FastMCP) -> None:
    """
    Register MCP tools with the provided FastMCP instance.

    Tools that depend on a satellite service (Tabular API, Metrics API) are only
    registered when that service is available for the configured udata instance.
    """
    register_search_datasets_tool(mcp)
    register_search_organizations_tool(mcp)
    register_search_dataservices_tool(mcp)
    register_get_dataservice_info_tool(mcp)
    register_get_dataservice_openapi_spec_tool(mcp)
    if query_resource_data.is_available():
        register_query_resource_data_tool(mcp)
    register_get_dataset_info_tool(mcp)
    register_list_dataset_resources_tool(mcp)
    register_get_resource_info_tool(mcp)
    if env_config.is_available("metrics_api"):
        register_get_metrics_tool(mcp)
