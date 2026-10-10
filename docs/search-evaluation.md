# Evaluating dataset search

[`scripts/eval/search_cases.json`](../scripts/eval/search_cases.json) holds 35 questions about dados.gov.pt, each
with the search query an assistant would plausibly send first and the dataset(s) that should come up.
[`scripts/eval/search_eval.py`](../scripts/eval/search_eval.py) scores a search engine against them.

```bash
UDATA_API_URL=https://dados.gov.pt/api/ UDATA_LANGUAGE=pt \
  uv run python scripts/eval/search_eval.py --verbose
```

| Option | |
|---|---|
| `--engine local` | What `search_datasets` uses (default): keywords, merged with the [semantic ranking](semantic-search.md) when `EMBEDDINGS_API_URL` is set and the vectors are built |
| `--engine keyword` | The keyword ranking alone |
| `--engine semantic` | The semantic ranking alone |
| `--engine api` | The portal's own search: one request per case, one second apart |
| `--verbose` | Show the top three results of every case that did not rank first |
| `--json FILE` | Write the outcome of every case to a file, to compare two runs |

## The cases

| Category | Cases | What it tests |
|---|---|---|
| `control` | 4 | Queries that use the words of the dataset title |
| `noise` | 3 | The right words buried among generic ones ("ficheiro com a lista das...") |
| `spelling` | 4 | Missing accents, typos, old spellings, an acronym |
| `synonym` | 11 | Another word for the same thing ("terramotos" for "sismos", "fármacos" for "medicamentos") |
| `vague` | 7 | A question with no word in common with the title ("mortes de bebés" for "mortalidade infantil") |
| `english` | 6 | English queries against Portuguese datasets |

An expected dataset is a pattern matched against the title (and optionally the organization), ignoring accents
and case, because the catalog holds many near-identical datasets and their IDs change when a publisher
republishes. The score is the rank of the first expected dataset in the top 10.

The queries were written once, before scoring. Do not reword a query to make it pass: fix the search, or fix
the expectation if another dataset is a correct answer too (two were widened that way after the first run).

## Results (export of 2026-10-04, embeddings from bge-m3)

Share of cases with an expected dataset in the top 10:

| Category | Cases | Keyword | Semantic | Both merged |
|---|---|---|---|---|
| control | 4 | 100% | 100% | 100% |
| noise | 3 | 100% | 100% | 100% |
| spelling | 4 | 100% | 100% | 100% |
| synonym | 11 | 45% | 73% | 82% |
| vague | 7 | 43% | 71% | 57% |
| english | 6 | 17% | 83% | 83% |
| **All** | **35** | **57%** | **83%** | **83%** |

All 35 cases:

| | Keyword | Semantic | Both merged |
|---|---|---|---|
| First result | 37% | 57% | 54% |
| Top 5 | 43% | 71% | 80% |
| Top 10 | 57% | 83% | 83% |

Still failing with both: "perigo de terramotos" (for "Risco de Sismos"), "linhas de comboio mapa", "em que
gasta o Estado o dinheiro", "mortes de bebés" (for "mortalidade infantil") and "number of farms Portugal".

## Adding an engine

An engine is an async function taking `(query, limit)` and returning dataset dicts with `title` and
`organization`. Add it to `ENGINES` in `search_eval.py` and select it with `--engine`.
