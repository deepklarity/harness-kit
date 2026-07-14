# Reflection reviewer model evaluator

The board's `reflection_model` is currently pinned to a sonnet tier because
haiku burned rework cycles emitting unparseable multi-section markdown on
task 159 (×3 reports, all hard-ERRORed under the new parser). Wave 3 shipped
a fenced-JSON contract + lenient parser + hard-ERROR on garbage (see
`odin/src/odin/reflection.py`), but nobody has measured whether a cheap
model can now emit the contract reliably. This harness answers that
question with numbers.

## What it measures

For each candidate model, on the captured inputs:

- **parse%** — the parser returned a verdict in `{PASS, NEEDS_WORK, FAIL}`.
- **ERROR%** — the parser returned `ERROR` (a reviewer-failure sentinel;
  unparseable output never dispatches rework).
- **agreement%** — the parser's verdict matched the operator-asserted
  ground truth.

Both numbers are reported per-model and per-fixture-category. The
recommendation is built from the `real_capture` category only — synthetic
parser-baseline fixtures are shape-correctness smoke tests, not model
evidence.

## Operating modes

```bash
# Offline (replay captured reflection outputs through the parser).
# No API key required; uses what is in fixtures/inputs/<id>/<model>.raw.txt.
python odin/tests/reflection_eval/eval.py --mode offline --recommend

# Live (invoke `claude -p <prompt> --model <model>` per (input, model)).
# Requires ANTHROPIC_API_KEY (or a logged-in `claude` OAuth session).
python odin/tests/reflection_eval/eval.py --mode live \
    --models haiku sonnet --recommend
```

Live mode writes captures to `results/captures/<model>/<id>.raw.txt` so a
follow-up offline run can replay the same evidence deterministically.

## Recommendation thresholds

The recommendation reads the `real_capture` summary only:

| Bar | Parse% | Agreement% | ERROR% | Min N | Action |
| --- | --- | --- | --- | --- | --- |
| Flip | ≥ 95 | ≥ 80 | ≤ 5 | 5 | flip the board default to the cheap model |
| Size-scale | ≥ 90 | any | ≤ 10 | 3 | wire cheap on small tasks; keep current default on large |
| Insufficient | < 3 per candidate | | | | re-run with more captures; do not flip |
| Keep | otherwise | | | | keep current default |

These thresholds are exposed in `eval.py::recommend()` and can be tuned in
one place.

## Why a script and not a pytest test

The existing `tests/unit/test_reflection.py` pins parser behaviour: each
test feeds a known-shaped string and asserts the parser returns a known
verdict. That's the right surface for parser regressions. This harness
pins *model* behaviour: it runs the actual reviewer prompt against a
candidate model and observes what comes back. Different scope, different
artifact, different invariants — keeping them separate prevents a parser
test from being silently absorbed into a model-measurement test (and vice
versa).

## Files

- `eval.py` — the harness (offline + live modes, category-aware aggregation).
- `fixtures/inputs/` — captured reviewer raw_outputs + synthetic
  parser-baseline shapes; see `fixtures/README.md`.
- `results/` — generated each run; not committed.