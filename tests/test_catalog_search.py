"""Tests for dataset search over the local catalog copy. No network."""

import csv
import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from mcp.server.fastmcp import FastMCP
from niquests_mock import MockRouter

from helpers import catalog_mirror, catalog_search
from tools import register_tools

_COLUMNS = [
    "id",
    "title",
    "slug",
    "acronym",
    "organization",
    "description",
    "description_short",
    "tags",
    "archived",
    "resources_count",
    "created_at",
    "last_modified",
    "metric.reuses",
    "metric.followers",
    "metric.views",
]


def _recent(days_ago: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()[:19]


_DATASETS = [
    {
        "id": "escolas",
        "title": "Escolas",
        "organization": "Câmara Municipal de Águeda",
        "description": "Estabelecimentos de ensino\ndo concelho.",
        "tags": "educacao,ensino",
        "resources_count": "3",
        "created_at": "2019-01-01T00:00:00",
        "last_modified": _recent(10),
        "metric.views": "50",
    },
    {
        "id": "conducao",
        "title": "Escolas de condução",
        "organization": "Câmara Municipal de Águeda",
        "description": "Escolas de condução licenciadas.",
        "created_at": "2020-01-01T00:00:00",
        "last_modified": _recent(400),
        "metric.views": "5",
    },
    {
        "id": "contratos",
        "title": "Contratos Públicos - Portal Base",
        "organization": "IMPIC",
        "description": "Contratos celebrados por entidades públicas.",
        "tags": "contratacao-publica",
        "created_at": "2021-01-01T00:00:00",
        "last_modified": _recent(2),
        "metric.views": "900",
    },
    {
        "id": "saude",
        "title": "Despesa em saúde",
        "organization": "INE",
        "description": "Inclui a despesa com contratos de prestação de serviços.",
        "created_at": "2022-01-01T00:00:00",
        "last_modified": _recent(2000),
    },
    {
        "id": "arquivado",
        "title": "Contratos Públicos antigos",
        "organization": "IMPIC",
        "archived": "2024-01-01T00:00:00",
    },
] + [
    # Filler, so that "dados" and "de" are common words and the others are rare
    {
        "id": f"filler-{n}",
        "title": f"Indicador {n} de dados",
        "organization": "INE",
        "description": "Série de dados estatísticos.",
    }
    for n in range(12)
]


def _write_mirror(datasets: list[dict], exported_days_ago: int = 3) -> None:
    directory = catalog_mirror.catalog_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "dataset.csv").open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=_COLUMNS, delimiter=";")
        writer.writeheader()
        for dataset in datasets:
            writer.writerow({"archived": "False", "slug": dataset["id"], **dataset})
    exported = datetime.now(UTC) - timedelta(days=exported_days_ago)
    state = {"files": {"dataset": {"last_modified": exported.isoformat()}}}
    (directory / "state.json").write_text(json.dumps(state))


@pytest.fixture(autouse=True)
def portal(monkeypatch) -> None:
    monkeypatch.setenv("UDATA_API_URL", "https://dados.gov.pt/api/")
    monkeypatch.setenv("UDATA_PORTAL_NAME", "dados.gov.pt")
    monkeypatch.setenv("UDATA_LANGUAGE", "pt")
    monkeypatch.delenv("UDATA_CATALOG_SEARCH", raising=False)
    catalog_search.clear_index()


async def _ids(query: str, **kwargs) -> list[str]:
    result = await catalog_search.search_datasets(query, **kwargs)
    return [dataset["id"] for dataset in result["data"]]


class TestAvailability:
    def test_needs_a_mirror(self):
        assert not catalog_search.is_available()
        _write_mirror(_DATASETS)
        assert catalog_search.is_available()

    def test_can_be_turned_off(self, monkeypatch):
        _write_mirror(_DATASETS)
        monkeypatch.setenv("UDATA_CATALOG_SEARCH", "0")
        assert not catalog_search.is_available()

    def test_old_export_is_not_used(self):
        _write_mirror(_DATASETS, exported_days_ago=30)
        assert not catalog_search.is_available()


class TestTokenize:
    def test_folds_accents_case_and_plurals(self):
        assert catalog_search.tokenize("Escolas de ÁGUEDA, contratação-pública") == [
            "escola",
            "de",
            "agueda",
            "contratacao",
            "publica",
        ]


