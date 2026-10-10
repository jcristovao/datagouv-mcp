# Hosting it publicly

Notes from 2026-10-10 on running the server somewhere other than your own machine: where, what to change first,
where to list it, and whether to charge. Prices and directory rules change often, and some prices below come from
third-party sources, so check them before acting.

## Who it is for

The plan below rests on two observations that pull in slightly different directions:

- **Paying customers are more likely developers and companies** (for example Portuguese legal-AI startups) than
  individual chat users. For them MCP is one delivery format next to an API, and what they pay for is data that
  web search can't give.
- **Many paying Claude and ChatGPT users would use this as is, if setup took a minute.** Journalists are the
  clearest case: they already search dados.gov.pt by hand, and "how many schools does Águeda have" or "who publishes
  public-contract data" is their daily work. They won't run `uv`, but they will paste a URL into a settings screen.

Both point to the same first step: a **free public endpoint with a one-page setup guide** (and, later, a listing in
the Claude directory, which makes it one click). Whether journalists or newsrooms would also pay is worth testing
once there are users, not before.

## Where to host it

The server is stateless and has no database. The one memory cost is the local query cache (up to 4 files of
25 MB each), so give it 512 MB to 1 GB of RAM.

| Option | Fit | Rough cost |
|---|---|---|
| **Hetzner VPS** (CX23 or CAX11) with Docker and Caddy | Simplest and cheapest, in the EU. You do patching, TLS and uptime | about €5.50–6/month (prices rose in June 2026) |
| **Scaleway Serverless Containers** (Paris) | EU provider, scales to zero, no server to maintain, uses the existing `Dockerfile` | about €0–5/month at low traffic |
| Fly.io or Koyeb (Frankfurt), Clever Cloud | Also fine | about $5–8/month; Clever Cloud about €10–20 |
| Cloudflare Workers | Poor fit: Python Workers run on Pyodide and can't run this server's dependencies well | — |
| Cloudflare Containers | Works (a Worker in front of the Docker image), but its OAuth library is TypeScript only | $5/month plan plus usage |

Recommended: Hetzner or Scaleway, optionally with Cloudflare in front for TLS, caching and rate limiting. An EU host
also covers data residency if personal data is ever served from the same infrastructure.

### Minimal setup on a VPS

```shell
# .env next to docker-compose.yml
UDATA_API_URL=https://dados.gov.pt/api/
UDATA_PORTAL_NAME=dados.gov.pt
UDATA_LANGUAGE=pt
MCP_HOST=0.0.0.0
MCP_PORT=8000
MCP_ENV=prod
MCP_ALLOWED_HOSTS=mcp.example.pt
```

```
# Caddyfile: TLS from Let's Encrypt, Host header passed through unchanged
mcp.example.pt {
    reverse_proxy 127.0.0.1:8000
}
```

Publish the container port on `127.0.0.1` only (`127.0.0.1:8000:8000` in `docker-compose.yml`), so that the
server is reachable only through Caddy.

## Before going public

| Item | State |
|---|---|
| Accept the public host name | **Done**: `MCP_ALLOWED_HOSTS` |
| Tool `title` and read-only annotations (needed by both directories) | **Already there** from upstream, on all 10 tools |
| Rate limiting per IP | To do, in the proxy (a Cloudflare rule; Caddy needs a plugin) |
| Request-size cap | To do, in the proxy |
| Privacy policy, support contact, documentation page, icon | To do, needed for directory listings |
| Wording | "Unofficial MCP server for dados.gov.pt, based on datagouv-mcp". No AMA or dados.gov.pt logos, nothing that reads as official |

### `MCP_ALLOWED_HOSTS`

The server checks the `Host` and `Origin` headers of every request, as the MCP specification asks, to block
DNS-rebinding attacks. By default it only accepts `mcp.data.gouv.fr`, `mcp.preprod.data.gouv.fr` and localhost, so
behind any other name every request gets `421 Misdirected Request`.

