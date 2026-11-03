---
name: hk-rca
description: "Walk a bug through Reproduce, Locate, Hypothesis, Failing Test, Fix, Verify Live, Document, and state the root cause in chat. A bug that will not reproduce takes Step 1's hard-bug branch: build a pass/fail loop, minimise, bisect, instrument. Triggers on: 'something is broken', 'debug this', 'RCA', 'root cause', 'diagnose', 'flaky', 'intermittent', 'performance regression', 'bisect', or /hk-rca."
argument-hint: "[optional: the symptom]"
---

This skill lives in `docs/skills/debugging/hk-rca/`. Read `docs/skills/debugging/hk-rca/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/debugging/hk-rca/`.
