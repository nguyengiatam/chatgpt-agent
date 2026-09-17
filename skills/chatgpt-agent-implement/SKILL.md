---
name: chatgpt-agent-implement
description: Delegate an implementation task to ChatGPT through the local ChatGPT Agent bridge, allowing ChatGPT to edit, test, and commit the current workspace when explicitly requested.
compatibility: Requires macOS, Microsoft Edge signed in to ChatGPT, Python 3, and the chatgpt-agent.py runtime from this repository.
---

# ChatGPT Agent Implement

Use the existing implementation runtime:

```bash
python3 ./chatgpt-agent.py --write $ARGUMENTS
```

This mode is write-capable and is intended only when the user explicitly asks for an implementation or workspace modification. The runtime enables the implementation workflow, runs commands as permitted by its safety rules, and requires the resulting work to be committed locally.