class TestSearch:
    @pytest.fixture(autouse=True)
    def mirror(self) -> None:
        _write_mirror(_DATASETS)

    async def test_result_shape(self):
        result = await catalog_search.search_datasets("escolas águeda", page_size=1)
        assert result["total"] == 2
        assert result["page"] == 1
        assert result["exported_at"]
        assert result["data"] == [
            {
                "id": "escolas",
                "title": "Escolas",
                "description_short": "Estabelecimentos de ensino do concelho.",
                "slug": "escolas",
                "organization": "Câmara Municipal de Águeda",
                "tags": ["educacao", "ensino"],
                "resources_count": 3,
                "url": "https://dados.gov.pt/datasets/escolas",
            }
        ]

    async def test_accents_case_and_plural_do_not_matter(self):
        assert await _ids("ESCOLA agueda") == ["escolas", "conducao"]

    async def test_title_match_ranks_above_description_match(self):
        assert await _ids("contratos") == ["contratos", "saude"]

    async def test_generic_and_common_words_do_not_hide_results(self):
        # The portal's search returns nothing for these; "ficheiro" is in no dataset
        assert (await _ids("dados de contratos públicos"))[0] == "contratos"
        assert (await _ids("ficheiro com as escolas de Águeda"))[0] == "escolas"

    async def test_partial_matches_rank_below_full_matches(self):
        ids = await _ids("contratos públicos saúde")
        assert set(ids) == {"contratos", "saude"}

    async def test_archived_datasets_are_left_out(self):
        assert "arquivado" not in await _ids("contratos públicos antigos")

    async def test_unknown_words_find_nothing(self):
        result = await catalog_search.search_datasets("xyzzy")
        assert result["total"] == 0
        assert result["data"] == []

    async def test_only_common_words_still_searches(self):
        assert (await catalog_search.search_datasets("dados"))["total"] == 12

    async def test_pagination(self):
        assert await _ids("escolas", page=2, page_size=1) == ["conducao"]
        assert await _ids("escolas", page=3, page_size=1) == []

    async def test_sort(self):
        assert await _ids("escolas", sort="created") == ["escolas", "conducao"]
        assert await _ids("escolas", sort="-created") == ["conducao", "escolas"]
        assert await _ids("contratos", sort="views") == ["saude", "contratos"]
        with pytest.raises(ValueError, match="sort"):
            await _ids("escolas", sort="title")

    @pytest.mark.parametrize(
        "update_range,expected",
        [
            ("last_30_days", ["escolas"]),
            ("last_12_months", ["escolas"]),
            ("last_3_years", ["escolas", "conducao"]),
        ],
    )
    async def test_last_update_range(self, update_range, expected):
        assert await _ids("escolas", last_update_range=update_range) == expected

    async def test_index_is_rebuilt_when_the_file_changes(self):
        assert await _ids("biblioteca") == []
        _write_mirror(_DATASETS + [{"id": "novo", "title": "Bibliotecas"}])
        assert await _ids("biblioteca") == ["novo"]


class TestSearchDatasetsTool:
    async def _call(self, query: str) -> str:
        app = FastMCP()
        register_tools(app)
        content, _ = await app.call_tool("search_datasets", {"query": query})
        return content[0].text  # type: ignore

    async def test_answers_from_the_local_copy(self, niquests_mock: MockRouter):
        _write_mirror(_DATASETS)
        text = await self._call("ficheiro com as escolas de Águeda")

        assert len(niquests_mock.calls) == 0
        assert "Found 2 dataset(s)" in text
        assert "Source: local copy of the catalog (exported " in text
        assert text.index("1. Escolas\n") < text.index("2. Escolas de condução")
        assert "URL: https://dados.gov.pt/datasets/escolas" in text

    async def test_description_matches_the_search_in_use(self):
        async def description() -> str:
            app = FastMCP()
            register_tools(app)
            tools = {tool.name: tool for tool in await app.list_tools()}
            return tools["search_datasets"].description or ""

        assert "AND logic" in await description()
        _write_mirror(_DATASETS)
        assert "ranked by relevance" in await description()
        assert "AND logic" not in await description()

    async def test_falls_back_to_the_api_when_nothing_is_found_locally(
        self, niquests_mock: MockRouter
    ):
        _write_mirror(_DATASETS)
        route = niquests_mock.get(
            re.compile(r"https://dados\.gov\.pt/api/2/datasets/search/")
        ).respond(
            json={
                "data": [{"id": "novo", "title": "Publicado ontem", "slug": "novo"}],
                "total": 1,
            }
        )
        text = await self._call("xyzzy")

        assert route.call_count == 1
        assert "1. Publicado ontem" in text
        assert "local copy" not in text

    async def test_uses_the_api_without_a_local_copy(self, niquests_mock: MockRouter):
        route = niquests_mock.get(
            re.compile(r"https://dados\.gov\.pt/api/2/datasets/search/")
        ).respond(json={"data": [], "total": 0})
        text = await self._call("escolas")
        assert route.call_count == 1
        assert text == "No datasets found for query: 'escolas'"
