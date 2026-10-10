# Documentation for this fork

This repository is an unofficial fork of [datagouv/datagouv-mcp](https://github.com/datagouv/datagouv-mcp), the
MCP server of the French open-data portal. The fork makes the same server work with **any
[udata](https://github.com/opendatateam/udata) portal**, starting with Portugal's
[dados.gov.pt](https://dados.gov.pt). Behaviour for data.gouv.fr is unchanged.

An MCP server gives an AI assistant (Claude, ChatGPT, …) tools it can call. With this one, the assistant can
search a portal's datasets, read their metadata, and query CSV and XLSX files, all from a conversation.

| Page | What it covers |
|---|---|
| [dados.gov.pt quick start](dados-gov-pt.md) | Run the server for Portugal, keep it running, connect Claude Code, questions to try |
| [Other udata instances](udata-instances.md) | Every setting the fork adds, and how tools adapt to what a portal offers |
| [Local queries](local-queries.md) | How `query_resource_data` works on portals without a Tabular API |
| [Fork and weekly sync](fork-and-sync.md) | Branch layout, and the job that keeps the fork up to date with upstream |
| [Development notes](development-notes.md) | History of the changes, raw live-test outputs, and a draft issue for upstream |

The upstream README at the [repository root](../README.md) still documents everything the fork did not change:
the tools themselves, tests, linting and releases.

## What the fork changes, in one table

| Area | Upstream | This fork |
|---|---|---|
| Portal | data.gouv.fr only (`prod` or `demo`) | Any udata portal, set with `UDATA_API_URL` |
| Tool descriptions | Name data.gouv.fr and use French examples | Name the configured portal; examples follow `UDATA_LANGUAGE` |
| Search clean-up | Drops French filler words ("données", "fichier") | Drops filler words for `fr`, `pt` or `en` |
| `query_resource_data` | Needs the French Tabular API | Falls back to downloading the file and querying it in the server |
| `get_metrics` | Needs the French Metrics API | Not offered when the portal has no Metrics API |
| `get_resource_info` | Checks the Tabular and Crawler services | Skips checks for services the portal doesn't have |

On dados.gov.pt, 9 of the 10 tools are available (all but `get_metrics`).

## Licence

MIT, same as upstream (copyright DINUM). See [LICENSE](../LICENSE). The licence covers this code, not the data on
the portals: each dataset has its own licence.
