---
name: chatgpt-agent-plan
description: Ask ChatGPT to investigate a repository and produce an implementation plan for a coding task using the local ChatGPT Agent bridge.
compatibility: Requires macOS, Microsoft Edge signed in to ChatGPT, Python 3, and the chatgpt-agent.py runtime from this repository.
---

# ChatGPT Agent Plan

Run from the repository root:

```bash
python3 ./chatgpt-agent.py --preset plan $ARGUMENTS
```

Use this for architectural investigation and implementation planning. Plan mode is read-only.

If a reusable plan file is requested, pass the runtime's `--out` option through the command arguments.
