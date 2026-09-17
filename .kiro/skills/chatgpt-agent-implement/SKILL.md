---
name: chatgpt-agent-implement
description: Delegate an implementation task to ChatGPT through the local ChatGPT Agent bridge, allowing ChatGPT to edit, test, and commit the current workspace when explicitly requested.
compatibility: Requires macOS, Microsoft Edge signed in to ChatGPT, Python 3, and the chatgpt-agent.py runtime from this repository.
---

# ChatGPT Agent Implement

Use the existing implementation runtime:

```bash
python3 ./chatgpt-agent.py --preset implement --write $ARGUMENTS
```

This mode is intentionally write-capable. Only invoke it when the user explicitly asks for ChatGPT to implement or modify the workspace.

Example:

```text
/chatgpt-agent-implement implement the requested retry handling and add tests
```

The runtime is responsible for its own workspace safety, command restrictions, testing, and local commit behavior.

Do not add `--allow-shell` separately unless the user explicitly requests the shell-enabled mode; the implementation preset already has the write workflow.
