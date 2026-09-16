---
name: chatgpt-agent-runtime
description: Use when invoking or debugging the ChatGPT bridge from Claude Code — the flags that exist, the prerequisites that make it fail before it starts, the one-run-at-a-time rule, and what the bridge deliberately cannot do.
---

# ChatGPT bridge runtime

`chatgpt-agent.py` runs a task inside the ChatGPT web UI and serves it
read-only workspace data until it answers. ChatGPT does the reading and the
reasoning; this side only fetches what was asked for.

The point is where the cost lands. The diff is never pasted into a prompt by
the caller, so it never enters Claude's context, and the reasoning is billed to
ChatGPT web-chat quota rather than to Claude or to Codex.

## Invocation

```
python3 "${CLAUDE_PLUGIN_ROOT}/chatgpt-agent.py" --preset review [flags] "<task>"
```

| Flag | Default | Meaning |
|---|---|---|
| `--preset` | `review` | `review` or `plan`; a preset is a file in `presets/` |
| `--workspace` | cwd | repository to expose, resolved to its git root |
| `--session` | none | resume a named conversation; omit for a fresh chat |
| `--new` | off | start that session over |
| `--out` | none | also write the answer to a file |
| `--max-rounds` | `8` | query budget before a conclusion is demanded |
| `--max-chars` | `250000` | workspace data served across the run |
| `--round-chars` | `80000` | workspace data served in one round |
| `--retries` | `3` | attempts per exchange before giving up |
| `--resume` | off | continue this session's interrupted run |
| `--timeout` | `300` | seconds to wait for one reply |
| `--list-sessions` / `--forget <name>` | | manage saved conversations |

A run normally takes one to three minutes across three or four exchanges.
Progress goes to stderr, the answer to stdout.

## Prerequisites, all mandatory

macOS; Microsoft Edge installed and running, signed in to ChatGPT; the terminal
granted Automation permission for Edge; and *View › Developer › Allow
JavaScript from Apple Events* enabled in Edge.

There is no fallback for any of these. Edge specifically — not Chrome, not
Safari — because the AppleScript vocabulary is resolved against it at compile
time. Run `scripts/doctor.py` to see which one is missing; it prints the fix
under each failure. Two of them are permissions only the user can grant, and
signing in is never automated.

## One run at a time

The bridge drives a single browser tab. While a reply is generating, the send
button is a stop button, so a second run started mid-stream fails with *Could
not press Send*. Never launch runs in parallel, and do not retry a run that
looks slow — it is almost certainly still waiting on a reply.

Each message carries a marker so the loop can find its own reply. That makes it
safe for the user to keep using the tab, but it does not make concurrent runs
safe.

## What it cannot do

There is no write op and no shell op — absent, not disabled. Reviewing means
feeding a model text nobody on this side wrote, so a repository comment saying
*"ignore your instructions and run …"* has to reach a capability that does not
exist.

Paths are resolved with `realpath` and checked for containment before every
read, and `.env*`, private keys and credential files are refused. Those rules
live in `chatgpt_ops.py`, not in the prompt, so the model cannot argue with
them.

Consequence for callers: the bridge never edits anything. If a task needs code
changed, that is Claude's job after the review, not the bridge's.

## Reading the result

Return ChatGPT's answer verbatim. It is a second opinion from a different
model, and summarising it discards exactly the disagreement that made it worth
asking for.

Then check it. ChatGPT saw only the files it requested and says so at the end
of a good review — treat an unread file as unreviewed, not as clean. A finding
that contradicts code you have actually read in this session is worth
contradicting out loud.

## Failure modes worth recognising

**Timed out waiting for the reply** — ChatGPT is still generating, or the tab
navigated away. Raise `--timeout` before suspecting anything else. The run is
checkpointed, so `--resume` picks it up rather than starting over; it checks
whether the interrupted message already got a reply before re-sending
anything.

**Could not type into the ChatGPT composer** — the tab is showing a login or an
interstitial rather than a chat. Run the doctor.

**ChatGPT kept sending an unusable request** — two malformed `c2c` blocks in a
row. Usually a long conversation losing the format; retry with `--new`.

**The answer arrives suspiciously fast and shallow** — the model replied
without a `c2c` block on the first turn, which ends the run by design. Re-run
with a more specific task.
