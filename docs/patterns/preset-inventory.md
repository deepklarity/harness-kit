---
title: "Preset/template inventory + the audit-preset contract"
date: 2026-07-07
category: design-patterns
tags: [preset, audit, contract, templates, fable-roadmap]
severity: design
status: documented
---

# Preset/template inventory + the audit-preset contract

The kit has more preset-shaped machinery than anyone has inventoried. Every
audit currently re-derives what evidence to gather, what shape its report takes,
and how findings become board tasks. This doc maps the existing mechanisms, then
defines an **audit-preset contract** so future audits (and the next task: the
build) can declare their evidence + checks + report shape + finding→task
mapping once and ride on shared infrastructure.

Two audits already in the kit — `fable-audit` (roadmap progress) and
`hk-slop-audit` (codebase hygiene) — are instantiated against the contract at
the bottom of this doc.

---

## Part 1 — Inventory of existing preset/template mechanisms

Each entry: what it does, where it lives, where it's consumed, how enhanceable
it is, and whether it's already "preset-shaped" (data-driven, declarative) or
"prescriptive" (hand-authored procedure).

### 1. Task prompt presets — preset-shaped, live

- **Data file**: `taskit/taskit-backend/data/task_presets.json`
  (27 presets, 6 categories: `code-review`, `ui-ux-audit`, `documentation`,
  `analysis`, `quality-process`, `development`).
- **Schema**: `{version, categories[], presets[]}` where each preset is
  `{id, title, description (full prompt text), category, icon,
  suggested_priority, source, sort_order}`.
- **Served by**: `tasks/views.py::list_presets` (line ~5333) → `GET /api/presets/`.
- **Consumed by**: `CreateTaskModal.tsx` via `HarnessTimeService.fetchPresets()`;
  `PresetPicker.tsx` renders them. Selection populates `title/description/
  priority`; no preset metadata is stored on the task.
- **Breadcrumb**: `docs/breadcrumb_analysis/prompt-presets/`.
- **Enhanceable**: yes — edit JSON, no migration. The two `hk-*` audit
  categories (`codebase-hygiene-audit`, `loop-audit`, `agent-native-architecture-audit`)
  already exist here as presets; they could be cross-referenced by the
  corresponding `hk-*-audit` skill for one source of truth.
- **Limitation**: prompt-only — no context isolation, no verification gate. The
  preset is just form-fill text.

### 2. TDD task presets (proposed) — preset-shaped, not yet built

- **Spec**: `docs/breadcrumb_analysis/task-preset-tdd-enforcement/FLOW.md`.
- **Schema (proposed)**: `{preset: test|implement|scaffold|integrate|verify|standalone,
  context_isolation: <rule>, verification_gate: <rule>, prompt_additions: <string>}`
  stored at `task.metadata["preset"]`.
- **Consumed by**: orchestrator's `_wrap_prompt()` (excludes/limits context per
  preset) and `_verify_preset_gate()` (parses proof against the gate) before
  status transitions.
- **Enhanceable**: yes by design — new presets = new entries in the dispatcher.
- **Status**: **PROPOSED**, not implemented. The proposal already gives us a
  mental model: a preset is **typed context + typed gate + typed prompt**.
  Audits are the same shape: typed evidence + typed check + typed report.

### 3. Wave/spec loaders — preset-shaped, hand-per-wave

- **Files**: `docs/fable_roadmap/bootstrap/create_wave{1..5}.py`,
  `create_wave4_refill{,2,3,4}.py`, `create_smoke_tables.py`.
- **Schema** (each): `TASKS = [dict(key, title, agent, model?, depends,
  description), ...]` plus a `COMMON_FOOTER` and a `PRE-DISPATCH CHECKLIST`
  docstring.
- **Loader body** (every file, identical shape): idempotent on `spec.odin_id`,
  creates one `Board` + one `Spec` + N `Task`s, wires `depends_on`, substitutes
  `<id>` into the description so the promote-gate's artifact parser sees real
  paths (the W5.1 fix lives here).
