"""Tests for the semantic ranking and its fusion with keywords. Fake embeddings endpoint."""

import json

import niquests
import numpy as np
import pytest
from mcp.server.fastmcp import FastMCP
from niquests_mock import MockRouter

from helpers import catalog_mirror, catalog_search, catalog_semantic, embeddings_client
from helpers.embeddings_client import EmbeddingsError
from tests.test_catalog_search import _write_mirror
from tools import register_tools

_ENDPOINT = "http://embeddings.test/v1"

# A toy model: each concept is one dimension, named by any of its words
_CONCEPTS = [
    ("sismo", "terramoto", "earthquake"),
    ("escola", "ensino", "school"),
    ("contrato", "procurement"),
    ("saude", "saúde", "health"),
]

_DATASETS = [
    {"id": "sismos", "title": "Risco de Sismos", "organization": "ANEPC"},
    {"id": "escolas", "title": "Escolas", "organization": "Câmara de Águeda"},
    {"id": "contratos", "title": "Contratos Públicos", "organization": "IMPIC"},
    {"id": "saude", "title": "Despesa em saúde", "organization": "INE"},
]


def _toy_vector(text: str) -> list[float]:
    lowered = text.lower()
    vector = [
        1.0 if any(word in lowered for word in concept) else 0.0
        for concept in _CONCEPTS
    ]
    return vector + [0.05]  # never a zero vector


@pytest.fixture
def endpoint(niquests_mock: MockRouter, monkeypatch):
    """A fake OpenAI-compatible embeddings endpoint; returns its route."""
    monkeypatch.setenv("EMBEDDINGS_API_URL", _ENDPOINT)

    def respond(request):
        texts = json.loads(request.body)["input"]
        response = niquests.Response()
        response.status_code = 200
        response._content = json.dumps(
            {
                "data": [
                    {"index": index, "embedding": _toy_vector(text)}
                    for index, text in enumerate(texts)
                ]
            }
        ).encode()
        return response

    return niquests_mock.post(f"{_ENDPOINT}/embeddings").mock(side_effect=respond)


@pytest.fixture(autouse=True)
def portal(monkeypatch) -> None:
    monkeypatch.setenv("UDATA_API_URL", "https://dados.gov.pt/api/")
    monkeypatch.setenv("UDATA_PORTAL_NAME", "dados.gov.pt")
    monkeypatch.setenv("UDATA_LANGUAGE", "pt")
    catalog_search.clear_index()
    catalog_semantic.clear()


