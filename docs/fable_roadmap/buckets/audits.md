# Audits

**Why.** The kit already audits itself — but every audit is hand-rolled: the
operator reruns metrics scripts, spot-checks tasks, and writes a report from
scratch. The system has presets (reusable task/audit templates) that could
carry this instead, and most existing presets are thin. Standardized audit
presets mean any project the kit builds — not just the kit itself — gets the
same periodic health checks: hygiene, cost, autonomy, doc-vs-reality drift,
test honesty. An audit that runs the same way everywhere is an audit that
can run unattended, and its findings become tasks without a human
re-deriving the questions each time.

**Where it stands.** `/fable-audit` and `/hk-slop-audit` exist as skills;
`autonomy_metrics.py` and the diagnostic scripts produce numbers; presets
exist in the system but are underused and mostly unenhanced. Nothing runs
periodically on its own; nothing is project-agnostic.

**Ideas.**
- Audit presets: turn the recurring audits (roadmap progress, slop/hygiene,
  cost per merged change, autonomy/unstick rate, doc drift) into presets the
  board can instantiate on a schedule, each ending in a short report plus
  auto-filed tasks for findings.
- Preset enhancement pass: inventory existing presets, measure which are
  used, upgrade the useful ones to carry proof requirements and WHYs the
  same way fable tasks do.
- Project-agnostic audit contract: an audit preset declares what evidence it
  needs (scripts, endpoints, logs); any project that provides the contract
  gets the audit for free.
- Static-autolint preset (user call): lint drift is not a cleanup campaign,
  it is a periodic audit — a preset runs the linters, autofixes what is
  safe, and files a task only when a finding needs judgment.
- Disk hygiene belongs in the periodic sweep: 183 GB of dead sandbox disks
  accumulated unseen (fixed as W5.19); the audit preset should check disk,
  logs, and worktree growth so the next leak is a red line, not a surprise.

When an idea here becomes real work, it gets its own section below this
line (or its own file) with the design, the tasks, and the before and after
numbers. The WHY above travels into every task description.
