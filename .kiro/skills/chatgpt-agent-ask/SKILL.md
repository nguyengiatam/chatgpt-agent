---
name: chatgpt-agent-ask
description: Ask ChatGPT a repository-aware question through the local ChatGPT Agent bridge without modifying the workspace.
compatibility: Requires macOS, Microsoft Edge signed in to ChatGPT, Python 3, and the chatgpt-cli.py runtime from this repository.
---

# ChatGPT Agent Ask

Use the repository's one-shot ChatGPT CLI:

```bash
python3 ./chatgpt-cli.py $ARGUMENTS
```

Use this when the user wants an answer from ChatGPT without repository modification. Do not enable the implementation workflow for this skill.
