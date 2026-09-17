# ChatGPT Agent

Delegate work to the **ChatGPT web UI** from the shell, Claude Code, or Kiro CLI,
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

A run reports its rounds on stderr and the answer on stdout.

## Install as a Claude Code plugin

Source: **https://github.com/nguyengiatam/chatgpt-agent** — worth reading first,
since a plugin runs on your machine.

```text
/plugin marketplace add https://github.com/nguyengiatam/chatgpt-agent.git
/plugin install chatgpt-agent@chatgpt-agent-marketplace
```

Then `/chatgpt-agent:doctor` before anything else. To update later:

```text
/plugin marketplace update chatgpt-agent-marketplace
```

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

## Install with Kiro CLI

This repository supports Kiro in two ways: as workspace skills and as a Kiro
Power using the Agent Plugins format.

### Option A — use the workspace skills

The workspace skills are for working on this repository itself. Clone the
repository and open it with Kiro CLI; the skills under `.kiro/skills/` are
discovered automatically and can be invoked as slash commands.

```bash
git clone https://github.com/nguyengiatam/chatgpt-agent.git
cd chatgpt-agent
kiro-cli
```

Available skills:

| Skill | Purpose |
|---|---|
| `/chatgpt-agent-review` | review a diff, branch or working tree |
| `/chatgpt-agent-plan` | write an implementation plan |
| `/chatgpt-agent-implement` | implement a change, test it and commit locally |
| `/chatgpt-agent-ask` | ask ChatGPT a one-shot question |
| `/chatgpt-agent-doctor` | check the ChatGPT Agent prerequisites |
| `/chatgpt-agent-sessions` | list or manage saved ChatGPT conversations |

For another project, use the Power installation below or import an individual
skill from GitHub. Kiro's skill importer accepts the corresponding
`skills/<name>/` directory or its `SKILL.md` file.

### Option B — install the repository as a Kiro Power

The repository contains a root `plugin.json` and packaged skills under
`skills/`, so it can be installed as a custom Kiro Power.

In Kiro:

1. Open the **Powers** panel.
2. Select **Add Custom Power**.
3. Choose **Import power from GitHub**.
4. Enter `https://github.com/nguyengiatam/chatgpt-agent`.
5. Install the power.

Kiro CLI supports Powers from v3. The same Power can therefore be used from
Kiro CLI after installation.

The Power contains the same six skills listed above. It does not duplicate the
ChatGPT browser bridge; every skill delegates to the existing scripts in this
repository.

### Kiro requirements

Kiro itself can run on supported macOS, Linux, and Windows environments, but
this ChatGPT Agent runtime requires **macOS + Microsoft Edge**, because it uses
Apple Events to control Edge.

You also need:

- Python 3 — standard library only, nothing to install for the runtime
- Microsoft Edge signed in to ChatGPT
- Edge's **View › Developer › Allow JavaScript from Apple Events** enabled
- permission for your terminal to control Microsoft Edge under macOS
  **System Settings › Privacy & Security › Automation**

Run the doctor skill or:

```bash
python3 scripts/doctor.py
```

to check the prerequisites.

## What it can and cannot do

By default every op only reads. `resolve_path` runs before each file touch, in
code rather than in the prompt: `realpath` first so a symlink out of the
workspace is caught, then containment, then a block on `.env*`, private keys
and credential files. The model cannot argue with a rule it cannot see. With
shell off, an instruction hidden in a repository has nothing to reach.

`--allow-shell` changes that, on purpose. Some questions cannot be answered by
reading, so the flag adds one op that runs real commands on your machine, as
you, with `cwd` wherever the model asks. The commands are printed before they
run and safety checks refuse destructive or publishing operations.

### `--write`: implementing, not reviewing

`--write` is the mode that says the workspace is the target:

```bash
./chatgpt-agent.py --write --session add-retry \
  "Implement the brief at docs/plans/retry.md. Branch is already checked out."
```

It implies `--allow-shell` and loads the `implement` preset. The model edits
files, runs tests and commits on the branch that is already checked out.
Pushing, publishing, merging to the default branch and rewriting history are
refused by the runtime.

## Flags

