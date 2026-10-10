# Semantic search

Optional addition to the [local dataset search](catalog-mirror.md#local-search). Every dataset of the catalog
copy gets an embedding (a vector that places texts with similar meaning close together), and so does each
query. Datasets close to the query are found even when they share no word with it: "bus stops Coimbra" finds
"Paragens Autocarros", "gastos dos hospitais com fármacos" finds "Despesa com Medicamentos nos Hospitais do SNS".

It does not replace the keyword ranking. Both rankings are computed and merged, because keywords remain better
for names, acronyms and exact titles.

## What it needs

| | |
|---|---|
| The catalog copy | See [Local copy of the catalog](catalog-mirror.md) |
| An embeddings endpoint | Anything that speaks the OpenAI `/v1/embeddings` protocol: llama.cpp, Ollama, vLLM, or a hosted API |
| The dataset vectors | Built with `python -m helpers.catalog_semantic` |

Without any of these, search works as before, on keywords only.

## Settings

| Variable | Meaning | Default |
|---|---|---|
| `EMBEDDINGS_API_URL` | Base URL of the endpoint, e.g. `http://127.0.0.1:8918/v1`. Unset turns semantic search off | unset |
| `EMBEDDINGS_MODEL` | Model name sent to the endpoint | `bge-m3` |
| `EMBEDDINGS_API_KEY` | Sent as a bearer token, for hosted endpoints | unset |
| `UDATA_SEMANTIC_SEARCH` | `0` turns semantic search off even when everything is in place | on when available |

Use a multilingual model: the catalog is in Portuguese and queries may not be.

## Set it up (this machine)

The embeddings come from `bge-m3` (multilingual, 1024 dimensions, 635 MB) on a dedicated llama.cpp server:
[`scripts/systemd/datagouv-mcp-embeddings.service`](../scripts/systemd/datagouv-mcp-embeddings.service), on
`127.0.0.1:8918`. It is separate from llama-swap and from `~/bin/llama-bge-m3`, so it does not swap other models
out or collide with them.

```bash
cp scripts/systemd/datagouv-mcp-embeddings.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now datagouv-mcp-embeddings.service

UDATA_API_URL=https://dados.gov.pt/api/ EMBEDDINGS_API_URL=http://127.0.0.1:8918/v1 \
  uv run python -m helpers.catalog_semantic
```

The first build embeds all 21k datasets, which took 4 minutes here. Later builds only embed datasets that are
new or whose text changed, and take under a second when nothing did. The daily catalog job
(`dados-gov-pt-catalog.service`) runs the build after refreshing the mirror, and the MCP service has
`EMBEDDINGS_API_URL` set.

## How it works

- **What is embedded:** the dataset's title, organization, tags and the first 1,000 characters of its
  description. The vectors are stored next to the mirror in `dataset.embeddings.npz` (48 MB), with a hash of each
  text so unchanged datasets are not embedded again. Changing `EMBEDDINGS_MODEL` rebuilds everything.
- **At search time:** the query is embedded (one call to the endpoint, 5-second limit, the last 256 queries are
  remembered) and compared with every dataset vector. The 50 closest datasets with a similarity of at least
  0.45 form the semantic ranking.
- **Merging:** each dataset scores `1 / (60 + rank)` in the keyword ranking plus twice that in the semantic
  ranking (reciprocal rank fusion). A dataset both rankings like comes first, and a dataset only one of them
  found still appears.
- **When it fails:** if the endpoint is down or slow, the search logs a warning and returns the keyword ranking.
  The output line says which ranking was used ("ranked by keywords and meaning" or "ranked by relevance").
- **Memory:** the vectors take about 90 MB in the MCP server once loaded.

## Does it help?

Measured on the 35 questions of the [search evaluation](search-evaluation.md): the right dataset is in the top
10 for 83% of them, up from 57% with keywords alone, and in the top 5 for 80%, up from 43%.

The semantic weight (2) was chosen after trying 1 to 5 on that same list, where anything from 1.5 up scored
about the same. The list is small and was written by the same person as the search, so read the gain as
"clearly helps" and not as a precise number.

The code is in [`helpers/catalog_semantic.py`](../helpers/catalog_semantic.py) and
[`helpers/embeddings_client.py`](../helpers/embeddings_client.py); the merge is `_fuse` in
[`helpers/catalog_search.py`](../helpers/catalog_search.py). Tests are in
[`tests/test_catalog_semantic.py`](../tests/test_catalog_semantic.py).
