# ChatGPT Agent

Delegate work to the **ChatGPT web UI** from the shell or from Claude Code,
through the Microsoft Edge window you are already signed in to. Built for
accounts that browser-automation tools cannot log in to.

ChatGPT asks for what it needs and this side serves it, so the repository never
has to be pasted into a prompt: the diff stays out of the calling agent's
context, and the reasoning is billed to web-chat quota rather than to the agent.

It drives the page with Edge's AppleScript `execute javascript` — not synthetic
keystrokes — so it never needs window focus, never touches your clipboard, and
cannot type into the wrong tab.

> **macOS + Microsoft Edge only.** Apple Events have no Linux or Windows
> equivalent, and Chrome and Safari each fail for their own reasons (see
> [docs/internals.md](docs/internals.md)). Edge must be running and signed in to
> ChatGPT, and two permissions must be granted by hand.

## Three things it does

| | What | Reads the repo | Writes to it |
|---|---|---|---|
| **ask** | a one-shot question | no | no |
| **review** / **plan** | judge a diff, or write an implementation plan | yes | no |
| **implement** | carry out a change and commit it | yes | **yes** |

```bash
./chatgpt-cli.py "explain this regex: ^\d{3}-\d{4}$"        # ask
./chatgpt-agent.py --preset review "Review the uncommitted change."
./chatgpt-agent.py --write "Implement docs/plans/retry.md on this branch."
```

A run reports its rounds on stderr and the answer on stdout:

```
workspace: /Users/me/code/app (write)
  on branch add-retry at 9f2c1a4, worktree clean
  Node project; dependencies are installed at <root>/node_modules
  data budget: 700000 chars, 120000 per round
round 1/24 - asking ChatGPT
  served 3 op(s): git_diff, read, search        [11482/700000 chars]
```

## Install as a Claude Code plugin

Source: **https://github.com/nguyengiatam/chatgpt-agent** — worth reading first,
since a plugin runs on your machine.

```
/plugin marketplace add https://github.com/nguyengiatam/chatgpt-agent.git
/plugin install chatgpt-agent@chatgpt-agent-marketplace
```

Then `/chatgpt-agent:doctor` before anything else — this plugin has more hard
requirements than most, and the doctor names whichever one is missing. To
update later: `/plugin marketplace update chatgpt-agent-marketplace`.

| Command | Purpose |
|---|---|
| `/chatgpt-agent:review` | review a diff, branch or working tree |
| `/chatgpt-agent:plan` | write an implementation plan |
| `/chatgpt-agent:implement` | carry out a change and commit it |
| `/chatgpt-agent:ask` | one-shot question, no repository access |
| `/chatgpt-agent:doctor` | check the prerequisites |
| `/chatgpt-agent:sessions` | list or forget saved conversations |

The `chatgpt-reviewer` agent forwards the same thing from a subagent when the
main thread should not spend context on it.

## Requirements and setup

- macOS, Microsoft Edge, signed in to ChatGPT
- Python 3 — standard library only, nothing to install
- Node only if you want to run the JavaScript tests

One toggle, once, in Edge's menu bar:

**View › Developer › Allow JavaScript from Apple Events**

Without it Edge refuses the injection and the tool says so. macOS will also ask,
on first run, to let your terminal control Edge — accept it, or set it later
under *System Settings › Privacy & Security › Automation*.

Run `python3 scripts/doctor.py` to check all of it at once.

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

So there are three states, and which one you are in is a flag you passed:
read-only by construction, commands because you asked for them with
`--allow-shell`, or the workspace as the target with `--write`. None of them is
a fence around a shell that is already running as you.

### `--write`: implementing, not reviewing

`--allow-shell` makes changing the tree *possible*; everything else in a review
run points away from it, and a model given a build task spends its first rounds
arguing with its own briefing. `--write` is the mode that says the workspace is
the target:

```bash
./chatgpt-agent.py --write --session add-retry \
  "Implement the brief at docs/plans/retry.md. Branch is already checked out."
```

It implies `--allow-shell` and loads the `implement` preset, which asks for the
smallest change that satisfies the task, the full suite rather than a filtered
subset, and a commit on the branch that is already checked out. There is no
separate write op: the model writes files the way you would at a terminal, with
a heredoc or an editor command.

What stays refused is what a local change has no business doing — pushing,
publishing, merging to the default branch, rewriting history — so the result
sits in your worktree for you to read before it goes anywhere. Deliberate
breaks still belong in a copy under `/tmp`: seeding a mutation to prove a new
test really fails is part of the job, leaving the workspace holding that
mutation is not.

The run also opens with the facts it would otherwise burn rounds discovering —
branch and HEAD, whether the worktree is clean, whether Node dependencies are
installed and where, which scripts `package.json` defines, which toolchain
markers are present. Facts only; anything that cannot be established is left
out rather than guessed at.

Two things it does not change. A green suite is evidence about the suite, not
about the change — read the diff. And the bridge still drives one browser tab,
so runs stay one at a time.

Implement runs also get a larger data budget — 700,000 characters against a
review's 250,000. The first real one spent a review's budget inside four rounds
and never reached an edit: reading the files to change, the conventions around
them and one reference implementation costs more than reading a diff. Keep an
eye on the `[spent/total]` counter in the progress log, and prefer `sed -n`
ranges over `cat` on large reference files.

## Flags

| Flag | Default | Purpose |
|---|---|---|
| `--preset` | `review`, or `implement` with `--write` | which preset to load |
| `--workspace` | cwd | repo to expose; resolved to its git root |
| `--session` | none | name a conversation to reuse across runs |
| `--new` | off | start a fresh chat for that session name |
| `--out` | none | also write the answer to a file |
| `--max-rounds` | `8` | query budget before a conclusion is demanded |
| `--max-chars` | `250000`, or `700000` with `--write` | workspace data served across the whole run |
| `--round-chars` | `80000`, or `120000` with `--write` | workspace data served in one round |
| `--allow-shell` | off | add the op that runs commands on your machine |
| `--write` | off | implement mode: edit, test and commit the workspace (implies `--allow-shell`) |
| `--retries` | `3` | attempts per exchange before giving up |
| `--resume` | off | continue the interrupted run for this session |
| `--timeout` | `300` | seconds per reply |

Sessions live in `~/.chatgpt-agent/sessions.json` as name → conversation URL.
Without `--session`, every run starts a fresh chat and nothing is remembered.

## One run at a time

The bridge steers a single browser tab: `ensure_tab` takes the first ChatGPT tab
it finds and navigates it. A second run started while the first is generating
steers that conversation out from under it and fails with *Could not press
Send*. There is no parallel mode.

Continuing an exhausted run under the same `--session` does not buy anything
either: the data budget resets per run, but every result already served stays in
the conversation, so the next run starts near the model's context limit. Start a
fresh chat and carry the findings across in the prompt.

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

## More

- [docs/internals.md](docs/internals.md) — how the bridge works, the `c2c`
  protocol, testing, and the traps that cost a debugging session each
- [CHANGELOG.md](CHANGELOG.md) — what changed and why
