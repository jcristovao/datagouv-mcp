# Other udata instances

By default the server talks to data.gouv.fr, exactly like upstream. Setting `UDATA_API_URL` points it at
another udata portal instead.

## Settings

| Variable | Purpose | Default |
|---|---|---|
| `UDATA_API_URL` | Base URL of the portal's API, e.g. `https://dados.gov.pt/api/`. Setting it switches the server to that portal and overrides `DATAGOUV_API_ENV` | unset (data.gouv.fr) |
| `UDATA_SITE_URL` | Public site URL, used for links in results | `UDATA_API_URL` without the trailing `api/` |
| `UDATA_PORTAL_NAME` | Portal name shown in tool descriptions | Host name of the site, without `www.` |
| `UDATA_LANGUAGE` | `fr`, `pt` or `en`. Picks the filler words dropped from searches and the examples in tool descriptions | `fr` on data.gouv.fr, `en` on other portals |
| `TABULAR_API_URL` | The portal's Tabular API, if it has one | unset |
| `METRICS_API_URL` | The portal's Metrics API, if it has one | unset |
| `CRAWLER_API_URL` | The portal's crawler service, if it has one | unset |
| `MCP_ALLOWED_HOSTS` | Extra host names the server answers to, comma-separated, e.g. `mcp.example.pt`. Needed to host it publicly; see [Hosting](hosting.md) | unset (data.gouv.fr names and localhost only) |
| `LOCAL_QUERY_MAX_BYTES` | Largest file `query_resource_data` may download when there is no Tabular API. `0` turns the fallback off | `26214400` (25 MB) |

A missing trailing slash is added, and blank values count as unset. All of these can also go in `.env`
(see [`.env.example`](../.env.example)) or be passed through `docker compose`.

The three satellite services are separate applications that data.gouv.fr runs next to udata. Most other
portals don't have them, which is why they are optional here.

## How the tools adapt

| Situation | Effect |
|---|---|
| No Metrics API | `get_metrics` is not offered |
| No Tabular API, local queries on (default) | `query_resource_data` downloads the file and queries it in the server |
| No Tabular API, `LOCAL_QUERY_MAX_BYTES=0` | `query_resource_data` is not offered, and other tools stop pointing to it |
| No Crawler | `get_resource_info` skips the "large file exception" lookup |

Tool descriptions are what the AI assistant reads to decide which tool to call, so they change with the
configuration: they name the configured portal, use examples in the configured language, and only mention tools
that exist.

With the default configuration, the full tool listing (names, descriptions, input schemas) is byte-identical to
upstream's. The [weekly sync](fork-and-sync.md) re-checks this on every merge.

## Why the language matters for search

udata's search requires every word of the query to match. People, and AI assistants, tend to add generic words
that aren't in dataset titles. Measured on dados.gov.pt on 2026-10-09:

| Query | Results |
|---|---|
| `contratos públicos` | 17 |
| `dados contratos públicos` | 1 |
| `ficheiro contratos públicos` | 0 |

With `UDATA_LANGUAGE=pt` the server removes words like "dados", "ficheiro", "arquivo" and "tabela" (plus format
names such as "csv") before searching, so the second query also returns 17. If the cleaned query finds nothing,
the original is tried.

## Adding a language

Add an entry to `_LANGUAGES` in [`helpers/portal_text.py`](../helpers/portal_text.py): the filler words, a pair
of example words, and an example organization acronym.

## Where the code lives

| File | Role |
|---|---|
| `helpers/env_config.py` | URLs per environment, `is_available()`, portal name |
| `helpers/portal_text.py` | Language data and the `describe()` decorator that fills description placeholders |
| `helpers/local_tabular_client.py` | Local query fallback |
| `helpers/transport_security.py` | Accepted host names, including `MCP_ALLOWED_HOSTS` |
| `tools/__init__.py` | Registers only the tools the portal can support |
| `tests/test_env_config.py`, `test_custom_instance.py`, `test_portal_text.py`, `test_local_tabular.py` | Tests for the above |
