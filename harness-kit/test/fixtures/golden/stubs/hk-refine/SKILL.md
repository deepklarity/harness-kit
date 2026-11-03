---
name: hk-refine
description: "Improve an output that isn't good enough (a reflection, plan, prompt, pipeline result) by an observe-hypothesize-change-rerun loop kept on disk. Triggers on: 'close the loop', 'this output isn't good enough', 'iterate on this', 'refine this output', 'improve this reflection', or /hk-refine."
argument-hint: "[what to improve, and what produced it]"
allowed-tools: Bash, Read, Edit, Write, Agent, Grep, Glob
---

This skill lives in `docs/skills/development/hk-refine/`. Read `docs/skills/development/hk-refine/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/development/hk-refine/`.