- **Why it's a preset**: each file is **data + a 30-line loader loop**. The
  data could move out of Python into JSON/YAML; the loader would be one shared
  function. Today every wave copy-pastes the loader.
- **Enhanceable**: yes. The natural next step is a single
  `load_spec_from_manifest(odin_id, path, tasks_json)` and per-wave files that
  contain only `TASKS = [...]`.
- **Limitation**: hand-written; no schema validation; description text is freeform
  prose (no fielded WHY/Scope/Acceptance/Verify enforced).

### 4. Skill packs — procedural, not preset-shaped

- **Files**: `.claude/skills/*/SKILL.md` (18 skills: `fable-audit`,
  `hk-arch-audit`, `hk-autonomy-audit`, `hk-breadcrumb-creator`, `hk-changelog`,
  `hk-compound`, `hk-follow-breadcrumb`, `hk-local-diagnose`, `hk-local-logs`,
  `hk-local-run-spec`, `hk-merge-resolve`, `hk-mock-first`, `hk-rca`, `hk-refine`,
  `hk-scout-wiki`, `hk-shift-changelog`, `hk-skill-creator`, `hk-slop-audit`).
- **Schema (implicit)**: YAML frontmatter `{name, description, argument-hint,
  allowed-tools}` + freeform markdown body describing the procedure.
- **Consumed by**: Claude Code skill trigger (matches on description keywords).
- **Enhanceable**: as a writing convention (the frontmatter is structured), but
  not as data — the body is a procedure, not a config. Two skills
  (`hk-slop-audit`, `fable-audit`) are the **exact** things the audit-preset
  contract should let us upgrade from procedure to data.
- **Limitation**: skills don't share infrastructure — each re-derives the
  "what evidence to gather / what shape the output takes" decisions. The
  contract below is the missing glue.

### 5. Claude Code agents — procedural, not preset-shaped

- **Files**: `.claude/agents/hk-senior-fullstack-engineer.md`,
  `.claude/agents/hk-test-writer.md`.
- **Schema (implicit)**: markdown with sections for role, workflow, style,
  rules. No frontmatter beyond filename.
- **Consumed by**: skill delegations or odin dispatch.
- **Enhanceable**: as convention only. Not relevant to the audit contract but
  worth noting as the *third* "authored prose" preset family alongside skills
  and wave task descriptions.

### 6. odin plan prompt — hardcoded, not file-based

- **Location**: `odin/src/odin/orchestrator.py::_build_plan_prompt` (line 947).
- **Schema (implicit)**: large f-string with embedded `PLANNING PHILOSOPHY`,
  `PROOF-FIRST DECOMPOSITION`, schema, dependency/artifact rules, optional
  `quota_instruction` and `quick_instruction` blocks.
- **Consumed by**: `odin plan` (all modes: interactive/auto/quiet) plus tests
  (`test_planning.py`, `test_build_self_context.py`).
- **Enhanceable**: only via code change. The "preset" lives in a Python
  function, not a file.
- **Limitation**: not a preset in the sense we want — can't be replaced per
  project or per call site. Mentioned here for completeness; out of scope for
  the audit-preset contract.

### 7. odin sample specs — sample inputs, not presets

- **Location**: `odin/sample_specs/**/*.md` (smoke/mcp/apps variants).
- **Purpose**: deterministic inputs for `odin plan` testing/demos.
- **Not a preset**: they're test fixtures shaped like spec markdown. Mentioned
  because a future audit might want to invoke `odin plan` on each as a
  regression check (today: out of scope).

### 8. Wave briefs (Markdown) — informal templates

