---
name: chatgpt-agent-ask
description: Ask ChatGPT a repository-aware question through the local ChatGPT Agent bridge without modifying the workspace.
compatibility: Requires macOS, Microsoft Edge signed in to ChatGPT, Python 3, and the chatgpt-agent.py runtime from this repository.
---

# ChatGPT Agent Ask

Run:

```bash
python3 ./chatgpt-agent.py --preset ask $ARGUMENTS
```

Use this when the user wants ChatGPT's reasoning about the current repository but does not want an implementation.

The default bridge is read-only. Do not enable write or shell capabilities unless explicitly requested.
