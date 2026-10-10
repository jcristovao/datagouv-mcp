"""
Semantic side of the local dataset search: one embedding per dataset of the
catalog mirror, compared with the embedding of the query.

It finds datasets that share no word with the query ("terramotos" for "Risco
de Sismos", or a query in another language). helpers/catalog_search.py merges
its ranking with the keyword ranking.

Optional. It needs an embeddings endpoint (EMBEDDINGS_API_URL) and the vectors
built by `python -m helpers.catalog_semantic`, run after each catalog refresh.
Only datasets that are new or whose text changed are embedded again.
"""

import argparse
import asyncio
import hashlib
import logging
import os
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import niquests
import numpy as np

from helpers import catalog_mirror, embeddings_client
from helpers.logging import MAIN_LOGGER_NAME

logger = logging.getLogger(MAIN_LOGGER_NAME)

VECTORS_FILE = "dataset.embeddings.npz"
# Enough of the description to carry its subject; keeps the build fast
_DESCRIPTION_CHARS = 1000
_BATCH_SIZE = 32
_CONCURRENT_BATCHES = 4
# A search must not wait long for the query embedding: keyword results are the fallback
_QUERY_TIMEOUT_SECONDS = 5.0
_QUERY_CACHE_SIZE = 256
# Datasets less similar to the query than this are not worth suggesting
MIN_SIMILARITY = 0.45
DEFAULT_LIMIT = 50


@dataclass(frozen=True)
class _Vectors:
    ids: list[str]
    matrix: np.ndarray
    model: str


_lock = threading.Lock()
_loaded: tuple[tuple[str, int], _Vectors] | None = None
_query_cache: OrderedDict[tuple[str, str], np.ndarray] = OrderedDict()


def _vectors_path() -> Path:
    return catalog_mirror.catalog_dir() / VECTORS_FILE


def is_available() -> bool:
    """Whether queries can be embedded and compared: endpoint set, vectors built."""
    if os.getenv("UDATA_SEMANTIC_SEARCH", "").strip().lower() in {"0", "false", "no"}:
        return False
    try:
        return embeddings_client.is_configured() and _vectors_path().is_file()
    except (OSError, KeyError):
        return False


def dataset_text(row: dict[str, str]) -> str:
    """The text that represents a dataset: what it is, who publishes it, its tags."""
    description = " ".join((row.get("description") or "").split())
    parts = [
        row.get("title") or "",
        (row.get("organization") or "").strip(),
        (row.get("tags") or "").replace(",", ", ").replace("-", " "),
        description[:_DESCRIPTION_CHARS],
    ]
    return "\n".join(part for part in parts if part)


def _text_hash(text: str, model: str) -> str:
    return hashlib.sha1(f"{model}\n{text}".encode("utf-8")).hexdigest()


def _read_store(path: Path) -> tuple[list[str], list[str], np.ndarray, str]:
    with np.load(path, allow_pickle=False) as store:
        return (
            [str(value) for value in store["ids"]],
            [str(value) for value in store["hashes"]],
            store["vectors"].astype(np.float32),
            str(store["model"]),
        )


async def build(force: bool = False) -> tuple[int, int]:
    """
    Bring the vectors in line with the mirrored dataset export.

    Returns (datasets, newly embedded). Vectors of unchanged datasets are kept.

    Raises:
        OSError: no catalog mirror.
        EmbeddingsError / niquests.RequestException: the endpoint failed.
    """
    path = _vectors_path()
    model = embeddings_client.model()
    rows = list(catalog_mirror.iter_dataset_rows(path.parent / "dataset.csv"))
    ids = [row["id"] for row in rows]
    texts = [dataset_text(row) for row in rows]
    hashes = [_text_hash(text, model) for text in texts]

    known: dict[str, np.ndarray] = {}
    if path.is_file() and not force:
        _, old_hashes, old_vectors, _ = _read_store(path)
        known = dict(zip(old_hashes, old_vectors))

    missing = [index for index, value in enumerate(hashes) if value not in known]
    batches = [
        missing[start : start + _BATCH_SIZE]
        for start in range(0, len(missing), _BATCH_SIZE)
    ]
    started = time.monotonic()
    limiter = asyncio.Semaphore(_CONCURRENT_BATCHES)
    session = niquests.AsyncSession()

    async def embed_batch(batch: list[int]) -> None:
        async with limiter:
            vectors = await embeddings_client.embed(
                [texts[index] for index in batch], timeout=300.0, session=session
            )
        for index, vector in zip(batch, vectors):
            known[hashes[index]] = vector

    try:
        await asyncio.gather(*(embed_batch(batch) for batch in batches))
    finally:
        await session.close()

    if missing or not path.is_file() or len(known) != len(set(hashes)):
        matrix = (
            np.stack([known[value] for value in hashes])
            if hashes
            else np.zeros((0, 0), dtype=np.float32)
        )
        temporary = path.with_name(f"{path.name}.tmp.npz")
        np.savez(
            temporary,
            ids=np.asarray(ids),
            hashes=np.asarray(hashes),
            vectors=matrix.astype(np.float16),
            model=np.asarray(model),
        )
        os.replace(temporary, path)
    logger.info(
        f"Catalog semantic: {len(ids)} datasets, {len(missing)} embedded "
        f"in {time.monotonic() - started:.0f}s"
    )
    return len(ids), len(missing)


def _get_vectors() -> _Vectors:
    global _loaded
    path = _vectors_path()
    key = (str(path), path.stat().st_mtime_ns)
    with _lock:
        if _loaded is None or _loaded[0] != key:
            ids, _, matrix, model = _read_store(path)
            _loaded = (key, _Vectors(ids, matrix, model))
            _query_cache.clear()
        return _loaded[1]


def clear() -> None:
    """Forget loaded vectors and cached queries. Useful for testing."""
    global _loaded
    with _lock:
        _loaded = None
        _query_cache.clear()


async def rank(query: str, limit: int = DEFAULT_LIMIT) -> list[tuple[str, float]]:
    """
    IDs of the datasets closest in meaning to the query, with their similarity,
    best first. Datasets under MIN_SIMILARITY are left out.

    Raises:
        OSError, EmbeddingsError, niquests.RequestException: see is_available().
    """
    vectors = await asyncio.to_thread(_get_vectors)
    if not len(vectors.ids):
        return []
    key = (vectors.model, " ".join(query.split()).casefold())
    query_vector = _query_cache.get(key)
    if query_vector is None:
        embedded = await embeddings_client.embed(
            [query], timeout=_QUERY_TIMEOUT_SECONDS
        )
        query_vector = embedded[0]
        _query_cache[key] = query_vector
        while len(_query_cache) > _QUERY_CACHE_SIZE:
            _query_cache.popitem(last=False)
    else:
        _query_cache.move_to_end(key)

    similarities = vectors.matrix @ query_vector
    count = min(limit, len(similarities))
    top = np.argpartition(-similarities, count - 1)[:count]
    top = top[np.argsort(-similarities[top])]
    return [
        (vectors.ids[index], float(similarities[index]))
        for index in top
        if similarities[index] >= MIN_SIMILARITY
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the dataset embeddings for the local catalog mirror."
    )
    parser.add_argument(
        "--force", action="store_true", help="embed every dataset again"
    )
    args = parser.parse_args(argv)
    try:
        datasets, embedded = asyncio.run(build(force=args.force))
    except Exception as e:  # noqa: BLE001
        print(f"Catalog semantic build failed: {e}", file=sys.stderr)
        return 1
    print(f"Catalog semantic: {_vectors_path()}")
    print(f"  datasets: {datasets}, newly embedded: {embedded}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