| Flag | Default | Purpose |
|---|---|---|
| `--preset` | `review`, or `implement` with `--write` | which preset to load |
| `--workspace` | cwd | repo to expose; resolved to its git root |
| `--session` | none | name a conversation to reuse across runs |
| `--new` | off | start a fresh chat for that session name |
| `--out` | none | also write the answer to a file |
| `--max-rounds` | `8`, or `36` with `--write` | query budget before a conclusion is demanded |
| `--max-chars` | `250000`, or `700000` with `--write` | workspace data served across the whole run |
| `--round-chars` | `80000`, or `120000` with `--write` | workspace data served in one round |
| `--allow-shell` | off | add the op that runs commands on your machine |
| `--write` | off | implement mode: edit, test and commit the workspace (implies `--allow-shell`) |
| `--retries` | `3` | attempts per exchange before giving up |
| `--resume` | off | continue the interrupted run for this session |
| `--timeout` | `300` | seconds per reply |
| `--list-sessions` | | print saved conversations with their age |
| `--forget <name>` | | drop one saved conversation |
| `--prune` | off | list dead checkpoints, stale sessions and abandoned claim files, then exit |
| `--days` | `14` | age `--prune` calls stale |
| `--all` | off | with `--prune`: everything, whatever its age |
| `--yes` | off | with `--prune`: delete instead of listing |

## State on disk

Three kinds of state live under `~/.chatgpt-agent`:

- `sessions.json` — one record per session name, including the conversation URL
  and, when known, the stable Edge tab id that currently shows it.
- `runs/<name>.json` — a checkpoint written before every wait, so a run killed
  mid-flight can be picked up with `--resume` instead of thrown away.
- `claims/*.lock` — bookkeeping beside kernel-held ownership locks. The file may
  remain after its owner exits; the lock does not.

Each run prunes old checkpoints and session bookmarks before writing its own
state. `--prune` exposes the same cleanup on demand and also reports abandoned
claim files as their own `claim` rows. It lists by default and deletes only with
`--yes`; state the current run needs is held back so housekeeping cannot sweep a
checkpoint it is about to resume.

## Parallel runs

Runs may execute in parallel. Each run owns its tab **and the conversation that
tab shows** for the whole run. Conversation ownership is the important part:
two tabs showing the same conversation still conflict. If a requested
conversation is already owned, the contender fails immediately and names the
holder; it never waits in a queue.

A one-shot question is less strict about the tab itself. If its first candidate
tab is claimed, it quietly tries another ChatGPT tab or opens one. If the
conversation shown by a candidate is already claimed, that conflict is refused
rather than routed around.

Ownership is a kernel-held file lock, not a pid file. When the owning process
ends — cleanly, by Ctrl-C, or even by `kill -9` — the kernel releases the lock,
so there is no stranded conversation and no ownership cleanup step to remember.
Abandoned claim *files* are only bookkeeping and are what `--prune` reports.

Parallelism still shares one Edge instance. Several tabs streaming at once are
heavier on the machine than one, and the bridge enforces no concurrency limit.
Also, a run cannot survive the machine sleeping longer than its `--timeout`:
macOS can throttle a background tab hard enough that a reply freezes part-way.
For long unattended runs, the mitigation in use is to wrap the invocation in
`caffeinate -dimsu`. That keeps the display from sleeping, which is what
usually triggers the lock - it cannot stop a lock you ask for by hand, and a
run that outlives its `--timeout` while locked still dies.

## Troubleshooting

| Message | Cause |
|---|---|
| *Edge is refusing to run JavaScript from AppleScript* | The setup toggle above is off |
| *This terminal is not allowed to control Microsoft Edge* | Grant it under Privacy & Security › Automation |
| *Microsoft Edge is not running* | Open Edge and sign in to ChatGPT |
| *The ChatGPT tab has no composer* | The tab is on a login, Cloudflare or error page |
| *Could not press Send* | Usually a ChatGPT redesign — see below |
| *Timed out …* | Raise `--timeout`; the message reports how much text had arrived |

## More

- [docs/internals.md](docs/internals.md) — how the bridge works, the `c2c`
  protocol, testing, and the traps that cost a debugging session each
- [CHANGELOG.md](CHANGELOG.md) — what changed and why
