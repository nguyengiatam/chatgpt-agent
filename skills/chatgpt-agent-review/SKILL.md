---
name: chatgpt-agent-review
description: Delegate a code review, diff review, branch review, or working-tree review to ChatGPT through the local ChatGPT Agent bridge.
compatibility: Requires macOS, Microsoft Edge signed in to ChatGPT, Python 3, and the chatgpt-agent.py runtime from this repository.
---

# ChatGPT Agent Review

Run from the repository root:

```bash
python3 ./chatgpt-agent.py --preset review $ARGUMENTS
```

Use this for code review. The bridge is read-only by default.

If the command fails, run the doctor skill or:

```bash
python3 ./scripts/doctor.py
```

Present ChatGPT's review as returned by the command. Do not silently rewrite or reorder its findings.
