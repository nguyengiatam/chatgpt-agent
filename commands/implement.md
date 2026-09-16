---
description: Have ChatGPT implement a change in the repository and commit it
argument-hint: "[--session <name>] [--workspace <path>] <what to build>"
---

Run the ChatGPT bridge in **implement mode**: ChatGPT reads the repository,
edits it, runs the build and the tests, and commits on the current branch.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/chatgpt-agent.py" --write $ARGUMENTS
```

`--write` turns on the command op and loads the `implement` preset, so the task
text only has to say what to build.

Before running it:

- **Check out the branch the work belongs on.** The run commits where it finds
  itself; it does not create branches.
- **Say what is out of scope.** The preset already forbids unrelated fixes, but
  naming the boundary in the task is what keeps a small change small.
- **Point at the spec if there is one.** An absolute path to a brief in the repo
  is cheaper than restating it, and the model can read it.

What the run guarantees, and what it does not: pushing, publishing, merging and
history rewrites are refused, so the result stays local for you to review. The
tests it runs are the repository's own — a green suite is evidence about the
suite, not about the change. Read the diff before you keep it.

One run at a time: the bridge drives a single browser tab.
