"""Tests for the local query fallback (no Tabular API). Mocked HTTP, no live API."""

import io
import re

import pytest
from mcp.server.fastmcp import FastMCP
from niquests_mock import MockRouter
from openpyxl import Workbook

from helpers import local_tabular_client
from helpers.local_tabular_client import LocalQueryError, query_rows
from tools import register_tools

_RID = "11111111-1111-1111-1111-111111111111"
_FILE_URL = "https://files.example.org/contratos.csv"

# Portuguese-style CSV: ";" separator, cp1252 encoding, decimal commas
_CSV = (
    "Município;População;Preço\r\n"
    "Lisboa;545923;10,5\r\n"
    "Porto;231800;7,25\r\n"
    "Évora;53591;\r\n"
    "\r\n"
    "Braga;193324;100\r\n"
).encode("cp1252")


def _names(result: dict) -> list[str]:
    return [row["Município"] for row in result["data"]]


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    local_tabular_client.clear_cache()


class TestQueryRows:
    def test_reads_semicolon_cp1252_csv(self):
        result = query_rows(_CSV, "csv")
        assert result["meta"]["total"] == 4
        assert result["data"][2] == {
            "Município": "Évora",
            "População": "53591",
            "Preço": "",
        }
        assert result["links"] == {}

    def test_reads_utf8_bom_comma_csv(self):
        content = "﻿nome,valor\ncão,1\ngato,2\n".encode("utf-8")
        result = query_rows(content, "csv")
        assert result["data"] == [
            {"nome": "cão", "valor": "1"},
            {"nome": "gato", "valor": "2"},
        ]

    def test_pagination(self):
        result = query_rows(_CSV, "csv", page=2, page_size=3)
        assert _names(result) == ["Braga"]
        assert result["meta"] == {"total": 4, "page": 2, "page_size": 3}
        first = query_rows(_CSV, "csv", page=1, page_size=3)
        assert first["links"] == {"next": True}

    @pytest.mark.parametrize(
        "operator,value,expected",
        [
            ("exact", "Porto", ["Porto"]),
            ("exact", "231800.0", ["Porto"]),
            ("contains", "OR", ["Porto", "Évora"]),
            ("less", "193324", ["Évora", "Braga"]),
            ("strictly_less", "193324", ["Évora"]),
            ("greater", "231800", ["Lisboa", "Porto"]),
            ("strictly_greater", "231800", ["Lisboa"]),
        ],
    )
    def test_filter_operators(self, operator, value, expected):
        column = "Município" if operator in ("exact", "contains") else "População"
        if value == "231800.0":
            column = "População"
        result = query_rows(
            _CSV,
            "csv",
            filter_column=column,
            filter_value=value,
            filter_operator=operator,
        )
        assert _names(result) == expected

    def test_comparison_understands_decimal_commas_and_skips_blanks(self):
        result = query_rows(
            _CSV,
            "csv",
            filter_column="Preço",
            filter_value="10",
            filter_operator="less",
        )
        assert _names(result) == ["Porto"]

    def test_numeric_sort_with_blanks_last(self):
        asc = query_rows(_CSV, "csv", sort_column="Preço")
        assert _names(asc) == ["Porto", "Lisboa", "Braga", "Évora"]
        desc = query_rows(_CSV, "csv", sort_column="Preço", sort_direction="desc")
        assert _names(desc) == ["Braga", "Lisboa", "Porto", "Évora"]

    def test_text_sort_then_paginate(self):
        result = query_rows(
            _CSV, "csv", sort_column="Município", sort_direction="desc", page_size=2
        )
        assert _names(result) == ["Porto", "Lisboa"]
        assert result["meta"]["total"] == 4

    def test_unknown_column_lists_available_columns(self):
        with pytest.raises(LocalQueryError, match="Município, População, Preço"):
            query_rows(_CSV, "csv", sort_column="nope")

    def test_blank_and_duplicate_headers(self):
        result = query_rows(b"a,,a\n1,2,3\n", "csv")
        assert result["data"] == [{"a": "1", "column_2": "2", "a_2": "3"}]

    def test_empty_file(self):
        assert query_rows(b"", "csv")["meta"]["total"] == 0

    def test_xlsx(self):
        workbook = Workbook()
        sheet = workbook.active
        assert sheet is not None
        sheet.append(["nome", "valor"])
        sheet.append(["a", 3])
        sheet.append(["b", 1.5])
        buffer = io.BytesIO()
        workbook.save(buffer)
        result = query_rows(buffer.getvalue(), "xlsx", sort_column="valor")
        assert result["data"] == [
            {"nome": "b", "valor": "1.5"},
            {"nome": "a", "valor": "3"},
        ]

    def test_invalid_xlsx(self):
        with pytest.raises(LocalQueryError, match="not a valid XLSX"):
            query_rows(b"not a zip", "xlsx")


