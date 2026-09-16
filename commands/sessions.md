---
description: List saved ChatGPT conversations, or forget one
argument-hint: "[--forget <name>]"
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/chatgpt-agent.py" --list-sessions $ARGUMENTS`

Each row is a session name and the ChatGPT conversation it resumes. Passing
`--session <name>` to review or plan continues that conversation, so ChatGPT
keeps what it already learned about the project; `--new` starts it over.

A session whose conversation has grown long is worth restarting with `--new`:
every served file stays in that conversation's context, and nothing here
prunes it.
