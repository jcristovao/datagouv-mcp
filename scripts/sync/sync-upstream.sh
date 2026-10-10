#!/usr/bin/env bash
# Weekly sync of the jcristovao/datagouv-mcp fork with datagouv/datagouv-mcp.
#
# Runs from datagouv-mcp-sync.timer. Works in its own clone (./repo), never in
# ~/code/datagouv-mcp, so it cannot disturb work in progress there.
#
#   1. Fetch both remotes. Fast-forward the fork's `main` mirror.
#   2. If upstream/main is already merged into the fork branch: exit, done.
#      (No Claude usage on these weeks.)
#   3. Merge upstream/main into a new sync/upstream-<sha> branch and run the
#      checks: ruff, pytest, and "default tool listing identical to upstream".
#   4. Only if the merge conflicts or a check fails: run Claude Code headless
#      to fix it, then re-run the checks.
#   5. Push the sync branch and open a pull request ON THE FORK (a draft if
#      the checks still fail). Nothing is ever pushed to the fork branch
#      itself and nothing is ever sent to the upstream repository.
#
# Manual run:  ~/code/datagouv-mcp-sync/sync-upstream.sh
# Dry run:     SYNC_DRY_RUN=1 ~/code/datagouv-mcp-sync/sync-upstream.sh
#              (does everything locally, but pushes nothing and opens no PR)
# Log:         ~/code/datagouv-mcp-sync/sync.log

set -uo pipefail

export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
export CLAUDE_CONFIG_DIR="$HOME/.claude-personal"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=/run/user/$(id -u)/bus}"
# The checks must see the default (data.gouv.fr) configuration
unset UDATA_API_URL UDATA_SITE_URL UDATA_PORTAL_NAME UDATA_LANGUAGE \
  TABULAR_API_URL METRICS_API_URL CRAWLER_API_URL LOCAL_QUERY_MAX_BYTES DATAGOUV_API_ENV

FORK="jcristovao/datagouv-mcp"
UPSTREAM_URL="https://github.com/datagouv/datagouv-mcp.git"
FORK_BRANCH="udata-instances"
UPSTREAM_BRANCH="main"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${SYNC_REPO_DIR:-$DIR/repo}"
DRY_RUN="${SYNC_DRY_RUN:-}"
UPSTREAM_TREE="$DIR/upstream-tree"
LOG="$DIR/sync.log"
CHECK_LOG="$DIR/last-checks.log"
CLAUDE_TIMEOUT="${SYNC_CLAUDE_TIMEOUT:-1800}"
MODEL="${SYNC_MODEL:-}" # empty = the config default

log() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG"; }
notify() { notify-send --app-name=datagouv-mcp-sync "$@" 2>/dev/null || true; }
die() {
  log "ERROR: $*"
  exit 1
}

# --- 1. clone / fetch --------------------------------------------------------
if [[ ! -d "$REPO/.git" ]]; then
  log "creating sync clone in $REPO"
  git clone -q "https://github.com/$FORK.git" "$REPO" || die "clone failed"
  git -C "$REPO" remote add upstream "$UPSTREAM_URL"
  git -C "$REPO" config credential.helper '!gh auth git-credential'
  git -C "$REPO" config user.name "João Cristóvão"
  git -C "$REPO" config user.email "jmacristovao@gmail.com"
fi
cd "$REPO" || die "cannot enter $REPO"

git merge --abort 2>/dev/null
git fetch -q --prune origin || die "fetch origin failed"
git fetch -q upstream || die "fetch upstream failed"

up="upstream/$UPSTREAM_BRANCH"
ours="origin/$FORK_BRANCH"

# Keep the fork's main as a mirror of upstream (fast-forward only)
if [[ -z "$DRY_RUN" && "$(git rev-parse "$up")" != "$(git rev-parse "origin/$UPSTREAM_BRANCH")" ]]; then
  if git push -q origin "$up:refs/heads/$UPSTREAM_BRANCH"; then
    log "fork $UPSTREAM_BRANCH fast-forwarded to $(git rev-parse --short "$up")"
  else
    log "WARN: could not fast-forward the fork's $UPSTREAM_BRANCH mirror"
  fi
fi

# --- 2. anything to do? ------------------------------------------------------
if git merge-base --is-ancestor "$up" "$ours"; then
  log "up to date: $up ($(git rev-parse --short "$up")) is already in $FORK_BRANCH"
  exit 0
fi

sync_branch="sync/upstream-$(git rev-parse --short "$up")"
if git rev-parse -q --verify "origin/$sync_branch" >/dev/null; then
  log "sync branch $sync_branch already pushed; waiting for its pull request to be merged"
  exit 0
fi

new_commits="$(git log --oneline --no-merges "$ours..$up")"
log "upstream has $(wc -l <<<"$new_commits") new commit(s); merging into $sync_branch"

# --- 3. merge + checks -------------------------------------------------------
git checkout -q -f -B "$sync_branch" "$ours" || die "checkout failed"
git clean -q -fd

run_checks() {
  # Returns 0 when everything passes. Full output goes to $CHECK_LOG.
  {
    echo "== uv sync" && uv sync -q &&
      echo "== ruff check" && uv run ruff check . &&
      echo "== ruff format --check" && uv run ruff format --check . &&
      echo "== pytest" &&
      { uv run pytest -q || {
        # Upstream's suite calls live French APIs and can time out: retry failures once
        echo "== pytest: retrying failed tests once"
        sleep 30
        uv run pytest -q --lf
      }; } &&
      echo "== default tool listing identical to upstream" &&
      rm -rf "$UPSTREAM_TREE" && git worktree prune &&
      git worktree add -q --detach "$UPSTREAM_TREE" "$up" &&
      uv run python "$DIR/dump_tools.py" "$UPSTREAM_TREE" >"$DIR/listing-upstream.json" &&
      uv run python "$DIR/dump_tools.py" "$REPO" >"$DIR/listing-fork.json" &&
      cmp "$DIR/listing-upstream.json" "$DIR/listing-fork.json"
  } >"$CHECK_LOG" 2>&1
  local rc=$?
  git worktree remove --force "$UPSTREAM_TREE" 2>/dev/null
  return $rc
}

