# Support other udata instances: dados.gov.pt prototype

Branch `udata-instances`, one local commit (`45c15b8 feat: support other udata instances`) on top of
upstream `5a37d6f`. Nothing has been pushed. This file is deliberately left out of the commit.

## What changed

**Configuration (`helpers/env_config.py`).** `_ENV_TARGETS` and the `demo`/`prod` behaviour are untouched. If
`UDATA_API_URL` is set, the server switches to a `custom` target that takes precedence over `DATAGOUV_API_ENV`:

| Variable | Purpose | Default on a custom instance |
|---|---|---|
| `UDATA_API_URL` | udata API base (`…/api/`) | none; setting it turns on custom mode |
| `UDATA_SITE_URL` | public site, used in links | `UDATA_API_URL` without the trailing `api/` |
| `UDATA_PORTAL_NAME` | name used in tool descriptions | site host without `www.` |
| `TABULAR_API_URL`, `METRICS_API_URL`, `CRAWLER_API_URL` | satellite services | unset, so the service is unavailable |

Trailing slashes are added when missing, and blank values count as unset (so docker-compose passthrough works).
New helpers:
- `is_available(api_name)`
- `get_env_name()`
- `get_portal_name()`
- `with_portal_name`, a decorator that fills `{portal}` in a tool docstring

`get_base_url()` keeps its signature. For a satellite that isn't configured it raises `ServiceUnavailableError`, a
`KeyError` subclass whose message names the env var to set.

**Graceful degradation.**
- `tools/__init__.py` only registers `query_resource_data` when a Tabular API is configured, and `get_metrics` when a
  Metrics API is. On dados.gov.pt that leaves 8 of the 10 tools.
- `get_resource_info` skips the Crawler exceptions lookup when there's no crawler. When there's no Tabular API, it
  skips the whole Tabular section and prints `Tabular API: not available on <portal> (fetch the resource URL directly)`.
- The health probe already calls only `search_datasets`, which uses the core API, so services that aren't configured
  can't fail it. I only updated its docstring to say so. `/health` reports `"data_env": "custom"` in custom mode.
- `get_metrics` gets its demo check from `env_config.get_env_name()`, so the logic now lives in one place.

**Portal-neutral wording.**
- `search_datasets`, `search_dataservices` and `search_organizations` use `{portal}` in their docstrings.
- The FastMCP server name is `f"{portal} MCP server"`.
- With the default config, the full `list_tools()` output (names, descriptions, input schemas, annotations) is
  **byte-identical** to upstream for both `prod` and `demo`. I checked this by dumping the listing as JSON before
  and after the change and comparing the files with `cmp`.

**Docs.** I updated the README (env var list and the `.env` example), `.env.example` and `docker-compose.yml`
(passthrough of the new variables). I didn't edit `CHANGELOG.md`, because upstream generates it from commit
messages in `tag_version.sh`.

## How to run against dados.gov.pt

```shell
cd ~/code/datagouv-mcp
uv sync
UDATA_API_URL=https://dados.gov.pt/api/ UDATA_PORTAL_NAME=dados.gov.pt UDATA_LANGUAGE=pt \
  MCP_HOST=127.0.0.1 MCP_PORT=8917 uv run main.py

# in another shell
MCP_PORT=8917 uv run python scripts/call_tool.py search_datasets '{"query": "contratos públicos"}'
```

Claude Code: `claude mcp add --transport http dados-gov-pt http://127.0.0.1:8917/mcp`

## Test results

- Upstream baseline: `147 passed, 2 deselected`.
- After the change: **`173 passed, 2 deselected`** (`uv run pytest`). The full suite still includes upstream's live
  tests against data.gouv.fr, and they pass.
- New tests:
  - `tests/test_env_config.py`: precedence, URL normalisation, site derivation, portal name, satellite availability,
    the `{portal}` decorator, and checks that the defaults are unchanged.
  - `tests/test_custom_instance.py`: tool registration with and without satellites, descriptions naming the
    portal, and `get_resource_info` against mocked dados.gov.pt endpoints. That last test asserts exactly 2 HTTP
    calls, so no Tabular or Crawler request is made.
- `ruff check`, `ruff format --check`, `ty check` and `pre-commit run --all-files` are all clean.

## Second commit: language-aware wording, no dangling tool references

**Search words.** `UDATA_LANGUAGE` (`fr`|`pt`|`en`) picks the stop words that `clean_search_query` strips, and the
example words in descriptions ("données"/"INSEE" → "dados"/"INE"). It defaults to `fr` on data.gouv.fr (identical
to before) and `en` on custom instances. The new module is `helpers/portal_text.py`; it replaces the
`with_portal_name` decorator with `describe(**extra)`.

Why it matters, measured on dados.gov.pt's raw search API:

