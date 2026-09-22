# Changelog

## Unreleased

**A throttled run now says why instead of silently slowing down.** While waiting for a reply, the CLI checks every 15 seconds whether its run tab stopped being active or its run window was minimized, warns once through the run log/stderr, and never changes browser focus.

**A `--resume` no longer stalls on a window it left behind.** Resuming used to drive the tab it had bound on a previous run, and that tab is a background tab in a small tool window that has long since fallen behind - Chromium throttles it, the Apple Event times out, and the run can spend its whole budget without landing a single edit. Every run now opens the conversation in a window of its own, the same shape a fresh run already drives reliably, and the old tool window is closed afterwards only once ownership is proved and the new window is up.

**`doctor` stops blaming the Edge JavaScript setting for every bridge failure.** The menu-bar fix is attached only when the refusal is a genuine JavaScript denial; any other failure now says the probe could not be classified and prints the whole error, not just its first line.

**Every c2c block in a reply is served, in order.** `extract_ops()` returned on the first block it met, so a reply carrying two lost the second without a word - the model got results for half of what it asked and either re-asked or carried on believing it had data it never received. All tagged blocks are now gathered into one op list; an untagged request-shaped block is still served only when no tagged block is present, and a broken block still raises.

## 0.7.1

**A reply is read only once ChatGPT says it is done.** The Copy / thumbs action
bar that appears under a finished assistant message is now the positive
end-of-message signal, and the reply is re-read ten seconds after that signal
first shows before it is handed back. Earlier releases trusted the stop button
plus two stable polls, but ChatGPT streams in bursts and the stop button is
absent during the gaps, so an ordinary mid-stream pause could be read as the
whole answer - the run then acted on half a reply.

**A missing signal degrades to slow, not to a hang.** If the action bar never
appears - a ChatGPT redesign, say - a reply that stays stable and unstreaming
for sixty seconds is accepted anyway, with a warning that names why.

## 0.7.0

**Every run gets its own small Edge window, and never borrows yours.** A run
opens its ChatGPT tab in a window of its own, sized small and parked in a corner,
cascaded so parallel runs do not stack on one spot. Earlier releases opened the
tab in whatever Edge window was in front — usually the one you were working in —
and two runs sharing that window could time out each other's Apple Events
(`-1712`). The run closes its window when it finishes; an interrupted run leaves
it open for `--resume`.

**Your own ChatGPT tabs are left alone.** The tool only drives tabs it opened
itself (recorded in `~/.chatgpt-agent/windows.json`, written under a file lock).
A ChatGPT tab you opened by hand is never picked up, typed into or closed. The
cascade counts only the tool's own live windows, so your windows never shift it.

**Tab claims are taken once, where the tab is opened.** A run that has to rebind
after its tab disappears now really releases the dead tab, so a parallel run is
no longer refused a tab that no longer exists.

**`doctor` works again.** It had crashed with `AttributeError` since the tab
bookkeeping changed; it now reports the ChatGPT tab by its stable id, and when
JavaScript cannot run in that tab the sign-in check says so instead of claiming
there is no tab. `doctor` has a test for the first time.

## 0.6.0

**Runs can now happen in parallel, one per conversation.** A review, a plan and
an implementation no longer have to take turns just because they share one
Edge. Each run owns the conversation it is using for its whole lifetime; if
another process asks for that same conversation it fails immediately and names
the holder instead of waiting behind a run that may last twenty minutes.

The binding no longer depends on where a tab happens to sit in a window. Edge's
stable tab id follows the tab through opens, closes and reordering, while the
conversation claim prevents two different tabs showing the same `/c/<id>` from
being treated as independent work. One-shot questions can skip a claimed tab
and use another, but an already-owned conversation is still refused.

**Ownership dies with the process.** Claims are kernel-held locks, so Ctrl-C,
a crash or even `kill -9` releases the conversation without a stale-pid guess or
a cleanup ritual. The small claim files can outlive their locks; `--prune` now
reports those abandoned files separately from stale sessions and dead
checkpoints.

Parallel runs still share one browser: several streaming tabs are heavier than
one, and there is no concurrency limit. Long unattended runs also need the
machine awake — sleeping longer than `--timeout` can throttle a tab until its
reply freezes — so `caffeinate -dimsu` is the practical wrapper for those runs.


## 0.5.0

