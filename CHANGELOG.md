# Changelog

## 0.3.3

**Fixed: a malformed `c2c` block could end a run with an empty result.** The
corrective turn told the model `"Re-send the block, or omit it to finish."` — an
escape hatch offered at the exact moment it is confused. It took it. The next
reply had no block, the loop read that as the final answer, wrote it to `--out`
and exited 0. Measured: a review run produced a **ten-byte report** whose whole
content was an empty ```` ```c ```` fence, with no error anywhere in the log.

Three changes, all in that path:

- The correction now asks for the block again and says plainly that the run is
  not finished. It never suggests stopping.
- **Three consecutive failures stop the run** (`MAX_BAD_FORMAT`), up from two.
  A single bad block is usually a rendering stumble and worth re-asking twice;
  three in a row is a broken conversation, and the error now says how many.
- **An empty reply is no longer an answer.** `proto.is_blank_answer` treats a
  reply that is only whitespace and empty fences as blank; the loop re-asks for
  it and, after three, fails loudly rather than saving nothing as a result.
  A fence with real content still counts as an answer.

The counter still resets after any served round, so a long run is not punished
for one stumble early on.

⚠ Not fixed here: *why* the block arrives malformed. Every observed case was cut
mid-token and ended in `]()` — `toMarkdown`'s anchor output — which suggests the
reply is being captured while the code block is still rendering, before it
becomes a `<pre>`. That is a capture-timing question in `StabilityTracker` and
`CGPT.state`, and it needs its own reproduction.


## 0.3.2

**Fixed: an implement run that ran out of rounds could not save its work.**
`BUDGET_SPENT` ends a run with "give your final answer now, do not emit a c2c
block" — correct for a review, where the answer *is* the deliverable. In write
mode the deliverable is a commit, and committing needs an op, so that
instruction forbids the only way to land anything. Measured: a run spent all 24
rounds, wired three modules, fixed two startup crashes and added a real gRPC
integration test, then left 16 modified paths uncommitted because it had no way
to save them.

Write mode now gets a landing round: one last exchange whose only permitted
commands stage and commit what is already there, with new edits and further
reading explicitly refused, followed by a final answer carrying the SHA.

**Changed: `--write` raises the round ceiling to 36.** A review converges in
three to eight exchanges. An implement run reads, edits, builds, tests, seeds a
mutation and commits, and each of those is at least one exchange — the same
measured run was still mid-verification when it hit 24. `--max-rounds` still
wins when given.


## 0.3.1

**Fixed: `search` returned "no matches" whenever the task arrived on stdin.**
`_run` spawned ripgrep without setting `stdin`, so the child inherited the
parent's — and ripgrep with a non-tty stdin searches *stdin* rather than the
path it was given. Passing a long task on stdin is the documented way to do it,
so in exactly that shape every search came back empty, with no error anywhere to
say why. Both `_run` and the shell op now close stdin; a regression test drives
the search op from a process whose stdin holds data, and goes red when the fix
is removed.

Found by chasing four test failures that only appeared "under load". They were
not load: `echo x | python3 test_chatgpt_ops.py` fails, `... < /dev/null`
passes. The earlier diagnosis was wrong because the runner kept only `tail -1`
of the output.

**Fixed: the briefing contradicted itself in write mode.** The protocol opened
with "You are connected to a local workspace through a read-only bridge" and
then appended a briefing asking for edits. The opening line is now chosen by
mode, and the op list is introduced as "ops for reading the workspace" rather
than "all read-only".

**Docs: the README was 511 lines and claimed read-only in three places.**
Restructured to 214 — what it is, the three things it does, install, setup, the
honest capability section, flags, and the one-run-at-a-time rule. The bridge
internals, the `c2c` protocol, the Edge traps and the testing notes moved to
`docs/internals.md`. The plugin and marketplace descriptions, the reviewer
agent's "nothing you forward can modify the repository", and the runtime skill's
summary were all written before `--write` existed and said so.


**Fixed: an implement run inherited a review's data budget and died inside four
rounds.** Measured on the first real `--write` task: 250,000 characters gone
before a single file was edited, spent reading the files to change, the
conventions around them and one reference implementation. A review reads a diff
and concludes; an implement run reads more and then still has to write, build
and test.

`--write` now defaults to 700,000 characters per run and 120,000 per round,
and `--max-chars` / `--round-chars` still win when given. The chosen budget is
printed at the start of the run, next to the workspace facts, so the number is
visible before it matters rather than after.

**Fixed: the exhaustion notice told an implement run to do the wrong thing.**
"Conclude with what you have and say what you could not verify" is review
language. An implement run that concludes without saying what it left in the
worktree loses the work — an uncommitted edit dies with the session. Write mode
now gets its own notice: say exactly what is in the worktree and what is
missing, and do not claim the task is done.


## 0.3.0

**New: `--write`, an implement mode.** The bridge could already change a tree —
`--allow-shell` runs real commands, and a command can write a file — but
everything pointed the other way: the shell briefing told the model to *"work
in the copy, not in the workspace itself"*, the only presets were `review` and
`plan`, and the refusal text called anything unusual "out of scope for a
review". So a run that was meant to build something spent its first rounds
arguing with its own instructions.

`--write` implies `--allow-shell`, loads the new `implement` preset, and swaps
the shell briefing for one that says the workspace is the target: edit it, build
it, test it, commit on the branch that is checked out. Pushing, publishing,
merging and history rewrites stay refused, so the result is local and reviewable.
Deliberate breaks — seeding a mutation to prove a new test actually fails — still
belong in a copy under /tmp, and the briefing says so.

**New: the run states the workspace facts up front.** A measured implement run
spent two of eleven rounds discovering where `node_modules` lived, and guessed
the parent directory first. The opening message now carries what the bridge can
establish cheaply: branch and HEAD, whether the worktree is clean, whether Node
dependencies are installed and where, which of `test`/`build`/`lint`/`typecheck`
exist in `package.json`, and which toolchain markers are present. Facts only —
anything that cannot be established is left out rather than guessed.

**Changed: refusals name the role they are refusing for.** `shell_objection`
takes a `role`, so an implement run is told to commit locally rather than to
"work in a copy". The commands refused are unchanged: pushing, escalating and
reaching another host belong to no role this tool offers.

**Changed: `--preset` now defaults late.** It parses as `None` and resolves to
`implement` under `--write`, `review` otherwise, so naming a preset explicitly
still wins.

**Refactor:** the argument parser moved out of `main()` into `build_parser()`,
which is what let the defaulting rule be tested rather than asserted.


## 0.2.5

**Fixed: the first attach on a freshly navigated chat was dropped.** React has
not bound its handler to the file input yet, so the change event goes nowhere -
and nothing downstream notices, because `input.files` still holds the file that
nobody consumed. The fail-closed gate from 0.2.2 caught it correctly and
reported `chip-gone`, but only after spending a whole round.

`attach_payload` now re-dispatches, up to three times, and treats the chip
appearing as the proof of success rather than the assignment returning ok. Every
retry is announced on stderr: a tab that needs two goes is the one signal that
says it was cold, and swallowing it would hide exactly that.

Verified by mutation - removing the retry loop, silencing the retry, treating
`ok` as success, and retrying past a genuine attach error each turn the suite
red.

## 0.2.4

**Fixed: the guard on the typed note watched the constant, not the string.**
0.2.3 removed the filename from `ATTACHED_NOTE` and asserted that the constant
no longer contains it. But the name is reinjectable at the concatenation site,
and a mutation appending `" The file is " + name` there left the constant
untouched — so the whole suite stayed green while the filename was back in the
turn, making the post-send check a tautology again. The test now drives `send()`
with a faked bridge and asserts on the text that actually leaves the function,
which catches any route back in.

**`sentWithAttachment` is structural too.** It scanned the turn's `textContent`,
so it depended on our own typed text staying clean. It now looks for the file
tile ChatGPT renders — observed live as DIV and BUTTON carrying
`aria-label="<name>"` — sharing one `labelledWith` helper with `hasChip`.
Neither guard reads prose any more.

**Verified by mutation rather than by assertion.** Three mutations were seeded
and each turns the suite red: reinjecting the name at the concatenation site (1
Python failure), restoring the `innerText` fallback (2 JS failures), and
reverting the turn scan to `textContent` (2 JS failures). That is the check
missing from 0.2.1 through 0.2.3 — each of those was accepted because the happy
path worked, and none demonstrated that the guard could fail.

## 0.2.3

**Fixed: both attachment guards were satisfied by the plugin's own words.** The
note typed alongside an upload said *"the attached file c2c-db93a8e7.txt"*, and
both guards search the turn for that filename. `sentWithAttachment` found it in
our own sentence, so it could never fail. `hasChip` had an `innerText` fallback
and the composer held the same sentence, so the fail-closed gate 0.2.2 built
could never close. A turn of 210 characters carrying no file card passed both.

Two changes, either of which fixes it, and both are in:

- The note no longer names the file. Every occurrence of the name in a turn or
  a composer now comes from the attachment card. The model never needed it — it
  has to open the attachment, not look it up by name.
- `hasChip` is structural only. The `innerText` fallback is gone: a name in
  prose is not an attachment, and that was true even before our own note made
  it worse.

**The pattern is worth naming, because this was the third in a row.** Each fix
was accepted on evidence the tool itself produced — first a guard reading an
absence, then a guard reading a string it had written, then that guard's patch
doing the same. The common cause was not any of the three bugs: it was that
every fix was verified only against the case where things work. None had a test
proving the guard could fail. This release adds those, and the 0.2.2 test that
asserted the `innerText` fallback — encoding the hole as intended behaviour —
is inverted.

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
