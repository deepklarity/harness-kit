# Ease of use & getting started

**Why.** A first-timer today faces a wall: services to start in the right
order, agent CLIs to install and authenticate, sandbox images to build, env
vars to guess — and the docs assume you have everything. Worst case is the
partial setup: someone with only codex (no claude, no glm) gets failures
deep inside a run instead of a clear answer up front. Every hour a new user
spends fighting installation is an hour they conclude the kit isn't for
them. The kit can't become a factory for other people's projects if only
its builders can switch it on.

**Where it stands.** `dev.sh` and `start_services.sh` work for us, on this
machine, with all five providers authenticated. There is no installer, no
preflight check, no "you have codex only, here's what works" answer, no
quickstart that proves the loop on a toy project in ten minutes.

**Ideas.**
- Doctor first. One command (`odin doctor` or `hk doctor`) that checks
  everything — services, CLIs, auth, sandbox runtime, disk, memory — and
  prints a table: what works, what's missing, what exactly to run to fix
  each line. The error deep in a run becomes a red line before any run.
- Providers are optional, gracefully. The kit runs with WHATEVER agent CLIs
  exist. One provider is enough to start; routing, reviews, and merges pick
  from what's installed and say so. A feature matrix answers "I only have
  codex — what do I lose?" honestly.
- Ten-minute quickstart. Install, doctor, then one tiny built-in sample
  spec that runs a real task end to end — dispatch, sandbox, review, merge
  — so the new user SEES the loop work before pointing it at their code.
- Assumption audit. Sweep the codebase for hardcoded provider assumptions
  (claude-only paths, model names in defaults, auth expected on disk) and
  turn each into a capability check with a plain failure message.

When an idea here becomes real work, it gets its own section below this
line (or its own file) with the design, the tasks, and the before and after
numbers. The WHY above travels into every task description.

## Assumption audit — task #252

Swept `odin/src` and `taskit-backend` for hardcoded provider assumptions.
Fixed the HIGH blast-radius sites (planning/exec/summarize pre-flight CLI
check, doctor base_agent probe, provider-agnostic reflection reviewer
defaults). Remaining lower-priority items for a future pass:

- [ ] `MergeAgentConfig.agent = "claude"` (models.py:175) — latent trap
      if LLM-based merge resolution is implemented; currently deterministic
- [ ] `AdvisorConfig.agent = "claude"`, `model = "claude-sonnet-5"`
      (models.py:192–193) — advisor disabled by default; needs the same
      derive-from-available treatment when enabled
- [ ] `DEFAULT_ADVISOR_MODEL/AGENT` constants (advisor.py:34–35) — same
- [ ] `REFLECTION_PREFERRED_AGENTS = ["gemini", "codex", "claude"]`
      (views.py:117) — ordering only (auto-path picks any available agent),
      but the list should include all providers for consistent preference
- [ ] `ALLOWED_FORCED_PROVIDERS = {"gemini"}` (forced_provider.py:12) —
      intentional trial restriction; revisit when the trial generalizes
