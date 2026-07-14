---
title: "Preset <-> skill parity map"
date: 2026-07-08
category: design-patterns
tags: [preset, skill, parity, task-260, exporter]
severity: design
status: documented
---

# Preset <-> skill parity map

Presets (board doorway, `data/task_presets.json`) and skills (session
doorway, `.claude/skills/`) are the same operational knowledge, entered
through two different doors. This maps the 32 curated presets against the
18 hand-authored skills: which already have a twin, which deserve one, and
which are doorway-specific by nature (and why that's fine, not a gap).

## Twinned today (11 pairs)

Both sides already exist and cover the same procedure.

| Preset | Skill | Notes |
|---|---|---|
| roadmap-audit | fable-audit | Preset is the audit-preset-contract instantiation of the skill. |
| hygiene-audit | hk-slop-audit | Same 7-category scan, preset version is contract-wired. |
| agent-native-architecture-audit | hk-arch-audit | |
| loop-audit | hk-autonomy-audit | |
| root-cause-analysis | hk-rca | Preset is the one-shot brief; skill is the interactive gated protocol. |
| iterative-output-refinement | hk-refine | |
| workflow-breadcrumb-analysis | hk-breadcrumb-creator | Preset `source` field already says "synthesized" from this skill. |
| changelog-generation | hk-changelog | |
| knowledge-capture | hk-compound | |
| mock-first-development | hk-mock-first | |
| merge-conflict-resolution | hk-merge-resolve | |

## Presets that deserve a skill twin (1)

| Preset | Why it's missing | Recommended skill |
|---|---|---|
| plan-implementation | The most common "let me think before I code" ask in a live session has no slash command today — everyone either freehands it or copies the preset text by hand. | `/hk-plan` — same explore-then-plan procedure, no coding. |

## Skills that deserve a preset twin (1)

| Skill | Why it's missing | Recommended preset |
|---|---|---|
| hk-scout-wiki | Explicitly "generic across projects" already (per its own description) — the one local-only skill that isn't tied to this repo's dev environment, so it's the one clean candidate for a dispatchable one-shot version. | scout-wiki-research — same three phases (scout, wiki, distill to backlog), dispatched as a board task with a link list in the task description. |

## Doorway-specific by nature (no twin needed)

**Skills tied to this repo's local dev environment.** A dispatched preset
task can land in any external repo; these skills assume this repo's own
Django ORM, log paths, and odin CLI wiring — porting them as presets would
just produce a task that fails on a repo that doesn't have
`testing_tools/`, `taskit/taskit-backend/logs/`, or an `.odin/` config.

| Skill | Reason |
|---|---|
| hk-local-diagnose | Wraps `testing_tools/*.py`, which only exists in this repo. |
| hk-local-logs | Reads this repo's specific log file paths. |
| hk-local-run-spec | Drives this repo's own `odin` CLI + local auth. |

**Skills that are session behavior, not a deliverable.** These modify how
the *current* agent behaves or what it consults — they don't produce an
artifact a cold-started dispatched task could hand back.

| Skill | Reason |
|---|---|
| hk-follow-breadcrumb | "Check the breadcrumb index before exploring" is a habit for the current task, not a task of its own. |
| hk-shift-changelog | Reports on the current session's own action history — a freshly dispatched agent has no history yet to report on. |
| hk-skill-creator | Edits `.claude/skills/` directly; a preset that "creates a preset" is what this task's exporter already does, not a distinct need. |

**Presets that are one-shot autonomous scans with no session-interaction
value.** These are meant to be pasted whole as a task brief and run cold in
an isolated agent context (screenshot-driven UI reviews, narrow-scoped slop
scans, format converters). A slash-command twin would just be the same
prompt with extra ceremony — nobody types `/hk-chart-validation` mid-session
when they can paste the preset into a task.

architecture-review, bug-report, ai-slop-detection, backend-slop-review,
frontend-slop-review, performance-audit, ui-gaps-analysis,
web-app-ui-validation, mobile-app-ui-validation, website-ux-audit,
chart-validation, markdown-to-html-report, visual-explainer,
artifact-review, day1-onboarding-overview, diagnose-error,
prompt-quality-audit, prompt-comparison-audit, static-autolint,
security-audit.

(`static-autolint` and `security-audit` are audit-category presets without
a skill twin yet — they're recent (task 217, task 245) and nobody has
asked for a live-session version; noted here rather than silently
excluded.)

## Coverage summary

| Bucket | Count |
|---|---|
| Twinned (preset + skill both exist) | 11 |
| Preset deserves a skill twin | 1 (plan-implementation) |
| Skill deserves a preset twin | 1 (hk-scout-wiki) |
| Doorway-specific skill (local-env or session-behavior) | 6 |
| Doorway-specific preset (one-shot scan, no session value) | 20 |
| **Total presets** | **32** |
| **Total skills** | **18** |

## Cross-references

- Preset inventory + audit-preset contract: `docs/patterns/preset-inventory.md`.
- Curation decisions (deletions/merge) for this pass: `.proof/task-260/proof.md`.
- Exporter that turns a preset into a portable skill: `odin/src/odin/export_skills.py`.
