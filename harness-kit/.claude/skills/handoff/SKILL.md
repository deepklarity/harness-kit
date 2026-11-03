---
name: "handoff"
description: "Compact the current conversation into a handoff document a fresh agent can resume from: where the work stands, what was decided and why, what is next, which files to open first. User-invoked only: /handoff."
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
imported: true
imported_from: "https://github.com/mattpocock/skills/tree/main/skills/productivity/handoff"
---

This skill lives in `docs/skills/knowledge/handoff/`. Read `docs/skills/knowledge/handoff/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/knowledge/handoff/`.