| query | results |
|---|---|
| `contratos públicos` | 17 |
| `dados contratos públicos` | 1 |
| `ficheiro contratos públicos` | 0 |

With `UDATA_LANGUAGE=pt`, `search_datasets "dados contratos públicos"` now returns all 17.

**Tool references.** Without `query_resource_data`, the descriptions of `search_datasets`, `list_dataset_resources`
and `get_resource_info` now point to "fetch the resource URL directly" instead of a tool that doesn't exist. The
condition lives in one place, `tools.query_resource_data.is_available()`, so step 4 only needs to change that
function.

The default listing is still byte-identical to upstream (prod and demo). Tests: **184 passed, 2 deselected**. ty
and ruff are clean.

## Live smoke test (dados.gov.pt, 2026-10-09)

Server started with the command above. `/health`:

```
{"status": "ok", "uptime_since": "2026-10-09T19:51:20.187915+00:00", "version": "1.0.1.dev1+g5a37d6f0f", "env": "local", "data_env": "custom"}
```

`search_datasets {"query": "contratos públicos", "page_size": 5}`

```
Found 17 dataset(s) for query: 'contratos públicos'
Page 1 of results:

1. Contratos Públicos na Saúde
   ID: 649bb048078190f89ee7719d
   Organization: IMPIC - Instituto Dos Mercados Públicos, do Imobiliário e da Construção
   Tags: contratacao, contratos-publicos, eficiencia, transparencia-sns-gov-pt
   Resources: 2
   URL: https://dados.gov.pt/datasets/contratos-publicos-na-saude-2

2. Contratos Públicos na Saúde
   ID: 5b03f6dac8d8c922944afa55
   Organization: IMPIC - Instituto Dos Mercados Públicos, do Imobiliário e da Construção
   Tags: contratacao, contratos-publicos, eficiencia, transparencia-sns-gov-pt
   Resources: 4
   URL: https://dados.gov.pt/datasets/contratos-publicos-na-saude-1

3. Dataset Estrutura de Missão PRR - Contratos Públicos
   ID: 6717db451b0eaad60ac5008c
   Organization: Estrutura de Missão Recuperar Portugal
   Resources: 1
   URL: https://dados.gov.pt/datasets/dataset-estrutura-de-missao-prr-contratos-publicos

4. Dataset Estrutura de Missão PRR - Entidades - Contratos Públicos
   ID: 6718c29ebbf3654d2bc5008c
   Organization: Estrutura de Missão Recuperar Portugal
   Resources: 1
   URL: https://dados.gov.pt/datasets/dataset-estrutura-de-missao-prr-entidades-contratos-publicos

5. Lista de Códigos e Descrições de CPV (Vocabulário Comum para os Contratos Públicos)
   ID: 65f4433162ef6d83a6fa3f8a
   Tags: contratos, cpv, json, publicos, vocabulario
   Resources: 1
   URL: https://dados.gov.pt/datasets/lista-de-codigos-e-descricoes-de-cpv-vocabulario-comum-para-os-contratos-publicos
```

`get_dataset_info {"dataset_id": "5b03f6dac8d8c922944afa55"}`

```
Dataset Information: Contratos Públicos na Saúde

ID: 5b03f6dac8d8c922944afa55
Slug: contratos-publicos-na-saude-1
URL: https://dados.gov.pt/datasets/contratos-publicos-na-saude-1/

Full description: O Portal Base é um espaço público, único e multifuncional, que agrega toda a informação sobre os contratos celebrados ao abrigo do Código dos Contratos Públicos (CCP), abrangendo as suas fases de formação e de execução.

Trata-se de uma iniciativa que assenta na ideia de maior transparência na divulgação da contratação pública efetuada em Portugal, concretizando um dos objetivos do Código.

O Portal Base é, nestes termos, um instrumento de _accountability_ da Administração Pública Portuguesa.

O...

Organization: IMPIC - Instituto Dos Mercados Públicos, do Imobiliário e da Construção
  Organization ID: 5ae97fa2c8d8c915d5faa3bf

Tags: contratacao, contratos-publicos, eficiencia, transparencia-sns-gov-pt

Resources: 4 file(s)

Created: 2019-02-28T00:00:00+00:00
Last updated: 2026-02-09T18:02:53.001000+00:00

License: notspecified
Update frequency: monthly
```

`list_dataset_resources {"dataset_id": "5b03f6dac8d8c922944afa55"}`

