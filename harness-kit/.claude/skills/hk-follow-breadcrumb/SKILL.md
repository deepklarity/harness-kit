---
name: hk-follow-breadcrumb
description: "Read docs/breadcrumb_analysis/ before exploring a flow and answer from it, citing file and function per hop. Triggers on: 'how does X work', 'where does X happen', 'where is this in the code', 'what files handle X', or /hk-follow-breadcrumb, and before spawning exploration subagents for a flow that crosses frontend, backend, worker or CLI."
argument-hint: "<what you're trying to understand, e.g. 'how does task execution work'>"
---

This skill lives in `docs/skills/debugging/hk-follow-breadcrumb/`. Read `docs/skills/debugging/hk-follow-breadcrumb/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/debugging/hk-follow-breadcrumb/`.
