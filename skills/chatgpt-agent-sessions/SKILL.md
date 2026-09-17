---
name: chatgpt-agent-sessions
description: Inspect or manage saved ChatGPT Agent sessions from Kiro CLI using the existing repository runtime.
compatibility: Requires macOS, Microsoft Edge signed in to ChatGPT, Python 3, and the chatgpt-agent.py runtime from this repository.
---

# ChatGPT Agent Sessions

List saved sessions:

```bash
python3 ./chatgpt-agent.py --list-sessions
```

Forget a saved session by name:

```bash
python3 ./chatgpt-agent.py --forget "$ARGUMENTS"
```

List what is stale enough to drop, from both the session store and the
checkpoints left by interrupted runs:

```bash
python3 ./chatgpt-agent.py --prune
```

This prints and deletes nothing. Add `--yes` to remove the listed rows,
`--days <n>` for a different age line, `--all` to ignore age entirely. Every run
already prunes what is older than 14 days, so this is for clearing something now.

Review and plan skills can resume a session with `--session <name>` or start a fresh conversation with `--new`. Do not modify session state unless the user explicitly asks — that includes `--prune --yes` and `--forget`.
