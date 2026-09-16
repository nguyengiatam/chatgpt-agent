# ChatGPT Agent

*(repository `chatgpt-agent`; the shell tool inside it is `chatgpt-cli.py`)*

Talk to the **ChatGPT web UI** from the shell, through the Microsoft Edge
window you are already signed in to. Built for accounts that browser-automation
tools (Playwright, Selenium, headless Chrome) cannot log in to.

It drives the page with Edge's AppleScript `execute javascript` command — not
by synthesising keystrokes — so it never needs window focus, never touches your
clipboard, and cannot type into the wrong tab.

> **macOS only.** This drives Microsoft Edge through Apple Events. There is no
> Linux or Windows equivalent, and no other browser is supported — Chrome and
> Safari both fail, for different reasons documented below. Edge must be
> running and already signed in to ChatGPT, and two permissions must be granted
> by hand. Run `python3 scripts/doctor.py` to check all of it at once.

## Install as a Claude Code plugin

Source: **https://github.com/nguyengiatam/chatgpt-agent** — worth reading first,
since a plugin runs on your machine.

```
/plugin marketplace add https://github.com/nguyengiatam/chatgpt-agent.git
/plugin install chatgpt-agent@chatgpt-agent-marketplace
```

The GitHub shorthand works too, and pins to a tag if you want one:

```
/plugin marketplace add nguyengiatam/chatgpt-agent
/plugin marketplace add nguyengiatam/chatgpt-agent@v0.2.5
```

Then `/chatgpt-agent:doctor` before anything else — this plugin has more hard
requirements than most, and the doctor names whichever one is missing.

To update later: `/plugin marketplace update chatgpt-agent-marketplace`.

| Command | Purpose |
|---|---|
| `/chatgpt-agent:review` | review a diff, branch or working tree |
| `/chatgpt-agent:plan` | write an implementation plan |
| `/chatgpt-agent:ask` | one-shot question, no repository access |
| `/chatgpt-agent:doctor` | check the prerequisites |
| `/chatgpt-agent:sessions` | list or forget saved conversations |

The `chatgpt-reviewer` agent forwards the same thing from a subagent when the
main thread should not spend context on it.

## Requirements

- macOS, Microsoft Edge, signed in to ChatGPT
- Python 3 (standard library only — nothing to install)
- Node is needed only to run the JavaScript tests

## Setup

One toggle, once. In Edge's menu bar:

**View › Developer › Allow JavaScript from Apple Events**

Without it Edge refuses the injection and the tool tells you so. macOS will
also ask, on first run, to let your terminal control Edge — accept it, or set
it later under *System Settings › Privacy & Security › Automation*.

Optional shortcut:

```bash
echo 'alias gpt="~/workspace/AI-Plugins/chatgpt-cli/chatgpt-cli.py"' >> ~/.zshrc
```

## Usage

```bash
./chatgpt-cli.py "explain this regex: ^\d{3}-\d{4}$"
cat notes.md | ./chatgpt-cli.py
git diff | ./chatgpt-cli.py "review this diff"
./chatgpt-cli.py "write a bash one-liner" > answer.md
```

The reply is printed as Markdown, with fenced code blocks and their language,
once ChatGPT has finished writing it.

Arguments and piped input are **both** prompt material. Given both, the
arguments become the instruction and the piped content follows after a blank
line — so the third example above sends the instruction *and* the diff.

| Flag | Default | Purpose |
|---|---|---|
| `--timeout` | `180` | Seconds to wait for a reply |
| `--poll` | `1.0` | Seconds between checks |
| `--focus` | off | Switch Edge to the ChatGPT tab; background tabs can render slowly |

These defaults live in the `argparse` setup at the bottom of `chatgpt-cli.py`;
`--help` lists the flags but not their values.

Exit codes: `0` success, `1` an explained failure on stderr, `2` a usage
error such as an empty prompt, `130` interrupted.

### Conversation handling

The first open `chatgpt.com` tab is reused, so replies keep the context of that
conversation. If no such tab exists, one is opened. Switch conversations, or
start a fresh one, in the browser as usual — the CLI follows whatever that tab
is showing.

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

## Testing

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

