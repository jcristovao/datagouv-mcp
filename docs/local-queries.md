# Local queries

`query_resource_data` lets an assistant read rows from a CSV or spreadsheet, with a filter, a sort and paging.
On data.gouv.fr this is done by a separate service, the Tabular API. Portals without one get a fallback: the
MCP server downloads the file and does the work itself. The tool's parameters and output are the same either
way.

## What it supports

| | |
|---|---|
| Formats | CSV, TSV, XLSX (first sheet) |
| Not supported | Old `.xls`, `.ods`, compressed files (`.zip`, `.csv.gz`), JSON |
| Size limit | 25 MB by default, set with `LOCAL_QUERY_MAX_BYTES` (`0` disables the fallback) |
| Encodings | UTF-8 with or without BOM, then cp1252, then latin-1 |
| Separators | Detected among `,` `;` tab and `|` |

For an unsupported format or an oversized file, the tool says so and tells the assistant to fetch the file URL
directly. `get_resource_info` gives the same verdict before any download.

## Filters and sorting

| Operator | Meaning |
|---|---|
| `exact` | Equal (as text, or as numbers when both sides are numbers) |
| `contains` | Text contains the value, ignoring case |
| `less`, `greater` | ≤ and ≥ |
| `strictly_less`, `strictly_greater` | < and > |

- Comparisons are numeric when both the cell and the value are numbers. Decimal commas (`10,5`) are understood.
  Otherwise they compare as text, which gives the right order for ISO dates (`2026-03-01`).
- Empty cells never match a comparison.
- Sorting is numeric when every non-empty value in the column is a number. Otherwise it is alphabetical,
  ignoring case and accents, so "Évora" sorts next to "Evora". Empty cells go last.
- An unknown column name returns an error listing the columns that exist.

## How it works

1. The file's URL, format and size come from the portal's catalog.
2. The file is downloaded through the same guarded HTTP client upstream uses for producer-supplied URLs, which
   refuses private and local addresses. The size limit is checked against the catalog size, the
   `Content-Length` header, and again while the download streams.
3. The file is saved in an on-disk cache (see below), and the last 4 files used are also kept in memory for
   10 minutes, so paging through a file reads neither the network nor the disk.
4. Each query parses the cached file. Without a sort, only the requested page is held in memory.

## The file cache

Downloaded files are kept on disk, so a later query on the same file is answered without downloading it again,
including after a server restart.

| Variable | Meaning | Default |
|---|---|---|
| `LOCAL_QUERY_CACHE_DIR` | Where the files are kept | `~/.cache/datagouv-mcp/resources` |
| `LOCAL_QUERY_CACHE_MAX_BYTES` | Total size the cache may reach. `0` turns the on-disk cache off | `1073741824` (1 GB) |
| `LOCAL_QUERY_CACHE_REVALIDATE_SECONDS` | How long a copy is used before asking the source whether it changed | `86400` (24 hours) |

**Size limit.** After each download, if the cache is over its limit, the files that have gone longest without
being accessed are deleted until it fits. Every query on a file counts as an access. With the default limits
(25 MB per file, 1 GB in total) the cache holds at least 40 files.

**Freshness.** A cached copy is used as is while both of these hold:

- the catalog still reports the same modification date and checksum for the file as when it was downloaded
  (the tool reads the catalog entry on every query anyway, so this costs nothing);
- the copy was downloaded or confirmed less than 24 hours ago.

When the catalog entry changed, the file is downloaded again. When only the 24 hours have passed, the server
sends a conditional request (`If-None-Match` / `If-Modified-Since`): a publisher's server that supports it
answers "not modified" without sending the file, and the copy is good for another 24 hours. So a file can be up
to 24 hours behind its source if the publisher replaces it without the catalog noticing.

Each file is stored as `<hash of the URL>.data` with a `.json` file beside it recording the URL, the resource
ID and the dates. Deleting the directory is safe: files are downloaded again when next needed.

## Known limits

- Numbers written with thousands separators (`1.234,56` or `1,234.56`) are compared as text.
- Every query re-parses the file. That is quick at 25 MB, but it is the reason not to raise the limit much.
- Only the first sheet of a workbook is read.
- Blank or repeated column headers are renamed (`column_2`, `name_2`).

The code is in [`helpers/local_tabular_client.py`](../helpers/local_tabular_client.py) and
[`helpers/file_cache.py`](../helpers/file_cache.py), with tests in
[`tests/test_local_tabular.py`](../tests/test_local_tabular.py) and
[`tests/test_file_cache.py`](../tests/test_file_cache.py).
