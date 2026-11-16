"""
Dataset search over the local copy of the catalog (see helpers/catalog_mirror.py).

The portal's own search requires every word of the query to match. This one
ranks instead: each query word weighs by how rare it is in the catalog, a
match in the title counts more than one in the description, and a dataset
missing some of the words is ranked lower rather than left out. Results use
the same shape as datagouv_api_client.search_datasets.

The index is built in memory on first use (about 21k datasets on dados.gov.pt)
and rebuilt when the mirrored file changes.
"""

import asyncio
import logging
import math
import os
import re
import threading
import time
import unicodedata
from array import array
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from helpers import catalog_mirror, catalog_semantic, env_config, portal_text
from helpers.logging import MAIN_LOGGER_NAME

logger = logging.getLogger(MAIN_LOGGER_NAME)

DATASETS_FILE = "dataset.csv"
# Exports are regenerated weekly. Past this age the mirror is probably no longer
# being refreshed, and the portal's live search is the better answer.
MAX_EXPORT_AGE = timedelta(days=21)

# How much a matching word counts, by where it was found
_FIELD_WEIGHTS = (4.0, 2.5, 2.0, 1.0)
_TITLE, _TAGS, _ORGANIZATION, _DESCRIPTION = range(4)
# A dataset is kept when the words it matches carry at least this share of the query
_MIN_COVERAGE = 0.5
# Words found in more than this share of datasets say nothing about relevance
_MAX_DOCUMENT_SHARE = 0.5
_PHRASE_BONUS = 1.5
# Up to this much extra when the query words make up the whole title
_TITLE_FOCUS_BONUS = 0.5
_DESCRIPTION_PREVIEW = 200
# Reciprocal rank fusion constant: higher values flatten the advantage of top ranks
_FUSION_K = 60
# How much the semantic ranking counts next to the keyword ranking
_SEMANTIC_WEIGHT = 2.0

_SORT_FIELDS = {
    "created": "created_at",
    "last_update": "last_modified",
    "reuses": "reuses",
    "followers": "followers",
    "views": "views",
}
_UPDATE_RANGES = {
    "last_30_days": timedelta(days=30),
    "last_12_months": timedelta(days=365),
    "last_3_years": timedelta(days=3 * 365),
}

