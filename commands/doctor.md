---
description: Check every prerequisite for the ChatGPT bridge and report what is missing
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py"`

Show the checklist above as-is.

If something failed, the fix is printed under it — relay that and stop. Do not
try to repair it yourself: two of the requirements are permissions only the
user can grant from System Settings and the Edge menu bar, and signing in to
ChatGPT is deliberately something this plugin never automates.