`MCP_ALLOWED_HOSTS` adds names, comma-separated. Each one is accepted as a `Host`, and `https://<name>` as an
`Origin`. A port (`name:8080`) or port wildcard (`name:*`) is allowed. A scheme or path pasted by mistake is
dropped. The defaults are always kept, so data.gouv.fr deployments are unaffected. Code:
[`helpers/transport_security.py`](../helpers/transport_security.py).

## Setup for chat users

What the one-page guide would say, for an endpoint at `https://mcp.example.pt/mcp`:

- **Claude** (claude.ai and the desktop app): Settings, Connectors, Add custom connector, paste the URL.
- **ChatGPT** (paid plans): Settings, Apps and connectors, Advanced settings, turn on Developer mode, then add a
  connector with the URL.
- **Claude Code**: `claude mcp add --transport http dados-gov-pt https://mcp.example.pt/mcp`

The first question to try is a good part of the guide: the ones in the [quick start](dados-gov-pt.md#4-questions-to-try)
work well.

## Directories and registries

| Where | Can it be listed? | What it takes |
|---|---|---|
| **Claude Connectors Directory** | Yes. Submit at claude.ai/directory/manage ("MCP connector"), from any paid Claude plan; free to list | HTTPS endpoint; tool titles and annotations; **no login for public data**; documentation, privacy policy, support contact, icon; at least 3 example prompts; compliance acknowledgments. Automated scan, then a "Community" listing, sometimes human review |
| **ChatGPT Apps** | Yes | Verified individual or organisation, privacy policy, support contact, tool annotations. **No selling of digital goods or subscriptions inside ChatGPT, freemium upsells included**; you may only ask users to log in to a plan bought elsewhere, with a neutral message |
| **Official MCP Registry** (registry.modelcontextprotocol.io, preview) | Yes | `server.json` with a `remotes` entry, published with `mcp-publisher`; namespace proven by GitHub (`io.github.jcristovao/…`) or a DNS record on a domain you own (`pt.example/…`). Other registries read from it |
| Smithery, Glama, mcp.so, PulseMCP | Yes, free and self-service | Glama indexes GitHub repositories on its own |

Sources: [Claude submission guide](https://claude.com/docs/connectors/building/submission),
[Anthropic directory policy](https://support.claude.com/en/articles/13145358-anthropic-software-directory-policy),
[OpenAI app submission guidelines](https://developers.openai.com/apps-sdk/app-submission-guidelines).

## Charging

**It is legal.** The EU Open Data Directive (2019/1024) limits what *public bodies* charge, not re-users, and
Portugal's Lei 68/2021 allows commercial re-use. Each dataset keeps its own licence: CC-BY requires credit to the
source, and datasets marked "not specified" are not clearly reusable.

**The mechanics are standard:** an API key or OAuth login, a per-key counter (Redis or Postgres), N free calls a
month, then the tool replies "quota exceeded, see plans". Billing can be Stripe metered billing, or prepaid credits
paid by Multibanco as on nif.pt. In this code, the ASGI wrapper `with_monitoring` in `main.py` already sees every
request, so a key check and counter would sit next to it. Gateways (Zuplo, Moesif, Apify) can do keys and metering
instead.

**For this server on its own, charging is weak.** The code is MIT and comes from the French project, the data is
free, data.gouv.fr runs its own MCP server for free, and anyone can host this fork. A paywall invites a free copy,
and if AMA launches an official one it would look like rent-seeking. ChatGPT's rules also rule out the freemium
upsell there.

**What fits better:**
1. Keep the dados.gov.pt endpoint free and anonymous, with per-IP limits. It is the showcase and the way in.
2. Charge for things that are worth paying for: data that web search can't give (curated or proprietary datasets
   served from the same infrastructure), higher limits with a service level, or private deployments for companies
   and newsrooms.
3. If journalists turn out to be a real audience, a newsroom plan (a key for a team, higher limits, help with
   queries) is a better test than a per-person subscription.

## Suggested order

1. Review and post the draft upstream issue ([development notes](development-notes.md#draft-upstream-issue-for-review-not-posted)).
2. Launch the free public instance with the setup guide.
3. Submit to the Claude directory and the registries.
4. Decide on charging once there are users, together with any paid data products.
