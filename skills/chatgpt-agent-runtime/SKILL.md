---
name: chatgpt-agent-runtime
description: Use when invoking or debugging the ChatGPT bridge from Claude Code — the flags that exist, prerequisites, parallel-run ownership, which tasks fit the bridge at all, and what --allow-shell does and does not bound.
---

# ChatGPT bridge runtime

`chatgpt-agent.py` runs a task inside the ChatGPT web UI and serves it
workspace data until it answers — read-only by default, plus one command-running
op when the caller passes `--allow-shell`, and the workspace itself as the target
when the caller passes `--write`. ChatGPT does the reading and the
reasoning; this side only serves what was asked for.

The point is where the cost lands. The diff is never pasted into a prompt by
the caller, so it never enters Claude's context, and the reasoning is billed to
ChatGPT web-chat quota rather than to Claude or to Codex.

## Invocation

```
python3 "${CLAUDE_PLUGIN_ROOT}/chatgpt-agent.py" --preset review [flags] "<task>"
```

| Flag | Default | Meaning |
|---|---|---|
| `--preset` | `review`, or `implement` with `--write` | `review`, `plan` or `implement`; a preset is a file in `presets/` |
| `--workspace` | cwd | repository to expose, resolved to its git root |
| `--session` | none | resume a named conversation; omit for a fresh chat |
| `--new` | off | start that session over |
| `--out` | none | also write the answer to a file |
| `--max-rounds` | `8`, `36` with `--write` | query budget before a conclusion is demanded |
| `--max-chars` | `250000`, `700000` with `--write` | workspace data served across the run |
| `--round-chars` | `80000`, `120000` with `--write` | workspace data served in one round |
| `--allow-shell` | off | add the op that runs commands on this machine |
| `--write` | off | implement mode: edit, test and commit the workspace (implies `--allow-shell`, preset `implement`) |
| `--retries` | `3` | attempts per exchange before giving up |
| `--resume` | off | continue this session's interrupted run |
| `--timeout` | `300` | seconds to wait for one reply |
| `--list-sessions` / `--forget <name>` | | manage saved conversations |
| `--prune` | off | list dead checkpoints, stale sessions and abandoned claim files, then exit |
| `--days` / `--all` / `--yes` | `14` / off / off | with `--prune`: the age line, ignore age, actually delete |

A run normally takes one to three minutes across three or four exchanges.
Progress goes to stderr, the answer to stdout.

## State, and what clears it

`~/.chatgpt-agent/sessions.json` holds one record per session name
(`{"url", "updated"}`), and `~/.chatgpt-agent/runs/<name>.json` holds the
checkpoint `--resume` reads. Every run prunes both of whatever is older than 14
days before writing its own state; `--prune` does it on demand and **lists by
default**, deleting only with `--yes`. Never pass `--yes` on your own
initiative — a checkpoint is what an interrupted run resumes from, and a
bookmark is the only record of which conversation a name points at.

A run being checkpointed and the session it is using are held out of every
prune, so housekeeping cannot sweep the run in flight.

## Prerequisites, all mandatory

macOS; Microsoft Edge installed and running, signed in to ChatGPT; the terminal
granted Automation permission for Edge; and *View › Developer › Allow
JavaScript from Apple Events* enabled in Edge.

There is no fallback for any of these. Edge specifically — not Chrome, not
Safari — because the AppleScript vocabulary is resolved against it at compile
time. Run `scripts/doctor.py` to see which one is missing; it prints the fix
under each failure. Two of them are permissions only the user can grant, and
signing in is never automated.

## Parallel runs and ownership

Runs may execute in parallel. A run claims its stable Edge tab id and the
conversation shown in that tab, and keeps those claims for the whole run.
Conversation ownership is exclusive even when the same conversation is open in
two different tabs. A contender never waits: if the conversation it needs is
owned, it fails immediately and names the holder.

One-shot questions can route around a busy *tab*: when the first candidate is
claimed they try another ChatGPT tab, or open one. They do not route around a
busy *conversation*. Named sessions likewise keep their conversation identity
and reopen/rebind it when the saved tab binding is stale.

