# dados.gov.pt quick start

## 1. Run the server

You need [uv](https://github.com/astral-sh/uv).

```shell
git clone https://github.com/jcristovao/datagouv-mcp.git
cd datagouv-mcp
uv sync

UDATA_API_URL=https://dados.gov.pt/api/ UDATA_PORTAL_NAME=dados.gov.pt UDATA_LANGUAGE=pt \
  MCP_HOST=127.0.0.1 MCP_PORT=8917 uv run main.py
```

Check that it's up:

```shell
curl http://127.0.0.1:8917/health
# {"status": "ok", ..., "data_env": "custom"}
```

The server fetches data from dados.gov.pt. It doesn't contact any French service in this configuration.

Keep `MCP_HOST=127.0.0.1`. The server has no authentication, and by default it only accepts requests addressed
to `localhost`, `127.0.0.1` or the French project's own host names (a protection against DNS-rebinding attacks).
To host it publicly under another name, see [Hosting it publicly](hosting.md).

## 2. Keep it running (Linux, systemd)

[`scripts/systemd/dados-gov-pt-mcp.service`](../scripts/systemd/dados-gov-pt-mcp.service) is a user service that
starts the server at login and restarts it if it crashes. It expects the checkout at `~/code/datagouv-mcp`;
edit `WorkingDirectory` and `ExecStart` if yours is elsewhere.

```shell
cp scripts/systemd/dados-gov-pt-mcp.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now dados-gov-pt-mcp.service
```

| Task | Command |
|---|---|
| Status | `systemctl --user status dados-gov-pt-mcp.service` |
| Logs, including every tool call | `journalctl --user -u dados-gov-pt-mcp.service -f` |
| Restart after pulling new code | `systemctl --user restart dados-gov-pt-mcp.service` |
| Stop and disable | `systemctl --user disable --now dados-gov-pt-mcp.service` |

The service runs the code in the checkout as it is on disk. After `git pull` (for example, once a
[weekly sync](fork-and-sync.md) pull request is merged), restart it.

## 3. Connect Claude Code

```shell
claude mcp add --transport http --scope user dados-gov-pt http://127.0.0.1:8917/mcp
claude mcp list
# dados-gov-pt: http://127.0.0.1:8917/mcp (HTTP) - ✔ Connected
```

`--scope user` makes it available in every project. It takes effect in **new** Claude Code sessions; a session
that was already open won't see it. To remove it: `claude mcp remove dados-gov-pt -s user`.

Other MCP clients work the same way as in the upstream README: use `http://127.0.0.1:8917/mcp` as the URL in
place of `https://mcp.data.gouv.fr/mcp`.

## 4. Questions to try

- *Que conjuntos de dados existem no dados.gov.pt sobre contratos públicos, e quem os publica?*
  Uses search and dataset details.
- *Quantas escolas tem o concelho de Águeda, e quantas são só de pré-escolar?*
  Uses the whole chain: search, list a dataset's files, then query a CSV. On 2026-10-10 the answer was 22
  establishments in the 2019 file, 3 of them pre-school only.
- *Que câmaras municipais publicam mais dados no dados.gov.pt?*
  Uses the organization search.

To see what the assistant actually called, watch the service log while it answers.

## Available tools on dados.gov.pt

| Tool | Works | Notes |
|---|---|---|
| `search_datasets` | yes | With `UDATA_LANGUAGE=pt`, filler words like "dados" and "ficheiro" are dropped from queries |
| `get_dataset_info` | yes | |
| `list_dataset_resources` | yes | |
| `get_resource_info` | yes | Says whether a file can be queried |
| `query_resource_data` | yes | CSV, TSV and XLSX up to 25 MB, queried in the server. See [Local queries](local-queries.md) |
| `search_organizations` | yes | |
| `search_dataservices` | yes | The portal lists only a handful of APIs |
| `get_dataservice_info` | yes | |
| `get_dataservice_openapi_spec` | yes | |
| `get_metrics` | no | dados.gov.pt has no Metrics API |