- **Files**: `docs/fable_roadmap/bootstrap/wave{1..5}_*.md` (referenced from
  `create_wave*.py`; the file paths live in the loader's `Spec.source`). Today
  the loader reads these for `Spec.content` but the actual task descriptions
  are inlined in `TASKS = [...]`.
- **Not a preset yet**: the briefs are prose, not data. A future "wave"
  preset could collapse wave_brief.md + create_waveN.py + TASKS list into one
  manifest.

### 9. Audit reports — typed artifacts, hand-curated

- **Location**: `docs/fable_roadmap/audits/YYYY-MM-DD*.md`
  (5 historical: `2026-07-06.md`, `2026-07-06-wave2.md`,
  `2026-07-07-vm-memory-provisioning.md`, `2026-07-07-wave3-close.md`,
  `2026-07-08-wave4-close.md`).
- **Shape (informal)**: verdict → verified-done table → downgrades → metrics →
  risks → ladder → next actions. Reads in 30 seconds from the top.
- **Not a preset**: written by hand by `fable-audit`. The slop audit uses a
  *different* shape (`slops/all_slops.md` + per-finding `slops/SLOP-NNN.md`).
  This is the duplication the contract collapses.

### 10. Static JSON single-source-of-truth files

- `taskit/taskit-backend/data/agent_models.json` — agent/model lineup.
- `taskit/taskit-backend/data/task_presets.json` — already counted (entry 1).
- `.odin/config.yaml`, `odin/config/config.sample.yaml`,
  `harness_usage_status/config/config.sample.yaml` — config presets, not
  relevant to audit presets but worth noting as the existing "presets shipped
  as JSON" precedent.

---

## Summary of the inventory

| # | Mechanism | Shape | State | Relevant to audit contract? |
|---|---|---|---|---|
| 1 | Task prompt presets | Data (JSON) | Live | Indirectly (cross-reference) |
| 2 | TDD task presets | Data (proposed) | Proposed | Indirectly (model) |
| 3 | Wave/spec loaders | Data + thin loader | Live, hand-per-wave | No (but parallel pattern) |
| 4 | Skill packs | Procedure (markdown) | Live | **Yes — these ARE the audits** |
| 5 | Claude Code agents | Procedure (markdown) | Live | No |
| 6 | odin plan prompt | Hardcoded | Live | No |
| 7 | odin sample specs | Sample inputs | Live | No |
| 8 | Wave briefs (Markdown) | Prose | Live, informal | No |
| 9 | Audit reports | Typed artifacts | Live, hand-curated | **Yes — these ARE the outputs** |
| 10 | Static config JSON | Data | Live | No |

**The two families that matter for the audit contract: #4 (skills) and #9
(reports).** A successful audit-preset contract turns both into data: the
skill becomes a YAML/JSON declaration; the report becomes its rendered
output.

The TDD task preset (#2) is the closest existing precedent for the mental
model. The wave loaders (#3) are the closest existing precedent for the
physical shape.

---

## Part 2 — The audit-preset contract

The contract has four parts. An audit preset declares them all. The build
task (next up) provides a runner that consumes them.

### Schema (one page)

```yaml
# Audit preset
apiVersion: audit.harness.kit/v1     # bumps when fields change
kind: Audit
metadata:
  id: <kebab-id>                      # unique; matches an hk-*-audit or fable-audit
  name: <human title>
  description: <one line>
  triggers:                           # when this runs (all match → run)
    - keyword: <string>               # e.g. "audit progress", "find slop"
    - schedule: <cron>                # optional periodic
    - on_event: <wave_close|sprint_end|...>  # optional hook
    - manual: true                    # explicit /hk-<id> invocation
spec:
  evidence:                           # the inputs the checks run against
    scripts:                          # shell commands whose stdout is captured
      - name: <display>
        command: <shell string>       # e.g. "python testing_tools/board_overview.py 5"
        cwd: <repo-relative dir>      # default: repo root
        timeout_s: <int>              # optional, default 60
        parse: <awk|grep|json|stdout> # how to extract signal from stdout
    endpoints:                        # HTTP probes
      - method: GET
        path: /api/<...>/
        auth: <none|env_jwt>          # default: env_jwt
        parse: <json|status>
    files:                            # file-level checks
      - path: <glob>
        check: <description of what to look for>   # the runner interprets this
        on_match: <finding-template-id>            # optional
    databases:                        # Django ORM via testing_tools/
      - tool: testing_tools/<script>.py
        args: [<list>]
        sections: [<brief|json|...>]  # what sections of the report to consume
  checks:                             # assertions over evidence; produce findings
    - id: <check-id>
      description: <what is verified>
      source: <which evidence item>
      expects: <truthy|falsy|<number>|<regex>>     # the signal value expected
      severity_on_fail: <P0..P4>      # default: P2
      finding_template: <finding-id>  # how to render this as a task
  report:                             # the rendered output
    path: <path-template, supports {date}, {slug}>
    shape:                             # ordered sections, each typed
      - id: verdict
        kind: line
        format: "**Verdict:** <ON TRACK|DRIFTING|BLOCKED|COMPLETE> — <why>"
      - id: verified_done_table       # only relevant for roadmap audits
        kind: table
        columns: [task, claim, proof, result]
      - id: findings_by_priority
        kind: table
        columns: [id, severity, location, summary]
      - id: metrics_snapshot
        kind: table
        columns: [metric, value, source]
      - id: top_3_risks
        kind: list
        max_items: 3
      - id: next_actions
        kind: list
        max_items: 5
        assignable: true              # each item can become a board task
    max_lines: 80                     # lean by user directive
    prose_first: true                 # tables only where they add density
  findings:                           # how findings become board tasks
    severity_scale: [P0, P1, P2, P3, P4]
    default_bucket_why: <bucket-name> # e.g. "trust" — WHY cited in each task
    task_template:
      title_prefix: "<ID>:"           # e.g. "SLOP-NNN:" or "AUDIT-NNN:"
      description_template: |
        ## What is wrong today
        <bucket-why-snippet — plain sentence, name the area in passing, no score>

        Finding: <summary>
        Evidence: <file/script/output pointer>
        Acceptance: <concrete check>
        Verify: <one command or one proof artifact>
      default_agent: <agent-name>     # suggestion, per user directive
      proof_path: ".proof/task-<id>/" # standard convention
    artifact_paths:                   # where the runner drops raw evidence
      - ".proof/audit-{date}/<slug>.txt"
```

### Semantics (what each field means)

**`metadata.triggers`** is what maps the preset to invocation surfaces
(`/hk-slop-audit`, `/fable-audit`, periodic, on-event). The runner subscribes
to all matching triggers; an explicit invocation always wins.

**`spec.evidence`** is the *declarative* list of inputs. The runner executes
each `script` from the configured `cwd` with a timeout, captures stdout, and
parses it per `parse`. It calls each `endpoint` with the configured auth.
For `files` and `databases`, the runner interprets `check` per its kind
(text grep, file size, JSON path, etc.) — the contract here is intentionally
light; specifics live in the build task.

**`spec.checks`** turn evidence into findings. Each check names a source, an
expected signal, a severity on failure, and a finding template. The runner
evaluates; mismatches produce a finding of the named severity. Multiple
checks can share a finding template.

**`spec.report`** is the rendered output's *shape*, not its prose. Sections
are typed (`line`, `table`, `list`) so the runner can render them from
structured data. `max_lines` enforces the user directive ("lean by user
directive: an audit is one short report"). `prose_first: true` keeps tables
from eating the page.

**`spec.findings`** is the bridge to the board. Every check failure and every
explicit `findings.by_*` rule produces a task via `task_template`. The
template is *parametrized* — the runner fills `<id>`, `<summary>`, the
evidence pointer, etc. The `default_agent` is a suggestion; the user can
reassign before dispatch (matches `Default First` + `Suggestive, not
prescriptive` from the product rules).

**Idempotency** is implicit: the runner is a pure function of (preset,
evidence-at-time-of-run, board state). Re-running produces a fresh report
dated today; findings become *new* tasks only if not already filed (the
runner checks `task.title LIKE '<prefix>%'` before creating).

**Failure modes the contract must handle** (these came up reading the wave-4
audit):

1. **Missing evidence** — script not found, endpoint down, file gone. The
   runner records the gap, fails the dependent checks with severity P1
   ("evidence missing"), and continues. The report names what didn't run.
2. **Stale evidence** — the evidence was collected more than N seconds ago
   (preset-configurable per evidence item, default 300s). Force a re-run.
3. **Partial board** — the board ID or spec ID doesn't exist yet. Runner
   degrades to "no data" for those checks and continues.
4. **Self-reference** — the audit preset lives in the repo it's auditing.
   The runner must be able to locate itself (`metadata.id` ↔ path on disk)
   without infinite recursion.

**Anti-requirements** (what the contract does *not* allow):

- No freeform prose in the report — only the typed sections in `report.shape`.
- No silent findings — every check mismatch is named with a finding id.
- No ad-hoc scripts in the evidence list — only commands the runner can
  invoke with the documented `parse` strategy. (Custom parsing strategies go
  in the build task, not the preset.)
- No CI integration baked in — the runner is invoked; CI is somebody else's
  problem.

---

## Part 3 — Worked examples

### Example A: `fable-audit` (roadmap progress)

```yaml
apiVersion: audit.harness.kit/v1
kind: Audit
metadata:
  id: fable-audit
  name: Fable roadmap auditor
  description: "Verify claimed progress with proof; update RESUME/BACKLOG."
  triggers:
    - keyword: "fable audit"
    - keyword: "roadmap audit"
    - keyword: "audit progress"
    - on_event: wave_close
    - manual: true
spec:
  evidence:
    scripts:
      - name: board overview
        command: "python testing_tools/board_overview.py 5 --json"
        cwd: taskit/taskit-backend
        parse: json
      - name: spec trace (per active spec)
        command: "python testing_tools/spec_trace.py <spec_id> --json --sections tasks,problems"
        cwd: taskit/taskit-backend
        parse: json
      - name: task inspect (per DONE task since last audit)
        command: "python testing_tools/task_inspect.py <task_id> --brief"
        cwd: taskit/taskit-backend
        parse: stdout
      - name: reflection inspect (per task with reflection)
        command: "python testing_tools/reflection_inspect.py <report_id> --sections verdict,diagnosis"
        cwd: taskit/taskit-backend
        parse: stdout
      - name: autonomy metrics
        command: "python testing_tools/autonomy_metrics.py --json"
        cwd: taskit/taskit-backend
        parse: json
      - name: verify gate (snapshot)
        command: "sh scripts/verify.sh"
        cwd: <repo-root>
        parse: stdout
    files:
      - path: docs/fable_roadmap/RESUME.md
        check: "Where things stand section matches board reality"
      - path: docs/fable_roadmap/BACKLOG.md
        check: "Top 3 items match wave-close audit recommendations"
      - path: docs/fable_roadmap/fable_roadmap.md
        check: "Bucket scores only move at audits, with evidence"
  checks:
    - id: done-since-last-audit
      description: "Every task DONE since last audit has verifiable proof on disk."
      source: spec trace + task inspect
      expects: "all(matches proof_path on disk)"
      severity_on_fail: P1
      finding_template: roadmap-claim-unverified
    - id: doc-vs-board-divergence
      description: "RESUME 'Where things stand' matches board_overview.py."
      source: board overview + RESUME.md
      expects: "no contradiction"
      severity_on_fail: P2
      finding_template: doc-vs-board
    - id: bucket-score-without-evidence
      description: "Bucket score moves only with audit-attached before/after."
      source: fable_roadmap.md + this report
      expects: "every score movement cites a metric"
      severity_on_fail: P1
      finding_template: bucket-score-evidence
    - id: ladder-ratchet
      description: "Ladder level changes follow ratchet rules in fable_roadmap.md."
      source: autonomy metrics + this report
      expects: "no level moved without meeting exit criteria"
      severity_on_fail: P2
      finding_template: ladder-rule-violation
    - id: hand-fixes-file-themselves
      description: "Every operator hand-fix since last audit has a filed task."
      source: board history (DONE/REVIEW transitions by operator email)
      expects: "each non-agent transition has a task with same root cause"
      severity_on_fail: P2
      finding_template: hand-fix-unfiled
    - id: learned-twice
      description: "No pattern recurs without a docs/patterns/ entry."
      source: git log (last N waves) + docs/patterns/
      expects: "every repeated mistake has a doc"
      severity_on_fail: P2
      finding_template: learned-twice
  report:
    path: docs/fable_roadmap/audits/{date}-{slug}.md
    shape:
      - id: verdict
        kind: line
        format: "**Verdict:** <ON TRACK|DRIFTING|BLOCKED> — <one-line why>"
      - id: verified_done_table
        kind: table
        columns: [task, claim, proof, result]
      - id: downgrades
        kind: list
      - id: metrics_snapshot
        kind: table
        columns: [metric, value, source]
      - id: top_3_risks
        kind: list
        max_items: 3
      - id: ladder_call
        kind: line
        format: "**Ladder:** <level>, <one-line why>"
      - id: next_actions
        kind: list
        max_items: 5
        assignable: true
    max_lines: 80
    prose_first: true
  findings:
    severity_scale: [P0, P1, P2, P3, P4]
    default_bucket_why: "the bucket the work belongs to (Trust / Memory / etc.)"
    task_template:
      title_prefix: "AUDIT-{date}-"
      description_template: |
        ## What is wrong today
        <bucket WHY snippet — plain sentence, no score>

        Finding: <summary>
        Evidence: <file/script/output pointer>
        Acceptance: <one concrete check>
        Verify: <one command or one proof artifact path>

        ## Working protocol
        - First principles: fix the CAUSE, not the symptom.
        - Failing test before fix.
        - Attach proof to .proof/task-<id>/proof.md and post a summary comment.
        - Do not create or switch branches; edit files in your worktree.
      default_agent: claude            # default; user can reassign
      proof_path: ".proof/task-<id>/"
    artifact_paths:
      - ".proof/audit-{date}-fable/board_overview.txt"
      - ".proof/audit-{date}-fable/verify_sh.txt"
```

**What this replaces today**: the `fable-audit/SKILL.md` procedural skill
(34 lines) plus the hand-written audit reports under `docs/fable_roadmap/audits/`.
The preset names the same evidence the skill names informally ("the board is
truth — `board_overview.py` / `spec_trace.py` / `task_inspect.py --brief`"),
enforces the same report shape the existing audits follow (verdict →
verified-done table → demotions → metrics → risks → ladder → next actions),
and produces tasks with the WHY context the buckets demand.

### Example B: `hk-slop-audit` (codebase hygiene)

```yaml
apiVersion: audit.harness.kit/v1
kind: Audit
metadata:
  id: hk-slop-audit
  name: Codebase hygiene auditor
  description: "Scan for misplaced files, dead code, temp files, security/structural/git slop."
  triggers:
    - keyword: "audit the codebase"
    - keyword: "find slop"
    - keyword: "hygiene check"
    - keyword: "clean up"
    - schedule: "monthly"             # periodic catch
    - manual: true
spec:
  evidence:
    scripts:
      - name: file listing
        command: "git ls-files"
        cwd: <repo-root>
        parse: stdout
      - name: gitignore violations
        command: "git ls-files --others --exclude-standard"
        cwd: <repo-root>
        parse: stdout
      - name: stale TODO/FIXME/HACK
        command: |
          git grep -nE '(TODO|FIXME|HACK|XXX)\b' -- '*.py' '*.ts' '*.tsx' '*.js' '*.sh' \
            | while IFS=: read -r f line _; do
                age=$(git blame --porcelain -L "$line,$line" -- "$f" 2>/dev/null | head -1 | awk '{print $2}')
                [ -n "$age" ] && [ "$age" \< "2025-12-01" ] && echo "$f:$line"
              done
        cwd: <repo-root>
        parse: stdout
      - name: duplicate definitions + commented-out dead code
        command: "sh scripts/self_audit_diff.sh HEAD~10..HEAD"
        cwd: <repo-root>
        parse: stdout
      - name: large files
        command: |
          git ls-files '*.py' '*.ts' '*.tsx' '*.js' '*.sh' \
            | xargs -I{} wc -l {} 2>/dev/null | awk '$1 > 500 {print}'
        cwd: <repo-root>
        parse: stdout
      - name: committed .env / secrets
        command: "git ls-files | grep -E '(\\.env$|id_rsa|\\.pem$|\\.key$)' || true"
        cwd: <repo-root>
        parse: stdout
      - name: branch drift
        command: "git branch -a --no-color --sort=-committerdate | head -20"
        cwd: <repo-root>
        parse: stdout
    files:
      - path: package.json
        check: "devDependencies vs dependencies correctness, no duplicates"
      - path: pyproject.toml / requirements*.txt
        check: "unused deps, version conflicts"
      - path: '**/CLAUDE.md'
        check: "no behavior claims that contradict code"
    databases: []                     # no DB checks for hygiene
  checks:
    # Category 1 — Misplaced files
    - id: misplaced-file
      description: "File in the wrong directory for its language/layer."
      source: file listing + CLAUDE.md conventions
      expects: "no obvious mismatches (e.g. *.py under taskit-frontend/src/)"
      severity_on_fail: P1
      finding_template: slop-misplaced
    # Category 2 — Dead/orphaned code
    - id: commented-out-dead-code
      description: "Runs of ≥5 commented lines that read like executable code."
      source: duplicate definitions + commented-out dead code
      expects: "verdict=clean"
      severity_on_fail: P1
      finding_template: slop-commented-code
    - id: duplicate-definitions
      description: "Function/class name defined more than once at module scope."
      source: duplicate definitions + commented-out dead code
      expects: "verdict=clean"
      severity_on_fail: P1
      finding_template: slop-duplicate-def
    # Category 3 — Temp/scratch files
    - id: temp-scratch-tracked
      description: "Tracked files matching temp_*, scratch_*, debug_*, old_*, backup_*."
      source: file listing
      expects: "no matches"
      severity_on_fail: P2
      finding_template: slop-temp
    # Category 4 — Security/config
    - id: committed-secrets
      description: "Tracked files matching .env / id_rsa / .pem / .key."
      source: committed .env / secrets
      expects: "no matches"
      severity_on_fail: P0
      finding_template: slop-secret
    # Category 5 — Structural
    - id: oversized-files
      description: "Source files >500 lines doing multiple things."
      source: large files
      expects: "no source file > 500 lines without a stated split reason"
      severity_on_fail: P3
      finding_template: slop-god-file
    - id: doc-vs-code-drift
      description: "CLAUDE.md or README describes behavior the code doesn't have."
      source: '**/CLAUDE.md' + code grep
      expects: "no contradiction"
      severity_on_fail: P2
      finding_template: slop-doc-drift
    # Category 6 — Dependencies
    - id: unused-or-dup-dependencies
      description: "Imports not used; packages with the same purpose."
      source: package.json + requirements
      expects: "every declared dep has at least one importer / use"
      severity_on_fail: P2
      finding_template: slop-dep
    # Category 7 — Git/project
    - id: gitignore-violations
      description: "Tracked files that should be ignored (.env, pyc, node_modules)."
      source: gitignore violations
      expects: "no matches"
      severity_on_fail: P2
      finding_template: slop-ignore
    - id: stale-todo-old-6mo
      description: "TODO/FIXME/HACK comments older than 6 months."
      source: stale TODO/FIXME/HACK
      expects: "no matches"
      severity_on_fail: P3
      finding_template: slop-stale-todo
  report:
    path: slops/all_slops.md          # matches the skill's current output path
    shape:
      - id: summary_header
        kind: line
        format: "**Audited:** {date}  **Scope:** <repo paths>  **Total:** N (P0: a, P1: b, ...)"
      - id: by_priority
        kind: table
        columns: ["#", "finding", "file(s)", "category"]
      - id: by_category
        kind: list
        group_by: category
      - id: top_5_fixes
        kind: list
        max_items: 5
      - id: zero_finding_categories
        kind: line
        format: "Categories with zero findings: <list> (confirms they were checked)"
    max_lines: 80
    prose_first: true
  findings:
    severity_scale: [P0, P1, P2, P3, P4]
    default_bucket_why: "trust"
    task_template:
      title_prefix: "SLOP-{NNN}-"
      description_template: |
        ## What is wrong today
        The kit must not lie or panic. Hygiene findings that erode the
        verification signal teach the human to ignore the kit. (This is trust work.)

        Priority: P<0-4>
        Category: <category>
        File(s): <paths>
        Age: <git blame date or estimate>

        ## What's Wrong
        <2-3 sentences>

        ## Evidence
        <code snippet or grep result>

        ## Suggested Fix
        <one concrete action>

        ## Risk of Fixing
        <Low/Medium/High — could fixing this break something?>

        ## Working protocol
        - First principles: fix the CAUSE, not the symptom.
        - Failing test before fix where applicable.
        - Attach proof to .proof/task-<id>/proof.md and post a summary comment.
        - Do not create or switch branches; edit files in your worktree.
      default_agent: claude
      proof_path: ".proof/task-<id>/"
    artifact_paths:
      - "slops/SLOP-{NNN}-{slug}.md"     # per-finding detail files (P0-P2 only)
      - ".proof/audit-{date}-slop/files_listing.txt"
```

**What this replaces today**: the `hk-slop-audit/SKILL.md` skill (117 lines)
plus the implicit hand-curation of `slops/all_slops.md` and per-finding
`slops/SLOP-NNN-*.md`. The 7 audit categories become typed checks; the P0-P4
severity scale is the contract's `severity_scale`; the per-finding detail
file convention becomes `artifact_paths`; the bucket WHY context (`Trust,
7/10`) is injected into every task description the way the fable wave
loaders do today.

---

## What the build task (next) does with this

The contract is the input; the runtime is the build. Concretely the next
task delivers:

1. **`audit/preset.py`** — load + validate a preset against `apiVersion`.
2. **`audit/runner.py`** — execute `evidence` in parallel, evaluate `checks`,
   render `report`, file `findings` as board tasks via `taskit_mcp.add_comment`
   (with the same operator email convention the wave loaders use).
3. **`audit/triggers.py`** — wire `/<preset.id>` to the runner; subscribe to
   `on_event` hooks from the existing wave-close flow.
4. **Migration of the two existing skills** — `fable-audit/SKILL.md` and
   `hk-slop-audit/SKILL.md` become thin wrappers that load their YAML preset
   and invoke the runner, preserving the trigger surface and argument hints.

The audit reports under `docs/fable_roadmap/audits/` and `slops/` become
*outputs* of the runner rather than hand-written artifacts. The skill bodies
lose ~80% of their prose — what's left is the trigger description, the
argument hint, and a one-line "this is data; see the preset YAML."

---

## Cross-references

- Existing prompt presets: `taskit/taskit-backend/data/task_presets.json` +
  `docs/breadcrumb_analysis/prompt-presets/FLOW.md`.
- TDD task preset proposal (closest mental model): `docs/breadcrumb_analysis/task-preset-tdd-enforcement/FLOW.md`.
- Wave loader precedent (data + thin loader): `docs/fable_roadmap/bootstrap/create_wave5.py:63-279`
  (the `TASKS = [dict(...), ...]` shape).
- Audit reports (current hand-curated outputs): `docs/fable_roadmap/audits/*.md`.
- Skills being migrated: `.claude/skills/fable-audit/SKILL.md`,
  `.claude/skills/hk-slop-audit/SKILL.md`.
- Breadcrumb-first exploration: `docs/breadcrumb_analysis/_INDEX.md`.