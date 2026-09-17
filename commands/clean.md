---
description: List dead run checkpoints and stale ChatGPT sessions, and remove them
argument-hint: "[--days <n>] [--all] [--yes]"
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/chatgpt-agent.py" --prune $ARGUMENTS`

The rows above are what is old enough to drop from `~/.chatgpt-agent`: a `run`
is a checkpoint left behind by a run that was killed mid-flight, a `session` is
a conversation bookmark nobody has used lately.

Nothing was deleted unless `--yes` was passed. Show the list to the user and let
them decide — do not re-run with `--yes` on your own initiative. A checkpoint is
what `--resume` needs, and a bookmark is the only record of which conversation a
session name points at; the conversation itself stays in ChatGPT either way, but
the name and the URL live here and nowhere else.

Every run already drops what is older than 14 days, so this command is for
removing something now, for a shorter line (`--days <n>`), or for clearing the
store outright (`--all`).