_WORD = re.compile(r"\w+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class _Dataset:
    id: str
    title: str
    slug: str
    organization: str
    description_short: str
    tags: tuple[str, ...]
    resources_count: int
    created_at: str
    last_modified: str
    reuses: int
    followers: int
    views: int
    title_words: tuple[str, ...]


@dataclass(frozen=True)
class _Index:
    datasets: list[_Dataset]
    # One inverted index per field: word -> positions in `datasets`
    postings: tuple[dict[str, array], ...]
    document_count: dict[str, int]
    position_by_id: dict[str, int]
    exported_at: str | None


_lock = threading.Lock()
# (file path, modification time) -> index, for the file last loaded
_loaded: tuple[tuple[str, int], _Index] | None = None


def _fold(text: str) -> str:
    """Lower-case and strip accents, so "Águeda" and "agueda" compare equal."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def tokenize(text: str) -> list[str]:
    """Words of a text, folded, with a plural "s" dropped ("escolas" -> "escola")."""
    return [
        word[:-1] if len(word) > 3 and word.endswith("s") else word
        for word in _WORD.findall(_fold(text))
    ]


def _to_int(value: str | None) -> int:
    try:
        return int(float(value or 0))
    except ValueError:
        return 0


def _preview(description: str) -> str:
    return _WHITESPACE.sub(" ", description).strip()[:_DESCRIPTION_PREVIEW]


def _datasets_path() -> Path:
    return catalog_mirror.catalog_dir() / DATASETS_FILE


def _exported_at(directory: Path) -> str | None:
    state = catalog_mirror.read_state(directory)
    entry = (state.get("files") or {}).get("dataset") or {}
    value = entry.get("last_modified")
    return str(value) if value else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def is_available() -> bool:
    """
    Whether dataset searches can be answered from the local catalog copy.

    Needs a mirrored dataset export for the current portal that is not older
    than MAX_EXPORT_AGE. UDATA_CATALOG_SEARCH=0 turns local search off.
    """
    if os.getenv("UDATA_CATALOG_SEARCH", "").strip().lower() in {"0", "false", "no"}:
        return False
    try:
        path = _datasets_path()
        if not path.is_file():
            return False
        exported = _parse_date(_exported_at(path.parent))
        if exported is None:
            exported = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except (OSError, KeyError):
        return False
    return datetime.now(UTC) - exported <= MAX_EXPORT_AGE


def _build_index(path: Path) -> _Index:
    started = time.monotonic()
    datasets: list[_Dataset] = []
    postings: tuple[dict[str, array], ...] = ({}, {}, {}, {})
    document_count: dict[str, int] = {}

    for row in catalog_mirror.iter_dataset_rows(path):
        title = row.get("title") or ""
        tags = tuple(tag for tag in (row.get("tags") or "").split(",") if tag)
        description = row.get("description") or ""
        position = len(datasets)
        datasets.append(
            _Dataset(
                id=row["id"],
                title=title,
                slug=row.get("slug") or "",
                organization=row.get("organization") or "",
                description_short=_preview(row.get("description_short") or description),
                tags=tags,
                resources_count=_to_int(row.get("resources_count")),
                created_at=row.get("created_at") or "",
                last_modified=row.get("last_modified") or "",
                reuses=_to_int(row.get("metric.reuses")),
                followers=_to_int(row.get("metric.followers")),
                views=_to_int(row.get("metric.views")),
                title_words=tuple(tokenize(title)),
            )
        )
        # Each word is indexed once per dataset, under its strongest field
        fields = (
            f"{title} {row.get('acronym') or ''}",
            " ".join(tags),
            row.get("organization") or "",
            description,
        )
        seen: set[str] = set()
        for field_index, text in enumerate(fields):
            for word in tokenize(text):
                if word in seen:
                    continue
                seen.add(word)
                postings[field_index].setdefault(word, array("I")).append(position)
                document_count[word] = document_count.get(word, 0) + 1

    logger.info(
        f"Catalog search: indexed {len(datasets)} datasets from {path} "
        f"in {time.monotonic() - started:.1f}s"
    )
    return _Index(
        datasets,
        postings,
        document_count,
        {dataset.id: position for position, dataset in enumerate(datasets)},
        _exported_at(path.parent),
    )


def _get_index() -> _Index:
    global _loaded
    path = _datasets_path()
    key = (str(path), path.stat().st_mtime_ns)
    with _lock:
        if _loaded is None or _loaded[0] != key:
            _loaded = (key, _build_index(path))
        return _loaded[1]


def clear_index() -> None:
    """Forget the loaded index. Useful for testing."""
    global _loaded
    with _lock:
        _loaded = None


def _query_terms(index: _Index, query: str) -> list[str]:
    """
    The query words worth scoring: known to the catalog, minus generic words
    ("dados", "ficheiro") and very common ones ("de"), unless nothing else is left.
    """
    total = len(index.datasets)
    words = list(dict.fromkeys(tokenize(query)))
    known = [word for word in words if word in index.document_count]
    generic = {word for stop in portal_text.stop_words() for word in tokenize(stop)}
    specific = [
        word
        for word in known
        if word not in generic
        and index.document_count[word] / total <= _MAX_DOCUMENT_SHARE
    ]
    return specific or known


def _rank(index: _Index, query: str) -> list[int]:
    """Positions of the matching datasets, best match first."""
    total = len(index.datasets)
    terms = _query_terms(index, query) if total else []
    if not terms:
        return []
    rarity = {word: math.log(1 + total / index.document_count[word]) for word in terms}
    query_weight = sum(rarity.values())

    scores: dict[int, float] = {}
    covered: dict[int, float] = {}
    for word in terms:
        for field_index, weight in enumerate(_FIELD_WEIGHTS):
            for position in index.postings[field_index].get(word, ()):
                scores[position] = scores.get(position, 0.0) + rarity[word] * weight
                covered[position] = covered.get(position, 0.0) + rarity[word]

    phrase = f" {' '.join(terms)} " if len(terms) > 1 else ""
    wanted = set(terms)
    ranked: list[tuple[float, int, int]] = []
    for position, weight in covered.items():
        coverage = weight / query_weight
        if coverage < _MIN_COVERAGE:
            continue
        dataset = index.datasets[position]
        # Missing words cost more than proportionally, so full matches lead
        score = scores[position] * coverage * coverage
        title_words = dataset.title_words
        if title_words:
            in_title = sum(1 for word in title_words if word in wanted)
            score *= 1 + _TITLE_FOCUS_BONUS * in_title / len(title_words)
            if phrase and phrase in f" {' '.join(title_words)} ":
                score *= _PHRASE_BONUS
        ranked.append((score, dataset.views, position))
    ranked.sort(reverse=True)
    return [position for *_, position in ranked]


def _fuse(keyword: list[int], semantic: list[int]) -> list[int]:
    """
    Merge two rankings (reciprocal rank fusion): a dataset scores by its rank
    in each list, so one that both rankings like beats one that only a single
    ranking puts first, and datasets found by only one of them still appear.
    """
    scores: dict[int, float] = {}
    for ranking, weight in ((keyword, 1.0), (semantic, _SEMANTIC_WEIGHT)):
        for rank, position in enumerate(ranking):
            scores[position] = scores.get(position, 0.0) + weight / (_FUSION_K + rank)
    return sorted(scores, key=lambda position: scores[position], reverse=True)


def _search(
    query: str,
    page: int,
    page_size: int,
    sort: str | None,
    last_update_range: str | None,
    semantic_ids: list[str] | None = None,
) -> dict[str, Any]:
    index = _get_index()
    positions = _rank(index, query)
    if semantic_ids:
        positions = _fuse(
            positions,
            [
                index.position_by_id[dataset_id]
                for dataset_id in semantic_ids
                if dataset_id in index.position_by_id
            ],
        )

    if last_update_range:
        if last_update_range not in _UPDATE_RANGES:
            raise ValueError(f"Unsupported last_update_range: {last_update_range}")
        earliest = (datetime.now(UTC) - _UPDATE_RANGES[last_update_range]).isoformat()
        positions = [
            position
            for position in positions
            # ISO dates compare correctly as text (the export has no time zone)
            if index.datasets[position].last_modified >= earliest[:19]
        ]
    if sort:
        field_name = _SORT_FIELDS.get(sort.lstrip("-"))
        if field_name is None:
            raise ValueError(f"Unsupported sort: {sort}")
        positions.sort(
            key=lambda position: getattr(index.datasets[position], field_name),
            reverse=sort.startswith("-"),
        )

    page = max(page, 1)
    page_size = max(1, min(page_size, 100))
    start = (page - 1) * page_size
    site_url = env_config.get_base_url("site")
    results = [
        {
            "id": dataset.id,
            "title": dataset.title,
            "description_short": dataset.description_short,
            "slug": dataset.slug,
            "organization": dataset.organization or None,
            "tags": list(dataset.tags),
            "resources_count": dataset.resources_count,
            "url": f"{site_url}datasets/{dataset.slug or dataset.id}",
        }
        for dataset in (
            index.datasets[position]
            for position in positions[start : start + page_size]
        )
    ]
    return {
        "data": results,
        "page": page,
        "page_size": len(results),
        "total": len(positions),
        "exported_at": index.exported_at,
        "ranking": "hybrid" if semantic_ids else "keyword",
    }


async def search_datasets(
    query: str,
    page: int = 1,
    page_size: int = 20,
    sort: str | None = None,
    last_update_range: str | None = None,
    semantic: bool | None = None,
) -> dict[str, Any]:
    """
    Search the local catalog copy. Same arguments and result shape as
    datagouv_api_client.search_datasets, plus 'exported_at' (date of the export)
    and 'ranking' ("hybrid" when the semantic ranking took part, else "keyword").

    `semantic` forces the semantic ranking on or off; by default it is used
    when available (see helpers/catalog_semantic.py), and a failure there
    quietly leaves the keyword ranking alone.

    Raises:
        OSError: the mirrored file cannot be read.
        ValueError: unsupported sort or last_update_range.
    """
    semantic_ids: list[str] | None = None
    if semantic if semantic is not None else catalog_semantic.is_available():
        try:
            semantic_ids = [
                dataset_id for dataset_id, _ in await catalog_semantic.rank(query)
            ]
        except Exception as e:
            if semantic:
                raise
            logger.warning(f"Semantic ranking failed, using keywords only: {e}")
    return await asyncio.to_thread(
        _search, query, page, page_size, sort, last_update_range, semantic_ids
    )
