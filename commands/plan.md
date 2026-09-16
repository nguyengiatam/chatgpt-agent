---
description: Have ChatGPT read the repository and write an implementation plan
argument-hint: "[--workspace <dir>] [--session <name>] [--new] [--out <file>] [--max-rounds <n>] <what to plan>"
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/chatgpt-agent.py" --preset plan $ARGUMENTS`

The output above is ChatGPT's implementation plan. Present it to the user
verbatim.

Then check it against the repository before anyone acts on it. ChatGPT saw only
the files it asked for, so verify that the paths and symbols it cites actually
exist and that the steps it proposes fit the code as it really is. Report any
step that does not survive that check.

Do not begin implementing. If the user wants the plan executed, that is a
separate decision they make after reading it.
