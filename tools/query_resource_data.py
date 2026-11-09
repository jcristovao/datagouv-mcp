import logging

import niquests
from mcp.server.fastmcp import FastMCP

from helpers import (
    datagouv_api_client,
    env_config,
    local_tabular_client,
    portal_text,
    tabular_api_client,
)
from helpers.logging import MAIN_LOGGER_NAME, log_tool
from helpers.mcp_tool_defaults import READ_ONLY_EXTERNAL_API_TOOL
from helpers.ssrf import BlockedAddressError

logger = logging.getLogger(MAIN_LOGGER_NAME)


def backend() -> str | None:
    """
    How query_resource_data gets its rows: "tabular" (Tabular API), "local"
    (download the file and query it in-process) or None (tool unavailable).
    """
    if env_config.is_available("tabular_api"):
        return "tabular"
    if local_tabular_client.is_enabled():
        return "local"
    return None


def is_available() -> bool:
    """Whether query_resource_data can run on the configured udata instance."""
    return backend() is not None


def _summary() -> str:
    if backend() == "local":
        limit = local_tabular_client.format_size(local_tabular_client.max_bytes())
        return (
            f"Query tabular data from a resource (files up to {limit}; the server\n"
            "downloads the file and queries it, no download needed on your side)."
        )
    return (
        "Query tabular data from a resource via the Tabular API (no download needed)."
    )