```
Resources in dataset: Contratos Públicos na Saúde
Dataset ID: 5b03f6dac8d8c922944afa55
Total resources: 4

1. Exportar para JSON
   Resource ID: ac0a1764-3937-4dae-81b8-16282a3a81ac
   Format: json
   MIME type: application/json
   Type: main
   URL: https://transparencia.sns.gov.pt/explore/dataset/portal-base/download?format=json&timezone=Europe/Berlin&use_labels_for_header=false

2. Exportar para CSV
   Resource ID: c13f54bc-408e-4d2a-98d6-c2c11a2b1ddf
   Format: csv
   MIME type: text/csv
   Type: main
   URL: https://transparencia.sns.gov.pt/explore/dataset/portal-base/download?format=csv&timezone=Europe/Berlin&use_labels_for_header=false

3. Export to CSV
   Resource ID: 561087b3-9cb0-4713-96be-17f1b447eae6
   Format: csv
   MIME type: text/csv
   Type: main
   URL: https://transparencia.sns.gov.pt/explore/dataset/portal-base/download?format=csv&timezone=Europe/Berlin&use_labels_for_header=true

4. Export to JSON
   Resource ID: 913bc57a-ae32-4066-a49e-d3b0da1dde3f
   Format: json
   MIME type: application/json
   Type: main
   URL: https://transparencia.sns.gov.pt/explore/dataset/portal-base/download?format=json&timezone=Europe/Berlin&use_labels_for_header=true
```

`search_organizations {"query": "câmara", "page_size": 5}`

```
Found 6 organization(s) (query 'câmara')
Page 1 of results:

1. Camara Municipal de Braga (CM Braga)
   ID: 674830739c9d713f509bc7b5
   Slug: camara-municipal-de-braga
   Metrics: datasets=0, reuses=0, followers=1, views=34
   URL: https://dados.gov.pt/organizations/camara-municipal-de-braga

2. Município do Porto (camara-municipal-do-porto)
   ID: 6090565c454ae3e68acc3c45
   Slug: municipio-do-porto
   Badges: local-authority
   Metrics: datasets=98, reuses=0, followers=6, views=45
   URL: https://dados.gov.pt/organizations/municipio-do-porto

3. Câmara Municipal de Oeiras (camara-municipal-de-oeiras)
   ID: 5c0eefbb454ae320decd1ab6
   Slug: camara-municipal-de-oeiras
   Badges: certified, local-authority
   Metrics: datasets=361, reuses=0, followers=9, views=5303
   URL: https://dados.gov.pt/organizations/camara-municipal-de-oeiras

4. Câmara Municipal de Cascais (camara-municipal-de-cascais)
   ID: 5ae9d111c8d8c9726d2cdbdf
   Slug: camara-municipal-de-cascais
   Badges: public-service, certified
   Metrics: datasets=334, reuses=5, followers=3, views=23614
   URL: https://dados.gov.pt/organizations/camara-municipal-de-cascais

5.  Câmara Municipal de Águeda  (camara-municipal-de-agueda)
   ID: 5ae9c8cfc8d8c9608bf89ee2
   Slug: camara-municipal-de-agueda
   Badges: public-service, certified
   Metrics: datasets=287, reuses=0, followers=7, views=4159
   URL: https://dados.gov.pt/organizations/camara-municipal-de-agueda
```

`get_resource_info {"resource_id": "c13f54bc-408e-4d2a-98d6-c2c11a2b1ddf"}`. This is the degraded path; I trimmed
the long column-list description:

```
Resource Information: Exportar para CSV

Resource ID: c13f54bc-408e-4d2a-98d6-c2c11a2b1ddf
Format: csv
MIME type: text/csv
Type: main

URL: https://transparencia.sns.gov.pt/explore/dataset/portal-base/download?format=csv&timezone=Europe/Berlin&use_labels_for_header=false

Description: - *Data de Celebração do Contrato*: data_de_celebracao_do_contrato[date]
[… 21 more column lines …]

Dataset ID: 5b03f6dac8d8c922944afa55
Dataset: Contratos Públicos na Saúde

Tabular API: not available on dados.gov.pt (fetch the resource URL directly)
```

`query_resource_data` (not registered): ``Unknown tool: query_resource_data``

That came to 7 requests to dados.gov.pt in total: one per tool call, two for `get_resource_info` (resource plus parent dataset), and one for the `/health` probe. `query_resource_data` never reached the network.

## Known gaps and follow-ups (not in this commit)

- **Hard-coded transport security.** `TransportSecuritySettings` in `main.py` only accepts *incoming* MCP requests
  addressed to `mcp.data.gouv.fr`, `mcp.preprod.data.gouv.fr` or localhost (DNS-rebinding protection). This has
  nothing to do with the outgoing calls to dados.gov.pt. It only matters if the server is hosted publicly under
  another name (e.g. `mcp.example.pt`); that would need something like `MCP_ALLOWED_HOSTS`.
- **Leftover naming.** The package description, User-Agent comment and `mcp_tool_defaults` comment still say
  data.gouv.fr. That's harmless.

## Step 4 design sketch: `query_resource_data` without a Tabular API

Goal: keep the tool's name, parameters and output format, and swap the backend for a local DuckDB query when
`TABULAR_API_URL` isn't set.

