---
name: audit-agent-efficiency
description: "Read a finished agent's transcript, find the turns it wasted, and put a fix on disk for each one (a CLAUDE.md line, a script, a breadcrumb, a pattern) so the next run is fast. Triggers on: 'audit that agent', 'why was that slow', 'review the agent's run', or /audit-agent-efficiency."
argument-hint: "[transcript path or task id, and what the task was]"
---

This skill lives in `docs/skills/audits/audit-agent-efficiency/`. Read `docs/skills/audits/audit-agent-efficiency/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/audits/audit-agent-efficiency/`.
