# Changelog

## 0.2.0

**`--allow-shell`, off by default.** Some questions cannot be answered by
reading — *does this suite actually catch this bug?* is an observation, not a
reading. The flag adds one op that runs commands on your machine, as you, with
`cwd` wherever the model asks.

Three things bound it, and they are not equal:

- it does not exist unless you pass the flag, which is the control that matters;
- every command is printed before it runs, so an unattended loop still leaves a
  record;
- `shell_objection` refuses recursive deletes, escalation, publishing, remote
  access, credential reads and destroying local work.

That last one bounds the **role**, not the blast radius. It catches a model that
drifts or blunders. It does not stop anyone who spells a command differently,
and the command runs with your privileges either way.

**If you read the 0.1.0 docs, one claim has changed.** They said the bridge has
no shell op, "absent, not disabled", and that an instruction hidden in a
repository could not reach a capability that did not exist. That is still true
with the flag off, and no longer true with it on. Every place that made the
claim has been rewritten rather than left to mislead.

**Six GitNexus ops:** `graph_status`, `impact`, `context`, `trace`,
`graph_query`, `detect_changes`. On a repository of any size, finding call sites
by search and read costs rounds and can still miss one; these answer in a single
call. Optional — without an index they say so and the model falls back to
search.

**SKILL.md gains "Tasks that do not fit"**, splitting work into what suits
read-only, what needs `--allow-shell`, and what does not belong on the bridge at
all. Added because a task was written against a bridge that could not run it,
and nothing in the docs would have warned its author in advance.

For a pass/fail gate, that section recommends splitting the work even when shell
is available: ChatGPT designs the mutations, the caller runs them, the results
come back for the verdict — so that the party being gated is not also the party
holding the evidence.

**Fixed:** the character budget and the hit count were briefly the same variable
in `search`, clipping results to fifty characters.

## 0.1.0

First release. `chatgpt-cli.py` sends one prompt to the ChatGPT web UI through
an Edge window you are already signed in to, using AppleScript rather than
synthesised keystrokes. `chatgpt-agent.py` turns that into a loop: ChatGPT asks
for files through a `c2c` wire format, this side serves them, and the exchange
repeats until it answers.

Ships as a Claude Code plugin: `/chatgpt-agent:review`, `:plan`, `:ask`,
`:doctor`, `:sessions`, a forwarding agent and a runtime skill.

macOS and Microsoft Edge only.
