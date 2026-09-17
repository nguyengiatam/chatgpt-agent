---
name: chatgpt-agent-sessions
description: Inspect or manage ChatGPT Agent sessions from Kiro CLI using the existing repository runtime.
compatibility: Requires macOS, Microsoft Edge signed in to ChatGPT, and Python 3.
---

# ChatGPT Agent Sessions

Use the existing CLI/runtime for session operations:

```bash
python3 ./chatgpt-agent.py --preset sessions $ARGUMENTS
```

If the installed runtime exposes a different sessions subcommand, consult:

```bash
python3 ./chatgpt-agent.py --help
```

Do not modify session state manually unless the user explicitly asks.
