---
description: Review a diff, branch or working tree with ChatGPT reading the repository itself
argument-hint: "[--workspace <dir>] [--session <name>] [--new] [--out <file>] [--max-rounds <n>] <what to review>"
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/chatgpt-agent.py" --preset review $ARGUMENTS`

The output above is ChatGPT's review. Present it to the user as-is — do not
re-summarise it, and do not soften or re-rank its findings.

Then, and only then, add your own short assessment: which findings you agree
with, and any you believe are wrong, with your reason. You have read the code
in this session and ChatGPT has not, so a finding that contradicts what you
know is worth contradicting.

Do not start fixing anything unless the user asks. This command answers a
question; it does not open a work item.

If the command failed, run `/chatgpt-agent:doctor` and report which requirement
is unmet rather than guessing.
