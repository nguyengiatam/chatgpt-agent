---
description: Ask ChatGPT a one-shot question with no repository access
argument-hint: "<question>"
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/chatgpt-cli.py" $ARGUMENTS`

A single prompt and a single reply, with no workspace bridge and no loop. Use
this for questions that need no repository context; use `/chatgpt-agent:review`
or `/chatgpt-agent:plan` when ChatGPT needs to read the code.

Present the reply as-is. It came from a different model than you, so say so if
it contradicts something you told the user earlier, rather than quietly picking
one of the two.
