# Error ledger — every error compounds

User directive: every error anyone sees — agent, operator, reviewer, test —
gets recorded WITH context, however minor. Errors are triaged later and
each becomes a fix, a breadcrumb, or a documented non-issue. Nothing gets
diagnosed twice.

The historical manual entries live in the **structured** error ledger at
`ErrorEvent` (model) and the triage surface at
`testing_tools/errors.py`. Run:

```bash
cd taskit/taskit-backend
python testing_tools/errors.py --seed   # one-time: import legacy entries
python testing_tools/errors.py --brief  # open entries grouped by signature
python testing_tools/errors.py --json   # for agents
python testing_tools/errors.py --set-disposition <id> fixed --note "..."
```

Automatic capture (task #222) hooks every place the system already
handles or logs an error — failure_tagger misses, merge ladder
failures, reflection ERROR verdicts, spec-verify gate crashes, and
celery task exceptions — into the same store, so this doc is now
narrative-only. The model carries the data; the doc carries the
intent.

## Why this doc still exists

A short README for the rule, not a dump for the data. Three lines an
operator reads once:

1. **Every error goes in.** If you see one and it's not in the ledger,
   add it via a hook in `tasks/errors.py`. The five instrumented sites
   cover most paths; new error sites should add a hook, not a comment.
2. **Triage later.** Don't fix in this commit. Set a disposition
   (`fixed` / `non-issue`) when the resolution is clear, otherwise
   leave it `open` and move on. The ledger is read at wave-close and
   on demand, not in the hot path.
3. **Dispositions round-trip via API.** The non-operator can mark
   entries fixed without DB access: `POST /errors/<id>/disposition/`
   with `{"disposition": "fixed", "note": "..."}`.

## Triage surface contract

| Mode | What you get |
| --- | --- |
| `--brief` | one line: open count, sources, group count |
| `--json` | structured: events, groups (signature + count), source/distribution |
| `--disposition open` | filter to the working set |
| `--source merge_failure` | filter to one instrumented kind |
| `--set-disposition <id> fixed --note ...` | update one row |
| `--seed` | idempotent import from this doc's historical entries |

Dispositions:

- `open` (default) — not triaged yet
- `fixed` — there's a follow-up task or it's been addressed; the note
  carries the pointer
- `non-issue` — host/infra/one-off, no follow-up needed; the note
  carries the rationale

## Adding a new instrumented kind

1. Add a value to `ErrorEvent.SOURCE_CHOICES` in `tasks/models.py`.
2. Add a `record_<kind>` helper in `tasks/errors.py` that wraps
   `record_error(source=...)`.
3. Hook the call site: find where the existing code already logs the
   error and add one call alongside it (wrapped in
   `try: ... except Exception: logger.exception(...)` so the ledger
   never crashes the live path).
4. Add a test that forces the failure and asserts the row exists.
## Pending import

No pending entries — the three W6 retrospective entries that lived in
this section (conflict-flag gap / zombie sandboxes / watcher clock
jump) were imported into `ErrorEvent` with `disposition=fixed`
(task #238). Run `python testing_tools/errors.py --import-pending` to
re-import; the call is idempotent and a no-op on the second run. The
doc is now narrative-only.

The import is now a registered data op (`tasks/dataops.py`): a service
restart runs it automatically via the `post_migrate` hook — no manual
runbook needed. **Data ops go through the registry, never a manual
runbook.**
