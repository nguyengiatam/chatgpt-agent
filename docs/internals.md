# Internals

How the bridge actually works, and the traps found the hard way. The front door
is [../README.md](../README.md); this file is for changing the tool rather than
using it.

## How it works

```
chatgpt-cli.py ──argv──> chatgpt_bridge.applescript ──> Microsoft Edge
                                                            │
                          chatgpt_dom.js ──injected────────>┘
```

| File | Role |
|---|---|
| `chatgpt-cli.py` | CLI, tab discovery, poll loop, error messages |
| `chatgpt_bridge.applescript` | Fixed bridge to Edge; every dynamic value arrives via `argv` |
| `chatgpt_dom.js` | Runs in the page: fills the composer, reads the reply, converts DOM to Markdown |

A run is: find the ChatGPT tab → check the composer exists → insert the prompt →
click Send → poll until done → print Markdown.

**The reply is found by anchoring on our own prompt**, not by taking the newest
assistant message. The tab is shared: you may type in it yourself, and a second
CLI run can land between our send and our read. Matching the user message whose
text equals the prompt we just sent, then reading the assistant message after
it, is what keeps someone else's answer from being printed as ours.

**Completion needs the reply to exist and to stop growing** for two consecutive
polls. The stop button is page-wide, so it only counts against us while our
reply is still the newest message — otherwise another run streaming into the
same tab would block us forever.

**The prompt is never interpolated into script source.** It crosses three
quoting layers (shell → AppleScript → JavaScript), so it is encoded once with
`json.dumps` into a JavaScript string literal and passed as an `argv` item;
`subprocess` is called with an argument list, never a shell string. Quotes,
backslashes, backticks, `${...}`, newlines and emoji all survive intact.

## Testing the shell tool

```bash
node test_chatgpt_dom.js
python3 -m unittest discover -p 'test_*.py' < /dev/null
```

`< /dev/null` matters: one test reads real stdin, so without a terminal and
without a redirect the suite waits for input that never comes.

Some of the Python tests drive the real AppleScript against a running Edge, and
skip when Edge has no open window. They are not optional extras: AppleScript
name collisions cannot be caught any other way. Inside a `tell application`
block a bare word is resolved against the **app's dictionary first**, so a
variable named `mode` silently became an Edge property (error `-1728`), and
`tab` silently became Edge's `tab` *class*, emitting the literal word `"tab"`
as a column separator. Both compiled cleanly under `osacompile`.

## Known fragility

The CSS selectors are the brittle part: a ChatGPT redesign breaks them. They
are all in the `SELECTORS` object at the top of `chatgpt_dom.js`, so it is a
one-place fix. To find the new values, open the ChatGPT tab and inspect the
composer and its send button.

Two traps worth remembering when you do:

- **The send button does not exist while the composer is empty.** Type
  something first, then inspect. `data-testid="send-button"` was verified live
  on 2026-09-16.
- **`aria-label` is localised** — on a Vietnamese UI the send button reads
  *"Gửi câu lệnh"*. Match `data-testid`, never visible text.

### Parallel runs: stable ids and kernel ownership

Tab positions are discovery data, not identity. Opening, closing or dragging a
tab can renumber every tab after it, so a `(window index, tab index)` captured
by one process is already stale by the time another browser call uses it. Edge's
integer tab `id` is stable across those reorderings, which is why `list` returns
both the human-useful positions and the id, while every later bridge operation
accepts the id.

There is a second race hiding inside that rule. Resolving an id to a window/tab
position in one `osascript` invocation and executing against that position in a
second invocation would merely move the stale-index window between two process
calls. Each bridge mode therefore receives the stable id, walks Edge's live tab
objects, matches `id of tb`, and performs the requested operation **inside that
same `osascript` invocation**. Reordering before or after that invocation is
irrelevant; there is no saved positional address to decay.

A stable tab is not enough for exclusivity because two tabs can show the same
ChatGPT `/c/<id>` conversation. A run therefore claims both its tab and, once it
is known, the conversation id for the whole run. Claims use non-blocking
`flock(LOCK_EX | LOCK_NB)`: a contender either owns the resource immediately or
fails immediately with metadata naming the holder. It never waits.

The lock is deliberately kernel-held rather than inferred from a pid file. Pids
can be reused, metadata can be corrupt, and a contender cannot safely decide
that another process is dead from bookkeeping alone. An open file description
held by the process gives the kernel that job instead: process exit, including
`SIGKILL`, releases the lock automatically. The `.lock` file may remain on disk,
but it has no authority once unlocked; `--prune` reports and can remove those
abandoned files separately from stale sessions and dead checkpoints.

Likewise, `<code>` elements carry **no** `language-*` class. The language is a
header label beside the Run button, which is why `chatgpt_dom.js` recovers it
by finding the lone bare word near the code that is not a button caption.

---

## Layers

| File | Responsibility |
|---|---|
| `chatgpt-agent.py` | the loop, sessions, CLI |
| `chatgpt_protocol.py` | the `c2c` wire format — parsing requests, rendering results |
| `chatgpt_ops.py` | what may be read, and the fence around it |
| `presets/*.md` | what the task is; nothing else knows about "review" |

A new task type is a new file in `presets/`. It never touches the other three.

## The c2c format

