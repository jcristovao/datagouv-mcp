# Development notes: support for other udata instances

These are working notes from building the fork (2026-10-09 and 2026-10-10): what changed commit by commit, raw
outputs of the live tests against dados.gov.pt, and a draft issue for upstream that has not been posted. For
current usage, start at the [docs index](README.md).

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
- `tools/__init__.py` only registers `get_metrics` when a Metrics API is configured. In the first commit
  `query_resource_data` was also dropped without a Tabular API (8 of 10 tools on dados.gov.pt); since step 4 it
  falls back to a local query instead, so dados.gov.pt has 9 of 10. See "Step 4 (built)" below.
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

## Step 4 (built): `query_resource_data` without a Tabular API

When there is no Tabular API, `query_resource_data` now downloads the file and queries it inside the server.
I used Python's built-in `csv` module plus `openpyxl` (new dependency, pure Python) instead of DuckDB.

- **Module:** `helpers/local_tabular_client.py`. The tool keeps its name, parameters and output format. Its
  result has the same shape as the Tabular API's, so the existing formatter is reused.
- **Formats:** CSV, TSV and XLSX (first sheet). The format comes from the catalog `format`, the URL extension or
  the MIME type. Anything else returns "fetch the resource URL directly". Old `.xls` and `.ods` are not supported.
- **Download:** uses the existing SSRF-hardened session, because the URL comes from the data producer. The size
  cap is `LOCAL_QUERY_MAX_BYTES` (default 25 MB; `0` disables the fallback and the tool is not registered). It is
  checked three times: against the catalog `filesize`, against `Content-Length`, and while streaming.
- **Cache:** the last 4 downloaded files are kept in memory for 10 minutes, so paging doesn't download again.
- **Parsing:** UTF-8 (with or without BOM), falling back to cp1252 and latin-1. The separator is sniffed from
  `, ; tab |`. Blank rows are skipped; blank or duplicate headers get stable names (`column_2`, `a_2`).
- **Filters:** same semantics as the Tabular API (`less`/`greater` inclusive, `strictly_*` exclusive).
  Comparisons are numeric when both sides are numbers, including decimal commas (`10,5`); otherwise they compare
  as text, which works for ISO dates. `contains` ignores case. Empty cells never match a comparison.
- **Sorting:** numeric if every non-empty value in the column is a number, otherwise case- and accent-insensitive
  text ("Évora" sorts next to "Evora"). Blanks go last.
- **Memory:** without a sort, only the requested page is kept in memory. With a sort, all matching rows are.
- **Errors:** an unknown column returns the list of available columns.
- **Descriptions:** `query_resource_data`, `list_dataset_resources` and `get_resource_info` have a third wording
  for this mode ("files up to 25 MB"), and `get_resource_info` says whether a given file can be queried.
- **data.gouv.fr is unaffected.** It has a Tabular API, so the local path is never used there. The default tool
  listing is still byte-identical to upstream.

Tests: **215 passed, 2 deselected** (`tests/test_local_tabular.py` is new). ruff and ty are clean.

Limits worth knowing:
- Each query re-parses the cached file. That's fine at 25 MB, but it's why the cap shouldn't be raised much.
- Thousands separators (`1.234,56` or `1,234.56`) are not parsed as numbers, so those columns compare as text.
- Compressed files (`.csv.gz`, `.zip`) are not supported.

### Live test (dados.gov.pt, 2026-10-10)

dados.gov.pt now exposes 9 of the 10 tools (all but `get_metrics`).

```
### query_resource_data {"resource_id": "a9a229b1-e29b-4351-8217-728a97ebec2b", "page_size": 2}
Querying resource: Escolas 2019, csv
Resource ID: a9a229b1-e29b-4351-8217-728a97ebec2b
Dataset: Escolas (ID: 5ae9c921c8d8c9146c44cc72)
Source: file downloaded and queried by the server (no Tabular API on dados.gov.pt)

Total rows (local copy): 22
Total pages: 11 (page size: 2)
Retrieved: 2 row(s) from page 1
Columns: gid, nome, tipo_ens, agrupament, sede_agrup, morada, lugar, freguesia, lat, lon

Data (2 rows):
  Row 1:
    gid: 18
    nome: Escola Básica António Graça
    tipo_ens: Pré-escolar e 1 ciclo do ensino básico
    agrupament: Águeda Sul
    sede_agrup: não
    morada: Rua do Pavilhão Gimnodesportivo 81, 3750-351 Barrô AGD
    lugar: Barrô
    freguesia: 10122
    lat: 96223.015
    lon: -28603.474
  Row 2:
    gid: 20
    nome: Jardim de Infância da Giesteira
    tipo_ens: Pré-escolar
    agrupament: Águeda
    sede_agrup: não
    morada: Rua da Escola 239, Giesteira, 3750-309 Águeda
    lugar: Giesteira
    freguesia: 10121
    lat: 102727.659
    lon: -22289.327

📄 More data available. Use page=2 to see the next page.
```

XLSX, same call shape:

```
### query_resource_data {"resource_id": "ac2e2c79-9e10-4f9e-8d83-84537839da18", "page_size": 2}
Querying resource: Agrupamentos de Escolas de Lisboa - 2017-2018
Resource ID: ac2e2c79-9e10-4f9e-8d83-84537839da18
Dataset: Agrupamentos de Escolas de Lisboa (ID: 5ae9c606c8d8c9146d44cc0b)
Source: file downloaded and queried by the server (no Tabular API on dados.gov.pt)

Total rows (local copy): 128
Total pages: 64 (page size: 2)
Retrieved: 2 row(s) from page 1
Columns: Ano Lectivo, Agrupamento de Escolas, Nome do Estabelecimento, Tipologia, Morada, Código Postal, Junta de Freguesia, Contacto Geral, Site
[… 2 rows shown, then "📄 More data available. Use page=2 to see the next page."]
```

Filter plus sort (rows trimmed to three columns), and an unknown column:

```
### query_resource_data {"resource_id": "a9a229b1-e29b-4351-8217-728a97ebec2b", "filter_column": "tipo_ens", "filter_value": "pré-escolar", "filter_operator": "contains", "sort_column": "lat", "sort_direction": "desc", "page_size": 3}
Querying resource: Escolas 2019, csv
Resource ID: a9a229b1-e29b-4351-8217-728a97ebec2b
Dataset: Escolas (ID: 5ae9c921c8d8c9146c44cc72)
Source: file downloaded and queried by the server (no Tabular API on dados.gov.pt)

Filter: tipo_ens contains pré-escolar
Sort: lat (desc)

Total rows (local copy): 12
Total pages: 4 (page size: 3)
Retrieved: 3 row(s) from page 1
Columns: gid, nome, tipo_ens, agrupament, sede_agrup, morada, lugar, freguesia, lat, lon

Data (3 rows):
  Row 1:
    nome: Escola Básica de Macinhata
    tipo_ens: Pré-escolar e 1 ciclo do ensino básico
    lat: 109724.955
  Row 2:
    nome: Escola Básica de Valongo do Vouga
    tipo_ens: Pré-escolar e 1 ciclo do ensino básico
    lat: 105087.261
  Row 3:
    nome: Escola Básica da Trofa
    tipo_ens: Pré-escolar e 1 ciclo do ensino básico
    lat: 104758.934

📄 More data available. Use page=2 to see the next page.

### query_resource_data {"resource_id": "a9a229b1-e29b-4351-8217-728a97ebec2b", "sort_column": "nope"}
Querying resource: Escolas 2019, csv
Resource ID: a9a229b1-e29b-4351-8217-728a97ebec2b
Dataset: Escolas (ID: 5ae9c921c8d8c9146c44cc72)
Source: file downloaded and queried by the server (no Tabular API on dados.gov.pt)

Sort: nope (asc)

⚠️  Column 'nope' does not exist in this resource. Available columns: gid, nome, tipo_ens, agrupament, sede_agrup, morada, lugar, freguesia, lat, lon
```

Size cap, on the large IMPIC export with the limit lowered to 2 MB for the test:

```
### query_resource_data {"resource_id": "c13f54bc-408e-4d2a-98d6-c2c11a2b1ddf", "page_size": 2}
Querying resource: Exportar para CSV
Resource ID: c13f54bc-408e-4d2a-98d6-c2c11a2b1ddf
Dataset: Contratos Públicos na Saúde (ID: 5b03f6dac8d8c922944afa55)
Source: file downloaded and queried by the server (no Tabular API on dados.gov.pt)

⚠️  The file is larger than the 2 MB limit for local queries. Fetch the resource URL directly instead.
```

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
> **Proposal** (working prototype on a local branch):
> - `UDATA_API_URL` (plus optional `UDATA_SITE_URL` and `UDATA_PORTAL_NAME`) selects a custom instance, and takes
>   precedence over `DATAGOUV_API_ENV`. `TABULAR_API_URL`, `METRICS_API_URL` and `CRAWLER_API_URL` are optional.
> - `get_metrics` isn't registered when there's no Metrics API, and `get_resource_info` skips the Tabular and
>   Crawler checks.
> - Tool descriptions use the configured portal name, and `UDATA_LANGUAGE` adapts the search stop words (today
>   French-only, e.g. "dados" breaks AND-searches on dados.gov.pt the way "données" does on data.gouv.fr).
> - **No change for data.gouv.fr.** With default settings, the `list_tools()` output is byte-identical to `main`,
>   and the existing suite passes unchanged.
>
> The prototype also has a fallback for `query_resource_data` on portals without a Tabular API: the server
> downloads the CSV/XLSX file (size-capped, through the existing SSRF guard) and applies the same filters, sorting
> and pagination in-process, using the standard `csv` module and `openpyxl`. This could be a separate PR.
>
> Would you be open to a PR along these lines? I'm happy to adapt it to your preferred shape, for example a named
> target table instead of env vars, or a different variable prefix.