def register_query_resource_data_tool(mcp: FastMCP) -> None:
    @mcp.tool(
        title="Query resource data",
        annotations=READ_ONLY_EXTERNAL_API_TOOL,
    )
    @log_tool
    @portal_text.describe(summary=_summary())
    async def query_resource_data(
        resource_id: str,
        page: int = 1,
        page_size: int = 20,
        filter_column: str | None = None,
        filter_value: str | None = None,
        filter_operator: str = "exact",
        sort_column: str | None = None,
        sort_direction: str = "asc",
    ) -> str:
        """
        {summary}

        Works for CSV/XLSX files. Start with small page_size (20) to preview structure.
        Use filter_column/filter_value/filter_operator to filter, sort_column/sort_direction to sort.
        Filter operators: exact, contains, less, greater, strictly_less, strictly_greater.
        For large datasets requiring full analysis, paginate through pages or use
        get_resource_info to retrieve the raw file URL and fetch it directly.
        """
        try:
            filter_operator = filter_operator.lower()
            sort_direction = sort_direction.lower()

            operator_map = {
                "exact": "exact",
                "contains": "contains",
                "less": "less",
                "greater": "greater",
                "strictly_less": "strictly_less",
                "strictly_greater": "strictly_greater",
            }
            if (
                filter_column
                and filter_value is not None
                and filter_operator not in operator_map
            ):
                supported = ", ".join(sorted(operator_map.keys()))
                return f"Error: invalid filter_operator. Supported values: {supported}."

            if sort_column and sort_direction not in {"asc", "desc"}:
                return "Error: invalid sort_direction. Supported values: asc, desc."

            use_tabular_api = backend() == "tabular"
            source = "Tabular API" if use_tabular_api else "local copy"
            resource: dict = {}

            # Get resource metadata to display context
            if use_tabular_api:
                try:
                    resource_metadata = await datagouv_api_client.get_resource_metadata(
                        resource_id
                    )
                    resource_title = resource_metadata.get("title", "Unknown")
                    dataset_id = resource_metadata.get("dataset_id")
                except Exception:  # noqa: BLE001
                    resource_title = "Unknown"
                    dataset_id = None
            else:
                # The local fallback needs the full payload (file URL, format, size)
                details = await datagouv_api_client.get_resource_details(resource_id)
                resource = details.get("resource", {})
                if not resource.get("id"):
                    return f"Error: Resource with ID '{resource_id}' not found."
                resource_title = resource.get("title") or "Unknown"
                dataset_id = details.get("dataset_id")

            # Get dataset title if available
            dataset_title = "Unknown"
            if dataset_id:
                try:
                    dataset_metadata = await datagouv_api_client.get_dataset_metadata(
                        str(dataset_id)
                    )
                    dataset_title = dataset_metadata.get("title", "Unknown")
                except Exception:  # noqa: BLE001, S110
                    pass

            content_parts = [
                f"Querying resource: {resource_title}",
                f"Resource ID: {resource_id}",
            ]
            if dataset_id:
                content_parts.append(f"Dataset: {dataset_title} (ID: {dataset_id})")
            if not use_tabular_api:
                content_parts.append(
                    "Source: file downloaded and queried by the server "
                    f"(no Tabular API on {env_config.get_portal_name()})"
                )
            content_parts.append("")

            # Show applied filters if any
            if filter_column and filter_value is not None:
                content_parts.append(
                    f"Filter: {filter_column} {filter_operator} {filter_value}"
                )
            if sort_column:
                content_parts.append(f"Sort: {sort_column} ({sort_direction})")
            if filter_column or sort_column:
                content_parts.append("")

            # Fetch data via the Tabular API (clamp page_size to valid range)
            page_size = max(1, min(page_size, 200))

            # Build filter and sort parameters for Tabular API
            api_params = {}

            # Add filter if provided
            if filter_column and filter_value is not None:
                operator = operator_map[filter_operator]
                param_key = f"{filter_column}__{operator}"
                api_params[param_key] = filter_value

            # Add sort if provided
            if sort_column:
                api_params[f"{sort_column}__sort"] = sort_direction

            logger.info(
                f"Querying {source} for resource: {resource_title} "
                f"(ID: {resource_id}), page: {page}, page_size: {page_size}, "
                f"filters: {api_params}"
            )

            try:
                if use_tabular_api:
                    tabular_data = await tabular_api_client.fetch_resource_data(
                        resource_id,
                        page=page,
                        page_size=page_size,
                        params=api_params if api_params else None,
                    )
                else:
                    tabular_data = await local_tabular_client.fetch_resource_data(
                        resource,
                        page=page,
                        page_size=page_size,
                        filter_column=filter_column,
                        filter_value=filter_value,
                        filter_operator=filter_operator,
                        sort_column=sort_column,
                        sort_direction=sort_direction,
                    )
                rows = tabular_data.get("data", [])
                meta = tabular_data.get("meta", {})
                total_count = meta.get("total")
                page_info = meta.get("page")
                page_size_meta = meta.get("page_size")

                if not rows:
                    content_parts.append(
                        "⚠️  No rows available (resource may be empty or filtered)."
                    )
                    return "\n".join(content_parts)

                if total_count is not None:
                    content_parts.append(f"Total rows ({source}): {total_count}")
                    # Calculate total pages
                    if page_size_meta and page_size_meta > 0:
                        total_pages = (
                            total_count + page_size_meta - 1
                        ) // page_size_meta
                        content_parts.append(
                            f"Total pages: {total_pages} (page size: {page_size_meta})"
                        )
                content_parts.append(
                    f"Retrieved: {len(rows)} row(s) from page {page_info or page}"
                )

                # Show column names
                if rows:
                    columns = [str(k) if k is not None else "" for k in rows[0]]
                    content_parts.append(f"Columns: {', '.join(columns)}")

                # Show all retrieved data
                content_parts.append("")
                if len(rows) == 1:
                    content_parts.append("Data (1 row):")
                else:
                    content_parts.append(f"Data ({len(rows)} rows):")
                for i, row in enumerate(rows, 1):
                    content_parts.append(f"  Row {i}:")
                    for key, value in row.items():
                        val_str = str(value) if value is not None else ""
                        if len(val_str) > 100:
                            val_str = val_str[:100] + "..."
                        content_parts.append(f"    {key}: {val_str}")

                links = tabular_data.get("links", {})
                if links.get("next"):
                    next_page = page + 1
                    content_parts.append("")

                    # Adapt message based on dataset size
                    if total_count and total_count > 1000:
                        content_parts.append(
                            f"⚠️ Large dataset ({total_count} rows). "
                            f"To get all data, paginate using page={next_page} or use "
                            f"get_resource_info to retrieve the raw file URL and fetch it directly."
                        )
                    else:
                        content_parts.append(
                            f"📄 More data available. Use page={next_page} to see the next page."
                        )

            except tabular_api_client.ResourceNotAvailableError as e:
                logger.warning(f"Resource not available: {resource_id} - {e!s}")
                content_parts.append(f"⚠️  {e!s}")
            except tabular_api_client.TabularApiRequestError as e:
                logger.warning(f"Tabular API request failed: {resource_id} - {e!s}")
                content_parts.append(f"⚠️  {e!s}")
            except local_tabular_client.LocalQueryError as e:
                logger.warning(f"Local query failed: {resource_id} - {e!s}")
                content_parts.append(f"⚠️  {e!s}")
            except BlockedAddressError as e:
                logger.warning(f"Blocked download for resource {resource_id}: {e}")
                content_parts.append(
                    f"❌ The file URL is not a public http(s) address ({e})"
                )
            except niquests.HTTPError as e:
                status = e.response.status_code if e.response is not None else None
                error_details = (
                    f"HTTP {status}: {e!s}" if status is not None else str(e)
                )
                if e.request:
                    error_details += f" - URL: {e.request.url}"
                error_label = (
                    "Tabular API error" if use_tabular_api else "Download error"
                )
                logger.warning(
                    f"{error_label} for resource {resource_id}: {error_details}"
                )
                content_parts.append(f"❌ {error_label} ({error_details})")
            except Exception as e:
                logger.exception(f"Unexpected error querying resource {resource_id}")
                content_parts.append(f"❌ Error querying resource: {e!s}")

            return "\n".join(content_parts)

        except niquests.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            if status is not None:
                return f"Error: HTTP {status} - {e!s}"
            return f"Error: {e!s}"
        except Exception as e:  # noqa: BLE001
            return f"Error: {e!s}"
