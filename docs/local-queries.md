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
3. The last 4 downloaded files are kept in memory for 10 minutes, so paging through a file doesn't download it
   again.
4. Each query parses the cached file. Without a sort, only the requested page is held in memory.

## Known limits

- Numbers written with thousands separators (`1.234,56` or `1,234.56`) are compared as text.
- Every query re-parses the file. That is quick at 25 MB, but it is the reason not to raise the limit much.
- Only the first sheet of a workbook is read.
- Blank or repeated column headers are renamed (`column_2`, `name_2`).

The code is in [`helpers/local_tabular_client.py`](../helpers/local_tabular_client.py), with tests in
[`tests/test_local_tabular.py`](../tests/test_local_tabular.py).