class TestDetectFormat:
    @pytest.mark.parametrize(
        "resource,expected",
        [
            ({"format": "CSV"}, "csv"),
            ({"format": "", "url": "https://x.org/a/file.xlsx?dl=1"}, "xlsx"),
            ({"format": "ods", "mime": "text/csv"}, "csv"),
            ({"format": "json", "url": "https://x.org/data.json"}, None),
            ({"format": "xls"}, None),
        ],
    )
    def test_detect_format(self, resource, expected):
        assert local_tabular_client.detect_format(resource) == expected


class TestFetchResourceData:
    async def test_downloads_once_then_uses_cache(self, niquests_mock: MockRouter):
        route = niquests_mock.get(_FILE_URL).respond(content=_CSV)
        resource = {"id": _RID, "url": _FILE_URL, "format": "csv"}
        first = await local_tabular_client.fetch_resource_data(resource, page_size=1)
        second = await local_tabular_client.fetch_resource_data(resource, page=2)
        assert first["meta"]["total"] == 4
        assert second["data"] == []
        assert route.call_count == 1

    async def test_rejects_declared_filesize_over_limit(
        self, niquests_mock: MockRouter, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_QUERY_MAX_BYTES", "10")
        resource = {"id": _RID, "url": _FILE_URL, "format": "csv", "filesize": 11}
        with pytest.raises(LocalQueryError, match="larger than"):
            await local_tabular_client.fetch_resource_data(resource)
        assert len(niquests_mock.calls) == 0

    async def test_aborts_download_over_limit(
        self, niquests_mock: MockRouter, monkeypatch
    ):
        monkeypatch.setenv("LOCAL_QUERY_MAX_BYTES", "10")
        niquests_mock.get(_FILE_URL).respond(content=_CSV)
        resource = {"id": _RID, "url": _FILE_URL, "format": "csv"}
        with pytest.raises(LocalQueryError, match="larger than"):
            await local_tabular_client.fetch_resource_data(resource)

    async def test_unsupported_format(self):
        resource = {"id": _RID, "url": "https://x.org/a.json", "format": "json"}
        with pytest.raises(LocalQueryError, match="cannot be queried"):
            await local_tabular_client.fetch_resource_data(resource)

    def test_max_bytes_env(self, monkeypatch):
        monkeypatch.setenv("LOCAL_QUERY_MAX_BYTES", "0")
        assert local_tabular_client.is_enabled() is False
        monkeypatch.setenv("LOCAL_QUERY_MAX_BYTES", "abc")
        assert (
            local_tabular_client.max_bytes() == local_tabular_client.DEFAULT_MAX_BYTES
        )


async def test_query_resource_data_tool_on_custom_instance(
    niquests_mock: MockRouter, monkeypatch
):
    """End to end: catalog lookup, file download, local filter and sort."""
    for var in ("TABULAR_API_URL", "LOCAL_QUERY_MAX_BYTES", "UDATA_SITE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("UDATA_API_URL", "https://dados.gov.pt/api/")
    monkeypatch.setenv("UDATA_PORTAL_NAME", "dados.gov.pt")
    niquests_mock.get(
        re.compile(rf"https://dados\.gov\.pt/api/2/datasets/resources/{_RID}/")
    ).respond(
        json={
            "resource": {
                "id": _RID,
                "title": "municipios.csv",
                "format": "csv",
                "url": _FILE_URL,
            },
            "dataset_id": None,
        }
    )
    niquests_mock.get(_FILE_URL).respond(content=_CSV)

    app = FastMCP()
    register_tools(app)
    content, _ = await app.call_tool(
        "query_resource_data",
        {
            "resource_id": _RID,
            "filter_column": "População",
            "filter_value": "100000",
            "filter_operator": "greater",
            "sort_column": "População",
            "sort_direction": "desc",
            "page_size": 2,
        },
    )
    text = content[0].text  # type: ignore

    assert "Querying resource: municipios.csv" in text
    assert "no Tabular API on dados.gov.pt" in text
    assert "Total rows (local copy): 3" in text
    assert "Total pages: 2 (page size: 2)" in text
    assert "Columns: Município, População, Preço" in text
    assert text.index("Lisboa") < text.index("Porto")
    assert "Braga" not in text
    assert "Use page=2" in text
