---
name: chatgpt-agent-review
description: Delegate a code review, diff review, branch review, or working-tree review to ChatGPT through the local ChatGPT Agent bridge.
compatibility: Requires macOS, Microsoft Edge signed in to ChatGPT, Python 3, and the chatgpt-agent.py runtime from this repository.
---

# ChatGPT Agent Review

Use the existing repository runtime rather than duplicating its bridge logic.

Run from the repository root:

```bash
python3 ./chatgpt-agent.py --preset review $ARGUMENTS
```

Examples:

```text
/chatgpt-agent-review review the current branch for correctness and regressions
/chatgpt-agent-review review the diff against main, focusing on security
```

The bridge is read-only by default. Do not add `--allow-shell` unless the user explicitly asks ChatGPT to execute commands.

If the command fails, run the doctor script:

```bash
python3 ./scripts/doctor.py
```

Present ChatGPT's review as returned by the command. Do not silently rewrite or reorder its findings.
