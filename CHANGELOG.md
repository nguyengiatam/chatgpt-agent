# Changelog

## 0.2.2

**Fixed: a turn could be sent without the file it referred to.** The gate before
Send waited for the send button to unblock, which is a negative signal - it
clears both when the upload finished and when ChatGPT dropped the attachment,
and those are indistinguishable from outside. Observed live: a chip present at
1s and 3s, gone by 6s, button reading free throughout. The message then went
with a pointer to a file nobody had, and whatever came back was an answer about
nothing.

The gate is now positive: the chip bearing our filename must still be on screen
*and* the button free. It fails closed - a chip that never settles times out and
nothing is sent, so a future UI change surfaces as a timeout rather than as a
silently empty turn.

**Added a structural check after Send.** The sent user turn must carry the
filename, or the run stops with an error naming it. Asking the message rather
than reading the model's wording keeps this independent of what ChatGPT says,
and of the language it says it in - and it stops an infrastructure failure from
being mistaken for a final answer, since a reply with no c2c block otherwise
ends the run by design.


## 0.2.1

**Fixed: a wide payload froze the ChatGPT tab.** Reported from the first real
run. Insertion cost is quadratic in NEWLINES, not characters — measured on the
live composer with 40,000 characters throughout: 1 line 0.1s, 200 lines 2.4s,
700 lines 16.7s, 2000 lines 116.3s. ProseMirror builds one block node per line
in a single transaction. Payloads over 120 lines now upload as a `.txt` and the
composer gets a pointer; the same 3000-line payload attaches in 0.1s.

**Fixed: `submit()` could click a dead button and report success.** ChatGPT
disables the send button with `aria-disabled`, while the DOM `disabled` property
stays `false` throughout — including for the seconds an upload takes. Reading
only `disabled` meant clicking a button React considered dead: the composer
cleared, nothing sent. This was silently wrong beyond attachments.

**Fixed: file-citation chips leaked into answers.** With an attachment in the
turn, ChatGPT renders citation buttons inside its reply, and the DOM-to-Markdown
pass pulled their text into the middle of sentences. Buttons are now skipped;
they are always chrome, never prose.

**Removed a fallback that never worked.** `insert()` fell back to a synthetic
`ClipboardEvent` when `execCommand` failed. Measured: it returns in 0.1s and
leaves the composer empty, because ProseMirror ignores an untrusted paste. It
looked like a safety net and caught nothing.

Attachments stay in the ChatGPT conversation, not on your machine. See
"Large payloads go as attachments" in the README for what that means for
cleanup.

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
