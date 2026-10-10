# Fork and weekly sync

## Branches

| Branch | Content |
|---|---|
| `udata-instances` (default) | The fork: upstream plus support for other udata portals |
| `main` | An untouched mirror of `datagouv/datagouv-mcp@main` |
| `sync/upstream-<sha>` | Created by the weekly sync, one per upstream state, merged through a pull request |

The fork is meant to stay a small layer on top of upstream. Two rules keep merges cheap:

- **Prefer new files** to edits in upstream's files. Most of the fork is in `helpers/portal_text.py`,
  `helpers/local_tabular_client.py`, new tests, and this `docs/` folder.
- **Don't change behaviour for data.gouv.fr.** With the default configuration the tool listing must stay
  byte-identical to upstream's.

## Weekly sync

A scheduled job checks upstream once a week and proposes a merge as a pull request on the fork. It is a plain
shell script; an AI assistant (Claude Code) is started only when the merge needs fixing.

```
fetch upstream ──► nothing new? ──► stop
                        │
                        ▼
              merge into sync/upstream-<sha>
                        │
              run checks: ruff, pytest,
              tool listing identical to upstream
                        │
          clean and passing? ──no──► Claude Code fixes it (headless, 30 min limit,
                        │            cannot push), then the checks run again
                        ▼
              push branch, open a pull request on the fork
              (a draft if the checks still fail)
```

What it never does: push to `udata-instances` directly, or send anything to the upstream repository. If a sync
pull request is already waiting, later runs do nothing until it is merged.

### Files

| File | Role |
|---|---|
| [`scripts/sync/sync-upstream.sh`](../scripts/sync/sync-upstream.sh) | The job |
| [`scripts/sync/dump_tools.py`](../scripts/sync/dump_tools.py) | Prints a source tree's tool listing as JSON, for the "identical to upstream" check |
| [`scripts/systemd/datagouv-mcp-sync.service`](../scripts/systemd/datagouv-mcp-sync.service) and `.timer` | Run the job every Monday at 08:00; a missed run happens at next boot |
| [`scripts/systemd/datagouv-mcp-sync-notify-failure@.service`](../scripts/systemd/datagouv-mcp-sync-notify-failure@.service) | Desktop notification when the job fails |

The files in `scripts/` are copies kept for reference. The job doesn't run from this checkout: it has its own
folder and its own clone, so it can't disturb work in progress here.

### Install

```shell
mkdir -p ~/code/datagouv-mcp-sync
cp scripts/sync/* ~/code/datagouv-mcp-sync/
cp scripts/systemd/datagouv-mcp-sync* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now datagouv-mcp-sync.timer
```

Requirements: `git`, `uv`, the GitHub CLI `gh` logged in to the account that owns the fork, and Claude Code
(`claude`) for the weeks that need it. The first run creates the clone in `~/code/datagouv-mcp-sync/repo`. It
uses HTTPS with `gh` as the credential helper, because SSH keys are usually not available to systemd jobs.

The script has the fork name, the Claude configuration folder and the commit identity at the top. Change them
if you reuse it for another fork.

### Use

| Task | Command |
|---|---|
| Run now | `systemctl --user start datagouv-mcp-sync.service` |
| Rehearse without pushing or opening a PR | `SYNC_DRY_RUN=1 ~/code/datagouv-mcp-sync/sync-upstream.sh` |
| Next scheduled run | `systemctl --user list-timers datagouv-mcp-sync.timer` |
| Log | `~/code/datagouv-mcp-sync/sync.log` (plus `last-checks.log` and `last-claude.log`) |

### After a sync pull request is merged

```shell
cd ~/code/datagouv-mcp
git pull
uv sync
systemctl --user restart dados-gov-pt-mcp.service   # if you run the server as a service
```

### What Claude is allowed to do

When it runs, Claude Code works only inside the sync clone. It can read and edit files and run `git` and `uv`
commands. `git push`, changing remotes and `gh` are blocked; the script does the pushing itself after
re-running the checks. Its summary is included in the pull request.

### Cost

Weeks with nothing new upstream, and weeks where the merge is clean, use no AI at all. A conflict costs one
short Claude Code session; the simulated one used for testing took about a minute.

## Contributing back to upstream

The generic part of this fork (configurable portal, optional satellite services, portal-neutral wording) could
be offered to the French project. A draft issue is at the end of the
[development notes](development-notes.md). Nothing has been posted upstream.
