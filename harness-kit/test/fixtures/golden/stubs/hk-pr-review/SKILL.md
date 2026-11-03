---
name: hk-pr-review
description: "Review a pull request, a branch, or the diff since a ref as one HTML page: a readiness score out of 5, findings in blockers, major and minor, a Standards section and a Spec section. Triggers on: 'review this PR', 'review my branch', 'is this ready to merge', 'review since <ref>', 'check this against the spec', 'standards and spec review', or /hk-pr-review."
argument-hint: "[PR url, number, branch, or a ref to review since] [path to the spec] [path to the repo, if not the current one]"
---

This skill lives in `docs/skills/reviews/hk-pr-review/`. Read `docs/skills/reviews/hk-pr-review/SKILL.md` and follow it exactly.

Every supporting file it mentions (REFERENCE.md, templates, `scripts/`, examples) sits beside it in that directory. Wherever it says "this skill's directory" or uses a path relative to itself, resolve it against `docs/skills/reviews/hk-pr-review/`.