Claims are non-blocking kernel `flock` locks. Metadata in the claim file is only
for the diagnostic; it does not decide whether a holder is alive. The kernel
releases ownership when the process ends, including `kill -9`, so there is no
manual unlock step. `--prune` reports leftover unlocked claim files separately
from dead checkpoints and stale sessions.

All runs still share one Edge instance. Several streaming tabs consume more
machine resources than one and there is no enforced concurrency limit. A run
also cannot outlive a machine sleep longer than `--timeout`; for long unattended
runs, use `caffeinate -dimsu` around the invocation to keep macOS awake.

Each message still carries a marker so the loop attributes a reply to the prompt
it sent. Ownership prevents another run from sharing the conversation; the
marker remains the second line of defence against manual activity in the tab.

## What it can and cannot do

Default: read only. Paths go through `realpath` and a containment check before
every read, and `.env*`, private keys and credential files are refused. Those
rules live in `chatgpt_ops.py`, not in the prompt, so the model cannot argue
with them. The bridge edits nothing; if a task needs code changed, that is
Claude's job after the review.

`--allow-shell` adds one op that runs real commands on the machine, as the user
who started the run. Pass it only when the task genuinely needs execution —
running a suite, seeding a mutation, reproducing a failure. Every command is
printed to stderr before it runs, and `shell_objection` refuses deletes,
escalation, publishing, remote access and credential reads. Those refusals
bound the role, not the blast radius: they catch drift, not evasion.

## Tasks that do not fit

Before writing a task with a pass/fail gate in it, check which of these it is.

**Fits read-only:** judging a diff, finding call sites, checking whether tests
exist and what they cover, tracing how a change propagates, writing a plan.

**Needs `--allow-shell`:** anything whose answer is an observation rather than a
reading — did the suite go red, does this reproduce, what does the build say.

**Needs `--write`:** changing the workspace itself — implementing a brief,
adding the tests that pin a behaviour, carrying out a refactor. `--write`
implies `--allow-shell`, loads the `implement` preset and tells the model the
workspace is the target: edit it, build it, test it, commit on the branch that
is checked out. Pushing, publishing, merging and history rewrites stay refused,
so the result is local and reviewable.

**Does not fit at all:** anything whose outcome has to leave this machine, and
anything you would not review before keeping. The run commits; it does not get
the last word on whether the change is right.

For a gate — "GO only if these four mutants go red" — prefer splitting it even
when shell is available: have ChatGPT *design* the mutations, let Claude Code
run them, then feed the results back with `--session` for the verdict. Not for
safety, but because the party being gated should not also be the party holding
the evidence. The `go test` output is then a record anyone can re-check.

⚠ **An implement run ends with a landing round.** When the rounds run out, write
mode gets one last exchange for staging and committing what is already there —
because the review ending ("give your final answer, no c2c block") forbids the
only way to save the work, and a run that obeys it leaves everything in the
worktree.

⚠ **An implement run's real ceiling is the data budget, not `--max-rounds`.**
The first one measured spent a review's 250,000 characters inside four rounds —
reading the files to change, the conventions, and one reference implementation —
and never reached an edit. `--write` raises the default to 700,000, but the
prompt still decides: name **one** reference implementation, not three, and say
to read it with `sed -n` ranges rather than `cat`. Watch the `[spent/total]`
counter in the log.

Continuing that run in the same `--session` does not help: the budget resets per
run, but every result already served stays in the ChatGPT conversation, so the
next run starts near the model's context limit. Start a fresh chat and carry the
previous run's findings across in the prompt.

The same split applies to `--write`: the run that writes the code is not the
run that certifies it. Take the commit it produces and gate it separately —
re-seed the mutations yourself against its commit and confirm the new tests go
red. Measured on one implement run, that check is what turned "it says the
tests pin this" into evidence.

## Attachments

A turn wider than 120 lines is uploaded as a `.txt` rather than typed, because
composer insertion costs quadratic time in newlines — the same 40k characters
took 0.1s on one line and 116.3s across two thousand, which freezes the tab.
The progress log says when this happens.

Nothing lands on the local disk. The upload does stay in the ChatGPT
conversation, so a long run leaves several files in the account; deleting that
conversation removes them, which is a reason to give related work one
`--session`.

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
