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

Use this for architectural investigation and implementation planning. The normal plan mode is read-only.

Example:

```text
/chatgpt-agent-plan design a retry strategy for the payment webhook worker
```

If a reusable plan file is requested, use the runtime's `--out` option through the command arguments.
