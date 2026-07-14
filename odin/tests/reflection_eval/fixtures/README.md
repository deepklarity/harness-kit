# Reflection model evaluation — fixtures

Two fixture categories are kept here, separated by their `fixture_category`
field in `ground_truth.json`:

| Category | Purpose | Counted in recommendation? |
| --- | --- | --- |
| `real_capture` | A captured reviewer raw_output pulled from the live TaskIt DB (or, when no DB is mounted in the sandbox, a literal transcription of the captured payload in the wave-3 fixtures). | Yes — this is the evidence the recommendation is built on. |
| `parser_baseline_synthetic` | A shape-correct synthetic review written to exercise one of the parser's recognized output shapes (JSON contract, markdown sections). Not a real captured review. | No — used only to confirm the parser pipeline accepts each shape; the `agreement%` on these fixtures is 1.0 by construction. |

## Why synthetic baselines are kept at all

Without them the harness can only be run against real captures. The captured
task-159 pair (1 haiku + 1 sonnet) demonstrates the parser correctly
classifies reviewer-failure streams as ERROR, but it cannot demonstrate that
a clean JSON-contract review or a clean markdown-headed review also parses.
The synthetic baselines plug that gap so the harness output is convincing
even on a small real-capture set.

The aggregate table splits real-capture and parser-baseline rows so the
operator can see both at a glance and the recommendation explicitly reads
from the real_capture category only.

## Reproducing or extending

To add a real capture:

1. Create `inputs/<task-id>_<short-tag>/` with `prompt.txt` (the assembled
   reflection prompt), `ground_truth.json` (operator-asserted verdict +
   `fixture_category: "real_capture"`), and one `<model>.raw.txt` per
   reviewer run (one per ReflectionReport).
2. Re-run `python odin/tests/reflection_eval/eval.py --recommend`.

To add a synthetic parser-baseline shape:

1. Create `inputs/_parser_baseline_<verdict>/` with `prompt.txt`,
   `ground_truth.json` (`fixture_category: "parser_baseline_synthetic"`),
   and one `<model>.raw.txt` per reviewer output to model.
2. Keep the model name on each capture aligned with the model the synthetic
   output is meant to represent (e.g. `sonnet.raw.txt` for sonnet-shaped
   output, `haiku.raw.txt` for haiku-shaped output).