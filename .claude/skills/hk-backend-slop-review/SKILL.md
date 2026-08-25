---
name: hk-backend-slop-review
description: Backend Slop Review. Use skill /hk-backend-slop-review to perform Backend
  Slop Review. Generated from task preset 'backend-slop-review' — edit data/task_presets.json,
  not this file.
generated: true
generated_from: data/task_presets.json#backend-slop-review
---

# Backend Slop Review

WHY: Find backend-specific AI slop, over-engineering, dead API endpoints, unnecessary abstractions, duplicate business logic, and cargo-cult handlers.

You are a senior backend systems engineer reviewing backend modules, API handlers, and data layer logic.

Applicability Gate (CRITICAL - FAST CHECK ONLY):
Before running backend slop review, check ONLY the current project root and immediate source folders (do NOT search parent directories, do NOT search outside the working directory, and do NOT inspect node_modules or .odin).
- If NOT APPLICABLE (no backend files, server routes, database models, or API endpoints exist): Post a task comment stating: "Applicability Gate: No backend source code or API routes found in this project. Backend slop review skipped." and STOP execution immediately. Output ODIN-STATUS SUCCESS right away. Do NOT perform exhaustive codebase crawling, do NOT search outside working directory, and do NOT generate any report file.
- If APPLICABLE: Proceed with the audit procedure below.

Procedure & Scope of Analysis:
1. Scan backend routes, controller/handler logic, middleware, service classes, and data access layers directly.
2. Check for dead message paths, unreachable routes, cargo-cult mocks, and unused database queries or serializers.
3. Identify over-abstracted state, unnecessary service wrappers, redundant helper functions, and duplicate business logic.
4. Evaluate error handling realism, defensive coding anti-patterns, and unused models or endpoints.

Report Structure & Formatting Requirements:
Save the final report to `BACKEND_SLOP_AUDIT_REPORT.md` at the root of the audited project directory. Do not modify any existing source code files.

1. Executive Overview: Provide exactly 2 concise paragraphs:
   - Paragraph 1: Exactly a 2-line intro summarizing what the audited backend module/codebase does and its architectural state. Do NOT use markdown subheadings (e.g. do not write '1.1 Backend Overview' or '1. Executive Overview'). Write a direct, clear intro paragraph.
   - Paragraph 2: Explanation of the Backend Slop Score (0-10 scale), explicitly stating the numeric score and severity (e.g. 'Overall Slop Score: 6/10 — Severity: MEDIUM') and a summary sentence of finding counts by severity (e.g. 'The audit identified 2 High, 3 Medium, and 1 Low severity findings across the codebase.').

2. Detailed Findings: Report ONLY genuine, verified findings discovered during analysis (strictly up to a maximum of 10).
   - Hard Cap: Maximum 10 findings strictly (F1 to F10).
   - If only 3, 5, or 6 genuine issues exist, output ONLY those 3, 5, or 6 findings. NEVER invent, hallucinate, duplicate, or pad extra findings to reach 10. Filter out low-signal nits.

Assign genuine, accurate High/Medium/Low risk tags based on real engineering impact:
- HIGH: Dead message paths, broken/unreachable features, correctness bugs, duplicated core logic.
- MEDIUM: Over-abstracted state, unnecessary wrappers, redundant helpers, inefficient patterns.
- LOW: Minor unused imports or small redundant comments.

Every finding MUST follow this exact format:

### F1 [Descriptive Title]
Location: [File path:startLine-endLine]
Likelihood: [High/Medium/Low]
Risk: [High/Medium/Low]

```python
[Exact 4-15 lines of source code extracted directly from the codebase]
```

**Reasoning:** [1-2 concise sentences explaining why this is slop]
**Impact:** [1-2 concise sentences explaining potential risk]

CRITICAL FORMATTING RULES FOR FINDINGS:
- Title Cleanliness: Do NOT prefix finding titles with severity labels like CRITICAL:, HIGH:, MEDIUM:, LOW:, or INFO: (e.g. write `### F1 Entire LocalRelay message path is dead`, NEVER `### F1 HIGH: Entire LocalRelay message path`). The Risk/Likelihood badges already carry that information.
- Location Cleanliness: `Location:` MUST contain ONLY the file path and line numbers (e.g. `Location: taskit/tasks/relay.py:112-128`). Do NOT append comments, notes, or descriptions to the Location line.
- Real Code Snippet: Inside the code block, you MUST provide the REAL source code lines (4-15 lines) extracted directly from the file. Do NOT write comments, descriptions, or placeholder text inside the code block.

3. Cross-Repository Pattern Observations: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining a cross-cutting backend pattern, over-abstraction theme, or data access pattern. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
4. Stable or Intentional Areas: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining clean, well-tested, and performant backend modules. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
5. AI Remediation Prompt: Provide a refined, highly focused prompt instructing another AI assistant how to systematically fix findings F1 through FN in order. Keep it concise, using bulleted action items per finding with target file paths. Do NOT insert artificial hard line breaks mid-sentence or mid-bullet; let each bullet point be a single continuous line so text flows naturally across 100% width.

Output File Requirement & User Control:
- Save the final markdown report to the root of the audited project directory as `BACKEND_SLOP_AUDIT_REPORT.md`. Do not modify any existing source code files.
- Ask User Confirmation: After saving the markdown report, ask the user: "Audit report saved to `BACKEND_SLOP_AUDIT_REPORT.md`. Would you like to compile it into an interactive HTML dashboard? (yes/no)". If the user confirms (yes), execute `python compile_audit.py BACKEND_SLOP_AUDIT_REPORT.md --yes`.
