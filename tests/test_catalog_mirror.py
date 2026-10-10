"""Tests for the local mirror of a portal's catalog export. Mocked HTTP, no live API."""

import hashlib
import json

import pytest
from niquests_mock import MockRouter

from helpers import catalog_mirror
from helpers.catalog_mirror import CatalogMirrorError

_DATASET_URL = "https://dados.gov.pt/api/1/datasets/catalogo/"
_FILES = "https://dados.gov.pt/s/resources/catalogo"
_DATASETS_CSV = b"id;title\n1;Escolas\n"
_ORGS_CSV = b"id;name\n9;INE\n"


def _resource(model: str, stamp: str, content: bytes, **overrides) -> dict:
    return {
        "title": f"export-{model}-{stamp}.csv",
        "url": f"{_FILES}/{stamp}/export-{model}-{stamp}.csv",
        "last_modified": f"{stamp}",
        "filesize": len(content),
        "checksum": {"type": "sha1", "value": hashlib.sha1(content).hexdigest()},
        "extras": {"csv-export:model": model},
        **overrides,
    }


@pytest.fixture(autouse=True)
def portal(monkeypatch) -> None:
    monkeypatch.setenv("UDATA_API_URL", "https://dados.gov.pt/api/")
    monkeypatch.setenv("UDATA_CATALOG_DATASET", "catalogo")


def _publish(router: MockRouter, *resources: tuple[dict, bytes]) -> dict:
    """Mock the catalog dataset and its files; returns the file routes by model."""
    router.reset()
    router.get(_DATASET_URL).respond(
        json={"resources": [resource for resource, _ in resources]}
    )
    return {
        catalog_mirror.export_model(resource): router.get(resource["url"]).respond(
            content=content
        )
        for resource, content in resources
    }


class TestSettings:
    def test_default_directory_is_per_portal(self, monkeypatch, tmp_path):
        monkeypatch.delenv("UDATA_CATALOG_DIR")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        assert (
            catalog_mirror.catalog_dir()
            == tmp_path / "datagouv-mcp" / "catalog" / "dados.gov.pt"
        )

    @pytest.mark.parametrize(
        "resource,expected",
        [
            ({"extras": {"csv-export:model": "dataset"}}, "dataset"),
            ({"title": "export-resource-20261004-021151.csv"}, "resource"),
            ({"title": "notes.csv", "extras": {}}, None),
            ({"extras": {"csv-export:model": "../etc"}}, None),
        ],
    )
    def test_export_model(self, resource, expected):
        assert catalog_mirror.export_model(resource) == expected


