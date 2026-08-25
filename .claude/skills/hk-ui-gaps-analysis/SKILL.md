---
name: hk-ui-gaps-analysis
description: UI Gaps Analysis. Use skill /hk-ui-gaps-analysis to perform UI Gaps Analysis.
  Generated from task preset 'ui-gaps-analysis' — edit data/task_presets.json, not
  this file.
generated: true
generated_from: data/task_presets.json#ui-gaps-analysis
---

# UI Gaps Analysis

# UI Gaps Analysis

WHY: catch missing UI states, interaction gaps, and UI logic coupling before users experience them.

You are a senior frontend engineer reviewing UI implementation code and state handling.

Applicability Gate (CRITICAL - FAST CHECK ONLY):
Before running UI gaps analysis, check ONLY the current project root and immediate source folders (do NOT search parent directories, do NOT search outside working directory, and do NOT inspect node_modules or .odin) for frontend UI components or state management logic (`.tsx`, `.jsx`, `.vue`, `.svelte`, `.css`).
- If NOT APPLICABLE: Post a task comment stating: "Applicability Gate: No frontend UI components or state logic found in this project. UI gaps analysis skipped." and STOP execution immediately. Output ODIN-STATUS SUCCESS right away. Do NOT perform exhaustive crawling and do NOT generate any report file.
- If APPLICABLE: Proceed with the audit procedure below and save the final report to `UI_GAPS_AUDIT_REPORT.md`.

Procedure & Scope of Analysis:
1. Review component state management, props flow, data rendering logic, and UI consistency.
2. Check for missing UI states: loading skeletons, unhandled error boundaries, empty list states, disabled button states, and edge cases.
3. Identify accessibility, responsiveness, and UI component coupling concerns.

Report Structure & Formatting Requirements:
Save the final report to `UI_GAPS_AUDIT_REPORT.md` at the root of the audited project directory. Do not modify any existing source code files.

1. Executive Overview: Provide exactly 2 concise paragraphs:
   - Paragraph 1: Exactly a 2-line intro summarizing what the audited application/codebase does and its overall UI state and interaction gap profile. Do NOT use markdown subheadings (e.g. do not write '1.1 UI State Overview' or '1. Executive Overview'). Write a direct, clear intro paragraph.
   - Paragraph 2: Explanation of the UI Gap Score (0-10 scale), explicitly stating the numeric score and severity (e.g. 'Overall UI Gap Score: 7/10 — Severity: MEDIUM') and a summary sentence of finding counts by severity (e.g. 'The audit identified 1 High, 2 Medium, and 1 Low severity findings across the codebase.').

2. Detailed Findings: Report ONLY genuine, verified UI gaps discovered during analysis (strictly up to a maximum of 10).
   - Hard Cap: Maximum 10 findings strictly (F1 to F10).
   - If only 3, 5, or 6 genuine issues exist, output ONLY those 3, 5, or 6 findings. NEVER invent, hallucinate, duplicate, or pad extra findings to reach 10. Filter out low-signal nits.

Assign genuine, accurate High/Medium/Low risk tags based on real engineering impact:
- HIGH: Unhandled UI crash during network error, missing empty state freezing screen, prop-drilling multi-layer coupling.
- MEDIUM: Missing button disabled state during submission, unhandled loading spinner, redundant component wrapper.
- LOW: Non-standard CSS property nit or small redundant state variable.

Every finding MUST follow this exact format:

### F1 [Descriptive Title]
Location: [File path:startLine-endLine]
Likelihood: [High/Medium/Low]
Risk: [High/Medium/Low]

```typescript
[Exact 4-15 lines of source code extracted directly from the codebase]
```

**Reasoning:** [1-2 concise sentences explaining why this is a UI state/interaction gap]
**Impact:** [1-2 concise sentences explaining user risk or potential crash]

CRITICAL FORMATTING RULES FOR FINDINGS:
- Title Cleanliness: Do NOT prefix finding titles with severity labels like CRITICAL:, HIGH:, MEDIUM:, LOW:, or INFO: (e.g. write `### F1 Missing empty state handler for search results grid`, NEVER `### F1 HIGH: Missing empty state handler`). The Risk/Likelihood badges already carry that information.
- Location Cleanliness: `Location:` MUST contain ONLY the file path and line numbers (e.g. `Location: src/components/SearchGrid.tsx:50-65`). Do NOT append comments, notes, or descriptions to the Location line.
- Real Code Snippet: Inside the code block, you MUST provide the REAL source code lines (4-15 lines) extracted directly from the file. Do NOT write comments, descriptions, or placeholder text inside the code block.

3. Cross-Repository Pattern Observations: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining a cross-cutting UI state pattern, error handling trend, or component coupling theme across the codebase. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
4. Stable or Intentional Areas: Provide exactly 3 concise bullet points. Each bullet point MUST be only 2 lines (maximum 3 lines) explaining clean UI components, robust error boundaries, and well-managed loading states. Keep it concise, punchy, and scannable without wall-of-text paragraphs.
5. AI Remediation Prompt: Provide a refined, highly focused prompt instructing another AI assistant how to systematically fix findings F1 through FN in order. Keep it concise, using bulleted action items per finding with target file paths. Do NOT insert artificial hard line breaks mid-sentence or mid-bullet; let each bullet point be a single continuous line so text flows naturally across 100% width.

Output File Requirement & User Control:
- Save the final markdown report to the root of the audited project directory as `UI_GAPS_AUDIT_REPORT.md`. Do not modify any existing source code files.
- Ask User Confirmation: After saving the markdown report, ask the user: "Audit report saved to `UI_GAPS_AUDIT_REPORT.md`. Would you like to compile it into an interactive HTML dashboard? (yes/no)". If the user confirms (yes), execute `python compile_audit.py UI_GAPS_AUDIT_REPORT.md --yes`.
