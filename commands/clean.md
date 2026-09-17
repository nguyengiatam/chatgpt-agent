---
description: List dead run checkpoints, stale ChatGPT sessions and abandoned claim files, and remove them
argument-hint: "[--days <n>] [--all] [--yes]"
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/chatgpt-agent.py" --prune $ARGUMENTS`

The rows above are removable state under `~/.chatgpt-agent`: a `run` is a
checkpoint left behind by a run that was killed mid-flight, a `session` is a
conversation bookmark nobody has used lately, and a `claim` is a lock file the
kernel confirms no process still owns. Claims are reported separately because
they are not age-based state.

Nothing was deleted unless `--yes` was passed. Show the list to the user and let
them decide — do not re-run with `--yes` on your own initiative. A checkpoint is
what `--resume` needs, and a bookmark is the only record of which conversation a
session name points at; the conversation itself stays in ChatGPT either way, but
the name and the URL live here and nowhere else.

Every run already drops checkpoints and sessions older than 14 days, so this
command is for removing something now, for a shorter line (`--days <n>`), for
clearing age-based state outright (`--all`), or for cleaning unlocked claim
files that ownership has already released.
