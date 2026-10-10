import pytest


@pytest.fixture(autouse=True)
def isolated_local_storage(tmp_path, monkeypatch) -> None:
    """Keep the on-disk file cache and catalog mirror out of the user's home."""
    monkeypatch.setenv("LOCAL_QUERY_CACHE_DIR", str(tmp_path / "file-cache"))
    monkeypatch.setenv("UDATA_CATALOG_DIR", str(tmp_path / "catalog"))
    for var in (
        "LOCAL_QUERY_CACHE_MAX_BYTES",
        "LOCAL_QUERY_CACHE_REVALIDATE_SECONDS",
        "UDATA_CATALOG_DATASET",
    ):
        monkeypatch.delenv(var, raising=False)
