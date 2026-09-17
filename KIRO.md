# Kiro CLI integration

This repository provides Kiro workspace skills under `.kiro/skills/` and reuses the existing ChatGPT Agent runtime.

## Workspace skills

When this repository is open in Kiro CLI, the skills under `.kiro/skills/` can be used as slash commands:

- `/chatgpt-agent-review`
- `/chatgpt-agent-plan`
- `/chatgpt-agent-implement`
- `/chatgpt-agent-ask`
- `/chatgpt-agent-doctor`
- `/chatgpt-agent-sessions`

The implementation skill is explicitly write-capable. The other operational skills are read-only by default.

## Requirements

The existing ChatGPT Agent requirements still apply: macOS, Microsoft Edge signed in to ChatGPT, Python 3, and the required Apple Events/Automation permissions.