merge_state() { # clean | conflict
  if [[ -f .git/MERGE_HEAD ]] || [[ -n "$(git status --porcelain)" ]]; then echo conflict; else echo clean; fi
}

used_claude=no
problem=""
if ! git merge -q --no-edit "$up" >>"$LOG" 2>&1; then
  problem="The merge stopped with conflicts in: $(git diff --name-only --diff-filter=U | tr '\n' ' ')"
elif ! run_checks; then
  problem="The merge was clean, but the checks fail. Last lines of the check output:
$(tail -n 40 "$CHECK_LOG")"
fi

# --- 4. Claude, only when needed ---------------------------------------------
if [[ -n "$problem" ]]; then
  log "needs fixing: ${problem%%$'\n'*}"
  used_claude=yes
  prompt="You are in a git clone of $FORK, a fork of datagouv/datagouv-mcp that adds support
for other udata portals (see docs/ for what the fork changes and why).
A weekly job is merging upstream/$UPSTREAM_BRANCH into the fork branch, on branch $sync_branch.

$problem

Your job: finish this merge so that the fork keeps working.
- Resolve conflicts by keeping upstream's changes AND the fork's features (configurable udata
  instance in helpers/env_config.py, helpers/portal_text.py, helpers/local_tabular_client.py,
  conditional tool registration and descriptions).
- With the default configuration, the tool listing must stay byte-identical to upstream:
  compare 'uv run python $DIR/dump_tools.py <tree>' for this tree and for a checkout of
  upstream/$UPSTREAM_BRANCH. If upstream changed a description that the fork templates,
  update the fork's template text to match.
- 'uv run ruff check .', 'uv run ruff format --check .' and 'uv run pytest -q' must pass.
  A few upstream tests call live French APIs and can time out; do not 'fix' those.
- Commit the result (finish the merge commit, or add a follow-up commit). Follow the repo's
  commit style.
- Do NOT push, do not use gh, do not change remotes, and do not edit files outside this clone.
End with a short summary of what you changed and anything a human should review."
  model_args=()
  [[ -n "$MODEL" ]] && model_args=(--model "$MODEL")
  log "invoking claude (timeout ${CLAUDE_TIMEOUT}s)"
  timeout "$CLAUDE_TIMEOUT" claude -p "$prompt" "${model_args[@]}" \
    --permission-mode acceptEdits \
    --allowedTools "Read" "Edit" "Write" "Glob" "Grep" "Bash(git:*)" "Bash(uv:*)" "Bash(cmp:*)" "Bash(diff:*)" \
    --disallowedTools "Bash(git push:*)" "Bash(git remote:*)" "Bash(gh:*)" \
    >"$DIR/last-claude.log" 2>&1
  log "claude exited rc=$?"
  cat "$DIR/last-claude.log" >>"$LOG"

  if [[ "$(merge_state)" != clean ]]; then
    git merge --abort 2>/dev/null
    git checkout -q -f "$ours" 2>/dev/null
    notify --urgency=critical "Sync com o upstream falhou" \
      "Conflitos por resolver. Ver $LOG"
    die "merge still unresolved after Claude; nothing pushed"
  fi
fi

checks=passed
if [[ "$used_claude" == yes ]] && ! run_checks; then
  checks=failed
fi

# --- 5. push + pull request on the fork --------------------------------------
if [[ -n "$DRY_RUN" ]]; then
  log "dry run: would push $sync_branch and open a PR (claude=$used_claude, checks=$checks)"
  exit 0
fi
git push -q -u origin "$sync_branch" || die "push of $sync_branch failed"

draft_args=()
title="Sync with upstream ($(git rev-parse --short "$up"))"
if [[ "$checks" == failed ]]; then
  draft_args=(--draft)
  title="$title: checks failing"
fi
body="Weekly sync: merges \`datagouv/datagouv-mcp@$UPSTREAM_BRANCH\` into \`$FORK_BRANCH\`.

**Upstream commits**
\`\`\`
$new_commits
\`\`\`

**Merge:** $([[ "$used_claude" == yes ]] && echo "needed fixing; resolved by Claude Code, please review the diff" || echo "clean, no conflicts")
**Checks (ruff, pytest, default tool listing identical to upstream):** $checks
$([[ "$checks" == failed ]] && printf '\n```\n%s\n```\n' "$(tail -n 30 "$CHECK_LOG")")
$([[ "$used_claude" == yes ]] && printf '\n**Claude summary**\n\n%s\n' "$(tail -n 25 "$DIR/last-claude.log")")"

# --repo is essential: without it gh targets the upstream repository for forks
pr_url="$(gh pr create --repo "$FORK" --base "$FORK_BRANCH" --head "$sync_branch" \
  --title "$title" --body "$body" "${draft_args[@]}")" || die "gh pr create failed"
log "opened $pr_url (claude=$used_claude, checks=$checks)"
notify "datagouv-mcp: PR de sync aberto" "$pr_url (verificações: $checks)"
[[ "$checks" == passed ]]