class TestSync:
    async def test_first_run_downloads_everything(self, niquests_mock: MockRouter):
        _publish(
            niquests_mock,
            (_resource("dataset", "20261004-021057", _DATASETS_CSV), _DATASETS_CSV),
            (_resource("organization", "20261004-021158", _ORGS_CSV), _ORGS_CSV),
        )
        report = await catalog_mirror.sync()

        assert report.updated == ["dataset", "organization"]
        assert report.ok
        directory = catalog_mirror.catalog_dir()
        assert (directory / "dataset.csv").read_bytes() == _DATASETS_CSV
        assert (directory / "organization.csv").read_bytes() == _ORGS_CSV
        state = json.loads((directory / "state.json").read_text())
        assert state["portal"] == "dados.gov.pt"
        assert state["files"]["dataset"]["size"] == len(_DATASETS_CSV)

    async def test_unchanged_files_are_not_downloaded_again(
        self, niquests_mock: MockRouter
    ):
        published = (
            (_resource("dataset", "20261004-021057", _DATASETS_CSV), _DATASETS_CSV),
            (_resource("organization", "20261004-021158", _ORGS_CSV), _ORGS_CSV),
        )
        _publish(niquests_mock, *published)
        await catalog_mirror.sync()

        routes = _publish(niquests_mock, *published)
        report = await catalog_mirror.sync()
        assert report.updated == []
        assert report.unchanged == ["dataset", "organization"]
        assert [route.call_count for route in routes.values()] == [0, 0]

    async def test_only_the_changed_export_is_downloaded(
        self, niquests_mock: MockRouter
    ):
        orgs = (_resource("organization", "20261004-021158", _ORGS_CSV), _ORGS_CSV)
        _publish(
            niquests_mock,
            (_resource("dataset", "20261004-021057", _DATASETS_CSV), _DATASETS_CSV),
            orgs,
        )
        await catalog_mirror.sync()

        newer = _DATASETS_CSV + b"2;Contratos\n"
        routes = _publish(
            niquests_mock, (_resource("dataset", "20261011-021100", newer), newer), orgs
        )
        report = await catalog_mirror.sync()
        assert report.updated == ["dataset"]
        assert report.unchanged == ["organization"]
        assert routes["organization"].call_count == 0
        assert (catalog_mirror.catalog_dir() / "dataset.csv").read_bytes() == newer

    async def test_missing_local_file_is_downloaded_again(
        self, niquests_mock: MockRouter
    ):
        published = (
            _resource("dataset", "20261004-021057", _DATASETS_CSV),
            _DATASETS_CSV,
        )
        _publish(niquests_mock, published)
        await catalog_mirror.sync()
        (catalog_mirror.catalog_dir() / "dataset.csv").unlink()

        _publish(niquests_mock, published)
        assert (await catalog_mirror.sync()).updated == ["dataset"]

    async def test_force_downloads_everything(self, niquests_mock: MockRouter):
        published = (
            _resource("dataset", "20261004-021057", _DATASETS_CSV),
            _DATASETS_CSV,
        )
        _publish(niquests_mock, published)
        await catalog_mirror.sync()
        _publish(niquests_mock, published)
        assert (await catalog_mirror.sync(force=True)).updated == ["dataset"]

    @pytest.mark.parametrize(
        "overrides,reason",
        [
            ({"checksum": {"type": "sha1", "value": "0" * 40}}, "checksum"),
            ({"filesize": 5}, "received"),
        ],
    )
    async def test_corrupt_download_keeps_the_previous_file(
        self, niquests_mock: MockRouter, overrides, reason
    ):
        _publish(
            niquests_mock,
            (_resource("dataset", "20261004-021057", _DATASETS_CSV), _DATASETS_CSV),
        )
        await catalog_mirror.sync()

        newer = _DATASETS_CSV + b"2;Contratos\n"
        published = (_resource("dataset", "20261011-021100", newer, **overrides), newer)
        _publish(niquests_mock, published)
        report = await catalog_mirror.sync()

        assert not report.ok
        assert reason in report.failed["dataset"]
        directory = catalog_mirror.catalog_dir()
        assert (directory / "dataset.csv").read_bytes() == _DATASETS_CSV
        assert not list(directory.glob("*.part"))
        # Still pending: the next run tries again
        _publish(niquests_mock, (_resource("dataset", "20261011-021100", newer), newer))
        assert (await catalog_mirror.sync()).updated == ["dataset"]

    async def test_failed_download_is_reported(self, niquests_mock: MockRouter):
        resource = _resource("dataset", "20261004-021057", _DATASETS_CSV)
        niquests_mock.get(_DATASET_URL).respond(json={"resources": [resource]})
        niquests_mock.get(resource["url"]).respond(503)
        report = await catalog_mirror.sync()
        assert list(report.failed) == ["dataset"]

    async def test_not_configured(self, monkeypatch):
        monkeypatch.delenv("UDATA_CATALOG_DATASET")
        with pytest.raises(CatalogMirrorError, match="UDATA_CATALOG_DATASET"):
            await catalog_mirror.sync()

    async def test_dataset_without_exports(self, niquests_mock: MockRouter):
        niquests_mock.get(_DATASET_URL).respond(
            json={"resources": [{"title": "notes.csv", "url": "https://x.org/n.csv"}]}
        )
        with pytest.raises(CatalogMirrorError, match="no catalog export files"):
            await catalog_mirror.sync()


class TestMain:
    def test_exit_codes(self, niquests_mock: MockRouter, monkeypatch, capsys):
        _publish(
            niquests_mock,
            (_resource("dataset", "20261004-021057", _DATASETS_CSV), _DATASETS_CSV),
        )
        assert catalog_mirror.main([]) == 0
        assert "updated: dataset" in capsys.readouterr().out

        monkeypatch.delenv("UDATA_CATALOG_DATASET")
        assert catalog_mirror.main([]) == 1
        assert "UDATA_CATALOG_DATASET" in capsys.readouterr().err
