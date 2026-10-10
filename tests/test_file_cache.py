"""Tests for the on-disk cache of downloaded resource files."""

import os
import time

from helpers import file_cache

_URL = "https://files.example.org/a.csv"


def _store(url: str, size: int, accessed_ago: float = 0.0) -> None:
    file_cache.store(url, b"x" * size, version="v1")
    if accessed_ago:
        stamp = time.time() - accessed_ago
        os.utime(file_cache._paths(url)[0], (stamp, stamp))


def _cached(*urls: str) -> list[bool]:
    return [file_cache.lookup(url) is not None for url in urls]


class TestSettings:
    def test_defaults(self, monkeypatch, tmp_path):
        assert file_cache.max_bytes() == file_cache.DEFAULT_MAX_BYTES
        assert file_cache.revalidate_seconds() == 24 * 60 * 60
        assert file_cache.is_enabled()
        monkeypatch.delenv("LOCAL_QUERY_CACHE_DIR")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert file_cache.cache_dir() == tmp_path / "datagouv-mcp" / "resources"

    def test_zero_disables_and_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("LOCAL_QUERY_CACHE_MAX_BYTES", "0")
        assert not file_cache.is_enabled()
        monkeypatch.setenv("LOCAL_QUERY_CACHE_MAX_BYTES", "lots")
        assert file_cache.max_bytes() == file_cache.DEFAULT_MAX_BYTES


class TestStoreAndRead:
    def test_round_trip(self):
        assert file_cache.lookup(_URL) is None
        assert file_cache.read(_URL) is None
        file_cache.store(_URL, b"a;b\n1;2\n", version="v1", etag='"abc"')
        meta = file_cache.lookup(_URL)
        assert meta is not None
        assert meta["version"] == "v1"
        assert meta["etag"] == '"abc"'
        assert meta["size"] == 8
        assert meta["fetched_at"] == meta["checked_at"]
        assert file_cache.read(_URL) == b"a;b\n1;2\n"

    def test_read_records_the_access(self):
        _store(_URL, 10, accessed_ago=3600)
        path = file_cache._paths(_URL)[0]
        before = path.stat().st_mtime
        file_cache.read(_URL)
        assert path.stat().st_mtime > before + 3000

    def test_mark_checked(self):
        file_cache.store(_URL, b"x", version="v1")
        path = file_cache._paths(_URL)[1]
        path.write_text(
            path.read_text().replace('"checked_at"', '"checked_at": 1, "_"')
        )
        assert file_cache.lookup(_URL)["checked_at"] == 1  # type: ignore
        file_cache.mark_checked(_URL)
        assert file_cache.lookup(_URL)["checked_at"] > time.time() - 5  # type: ignore

    def test_missing_data_file_is_a_miss(self):
        file_cache.store(_URL, b"x", version="v1")
        file_cache._paths(_URL)[0].unlink()
        assert file_cache.lookup(_URL) is None

    def test_remove(self):
        file_cache.store(_URL, b"x", version="v1")
        file_cache.remove(_URL)
        assert file_cache.lookup(_URL) is None
        assert list(file_cache.cache_dir().iterdir()) == []


class TestEviction:
    def test_deletes_least_recently_accessed_first(self, monkeypatch):
        monkeypatch.setenv("LOCAL_QUERY_CACHE_MAX_BYTES", "250")
        old, mid, new = (f"https://files.example.org/{n}.csv" for n in "abc")
        _store(old, 100, accessed_ago=300)
        _store(mid, 100, accessed_ago=200)
        assert _cached(old, mid) == [True, True]

        _store(new, 100)
        assert _cached(old, mid, new) == [False, True, True]
        assert len(list(file_cache.cache_dir().iterdir())) == 4

    def test_an_access_protects_a_file(self, monkeypatch):
        monkeypatch.setenv("LOCAL_QUERY_CACHE_MAX_BYTES", "250")
        old, mid, new = (f"https://files.example.org/{n}.csv" for n in "abc")
        _store(old, 100, accessed_ago=300)
        _store(mid, 100, accessed_ago=200)
        file_cache.read(old)

        _store(new, 100)
        assert _cached(old, mid, new) == [True, False, True]

    def test_deletes_as_many_as_needed(self, monkeypatch):
        monkeypatch.setenv("LOCAL_QUERY_CACHE_MAX_BYTES", "250")
        urls = [f"https://files.example.org/{n}.csv" for n in "abc"]
        for age, url in zip((300, 200, 100), urls):
            _store(url, 80, accessed_ago=age)
        big = "https://files.example.org/big.csv"
        _store(big, 200)
        assert _cached(*urls, big) == [False, False, False, True]

    def test_file_larger_than_the_cache_is_not_stored(self, monkeypatch):
        monkeypatch.setenv("LOCAL_QUERY_CACHE_MAX_BYTES", "50")
        _store(_URL, 100)
        assert file_cache.lookup(_URL) is None

    def test_lowering_the_limit_applies_on_next_store(self, monkeypatch):
        urls = [f"https://files.example.org/{n}.csv" for n in "abc"]
        for age, url in zip((300, 200, 100), urls):
            _store(url, 100, accessed_ago=age)
        monkeypatch.setenv("LOCAL_QUERY_CACHE_MAX_BYTES", "150")
        assert len(file_cache.evict()) == 2
        assert _cached(*urls) == [False, False, True]
