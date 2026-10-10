"""
Score a dataset search engine against a fixed list of questions.

Usage:
    python scripts/eval/search_eval.py [--engine NAME] [--cases FILE] [--top 10] [--verbose]

Each case names the dataset(s) that should come up for a query. The score is
where the first expected dataset lands in the results (hit@1, hit@5, hit@10,
mean reciprocal rank), overall and per category of difficulty.

Engines are functions taking (query, limit) and returning dataset dicts with
at least "title" and "organization". Add one to ENGINES to compare it.

Engines:
    local     what search_datasets uses: keywords, merged with the semantic
              ranking when it is available (see docs/semantic-search.md)
    keyword   the keyword ranking alone
    semantic  the semantic ranking alone
    api       the portal's own search (one request per case, with a pause)

All but "api" need the catalog mirror (see docs/catalog-mirror.md).
"""

import argparse
import asyncio
import json
import re
import sys
import unicodedata
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from helpers import (
    catalog_mirror,
    catalog_search,
    catalog_semantic,
    datagouv_api_client,
)

DEFAULT_CASES = Path(__file__).with_name("search_cases.json")
_API_PAUSE_SECONDS = 1.0

Engine = Callable[[str, int], Awaitable[list[dict[str, Any]]]]


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


async def local_engine(query: str, limit: int) -> list[dict[str, Any]]:
    result = await catalog_search.search_datasets(query, page_size=limit)
    return result["data"]


async def keyword_engine(query: str, limit: int) -> list[dict[str, Any]]:
    result = await catalog_search.search_datasets(
        query, page_size=limit, semantic=False
    )
    return result["data"]


_rows_by_id: dict[str, dict[str, str]] = {}


async def semantic_engine(query: str, limit: int) -> list[dict[str, Any]]:
    if not _rows_by_id:
        path = catalog_mirror.catalog_dir() / catalog_search.DATASETS_FILE
        _rows_by_id.update(
            {row["id"]: row for row in catalog_mirror.iter_dataset_rows(path)}
        )
    ranked = await catalog_semantic.rank(query, limit=limit)
    return [_rows_by_id[dataset_id] for dataset_id, _ in ranked]


async def api_engine(query: str, limit: int) -> list[dict[str, Any]]:
    await asyncio.sleep(_API_PAUSE_SECONDS)
    result = await datagouv_api_client.search_datasets(query, page_size=limit)
    return result["data"]


ENGINES: dict[str, Engine] = {
    "local": local_engine,
    "keyword": keyword_engine,
    "semantic": semantic_engine,
    "api": api_engine,
}


def is_expected(dataset: dict[str, Any], expect: dict[str, str]) -> bool:
    if not re.search(expect["title"], fold(dataset.get("title") or "")):
        return False
    organization = expect.get("organization")
    return not organization or bool(
        re.search(organization, fold(dataset.get("organization") or ""))
    )


def first_hit(results: list[dict[str, Any]], expect: dict[str, str]) -> int | None:
    """Rank (1-based) of the first expected dataset, or None."""
    for rank, dataset in enumerate(results, 1):
        if is_expected(dataset, expect):
            return rank
    return None


def summarize(ranks: list[int | None]) -> dict[str, float]:
    count = len(ranks) or 1
    return {
        "cases": len(ranks),
        "hit@1": sum(1 for rank in ranks if rank == 1) / count,
        "hit@5": sum(1 for rank in ranks if rank and rank <= 5) / count,
        "hit@10": sum(1 for rank in ranks if rank and rank <= 10) / count,
        "mrr": sum(1 / rank for rank in ranks if rank) / count,
    }


async def run(engine: Engine, cases: list[dict[str, Any]], top: int) -> list[dict]:
    outcomes = []
    for case in cases:
        results = await engine(case["query"], top)
        outcomes.append(
            {
                **case,
                "rank": first_hit(results, case["expect"]),
                "top": [dataset.get("title") or "" for dataset in results[:3]],
            }
        )
    return outcomes


def report(outcomes: list[dict], top: int, verbose: bool) -> None:
    print(f"{'rank':>5}  {'category':<9} {'case':<26} query")
    for outcome in outcomes:
        rank = outcome["rank"]
        shown = str(rank) if rank else f">{top}"
        print(
            f"{shown:>5}  {outcome['category']:<9} {outcome['id']:<26} "
            f"{outcome['query']}"
        )
        if verbose and rank != 1:
            for title in outcome["top"]:
                print(f"{'':>17}got: {title[:90]}")

    print(f"\n{'':<10}{'cases':>6}{'hit@1':>8}{'hit@5':>8}{'hit@10':>8}{'MRR':>8}")
    categories = list(dict.fromkeys(outcome["category"] for outcome in outcomes))
    for name in [*categories, "ALL"]:
        ranks = [
            outcome["rank"]
            for outcome in outcomes
            if name == "ALL" or outcome["category"] == name
        ]
        stats = summarize(ranks)
        print(
            f"{name:<10}{stats['cases']:>6.0f}{stats['hit@1']:>8.0%}"
            f"{stats['hit@5']:>8.0%}{stats['hit@10']:>8.0%}{stats['mrr']:>8.2f}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--engine", choices=sorted(ENGINES), default="local")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--top", type=int, default=10, help="results to look at")
    parser.add_argument("--verbose", action="store_true", help="show what came up")
    parser.add_argument("--json", type=Path, help="also write the outcomes here")
    args = parser.parse_args(argv)

    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    outcomes = asyncio.run(run(ENGINES[args.engine], cases, args.top))
    report(outcomes, args.top, args.verbose)
    if args.json:
        args.json.write_text(
            json.dumps(outcomes, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