1. **Registration.** Register `query_resource_data` when there's a Tabular API, **or** when `duckdb` can be
   imported. Make DuckDB an optional extra (`datagouv-mcp[local-query]`) so upstream's image doesn't grow. A
   `LOCAL_QUERY_ENABLED` flag would let operators opt out.
2. **Resolve.** Call `2/datasets/resources/{rid}/` to get `url`, `format`, `mime`, `filesize` and `last_modified`.
   Only accept csv/tsv/xlsx/xls (and maybe parquet). Anything else returns the same "fetch the URL directly" hint
   as today.
3. **Download.** The URL comes from the data producer, so use `helpers/ssrf.ssrf_async_session`. Enforce the size
   cap while streaming (`LOCAL_QUERY_MAX_BYTES`, default around 50 MB). Don't rely on `filesize` or
   `Content-Length`: the IMPIC CSV above is an Opendatasoft export with no declared size. Abort with a clear error
   at the cap.
4. **Cache.** Use a temp dir keyed by `resource_id` + `last_modified`, with a TTL and LRU eviction by total bytes.
   Paging through a file shouldn't download it again.
5. **Load.**
   - CSV: `read_csv(..., auto_detect=true)`. Portuguese files are often `;`-separated, in cp1252/latin-1, and use
     decimal commas, so retry with `encoding='latin-1'` if UTF-8 fails, and pass `decimal_separator=','` when the
     sniffer finds `;`.
   - XLSX: DuckDB's `excel` extension (`read_xlsx`), using the first sheet.
   - Materialise into an in-memory table per request, or a persistent `.duckdb` file per cached resource.
6. **Query mapping.**
   - Filters map to `exact` → `=`, `contains` → `ILIKE '%' || ? || '%'`, `less`/`greater` → `<=`/`>=`, and
     `strictly_*` → `<`/`>`. These should match the Tabular API's semantics; check the inclusive/exclusive
     behaviour against tabular-api before building.
   - Sorting maps to `ORDER BY "col" ASC|DESC`. Paging maps to `LIMIT page_size OFFSET (page-1)*page_size`, and
     the total comes from `COUNT(*)`.
   - Check column names against the table's columns and quote them. Bind values as parameters; never interpolate
     them. Cast values for comparison operators, falling back to string comparison.
7. **Output.** Reuse the current formatter (columns, rows, total, page X of Y), plus one line saying the data was
   queried locally from a downloaded copy.
8. **Tests.** Mock the download with a small CSV (`;` + latin-1) and an XLSX fixture. Cover each operator, sorting,
   paging, the size-cap abort, and the SSRF block on a private IP.

## Draft upstream issue (for review, not posted)

> **Title:** Support other udata instances (e.g. dados.gov.pt)
>
> Hi! datagouv-mcp is the most complete MCP server for udata, and it could serve other udata portals with very
> few changes. I tried it against [dados.gov.pt](https://dados.gov.pt) (Portugal, udata 15.1, about 21k datasets).
> All the core endpoints the client uses (`1/site/`, `2/datasets/search/`, `1/datasets/{id}/`,
> `2/datasets/resources/{rid}/`, `2/organizations/search/`, `2/dataservices/search/`, `1/dataservices/{id}/`)
> answer with the same shapes as data.gouv.fr.
>
> What's France-specific is small:
> - the URL table in `helpers/env_config.py` (`demo`/`prod` only);
> - the satellite services (Tabular API, Metrics API, Crawler), which other portals usually don't have;
> - "data.gouv.fr" written into a few tool descriptions.
>
> **Proposal** (working prototype on a local branch, about 400 lines including tests):
> - `UDATA_API_URL` (plus optional `UDATA_SITE_URL` and `UDATA_PORTAL_NAME`) selects a custom instance, and takes
>   precedence over `DATAGOUV_API_ENV`. `TABULAR_API_URL`, `METRICS_API_URL` and `CRAWLER_API_URL` are optional.
> - Tools whose service isn't configured aren't registered (`query_resource_data`, `get_metrics`), and
>   `get_resource_info` skips those checks.
> - Tool descriptions use the configured portal name, and `UDATA_LANGUAGE` adapts the search stop words (today
>   French-only, e.g. "dados" breaks AND-searches on dados.gov.pt the way "données" does on data.gouv.fr).
> - **No change for data.gouv.fr.** With default settings, the `list_tools()` output is byte-identical to `main`,
>   and the existing suite passes unchanged.
>
> A possible follow-up: a local DuckDB fallback for `query_resource_data` on portals without a Tabular API
> (download with a size cap, then query in-process), as an optional extra.
>
> Would you be open to a PR along these lines? I'm happy to adapt it to your preferred shape, for example a named
> target table instead of env vars, or a different variable prefix.
