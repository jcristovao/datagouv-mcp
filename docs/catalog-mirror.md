# Local copy of the catalog

udata portals can publish their own catalog as a dataset of CSV files, one per kind of record, regenerated on a
schedule. `helpers/catalog_mirror.py` keeps a local copy of those files and only downloads the ones that changed.

On dados.gov.pt the dataset is
[Catálogo de dados dos dados.gov.pt](https://dados.gov.pt/datasets/catalogo-de-dados-dos-dados-gov-pt), regenerated
weekly (Sunday around 02:00 UTC):

| File | Content | Size (October 2026) |
|---|---|---|
| `dataset.csv` | One row per dataset: title, description, organization, tags, licence, dates, metrics | 30 MB, 21k datasets |
| `resource.csv` | One row per file: dataset, title, URL, format, size, checksum | 42 MB, 65k files |
| `organization.csv` | Publishers | 0.8 MB |
| `tag.csv`, `reuse.csv`, `discussion.csv`, `dataservice.csv`, `harvest.csv` | The portal's other records | under 0.2 MB each |

The files use `;` as separator, UTF-8, and quoted fields that can span several lines (read them with a CSV
parser, not line by line).

The mirror holds the catalog (metadata) only. The data files themselves stay with their publishers and are
fetched on demand, see [Local queries](local-queries.md).

The server uses the mirror for `search_datasets`, see [Local search](#local-search) below. The other files are
kept for future use.

## Settings

| Variable | Meaning | Default |
|---|---|---|
| `UDATA_CATALOG_DATASET` | ID or slug of the portal's catalog export dataset. Required | unset |
| `UDATA_CATALOG_DIR` | Where the files go | `~/.local/share/datagouv-mcp/catalog/<portal host>` |
| `UDATA_CATALOG_SEARCH` | `0` makes `search_datasets` ignore the mirror and always use the portal's API | on when a mirror exists |

`UDATA_API_URL` selects the portal, as for the server. For dados.gov.pt the dataset ID is
`67c5a3b3b50fe67ba7aa1905`.

## Run it

```bash
UDATA_API_URL=https://dados.gov.pt/api/ UDATA_CATALOG_DATASET=67c5a3b3b50fe67ba7aa1905 \
  uv run python -m helpers.catalog_mirror
```

```
Catalog mirror: /home/you/.local/share/datagouv-mcp/catalog/dados.gov.pt
  updated: dataset, resource
  unchanged: dataservice, discussion, harvest, organization, reuse, tag
```

`--force` downloads every file again. The exit code is 1 if the check or any download failed.

## How a run works

1. One request reads the catalog dataset, which lists each export file with its URL, modification date, size
   and checksum.
2. That is compared with `state.json`, written by the previous run. A file is downloaded when any of those four
   values changed, or when the local file is missing.
3. A download is written to a `.part` file and only replaces the previous copy once its size and checksum match
   the catalog. A failed download leaves the previous copy in place and is tried again on the next run.

On a day with nothing new, a run is one request and takes under a second.

## Daily check (Linux, systemd)

[`scripts/systemd/dados-gov-pt-catalog.service`](../scripts/systemd/dados-gov-pt-catalog.service) and
[`.timer`](../scripts/systemd/dados-gov-pt-catalog.timer) run the check every day at 07:30, and catch up after
the machine was off.

```bash
cp scripts/systemd/dados-gov-pt-catalog.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now dados-gov-pt-catalog.timer
```

| | |
|---|---|
| Run it now | `systemctl --user start dados-gov-pt-catalog.service` |
| Last runs | `journalctl --user -u dados-gov-pt-catalog.service -n 20` |
| Next run | `systemctl --user list-timers dados-gov-pt-catalog.timer` |

A failed run raises a desktop notification through the same unit the weekly sync uses
(`datagouv-mcp-sync-notify-failure@.service`, see [Fork and weekly sync](fork-and-sync.md)).

## Local search

When a mirror exists for the configured portal, `search_datasets` answers from `dataset.csv` instead of calling
the portal. No setting is needed on the server: it looks in the same directory the mirror writes to.

The portal's search only returns datasets that contain every word of the query, so "dados de contratos
públicos" finds 1 dataset and "ficheiro com as escolas de Águeda" finds none. The local search ranks instead:

- Accents, case and a plural "s" are ignored: "ESCOLA agueda" matches "Escolas" from "Câmara Municipal de Águeda".
- A rare word weighs more than a common one. Words found in more than half of the datasets ("de"), and the
  generic words of the portal's language ("dados", "ficheiro", see `UDATA_LANGUAGE`), are ignored unless the
  query has nothing else.
- A match in the title counts 4, in the tags 2.5, in the organization name 2, in the description 1.
- A dataset is listed when the words it matches carry at least half the weight of the query. Missing words lower
  its rank, so datasets matching everything come first.
- Titles that contain the query as a phrase, or little else than the query words, get a bonus. Ties go to the
  most viewed dataset.
- Archived datasets are left out, as in the portal's search.

`sort` and `last_update_range` work as with the API. The output says where the answer came from:

```
Found 34 dataset(s) for query: 'ficheiro com as escolas de Águeda'
Source: local copy of the catalog (exported 2026-10-04), ranked by relevance
```

The API is still used in three cases:

- The local search finds nothing. The export is weekly, so a dataset published since then is only in the API.
- The export is more than 21 days old, which means the mirror is no longer being refreshed.
- Reading the mirror fails.

The index is built in memory on the first search after a start (1.4 s for the 21k datasets of dados.gov.pt,
about 80 MB of memory), and rebuilt when the daily check replaces the file. Searches then take a few
milliseconds. The tool's description changes with the mode: it only warns about "AND logic" when the API is in
use. It is set when the server starts, so restart the server after creating the first mirror.

Only `search_datasets` uses the mirror. Organization and dataservice searches, dataset details and resource
lists still come from the API, so they are always current.

The code is in [`helpers/catalog_mirror.py`](../helpers/catalog_mirror.py) and
[`helpers/catalog_search.py`](../helpers/catalog_search.py), with tests in
[`tests/test_catalog_mirror.py`](../tests/test_catalog_mirror.py) and
[`tests/test_catalog_search.py`](../tests/test_catalog_search.py).
