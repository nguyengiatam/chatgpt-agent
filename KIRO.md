# Kiro CLI integration

This repository provides Kiro workspace skills under `.kiro/skills/` and packaged Agent Plugin skills under `skills/`. Both reuse the existing ChatGPT Agent runtime.

## Workspace skills

When this repository is open in Kiro CLI, the skills under `.kiro/skills/` are discovered automatically and can be used as slash commands:

- `/chatgpt-agent-review`
- `/chatgpt-agent-plan`
- `/chatgpt-agent-implement`
- `/chatgpt-agent-ask`
- `/chatgpt-agent-doctor`
- `/chatgpt-agent-sessions`

The implementation skill is explicitly write-capable. Review, plan, ask, and doctor are read-only. The sessions skill is read-only when listing sessions but can modify saved session state when the user explicitly asks it to forget a session.

## Packaged Power

The root `plugin.json` packages the skills under `skills/` for installation as a Kiro Power. The packaged skills call the same repository scripts; the browser bridge is not duplicated.

## Requirements

The existing ChatGPT Agent requirements still apply: macOS, Microsoft Edge signed in to ChatGPT, Python 3, and the required Apple Events/Automation permissions.
