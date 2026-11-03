---
name: hk-autonomy-audit
description: "Judge whether an agent could take one area from symptom to verified fix with no person in the loop, and write up the gaps as one committed markdown page in docs/loop_audits/ with a score out of 10. Triggers on: 'loop audit', 'agent readiness', 'could an agent fix this alone', 'can an agent fix this on its own', or /hk-autonomy-audit."
argument-hint: "[area or flow to audit, e.g. 'spec execution' or 'reflection reports']"
---

This skill lives in `docs/skills/audits/hk-autonomy-audit/`. Read `docs/skills/audits/hk-autonomy-audit/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/audits/hk-autonomy-audit/`.
