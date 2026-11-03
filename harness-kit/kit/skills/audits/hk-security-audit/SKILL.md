---
name: hk-security-audit
description: "Run this skill's security_audit.py over the repo, verify each hit by reading the code, and write up to ten findings as one HTML page scored out of 10: leaked secrets, endpoints without auth, credential mounts, OAuth tokens in sandboxes, egress defaulting to allow, shell=True. Triggers on: 'security audit', 'scan for vulns', 'is this code safe', or /hk-security-audit."
argument-hint: "[directory to scan, default: repo root]"
allowed-tools: Bash, Read, Grep, Glob, Write
generated: true
---

# Security audit

Runs the scanner, reads the code behind each hit, and writes up what is real. Reads only; no source file changes. Hardcoded localhost URLs and ports stay with `hk-code-quality-audit`; secrets and auth are this skill's.

## Steps

1. Read `docs/playbook/tone_and_taste.md`. Every sentence on the page and in chat follows it.
2. Pick the scope: `$ARGUMENTS` if given, else the repo root. If it holds no source or config files, say so in one chat line and stop.
3. Run `python3 <this skill's folder>/scripts/security_audit.py <scope>` and keep its JSON
   output (`--output` writes it to a file; default is stdout). It is dependency-free and checks
   only what reading the code can show: secrets in tracked files, framework defaults that leave
   endpoints unauthenticated, credential mounts, tokens passed into a subprocess or sandbox,
   egress defaulting to allow, and `shell=True`. If CLAUDE.md names an additional workspace
   scanner, run that too and merge its findings in. For known vulnerabilities in dependencies
   run each ecosystem's audit tool (`npm audit --json`, `pip-audit -r <requirements>`) when it is
   on PATH and merge what it reports; when it is not, the page says that check was skipped.
4. Drop hits under vendored or build directories (dependency folders, build output, orchestration
   state). The remaining list is the pre-scan.
5. Read the code at every remaining P0 and P1 finding and at each P2 and P3 category's first few
   locations. Then read what the scanner cannot see: every endpoint in the backend service for a
   missing auth or permission guard, tracked files for `.env`, `.pem`, `.key` and `id_rsa`, `git log -p -S<pattern>` for each secret
   pattern the scanner reports in the tree, and any
   sandbox or execution config for a default that allows network egress. A finding is kept only
   when you can quote the lines and name what an attacker gets. Keep at most ten, ranked by what
   is reachable from outside first; fewer is fine.
6. Give each finding a severity and derive the score from the tables below.
7. Build the page: copy `docs/skills/knowledge/hk-writeup/SHELL.html`, replace `<!-- CONTENT -->` with the content below and `<title>Writeup</title>` with the real title, and write it to `$(git rev-parse --show-toplevel)/writeup/security-audit-<scope-slug>.html`.
8. Say in one chat line where the page went, what the score is, and which checks were skipped. Done when that line is sent.

## Severity and score

Severity is 1 to 5, 5 worst, set by what an attacker gets; the scanner's P0 to P3 is a hint, not the answer.

| Severity | Finding |
|---|---|
| 5 | a secret in the tree or git history, or an unauthenticated endpoint that writes |
| 4 | reachable from outside without a secret: an unauthenticated read endpoint, an OAuth token handed to a sandbox, egress defaulting to allow |
| 3 | reachable only by someone already inside: a credential mount into a sandbox, `shell=True` on input a user controls |
| 2 | `shell=True` on fixed strings, a weak default that config overrides |
| 1 | hygiene: a key file pattern missing from `.gitignore`, a comment naming a secret's location |

Score 0 to 10 and the band for each set of findings: [../README.md](../README.md).

Inside the band, go to the top when the endpoints you read all check auth and the fix is one file, and to the bottom when the same flaw repeats across modules.

## What goes on the page

In this order, plain sentences and tables, no badges.

**Overview.** Two sentences: what the scoped code does and who can reach it, then the score out of 10 with the sentence that derives it and the finding count. One more sentence naming the scanner's raw count and how many hits were vendored or false.

**Findings.** One `details.row` per finding, numbered F1 to F10: `.row-name` is the severity in words ("severity four"), `.row-status` the title stating the flaw. The body holds `file:start-end` in `.mono`; the literal four to fifteen lines in a `<pre>` with the file's own language; one sentence on the flaw; one sentence on what an attacker gets; one sentence on the fix; the scanner id if it came from the scan, or "read by hand" if not.

**Scanner hits not kept.** A `.tbl` of category, count, and one sentence on why they were dropped (vendored, test fixture, false positive). Omit if none were dropped.

## Closing step

Then the closing step in [../README.md](../README.md#closing-step): board rows for severity 4 and 5, a pattern for a recurring cause, one ledger line.

## Example: in harness-kit

- The scanner: `python3 docs/skills/audits/hk-security-audit/scripts/security_audit.py <scope>` (JSON on stdout; the skill writes no scratch file) (`--help` lists the flags). Its `findings` array carries `id`, `severity` P0 to P3, `category`, `location`, `summary`, `evidence`.
- Vendored paths to drop: `.venv*`, `build`, `dist`, `node_modules`.
- What the scanner cannot see here: every endpoint in the backend service, and the sandbox config for its default egress flag.
