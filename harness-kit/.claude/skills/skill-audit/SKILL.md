---
name: skill-audit
description: "Audit a skill under docs/skills and write up, as one HTML page, whether an agent can run it as written: a verdict, a score out of 10, and ranked findings each with a severity out of 5 and the fix to apply. Applies the rewrite when asked. Triggers on: 'audit this skill', 'is this skill any good', 'review the skill', 'improve this skill', 'skill audit', or /skill-audit."
---

This skill lives in `docs/skills/audits/skill-audit/`. Read `docs/skills/audits/skill-audit/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/audits/skill-audit/`.