**Both state stores now have a floor.** `~/.chatgpt-agent` was append-only in
practice: `clear_run()` fires when a run ends cleanly, but a run killed
mid-flight — a malformed `c2c` block, a closed lid, a Ctrl-C — left its
checkpoint behind for good, and a session bookmark was never dropped at all. A
few weeks of daily use on one project left six dead checkpoints, one of them
28 KB because it carries the pending message, and 26 bookmarks that nothing
would ever have removed.

Every run now prunes what is older than 14 days before writing state of its own,
and `--prune` does it on demand. It **lists by default** and deletes only with
`--yes`: a checkpoint is exactly what `--resume` needs, and a bookmark is the
only record of which conversation a session name points at. `--days <n>` moves
the line, `--all` ignores age.

Nothing in flight is swept. The plan holds back the run being checkpointed right
now and the session it is using, so a `--resume` after a two-week gap still
finds its checkpoint.

**Session entries are records, not URL strings.** A bookmark is now
`{"url": …, "updated": …}`, and reusing a session restamps it, so a conversation
returned to every week never looks stale. Stores written by earlier versions are
still read, and are dated from the file's own mtime — without a stamp from
somewhere, an old bookmark could never age out. `--list-sessions` prints that
age as a third column.

New: `/chatgpt-agent:clean`.



## 0.4.0

**Kiro CLI integration**, and **one version across every manifest**.

The three manifests had drifted apart: `plugin.json` (Kiro) said `0.4.0`,
`.claude-plugin/plugin.json` said `0.3.5`, and `.claude-plugin/marketplace.json`
had been left at `0.3.2` for three releases. A stale marketplace version is the
one that bites in practice — it is what a fresh install resolves. All three now
read `0.4.0` and move together.

This release carries the Kiro skills and docs added in #1 on top of the 0.3.3 →
0.3.5 bridge fixes.



## 0.3.5

**Fixed: a message's chrome could be read as its answer.** `state()` fell back to
the whole assistant turn when it found no `.markdown` body. That turn exists in
the DOM *before* its answer is rendered, and the bare node carries interface
text — on a Vietnamese Edge, the label "Bài viết". Stable across polls and not
empty, so the tracker called it finished and the run saved it: a review that had
already done seven rounds of real work ended with a **12-byte report** reading
`Bài viết`.

`state()` no longer falls back. No markdown body means no answer yet, and the
empty text it now reports is what already tells the caller to keep waiting.

This is the same family as 0.3.4 — sampling the DOM before it is ready — but a
different symptom, and 0.3.4's guards could not catch it: the text parses as an
answer, it just isn't one. The blank-answer check added in 0.3.3 does not fire
either, because `Bài viết` is not blank.

⚠ Locale is what made this visible, not what caused it: the fallback returns
chrome in any language. It had simply never been sampled that early before.



## 0.3.4

**Fixed: replies were read while the code block was still being drawn.** 0.3.3
stopped a malformed block from ending a run quietly; this fixes why the block
was malformed. It was never the model: sampled after the run failed, the very
same three messages held complete, valid JSON.

Two time-dependencies, both measured on a live conversation:

- **The body is captured mid-rebuild.** `StabilityTracker` returns as soon as
  the message's `textContent` holds still for two polls, but ChatGPT keeps
  restructuring the code block after the text plateaus. Every failed capture was
  cut mid-token — `{"ops":[{"op":"git`, `{"ops":[{"`, three runs in a row.
- **The language label lands late.** ChatGPT renders it as a header element
  beside the code, not as a class on `<code>` (measured: `class` is empty).
  Immediately after a page load the same node rendered as an untagged fence; a
  moment later, as ```` ```c2c ````. An untagged block is not recognised as a
  request, so a whole round is lost.

- `proto.is_still_rendering` treats a c2c fence whose body does not parse, or an
  odd number of fences, as "not finished". `wait_for_reply` resets the stability
  run and keeps polling instead of returning a half-drawn message.
- `extract_ops` now also accepts an **untagged** fence whose body is exactly the
  protocol shape — a dict with a non-empty `ops` list. A tagged block still wins,
  and an untagged block of anything else is left alone.

Together with 0.3.3 the failure is contained twice over: the reply is no longer
read early, and if a bad block still arrives it is re-asked rather than saved.



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
honest capability section, flags, and the former serial-run guidance. The bridge
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