class TestEmbeddingsClient:
    async def test_returns_unit_vectors_in_order(self, endpoint):
        vectors = await embeddings_client.embed(["escola", "sismo e saude"])
        assert vectors.shape == (2, 5)
        assert vectors.dtype == np.float32
        assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
        assert vectors[0].argmax() == 1
        sent = json.loads(endpoint.calls[0].request.body)
        assert sent == {"model": "bge-m3", "input": ["escola", "sismo e saude"]}

    async def test_model_and_key_from_environment(self, endpoint, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_MODEL", "other-model")
        monkeypatch.setenv("EMBEDDINGS_API_KEY", "secret")
        await embeddings_client.embed(["escola"])
        request = endpoint.calls[0].request
        assert json.loads(request.body)["model"] == "other-model"
        assert request.headers["Authorization"] == "Bearer secret"

    async def test_not_configured(self):
        assert not embeddings_client.is_configured()
        with pytest.raises(EmbeddingsError, match="EMBEDDINGS_API_URL"):
            await embeddings_client.embed(["escola"])

    async def test_wrong_number_of_vectors(self, niquests_mock, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_API_URL", _ENDPOINT)
        niquests_mock.post(f"{_ENDPOINT}/embeddings").respond(
            json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        )
        with pytest.raises(EmbeddingsError, match="Expected 2"):
            await embeddings_client.embed(["a", "b"])


class TestBuild:
    async def test_builds_then_only_embeds_what_changed(self, endpoint):
        _write_mirror(_DATASETS)
        assert not catalog_semantic.is_available()
        assert await catalog_semantic.build() == (4, 4)
        assert catalog_semantic.is_available()

        endpoint.reset()
        assert await catalog_semantic.build() == (4, 0)
        assert endpoint.call_count == 0

        _write_mirror(
            _DATASETS[:3]
            + [{"id": "novo", "title": "Ensino superior", "organization": "DGEEC"}]
        )
        assert await catalog_semantic.build() == (4, 1)
        sent = json.loads(endpoint.calls[0].request.body)["input"]
        assert sent == ["Ensino superior\nDGEEC"]
        ranked = await catalog_semantic.rank("school")
        assert {dataset_id for dataset_id, _ in ranked} == {"escolas", "novo"}

    async def test_force_embeds_everything_again(self, endpoint):
        _write_mirror(_DATASETS)
        await catalog_semantic.build()
        assert await catalog_semantic.build(force=True) == (4, 4)

    async def test_changing_the_model_embeds_everything_again(
        self, endpoint, monkeypatch
    ):
        _write_mirror(_DATASETS)
        await catalog_semantic.build()
        monkeypatch.setenv("EMBEDDINGS_MODEL", "other-model")
        assert await catalog_semantic.build() == (4, 4)

    def test_dataset_text(self):
        text = catalog_semantic.dataset_text(
            {
                "title": "Escolas",
                "organization": " Câmara de Águeda ",
                "tags": "educacao,ensino-basico",
                "description": "Lista\n\nde   escolas. " + "x" * 2000,
            }
        )
        lines = text.split("\n")
        assert lines[:3] == ["Escolas", "Câmara de Águeda", "educacao, ensino basico"]
        assert lines[3].startswith("Lista de escolas. xxx")
        assert len(lines[3]) == 1000

    def test_main_reports_failure(self, monkeypatch, capsys):
        # No mirror, no endpoint
        assert catalog_semantic.main([]) == 1
        assert "failed" in capsys.readouterr().err


class TestRank:
    @pytest.fixture(autouse=True)
    async def built(self, endpoint) -> None:
        _write_mirror(_DATASETS)
        await catalog_semantic.build()
        endpoint.reset()

    async def test_finds_by_meaning_not_words(self):
        ranked = await catalog_semantic.rank("perigo de terramotos")
        assert [dataset_id for dataset_id, _ in ranked] == ["sismos"]
        assert ranked[0][1] > 0.9

    async def test_unrelated_query_finds_nothing(self):
        assert await catalog_semantic.rank("xyzzy") == []

    async def test_query_embeddings_are_cached(self, endpoint):
        await catalog_semantic.rank("Terramotos")
        await catalog_semantic.rank("  terramotos ")
        assert endpoint.call_count == 1

    async def test_can_be_turned_off(self, monkeypatch):
        assert catalog_semantic.is_available()
        monkeypatch.setenv("UDATA_SEMANTIC_SEARCH", "0")
        assert not catalog_semantic.is_available()


class TestHybridSearch:
    @pytest.fixture(autouse=True)
    async def built(self, endpoint) -> None:
        _write_mirror(_DATASETS)
        await catalog_semantic.build()

    async def test_semantic_adds_what_keywords_miss(self):
        keyword = await catalog_search.search_datasets("terramotos", semantic=False)
        assert keyword["total"] == 0
        assert keyword["ranking"] == "keyword"

        hybrid = await catalog_search.search_datasets("terramotos")
        assert [dataset["id"] for dataset in hybrid["data"]] == ["sismos"]
        assert hybrid["ranking"] == "hybrid"

    async def test_agreement_ranks_first(self):
        # Keywords find both "saude" and "contratos" (one word each); meaning agrees on contratos
        result = await catalog_search.search_datasets("despesa procurement público")
        assert [dataset["id"] for dataset in result["data"]][:1] == ["contratos"]
        assert {dataset["id"] for dataset in result["data"]} == {"contratos", "saude"}

    async def test_endpoint_failure_falls_back_to_keywords(
        self, niquests_mock: MockRouter
    ):
        catalog_semantic.clear()
        niquests_mock.reset()
        niquests_mock.post(f"{_ENDPOINT}/embeddings").respond(503)
        result = await catalog_search.search_datasets("escolas")
        assert [dataset["id"] for dataset in result["data"]] == ["escolas"]
        assert result["ranking"] == "keyword"
        with pytest.raises(niquests.HTTPError):
            await catalog_search.search_datasets("escolas", semantic=True)

    async def test_stale_vectors_are_ignored(self):
        # The mirror moved on: a dataset the vectors know about is gone
        _write_mirror(_DATASETS[1:])
        result = await catalog_search.search_datasets("terramotos")
        assert result["total"] == 0

    async def test_tool_output_and_description(self):
        app = FastMCP()
        register_tools(app)
        tools = {tool.name: tool for tool in await app.list_tools()}
        assert "by meaning" in (tools["search_datasets"].description or "")
        content, _ = await app.call_tool("search_datasets", {"query": "earthquakes"})
        text = content[0].text  # type: ignore
        assert "1. Risco de Sismos" in text
        assert "ranked by keywords and meaning" in text


def test_vectors_file_sits_next_to_the_mirror():
    assert (
        catalog_semantic._vectors_path()
        == catalog_mirror.catalog_dir() / "dataset.embeddings.npz"
    )
