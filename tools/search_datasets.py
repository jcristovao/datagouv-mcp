import logging

from mcp.server.fastmcp import FastMCP

from helpers import catalog_search, datagouv_api_client, portal_text
from helpers.logging import MAIN_LOGGER_NAME, log_tool
from helpers.mcp_tool_defaults import READ_ONLY_EXTERNAL_API_TOOL
from tools import query_resource_data

logger = logging.getLogger(MAIN_LOGGER_NAME)


def _workflow() -> str:
    return (
        "search_datasets → list_dataset_resources → query_resource_data"
        if query_resource_data.is_available()
        else "search_datasets → list_dataset_resources → fetch the resource URL"
    )


def _query_hint() -> str:
    if catalog_search.is_available():
        return (
            "Use short, specific queries. Results are ranked by relevance, so a\n"
            "dataset does not need to contain every word of the query."
        )
    return (
        "Use short, specific queries (the API uses AND logic, so generic words\n"
        f"like {portal_text.generic_words()} may return zero results)."
    )


async def _search_local_catalog(
    query: str,
    page: int,
    page_size: int,
    sort: str | None,
    last_update_range: str | None,
) -> dict | None:
    """
    Search the local copy of the catalog, when there is one.

    Returns None when the portal's API should answer instead: no usable local
    copy, a failure reading it, or nothing found in it (the copy is refreshed
    weekly by the portal, so the newest datasets are only in the API).
    """
    if not catalog_search.is_available():
        return None
    try:
        result = await catalog_search.search_datasets(
            query=query,
            page=page,
            page_size=page_size,
            sort=sort,
            last_update_range=last_update_range,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Local catalog search failed, using the API instead: {e}")
        return None
    return result if result.get("total") else None


def clean_search_query(query: str) -> str:
    """
    Clean search query by removing generic stop words that are not typically
    present in dataset metadata but are often added by users.

    The API uses strict AND logic, so adding generic words like "données"
    (or "dados" on a Portuguese portal) that don't appear in metadata causes searches to return zero results.

    Args:
        query: Original search query

    Returns:
        Cleaned query with stop words removed
    """
    # Stop words that are generic and often not in dataset metadata
    # These are words users commonly add but that break AND-based searches
    stop_words = portal_text.stop_words()

    # Split query into words, preserving spacing
    words = query.split()
    # Filter out stop words (case-insensitive)
    cleaned_words = [word for word in words if word.lower().strip() not in stop_words]

    # Rejoin words, preserving original spacing pattern
    cleaned_query = " ".join(cleaned_words)
    # Clean up multiple spaces
    cleaned_query = " ".join(cleaned_query.split())

    if cleaned_query != query:
        logger.debug("Cleaned search query: '%s' -> '%s'", query, cleaned_query)

    return cleaned_query


def register_search_datasets_tool(mcp: FastMCP) -> None:
    @mcp.tool(
        title="Search datasets",
        annotations=READ_ONLY_EXTERNAL_API_TOOL,
    )
    @log_tool
    @portal_text.describe(workflow=_workflow(), query_hint=_query_hint())
    async def search_datasets(
        query: str,
        page: int = 1,
        page_size: int = 20,
        sort: str | None = None,
        last_update_range: str | None = None,
    ) -> str:
        """
        Search for datasets on {portal} by keywords.

        This is typically the first step in exploring {portal}.
        {query_hint}

        Use `sort` to order results. Accepted values: created, last_update,
        reuses, followers, views. Optionally prefixed with '-' for descending
        (e.g. -last_update). Use `last_update_range` to restrict
        results to recently updated datasets: last_30_days, last_12_months,
        last_3_years.

        Typical workflow: {workflow}.
        """
        # Clean the query to remove generic stop words that break AND-based searches
        cleaned_query = clean_search_query(query)

        local_result = await _search_local_catalog(
            query, page, page_size, sort, last_update_range
        )

        # Try with cleaned query first
        result = local_result or await datagouv_api_client.search_datasets(
            query=cleaned_query,
            page=page,
            page_size=page_size,
            sort=sort,
            last_update_range=last_update_range,
        )

        # Format the result as text content
        datasets = result.get("data", [])

        # Fallback: if cleaned query returns no results and it differs from original,
        # try with the original query
        if not datasets and cleaned_query != query:
            logger.debug(
                "No results with cleaned query '%s', trying original query '%s'",
                cleaned_query,
                query,
            )
            result = await datagouv_api_client.search_datasets(
                query=query,
                page=page,
                page_size=page_size,
                sort=sort,
                last_update_range=last_update_range,
            )
            datasets = result.get("data", [])

        if not datasets:
            return f"No datasets found for query: '{query}'"

        content_parts = [
            f"Found {result.get('total', len(datasets))} dataset(s) for query: '{query}'",
            f"Page {result.get('page', 1)} of results:\n",
        ]
        if local_result:
            exported = str(local_result.get("exported_at") or "")[:10]
            content_parts.insert(
                1,
                "Source: local copy of the catalog"
                + (f" (exported {exported})" if exported else "")
                + ", ranked by relevance",
            )
        for i, ds in enumerate(datasets, 1):
            content_parts.append(f"{i}. {ds.get('title', 'Untitled')}")
            content_parts.append(f"   ID: {ds.get('id')}")
            if ds.get("description_short"):
                desc = ds.get("description_short", "")[:200]
                content_parts.append(f"   Description: {desc}...")
            if ds.get("organization"):
                content_parts.append(f"   Organization: {ds.get('organization')}")
            if ds.get("tags"):
                tags = ", ".join(ds.get("tags", [])[:5])
                content_parts.append(f"   Tags: {tags}")
            content_parts.append(f"   Resources: {ds.get('resources_count', 0)}")
            content_parts.append(f"   URL: {ds.get('url')}")
            content_parts.append("")

        return "\n".join(content_parts)