## Troubleshooting

| Message | Cause |
|---|---|
| *Edge is refusing to run JavaScript from AppleScript* | The setup toggle above is off |
| *This terminal is not allowed to control Microsoft Edge* | Grant it under Privacy & Security › Automation |
| *Microsoft Edge is not running* | Open Edge and sign in to ChatGPT |
| *The ChatGPT tab has no composer* | The tab is on a login, Cloudflare or error page |
| *Could not press Send* | Usually a ChatGPT redesign — see below |
| *Timed out …* | Raise `--timeout`; the message reports how much text had arrived |

Error matching is by **numeric AppleScript code**, not English text, because
macOS localises these messages.

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

### Sharing the tab

Concurrent use is handled for *reading* but not for *writing*: while ChatGPT is
generating, the send button is replaced by a stop button, so a second run
started mid-stream fails with *Could not press Send* rather than corrupting
anything. Run invocations one at a time.

Likewise, `<code>` elements carry **no** `language-*` class. The language is a
header label beside the Run button, which is why `chatgpt_dom.js` recovers it
by finding the lone bare word near the code that is not a button caption.

---

# chatgpt-agent.py — multi-turn tasks over a read-only workspace

`chatgpt-cli.py` sends one prompt and prints one reply. `chatgpt-agent.py`
turns that into a loop: ChatGPT asks for files, this side serves them, and the
exchange repeats until ChatGPT answers.

The point is where the work happens. ChatGPT does the reading and the
reasoning, so that cost lands on web-chat quota; this process only fetches what
was asked for. A diff never has to pass through the agent that ran the command.

```bash
./chatgpt-agent.py --preset review --workspace ~/code/app "review this branch"
./chatgpt-agent.py --preset plan --session app --out plan.md "add retry to ingest"
```

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

## What it can and cannot do

By default every op only reads. `resolve_path` runs before each file touch, in
code rather than in the prompt: `realpath` first so a symlink out of the
workspace is caught, then containment, then a block on `.env*`, private keys
and credential files. The model cannot argue with a rule it cannot see. With
shell off, an instruction hidden in a repository has nothing to reach.

`--allow-shell` changes that, on purpose. Some questions — *does this suite
actually catch this bug?* — cannot be answered by reading, so the flag adds one
op that runs real commands on your machine, as you, with `cwd` wherever the
model asks:

```bash
./chatgpt-agent.py --preset review --allow-shell \
  "Copy the repo to /tmp/mt, flip the comparison on line 47 there, run the tests,
   and tell me whether they went red."
```

Three things bound it, and it is worth being exact about which is which:

- **It does not exist unless you pass the flag.** Ordinary reviews stay
  read-only. This is the control that actually matters.
- **Every command is printed before it runs**, so an unattended loop still
  leaves you a record of what happened.
- **`shell_objection` refuses work that is not a reviewer's**: recursive
  deletes, escalation, publishing, reaching another host, reading credentials,
  destroying local work. This bounds the **role** — it catches a model that
  drifts or blunders. It is **not** a sandbox, and anyone determined to spell a
  command differently will. The command runs as you either way.

So: read-only by construction, or shell because you asked for it. There is no
third state where shell is on and the bridge is still a fence.

## Flags

| Flag | Default | Purpose |
|---|---|---|
| `--preset` | `review` | which preset to load |
| `--workspace` | cwd | repo to expose; resolved to its git root |
| `--session` | none | name a conversation to reuse across runs |
| `--new` | off | start a fresh chat for that session name |
| `--out` | none | also write the answer to a file |
| `--max-rounds` | `8` | query budget before a conclusion is demanded |
| `--max-chars` | `250000` | workspace data served across the whole run |
| `--round-chars` | `80000` | workspace data served in one round |
| `--allow-shell` | off | add the op that runs commands on your machine |
| `--retries` | `3` | attempts per exchange before giving up |
| `--resume` | off | continue the interrupted run for this session |
| `--timeout` | `300` | seconds per reply |

Sessions live in `~/.chatgpt-agent/sessions.json` as name → conversation URL.
Without `--session`, every run starts a fresh chat and nothing is remembered.

## Testing

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
