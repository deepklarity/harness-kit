# debugging

Finding root causes. Each skill's tier is a row in [`../skills.csv`](../skills.csv); the full list of categories is [`../README.md`](../README.md).

| Skill | What it does |
|---|---|
| [hk-breadcrumb-creator](hk-breadcrumb-creator/SKILL.md) | Trace a flow through the code and write its breadcrumb (FLOW.md, DETAILS.md, DEBUG.md) under docs/breadcrumb_analysis/, with the index row. |
| [hk-follow-breadcrumb](hk-follow-breadcrumb/SKILL.md) | Read docs/breadcrumb_analysis/ before exploring a flow and answer from it, citing file and function per hop. |
| [hk-pattern-check](hk-pattern-check/SKILL.md) | Check docs/patterns/ for learnings that apply to the current problem and relay the ones that fit in under 200 words. |
| [hk-rca](hk-rca/SKILL.md) | Walk a bug through Reproduce, Locate, Hypothesis, Failing Test, Fix, Verify Live, Document, and state the root cause in chat. A bug that will not reproduce takes Step 1's hard-bug branch: build a pass/fail loop, minimise, bisect, instrument. |
| [hk-testing-shortcuts](hk-testing-shortcuts/SKILL.md) | Answer a testing question (a command, a path, how to get a test id) from the repo's own testing docs, and when the answer was not written down yet, save it as a guide in docs/testing_process/ or a script in the workspace's testing tools folder. |
