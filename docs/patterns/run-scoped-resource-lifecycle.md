tags: leak, lifecycle, msb, gc, structural-fix, sandbox

# Run-Scoped Resources Need Run-Scoped Lifecycle

Every confined task run in this repo leaked ~3 GB of disk: the microsandbox
harness was creating an ephemeral VM (`msb-<hash>`) per run but never removed
it. Operators deleted sandboxes by hand — a hygiene rule, not a system fix.
The same shape leaked per-worktree `node_modules` (~1 GB each).

The fix is structural: **lifecycle is owned by the run boundary, not by an
out-of-band cron / sweeper / operator ritual.** A cron that polls the disk
treats the symptom (leftover files); the cause (no removal at the run end)
keeps producing leftovers at the same rate.

## The rule

Any resource created between the start and end of a unit of work MUST be
released in a `finally` block at that boundary, OR be unowned-and-prunable
through an explicit GC command — never the default. Defaults that leak are
not "be tidy" choices; they are implicit leaks-by-default and they win.

The two paired contracts:

1. **Per-run lifecycle.** Code that creates the resource creates the cleanup
   in the same function — same try / finally block. Not a separate cleanup
   routine. Not a "remember to call this after". Not a cron.
2. **Backstop for crashes.** A crash cannot run its own finally. The
   pre-existing crash leftovers must be reclaimable, but only by explicit
   action (`odin gc --prune`, default dry-run). Auto-sweeping on every
   startup is itself a hidden coupling that makes ops predict harder.

## The harness shape (the actual fix in this repo)

`MicrosandboxHarness._execute_sync` (in
`odin/src/odin/harnesses/microsandbox.py`) now:

- generates a deterministic sandbox name `odin-msb-<token>` BEFORE booting;
- passes `--name <token>` to `msb run`;
- in an inner `finally` block (covers success / non-zero / timeout /
  exception), calls `MicrosandboxHarness._remove_sandbox(sandbox_name)`;
- in an outer `finally` block, `shutil.rmtree(tmpdir, ignore_errors=True)`
  for the `odin-msb-*` temp dir.

`MicrosandboxHarness._remove_sandbox` is the single safety net: it
**refuses** (returns False without running msb) on any name that doesn't
match the `odin-msb-*` prefix. `odinbuild` and `odin-agents` cannot leak
even by a typo.

`MicrosandboxHarness.sweep_startup_orphans()` is called from
`Orchestrator.__init__` for the **backstop** half — covers the crash that
couldn't run its own finally. Idempotent, refuses anything not matching
the prefix, never touches snapshots.

`OdinCLI.gc` (the `odin gc` command) is the manual cousin: dry-run by
default, refuse named/snapshots, report `node_modules` cost separately so
operators can see the per-worktree `npm install` tax.

## Why not just cron?

A cron-style sweeper treats the symptom — leftover files — at the same rate
the symptom is produced. The cause (no removal) is still doing the work, and
the sweeper is invisible until the day it breaks (volume full, slow scan,
race with a still-running run). Worse, a sweeper that operates on the
default is impossible to opt out of; a `finally` block the reader can see
in the code is impossible to mistake.

The right place to capture the rule is the code that creates the resource.
Every leakable resource in this repo should have an `odin gc` route; the
default behavior of the system shouldn't need one to be clean.

## The npm-cache corollary

The same shape repeats for `node_modules`. Every frontend-touching worktree
runs `npm install` and pays ~1 GB of duplicated packages. The fastest fix
that actually works: a shared npm cache mounted into the guest
(`-v ~/.npm-cache:/root/.npm` + `npm_config_cache=/root/.npm`), or baking
node deps into `odin-agents` alongside the python test-deps recipe. The
fix isn't "tell the operator to clean up" — it's "stop producing the waste
in the first place". File as a follow-up: see BACKLOG.

## What to do when you spot a leak-shaped item

1. Trace the resource lifecycle: who creates it, who removes it, what
   crashes between those two points.
2. If removal is missing, add it at the run boundary (finally).
3. Add a startup sweep only for the crash case — never as primary fix.
4. Add an `odin gc` route so operators can audit + reclaim explicitly.
5. Pin everything in tests: `name` matching, refuse-list matches, ordering
   (cleanup AFTER the resource-creation call), temp-dir removal alongside.