ChatGPT asks by emitting one fenced block tagged `c2c`:

````
```c2c
{"ops":[{"op":"read","path":"src/pay.py"},{"op":"search","pattern":"calc_fee"}]}
```
````

**A reply with no block is the final answer.** That rule was chosen so the
likely failure is the harmless one: a model that forgets the block ends the run
early, where a model that forgets a "done" marker would hang it forever.

Read-only ops: `read`, `list`, `search`, `git_diff`, `git_log`, `git_show`.

When the repository has a [GitNexus](https://github.com/looptech-ai/gitnexus)
index, six more answer in one call what search and read approximate over
several rounds: `graph_status`, `impact`, `context`, `trace`, `graph_query`,
`detect_changes`. They are optional — without an index they say so and the
model falls back to search.

## Testing the agent

```bash
node test_chatgpt_dom.js
python3 -m unittest discover -p 'test_*.py' < /dev/null
```

`< /dev/null` matters: `ReadPromptTest` reads real stdin, so without a terminal
and without a redirect the suite waits for input that never comes.

## Why Edge, and only Edge

The AppleScript verb this tool depends on, `execute tab N of window M
javascript`, is Chromium vocabulary: AppleScript resolves it against the
application's dictionary **at compile time**, not at run time. Three things
follow, each verified on a machine with Edge and no Chrome:

- A literal `tell application "Google Chrome"` block fails to compile at all
  when Chrome is absent — error `-2741`, before a single line runs. A script
  with a branch per browser is therefore not possible: one missing browser
  breaks the whole file.
- `using terms from application "Google Chrome"` does not rescue it. With
  Chrome absent the terms silently fail to load and `execute … javascript` is
  then a syntax error.
- `using terms from application "Microsoft Edge"` — an installed browser —
  followed by `tell application <variable>` *does* work, returning `42` from
  `1+41`.

So supporting the whole Chromium family is possible, but only by substituting
an installed browser's name into the `using terms from` line before running.
That is deliberately not done here: it reintroduces string interpolation into
the AppleScript source, which this bridge otherwise refuses to do, in exchange
for code paths that nobody has run.

Safari is a separate implementation, not a flag: it uses `do JavaScript in
document`, different syntax entirely. Firefox has no AppleScript scripting and
never will.

## Large payloads go as attachments

Typing into the ChatGPT composer costs time **quadratic in the number of
newlines**, not in characters. Measured on the live composer with the same
40,000 characters throughout:

| lines | insert time |
|---|---|
| 1 | 0.1s |
| 50 | 0.5s |
| 200 | 2.4s |
| 700 | 16.7s |
| 2000 | 116.3s |

`execCommand("insertText")` makes ProseMirror build one block node per line
inside a single transaction. A file listing or a diff is the worst possible
shape, and a big one freezes the tab outright. So above 120 lines the payload is
uploaded as a `.txt` instead and the composer gets a one-line pointer. The same
3000-line payload that would take three minutes to type attaches in 0.1s.

**Nothing is written to this machine.** The file is built in the page from data
already in memory; there is no temp file to clean up locally.

**The upload does persist in your ChatGPT account.** It belongs to the
conversation it was sent in, and stays there. A run that attaches on four rounds
leaves four files, and repeated names get numbered — `c2c-8fd3427b(3).txt`.
Deleting the conversation takes its attachments with it, which is the argument
for `--session`: everything a project uploads stays in one conversation you can
delete in one action. `--forget` only drops this tool's bookmark; it does not
touch anything in ChatGPT.

The threshold is `ATTACH_LINES` in `chatgpt-cli.py`. Raising it means fewer
uploads and slower turns; the table above is the trade.

## When a run is interrupted

Everything served stays in the ChatGPT conversation forever, so the run has two
data ceilings as well as a round count: `--round-chars` stops one greedy turn
(six ops at the per-op ceiling would be 384 KB in a single message) and
`--max-chars` stops the slower death across many polite rounds. Running out of
context does not announce itself — the model starts forgetting the protocol
instead, which looks like a parser bug. The budget is printed each round.

Each exchange is retried `--retries` times. Sending and waiting are retried
separately: once a message has been posted the retry goes back to waiting on
its marker, never to posting it again.

If a run still dies, the conversation URL and a resume command are printed, and
a checkpoint was written before the failure:

```
conversation: https://chatgpt.com/c/...
resume with: --resume --session app
```

`--resume` reopens that conversation and asks whether the interrupted message
already has a reply — if it does, the run continues from there without
re-sending it. Runs are checkpointed under their `--session` name, or under
`_last` when none was given, in `~/.chatgpt-agent/runs/`.

A checkpoint is removed when its run ends, which means a run that never ends —
killed, crashed, given up on — leaves one behind. Every run therefore prunes
checkpoints and session bookmarks older than `STATE_TTL_DAYS` (14) before
writing its own. `--prune` exposes the same plan on demand and, separately,
asks the kernel which claim files are no longer locked. Its output labels those
as `claim` rows rather than folding them into session/checkpoint age cleanup.
`prune_plan()` lists age-based state, `prune_apply()` deletes it, and the CLI
only mutates either category with `--yes`. The current run's name is held back,
so housekeeping can never sweep the checkpoint a `--resume` is about to read.

