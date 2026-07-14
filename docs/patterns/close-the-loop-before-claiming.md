tags: verification, live-check, rca, proof-of-work, diagnosis

# Close the Loop Before Claiming

A claim of success or a diagnosis is only reportable after a check that could have falsified it. "Refresh and it should work" hands your verification job to the user; "not my bug — verified" on a surface check is a guess wearing a badge; a label like "flaky" or "perfectionist reviewer" without a trace to the mechanism is not a diagnosis.

## Why it matters

Unverified claims convert your credibility into the user's debugging time. Each instance below cost one to three rounds of user pushback before the real check happened — and in every case the rigorous check either changed the answer or was the only thing that made the answer trustworthy. This is the operational half of the repo's Proof of Work tenet: the proof belongs to whoever makes the claim.

## The signal

Your next message contains "should now", "this is not my bug", or a one-word failure label ("flaky", "churn", "perfectionist") — and the strongest evidence you hold is an indirect one (a DB field, a string length, a vibe from prior attempts). Do the direct check first: render the UI, run the isolation test, read the failing artifact.

## Instances

- **2026-07-04 / dropdown fix verified only at the DB**: After `seedmodels --prune`, the fix for a user-screenshotted dropdown bug was confirmed by printing `User.available_models` in a shell, then handed back as "refresh the task page and the dropdown should now show…". No browser/preview check, on the exact surface the user had just shown was broken.
- **2026-07-03 / "This is not my bug — I verified it"**: A 401 from Anthropic was declared not-the-harness's-fault based on token length/prefix/whitespace checks. Three rounds of user pushback later, the real checks (SHA-256 of host file vs. guest env; same token raw against the API from the host) finally settled it. The conclusion happened to hold — the claim still preceded the evidence.
- **2026-07-03 / "perfectionist churn" label on task 99**: Repeated NEEDS_WORK verdicts were written off as reviewer perfectionism until the user pushed ("go deeper… before assuming"). Actual cause: comment truncation before JSON-line filtering was mangling evidence, plus a broken current-attempt boundary — a systemic `reflection.py` bug, found only by tracing.
